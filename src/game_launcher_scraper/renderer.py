from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict
from pathlib import Path
from html import unescape
from contextlib import suppress
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

from .models import GameRecord, RenderedGame
from .utils import ensure_directory, unique_name


LAUNCHER_COLORS = {
    "Steam": "#1b2838",
    "Epic Games": "#2a2a2a",
    "EA app": "#ff4747",
    "GOG Galaxy": "#5d2d91",
}

# Deterministic fallbacks for titles whose launcher metadata lacks usable art
# and where generic discovery can fail due source-side restrictions.
KNOWN_COVER_OVERRIDES = {
  "crysis": [
    "https://cdn.cloudflare.steamstatic.com/steam/apps/17300/library_600x900.jpg",
    "https://cdn.cloudflare.steamstatic.com/steam/apps/17300/header.jpg",
  ],
}


def render_site(records: list[GameRecord], output_dir: Path) -> Path:
    output_dir = ensure_directory(output_dir)
    assets_dir = ensure_directory(output_dir / "assets")
  # Non-destructive by design: never wipe the assets directory on refresh.
  # This preserves already-downloaded/cached icons across repeated reloads.
    icons_dir = ensure_directory(assets_dir / "icons")
    remote_icons_dir = ensure_directory(icons_dir / "remote")

    rendered = [_materialize_record(record, icons_dir, remote_icons_dir) for record in records]

    _write_text(assets_dir / "styles.css", _styles_css())
    _write_text(assets_dir / "app.js", _app_js())
    _write_text(assets_dir / "games-data.js", _games_data_js(rendered))
    _write_text(output_dir / "index.html", _index_html())
    return output_dir / "index.html"


def _materialize_record(record: GameRecord, icons_dir: Path, remote_icons_dir: Path) -> RenderedGame:
    icon_url = _copy_or_generate_icon(record, icons_dir, remote_icons_dir)
    return RenderedGame(
        name=record.name,
        launcher=record.launcher,
        launch_url=record.launch_url,
        icon_url=icon_url,
        search_blob=record.search_blob(),
        app_id=record.app_id,
        install_path=record.install_path,
        extra=record.extra,
    )


def _copy_or_generate_icon(record: GameRecord, icons_dir: Path, remote_icons_dir: Path) -> str:
    if record.icon_path:
        source = Path(record.icon_path)
        if source.exists():
            destination = icons_dir / f"{unique_name(record.name, str(source))}{source.suffix.lower()}"
            if not destination.exists():
                shutil.copy2(source, destination)
            return f"assets/icons/{destination.name}"

    remote_icon = _download_remote_icon(record, remote_icons_dir)
    if remote_icon is not None:
        return remote_icon

    icon_name = f"{unique_name(record.name, record.launch_url)}.svg"
    destination = icons_dir / icon_name
    if not destination.exists():
        destination.write_text(_generated_icon_svg(record), encoding="utf-8")
    return f"assets/icons/{icon_name}"


def _download_remote_icon(record: GameRecord, remote_icons_dir: Path) -> str | None:
    # First: honor explicit CDN URL(s) provided by an adapter (e.g. Steam's
    # library art). This is far more accurate and faster than the generic
    # Wikipedia search fallback.
    direct_value = record.extra.get("remote_icon_url") if record.extra else None
    direct_urls: list[str] = []

    override_urls = KNOWN_COVER_OVERRIDES.get(record.name.strip().lower())
    if override_urls:
      direct_urls.extend([u for u in override_urls if isinstance(u, str) and u])

    if isinstance(direct_value, str) and direct_value:
      direct_urls.append(direct_value)
    elif isinstance(direct_value, (list, tuple)):
      direct_urls.extend([u for u in direct_value if isinstance(u, str) and u])
    for direct_url in direct_urls:
        with suppress(Exception):
            image_bytes, extension = _fetch_direct_image(direct_url)
            if image_bytes:
                destination = remote_icons_dir / f"{unique_name(record.name, direct_url)}{extension}"
                if not destination.exists():
                    destination.write_bytes(image_bytes)
                return f"assets/icons/remote/{destination.name}"

    queries = [record.name]
    if record.app_id:
        queries.append(f"{record.name} video game")

    for query in queries:
        with suppress(Exception):
            image_bytes, extension = _fetch_wikipedia_game_image(query)
            if image_bytes is None:
                continue
            destination = remote_icons_dir / f"{unique_name(record.name, query)}{extension}"
            if not destination.exists():
                destination.write_bytes(image_bytes)
            return f"assets/icons/remote/{destination.name}"

    # Last-resort fallback: do a broad web search and extract the best
    # candidate image from result pages (og:image / twitter:image). This is
    # intentionally slower and only runs when direct + Wikipedia both fail.
    for query in queries:
      with suppress(Exception):
        image_bytes, extension = _fetch_web_cover_image(query)
        if image_bytes is None:
          continue
        destination = remote_icons_dir / f"{unique_name(record.name, f'web:{query}')}{extension}"
        if not destination.exists():
          destination.write_bytes(image_bytes)
        return f"assets/icons/remote/{destination.name}"

    return None


