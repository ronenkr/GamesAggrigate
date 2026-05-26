from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..models import GameRecord, ScanConfig
from ..utils import first_existing_path, normalize_search_terms, read_text, safe_string
from .base import LauncherAdapter


_KV_LINE = re.compile(r'^\s*"([^"]+)"\s*"([^"]*)"\s*$')
_LIBRARY_PATH = re.compile(r'^\s*"path"\s*"([^"]+)"\s*$', re.IGNORECASE | re.MULTILINE)
_STEAMID_BLOCK = re.compile(r'"(7656119\d+)"\s*\{([^}]*)\}', re.DOTALL)


class SteamAdapter(LauncherAdapter):
    launcher_id = "steam"
    display_name = "Steam"

    def discover(self, config: ScanConfig) -> list[GameRecord]:
        root = config.steam_root or self._detect_root()
        if root is None:
            return []

        # 1. Discover locally installed games via steamapps manifests.
        records_by_appid: dict[str, GameRecord] = {}
        for record in self._discover_installed(root):
            records_by_appid[record.app_id] = record

        # 2. Merge in the full owned library via the Steam Web API when a key is
        # available. This is the only way to surface games the user owns but
        # hasn't installed locally.
        if config.steam_api_key:
            steam_id = config.steam_id or self._detect_steam_id(root)
            if steam_id:
                for record in self._fetch_owned_via_web_api(config.steam_api_key, steam_id):
                    existing = records_by_appid.get(record.app_id)
                    if existing is None:
                        records_by_appid[record.app_id] = record
                    else:
                        # Keep installed data; just attach the CDN icon hint
                        # so the renderer can pull box art.
                        merged_extra = dict(existing.extra)
                        if "remote_icon_url" in record.extra and "remote_icon_url" not in merged_extra:
                            merged_extra["remote_icon_url"] = record.extra["remote_icon_url"]
                        records_by_appid[record.app_id] = GameRecord(
                            name=existing.name,
                            launcher=existing.launcher,
                            launch_url=existing.launch_url,
                            app_id=existing.app_id,
                            install_path=existing.install_path,
                            icon_path=existing.icon_path,
                            icon_kind=existing.icon_kind,
                            search_terms=existing.search_terms,
                            extra=merged_extra,
                        )

        return list(records_by_appid.values())

    def _discover_installed(self, root: Path) -> list[GameRecord]:
        library_roots = self._discover_library_roots(root)
        records: list[GameRecord] = []
        seen_ids: set[str] = set()

        for library_root in library_roots:
            steamapps = library_root / "steamapps"
            if not steamapps.exists():
                continue

            for manifest in steamapps.glob("appmanifest_*.acf"):
                app_state = self._parse_appmanifest(manifest)
                app_id = safe_string(app_state.get("appid"))
                if not app_id or app_id in seen_ids:
                    continue
                seen_ids.add(app_id)

                name = safe_string(app_state.get("name"), default=f"Steam App {app_id}")
                install_dir = safe_string(app_state.get("installdir"))
                install_path = steamapps / "common" / install_dir if install_dir else None
                icon_path = self._discover_icon_path(library_root, app_id)
                records.append(
                    GameRecord(
                        name=name,
                        launcher=self.display_name,
                        launch_url=f"steam://rungameid/{app_id}",
                        app_id=app_id,
                        install_path=str(install_path) if install_path else None,
                        icon_path=str(icon_path) if icon_path else None,
                        icon_kind="local" if icon_path else "generated",
                        search_terms=normalize_search_terms(app_id, install_dir),
                        extra={
                            "manifest": str(manifest),
                            "installed": True,
                            "remote_icon_url": self._steam_cdn_icon_url(app_id),
                        },
                    )
                )

        return records

    def _fetch_owned_via_web_api(self, api_key: str, steam_id: str) -> list[GameRecord]:
        params = urlencode(
            {
                "key": api_key,
                "steamid": steam_id,
                "include_appinfo": "1",
                "include_played_free_games": "1",
                "format": "json",
            }
        )
        url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/?{params}"
        try:
            request = Request(url, headers={"User-Agent": "GameLauncherScraper/1.0"})
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8", errors="ignore"))
        except Exception:
            return []

        games = payload.get("response", {}).get("games") or []
        records: list[GameRecord] = []
        for game in games:
            app_id = str(game.get("appid") or "").strip()
            if not app_id:
                continue
            name = safe_string(game.get("name"), default=f"Steam App {app_id}")
            records.append(
                GameRecord(
                    name=name,
                    launcher=self.display_name,
                    launch_url=f"steam://rungameid/{app_id}",
                    app_id=app_id,
                    install_path=None,
                    icon_path=None,
                    icon_kind="generated",
                    search_terms=normalize_search_terms(app_id),
                    extra={
                        "installed": False,
                        "remote_icon_url": self._steam_cdn_icon_url(app_id),
                    },
                )
            )
        return records

    def _steam_cdn_icon_url(self, app_id: str) -> list[str]:
        # library_600x900 is the modern portrait box art (great for the grid),
        # but it's missing for many older titles. Fall back to header.jpg,
        # which exists for essentially every Steam app, then to capsule art.
        base = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}"
        return [
            f"{base}/library_600x900.jpg",
            f"{base}/header.jpg",
            f"{base}/capsule_616x353.jpg",
            f"{base}/capsule_231x87.jpg",
        ]

    def _detect_steam_id(self, root: Path) -> str | None:
        login_users = root / "config" / "loginusers.vdf"
        text = read_text(login_users)
        if not text:
            return None
        # Prefer the user marked mostrecent=1.
        for match in _STEAMID_BLOCK.finditer(text):
            sid, body = match.group(1), match.group(2)
            if re.search(r'"mostrecent"\s*"1"', body):
                return sid
        # Fall back to the first SteamID present.
        first = _STEAMID_BLOCK.search(text)
        return first.group(1) if first else None

    def _detect_root(self) -> Path | None:
        # Prefer real Steam installs over AppData caches (which exist but contain no steamapps).
        candidates = [
            Path("C:/Program Files (x86)/Steam"),
            Path("C:/Program Files/Steam"),
            Path.home() / "AppData" / "Local" / "Steam",
            Path.home() / "AppData" / "Roaming" / "Steam",
        ]
        for candidate in candidates:
            if (candidate / "steamapps").exists():
                return candidate
        return first_existing_path(candidates)

    def _discover_library_roots(self, steam_root: Path) -> list[Path]:
        candidates = [steam_root]
        library_vdf = steam_root / "steamapps" / "libraryfolders.vdf"
        text = read_text(library_vdf)
        if text:
            for match in _LIBRARY_PATH.finditer(text):
                candidates.append(Path(match.group(1).replace("\\", "/")))
        seen: set[str] = set()
        unique: list[Path] = []
        for candidate in candidates:
            key = str(candidate).lower()
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        return unique

    def _parse_appmanifest(self, path: Path) -> dict[str, str]:
        text = read_text(path) or ""
        inside = False
        values: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == '"AppState"':
                inside = True
                continue
            if inside and stripped == "{":
                continue
            if inside and stripped == "}":
                break
            match = _KV_LINE.match(line)
            if match and inside:
                values[match.group(1)] = match.group(2)
        return values

    def _discover_icon_path(self, steam_root: Path, app_id: str) -> Path | None:
        candidates = [
            steam_root / "appcache" / "librarycache" / f"{app_id}_icon.jpg",
            steam_root / "appcache" / "librarycache" / f"{app_id}_icon.png",
            steam_root / "appcache" / "librarycache" / f"{app_id}_capsule_231x87.jpg",
        ]
        return first_existing_path(candidates)
