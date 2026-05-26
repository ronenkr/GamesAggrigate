from __future__ import annotations

import gzip
import json
import os
import zlib
from pathlib import Path

from ..models import GameRecord, ScanConfig
from ..utils import (
    best_image_match,
    first_existing_image,
    first_existing_path,
    is_image_file,
    iter_image_files,
    normalize_search_terms,
    safe_string,
    tokenize,
)
from .base import LauncherAdapter

try:  # optional dependency; only needed to decode brotli-compressed webcache entries
    import brotli  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    brotli = None


class EpicAdapter(LauncherAdapter):
    launcher_id = "epic"
    display_name = "Epic Games"

    def discover(self, config: ScanConfig) -> list[GameRecord]:
        manifest_dir = config.epic_manifest_dir or self._detect_manifest_dir()

        records_by_key: dict[str, GameRecord] = {}

        if manifest_dir is not None and manifest_dir.exists():
            for record in self._discover_installed(manifest_dir):
                records_by_key[record.app_id.lower()] = record

        for record in self._discover_from_webcache(config):
            key = record.app_id.lower()
            existing = records_by_key.get(key)
            if existing is None:
                # Also dedupe against installed entries by display name
                name_dupes = [r for r in records_by_key.values() if r.name.lower() == record.name.lower()]
                if name_dupes:
                    continue
                records_by_key[key] = record
            else:
                merged_extra = dict(existing.extra)
                if "remote_icon_url" in record.extra and "remote_icon_url" not in merged_extra:
                    merged_extra["remote_icon_url"] = record.extra["remote_icon_url"]
                records_by_key[key] = GameRecord(
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

        return list(records_by_key.values())

    def _discover_installed(self, manifest_dir: Path) -> list[GameRecord]:
        payloads = self._load_payloads(manifest_dir)
        icon_images = self._index_icon_images(manifest_dir)

        records: list[GameRecord] = []
        seen: set[str] = set()
        for manifest, payload in payloads:
            name = safe_string(payload.get("DisplayName")) or safe_string(payload.get("AppName"))
            app_name = safe_string(payload.get("AppName"))
            if not name:
                name = app_name or f"Epic Game {manifest.stem}"
            if not app_name:
                app_name = safe_string(payload.get("CatalogItemId"), default=manifest.stem)

            key = app_name.lower()
            if key in seen:
                continue
            seen.add(key)

            install_path = safe_string(payload.get("InstallLocation"))
            icon_path = self._discover_icon_path(
                payload=payload,
                manifest_path=manifest,
                app_name=app_name,
                install_path=install_path or None,
                icon_images=icon_images,
                display_name=name,
            )
            records.append(
                GameRecord(
                    name=name,
                    launcher=self.display_name,
                    launch_url=f"com.epicgames.launcher://apps/{app_name}?action=launch&silent=true",
                    app_id=app_name,
                    install_path=install_path or None,
                    icon_path=str(icon_path) if icon_path else None,
                    icon_kind="local" if icon_path else "generated",
                    search_terms=normalize_search_terms(
                        app_name,
                        payload.get("CatalogItemId"),
                        payload.get("NamespaceId"),
                        payload.get("ArtifactId"),
                        install_path,
                    ),
                    extra={"manifest": str(manifest), "installed": True},
                )
            )

        return records

    # ------------------------------------------------------------------
    # Webcache scraping: surface the full owned library from the Epic
    # Games Launcher's local Chromium webcache (GraphQL responses).
    # ------------------------------------------------------------------
    def _discover_from_webcache(self, config: ScanConfig) -> list[GameRecord]:
        if config.epic_webcache_dirs is None:
            cache_dirs = self._detect_webcache_dirs()
        else:
            cache_dirs = [Path(p) for p in config.epic_webcache_dirs if Path(p).exists()]
        if not cache_dirs:
            return []

        library_records: dict[tuple[str, str], dict] = {}
        catalog_items: dict[tuple[str, str], dict] = {}

        for cache_dir in cache_dirs:
            for f in cache_dir.glob("f_*"):
                try:
                    raw = f.read_bytes()
                except OSError:
                    continue
                if b"catalogItemId" not in raw and b"keyImages" not in raw:
                    continue
                for variant in self._try_decode_all(raw):
                    for obj in self._find_json_values(variant):
                        for r in self._collect_library_records(obj):
                            ns = r.get("namespace")
                            cid = r.get("catalogItemId")
                            if ns and cid:
                                library_records.setdefault((ns, cid), r)
                        for c in self._collect_catalog_items(obj):
                            ns = c.get("namespace")
                            cid = c.get("id")
                            if ns and cid:
                                catalog_items.setdefault((ns, cid), c)

        records: list[GameRecord] = []
        for (ns, cid), lib in library_records.items():
            cat = catalog_items.get((ns, cid)) or {}
            cats = [c.get("path") for c in (cat.get("categories") or []) if isinstance(c, dict)]
            # Drop pure DLC / addons (parent game covers them).
            if "addons" in cats or "dlc" in cats:
                continue
            # Require a positive game signal: either no catalog (be permissive)
            # or catalog explicitly marks it as a game.
            if cats and "games" not in cats and "applications" not in cats:
                continue

            title = safe_string(cat.get("title")) or safe_string(lib.get("sandboxName")) or safe_string(lib.get("productId"))
            app_name = safe_string(lib.get("appName")) or cid
            if not title:
                title = app_name

            ki = cat.get("keyImages") or []
            imgs = {img.get("type"): img.get("url") for img in ki if isinstance(img, dict)}
            remote_icons = [
                imgs.get("DieselGameBoxTall"),
                imgs.get("OfferImageTall"),
                imgs.get("DieselGameBox"),
                imgs.get("OfferImageWide"),
                imgs.get("Thumbnail"),
            ]
            remote_icons = [u for u in remote_icons if u]

            launch_url = (
                f"com.epicgames.launcher://apps/{ns}%3A{cid}%3A{app_name}?action=launch&silent=true"
            )
            records.append(
                GameRecord(
                    name=title,
                    launcher=self.display_name,
                    launch_url=launch_url,
                    app_id=app_name,
                    install_path=None,
                    icon_path=None,
                    icon_kind="generated",
                    search_terms=normalize_search_terms(app_name, title, ns, cid),
                    extra={
                        "namespace": ns,
                        "catalogItemId": cid,
                        "installed": False,
                        **({"remote_icon_url": remote_icons} if remote_icons else {}),
                    },
                )
            )
        return records

    def _detect_webcache_dirs(self) -> list[Path]:
        local_appdata = os.environ.get("LOCALAPPDATA")
        if not local_appdata:
            return []
        saved = Path(local_appdata) / "EpicGamesLauncher" / "Saved"
        if not saved.exists():
            return []
        results: list[Path] = []
        for sub in saved.iterdir():
            if not sub.is_dir() or not sub.name.startswith("webcache"):
                continue
            cache = sub / "Cache"
            if cache.exists():
                results.append(cache)
        return results

    def _try_decode_all(self, raw: bytes) -> list[bytes]:
        out: list[bytes] = [raw]
        decoders = [gzip.decompress, lambda d: zlib.decompress(d, -15), zlib.decompress]
        if brotli is not None:
            decoders.insert(0, brotli.decompress)  # type: ignore[arg-type]
        for fn in decoders:
            try:
                out.append(fn(raw))
            except Exception:
                pass
        return out

    def _find_json_values(self, buf: bytes):
        try:
            text = buf.decode("utf-8")
        except UnicodeDecodeError:
            text = buf.decode("utf-8", errors="ignore")
        dec = json.JSONDecoder()
        i = 0
        length = len(text)
        while i < length:
            while i < length and text[i] not in "{[":
                i += 1
            if i >= length:
                break
            try:
                obj, end = dec.raw_decode(text, i)
                yield obj
                i = end
            except json.JSONDecodeError:
                i += 1

    def _collect_library_records(self, node, out: list | None = None) -> list:
        if out is None:
            out = []
        if isinstance(node, dict):
            recs = node.get("records")
            if isinstance(recs, list) and recs and isinstance(recs[0], dict) and "catalogItemId" in recs[0]:
                out.extend(recs)
            for v in node.values():
                self._collect_library_records(v, out)
        elif isinstance(node, list):
            for v in node:
                self._collect_library_records(v, out)
        return out

    def _collect_catalog_items(self, node, out: list | None = None) -> list:
        if out is None:
            out = []
        if isinstance(node, dict):
            if (
                isinstance(node.get("title"), str)
                and isinstance(node.get("categories"), list)
                and isinstance(node.get("keyImages"), list)
                and node.get("id")
            ):
                out.append(node)
                return out
            for v in node.values():
                self._collect_catalog_items(v, out)
        elif isinstance(node, list):
            for v in node:
                self._collect_catalog_items(v, out)
        return out

    def _detect_manifest_dir(self) -> Path | None:
        candidates = [
            Path("C:/ProgramData/Epic/EpicGamesLauncher/Data/Manifests"),
            Path("C:/ProgramData/Epic Games Launcher/Data/Manifests"),
        ]
        return first_existing_path(candidates)

    def _parse_manifest(self, path: Path) -> dict[str, object]:
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _load_payloads(self, manifest_dir: Path) -> list[tuple[Path, dict[str, object]]]:
        payloads: list[tuple[Path, dict[str, object]]] = []

        for manifest in sorted(manifest_dir.glob("*.item")):
            payload = self._parse_manifest(manifest)
            if payload:
                payloads.append((manifest, payload))

        launcher_installed = manifest_dir.parent / "LauncherInstalled.dat"
        installed_payload = self._parse_manifest(launcher_installed)
        installation_list = installed_payload.get("InstallationList") if isinstance(installed_payload, dict) else None
        if isinstance(installation_list, list):
            for index, item in enumerate(installation_list):
                if not isinstance(item, dict):
                    continue
                pseudo_manifest = manifest_dir / f"launcher_installed_{index}.item"
                payloads.append((pseudo_manifest, item))

        return payloads

    def _index_icon_images(self, manifest_dir: Path) -> list[Path]:
        data_root = manifest_dir.parent
        roots = [
            data_root / "Catalog",
            data_root / "Catalog" / "cache",
            data_root / "Catalog" / "catcache",
            data_root / "EMS",
            data_root / "Manifests",
            manifest_dir,
        ]
        images: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            for image in iter_image_files(root, max_files=15000):
                key = str(image).lower()
                if key not in seen:
                    seen.add(key)
                    images.append(image)
        return images

    def _discover_icon_path(
        self,
        payload: dict[str, object],
        manifest_path: Path,
        app_name: str,
        install_path: str | None,
        icon_images: list[Path],
        display_name: str,
    ) -> Path | None:
        manifest_dir = manifest_path.parent
        data_root = manifest_dir.parent

        direct_fields = [
            payload.get("DisplayImage"),
            payload.get("Icon"),
            payload.get("IconImage"),
            payload.get("Image"),
            payload.get("ImagePath"),
            payload.get("InstallIcon"),
        ]
        for field in direct_fields:
            if isinstance(field, str):
                candidate = Path(field)
                if not candidate.is_absolute():
                    candidate = manifest_dir / field
                if is_image_file(candidate):
                    return candidate

        ids = [
            app_name,
            safe_string(payload.get("CatalogItemId")),
            safe_string(payload.get("NamespaceId")),
            safe_string(payload.get("ArtifactId")),
        ]
        ids = [value for value in ids if value]

        cache_dirs = [
            data_root / "Catalog" / "catcache",
            data_root / "Catalog" / "cache",
            data_root / "EMS" / "current",
            manifest_dir,
        ]
        for cache_dir in cache_dirs:
            if not cache_dir.exists():
                continue
            candidates: list[Path] = []
            for item_id in ids:
                for extension in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]:
                    candidates.append(cache_dir / f"{item_id}{extension}")
                    candidates.append(cache_dir / f"{item_id}_icon{extension}")
                    candidates.append(cache_dir / f"{item_id}-icon{extension}")
                    candidates.append(cache_dir / f"{item_id}_logo{extension}")
                    candidates.append(cache_dir / f"{item_id}-logo{extension}")
            resolved = first_existing_image(candidates)
            if resolved:
                return resolved

        desired_tokens = set()
        desired_tokens |= tokenize(app_name)
        desired_tokens |= tokenize(safe_string(payload.get("CatalogItemId")))
        desired_tokens |= tokenize(safe_string(payload.get("NamespaceId")))
        desired_tokens |= tokenize(safe_string(payload.get("ArtifactId")))

        preferred_tokens = tokenize(display_name)
        fuzzy_match = best_image_match(icon_images, desired_tokens=desired_tokens, preferred_tokens=preferred_tokens)
        if fuzzy_match:
            return fuzzy_match

        if install_path:
            install_dir = Path(install_path)
            local_candidates: list[Path] = []
            for root in [install_dir, install_dir / ".egstore"]:
                for filename in [
                    "icon.png",
                    "icon.jpg",
                    "icon.jpeg",
                    "logo.png",
                    "logo.jpg",
                    "cover.png",
                    "cover.jpg",
                    "banner.png",
                    "banner.jpg",
                ]:
                    local_candidates.append(root / filename)
            resolved = first_existing_image(local_candidates)
            if resolved:
                return resolved

        return None
