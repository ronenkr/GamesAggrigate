from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".ico"}
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def read_json(path: Path) -> Any | None:
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = _NON_ALNUM.sub("-", value)
    value = value.strip("-")
    return value or "item"


def stable_short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:10]


def unique_name(base_name: str, discriminator: str) -> str:
    return f"{slugify(base_name)}-{stable_short_hash(discriminator)}"


def first_existing_path(candidates: Iterable[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS


def first_existing_image(candidates: Iterable[Path]) -> Path | None:
    for candidate in candidates:
        if is_image_file(candidate):
            return candidate
    return None


def tokenize(value: str | None) -> set[str]:
    if not value:
        return set()
    normalized = _TOKEN_SPLIT.sub(" ", value.lower())
    tokens = {token for token in normalized.split() if token}
    return {token for token in tokens if len(token) >= 2}


def iter_image_files(root: Path, max_files: int = 50000) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    results: list[Path] = []
    count = 0
    for path in root.rglob("*"):
        if count >= max_files:
            break
        if is_image_file(path):
            results.append(path)
            count += 1
    return results


def best_image_match(
    images: Iterable[Path],
    desired_tokens: set[str],
    preferred_tokens: set[str] | None = None,
) -> Path | None:
    desired = {token.lower() for token in desired_tokens if token}
    preferred = {token.lower() for token in (preferred_tokens or set()) if token}
    if not desired and not preferred:
        return None

    best: tuple[int, int, int, Path] | None = None
    for image in images:
        stem_tokens = tokenize(image.stem)
        parent_tokens = tokenize(image.parent.name)
        all_tokens = stem_tokens | parent_tokens
        if not all_tokens:
            continue

        desired_hits = len(desired & all_tokens)
        preferred_hits = len(preferred & all_tokens)

        # Use compact filenames to avoid selecting giant cache junk when ties occur.
        rank = (desired_hits, preferred_hits, -len(image.name), image)
        if best is None or rank > best:
            best = rank

    if best is None:
        return None
    if best[0] == 0 and best[1] == 0:
        return None
    return best[3]


def safe_string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def normalize_search_terms(*values: Any) -> tuple[str, ...]:
    terms: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            terms.append(text)
    return tuple(terms)
