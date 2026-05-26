from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GameRecord:
    name: str
    launcher: str
    launch_url: str
    app_id: str | None = None
    install_path: str | None = None
    icon_path: str | None = None
    icon_kind: str = "generated"
    search_terms: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def search_blob(self) -> str:
        parts = [self.name, self.launcher, self.launch_url]
        if self.app_id:
            parts.append(self.app_id)
        if self.install_path:
            parts.append(self.install_path)
        parts.extend(self.search_terms)
        parts.extend(str(value) for value in self.extra.values() if value is not None)
        return " ".join(parts).lower()


@dataclass(frozen=True)
class ScanConfig:
    steam_root: Path | None = None
    epic_manifest_dir: Path | None = None
    ea_manifest_dir: Path | None = None
    gog_root: Path | None = None
    output_dir: Path | None = None
    include_external_adapters: bool = True
    # Optional Steam Web API credentials for fetching the full owned library
    # (covers games the user owns but has not installed locally).
    steam_api_key: str | None = None
    steam_id: str | None = None
    # Override the auto-detected Epic Games Launcher Chromium webcache dirs.
    # `None` -> auto-detect under %LOCALAPPDATA%\EpicGamesLauncher\Saved\webcache*.
    # Empty tuple -> skip webcache scraping (no owned-but-not-installed games).
    epic_webcache_dirs: tuple[Path, ...] | None = None
    # Override the auto-detected Xbox app cache database (AsyncCache.db).
    # `None` -> auto-detect under %LOCALAPPDATA%\Packages\Microsoft.GamingApp_*\LocalState\.
    # Path('') / non-existent path -> skip Xbox discovery.
    xbox_cache_db: Path | None = None


@dataclass(frozen=True)
class RenderedGame:
    name: str
    launcher: str
    launch_url: str
    icon_url: str
    search_blob: str
    app_id: str | None = None
    install_path: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
