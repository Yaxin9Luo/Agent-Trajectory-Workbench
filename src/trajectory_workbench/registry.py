from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trajectory_workbench.models import RegistryEntry


DEFAULT_REGISTRY_PATH = Path.home() / ".agent-trajectory-workbench" / "registry.json"


class Registry:
    def __init__(self, path: Path = DEFAULT_REGISTRY_PATH) -> None:
        self.path = path.expanduser().resolve()

    def register(
        self,
        source_path: Path,
        label: str | None,
        adapter_id: str = "moh-v1",
    ) -> RegistryEntry:
        resolved = source_path.expanduser().resolve()
        entry_id = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
        data = self._read()
        entries = data["entries"]
        existing = next((item for item in entries if item["id"] == entry_id), None)
        now = datetime.now(UTC).isoformat()
        display_label = (label or resolved.name).strip() or resolved.name
        if existing is None:
            stored = {
                "id": entry_id,
                "path": str(resolved),
                "label": display_label,
                "adapter_id": adapter_id,
                "registered_at": now,
            }
            entries.append(stored)
        else:
            existing["label"] = display_label
            existing["adapter_id"] = adapter_id
            stored = existing
        self._write(data)
        return self._entry(stored)

    def list_entries(self) -> list[RegistryEntry]:
        return [self._entry(item) for item in self._read()["entries"]]

    def get(self, entry_id: str) -> RegistryEntry | None:
        item = next(
            (item for item in self._read()["entries"] if item["id"] == entry_id),
            None,
        )
        return self._entry(item) if item is not None else None

    def _entry(self, item: dict[str, Any]) -> RegistryEntry:
        path = str(item["path"])
        return RegistryEntry(
            id=str(item["id"]),
            path=path,
            label=str(item["label"]),
            adapter_id=str(item.get("adapter_id", "moh-v1")),
            registered_at=str(item["registered_at"]),
            available=Path(path).is_dir(),
        )

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "entries": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("entries"), list):
            raise ValueError(f"Unsupported registry format: {self.path}")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