def _fetch_direct_image(image_url: str) -> tuple[bytes | None, str]:
    request = Request(image_url, headers={"User-Agent": "GameLauncherScraper/1.0"})
    with urlopen(request, timeout=10) as response:
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        image_bytes = response.read()
    if not image_bytes:
        return None, ".jpg"
    return image_bytes, _content_type_extension(content_type, image_url)


def _fetch_wikipedia_game_image(query: str) -> tuple[bytes | None, str]:
    api_url = (
        "https://en.wikipedia.org/w/api.php"
        "?action=query&generator=search"
        f"&gsrsearch={quote_plus(query)}"
        "&gsrnamespace=0"
        "&gsrlimit=5"
        "&prop=pageimages|info"
        "&piprop=thumbnail|original"
        "&pithumbsize=512"
        "&inprop=url"
        "&format=json"
    )
    request = Request(api_url, headers={"User-Agent": "GameLauncherScraper/1.0"})
    with urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8", errors="ignore"))

    pages = payload.get("query", {}).get("pages", {})
    for page in pages.values():
        image_url = page.get("thumbnail", {}).get("source") or page.get("original", {}).get("source")
        if not image_url:
            continue

        request = Request(image_url, headers={"User-Agent": "GameLauncherScraper/1.0"})
        with urlopen(request, timeout=10) as response:
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            image_bytes = response.read()

        if image_bytes:
            return image_bytes, _content_type_extension(content_type, image_url)

    return None, ".jpg"


def _fetch_web_cover_image(query: str) -> tuple[bytes | None, str]:
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query + ' game cover art')}"
    request = Request(search_url, headers={"User-Agent": "GameLauncherScraper/1.0"})
    with urlopen(request, timeout=8) as response:
        html = response.read().decode("utf-8", errors="ignore")

    for link in _extract_duckduckgo_result_links(html)[:10]:
        if _is_probably_image_url(link):
            with suppress(Exception):
                return _fetch_direct_image(link)

        image_url = _extract_page_meta_image(link)
        if not image_url:
            continue
        with suppress(Exception):
            image_bytes, extension = _fetch_direct_image(image_url)
            if image_bytes:
                return image_bytes, extension

    return None, ".jpg"


def _extract_duckduckgo_result_links(html: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()

    for match in re.finditer(r'href=["\']([^"\']+)["\']', html):
        href = unescape(match.group(1)).strip()
        if not href:
            continue

        parsed = urlparse(href)
        resolved = href

        if href.startswith("/l/?") or href.startswith("//duckduckgo.com/l/?"):
            if href.startswith("//"):
                parsed = urlparse("https:" + href)
            else:
                parsed = urlparse("https://duckduckgo.com" + href)
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            resolved = unquote(target).strip()

        if not resolved.lower().startswith(("http://", "https://")):
            continue

        host = (urlparse(resolved).hostname or "").lower()
        if "duckduckgo.com" in host:
            continue

        if resolved in seen:
            continue
        seen.add(resolved)
        links.append(resolved)

    return links


def _extract_page_meta_image(page_url: str) -> str | None:
    request = Request(page_url, headers={"User-Agent": "GameLauncherScraper/1.0"})
    with urlopen(request, timeout=7) as response:
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if "html" not in content_type and content_type:
            return None
        html = response.read().decode("utf-8", errors="ignore")

    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
        r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
    ]

    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = unescape(match.group(1)).strip()
        if not candidate:
            continue
        if candidate.startswith("//"):
            return "https:" + candidate
        if candidate.startswith("/"):
            return urljoin(page_url, candidate)
        if candidate.lower().startswith(("http://", "https://")):
            return candidate

    return None


def _is_probably_image_url(value: str) -> bool:
    suffix = Path(urlparse(value).path).suffix.lower()
    return suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _content_type_extension(content_type: str, image_url: str) -> str:
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
    }
    if content_type in mapping:
        return mapping[content_type]

    suffix = Path(image_url).suffix.lower()
    if suffix == ".jpeg":
        return ".jpg"
    if suffix in {".png", ".jpg", ".webp", ".gif", ".bmp"}:
        return suffix
    return ".jpg"


