const fs = require("node:fs");
const path = require("node:path");
const https = require("node:https");

const CATEGORY_CACHE_FILE = "rawg-category-cache.json";
const CATEGORY_STATE_FILE = "rawg-category-state.json";
const CACHE_VERSION = 3;
const MAX_FETCH_PER_LOAD = 80;
const MAX_FETCH_PER_FORCE = 2000;
const FETCH_CONCURRENCY = 6;
const REQUEST_TIMEOUT_MS = 8000;
const MAX_CATEGORIES_PER_GAME = 4;
const MIN_FETCH_INTERVAL_MS = 12 * 60 * 60 * 1000;
const EMPTY_CATEGORY_RETRY_MS = 0;

const KNOWN_GENRES = new Set([
  "Action",
  "Adventure",
  "RPG",
  "Strategy",
  "Simulation",
  "Sports",
  "Racing",
  "Puzzle",
  "Platformer",
  "Shooter",
  "First-Person Shooter",
  "Third-Person Shooter",
  "Fighting",
  "Arcade",
  "Indie",
  "Casual",
  "MMO",
  "Massively Multiplayer",
  "Family",
  "Educational",
  "Card Game",
  "Board Game",
  "Free to Play",
  "Early Access",
  "Turn-Based Strategy",
  "Real-Time Strategy",
  "Horror",
  "Survival",
  "Open World",
  "Stealth",
  "Sandbox",
  "Visual Novel",
  "Roguelike",
  "Metroidvania",
  "VR",
  "Music",
  "Rhythm",
  "Tower Defense",
  "MOBA",
  "Battle Royale",
  "Hack and Slash",
  "Beat 'em up",
  "Pinball",
  "Party",
  "Trivia",
  "Point-and-Click",
]);

function isPlausibleGenre(value) {
  const v = String(value || "").trim();
  if (!v) {
    return false;
  }
  if (KNOWN_GENRES.has(v)) {
    return true;
  }
  // Reject obvious non-genre noise: colons, parentheses, digits, very long names.
  if (/[:()\[\]]/.test(v)) {
    return false;
  }
  if (/\d/.test(v)) {
    return false;
  }
  if (v.length > 28) {
    return false;
  }
  // Reject things with 4+ words — real genres are 1-3 words.
  if (v.split(/\s+/).length > 3) {
    return false;
  }
  return false; // Conservative: only accept whitelisted genres.
}

function sanitizeCategories(list) {
  const out = [];
  if (!Array.isArray(list)) {
    return out;
  }
  for (const raw of list) {
    const v = String(raw || "").trim();
    if (!v) {
      continue;
    }
    if (!isPlausibleGenre(v)) {
      continue;
    }
    if (!out.includes(v)) {
      out.push(v);
    }
  }
  return out;
}

function slugifyTitle(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function titleSlugCandidates(title) {
  const base = slugifyTitle(title);
  if (!base) {
    return [];
  }
  const candidates = new Set([base]);

  // Strip parenthetical suffixes from the original title before re-slugifying (e.g. "DOOM Eternal (PC)" -> "DOOM Eternal").
  const noParen = String(title || "").replace(/\s*\([^)]*\)\s*/g, " ").trim();
  if (noParen && noParen.toLowerCase() !== String(title || "").toLowerCase()) {
    const slug = slugifyTitle(noParen);
    if (slug) {
      candidates.add(slug);
    }
  }

  // Strip trailing platform / launcher / edition noise.
  const editionStrip = /-(pc|windows|xbox|playstation|ps4|ps5|battlemode|multiplayer|singleplayer|standard|definitive|deluxe|ultimate|gold|game-of-the-year|goty|enhanced|complete|legendary|anniversary|remastered|remaster|hd|extended|collectors|collector|directors-cut|director-cut)(-edition)?$/i;
  let work = base;
  for (let i = 0; i < 4; i += 1) {
    const next = work.replace(editionStrip, "").replace(/-edition$/i, "");
    if (next === work) {
      break;
    }
    work = next;
    if (work) {
      candidates.add(work);
    }
  }

  const cleaned = base.replace(/-tm$/i, "").replace(/-r$/i, "");
  if (cleaned && cleaned !== base) {
    candidates.add(cleaned);
  }

  const lower = String(title || "").toLowerCase().trim();
  if (lower === "doom" || lower.startsWith("doom ") || lower === "doom + doom ii") {
    candidates.add("doom-4");
    candidates.add("doom-2016");
    candidates.add("doom-1993");
    candidates.add("doom-ii");
    candidates.add("doom-3");
    candidates.add("doom-eternal");
    candidates.add("doom-64");
  }
  if (lower === "prey") {
    candidates.add("prey-2017");
  }
  if (lower === "wolfenstein") {
    candidates.add("wolfenstein-the-new-order");
  }

  return [...candidates];
}

