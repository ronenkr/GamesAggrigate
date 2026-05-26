from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from game_launcher_scraper.catalog import scan_launchers
from game_launcher_scraper.launchers.ea import EAAdapter
from game_launcher_scraper.launchers.epic import EpicAdapter
from game_launcher_scraper.launchers.gog import GOGAdapter
from game_launcher_scraper.models import ScanConfig


class LauncherTests(unittest.TestCase):
    def test_scan_handles_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = ScanConfig(
                steam_root=Path(temp_dir) / "steam",
                epic_manifest_dir=Path(temp_dir) / "epic",
                ea_manifest_dir=Path(temp_dir) / "ea",
                gog_root=Path(temp_dir) / "gog",
                include_external_adapters=False,
                epic_webcache_dirs=(),
                xbox_cache_db=Path(temp_dir) / "missing.db",
            )
            records = scan_launchers(config)
            self.assertEqual(records, [])

    def test_epic_icon_extraction_from_manifest_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifests = root / "Data" / "Manifests"
            manifests.mkdir(parents=True)
            install_dir = root / "Games" / "MyEpicGame"
            install_dir.mkdir(parents=True)
            icon_path = install_dir / "icon.png"
            icon_path.write_bytes(b"png")

            manifest_path = manifests / "test.item"
            manifest_path.write_text(
                json.dumps(
                    {
                        "DisplayName": "Epic Test",
                        "AppName": "EpicTestApp",
                        "InstallLocation": str(install_dir),
                        "Icon": str(icon_path),
                    }
                ),
                encoding="utf-8",
            )

            records = EpicAdapter().discover(ScanConfig(epic_manifest_dir=manifests, epic_webcache_dirs=()))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].icon_path, str(icon_path))
            self.assertEqual(records[0].icon_kind, "local")

    def test_ea_icon_extraction_from_manifest_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifests = root / "EA" / "Installed Games"
            manifests.mkdir(parents=True)
            icon_path = manifests / "ea-boxart.jpg"
            icon_path.write_bytes(b"jpg")

            manifest_path = manifests / "game.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "displayName": "EA Test",
                        "offerId": "OFFER-123",
                        "iconPath": str(icon_path),
                    }
                ),
                encoding="utf-8",
            )

            records = EAAdapter().discover(ScanConfig(ea_manifest_dir=manifests))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].icon_path, str(icon_path))
            self.assertEqual(records[0].icon_kind, "local")

    def test_gog_icon_extraction_from_manifest_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            storage = root / "storage"
            storage.mkdir(parents=True)

            install_dir = root / "Games" / "SampleGame"
            install_dir.mkdir(parents=True)
            icon_path = install_dir / "goggame.ico"
            icon_path.write_bytes(b"ico")

            db_path = storage / "galaxy-2.0.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "CREATE TABLE LibraryReleases (id INTEGER PRIMARY KEY, userId INTEGER, releaseKey TEXT)"
                )
                connection.execute(
                    "INSERT INTO LibraryReleases (id, userId, releaseKey) VALUES (1, 999, 'gog_12345')"
                )

                connection.execute(
                    "CREATE TABLE GamePieceTypes (id INTEGER PRIMARY KEY, type TEXT)"
                )
                connection.execute(
                    "INSERT INTO GamePieceTypes (id, type) VALUES (1, 'title')"
                )

                connection.execute(
                    "CREATE TABLE GamePieces (releaseKey TEXT, gamePieceTypeId INTEGER, value TEXT)"
                )
                connection.execute(
                    "INSERT INTO GamePieces (releaseKey, gamePieceTypeId, value) VALUES (?, ?, ?)",
                    ("gog_12345", 1, json.dumps({"title": "Sample Game"})),
                )

                connection.execute(
                    "CREATE TABLE InstalledBaseProducts (productId TEXT, installationPath TEXT)"
                )
                connection.execute(
                    "INSERT INTO InstalledBaseProducts (productId, installationPath) VALUES (?, ?)",
                    ("12345", str(install_dir)),
                )
                connection.commit()
            finally:
                connection.close()

            records = GOGAdapter().discover(ScanConfig(gog_root=storage))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].name, "Sample Game")
            self.assertEqual(records[0].launch_url, "goggalaxy://openGameView/gog_12345")
            self.assertEqual(records[0].install_path, str(install_dir))
            self.assertEqual(records[0].icon_path, str(icon_path))
            self.assertEqual(records[0].icon_kind, "local")

    def test_gog_excludes_metadata_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Path(temp_dir) / "storage"
            storage.mkdir(parents=True)

            db_path = storage / "galaxy-2.0.db"
            connection = sqlite3.connect(db_path)
            try:
                # Noise tables that previously polluted output.
                connection.execute("CREATE TABLE Platforms (id TEXT, name TEXT)")
                connection.execute("INSERT INTO Platforms (id, name) VALUES ('42', 'amiga')")
                connection.execute("CREATE TABLE Languages (id TEXT, name TEXT)")
                connection.execute("INSERT INTO Languages (id, name) VALUES ('1', 'Albanian')")
                connection.execute(
                    "CREATE TABLE LimitedDetails (gameId TEXT, title TEXT, launchUri TEXT)"
                )
                connection.execute(
                    "INSERT INTO LimitedDetails (gameId, title, launchUri) "
                    "VALUES ('182869', '1nsane', 'galaxy://launch/182869')"
                )

                # The actual user-owned game.
                connection.execute(
                    "CREATE TABLE LibraryReleases (id INTEGER PRIMARY KEY, userId INTEGER, releaseKey TEXT)"
                )
                connection.execute(
                    "INSERT INTO LibraryReleases (id, userId, releaseKey) VALUES (1, 999, 'gog_777')"
                )
                connection.execute(
                    "CREATE TABLE GamePieceTypes (id INTEGER PRIMARY KEY, type TEXT)"
                )
                connection.execute("INSERT INTO GamePieceTypes (id, type) VALUES (1, 'title')")
                connection.execute(
                    "CREATE TABLE GamePieces (releaseKey TEXT, gamePieceTypeId INTEGER, value TEXT)"
                )
                connection.execute(
                    "INSERT INTO GamePieces (releaseKey, gamePieceTypeId, value) VALUES (?, ?, ?)",
                    ("gog_777", 1, json.dumps({"title": "My Owned Game"})),
                )
                connection.commit()
            finally:
                connection.close()

            records = GOGAdapter().discover(ScanConfig(gog_root=storage))
            names = {record.name for record in records}
            self.assertEqual(names, {"My Owned Game"})
            self.assertNotIn("1nsane", names)
            self.assertNotIn("amiga", names)
            self.assertNotIn("Albanian", names)

    def test_ea_skips_uninstalled_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            # Manifest under a generic catalog folder with no install evidence.
            manifests = root / "EA" / "Catalog"
            manifests.mkdir(parents=True)

            manifest_path = manifests / "uninstalled.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "displayName": "Should Be Skipped",
                        "offerId": "OFFER-SKIP",
                    }
                ),
                encoding="utf-8",
            )

            records = EAAdapter().discover(ScanConfig(ea_manifest_dir=manifests))
            self.assertEqual(records, [])

    def test_gog_excludes_dlc_and_voucher_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Path(temp_dir) / "storage"
            storage.mkdir(parents=True)

            db_path = storage / "galaxy-2.0.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "CREATE TABLE LibraryReleases (id INTEGER PRIMARY KEY, releaseKey TEXT)"
                )
                connection.executemany(
                    "INSERT INTO LibraryReleases (id, releaseKey) VALUES (?, ?)",
                    [
                        (1, "gog_real_game"),
                        (2, "gog_discount_code"),
                        (3, "gog_overlay"),
                    ],
                )

                connection.execute(
                    "CREATE TABLE GamePieceTypes (id INTEGER PRIMARY KEY, type TEXT)"
                )
                connection.execute("INSERT INTO GamePieceTypes (id, type) VALUES (1, 'title')")
                connection.execute(
                    "CREATE TABLE GamePieces (releaseKey TEXT, gamePieceTypeId INTEGER, value TEXT)"
                )
                connection.executemany(
                    "INSERT INTO GamePieces (releaseKey, gamePieceTypeId, value) VALUES (?, ?, ?)",
                    [
                        ("gog_real_game", 1, json.dumps({"title": "Serious Sam"})),
                        ("gog_discount_code", 1, json.dumps({"title": "CDPR Gear - discount code"})),
                        ("gog_overlay", 1, json.dumps({"title": "Game Overlay 2"})),
                    ],
                )

                connection.execute(
                    "CREATE TABLE ReleaseProperties (releaseKey TEXT, isDlc INTEGER, isVisibleInLibrary INTEGER)"
                )
                connection.executemany(
                    "INSERT INTO ReleaseProperties (releaseKey, isDlc, isVisibleInLibrary) VALUES (?, ?, ?)",
                    [
                        ("gog_real_game", 0, 1),
                        ("gog_discount_code", 1, 1),
                        ("gog_overlay", 1, 1),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            records = GOGAdapter().discover(ScanConfig(gog_root=storage))
            names = {record.name for record in records}
            self.assertEqual(names, {"Serious Sam"})

    def test_ea_discovers_install_data_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_dir = Path(temp_dir) / "EA Desktop"
            install_data = manifest_dir / "InstallData"
            (install_data / "Crysis").mkdir(parents=True)
            (install_data / "The Sims 4").mkdir(parents=True)

            records = EAAdapter().discover(ScanConfig(ea_manifest_dir=manifest_dir))
            names = {record.name for record in records}
            self.assertEqual(names, {"Crysis", "The Sims 4"})
            urls = {record.launch_url for record in records}
            # Per-game unique launch URLs (catalog dedupe is by (launcher, launch_url)).
            self.assertEqual(len(urls), 2)
            for record in records:
                self.assertEqual(record.launcher, "EA app")
                self.assertTrue(record.launch_url.startswith("origin2://library/open"))
                self.assertTrue(record.extra.get("installed"))


if __name__ == "__main__":
    unittest.main()
