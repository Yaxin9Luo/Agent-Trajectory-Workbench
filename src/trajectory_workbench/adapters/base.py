from __future__ import annotations

from pathlib import Path
from typing import Protocol

from trajectory_workbench.models import NormalizedRun, ProbeResult


class TrajectoryAdapter(Protocol):
    adapter_id: str

    def probe(self, path: Path) -> ProbeResult: ...

    def load(self, path: Path) -> NormalizedRun: ...

