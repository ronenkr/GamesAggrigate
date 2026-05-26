const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const KNOWN_COVER_OVERRIDES = {
  crysis: "https://cdn.cloudflare.steamstatic.com/steam/apps/17300/library_600x900.jpg",
};

const STEAM_NON_GAME_APP_IDS = new Set([
  "228980", // Steamworks Common Redistributables
  "250820", // SteamVR
  "1070560", // Steam Linux Runtime
  "1391110", // Steam Linux Runtime - Soldier
  "1628350", // Steam Linux Runtime - Sniper
  "1493710", // Proton Experimental
]);

const STEAM_NON_GAME_NAME_PATTERNS = [
  /steamworks\s+common\s+redistributables/i,
  /^steamvr$/i,
  /steam\s+linux\s+runtime/i,
  /^proton(\s|$)/i,
  /source\s+sdk/i,
  /dedicated\s+server/i,
];

const GENERIC_NON_GAME_NAME_PATTERNS = [
  /\bredistributable(s)?\b/i,
  /\bruntime\b/i,
  /\bsdk\b/i,
  /\bdedicated\s+server\b/i,
  /\bserver\s+browser\b/i,
  /\bcompatibility\s+tool\b/i,
  /\bdriver\s+updater\b/i,
];

function parseWindowAssignment(raw) {
  const cleaned = raw
    .replace(/^\s*window\.__GAME_LIBRARY__\s*=\s*/m, "")
    .replace(/;\s*$/, "");
  return JSON.parse(cleaned);
}

function isRemoteUrl(value) {
  const text = String(value || "").trim().toLowerCase();
  return text.startsWith("http://") || text.startsWith("https://") || text.startsWith("file://") || text.startsWith("data:");
}

function resolveIconUrl(iconUrl, libraryRoot) {
  const value = String(iconUrl || "").trim();
  if (!value) {
    return value;
  }
  if (isRemoteUrl(value)) {
    return value;
  }

  const normalized = value.replace(/\//g, path.sep);
  const absolute = path.resolve(libraryRoot, normalized);
  return pathToFileURL(absolute).href;
}

function normalizeRecord(record, libraryRoot) {
  const next = { ...record };
  next.icon_url = resolveIconUrl(record && record.icon_url, libraryRoot);

  const launcher = String((next && next.launcher) || "").trim().toLowerCase();
  const launchUrl = String((next && next.launch_url) || "").trim();
  if (launcher === "xbox") {
    const match = launchUrl.match(/^ms-windows-store:\/\/pdp\/\?productid=([a-z0-9]+)$/i);
    if (match) {
      next.launch_url = `xbox://game/?productId=${match[1]}`;
    }
  }

  const key = String(next && next.name ? next.name : "").trim().toLowerCase();
  const override = KNOWN_COVER_OVERRIDES[key];
  if (override && typeof next.icon_url === "string" && /assets\/icons\/.*\.svg$/i.test(next.icon_url)) {
    next.icon_url = override;
  }

  return next;
}

function shouldKeepRecord(record) {
  const launcher = String((record && record.launcher) || "").trim().toLowerCase();
  const name = String((record && record.name) || "").trim();
  const appId = String((record && record.app_id) || "").trim();

  if (!name) {
    return false;
  }

  if (launcher === "steam") {
    if (STEAM_NON_GAME_APP_IDS.has(appId)) {
      return false;
    }
    for (const pattern of STEAM_NON_GAME_NAME_PATTERNS) {
      if (pattern.test(name)) {
        return false;
      }
    }
  }

  for (const pattern of GENERIC_NON_GAME_NAME_PATTERNS) {
    if (pattern.test(name)) {
      return false;
    }
  }

  return true;
}

function loadLibraryData(appRoot) {
  const candidates = [
    path.join(appRoot, "dist", "game-library", "assets", "games-data.js"),
    path.join(process.cwd(), "dist", "game-library", "assets", "games-data.js"),
  ];

  for (const candidate of candidates) {
    if (!fs.existsSync(candidate)) {
      continue;
    }
    const raw = fs.readFileSync(candidate, "utf8");
    const parsed = parseWindowAssignment(raw);
    if (Array.isArray(parsed)) {
      const libraryRoot = path.dirname(path.dirname(candidate));
      return parsed
        .map((record) => normalizeRecord(record, libraryRoot))
        .filter((record) => shouldKeepRecord(record));
    }
  }

  return [];
}

module.exports = {
  loadLibraryData,
};
