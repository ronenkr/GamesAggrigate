const { contextBridge, ipcRenderer } = require("electron");

function subscribe(channel, callback) {
  if (typeof callback !== "function") {
    return () => {};
  }
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => {
    ipcRenderer.removeListener(channel, listener);
  };
}

contextBridge.exposeInMainWorld("gameLibraryApi", {
  loadLibrary: () => ipcRenderer.invoke("library:load"),
  scanLibrary: () => ipcRenderer.invoke("library:scan"),
  refreshCategories: () => ipcRenderer.invoke("library:refreshCategories"),
  getSteamKeyStatus: () => ipcRenderer.invoke("steam:keyStatus"),
  saveSteamKey: (key) => ipcRenderer.invoke("steam:keySave", { key }),
  launchGame: (game) => ipcRenderer.invoke("game:launch", game),
  onLibraryUpdated: (callback) => subscribe("library:updated", callback),
  onScanStatus: (callback) => subscribe("scan:status", callback),
  onAppLog: (callback) => subscribe("app:log", callback),
  onToggleConsole: (callback) => subscribe("app:toggle-console", callback),
});
