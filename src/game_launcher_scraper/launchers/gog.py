from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..models import GameRecord, ScanConfig
from ..utils import (
    best_image_match,
    first_existing_image,
    first_existing_path,
    iter_image_files,
    normalize_search_terms,
    safe_string,
    tokenize,
)
from .base import LauncherAdapter


class GOGAdapter(LauncherAdapter):
    launcher_id = "gog"
    display_name = "GOG Galaxy"

    def discover(self, config: ScanConfig) -> list[GameRecord]:
        root = config.gog_root or self._detect_root()
        if root is None or not root.exists():
            return []

        db_path = self._find_galaxy_database(root)
        if db_path is None:
            return []

        icon_images = self._index_icon_images(root)
        records = self._discover_from_galaxy_db(db_path, icon_images)

        deduped: dict[str, GameRecord] = {}
        for record in records:
            key = record.launch_url.lower()
            deduped.setdefault(key, record)
        return list(deduped.values())

    def _detect_root(self) -> Path | None:
        candidates = [
            Path("C:/ProgramData/GOG.com/Galaxy/storage"),
            Path.home() / "AppData" / "Local" / "GOG.com" / "Galaxy" / "storage",
            Path.home() / "AppData" / "Local" / "GOG Galaxy" / "storage",
        ]
        return first_existing_path(candidates)

    def _find_galaxy_database(self, root: Path) -> Path | None:
        candidates = [
            root / "galaxy-2.0.db",
            root / "galaxy.db",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        for candidate in root.rglob("galaxy-2.0.db"):
            return candidate
        for candidate in root.rglob("galaxy.db"):
            return candidate
        return None

    def _discover_from_galaxy_db(self, db_path: Path, icon_images: list[Path]) -> list[GameRecord]:
        try:
            connection = sqlite3.connect(str(db_path))
        except sqlite3.Error:
            return []

        try:
            tables = {
                row[0].lower(): row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            # Galaxy 2.0 records the user's owned/library games in LibraryReleases.
            # Older or stripped-down builds may instead expose OwnedGames.
            owned_table_key = None
            for candidate in ("libraryreleases", "ownedgames"):
                if candidate in tables:
                    owned_table_key = candidate
                    break
            if owned_table_key is None:
                return []

            owned_keys = self._fetch_owned_release_keys(connection, tables[owned_table_key])
            if not owned_keys:
                return []

            # Drop entries flagged as DLC/non-game (discount codes, Galaxy overlays, etc.).
            non_game_keys = self._fetch_non_game_release_keys(connection, tables)
            owned_keys -= non_game_keys
            if not owned_keys:
                return []

            titles = self._fetch_titles(connection, tables)
            installed = self._fetch_installed(connection, tables)

            records: list[GameRecord] = []
            for release_key in sorted(owned_keys):
                title = titles.get(release_key)
                if not title:
                    continue

                install_path = installed.get(release_key)
                launch_url = f"goggalaxy://openGameView/{release_key}"
                icon_path = self._discover_icon_path(
                    release_key=release_key,
                    install_path=install_path,
                    icon_images=icon_images,
                    display_name=title,
                )
                records.append(
                    GameRecord(
                        name=title,
                        launcher=self.display_name,
                        launch_url=launch_url,
                        app_id=release_key,
                        install_path=install_path,
                        icon_path=str(icon_path) if icon_path else None,
                        icon_kind="local" if icon_path else "generated",
                        search_terms=normalize_search_terms(release_key, install_path),
                        extra={"installed": bool(install_path)},
                    )
                )
            return records
        finally:
            connection.close()

    def _fetch_owned_release_keys(self, connection: sqlite3.Connection, table_name: str) -> set:
        columns = {row[1].lower(): row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}
        key_column = columns.get("releasekey")
        if not key_column:
            return set()
        try:
            rows = connection.execute(f"SELECT {key_column} FROM {table_name}").fetchall()
        except sqlite3.Error:
            return set()
        return {str(row[0]) for row in rows if row and row[0]}

    def _fetch_non_game_release_keys(self, connection: sqlite3.Connection, tables: dict) -> set:
        """Return release keys that should NOT be rendered as games.

        Galaxy 2.0 stores a row per owned release in ReleaseProperties with an
        ``isDlc`` flag. Promotional vouchers, discount codes and add-ons
        (Galaxy Overlay, demos, etc.) all flip this flag, so excluding them
        gives a clean list of actual base games.
        """
        if "releaseproperties" not in tables:
            return set()
        table = tables["releaseproperties"]
        columns = {row[1].lower(): row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        key_column = columns.get("releasekey")
        dlc_column = columns.get("isdlc")
        if not key_column or not dlc_column:
            return set()
        try:
            rows = connection.execute(
                f"SELECT {key_column}, {dlc_column} FROM {table} WHERE {dlc_column} = 1"
            ).fetchall()
        except sqlite3.Error:
            return set()
        return {str(row[0]) for row in rows if row and row[0]}

    def _fetch_titles(self, connection: sqlite3.Connection, tables: dict) -> dict:
        if "gamepieces" not in tables or "gamepiecetypes" not in tables:
            return {}
        gp = tables["gamepieces"]
        gpt = tables["gamepiecetypes"]
        try:
            rows = connection.execute(
                f"SELECT gp.releaseKey, gpt.type, gp.value FROM {gp} gp "
                f"JOIN {gpt} gpt ON gpt.id = gp.gamePieceTypeId "
                "WHERE gpt.type IN ('title', 'originalTitle')"
            ).fetchall()
        except sqlite3.Error:
            return {}

        titles: dict = {}
        originals: dict = {}
        for release_key, type_name, raw_value in rows:
            if not release_key or not raw_value:
                continue
            value = self._extract_title_value(raw_value)
            if not value:
                continue
            if type_name == "title":
                titles[str(release_key)] = value
            elif type_name == "originalTitle":
                originals[str(release_key)] = value

        for release_key, value in originals.items():
            titles.setdefault(release_key, value)
        return titles

    def _extract_title_value(self, raw_value: object) -> str | None:
        text = safe_string(raw_value)
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return text
        if isinstance(parsed, dict):
            for key in ("title", "name", "originalTitle"):
                candidate = parsed.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            return None
        if isinstance(parsed, str) and parsed.strip():
            return parsed.strip()
        return None

    def _fetch_installed(self, connection: sqlite3.Connection, tables: dict) -> dict:
        installed: dict = {}
        for candidate_table in (
            "installedbaseproducts",
            "installedproducts",
            "installedexternalproducts",
        ):
            if candidate_table not in tables:
                continue
            table_name = tables[candidate_table]
            columns = {row[1].lower(): row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}
            path_column = (
                columns.get("installationpath")
                or columns.get("installpath")
                or columns.get("path")
            )
            key_column = (
                columns.get("productid")
                or columns.get("releasekey")
                or columns.get("gamereleasekey")
            )
            if not path_column or not key_column:
                continue
            try:
                rows = connection.execute(
                    f"SELECT {key_column}, {path_column} FROM {table_name}"
                ).fetchall()
            except sqlite3.Error:
                continue
            for raw_key, raw_path in rows:
                key_text = safe_string(raw_key)
                path_text = safe_string(raw_path)
                if not key_text or not path_text:
                    continue
                if candidate_table in ("installedbaseproducts", "installedproducts"):
                    installed.setdefault(f"gog_{key_text}", path_text)
                else:
                    installed.setdefault(key_text, path_text)
        return installed

    def _index_icon_images(self, root: Path) -> list[Path]:
        roots = [
            root,
            root / "icons",
            root / "images",
            root.parent / "icons",
            root.parent / "imagecache",
            root.parent / "webcache",
        ]
        images: list[Path] = []
        seen: set = set()
        for candidate_root in roots:
            for image in iter_image_files(candidate_root, max_files=20000):
                key = str(image).lower()
                if key in seen:
                    continue
                seen.add(key)
                images.append(image)
        return images

    def _discover_icon_path(
        self,
        release_key: str,
        install_path: str | None,
        icon_images: list[Path],
        display_name: str,
    ) -> Path | None:
        if install_path:
            install_dir = Path(install_path)
            local_candidates: list[Path] = []
            for filename in [
                "goggame.ico",
                "icon.png",
                "icon.jpg",
                "icon.jpeg",
                "cover.png",
                "cover.jpg",
                "boxart.png",
                "boxart.jpg",
            ]:
                local_candidates.append(install_dir / filename)
            resolved = first_existing_image(local_candidates)
            if resolved:
                return resolved

        desired_tokens = tokenize(release_key)
        if "_" in release_key:
            desired_tokens |= tokenize(release_key.split("_", 1)[1])
        preferred_tokens = tokenize(display_name)
        return best_image_match(icon_images, desired_tokens=desired_tokens, preferred_tokens=preferred_tokens)
