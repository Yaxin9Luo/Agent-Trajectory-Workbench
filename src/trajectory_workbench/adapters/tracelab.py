"""TraceLab / Harbor-export trial directories: `dialog.jsonl` + `results.json`.

`dialog.jsonl` holds one chat sample per context segment or subagent; `results.json`
carries the grader verdict for the whole trial, and `run_metadata.json` the agent and
model. Exports nest a copy of the same files one level down; the top directory is used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from trajectory_workbench.adapters.common import read_json
from trajectory_workbench.adapters.openai_chat import (
    OpenAIChatAdapter,
    looks_like_chat_sample,
    tracelab_outcome,
)
from trajectory_workbench.models import NormalizedRun


class TraceLabTrialAdapter:
    adapter_id = "tracelab"
    label = "TraceLab trial"

    def __init__(self) -> None:
        self.chat = OpenAIChatAdapter()

    def detect(self, path: Path) -> bool:
        return (
            path.is_dir()
            and (path / "dialog.jsonl").is_file()
            and (path / "results.json").is_file()
        )

    def _context(self, path: Path) -> tuple[dict[str, Any], str | None, dict[str, Any], dict[str, Any]]:
        try:
            outcome, task_id, timing = tracelab_outcome(read_json(path / "results.json"))
        except (OSError, ValueError):
            outcome, task_id, timing = None, None, {}
        meta: dict[str, Any] = {"format": "tracelab", "trial_dir": path.name}
        metadata_path = path / "run_metadata.json"
        if metadata_path.is_file():
            try:
                metadata = read_json(metadata_path)
            except (OSError, ValueError):
                metadata = {}
            if isinstance(metadata, dict):
                meta["dataset"] = metadata.get("dataset_name")
                meta["harness"] = metadata.get("agent_name")
                meta["harness_version"] = metadata.get("agent_version")
                meta["schema"] = metadata.get("source_schema")
                if metadata.get("model_name"):
                    meta["model"] = metadata["model_name"]
        return outcome, task_id, timing, meta

    def iter_runs(self, path: Path) -> Iterator[tuple[dict[str, Any] | None, NormalizedRun]]:
        outcome, task_id, timing, meta = self._context(path)
        dialog = path / "dialog.jsonl"
        with dialog.open("rb") as handle:
            index = 0
            while True:
                offset = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                if not raw.strip():
                    continue
                try:
                    sample = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    index += 1
                    continue
                if isinstance(sample, dict) and looks_like_chat_sample(sample):
                    locator = {"offset": offset, "row": index, "key": str(sample.get("id") or f"@{offset}")}
                    yield locator, self._convert(sample, path, index, outcome, task_id, timing, meta)
                index += 1

    def load(self, path: Path, locator: dict[str, Any] | None) -> NormalizedRun:
        outcome, task_id, timing, meta = self._context(path)
        with (path / "dialog.jsonl").open("rb") as handle:
            handle.seek(int((locator or {}).get("offset", 0)))
            sample = json.loads(handle.readline())
        return self._convert(sample, path, int((locator or {}).get("row", 0)), outcome, task_id, timing, meta)

    def _convert(self, sample, path, index, outcome, task_id, timing, meta) -> NormalizedRun:
        return self.chat.from_sample(
            sample,
            path=path,
            row_index=index,
            outcome=dict(outcome) if outcome else None,
            task_id=task_id,
            extra_meta={k: v for k, v in meta.items() if v is not None},
            timing=timing,
            adapter_id=self.adapter_id,
        )
