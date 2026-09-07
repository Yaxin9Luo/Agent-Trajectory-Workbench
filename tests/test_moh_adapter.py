from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.adapters.moh_v1 import MohV1Adapter
from tests.fixtures import make_moh_run, write_json, write_jsonl


class MohV1AdapterTest(unittest.TestCase):
    def test_runtime_classification_uses_finalization_and_does_not_invent_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            write_jsonl(run / "events.jsonl", [])
            self.assertEqual(MohV1Adapter().load(run).runtime["classification"], "unknown")
            write_jsonl(run / "events.jsonl", [{
                "event_type": "run.finalized", "payload": {"classification": None},
            }])
            self.assertEqual(MohV1Adapter().load(run).runtime["classification"], "completed")
            write_jsonl(run / "events.jsonl", [{
                "event_type": "run.finalized", "payload": {"classification": "cancelled"},
            }])
            self.assertEqual(MohV1Adapter().load(run).runtime["classification"], "cancelled")

    def test_moh_image_tool_merges_manifest_session_and_observed_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run", extra_tool_names=(
                "mcp__generate_image__generate_image", "mcp__future_tools__generate_image",
            ))
            manifest_path = run / "resolved_run_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["execution_profile"]["agent"]["allowed_tools"].append("generate_image")
            write_json(manifest_path, manifest)
            trace_path = run / "attempts/001/records/trajectory.jsonl"
            trace = [json.loads(line) for line in trace_path.read_text().splitlines()]
            trace[0]["tools"].append("mcp__generate_image__generate_image")
            write_jsonl(trace_path, trace)

            normalized = MohV1Adapter().load(run)

        catalog = {item["name"]: item for item in normalized.tool_catalog}
        image = catalog["generate_image"]
        self.assertEqual(image["call_count"], 1)
        self.assertTrue(image["declared"])
        self.assertTrue(image["available_in_session"])
        self.assertEqual(set(image["raw_names"]), {
            "generate_image", "mcp__generate_image__generate_image",
        })
        self.assertNotIn("mcp__generate_image__generate_image", catalog)
        self.assertEqual(catalog["mcp__future_tools__generate_image"]["call_count"], 1)
        tool = next(tool for tool in normalized.tools if tool["name"] == "generate_image")
        self.assertEqual(tool["raw_name"], "mcp__generate_image__generate_image")
        self.assertEqual(tool["result"]["text"], "future tool result")

    def test_probe_reports_missing_contract_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = MohV1Adapter().probe(Path(temp))

        self.assertFalse(result.ok)
        self.assertTrue(any("events.jsonl" in item for item in result.errors))

    def test_load_normalizes_tools_messages_states_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")

            normalized = MohV1Adapter().load(run)

        self.assertEqual(normalized.adapter_id, "moh-v1")
        self.assertEqual(normalized.attempt_id, "001")
        self.assertEqual(normalized.metrics["thinking_token_events"], 1)
        self.assertEqual(len(normalized.tools), 2)
        self.assertEqual(normalized.tools[0]["result"]["text"], "ok")
        self.assertEqual(len(normalized.artifact_states), 2)
        self.assertEqual(len(normalized.workbench), 1)
        self.assertEqual(normalized.workbench[0]["observation"]["status"], "completed")
        self.assertEqual(normalized.runtime["classification"], "artifact_invalid")
        self.assertEqual(normalized.runtime["failure_message"], "sealed observation missing")
        self.assertTrue(all(message["role"] != "thinking_tokens" for message in normalized.messages))

    def test_tool_catalog_combines_session_manifest_and_observed_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(
                Path(temp) / "run",
                extra_tool_names=("mcp__future_tools__generate_image",),
            )

            normalized = MohV1Adapter().load(run)

        catalog = {item["name"]: item for item in normalized.tool_catalog}
        self.assertEqual(catalog["mcp__future_tools__generate_image"]["call_count"], 1)
        self.assertTrue(catalog["mcp__future_tools__generate_image"]["observed"])
        self.assertFalse(
            catalog["mcp__future_tools__generate_image"]["available_in_session"]
        )
        self.assertFalse(catalog["mcp__future_tools__generate_image"]["declared"])
        self.assertEqual(catalog["Task"]["call_count"], 0)
        self.assertTrue(catalog["Task"]["available_in_session"])
        self.assertFalse(catalog["Task"]["declared"])
        self.assertEqual(catalog["WebSearch"]["call_count"], 0)
        self.assertFalse(catalog["WebSearch"]["observed"])
        self.assertTrue(catalog["WebSearch"]["available_in_session"])
        self.assertTrue(catalog["WebSearch"]["declared"])
        self.assertEqual(catalog["WebSearch"]["raw_names"], ["web_search", "WebSearch"])
        self.assertEqual(catalog["Slides Workbench"]["call_count"], 1)
        self.assertTrue(catalog["Slides Workbench"]["available_in_session"])
        self.assertTrue(catalog["Slides Workbench"]["declared"])
        self.assertEqual(normalized.native_tool_surfaces, ["default"])

    def test_distinct_observed_tool_names_are_not_collapsed_as_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(
                Path(temp) / "run",
                extra_tool_names=("future-tool", "future_tool"),
            )

            normalized = MohV1Adapter().load(run)

        catalog = {item["name"]: item for item in normalized.tool_catalog}
        self.assertEqual(catalog["future-tool"]["call_count"], 1)
        self.assertEqual(catalog["future_tool"]["call_count"], 1)

    def test_load_without_manifest_keeps_observed_tools_browsable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            (run / "resolved_run_manifest.json").unlink()

            normalized = MohV1Adapter().load(run)

        self.assertEqual(
            [item["name"] for item in normalized.tool_catalog],
            ["Bash", "Slides Workbench", "Task", "WebSearch"],
        )
        self.assertTrue(
            all(item["available_in_session"] for item in normalized.tool_catalog)
        )
        self.assertTrue(all(not item["declared"] for item in normalized.tool_catalog))
        self.assertEqual(normalized.native_tool_surfaces, [])


if __name__ == "__main__":
    unittest.main()
