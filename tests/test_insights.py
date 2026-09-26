from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.insights import artifact_diff, compare_runs, error_aggregation
from trajectory_workbench.models import NormalizedRun
from trajectory_workbench.registry import Registry
from trajectory_workbench.service import WorkbenchService
from tests.fixtures import make_moh_run


def _run(tool_errors: int = 0, run_id: str = "run") -> NormalizedRun:
    tools = []
    for i in range(2 + tool_errors):
        is_error = i >= 2
        tools.append(
            {
                "id": f"t{i}",
                "name": "Bash" if i % 2 == 0 else "WebSearch",
                "input": {"cmd": i},
                "result": {"text": "err" if is_error else "ok", "is_error": is_error, "images": []},
                "offset_ms": i * 100,
                "line_number": i,
            }
        )
    return NormalizedRun(
        adapter_id="moh-v1",
        source_path="/tmp/run",
        run_id=run_id,
        title="run",
        attempt_id="001",
        metrics={"tool_calls": len(tools)},
        timeline=[],
        messages=[],
        tools=tools,
        tool_catalog=[],
        native_tool_surfaces=[],
        artifact_states=[],
        workbench=[],
        runtime={"classification": "completed"},
        model_prompt=None,
    )


class ErrorAggregationTest(unittest.TestCase):
    def test_no_retry_when_error_is_last(self) -> None:
        normalized = _run(1)
        result = error_aggregation(normalized)
        self.assertEqual(result["error_count"], 1)
        self.assertIsNone(result["errors"][0]["recovery"])
        self.assertEqual(result["retry_count"], 0)

    def test_retry_is_recognized(self) -> None:
        normalized = _run(1)
        error_name = normalized.tools[-1]["name"]
        normalized.tools.append(
            {
                "id": "t-retry",
                "name": error_name,
                "input": {"cmd": 99},
                "result": {"text": "ok", "is_error": False, "images": []},
                "offset_ms": 999,
                "line_number": 9,
            }
        )
        result = error_aggregation(normalized)
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(result["errors"][0]["recovery"]["tool_id"], "t-retry")


class ArtifactDiffTest(unittest.TestCase):
    def test_diffs_between_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "attempts" / "001" / "workspace"
            workspace.mkdir(parents=True)
            artifact = workspace / "artifact.html"
            artifact.write_text("<p>one</p>\n", encoding="utf-8")
            first_sha = hashlib.sha256(b"<p>one</p>\n").hexdigest()
            sealed = root / "attempts" / "001" / "records" / "sealed" / "artifact.html"
            sealed.parent.mkdir(parents=True)
            sealed.write_text("<p>one</p>\n", encoding="utf-8")
            artifact.write_text("<p>one</p>\n<p>two</p>\n", encoding="utf-8")
            second_sha = hashlib.sha256(b"<p>one</p>\n<p>two</p>\n").hexdigest()
            normalized = _run()
            normalized.artifact_states = [
                {"received_offset_ms": 0, "size_bytes": 10, "artifact_sha256": first_sha},
                {"received_offset_ms": 100, "size_bytes": 20, "artifact_sha256": second_sha},
            ]
            result = artifact_diff(normalized, root)
            self.assertEqual(len(result["diffs"]), 1)
            self.assertEqual(result["diffs"][0]["added"], 1)
            self.assertEqual(result["diffs"][0]["removed"], 0)
            self.assertFalse(result["diffs"][0]["unavailable"])


class CompareRunsTest(unittest.TestCase):
    def test_differing_tools_are_listed(self) -> None:
        a = _run(0, "run-a")
        b = _run(0, "run-b")
        a.tool_catalog = [{"name": "Bash", "call_count": 3}]
        b.tool_catalog = [{"name": "Bash", "call_count": 1}, {"name": "WebSearch", "call_count": 2}]
        result = compare_runs([a, b])
        self.assertIn("Bash", result["comparison"]["tool_diff"])
        self.assertIn("WebSearch", result["comparison"]["tool_diff"])
        self.assertEqual(result["run_count"], 2)


class InsightsServiceTest(unittest.TestCase):
    def test_errors_and_diffs_are_served(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_moh_run(base / "run")
            service = WorkbenchService(Registry(base / "registry.json"))
            run_id = service.import_run(str(run), None)["id"]
            errors = service.get_errors(run_id)
            diffs = service.get_artifact_diffs(run_id)
            comparison = service.compare([run_id, run_id])
        self.assertEqual(errors["error_count"], 0)
        self.assertGreaterEqual(diffs["state_count"], 0)
        self.assertEqual(comparison["run_count"], 2)


if __name__ == "__main__":
    unittest.main()
