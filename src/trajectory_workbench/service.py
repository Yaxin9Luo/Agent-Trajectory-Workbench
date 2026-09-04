from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trajectory_workbench.adapters.moh_v1 import MohV1Adapter
from trajectory_workbench.models import NormalizedRun
from trajectory_workbench.registry import Registry


class WorkbenchService:
    def __init__(
        self,
        registry: Registry,
        adapter: MohV1Adapter | None = None,
    ) -> None:
        self.registry = registry
        self.adapter = adapter or MohV1Adapter()
        self._cache: dict[str, tuple[tuple[tuple[str, int, int], ...], NormalizedRun]] = {}

    def import_run(self, source_path: str, label: str | None) -> dict[str, Any]:
        supplied = Path(source_path).expanduser()
        if not supplied.is_absolute():
            raise ValueError("Run path must be absolute")
        resolved = supplied.resolve()
        probe = self.adapter.probe(resolved)
        if not probe.ok:
            raise ValueError("; ".join(probe.errors))
        entry = self.registry.register(resolved, label, self.adapter.adapter_id)
        self._cache.pop(entry.id, None)
        return entry.to_dict()

    def list_runs(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.registry.list_entries()]

    def get_run(self, run_id: str) -> dict[str, Any]:
        entry, normalized = self._load(run_id)
        payload = normalized.summary_dict()
        payload["id"] = entry.id
        payload["label"] = entry.label
        payload["available"] = entry.available
        return payload

    def get_messages(
        self,
        run_id: str,
        *,
        roles: set[str] | None = None,
        tool: str | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        _, normalized = self._load(run_id)
        selected: list[dict[str, Any]] = []
        query = (search or "").casefold()
        for message in normalized.messages:
            if roles and message["role"] not in roles:
                continue
            if tool and not any(item["name"] == tool for item in message["tools"]):
                continue
            if query:
                haystack = json.dumps(message, ensure_ascii=False).casefold()
                if query not in haystack:
                    continue
            selected.append(message)
        bounded_offset = max(0, offset)
        bounded_limit = min(500, max(1, limit))
        return {
            "total": len(selected),
            "offset": bounded_offset,
            "limit": bounded_limit,
            "items": selected[bounded_offset : bounded_offset + bounded_limit],
        }

    def resolve_file(self, run_id: str, relative_path: str) -> Path:
        entry = self.registry.get(run_id)
        if entry is None:
            raise KeyError(run_id)
        root = Path(entry.path).resolve()
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root):
            raise PermissionError("Requested file is outside the registered run")
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    def _load(self, run_id: str) -> tuple[Any, NormalizedRun]:
        entry = self.registry.get(run_id)
        if entry is None:
            raise KeyError(run_id)
        if not entry.available:
            raise FileNotFoundError(entry.path)
        root = Path(entry.path)
        fingerprint = self._fingerprint(root)
        cached = self._cache.get(run_id)
        if cached is not None and cached[0] == fingerprint:
            return entry, cached[1]
        normalized = self.adapter.load(root)
        self._cache[run_id] = (fingerprint, normalized)
        return entry, normalized

    def _fingerprint(self, root: Path) -> tuple[tuple[str, int, int], ...]:
        candidates = [root / "events.jsonl", root / "resolved_run_manifest.json"]
        candidates.extend((root / "attempts").glob("*/records/*.json"))
        candidates.extend((root / "attempts").glob("*/records/*.jsonl"))
        rows: list[tuple[str, int, int]] = []
        for path in sorted(candidates):
            if not path.is_file():
                continue
            stat = path.stat()
            rows.append((str(path.relative_to(root)), stat.st_mtime_ns, stat.st_size))
        return tuple(rows)
