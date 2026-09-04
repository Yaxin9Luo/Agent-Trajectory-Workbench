from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.registry import Registry
from trajectory_workbench.service import WorkbenchService
from tests.fixtures import make_moh_run, write_json


class WorkbenchServiceTest(unittest.TestCase):
    def test_import_list_load_and_filter_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_moh_run(base / "run")
            service = WorkbenchService(Registry(base / "registry.json"))

            imported = service.import_run(str(run), "Demo")
            listed = service.list_runs()
            normalized = service.get_run(imported["id"])
            bash_only = service.get_messages(
                imported["id"], tool="Bash", offset=0, limit=20
            )
            searched = service.get_messages(
                imported["id"], search="Inspect files", offset=0, limit=20
            )

        self.assertEqual(listed[0]["label"], "Demo")
        self.assertTrue(listed[0]["available"])
        self.assertEqual(normalized["metrics"]["tool_calls"], 2)
        self.assertEqual(
            [item["name"] for item in normalized["tool_catalog"]],
            ["Bash", "Slides Workbench", "Task", "WebSearch"],
        )
        self.assertEqual(normalized["native_tool_surfaces"], ["default"])
        self.assertEqual(bash_only["total"], 1)
        self.assertEqual(bash_only["items"][0]["tools"][0]["name"], "Bash")
        self.assertEqual(searched["total"], 1)

    def test_rejects_relative_and_invalid_run_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = WorkbenchService(Registry(Path(temp) / "registry.json"))

            with self.assertRaisesRegex(ValueError, "absolute"):
                service.import_run("relative/run", None)
            with self.assertRaisesRegex(ValueError, "Missing events.jsonl"):
                service.import_run(temp, None)

    def test_file_path_rejects_escape_from_registered_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_moh_run(base / "run")
            outside = base / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            service = WorkbenchService(Registry(base / "registry.json"))
            run_id = service.import_run(str(run), None)["id"]

            artifact = service.resolve_file(
                run_id, "attempts/001/workspace/artifact.html"
            )
            with self.assertRaises(PermissionError):
                service.resolve_file(run_id, "../secret.txt")

        self.assertEqual(artifact.name, "artifact.html")

    def test_manifest_changes_invalidate_the_cached_tool_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_moh_run(base / "run")
            service = WorkbenchService(Registry(base / "registry.json"))
            run_id = service.import_run(str(run), None)["id"]
            service.get_run(run_id)
            manifest_path = run / "resolved_run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["execution_profile"]["agent"]["allowed_tools"].append(
                "future_search"
            )

            write_json(manifest_path, manifest)
            refreshed = service.get_run(run_id)

        self.assertIn(
            "future_search", [item["name"] for item in refreshed["tool_catalog"]]
        )


if __name__ == "__main__":
    unittest.main()
