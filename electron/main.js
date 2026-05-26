const { app, BrowserWindow, globalShortcut, ipcMain, safeStorage, shell } = require("electron");
const { execFile } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { loadLibraryData } = require("./library");
const { loadCategoriesFromCache, refreshCategoryCache } = require("./categories");

const SCAN_STATE_FILE = "scan-state.json";
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const SCAN_STATUS_EVENT = "scan:status";
const LIBRARY_UPDATED_EVENT = "library:updated";
const APP_LOG_EVENT = "app:log";
const TOGGLE_CONSOLE_EVENT = "app:toggle-console";
const LIBRARY_BACKUP_DIR = "library-backup";
const STEAM_KEY_FILE = "steam-api-key.bin";
const SIGNIFICANT_DROP_MIN_BASE = 200;
const SIGNIFICANT_DROP_RATIO = 0.8;

// Ensure Chromium cache paths are writable on locked-down environments.
const localAppData = process.env.LOCALAPPDATA || app.getPath("userData");
const appDataRoot = path.join(localAppData, "GamesLibrarian");
const userDataDir = path.join(appDataRoot, "user-data");
const cacheDir = path.join(appDataRoot, "cache");

try {
  fs.mkdirSync(userDataDir, { recursive: true });
  fs.mkdirSync(cacheDir, { recursive: true });
} catch (_error) {
  // Best effort. Electron will fallback to defaults if these paths fail.
}

app.setPath("userData", userDataDir);
app.setPath("sessionData", cacheDir);
app.commandLine.appendSwitch("disk-cache-dir", cacheDir);
app.commandLine.appendSwitch("disable-gpu-shader-disk-cache");

const scanStatePath = path.join(appDataRoot, SCAN_STATE_FILE);
const steamKeyPath = path.join(appDataRoot, STEAM_KEY_FILE);
const scanState = {
  running: false,
  lastScanAt: "",
  lastScanError: "",
};
let categoryRefreshRunning = false;

try {
  const persisted = JSON.parse(fs.readFileSync(scanStatePath, "utf8"));
  if (persisted && typeof persisted === "object") {
    scanState.lastScanAt = String(persisted.lastScanAt || "");
    scanState.lastScanError = String(persisted.lastScanError || "");
  }
} catch {
  // No previous scan state.
}

function persistScanState() {
  try {
    fs.mkdirSync(path.dirname(scanStatePath), { recursive: true });
    fs.writeFileSync(
      scanStatePath,
      JSON.stringify({
        lastScanAt: scanState.lastScanAt,
        lastScanError: scanState.lastScanError,
      }, null, 2),
      "utf8",
    );
  } catch {
    // Best-effort persistence only.
  }
}

function isScanStale(lastScanAt) {
  if (!lastScanAt) {
    return true;
  }
  const parsed = Date.parse(lastScanAt);
  if (!Number.isFinite(parsed)) {
    return true;
  }
  return Date.now() - parsed >= WEEK_MS;
}

function currentScanStatus() {
  return {
    running: scanState.running,
    lastScanAt: scanState.lastScanAt,
    lastScanError: scanState.lastScanError,
    stale: isScanStale(scanState.lastScanAt),
  };
}

function broadcast(channel, payload) {
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) {
      window.webContents.send(channel, payload);
    }
  }
}

function logApp(level, message, meta) {
  broadcast(APP_LOG_EVENT, {
    ts: new Date().toISOString(),
    level: String(level || "info"),
    message: String(message || ""),
    meta: meta && typeof meta === "object" ? meta : undefined,
  });
}

function emitScanStatus() {
  broadcast(SCAN_STATUS_EVENT, currentScanStatus());
}

function loadBaseLibrary() {
  return loadLibraryData(app.getAppPath());
}

function libraryDataCandidates() {
  return [
    path.join(app.getAppPath(), "dist", "game-library", "assets", "games-data.js"),
    path.join(process.cwd(), "dist", "game-library", "assets", "games-data.js"),
  ];
}

function firstExistingLibraryDataPath() {
  const candidates = libraryDataCandidates();
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return candidates[candidates.length - 1];
}

function backupLibraryData() {
  const source = firstExistingLibraryDataPath();
  if (!source || !fs.existsSync(source)) {
    return "";
  }

  const backupDir = path.join(appDataRoot, LIBRARY_BACKUP_DIR);
  const backupFile = path.join(backupDir, "games-data.js");
  try {
    fs.mkdirSync(backupDir, { recursive: true });
    fs.copyFileSync(source, backupFile);
    logApp("debug", "Backed up library data before scan", { source, backupFile });
    return backupFile;
  } catch (error) {
    logApp("warn", "Failed to backup library data before scan", {
      source,
      reason: error && error.message ? error.message : String(error),
    });
    return "";
  }
}

