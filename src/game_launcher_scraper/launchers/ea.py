from __future__ import annotations

import json
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


class EAAdapter(LauncherAdapter):
    launcher_id = "ea"
    display_name = "EA app"

    def discover(self, config: ScanConfig) -> list[GameRecord]:
        manifest_dir = config.ea_manifest_dir or self._detect_manifest_dir()
        if manifest_dir is None or not manifest_dir.exists():
            return []

        icon_images = self._index_icon_images(manifest_dir)
        records: list[GameRecord] = []
        seen: set[str] = set()

        # Primary source on EA Desktop: each installed game gets a folder under
        # C:\ProgramData\EA Desktop\InstallData\<Game Name>\. Manifest .json/.item/.mfst
        # files only exist for legacy Origin installs.
        for record in self._discover_from_install_data(manifest_dir, icon_images):
            key = record.app_id.lower() if record.app_id else record.name.lower()
            if key in seen:
                continue
            seen.add(key)
            records.append(record)

        for candidate in sorted(manifest_dir.rglob("*")):
            if not candidate.is_file() or candidate.suffix.lower() not in {".json", ".item", ".mfst"}:
                continue
            payload_entries = self._parse_candidate_entries(candidate)
            if not payload_entries:
                continue

            for payload in payload_entries:
                display_name = safe_string(payload.get("displayName")) or safe_string(payload.get("title")) or safe_string(payload.get("name"))
                offer_id = safe_string(payload.get("offerId")) or safe_string(payload.get("productId"))
                if not display_name and not offer_id:
                    continue

                install_path = safe_string(payload.get("installPath")) or safe_string(payload.get("InstallPath"))

                # Only emit games the user actually has installed.
                # Evidence is either an installPath on disk, or a manifest
                # filed under an "Installed Games" / "Installed" segment.
                if not self._is_installed_entry(candidate, install_path):
                    continue

                launch_token = offer_id or safe_string(payload.get("gameId")) or safe_string(payload.get("id"))
                if not launch_token:
                    launch_token = candidate.stem

                dedupe_key = launch_token.lower()
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                if not display_name:
                    display_name = f"EA Game {launch_token}"

                icon_path = self._discover_icon_path(
                    payload=payload,
                    manifest_path=candidate,
                    launch_token=launch_token,
                    install_path=install_path or None,
                    icon_images=icon_images,
                    display_name=display_name,
                )
                records.append(
                    GameRecord(
                        name=display_name,
                        launcher=self.display_name,
                        launch_url=f"origin2://game/launch?offerIds={launch_token}",
                        app_id=launch_token,
                        install_path=install_path or None,
                        icon_path=str(icon_path) if icon_path else None,
                        icon_kind="local" if icon_path else "generated",
                        search_terms=normalize_search_terms(offer_id, payload.get("gameId"), install_path),
                        extra={"manifest": str(candidate)},
                    )
                )

        return records

    def _detect_manifest_dir(self) -> Path | None:
        candidates = [
            Path("C:/ProgramData/EA Desktop/Installed Games"),
            Path("C:/ProgramData/EA Desktop"),
            Path("C:/ProgramData/Electronic Arts/EA Desktop/Installed Games"),
            Path.home() / "AppData" / "Local" / "Electronic Arts" / "EA Desktop",
        ]
        return first_existing_path(candidates)

    def _discover_from_install_data(
        self,
        manifest_dir: Path,
        icon_images: list,
    ) -> list[GameRecord]:
        """Enumerate EA Desktop's InstallData/<Title>/ folders as installed games."""
        # Scoped to the configured manifest_dir so tests can isolate the scan
        # via a temporary directory and the real machine paths don't leak in.
        install_data_dir = manifest_dir / "InstallData"
        if not install_data_dir.exists():
            return []

        install_locations = self._read_uninstall_locations()

        records: list[GameRecord] = []
        for entry in sorted(install_data_dir.iterdir()):
            if not entry.is_dir():
                continue
            display_name = entry.name
            install_path = install_locations.get(display_name.lower())

            icon_path = self._discover_icon_path(
                payload={},
                manifest_path=entry,
                launch_token=display_name,
                install_path=install_path,
                icon_images=icon_images,
                display_name=display_name,
            )
            # Use a URI fragment to keep each game's deep link unique (EA
            # Desktop ignores fragments) so the catalog-level dedupe by
            # (launcher, launch_url) does not collapse multiple installs.
            slug = "".join(ch if ch.isalnum() else "-" for ch in display_name.lower()).strip("-") or "game"
            launch_url = f"origin2://library/open#{slug}"
            records.append(
                GameRecord(
                    name=display_name,
                    launcher=self.display_name,
                    launch_url=launch_url,
                    app_id=display_name,
                    install_path=install_path,
                    icon_path=str(icon_path) if icon_path else None,
                    icon_kind="local" if icon_path else "generated",
                    search_terms=normalize_search_terms(display_name, install_path),
                    extra={"installed": True, "source": "InstallData"},
                )
            )
        return records

    def _read_uninstall_locations(self) -> dict[str, str]:
        """Best-effort lookup of installed-game directories from the Windows registry."""
        try:
            import winreg  # type: ignore[import-not-found]
        except ImportError:
            return {}

        locations: dict[str, str] = {}
        roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, subkey in roots:
            try:
                root_key = winreg.OpenKey(hive, subkey)
            except OSError:
                continue
            try:
                index = 0
                while True:
                    try:
                        child_name = winreg.EnumKey(root_key, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        child = winreg.OpenKey(root_key, child_name)
                        display_name, _ = winreg.QueryValueEx(child, "DisplayName")
                        try:
                            install_location, _ = winreg.QueryValueEx(child, "InstallLocation")
                        except OSError:
                            continue
                        if display_name and install_location and Path(install_location).exists():
                            locations[str(display_name).lower()] = str(install_location)
                    except OSError:
                        continue
            finally:
                winreg.CloseKey(root_key)
        return locations

    def _is_installed_entry(self, manifest_path: Path, install_path: str | None) -> bool:
        if install_path:
            try:
                if Path(install_path).exists():
                    return True
            except OSError:
                pass
        path_segments = {part.lower() for part in manifest_path.parts}
        if "installed games" in path_segments or "installed" in path_segments:
            return True
        return False

    def _parse_candidate_entries(self, path: Path) -> list[dict[str, object]]:
        try:
            if path.suffix.lower() in {".json", ".item", ".mfst"}:
                payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
                return self._iter_entries(payload)
        except (OSError, json.JSONDecodeError):
            return []
        return []

    def _iter_entries(self, payload: object) -> list[dict[str, object]]:
        if isinstance(payload, dict):
            entries: list[dict[str, object]] = [payload]
            for value in payload.values():
                if isinstance(value, dict):
                    entries.append(value)
                elif isinstance(value, list):
                    entries.extend(item for item in value if isinstance(item, dict))
            return entries
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _index_icon_images(self, manifest_dir: Path) -> list[Path]:
        roots = [
            manifest_dir,
            manifest_dir.parent,
            Path("C:/ProgramData/EA Desktop"),
            Path("C:/ProgramData/EA Desktop/Cache"),
            Path("C:/ProgramData/EA Desktop/Cache/Assets"),
            Path("C:/ProgramData/Electronic Arts/EA Desktop"),
            Path("C:/ProgramData/Electronic Arts/EA Desktop/Cache"),
            Path.home() / "AppData" / "Local" / "Electronic Arts" / "EA Desktop",
            Path.home() / "AppData" / "Local" / "Electronic Arts" / "EA Desktop" / "Cache",
        ]

        images: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            for image in iter_image_files(root, max_files=12000):
                key = str(image).lower()
                if key in seen:
                    continue
                seen.add(key)
                images.append(image)
        return images

    def _discover_icon_path(
        self,
        payload: dict[str, object],
        manifest_path: Path,
        launch_token: str,
        install_path: str | None,
        icon_images: list[Path],
        display_name: str,
    ) -> Path | None:
        manifest_dir = manifest_path.parent

        direct_fields = [
            payload.get("iconPath"),
            payload.get("IconPath"),
            payload.get("icon"),
            payload.get("image"),
            payload.get("imagePath"),
            payload.get("boxArtPath"),
            payload.get("packArt"),
        ]
        for field in direct_fields:
            if isinstance(field, str):
                candidate = Path(field)
                if not candidate.is_absolute():
                    candidate = manifest_dir / field
                if is_image_file(candidate):
                    return candidate

        id_tokens = [
            launch_token,
            safe_string(payload.get("offerId")),
            safe_string(payload.get("productId")),
            safe_string(payload.get("gameId")),
            safe_string(payload.get("id")),
        ]
        id_tokens = [token for token in id_tokens if token]

        cache_dirs = [
            manifest_dir,
            manifest_dir.parent,
            Path("C:/ProgramData/EA Desktop"),
            Path("C:/ProgramData/EA Desktop/Cache"),
            Path("C:/ProgramData/EA Desktop/Cache/Assets"),
            Path("C:/ProgramData/Electronic Arts/EA Desktop"),
            Path("C:/ProgramData/Electronic Arts/EA Desktop/Cache"),
            Path.home() / "AppData" / "Local" / "Electronic Arts" / "EA Desktop" / "Cache",
        ]
        for cache_dir in cache_dirs:
            if not cache_dir.exists():
                continue
            candidates: list[Path] = []
            for token in id_tokens:
                for extension in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]:
                    candidates.append(cache_dir / f"{token}{extension}")
                    candidates.append(cache_dir / f"{token}_icon{extension}")
                    candidates.append(cache_dir / f"{token}-icon{extension}")
                    candidates.append(cache_dir / f"{token}_boxart{extension}")
                    candidates.append(cache_dir / f"{token}-boxart{extension}")
            resolved = first_existing_image(candidates)
            if resolved:
                return resolved

        desired_tokens = set()
        desired_tokens |= tokenize(launch_token)
        desired_tokens |= tokenize(safe_string(payload.get("offerId")))
        desired_tokens |= tokenize(safe_string(payload.get("productId")))
        desired_tokens |= tokenize(safe_string(payload.get("gameId")))
        preferred_tokens = tokenize(display_name)
        fuzzy_match = best_image_match(icon_images, desired_tokens=desired_tokens, preferred_tokens=preferred_tokens)
        if fuzzy_match:
            return fuzzy_match

        if install_path:
            install_dir = Path(install_path)
            local_candidates: list[Path] = []
            for filename in [
                "icon.png",
                "icon.jpg",
                "icon.jpeg",
                "boxart.png",
                "boxart.jpg",
                "cover.png",
                "cover.jpg",
                "artwork.png",
                "artwork.jpg",
            ]:
                local_candidates.append(install_dir / filename)
            resolved = first_existing_image(local_candidates)
            if resolved:
                return resolved

        return None
