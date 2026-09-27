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


OUTCOME_STATUSES = ("pass", "partial", "fail", "error", "unknown")


@dataclass(slots=True)
class NormalizedRun:
    """One trajectory in the format-independent shape the service and UI consume.

    `task`, `outcome` and `meta` are filled by every adapter. `outcome` is the grader or
    harness verdict recorded in the source; it is never inferred from the transcript.
    The MoH-specific fields (artifact states, Workbench, runtime chain, model prompt) stay
    empty for other formats.
    """

    adapter_id: str
    source_path: str
    run_id: str
    title: str
    metrics: dict[str, Any]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    timeline: list[dict[str, Any]] = field(default_factory=list)
    attempt_id: str | None = None
    tool_catalog: list[dict[str, Any]] = field(default_factory=list)
    native_tool_surfaces: list[str] = field(default_factory=list)
    artifact_states: list[dict[str, Any]] = field(default_factory=list)
    workbench: list[dict[str, Any]] = field(default_factory=list)
    runtime: dict[str, Any] = field(default_factory=dict)
    model_prompt: dict[str, Any] | None = None
    task: dict[str, Any] = field(default_factory=dict)
    outcome: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

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
            "model_prompt": self.model_prompt,
            "task": self.task,
            "outcome": self.outcome,
            "meta": self.meta,
        }
