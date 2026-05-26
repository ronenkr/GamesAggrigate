const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const ROOT = process.cwd();
const ELECTRON_DIR = path.join(ROOT, "node_modules", "electron");
const DIST_DIR = path.join(ELECTRON_DIR, "dist");
const PATH_TXT = path.join(ELECTRON_DIR, "path.txt");
const EXE_PATH = path.join(DIST_DIR, "electron.exe");

function exists(p) {
  try {
    fs.accessSync(p, fs.constants.F_OK);
    return true;
  } catch {
    return false;
  }
}

function runNodeInstallScript() {
  const installScript = path.join(ELECTRON_DIR, "install.js");
  if (!exists(installScript)) {
    throw new Error("Electron install.js not found. Is electron installed?");
  }

  const result = spawnSync(process.execPath, [installScript], {
    cwd: ROOT,
    stdio: "inherit",
    env: process.env,
  });

  if (result.status !== 0) {
    throw new Error("Electron install.js failed.");
  }
}

function requireFromElectronNodeModules(moduleName) {
  try {
    return require(moduleName);
  } catch {
    const candidate = path.join(ELECTRON_DIR, "node_modules", moduleName);
    return require(candidate);
  }
}

async function manualRepair() {
  const { downloadArtifact } = requireFromElectronNodeModules("@electron/get");
  const electronPkg = JSON.parse(fs.readFileSync(path.join(ELECTRON_DIR, "package.json"), "utf8"));

  const zipPath = await downloadArtifact({
    version: electronPkg.version,
    artifactName: "electron",
    platform: process.platform,
    arch: process.arch,
  });

  if (exists(DIST_DIR)) {
    fs.rmSync(DIST_DIR, { recursive: true, force: true });
  }
  fs.mkdirSync(DIST_DIR, { recursive: true });

  if (process.platform === "win32") {
    const result = spawnSync(
      "powershell",
      [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        `Expand-Archive -Path '${zipPath.replace(/'/g, "''")}' -DestinationPath '${DIST_DIR.replace(/'/g, "''")}' -Force`,
      ],
      {
        cwd: ROOT,
        stdio: "inherit",
        env: process.env,
      }
    );
    if (result.status !== 0) {
      throw new Error("Expand-Archive failed.");
    }
  } else {
    const extract = requireFromElectronNodeModules("extract-zip");
    await extract(zipPath, { dir: DIST_DIR });
  }

  fs.writeFileSync(PATH_TXT, process.platform === "win32" ? "electron.exe" : "electron", "utf8");
  fs.writeFileSync(path.join(DIST_DIR, "version"), `v${electronPkg.version}`, "utf8");
}

async function ensureBinary() {
  if (exists(PATH_TXT) && exists(EXE_PATH)) {
    console.log("[electron] binary already present.");
    return;
  }

  console.log("[electron] repairing missing binary via install.js...");
  try {
    runNodeInstallScript();
  } catch (error) {
    console.warn("[electron] install.js path failed:", error.message);
  }

  if (exists(PATH_TXT) && exists(EXE_PATH)) {
    console.log("[electron] repair succeeded with install.js.");
    return;
  }

  console.log("[electron] running manual repair fallback...");
  await manualRepair();

  if (!(exists(PATH_TXT) && exists(EXE_PATH))) {
    throw new Error("Electron binary still missing after repair.");
  }

  console.log("[electron] manual repair succeeded.");
}

ensureBinary().catch((error) => {
  console.error("[electron] ensure binary failed:", error && error.message ? error.message : String(error));
  process.exit(1);
});