function restoreLibraryData(backupFile) {
  if (!backupFile || !fs.existsSync(backupFile)) {
    return false;
  }
  const destination = firstExistingLibraryDataPath();
  try {
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(backupFile, destination);
    logApp("warn", "Restored previous library data after suspicious scan drop", {
      destination,
      backupFile,
    });
    return true;
  } catch (error) {
    logApp("error", "Failed to restore previous library backup", {
      destination,
      backupFile,
      reason: error && error.message ? error.message : String(error),
    });
    return false;
  }
}

function loadLibraryWithCache(baseRecords) {
  return loadCategoriesFromCache(baseRecords, appDataRoot);
}

function runCommand(command, args, options) {
  return new Promise((resolve) => {
    execFile(command, args, options, (error, stdout, stderr) => {
      if (error) {
        resolve({ ok: false, error, stdout: String(stdout || ""), stderr: String(stderr || "") });
        return;
      }
      resolve({ ok: true, stdout: String(stdout || ""), stderr: String(stderr || "") });
    });
  });
}

function encryptionAvailable() {
  try {
    return Boolean(safeStorage && safeStorage.isEncryptionAvailable());
  } catch {
    return false;
  }
}

function readStoredSteamApiKey() {
  try {
    if (!fs.existsSync(steamKeyPath)) {
      return "";
    }
    const encrypted = fs.readFileSync(steamKeyPath);
    if (!encrypted || !encrypted.length || !encryptionAvailable()) {
      return "";
    }
    return String(safeStorage.decryptString(encrypted) || "").trim();
  } catch {
    return "";
  }
}

function resolveSteamApiKey() {
  const envKey = String(process.env.STEAM_API_KEY || "").trim();
  if (envKey) {
    return { value: envKey, source: "env" };
  }
  const storedKey = readStoredSteamApiKey();
  if (storedKey) {
    return { value: storedKey, source: "encrypted-local" };
  }
  return { value: "", source: "none" };
}

function steamApiKeyStatus() {
  const resolved = resolveSteamApiKey();
  return {
    hasKey: Boolean(resolved.value),
    source: resolved.source,
    encryptionAvailable: encryptionAvailable(),
  };
}

function validateSteamApiKey(rawValue) {
  const value = String(rawValue || "").trim();
  if (!value) {
    return { ok: false, message: "Steam API key cannot be empty." };
  }
  if (!/^[A-Fa-f0-9]{32}$/.test(value)) {
    return { ok: false, message: "Steam API key must be 32 hex characters." };
  }
  return { ok: true, value: value.toUpperCase() };
}

function saveSteamApiKey(rawValue) {
  if (!encryptionAvailable()) {
    return { ok: false, message: "Local encryption is not available on this system." };
  }
  const validated = validateSteamApiKey(rawValue);
  if (!validated.ok) {
    return validated;
  }

  try {
    const encrypted = safeStorage.encryptString(validated.value);
    fs.mkdirSync(path.dirname(steamKeyPath), { recursive: true });
    fs.writeFileSync(steamKeyPath, encrypted);
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      message: error && error.message ? error.message : "Failed to store Steam API key.",
    };
  }
}

async function runLibraryScan() {
  const projectRoot = process.cwd();
  const outputPath = path.join(projectRoot, "dist", "game-library");
  const srcPath = path.join(projectRoot, "src");
  const env = { ...process.env };

  if (fs.existsSync(srcPath)) {
    const currentPythonPath = String(env.PYTHONPATH || "").trim();
    env.PYTHONPATH = currentPythonPath ? `${srcPath};${currentPythonPath}` : srcPath;
  }

  const steamKey = resolveSteamApiKey();
  if (steamKey.value) {
    env.STEAM_API_KEY = steamKey.value;
    logApp("info", "Steam owned-library scan enabled", { keySource: steamKey.source });
  } else {
    logApp("warn", "Steam API key missing; Steam scan will include installed games only");
  }

  const baseArgs = ["-m", "game_launcher_scraper", "--output", outputPath];
  const attempts = [
    { command: "python", args: baseArgs },
    { command: "py", args: ["-3", ...baseArgs] },
  ];

  logApp("info", "Starting library scan", { outputPath });

  let lastFailure = "";
  for (const attempt of attempts) {
    const result = await runCommand(attempt.command, attempt.args, { cwd: projectRoot, env });
    if (result.ok) {
      logApp("info", "Library scan completed", {
        command: attempt.command,
        output: result.stdout.trim() || "Scan completed.",
      });
      return { ok: true, message: result.stdout.trim() || "Scan completed." };
    }

    const stderr = result.stderr.trim();
    const errorMessage = result.error && result.error.message ? result.error.message : "Scan failed";
    lastFailure = stderr || errorMessage;

    const code = result.error && result.error.code;
    const shouldRetry = code === "ENOENT" || /not recognized|cannot find/i.test(lastFailure);
    logApp("warn", "Library scan attempt failed", {
      command: attempt.command,
      retrying: shouldRetry,
      reason: lastFailure,
    });
    if (!shouldRetry) {
      break;
    }
  }

  logApp("error", "Library scan failed", { reason: lastFailure || "Unable to run scanner." });
  return { ok: false, message: lastFailure || "Unable to run scanner." };
}

