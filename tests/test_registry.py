from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.registry import Registry


class RegistryTest(unittest.TestCase):
    def test_registers_resolved_path_and_preserves_missing_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "run"
            source.mkdir()
            registry = Registry(base / "registry.json")

            entry = registry.register(source, "Example")

            self.assertEqual(entry.path, str(source.resolve()))
            self.assertTrue(entry.available)
            raw = json.loads((base / "registry.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["entries"][0]["label"], "Example")

            source.rmdir()
            listed = registry.list_entries()
            self.assertEqual(len(listed), 1)
            self.assertFalse(listed[0].available)

    def test_register_is_idempotent_for_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "run"
            source.mkdir()
            registry = Registry(base / "registry.json")

            first = registry.register(source, None)
            second = registry.register(source, "Renamed")

            self.assertEqual(first.id, second.id)
            self.assertEqual(registry.list_entries()[0].label, "Renamed")


if __name__ == "__main__":
    unittest.main()