function normalizeCategoryName(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }

  const lc = raw.toLowerCase();

  // Strong RPG / shooter / strategy recognition BEFORE other rules so we map
  // RAWG's verbose names like "Role-Playing Games (RPG)" cleanly.
  if (/role[\s-]*playing/i.test(raw) || /\brpg\b/i.test(raw)) {
    return "RPG";
  }
  if (/first[\s-]*person\s+shooter/i.test(raw) || /\bfps\b/i.test(raw)) {
    return "First-Person Shooter";
  }
  if (/third[\s-]*person\s+shooter/i.test(raw)) {
    return "Third-Person Shooter";
  }
  if (/turn[\s-]*based.*strateg/i.test(raw)) {
    return "Turn-Based Strategy";
  }
  if (/real[\s-]*time.*strateg/i.test(raw)) {
    return "Real-Time Strategy";
  }
  if (/massively\s+multiplayer/i.test(raw) || lc === "mmo") {
    return "MMO";
  }

  const known = {
    "shooter": "Shooter",
    "strategy": "Strategy",
    "adventure": "Adventure",
    "action": "Action",
    "platformer": "Platformer",
    "puzzle": "Puzzle",
    "racing": "Racing",
    "sports": "Sports",
    "fighting": "Fighting",
    "simulation": "Simulation",
    "family": "Family",
    "educational": "Educational",
    "card": "Card Game",
    "board games": "Board Game",
    "arcade": "Arcade",
    "casual": "Casual",
    "indie": "Indie",
    "free to play": "Free to Play",
    "early access": "Early Access",
    "violent": "",
    "gore": "",
    "nudity": "",
    "sexual content": "",
  };

  if (lc in known) {
    return known[lc];
  }

  return raw
    .split(/\s+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

function readCache(cacheRoot) {
  const filePath = path.join(cacheRoot, CATEGORY_CACHE_FILE);
  try {
    const payload = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (!payload || typeof payload.entries !== "object") {
      return { filePath, entries: {} };
    }
    const entries = payload.entries;
    const upgraded = {};
    for (const [key, entry] of Object.entries(entries)) {
      if (!entry || typeof entry !== "object") {
        continue;
      }
      const cleaned = sanitizeCategories(entry.categories);
      // If entry had categories but ALL got rejected by the sanitizer, it was poisoned — drop it so we re-fetch.
      const wasPoisoned = Array.isArray(entry.categories) && entry.categories.length > 0 && cleaned.length === 0;
      if (wasPoisoned) {
        continue;
      }
      upgraded[key] = { ...entry, categories: cleaned };
    }
    return { filePath, entries: upgraded };
  } catch {
    return { filePath, entries: {} };
  }
}

function writeCache(filePath, entries) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify({ version: CACHE_VERSION, entries }, null, 2), "utf8");
}

function readState(cacheRoot) {
  const filePath = path.join(cacheRoot, CATEGORY_STATE_FILE);
  try {
    const payload = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (!payload || typeof payload !== "object") {
      return { filePath, lastFetchAt: "" };
    }
    return { filePath, lastFetchAt: String(payload.lastFetchAt || "") };
  } catch {
    return { filePath, lastFetchAt: "" };
  }
}