async function refreshCategoriesInBackground(baseRecords, options) {
  const force = Boolean(options && options.force);
  if (categoryRefreshRunning) {
    logApp("debug", "Skipped category refresh because one is already running");
    return { updated: false, skipped: true };
  }

  categoryRefreshRunning = true;
  try {
    logApp(force ? "info" : "debug", "Refreshing categories", {
      records: baseRecords.length,
      force,
    });
    const refreshed = await refreshCategoryCache(baseRecords, appDataRoot, { force });
    if (refreshed.updated) {
      logApp("info", "Category refresh updated records", {
        processed: refreshed.processed,
        resolved: refreshed.resolved,
      });
      broadcast(LIBRARY_UPDATED_EVENT, {
        data: refreshed.data,
        reason: "categories",
      });
    } else {
      logApp("debug", "Category refresh found no updates");
    }

    const summary = refreshed.summary;
    if (summary) {
      logApp("info", "Category coverage summary", {
        total: summary.total,
        withCategories: summary.withCategories,
        withoutCategories: summary.withoutCategories,
        notYetFetched: summary.notInCache,
      });
      if (summary.reasons && summary.reasons.length) {
        for (const bucket of summary.reasons) {
          logApp("info", `Missing categories: ${bucket.reason}`, {
            count: bucket.count,
            samples: bucket.samples,
          });
        }
      }
    }
    return refreshed;
  } catch (error) {
    logApp("warn", "Category refresh failed", {
      reason: error && error.message ? error.message : String(error),
    });
    return { updated: false, error: error && error.message ? error.message : String(error) };
  } finally {
    categoryRefreshRunning = false;
  }
}

async function startScanIfNeeded(force = false, forceCategoryRefresh = false) {
  if (scanState.running) {
    logApp("debug", "Scan request ignored because a scan is already running");
    return;
  }

  if (!force && !isScanStale(scanState.lastScanAt)) {
    logApp("debug", "Scan skipped because cached scan is still fresh", { lastScanAt: scanState.lastScanAt });
    return;
  }

  scanState.running = true;
  scanState.lastScanError = "";
  emitScanStatus();

  const previousRecords = loadBaseLibrary();
  const previousCount = previousRecords.length;
  const backupFile = backupLibraryData();

  const result = await runLibraryScan();
  if (result.ok) {
    const baseRecords = loadBaseLibrary();
    const nextCount = baseRecords.length;
    const significantDrop = previousCount >= SIGNIFICANT_DROP_MIN_BASE
      && nextCount < Math.floor(previousCount * SIGNIFICANT_DROP_RATIO);

    if (significantDrop && restoreLibraryData(backupFile)) {
      scanState.lastScanError = `Scan result dropped from ${previousCount} to ${nextCount}. Kept previous library data.`;
      logApp("warn", "Suspicious scan drop detected; keeping previous library", {
        previousCount,
        nextCount,
      });
    } else {
      scanState.lastScanAt = new Date().toISOString();
      scanState.lastScanError = "";

      const data = loadLibraryWithCache(baseRecords);
      broadcast(LIBRARY_UPDATED_EVENT, {
        data,
        reason: "scan",
      });
      logApp("info", "Library data broadcast after successful scan", { records: data.length });
      void refreshCategoriesInBackground(baseRecords, { force: forceCategoryRefresh });
    }
  } else {
    scanState.lastScanError = result.message;
    logApp("error", "Scan ended with error", { reason: result.message });
  }

  scanState.running = false;
  persistScanState();
  emitScanStatus();
}

function createMainWindow() {
  const win = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 980,
    minHeight: 640,
    autoHideMenuBar: true,
    icon: path.join(__dirname, "assets", "games-librarian-icon.png"),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  win.loadFile(path.join(__dirname, "renderer", "index.html"));
  logApp("info", "Main window created");
}

