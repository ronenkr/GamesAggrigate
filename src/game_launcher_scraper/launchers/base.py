from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import GameRecord, ScanConfig


class LauncherAdapter(ABC):
    launcher_id: str
    display_name: str

    @abstractmethod
    def discover(self, config: ScanConfig) -> list[GameRecord]:
        raise NotImplementedError