function writeState(filePath, lastFetchAt) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify({ lastFetchAt }, null, 2), "utf8");
}

function shouldFetchNow(lastFetchAt) {
  if (!lastFetchAt) {
    return true;
  }
  const previous = Date.parse(lastFetchAt);
  if (!Number.isFinite(previous)) {
    return true;
  }
  return Date.now() - previous >= MIN_FETCH_INTERVAL_MS;
}

function shouldRefreshEmptyCategoryEntry(entry) {
  if (!entry || typeof entry !== "object") {
    return true;
  }
  if (Array.isArray(entry.categories) && entry.categories.length > 0) {
    return false;
  }

  const parsed = Date.parse(String(entry.fetchedAt || ""));
  if (!Number.isFinite(parsed)) {
    return true;
  }
  return Date.now() - parsed >= EMPTY_CATEGORY_RETRY_MS;
}

function requestText(url, options) {
  const headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Games Librarian/1.0",
    "Accept-Language": "en-US,en;q=0.9",
    ...(options && options.headers),
  };
  return new Promise((resolve, reject) => {
    const req = https.request(url, { headers }, (res) => {
      if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        const depth = (options && options.redirectDepth) || 0;
        if (depth >= 3) {
          reject(new Error(`Too many redirects from ${url}`));
          return;
        }
        const nextUrl = new URL(res.headers.location, url).toString();
        res.resume();
        resolve(requestText(nextUrl, { ...(options || {}), redirectDepth: depth + 1 }));
        return;
      }
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
      });
      res.on("end", () => {
        if (res.statusCode && res.statusCode >= 400) {
          reject(new Error(`HTTP ${res.statusCode} from ${url}`));
          return;
        }
        resolve(body);
      });
    });
    req.setTimeout(REQUEST_TIMEOUT_MS, () => {
      req.destroy(new Error(`Request timed out: ${url}`));
    });
    req.on("error", reject);
    req.end();
  });
}

function collectGenresFromBucket(bucket) {
  const categories = [];
  if (!Array.isArray(bucket)) {
    return categories;
  }
  for (const entry of bucket) {
    const name = entry && (entry.name || entry.slug);
    if (!name) {
      continue;
    }
    const normalized = normalizeCategoryName(name);
    if (normalized && !categories.includes(normalized)) {
      categories.push(normalized);
    }
  }
  return categories;
}

function extractGenresFromNextData(html) {
  const match = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/i);
  if (!match) {
    return [];
  }
  let data;
  try {
    data = JSON.parse(match[1]);
  } catch {
    return [];
  }

  const buckets = [];
  const props = data && data.props;
  const pageProps = props && props.pageProps;
  if (pageProps) {
    if (pageProps.game && Array.isArray(pageProps.game.genres)) {
      buckets.push(pageProps.game.genres);
    }
    if (Array.isArray(pageProps.genres)) {
      buckets.push(pageProps.genres);
    }
    const state = pageProps.initialState;
    if (state) {
      for (const candidate of [state.game, state.gameSeo, state.gameInfo]) {
        if (candidate && Array.isArray(candidate.genres)) {
          buckets.push(candidate.genres);
        }
      }
    }
  }

  for (const bucket of buckets) {
    const categories = collectGenresFromBucket(bucket);
    if (categories.length) {
      return categories;
    }
  }
  return [];
}

function extractGenresFromInlineJson(html) {
  // RAWG inlines its app state as raw JSON in the HTML. Match every "genres":[ ... ]
  // occurrence and only accept arrays where every element is a well-formed RAWG genre
  // object {"id":N,"name":"X","slug":"x",...}. This avoids matching navigation widgets
  // or unrelated arrays that just happen to contain "name" fields.
  const out = [];
  const seen = new Set();
  const re = /"genres"\s*:\s*\[([^\]]{0,8000})\]/g;
  let match = re.exec(html);
  while (match) {
    const chunk = match[1];
    const items = chunk.match(/\{"id":\d+,"name":"[^"]+","slug":"[a-z0-9-]+"[^}]*\}/g);
    if (items && items.length) {
      for (const item of items) {
        const m = item.match(/"name":"([^"]+)"/);
        if (m) {
          const name = normalizeCategoryName(m[1]);
          if (name && !seen.has(name)) {
            seen.add(name);
            out.push(name);
          }
        }
      }
      if (out.length) {
        return out;
      }
    }
    match = re.exec(html);
  }
  return out;
}

