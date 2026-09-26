from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.adapters.moh_v1 import MohV1Adapter
from tests.fixtures import make_moh_run, write_json, write_jsonl


class MohV1AdapterTest(unittest.TestCase):
    def test_tool_result_preserves_native_images_without_top_level_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            trace_path = run / "attempts/001/records/trajectory.jsonl"
            trace = [json.loads(line) for line in trace_path.read_text().splitlines()]
            native_image = {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "cG5n"},
            }
            result_row = trace[6]
            result = result_row["message"]["content"][0]
            result["content"].append(native_image)
            result["is_error"] = True
            result_row["tool_use_result"] = [native_image, native_image]
            write_jsonl(trace_path, trace)

            normalized = MohV1Adapter().load(run)

        result = normalized.tools[1]["result"]
        self.assertEqual(result.get("images"), [{"media_type": "image/png", "data": "cG5n"}])
        self.assertTrue(result["is_error"])
        self.assertEqual(json.loads(result["text"])["status"], "completed")
        self.assertIsNone(normalized.workbench[0]["contact_sheet_relative_path"])

    def test_tool_result_ignores_invalid_or_remote_image_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            trace_path = run / "attempts/001/records/trajectory.jsonl"
            trace = [json.loads(line) for line in trace_path.read_text().splitlines()]
            trace[6]["message"]["content"][0]["content"].extend([
                {"type": "image", "source": {"type": "url", "url": "https://example.com/image.png"}},
                {"type": "image", "source": {"type": "base64", "media_type": "text/html", "data": "cG5n"}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "invalid!"}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ""}},
                {"type": "image", "source": {"type": "base64", "media_type": [], "data": "cG5n"}},
            ])
            write_jsonl(trace_path, trace)

            normalized = MohV1Adapter().load(run)

        self.assertEqual(normalized.tools[1]["result"].get("images"), [])
        self.assertEqual(normalized.tools[0]["result"].get("images"), [])

    def test_missing_contact_sheet_is_not_exposed_as_a_broken_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            sheet = run / "attempts/001/workspace/.rsi-moh-slides-workbench/contact-sheet-001.png"
            sheet.unlink()

            normalized = MohV1Adapter().load(run)

        self.assertIsNone(normalized.workbench[0]["contact_sheet_relative_path"])

    def test_contact_sheet_can_use_hash_matched_sealed_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            relative_sheet = Path(".rsi-moh-slides-workbench/contact-sheet-001.png")
            (run / "attempts/001/workspace" / relative_sheet).write_bytes(b"different image")
            sealed_sheet = (
                run / "attempts/001/records/.rsi-moh-slides-workbench-v7-s01-v4-static" / relative_sheet
            )
            sealed_sheet.parent.mkdir(parents=True)
            sealed_sheet.write_bytes(b"png")
            trace_path = run / "attempts/001/records/trajectory.jsonl"
            trace = [json.loads(line) for line in trace_path.read_text().splitlines()]
            text_block = trace[6]["message"]["content"][0]["content"][0]
            payload = json.loads(text_block["text"])
            payload["contact_sheet_sha256"] = hashlib.sha256(b"png").hexdigest()
            text_block["text"] = json.dumps(payload)
            write_jsonl(trace_path, trace)

            normalized = MohV1Adapter().load(run)
            self.assertEqual(
                normalized.workbench[0]["contact_sheet_relative_path"],
                sealed_sheet.relative_to(run).as_posix(),
            )
            sealed_sheet.write_bytes(b"changed image")
            self.assertIsNone(MohV1Adapter().load(run).workbench[0]["contact_sheet_relative_path"])

    def test_contact_sheet_does_not_expose_paths_outside_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            outside = Path(temp) / "outside.png"
            outside.write_bytes(b"png")
            linked_sheet = run / "attempts/001/workspace/linked.png"
            linked_sheet.symlink_to(outside)
            trace_path = run / "attempts/001/records/trajectory.jsonl"
            trace = [json.loads(line) for line in trace_path.read_text().splitlines()]
            text_block = trace[6]["message"]["content"][0]["content"][0]
            for supplied in (str(outside), "../../../../outside.png", "linked.png"):
                with self.subTest(path=supplied):
                    text_block["text"] = json.dumps({"contact_sheet_path": supplied})
                    write_jsonl(trace_path, trace)
                    self.assertIsNone(MohV1Adapter().load(run).workbench[0]["contact_sheet_relative_path"])

    def test_result_metadata_is_retained_even_without_a_text_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            trace_path = run / "attempts/001/records/trajectory.jsonl"
            trace = [json.loads(line) for line in trace_path.read_text().splitlines()]
            native_result = {
                "type": "result", "subtype": "success", "is_error": False,
                "modelUsage": {"kimi-k3": {"inputTokens": 42, "outputTokens": 12}},
                "stop_reason": "end_turn",
            }
            trace[-1] = native_result
            write_jsonl(trace_path, trace)

            normalized = MohV1Adapter().load(run)

        result_messages = [item for item in normalized.messages if item["role"] == "result"]
        self.assertEqual(len(result_messages), 1)
        self.assertEqual(result_messages[0].get("native_result"), native_result)

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
