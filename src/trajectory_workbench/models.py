from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class RegistryEntry:
    id: str
    path: str
    label: str
    adapter_id: str
    registered_at: str
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProbeResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    attempt_id: str | None = None


@dataclass(slots=True)
class NormalizedRun:
    adapter_id: str
    source_path: str
    run_id: str
    title: str
    attempt_id: str
    metrics: dict[str, Any]
    timeline: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    tool_catalog: list[dict[str, Any]]
    native_tool_surfaces: list[str]
    artifact_states: list[dict[str, Any]]
    workbench: list[dict[str, Any]]
    runtime: dict[str, Any]

    def summary_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "source_path": self.source_path,
            "run_id": self.run_id,
            "title": self.title,
            "attempt_id": self.attempt_id,
            "metrics": self.metrics,
            "timeline": self.timeline,
            "tools": self.tools,
            "tool_catalog": self.tool_catalog,
            "native_tool_surfaces": self.native_tool_surfaces,
            "artifact_states": self.artifact_states,
            "workbench": self.workbench,
            "runtime": self.runtime,
        }