def _generated_icon_svg(record: GameRecord) -> str:
    color = LAUNCHER_COLORS.get(record.launcher, "#4169e1")
    initials = _initials(record.name)
    label = record.name.replace("&", "and")[:40]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="{label}">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{color}"/>'
        '<stop offset="100%" stop-color="#111827"/>'
        '</linearGradient></defs>'
        '<rect width="256" height="256" rx="48" fill="url(#g)"/>'
        '<circle cx="208" cy="48" r="28" fill="rgba(255,255,255,.12)"/>'
        '<text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" '
        'font-family="Segoe UI, Arial, sans-serif" font-size="88" font-weight="700" fill="#ffffff">'
        f"{initials}</text>"
        '<text x="50%" y="83%" dominant-baseline="middle" text-anchor="middle" '
        'font-family="Segoe UI, Arial, sans-serif" font-size="18" fill="rgba(255,255,255,.8)">'
        f"{label}</text>"
        '</svg>'
    )


def _initials(value: str) -> str:
    parts = [part for part in value.replace("-", " ").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        word = parts[0]
        return (word[:2] or "?").upper()
    return (parts[0][0] + parts[1][0]).upper()


def _games_data_js(records: list[RenderedGame]) -> str:
    payload = [asdict(record) for record in records]
    return "window.__GAME_LIBRARY__ = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n"


def _index_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="dark" />
  <title>Game Library</title>
  <link rel="stylesheet" href="assets/styles.css" />
</head>
<body>
  <main class="shell">
    <header class="hero">
      <div>
        <p class="eyebrow">Local launcher inventory</p>
        <h1>Searchable Game Library</h1>
        <p class="lede">Steam, Epic, EA app, GOG, and any future launcher adapters that follow the same catalog contract.</p>
      </div>
      <div class="stats">
        <div><span id="game-count">0</span><label>Games</label></div>
        <div><span id="launcher-count">0</span><label>Launchers</label></div>
      </div>
    </header>

    <section class="controls">
      <input id="search" type="search" placeholder="Search games, launchers, IDs, paths..." autocomplete="off" />
      <select id="launcher-filter">
        <option value="all">All launchers</option>
      </select>
    </section>

    <section id="empty-state" class="empty-state hidden">
      <h2>No games found</h2>
      <p>The selected launcher locations did not return any records. Check launcher paths or add another adapter.</p>
    </section>

    <section id="grid" class="grid" aria-live="polite"></section>
  </main>

  <script src="assets/games-data.js"></script>
  <script src="assets/app.js"></script>
</body>
</html>
"""


def _app_js() -> str:
    return """(() => {
  const data = Array.isArray(window.__GAME_LIBRARY__) ? window.__GAME_LIBRARY__ : [];
  const searchInput = document.getElementById('search');
  const launcherFilter = document.getElementById('launcher-filter');
  const grid = document.getElementById('grid');
  const emptyState = document.getElementById('empty-state');
  const gameCount = document.getElementById('game-count');
  const launcherCount = document.getElementById('launcher-count');

  const launchers = [...new Set(data.map((item) => item.launcher))].sort((a, b) => a.localeCompare(b));
  for (const launcher of launchers) {
    const option = document.createElement('option');
    option.value = launcher;
    option.textContent = launcher;
    launcherFilter.appendChild(option);
  }

  launcherCount.textContent = String(launchers.length);

  function normalize(text) {
    return String(text || '').toLowerCase();
  }

  function matches(item, query, launcher) {
    const queryMatch = !query || item.search_blob.includes(query);
    const launcherMatch = launcher === 'all' || item.launcher === launcher;
    return queryMatch && launcherMatch;
  }

  function render() {
    const query = normalize(searchInput.value).trim();
    const launcher = launcherFilter.value;
    const filtered = data.filter((item) => matches(item, query, launcher));

    gameCount.textContent = String(filtered.length);
    grid.innerHTML = '';

    emptyState.classList.toggle('hidden', filtered.length !== 0);

    for (const item of filtered) {
      const card = document.createElement('article');
      card.className = 'card';
      card.innerHTML = `
        <a class="card-link" href="${item.launch_url}" aria-label="Launch ${escapeHtml(item.name)}">
          <img class="icon" src="${item.icon_url}" alt="${escapeHtml(item.name)} icon" loading="lazy" referrerpolicy="no-referrer" />
          <div class="card-body">
            <h2>${escapeHtml(item.name)}</h2>
            <p>${escapeHtml(item.launcher)}</p>
          </div>
          <span class="launch-pill">Launch</span>
        </a>
      `;
      grid.appendChild(card);
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  searchInput.addEventListener('input', render);
  launcherFilter.addEventListener('change', render);
  render();
})();
"""


def _styles_css() -> str:
    return """* {
  box-sizing: border-box;
}

:root {
  color-scheme: dark;
  --bg: #07111f;
  --bg-elevated: rgba(16, 24, 40, 0.78);
  --panel: rgba(14, 20, 34, 0.92);
  --text: #eef4ff;
  --muted: #96a6bf;
  --border: rgba(148, 163, 184, 0.18);
  --accent: #68d7ff;
  --accent-strong: #3cb5f5;
  --shadow: 0 24px 80px rgba(0, 0, 0, 0.36);
}

html, body {
  min-height: 100%;
}

body {
  margin: 0;
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  color: var(--text);
  background:
    radial-gradient(circle at top left, rgba(104, 215, 255, 0.18), transparent 32%),
    radial-gradient(circle at top right, rgba(140, 93, 255, 0.16), transparent 28%),
    linear-gradient(180deg, #050b16 0%, #07111f 45%, #081426 100%);
}

.shell {
  width: calc(100vw - 32px);
  max-width: none;
  margin: 0 auto;
  padding: 32px 0 48px;
}

.hero {
  display: flex;
  gap: 24px;
  align-items: end;
  justify-content: space-between;
  padding: 28px;
  border: 1px solid var(--border);
  border-radius: 28px;
  background: linear-gradient(180deg, rgba(11, 18, 34, 0.9), rgba(10, 15, 28, 0.7));
  box-shadow: var(--shadow);
}

.eyebrow {
  margin: 0 0 10px;
  color: var(--accent);
  font-size: 0.82rem;
  text-transform: uppercase;
  letter-spacing: 0.18em;
}

h1 {
  margin: 0;
  font-size: clamp(2.3rem, 5vw, 4.8rem);
  line-height: 0.98;
}

.lede {
  max-width: 64ch;
  margin: 14px 0 0;
  color: var(--muted);
  font-size: 1.02rem;
}

.stats {
  display: flex;
  gap: 18px;
}

.stats div {
  min-width: 120px;
  padding: 16px 18px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.03);
}

.stats span {
  display: block;
  font-size: 2rem;
  font-weight: 700;
}

.stats label {
  color: var(--muted);
  font-size: 0.82rem;
}

.controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 240px;
  gap: 16px;
  margin: 20px 0;
}

input, select {
  width: 100%;
  padding: 16px 18px;
  border: 1px solid var(--border);
  border-radius: 18px;
  color: var(--text);
  background: var(--panel);
  box-shadow: var(--shadow);
  outline: none;
}

input::placeholder {
  color: #6f7f97;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 18px;
}

.card {
  border: 1px solid var(--border);
  border-radius: 24px;
  background: rgba(11, 18, 34, 0.92);
  overflow: hidden;
  box-shadow: var(--shadow);
}

.card-link {
  display: grid;
  gap: 14px;
  min-height: 100%;
  padding: 16px;
  color: inherit;
  text-decoration: none;
}

.icon {
  width: 100%;
  aspect-ratio: 2 / 3;
  border-radius: 14px;
  object-fit: cover;
  background: rgba(255, 255, 255, 0.04);
}

.card-body h2 {
  margin: 0;
  font-size: 1.06rem;
}

.card-body p {
  margin: 8px 0 0;
  color: var(--muted);
}

.launch-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: fit-content;
  padding: 8px 12px;
  border-radius: 999px;
  color: #03111d;
  background: linear-gradient(135deg, var(--accent), var(--accent-strong));
  font-size: 0.8rem;
  font-weight: 700;
}

.empty-state {
  padding: 32px;
  border: 1px dashed var(--border);
  border-radius: 24px;
  color: var(--muted);
  background: rgba(255, 255, 255, 0.03);
}

.empty-state h2 {
  margin-top: 0;
  color: var(--text);
}

.hidden {
  display: none;
}

@media (max-width: 760px) {
  .shell {
    width: min(100vw - 20px, 1280px);
    padding: 18px 0 32px;
  }

  .hero,
  .controls {
    grid-template-columns: 1fr;
    display: grid;
  }

  .stats {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
"""


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