function launchViaShellAppsFolder(target) {
  return new Promise((resolve, reject) => {
    const args = [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      `Start-Process \"${target.replace(/\"/g, '\\\"')}\"`,
    ];
    execFile("powershell", args, (error) => {
      if (error) {
        reject(error);
        return;
      }
      resolve();
    });
  });
}

async function launchGame(game) {
  const launchUrl = String((game && game.launch_url) || "").trim();
  if (!launchUrl) {
    logApp("error", "Launch failed: missing launch target");
    return { ok: false, message: "Missing launch target." };
  }

  const gameName = String((game && game.name) || "Unknown game");
  logApp("info", "Launch requested", { game: gameName, target: launchUrl });

  try {
    if (launchUrl.toLowerCase().startsWith("shell:appsfolder\\")) {
      await launchViaShellAppsFolder(launchUrl);
      logApp("info", "Launch sent via AppsFolder", { game: gameName });
      return { ok: true, message: "Launched via AppsFolder shell target." };
    }

    // Protocol-first path (steam://, com.epicgames.launcher://, goggalaxy://,
    // origin2://, ms-windows-store://, xbox://, etc.)
    const opened = await shell.openExternal(launchUrl);
    if (opened !== "") {
      logApp("warn", "Launch request returned non-empty message", { game: gameName, message: opened });
      return { ok: false, message: opened };
    }
    logApp("info", "Launch request sent", { game: gameName });
    return { ok: true, message: "Launch request sent." };
  } catch (error) {
    logApp("error", "Launch failed", {
      game: gameName,
      reason: error && error.message ? error.message : String(error),
    });
    return { ok: false, message: error && error.message ? error.message : String(error) };
  }
}

ipcMain.handle("library:load", async () => {
  try {
    logApp("info", "Library load requested");
    const baseRecords = loadBaseLibrary();
    const data = loadLibraryWithCache(baseRecords);

    void refreshCategoriesInBackground(baseRecords);
    void startScanIfNeeded(false);

    logApp("info", "Library loaded from local cache", { records: data.length });
    return { ok: true, data, scan: currentScanStatus() };
  } catch (error) {
    logApp("error", "Library load failed", {
      reason: error && error.message ? error.message : String(error),
    });
    return {
      ok: false,
      data: [],
      scan: currentScanStatus(),
      message: error && error.message ? error.message : String(error),
    };
  }
});

ipcMain.handle("game:launch", async (_event, game) => launchGame(game));

ipcMain.handle("library:scan", async () => {
  logApp("info", "Manual scan requested by user");
  void startScanIfNeeded(true, true);
  return { ok: true, running: true };
});

ipcMain.handle("library:refreshCategories", async () => {
  logApp("info", "Manual category refresh requested by user");
  try {
    const baseRecords = await loadBaseLibrary();
    void refreshCategoriesInBackground(baseRecords, { force: true });
    return { ok: true, running: true, count: baseRecords.length };
  } catch (error) {
    const reason = error && error.message ? error.message : String(error);
    logApp("error", "Failed to start category refresh", { reason });
    return { ok: false, message: reason };
  }
});

ipcMain.handle("steam:keyStatus", async () => steamApiKeyStatus());

ipcMain.handle("steam:keySave", async (_event, payload) => {
  const result = saveSteamApiKey(payload && payload.key);
  if (result.ok) {
    logApp("info", "Steam API key saved in encrypted local storage");
  } else {
    logApp("warn", "Steam API key save failed", {
      reason: result.message || "Unknown error",
    });
  }
  return result;
});

app.whenReady().then(() => {
  logApp("info", "App ready");

  // Ctrl+` is the standard terminal-toggle shortcut (VS Code, Terminal, etc.)
  // Ctrl+D is kept as alias but Chromium eats it before DOM keydown fires.
  function broadcastToggleConsole() {
    broadcast(TOGGLE_CONSOLE_EVENT, { ts: new Date().toISOString(), source: "globalShortcut" });
  }

  const backtickOk = globalShortcut.register("CommandOrControl+`", broadcastToggleConsole);
  // Ctrl+D as secondary — may fail if OS already owns it, that's fine
  const ctrlDOk = globalShortcut.register("CommandOrControl+D", broadcastToggleConsole);
  logApp("info", "Console shortcut registration", { backtick: backtickOk, ctrlD: ctrlDOk });

  createMainWindow();
  void startScanIfNeeded(false);

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    logApp("info", "All windows closed. Quitting app.");
    app.quit();
  }
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
});
