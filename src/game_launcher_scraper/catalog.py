from __future__ import annotations

from dataclasses import replace

from .launchers import BUILTIN_ADAPTERS, load_external_adapters
from .models import GameRecord, ScanConfig


def scan_launchers(config: ScanConfig) -> list[GameRecord]:
    adapter_classes = list(BUILTIN_ADAPTERS)
    if config.include_external_adapters:
        adapter_classes.extend(load_external_adapters())

    records: list[GameRecord] = []
    seen: set[tuple[str, str]] = set()

    for adapter_class in adapter_classes:
        adapter = adapter_class()
        try:
            discovered = adapter.discover(config)
        except Exception:
            continue
        for record in discovered:
            key = (record.launcher.lower(), record.launch_url.lower())
            if key in seen:
                continue
            seen.add(key)
            records.append(record)

    records.sort(key=lambda record: (record.name.lower(), record.launcher.lower()))
    return records


def enrich_records(records: list[GameRecord]) -> list[GameRecord]:
    return [replace(record) for record in records]