function extractGenresFromRawgHtml(html) {
  const fromNext = extractGenresFromNextData(html);
  if (fromNext.length) {
    return fromNext;
  }
  return extractGenresFromInlineJson(html);
}

function extractSlugsFromSearchHtml(html) {
  const slugs = [];
  const seen = new Set();
  const regex = /href="\/games\/([^"?#/]+)["?]/gi;
  let match = regex.exec(html);
  while (match) {
    const slug = String(match[1] || "").trim().toLowerCase();
    if (slug && !seen.has(slug)) {
      seen.add(slug);
      slugs.push(slug);
    }
    match = regex.exec(html);
  }
  return slugs;
}

async function fetchSteamGenresForAppId(appId) {
  const id = String(appId || "").trim();
  if (!id || !/^\d+$/.test(id)) {
    return { categories: [], reason: "no-appid" };
  }
  try {
    const body = await requestText(
      `https://store.steampowered.com/api/appdetails?appids=${id}&cc=us&l=en`,
      { headers: { Accept: "application/json" } },
    );
    const payload = JSON.parse(body);
    const entry = payload && payload[id];
    if (!entry || !entry.success || !entry.data) {
      return { categories: [], reason: "steam-appdetails-empty" };
    }
    const genres = Array.isArray(entry.data.genres) ? entry.data.genres : [];
    const categories = [];
    for (const g of genres) {
      const name = g && (g.description || g.name);
      if (!name) {
        continue;
      }
      const normalized = normalizeCategoryName(name);
      if (normalized && !categories.includes(normalized)) {
        categories.push(normalized);
      }
    }
    if (!categories.length) {
      return { categories: [], reason: genres.length ? "steam-genres-rejected" : "steam-no-genres" };
    }
    return { categories, reason: "" };
  } catch (err) {
    return { categories: [], reason: `steam-error:${(err && err.message) || "unknown"}` };
  }
}

async function fetchSteamGenresByTitle(title) {
  const term = String(title || "").trim();
  if (!term) {
    return { categories: [], reason: "no-title" };
  }
  try {
    const body = await requestText(
      `https://store.steampowered.com/api/storesearch/?term=${encodeURIComponent(term)}&l=en&cc=us`,
      { headers: { Accept: "application/json" } },
    );
    const payload = JSON.parse(body);
    const items = payload && Array.isArray(payload.items) ? payload.items : [];
    if (!items.length) {
      return { categories: [], reason: "steam-search-no-results" };
    }
    for (const item of items.slice(0, 3)) {
      const result = await fetchSteamGenresForAppId(item && item.id);
      if (result.categories.length) {
        return result;
      }
    }
    return { categories: [], reason: "steam-search-no-genres" };
  } catch (err) {
    return { categories: [], reason: `steam-search-error:${(err && err.message) || "unknown"}` };
  }
}

