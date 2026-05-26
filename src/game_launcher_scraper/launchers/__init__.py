from __future__ import annotations

try:
    from importlib import metadata as importlib_metadata
except ImportError:
    importlib_metadata = None

from .base import LauncherAdapter
from .ea import EAAdapter
from .epic import EpicAdapter
from .gog import GOGAdapter
from .steam import SteamAdapter
from .xbox import XboxAdapter

BUILTIN_ADAPTERS: list[type[LauncherAdapter]] = [
    SteamAdapter,
    EpicAdapter,
    EAAdapter,
    GOGAdapter,
    XboxAdapter,
]


def load_external_adapters() -> list[type[LauncherAdapter]]:
    adapters: list[type[LauncherAdapter]] = []
    if importlib_metadata is None:
        return adapters
    try:
        entry_points = importlib_metadata.entry_points()
        group = entry_points.select(group="game_launcher_scraper.launchers")
    except Exception:
        return adapters

    for entry_point in group:
        try:
            loaded = entry_point.load()
        except Exception:
            continue
        if isinstance(loaded, type) and issubclass(loaded, LauncherAdapter):
            adapters.append(loaded)
    return adapters
