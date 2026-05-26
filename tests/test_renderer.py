from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from game_launcher_scraper.models import GameRecord
from game_launcher_scraper.renderer import render_site


class RendererTests(unittest.TestCase):
    def test_render_site_writes_html_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "site"
            index_path = render_site(
                [
                    GameRecord(
                        name="Test Game",
                        launcher="Steam",
                        launch_url="steam://rungameid/123",
                        app_id="123",
                    )
                ],
                output_dir,
            )

            self.assertTrue(index_path.exists())
            self.assertTrue((output_dir / "assets" / "app.js").exists())
            self.assertTrue((output_dir / "assets" / "games-data.js").exists())
            html = index_path.read_text(encoding="utf-8")
            self.assertIn("Searchable Game Library", html)

    def test_render_site_downloads_remote_icon_when_local_icon_is_missing(self) -> None:
        wikipedia_payload = json.dumps(
            {
                "query": {
                    "pages": {
                        "123": {
                            "thumbnail": {
                                "source": "https://upload.wikimedia.org/example-cover.jpg"
                            }
                        }
                    }
                }
            }
        ).encode("utf-8")
        image_bytes = b"fake-image-bytes"

        class DummyHeaders(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        class DummyResponse:
            def __init__(self, payload: bytes, content_type: str):
                self._payload = io.BytesIO(payload)
                self.headers = DummyHeaders({"Content-Type": content_type})

            def read(self) -> bytes:
                return self._payload.read()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request, timeout=0):
            target = request.full_url if hasattr(request, "full_url") else request
            if "wikipedia.org/w/api.php" in target:
                return DummyResponse(wikipedia_payload, "application/json")
            return DummyResponse(image_bytes, "image/jpeg")

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "site"
            with mock.patch("game_launcher_scraper.renderer.urlopen", side_effect=fake_urlopen):
                index_path = render_site(
                    [
                        GameRecord(
                            name="A Game With No Local Icon",
                            launcher="Steam",
                            launch_url="steam://rungameid/999",
                            app_id="999",
                        )
                    ],
                    output_dir,
                )

            self.assertTrue(index_path.exists())
            remote_icons = list((output_dir / "assets" / "icons" / "remote").iterdir())
            self.assertEqual(len(remote_icons), 1)
            self.assertEqual(remote_icons[0].read_bytes(), image_bytes)
            games_data = (output_dir / "assets" / "games-data.js").read_text(encoding="utf-8")
            self.assertIn("assets/icons/remote/", games_data)

    def test_render_site_refresh_preserves_existing_icons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "site"

            # First render creates at least one icon file.
            render_site(
                [
                    GameRecord(
                        name="Persistent Icon Game",
                        launcher="Steam",
                        launch_url="steam://rungameid/111",
                        app_id="111",
                    )
                ],
                output_dir,
            )

            icons_dir = output_dir / "assets" / "icons"
            initial_icons = {str(p.relative_to(icons_dir)) for p in icons_dir.rglob("*") if p.is_file()}
            self.assertGreater(len(initial_icons), 0)

            # Simulate reload/regeneration with zero records.
            render_site([], output_dir)

            after_icons = {str(p.relative_to(icons_dir)) for p in icons_dir.rglob("*") if p.is_file()}
            self.assertTrue(initial_icons.issubset(after_icons))


if __name__ == "__main__":
    unittest.main()
