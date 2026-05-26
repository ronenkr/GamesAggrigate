(() => {
  const searchInput = document.getElementById("search");
  const launcherFilter = document.getElementById("launcher-filter");
  const categoryFilter = document.getElementById("category-filter");
  const availabilityFilter = document.getElementById("availability-filter");
  const grid = document.getElementById("grid");
  const emptyState = document.getElementById("empty-state");
  const gameCount = document.getElementById("game-count");
  const launcherCount = document.getElementById("launcher-count");
  const reloadButton = document.getElementById("reload");
  const reloadButtonLabel = reloadButton ? reloadButton.querySelector(".scan-button-label") : null;
  const scanNowButton = document.getElementById("scan-now");
  const scanNowButtonLabel = scanNowButton ? scanNowButton.querySelector(".scan-button-label") : null;
  const consoleToggleButton = document.getElementById("console-toggle");
  const settingsCogButton = document.getElementById("settings-cog");
  const scanStatus = document.getElementById("scan-status");
  const appConsole = document.getElementById("app-console");
  const appConsoleBody = document.getElementById("app-console-body");
  const appConsoleClear = document.getElementById("app-console-clear");
  const appConsoleHide = document.getElementById("app-console-hide");
  const steamKeyModal = document.getElementById("steam-key-modal");
  const steamKeyInput = document.getElementById("steam-key-input");
  const steamKeySaveButton = document.getElementById("steam-key-save");
  const steamKeyCancelButton = document.getElementById("steam-key-cancel");
  const steamKeyNote = document.getElementById("steam-key-note");
  const steamKeyCurrent = document.getElementById("steam-key-current");

  /** @type {any[]} */
  let allGames = [];
  let appConsoleVisible = false;
  let steamPromptShown = false;
  let steamModalActive = false;
  const STEAM_PROMPT_DISMISSED_KEY = "steamKeyPromptDismissed";

  function isSteamPromptDismissed() {
    try {
      return window.localStorage.getItem(STEAM_PROMPT_DISMISSED_KEY) === "1";
    } catch {
      return false;
    }
  }

  function setSteamPromptDismissed(value) {
    try {
      if (value) {
        window.localStorage.setItem(STEAM_PROMPT_DISMISSED_KEY, "1");
      } else {
        window.localStorage.removeItem(STEAM_PROMPT_DISMISSED_KEY);
      }
    } catch {
      // Best-effort preference persistence.
    }
  }

  function nowIso() {
    return new Date().toISOString();
  }

  function stringifyMeta(meta) {
    if (meta == null) {
      return "";
    }
    try {
      return JSON.stringify(meta);
    } catch {
      return String(meta);
    }
  }

  function appendConsoleLine(level, message, meta, ts) {
    if (!appConsoleBody) {
      return;
    }
    const line = document.createElement("div");
    const normalizedLevel = String(level || "info").toLowerCase();
    line.className = `app-console-line ${normalizedLevel}`;
    const timestamp = (() => {
      const date = new Date(ts || nowIso());
      return Number.isNaN(date.getTime()) ? nowIso() : date.toLocaleTimeString();
    })();
    const suffix = meta == null ? "" : ` ${stringifyMeta(meta)}`;
    line.textContent = `[${timestamp}] [${normalizedLevel.toUpperCase()}] ${String(message || "")}${suffix}`;
    appConsoleBody.appendChild(line);
    appConsoleBody.scrollTop = appConsoleBody.scrollHeight;
  }

  function setConsoleVisible(visible) {
    appConsoleVisible = Boolean(visible);
    if (!appConsole) {
      return;
    }
    appConsole.classList.toggle("hidden", !appConsoleVisible);
    if (appConsoleVisible) {
      appendConsoleLine("debug", "Console opened");
    }
  }

  function toggleConsole() {
    setConsoleVisible(!appConsoleVisible);
  }

  function setSteamPromptVisible(visible) {
    if (!steamKeyModal) {
      return;
    }
    steamModalActive = Boolean(visible);
    steamKeyModal.classList.toggle("hidden", !visible);
    if (visible && steamKeyInput) {
      steamKeyInput.focus();
      steamKeyInput.select();
    }
  }

  function setSteamPromptNote(message, className) {
    if (!steamKeyNote) {
      return;
    }
    steamKeyNote.textContent = String(message || "");
    steamKeyNote.classList.remove("error", "success");
    if (className) {
      steamKeyNote.classList.add(className);
    }
  }

  function setSteamCurrentKeyIndicator(hasKey) {
    if (!steamKeyCurrent) {
      return;
    }
    steamKeyCurrent.classList.toggle("hidden", !hasKey);
    if (hasKey) {
      steamKeyCurrent.textContent = "Saved key: ****** (you can paste a new key to replace it)";
    }
  }

  function closeSteamPrompt(reason) {
    setSteamPromptVisible(false);
    if (reason === "cancel") {
      setSteamPromptDismissed(true);
      appendConsoleLine("info", "Steam API key prompt canceled. Steam remains installed-only until a key is saved.");
    }
  }

  async function openSteamPrompt(options) {
    const settingsOpen = Boolean(options && options.fromSettings);
    if (!settingsOpen && steamPromptShown) {
      return;
    }

    if (!settingsOpen && isSteamPromptDismissed()) {
      return;
    }

    if (!settingsOpen) {
      steamPromptShown = true;
    }

    if (!window.gameLibraryApi || typeof window.gameLibraryApi.getSteamKeyStatus !== "function") {
      return;
    }

    let status;
    try {
      status = await window.gameLibraryApi.getSteamKeyStatus();
    } catch (error) {
      appendConsoleLine("warn", "Unable to check Steam key status", {
        reason: error && error.message ? error.message : String(error),
      });
      return;
    }

    if (!settingsOpen && status && status.hasKey) {
      return;
    }

    if (!steamKeyModal || !steamKeyInput || !steamKeySaveButton || !steamKeyCancelButton) {
      return;
    }

    if (status && status.hasKey) {
      setSteamPromptNote("A key is already saved. Enter a new key to replace it.", "");
    } else {
      setSteamPromptNote("The key is stored encrypted on this PC only.", "");
    }
    setSteamCurrentKeyIndicator(Boolean(status && status.hasKey));
    steamKeyInput.value = "";
    setSteamPromptVisible(true);
    if (!settingsOpen) {
      appendConsoleLine("info", "Steam API key not configured. Prompting for key (Cancel keeps installed-only mode).");
    }
  }

  async function saveSteamPromptKey() {
    if (!window.gameLibraryApi || typeof window.gameLibraryApi.saveSteamKey !== "function") {
      setSteamPromptNote("saveSteamKey API not available.", "error");
      return;
    }

    const key = String((steamKeyInput && steamKeyInput.value) || "").trim();
    if (steamKeySaveButton) {
      steamKeySaveButton.disabled = true;
    }
    if (steamKeyCancelButton) {
      steamKeyCancelButton.disabled = true;
    }
    try {
      const result = await window.gameLibraryApi.saveSteamKey(key);
      if (!result || !result.ok) {
        setSteamPromptNote((result && result.message) || "Failed to save Steam API key.", "error");
        return;
      }
      setSteamPromptDismissed(false);
      setSteamPromptNote("Steam API key saved. Running a new scan for full owned library...", "success");
      appendConsoleLine("info", "Steam API key saved. Starting fresh scan to include owned Steam games.");
      closeSteamPrompt();
      void scanLibrary();
    } catch (error) {
      setSteamPromptNote(error && error.message ? error.message : String(error), "error");
    } finally {
      if (steamKeySaveButton) {
        steamKeySaveButton.disabled = false;
      }
      if (steamKeyCancelButton) {
        steamKeyCancelButton.disabled = false;
      }
    }
  }

  function setScanButtonRunning(running) {
    if (!scanNowButton) {
      return;
    }
    const isRunning = Boolean(running);
    scanNowButton.classList.toggle("is-running", isRunning);
    scanNowButton.disabled = isRunning;
    scanNowButton.setAttribute("aria-busy", isRunning ? "true" : "false");
    if (scanNowButtonLabel) {
      scanNowButtonLabel.textContent = isRunning ? "Scanning..." : "Scan PC Now";
    }
  }

  function setRefreshCategoriesRunning(running) {
    if (!reloadButton) {
      return;
    }
    const isRunning = Boolean(running);
    reloadButton.classList.toggle("is-running", isRunning);
    // Reload button stays clickable; spinner just shows background category work.
    reloadButton.setAttribute("aria-busy", isRunning ? "true" : "false");
    if (reloadButtonLabel && !reloadButton.dataset.loading) {
      reloadButtonLabel.textContent = isRunning ? "Refreshing..." : "Reload";
    }
  }

  function formatTimestamp(value) {
    if (!value) {
      return "Never";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "Unknown";
    }
    return date.toLocaleString();
  }

  function setScanStatus(scan) {
    if (!scanStatus) {
      return;
    }

    if (!scan || typeof scan !== "object") {
      scanStatus.textContent = "Library loaded from local cache.";
      return;
    }

    if (scan.running) {
      scanStatus.textContent = "Scanning your PC library for new changes...";
      return;
    }

    const lastScan = formatTimestamp(scan.lastScanAt);
    if (!scan.lastScanAt) {
      scanStatus.textContent = "No previous PC scan timestamp found. A scan will start automatically.";
      return;
    }

    if (scan.stale) {
      scanStatus.textContent = `Last PC scan: ${lastScan}. Older than 1 week, auto-rescan queued.`;
      return;
    }

    if (scan.lastScanError) {
      scanStatus.textContent = `Last PC scan: ${lastScan}. Previous scan error: ${scan.lastScanError}`;
      return;
    }

    scanStatus.textContent = `Last PC scan: ${lastScan}.`;
  }

  function normalize(text) {
    return String(text || "").toLowerCase();
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll("\"", "&quot;")
      .replaceAll("'", "&#39;");
  }

  function updateLaunchers() {
    const launchers = [...new Set(allGames.map((item) => item.launcher))].sort((a, b) => a.localeCompare(b));

    launcherFilter.innerHTML = "";
    const all = document.createElement("option");
    all.value = "all";
    all.textContent = "All launchers";
    launcherFilter.appendChild(all);

    for (const launcher of launchers) {
      const option = document.createElement("option");
      option.value = launcher;
      option.textContent = launcher;
      launcherFilter.appendChild(option);
    }

    launcherCount.textContent = String(launchers.length);
  }

  function categoriesFor(item) {
    const extra = item && item.extra && typeof item.extra === "object" ? item.extra : null;
    const categories = extra && Array.isArray(extra.categories) ? extra.categories : [];
    return categories.filter(Boolean);
  }

  function updateCategories() {
    const categories = [...new Set(allGames.flatMap((item) => categoriesFor(item)))].sort((a, b) => a.localeCompare(b));
    const previousValue = categoryFilter.value || "all";

    categoryFilter.innerHTML = "";

    const all = document.createElement("option");
    all.value = "all";
    all.textContent = "All categories";
    categoryFilter.appendChild(all);

    const uncategorized = document.createElement("option");
    uncategorized.value = "uncategorized";
    uncategorized.textContent = "Uncategorized";
    categoryFilter.appendChild(uncategorized);

    for (const category of categories) {
      const option = document.createElement("option");
      option.value = category;
      option.textContent = category;
      categoryFilter.appendChild(option);
    }

    categoryFilter.value = [...categoryFilter.options].some((option) => option.value === previousValue)
      ? previousValue
      : "all";
  }

  function isInstalled(item) {
    const extra = item && item.extra && typeof item.extra === "object" ? item.extra : null;
    if (extra && typeof extra.installed === "boolean") {
      return extra.installed;
    }
    if (typeof item.install_path === "string" && item.install_path.trim()) {
      return true;
    }
    return normalize(item.launch_url).startsWith("shell:appsfolder\\");
  }

  function availabilityLabel(item) {
    return isInstalled(item) ? "Launchable" : "Installable";
  }

  function matches(item, query, launcher, category, availability) {
    const queryMatch = !query || normalize(item.search_blob).includes(query);
    const launcherMatch = launcher === "all" || item.launcher === launcher;
    const categories = categoriesFor(item);
    const categoryMatch = category === "all"
      || (category === "uncategorized" && categories.length === 0)
      || categories.includes(category);
    if (availability === "all") {
      return queryMatch && launcherMatch && categoryMatch;
    }

    const installed = isInstalled(item);
    const availabilityMatch = (availability === "launchable" && installed) || (availability === "installable" && !installed);
    return queryMatch && launcherMatch && categoryMatch && availabilityMatch;
  }

  async function handleLaunch(item, button, status) {
    const installed = isInstalled(item);
    const actionText = installed ? "Launch" : "Install";
    button.disabled = true;
    status.textContent = installed ? "Launching..." : "Opening install page...";
    status.className = "launch-status pending";

    try {
      appendConsoleLine("info", "Launch requested", { name: item && item.name, launcher: item && item.launcher });
      const result = await window.gameLibraryApi.launchGame(item);
      if (result && result.ok) {
        status.textContent = `${actionText} request sent`;
        status.className = "launch-status success";
      } else {
        appendConsoleLine("warn", "Launch failed", { name: item && item.name, reason: result && result.message });
        status.textContent = (result && result.message) || `${actionText} failed`;
        status.className = "launch-status error";
      }
    } catch (error) {
      appendConsoleLine("error", "Launch threw error", {
        name: item && item.name,
        reason: error && error.message ? error.message : String(error),
      });
      status.textContent = error && error.message ? error.message : String(error);
      status.className = "launch-status error";
    } finally {
      button.disabled = false;
      setTimeout(() => {
        status.textContent = "";
        status.className = "launch-status";
      }, 3000);
    }
  }

  function render() {
    const query = normalize(searchInput.value).trim();
    const launcher = launcherFilter.value;
    const category = categoryFilter.value;
    const availability = availabilityFilter.value;
    const filtered = allGames.filter((item) => matches(item, query, launcher, category, availability));

    gameCount.textContent = String(filtered.length);
    grid.innerHTML = "";
    emptyState.classList.toggle("hidden", filtered.length !== 0);

    for (const item of filtered) {
      const installed = isInstalled(item);
      const actionText = installed ? "Launch" : "Install";
      const availability = availabilityLabel(item);
      const categories = categoriesFor(item);
      const categoryMarkup = categories.length
        ? categories.map((value) => `<span class="category-chip">${escapeHtml(value)}</span>`).join("")
        : '<span class="category-chip muted">Uncategorized</span>';
      const card = document.createElement("article");
      card.className = "card";
      card.innerHTML = `
        <img class="icon" src="${escapeHtml(item.icon_url || "")}" alt="${escapeHtml(item.name)} icon" loading="lazy" referrerpolicy="no-referrer" />
        <div class="card-body">
          <h2>${escapeHtml(item.name)}</h2>
          <p>${escapeHtml(item.launcher)}</p>
          <div class="category-row">${categoryMarkup}</div>
          <span class="availability ${installed ? "launchable" : "installable"}">${availability}</span>
        </div>
        <div class="card-actions">
          <button class="launch-btn ${installed ? "launch" : "install"}" type="button">${actionText}</button>
          <span class="launch-status"></span>
        </div>
      `;

      const icon = card.querySelector(".icon");
      icon.addEventListener("error", () => {
        icon.src = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 900'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='%231a2942'/><stop offset='1' stop-color='%230a1220'/></linearGradient></defs><rect width='600' height='900' fill='url(%23g)'/><text x='50%25' y='50%25' dominant-baseline='middle' text-anchor='middle' fill='%2396a6bf' font-size='54' font-family='Segoe UI'>No Image</text></svg>";
      }, { once: true });

      const launchButton = card.querySelector(".launch-btn");
      const status = card.querySelector(".launch-status");
      launchButton.addEventListener("click", () => {
        handleLaunch(item, launchButton, status);
      });

      grid.appendChild(card);
    }
  }

  async function loadLibrary() {
    appendConsoleLine("info", "Reloading library data");
    reloadButton.dataset.loading = "1";
    reloadButton.disabled = true;
    if (reloadButtonLabel) {
      reloadButtonLabel.textContent = "Loading...";
    }
    try {
      const result = await window.gameLibraryApi.loadLibrary();
      if (!result || !result.ok) {
        allGames = [];
        console.error("Failed to load library:", result && result.message);
        appendConsoleLine("error", "Library load failed", { reason: result && result.message });
        setScanStatus(result && result.scan);
      } else {
        allGames = Array.isArray(result.data) ? result.data : [];
        appendConsoleLine("info", "Library loaded", { records: allGames.length });
        setScanStatus(result.scan);
      }
      updateLaunchers();
      updateCategories();
      render();
    } finally {
      delete reloadButton.dataset.loading;
      reloadButton.disabled = false;
      if (reloadButtonLabel) {
        reloadButtonLabel.textContent = "Reload";
      }
    }

    // Kick off a forced category refresh in the background so DOOM and friends
    // get fresh genres on every Reload click.
    void refreshAllCategories();
  }

  async function scanLibrary() {
    if (!window.gameLibraryApi || typeof window.gameLibraryApi.scanLibrary !== "function") {
      appendConsoleLine("error", "scanLibrary API not available");
      return;
    }
    setScanButtonRunning(true);
    appendConsoleLine("info", "Manual PC scan requested");
    try {
      await window.gameLibraryApi.scanLibrary();
    } catch (err) {
      setScanButtonRunning(false);
      appendConsoleLine("error", "Scan request failed", { reason: err && err.message ? err.message : String(err) });
    }
  }

  searchInput.addEventListener("input", render);
  launcherFilter.addEventListener("change", render);
  categoryFilter.addEventListener("change", render);
  availabilityFilter.addEventListener("change", render);
  reloadButton.addEventListener("click", loadLibrary);

  if (scanNowButton) {
    scanNowButton.addEventListener("click", scanLibrary);
  }

  if (settingsCogButton) {
    settingsCogButton.addEventListener("click", () => {
      void openSteamPrompt({ fromSettings: true });
    });
  }

  if (steamKeyCancelButton) {
    steamKeyCancelButton.addEventListener("click", () => closeSteamPrompt("cancel"));
  }

  if (steamKeySaveButton) {
    steamKeySaveButton.addEventListener("click", () => {
      void saveSteamPromptKey();
    });
  }

  if (steamKeyModal) {
    steamKeyModal.addEventListener("click", (event) => {
      if (event.target === steamKeyModal) {
        closeSteamPrompt("cancel");
      }
    });
  }

  async function refreshAllCategories() {
    if (!window.gameLibraryApi || typeof window.gameLibraryApi.refreshCategories !== "function") {
      appendConsoleLine("error", "refreshCategories API not available");
      return;
    }
    setRefreshCategoriesRunning(true);
    appendConsoleLine("info", "Force-refreshing all categories from Steam & RAWG");
    try {
      const result = await window.gameLibraryApi.refreshCategories();
      if (!result || !result.ok) {
        appendConsoleLine("error", "Category refresh failed to start", { reason: result && result.message });
      } else {
        appendConsoleLine("info", "Category refresh started", { count: result.count });
      }
    } catch (err) {
      appendConsoleLine("error", "Category refresh request failed", { reason: err && err.message ? err.message : String(err) });
    }
    // Background refresh — onLibraryUpdated(reason=categories) will clear the spinner.
    setTimeout(() => setRefreshCategoriesRunning(false), 60000);
  }

  if (consoleToggleButton) {
    consoleToggleButton.addEventListener("click", toggleConsole);
  }

  if (appConsoleClear) {
    appConsoleClear.addEventListener("click", () => {
      if (appConsoleBody) {
        appConsoleBody.innerHTML = "";
      }
      appendConsoleLine("debug", "Console cleared");
    });
  }

  if (appConsoleHide) {
    appConsoleHide.addEventListener("click", () => setConsoleVisible(false));
  }

  // Use window so the handler fires regardless of which element has focus.
  // Primary: Ctrl+` (backtick) — same shortcut VS Code/Terminal use.
  // Fallback: Ctrl+D in case globalShortcut catches it before Chromium does.
  window.addEventListener("keydown", (event) => {
    if (steamModalActive && event.key === "Escape") {
      event.preventDefault();
      closeSteamPrompt("cancel");
      return;
    }
    if (steamModalActive && event.key === "Enter" && event.target === steamKeyInput) {
      event.preventDefault();
      void saveSteamPromptKey();
      return;
    }
    const isBacktick = event.key === "`" || event.key === "Dead";
    const isD = event.key === "d" || event.key === "D";
    if (event.ctrlKey && (isBacktick || isD)) {
      event.preventDefault();
      event.stopPropagation();
      toggleConsole();
    }
  }, true); // capture phase — runs before any focused element can swallow it

  window.addEventListener("error", (event) => {
    appendConsoleLine("error", "Window error", {
      message: event && event.message,
      source: event && event.filename,
      line: event && event.lineno,
      column: event && event.colno,
    });
  });

  window.addEventListener("unhandledrejection", (event) => {
    const reason = event && event.reason;
    appendConsoleLine("error", "Unhandled promise rejection", {
      reason: reason && reason.message ? reason.message : String(reason),
    });
  });

  if (window.gameLibraryApi && typeof window.gameLibraryApi.onLibraryUpdated === "function") {
    window.gameLibraryApi.onLibraryUpdated((payload) => {
      if (!payload || !Array.isArray(payload.data)) {
        return;
      }
      allGames = payload.data;
      appendConsoleLine("info", "Library updated", {
        reason: payload.reason || "unknown",
        records: allGames.length,
      });
      if (payload.reason === "categories") {
        setRefreshCategoriesRunning(false);
      }
      updateLaunchers();
      updateCategories();
      render();
    });
  }

  if (window.gameLibraryApi && typeof window.gameLibraryApi.onScanStatus === "function") {
    window.gameLibraryApi.onScanStatus((payload) => {
      appendConsoleLine("debug", "Scan status changed", payload);
      setScanButtonRunning(payload && payload.running);
      setScanStatus(payload);
    });
  }

  if (window.gameLibraryApi && typeof window.gameLibraryApi.onAppLog === "function") {
    window.gameLibraryApi.onAppLog((entry) => {
      if (!entry || typeof entry !== "object") {
        return;
      }
      appendConsoleLine(entry.level, entry.message, entry.meta, entry.ts);
    });
  }

  if (window.gameLibraryApi && typeof window.gameLibraryApi.onToggleConsole === "function") {
    window.gameLibraryApi.onToggleConsole(() => {
      toggleConsole();
    });
  }

  appendConsoleLine("info", "Renderer ready. Use Console button or Ctrl+` to toggle console.");

  loadLibrary();
  void openSteamPrompt({ fromSettings: false });
})();