async function fetchRawgCategoriesForTitle(title) {
  const baseSlug = slugifyTitle(title);
  if (!baseSlug) {
    return { categories: [], reason: "no-slug" };
  }

  const tried = new Set();
  let networkErrors = 0;
  let pagesLoaded = 0;
  let pagesWithGenresButRejected = 0;

  const tryCandidate = async (candidate) => {
    if (tried.has(candidate)) {
      return null;
    }
    tried.add(candidate);
    try {
      const html = await requestText(`https://rawg.io/games/${candidate}`);
      pagesLoaded += 1;
      const rawCategories = extractGenresFromRawgHtml(html);
      if (rawCategories.length) {
        return rawCategories;
      }
      // Page loaded but no usable genre array — was it missing entirely or stripped by sanitizer?
      if (/"genres"\s*:\s*\[/.test(html)) {
        pagesWithGenresButRejected += 1;
      }
      return null;
    } catch {
      networkErrors += 1;
      return null;
    }
  };

  for (const candidate of titleSlugCandidates(title)) {
    const result = await tryCandidate(candidate);
    if (result && result.length) {
      return { categories: result, reason: "" };
    }
  }

  try {
    const searchHtml = await requestText(`https://rawg.io/search?query=${encodeURIComponent(title)}`);
    const searchSlugs = extractSlugsFromSearchHtml(searchHtml).slice(0, 8);
    if (!searchSlugs.length && pagesLoaded === 0 && networkErrors === 0) {
      return { categories: [], reason: "rawg-no-search-results" };
    }
    for (const candidate of searchSlugs) {
      const result = await tryCandidate(candidate);
      if (result && result.length) {
        return { categories: result, reason: "" };
      }
    }
  } catch {
    if (pagesLoaded === 0) {
      return { categories: [], reason: "rawg-search-error" };
    }
  }

  if (pagesLoaded === 0) {
    return { categories: [], reason: networkErrors > 0 ? "rawg-network-error" : "rawg-no-page-match" };
  }
  if (pagesWithGenresButRejected > 0) {
    return { categories: [], reason: "rawg-genres-rejected" };
  }
  return { categories: [], reason: "rawg-page-no-genres" };
}

async function fetchCategoriesForRecord(record) {
  const title = String((record && record.name) || "").trim();
  if (!title) {
    return { categories: [], reason: "no-title", attempts: [] };
  }

  const launcher = String((record && record.launcher) || "").trim().toLowerCase();
  const attempts = [];

  if (launcher === "steam") {
    const steamResult = await fetchSteamGenresForAppId(record && record.app_id);
    attempts.push({ source: "steam-appid", ok: steamResult.categories.length > 0, reason: steamResult.reason });
    if (steamResult.categories.length) {
      return { categories: steamResult.categories, reason: "", attempts };
    }
  }

  const rawg = await fetchRawgCategoriesForTitle(title);
  attempts.push({ source: "rawg", ok: rawg.categories.length > 0, reason: rawg.reason });
  if (rawg.categories.length) {
    return { categories: rawg.categories, reason: "", attempts };
  }

  const steamFallback = await fetchSteamGenresByTitle(title);
  attempts.push({ source: "steam-search", ok: steamFallback.categories.length > 0, reason: steamFallback.reason });
  if (steamFallback.categories.length) {
    return { categories: steamFallback.categories, reason: "", attempts };
  }

  // Pick the most informative reason from the attempts (prefer non-empty, non-no-appid).
  const informative = attempts.find((a) => a.reason && a.reason !== "no-appid") || attempts[attempts.length - 1];
  return {
    categories: [],
    reason: (informative && informative.reason) || "no-source-matched",
    attempts,
  };
}

function applyCategories(records, cacheEntries) {
  return records.map((record) => {
    const key = slugifyTitle(record.name);
    const categories = Array.isArray(cacheEntries[key] && cacheEntries[key].categories)
      ? cacheEntries[key].categories.slice(0, MAX_CATEGORIES_PER_GAME)
      : [];
    const next = { ...record };
    next.extra = { ...(record.extra || {}) };
    if (categories.length) {
      next.extra.categories = categories;
      const existingBlob = String(next.search_blob || "");
      next.search_blob = `${existingBlob} ${categories.join(" ")}`.trim().toLowerCase();
    }
    return next;
  });
}

async function runFetchCycle(records, cacheRoot, options) {
  const force = Boolean(options && options.force);
  const budget = force ? MAX_FETCH_PER_FORCE : MAX_FETCH_PER_LOAD;
  const { filePath, entries } = readCache(cacheRoot);
  const { filePath: stateFilePath, lastFetchAt } = readState(cacheRoot);
  const missing = [];

  for (const record of records) {
    const key = slugifyTitle(record.name);
    if (!key) {
      continue;
    }
    const existing = entries[key];

    const needsRetry = Boolean(existing) && shouldRefreshEmptyCategoryEntry(existing);
    if (existing && !needsRetry && !force) {
      continue;
    }

    const reason = force && existing ? "force" : needsRetry ? "retry-empty" : "new";
    missing.push({ key, record, reason });
    if (missing.length >= budget) {
      break;
    }
  }

  if (missing.length === 0) {
    return {
      updated: false,
      data: applyCategories(records, entries),
      processed: 0,
      resolved: 0,
      summary: buildSummary(records, entries),
    };
  }

  const forceLike = missing.some((item) => item.reason === "retry-empty" || item.reason === "force");
  if (!forceLike && !shouldFetchNow(lastFetchAt)) {
    return {
      updated: false,
      data: applyCategories(records, entries),
      processed: 0,
      resolved: 0,
      summary: buildSummary(records, entries),
    };
  }

  let index = 0;
  let resolved = 0;
  const workers = Array.from({ length: Math.min(FETCH_CONCURRENCY, missing.length) }, async () => {
    while (index < missing.length) {
      const item = missing[index];
      index += 1;
      try {
        const fetchResult = await fetchCategoriesForRecord(item.record);
        const categories = sanitizeCategories(fetchResult.categories);
        if (categories.length) {
          resolved += 1;
        }
        entries[item.key] = {
          title: item.record.name,
          categories,
          fetchedAt: new Date().toISOString(),
          source: categories.length ? "auto" : "auto-empty",
          reason: categories.length ? "" : (fetchResult.reason || "no-source-matched"),
        };
      } catch (err) {
        entries[item.key] = {
          title: item.record.name,
          categories: [],
          fetchedAt: new Date().toISOString(),
          source: "auto-error",
          reason: `exception:${(err && err.message) || "unknown"}`,
        };
      }
    }
  });

  if (workers.length) {
    await Promise.all(workers);
  }

  writeCache(filePath, entries);
  writeState(stateFilePath, new Date().toISOString());
  return {
    updated: true,
    data: applyCategories(records, entries),
    processed: missing.length,
    resolved,
    summary: buildSummary(records, entries),
  };
}

function buildSummary(records, entries) {
  const total = records.length;
  let withCategories = 0;
  let withoutCategories = 0;
  let notInCache = 0;
  const reasonCounts = new Map();
  const reasonSamples = new Map();
  const missingTitles = [];

  for (const record of records) {
    const key = slugifyTitle(record.name);
    const entry = key ? entries[key] : null;
    const cats = entry && Array.isArray(entry.categories) ? entry.categories : [];
    if (cats.length) {
      withCategories += 1;
      continue;
    }
    withoutCategories += 1;
    let reason;
    if (!entry) {
      reason = "not-yet-fetched";
      notInCache += 1;
    } else {
      reason = entry.reason || "unknown";
    }
    reasonCounts.set(reason, (reasonCounts.get(reason) || 0) + 1);
    const samples = reasonSamples.get(reason) || [];
    if (samples.length < 5) {
      samples.push(record.name);
      reasonSamples.set(reason, samples);
    }
    if (missingTitles.length < 25) {
      missingTitles.push(record.name);
    }
  }

  const reasons = [...reasonCounts.entries()]
    .map(([reason, count]) => ({ reason, count, samples: reasonSamples.get(reason) || [] }))
    .sort((a, b) => b.count - a.count);

  return {
    total,
    withCategories,
    withoutCategories,
    notInCache,
    reasons,
    missingTitles,
  };
}

async function enrichCategories(records, cacheRoot) {
  const result = await runFetchCycle(records, cacheRoot, { force: false });
  return result.data;
}

function loadCategoriesFromCache(records, cacheRoot) {
  const { entries } = readCache(cacheRoot);
  return applyCategories(records, entries);
}

async function refreshCategoryCache(records, cacheRoot, options) {
  return runFetchCycle(records, cacheRoot, options || { force: false });
}

module.exports = {
  enrichCategories,
  loadCategoriesFromCache,
  normalizeCategoryName,
  refreshCategoryCache,
};
