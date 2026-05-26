"""Xbox / Microsoft Store launcher adapter.

Reads the user's owned-game library directly from the Xbox app's local SQLite
cache (`AsyncCache.db`), which stores all Microsoft Store entitlements plus
cached `product_summary` records (title, artwork, package family name, etc.).

No authentication or network access is required.

Cache lives at:
  %LOCALAPPDATA%\\Packages\\Microsoft.GamingApp_8wekyb3d8bbwe\\LocalState\\AsyncCache.db

The Xbox app must have been opened at least once while signed in.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from ..models import GameRecord, ScanConfig
from ..utils import normalize_search_terms, safe_string
from .base import LauncherAdapter

# Order in which we prefer artwork purposes for the card (portrait first).
_ART_PRIORITY = (
    "POSTER",            # tall portrait, ideal for our 2:3 cards
    "BRANDEDKEYART",     # portrait with logo
    "TITLEDHEROART",
    "FEATUREPROMOTIONALSQUAREART",
    "BOXART",            # square
    "HERO",
    "SCREENSHOT",
    "Logo",
)


class XboxAdapter(LauncherAdapter):
    launcher_id = "xbox"
    display_name = "Xbox"

    def discover(self, config: ScanConfig) -> list[GameRecord]:
        db_path = self._resolve_cache_db(config)
        if db_path is None or not db_path.exists():
            return []

        try:
            rows = self._read_cache(db_path)
        except sqlite3.DatabaseError:
            return []
        if not rows:
            return []

        entitlements: dict[str, dict] = rows.get("entitlement_data") or {}
        if not entitlements:
            return []
        summaries: dict[str, dict] = rows.get("product_summary") or {}

        installed_pfns = self._installed_pfns()

        records: list[GameRecord] = []
        seen: set[str] = set()
        for product_id, _ent in entitlements.items():
            summary = summaries.get(product_id)
            if not summary:
                continue
            if summary.get("productKind") != "GAME":
                continue

            title = safe_string(summary.get("title")) or safe_string(summary.get("shortTitle"))
            if not title:
                continue

            pfn = self._alternate_id(summary, "PACKAGEFAMILYNAME")
            xbox_title_id = self._alternate_id(summary, "XBOXTITLEID")

            dedupe = product_id.lower()
            if dedupe in seen:
                continue
            seen.add(dedupe)

            is_installed = bool(pfn) and pfn.lower() in installed_pfns
            artwork_urls = self._artwork_urls(summary)

            # Launch: prefer direct AppsFolder launch when installed,
            # otherwise deep-link into the Xbox app for the product.
            if is_installed and pfn:
                launch_url = f"shell:AppsFolder\\{pfn}!App"
            else:
                launch_url = f"xbox://game/?productId={product_id}"

            extra: dict[str, object] = {
                "product_id": product_id,
                "installed": is_installed,
            }
            if pfn:
                extra["package_family_name"] = pfn
            if xbox_title_id:
                extra["xbox_title_id"] = xbox_title_id
            if artwork_urls:
                extra["remote_icon_url"] = artwork_urls

            records.append(
                GameRecord(
                    name=title,
                    launcher=self.display_name,
                    launch_url=launch_url,
                    app_id=product_id,
                    install_path=None,
                    icon_path=None,
                    icon_kind="remote" if artwork_urls else "generated",
                    search_terms=normalize_search_terms(
                        product_id, pfn, xbox_title_id, summary.get("developer"), summary.get("publisher")
                    ),
                    extra=extra,
                )
            )

        return records

    # ---- helpers --------------------------------------------------------

    def _resolve_cache_db(self, config: ScanConfig) -> Path | None:
        override = getattr(config, "xbox_cache_db", None)
        if override is not None:
            return Path(override)
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return None
        return Path(local) / "Packages" / "Microsoft.GamingApp_8wekyb3d8bbwe" / "LocalState" / "AsyncCache.db"

    def _read_cache(self, db_path: Path) -> dict[str, dict]:
        """Copy the DB to a temp file (Xbox app keeps it locked) then read both scopes."""
        result: dict[str, dict] = {}
        with tempfile.TemporaryDirectory() as tmp:
            copy_path = Path(tmp) / "AsyncCache.db"
            try:
                shutil.copy2(db_path, copy_path)
            except OSError:
                return result
            try:
                con = sqlite3.connect(f"file:{copy_path}?mode=ro&immutable=1", uri=True)
            except sqlite3.OperationalError:
                con = sqlite3.connect(str(copy_path))
            try:
                cur = con.cursor()
                # entitlement_data has a single row keyed by ''/'default'
                ent_row = cur.execute(
                    "SELECT value FROM AsyncCache WHERE scope='entitlement_data' LIMIT 1"
                ).fetchone()
                if ent_row:
                    try:
                        result["entitlement_data"] = json.loads(ent_row[0])
                    except (TypeError, ValueError):
                        result["entitlement_data"] = {}

                summaries: dict[str, dict] = {}
                for key, value in cur.execute(
                    "SELECT key, value FROM AsyncCache WHERE scope='product_summary'"
                ):
                    try:
                        obj = json.loads(value)
                    except (TypeError, ValueError):
                        continue
                    data = obj.get("data") if isinstance(obj, dict) else None
                    if isinstance(data, dict):
                        summaries[key] = data
                result["product_summary"] = summaries
            finally:
                con.close()
        return result

    @staticmethod
    def _alternate_id(summary: dict, id_type: str) -> str | None:
        for entry in summary.get("alternateIds") or []:
            if entry.get("idType") == id_type:
                value = safe_string(entry.get("id"))
                if value:
                    return value
        return None

    @staticmethod
    def _artwork_urls(summary: dict) -> list[str]:
        by_purpose: dict[str, str] = {}
        for art in summary.get("artwork") or []:
            uri = safe_string(art.get("uri"))
            if not uri:
                continue
            purpose = art.get("purpose") or ""
            by_purpose.setdefault(purpose, uri)
        urls: list[str] = []
        for purpose in _ART_PRIORITY:
            if purpose in by_purpose:
                urls.append(by_purpose[purpose])
        # Append anything else as last-resort fallbacks.
        for purpose, uri in by_purpose.items():
            if purpose not in _ART_PRIORITY:
                urls.append(uri)
        return urls

    def _installed_pfns(self) -> set[str]:
        """Best-effort: enumerate currently-installed UWP/MSIX package family names."""
        pfns: set[str] = set()
        local = os.environ.get("LOCALAPPDATA")
        if local:
            packages = Path(local) / "Packages"
            if packages.exists():
                try:
                    for entry in packages.iterdir():
                        if entry.is_dir():
                            pfns.add(entry.name.lower())
                except OSError:
                    pass
        return pfns
