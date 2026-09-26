from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.adapters.moh_v1 import MohV1Adapter
from trajectory_workbench.registry import Registry
from trajectory_workbench.service import WorkbenchService
from tests.fixtures import make_moh_run, write_json


SYSTEM_MARKDOWN = "# Slides harness\n按任务要求交付；静态页面也可以。\n"
USER_MARKDOWN = "# 本次任务\r\n制作两页水循环说明，使用 inputs/示意图.svg。\r\n"


def add_model_prompt(run: Path) -> None:
    """Use the exact ModelPromptV1 / ModelPromptBindingV1 record shapes."""
    records = run / "attempts/001/records"
    write_json(records / "request.json", {
        "format_version": 1,
        "attempt_id": "001",
        "context": "",
        "instructions": "",
        "model_prompt": {
            "format_version": 1,
            "system_markdown": SYSTEM_MARKDOWN,
            "user_markdown": USER_MARKDOWN,
        },
        "record_paths": {
            "system_prompt": "/workspace/moh-output/runs/example/attempts/001/records/system_prompt.md",
            "stdin": "/workspace/moh-output/runs/example/attempts/001/records/stdin.txt",
        },
    })
    (records / "system_prompt.md").write_bytes(SYSTEM_MARKDOWN.encode("utf-8"))
    (records / "stdin.txt").write_bytes(USER_MARKDOWN.encode("utf-8"))
    process = json.loads((records / "process.json").read_text())
    process["stdin_sha256"] = hashlib.sha256(USER_MARKDOWN.encode("utf-8")).hexdigest()
    process["model_prompt"] = {
        "format_version": 1,
        "system_markdown_sha256": hashlib.sha256(SYSTEM_MARKDOWN.encode("utf-8")).hexdigest(),
        "user_markdown_sha256": hashlib.sha256(USER_MARKDOWN.encode("utf-8")).hexdigest(),
    }
    write_json(records / "process.json", process)


class ModelPromptTest(unittest.TestCase):
    def test_prompt_is_separate_from_native_messages_tools_and_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            native = MohV1Adapter().load(run)
            add_model_prompt(run)
            original = {str(p): p.read_bytes() for p in run.rglob("*") if p.is_file()}

            normalized = MohV1Adapter().load(run)
            prompt = normalized.summary_dict().get("model_prompt")

            self.assertIsInstance(prompt, dict)
            self.assertEqual(prompt["source"], "attempts/001/records/request.json")
            self.assertEqual(prompt["scope"], "moh_model_prompt_v1")
            self.assertFalse(prompt["native_system_complete"])
            self.assertEqual(prompt["system"]["text"], SYSTEM_MARKDOWN)
            self.assertEqual(prompt["user"]["text"], USER_MARKDOWN)
            for role, record in (("system", "system_prompt.md"), ("user", "stdin.txt")):
                self.assertEqual(prompt[role]["record_path"], "attempts/001/records/" + record)
                self.assertEqual(prompt[role]["status"], "records_match")
                self.assertIsNone(prompt[role].get("line_number"))
                self.assertIsNone(prompt[role].get("offset_ms"))
            self.assertEqual(normalized.messages, native.messages)
            self.assertEqual(normalized.tools, native.tools)
            self.assertEqual(normalized.tool_catalog, native.tool_catalog)
            self.assertEqual(normalized.timeline, native.timeline)
            self.assertEqual(normalized.metrics, native.metrics)
            self.assertEqual(original, {str(p): p.read_bytes() for p in run.rglob("*") if p.is_file()})

    def test_changed_record_or_process_hash_is_never_reported_as_matching(self) -> None:
        for change in ("system_file", "user_file", "system_binding", "user_binding", "stdin_binding"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                run = make_moh_run(Path(temp) / "run")
                add_model_prompt(run)
                records = run / "attempts/001/records"
                role = "system" if change.startswith("system") else "user"
                if change.endswith("_file"):
                    name = "system_prompt.md" if role == "system" else "stdin.txt"
                    (records / name).write_bytes(b"altered record")
                else:
                    process = json.loads((records / "process.json").read_text())
                    if change == "stdin_binding":
                        process["stdin_sha256"] = "0" * 64
                    else:
                        process["model_prompt"][role + "_markdown_sha256"] = "0" * 64
                    write_json(records / "process.json", process)

                prompt = MohV1Adapter().load(run).summary_dict().get("model_prompt")

                self.assertIsInstance(prompt, dict)
                self.assertEqual(prompt[role]["status"], "mismatch")
                self.assertEqual(prompt[role]["text"], SYSTEM_MARKDOWN if role == "system" else USER_MARKDOWN)

    def test_missing_records_or_process_binding_stay_unverified(self) -> None:
        for missing in ("system_prompt.md", "stdin.txt", "model_prompt"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temp:
                run = make_moh_run(Path(temp) / "run")
                add_model_prompt(run)
                records = run / "attempts/001/records"
                if missing == "model_prompt":
                    process = json.loads((records / "process.json").read_text())
                    del process["model_prompt"]
                    write_json(records / "process.json", process)
                else:
                    (records / missing).unlink()

                prompt = MohV1Adapter().load(run).summary_dict().get("model_prompt")

                self.assertIsInstance(prompt, dict)
                affected = ["system", "user"] if missing == "model_prompt" else ["system" if missing.endswith(".md") else "user"]
                for role in affected:
                    self.assertEqual(prompt[role]["status"], "unverified")
                if missing == "model_prompt":
                    self.assertIsNone(prompt["system"]["process_sha256"])
                    self.assertIsNone(prompt["user"]["process_sha256"])

    def test_external_record_symlink_is_not_read_for_prompt_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            add_model_prompt(run)
            outside = Path(temp) / "outside.txt"
            outside.write_text("not a run record")
            system_file = run / "attempts/001/records/system_prompt.md"
            system_file.unlink()
            system_file.symlink_to(outside)

            prompt = MohV1Adapter().load(run).summary_dict().get("model_prompt")

            self.assertIsInstance(prompt, dict)
            self.assertEqual(prompt["system"]["status"], "unverified")
            self.assertIsNone(prompt["system"]["record_sha256"])

    def test_legacy_or_unsupported_prompt_does_not_invent_system_content(self) -> None:
        for value in (None, {"format_version": 2, "system_markdown": "future", "user_markdown": "future"}, {"format_version": True, "system_markdown": "invalid", "user_markdown": "invalid"}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp:
                run = make_moh_run(Path(temp) / "run")
                request = {"context": "runtime context", "instructions": "legacy task"}
                if value is not None:
                    request["model_prompt"] = value
                write_json(run / "attempts/001/records/request.json", request)

                normalized = MohV1Adapter().load(run)

                self.assertIsNone(normalized.summary_dict().get("model_prompt"))
                self.assertEqual(normalized.messages[0]["id"], "line-1")

    def test_service_keeps_native_pagination_and_rechecks_prompt_record_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            add_model_prompt(run)
            service = WorkbenchService(Registry(Path(temp) / "registry.json"))
            run_id = service.import_run(str(run), "Prompt fixture")["id"]
            before = service.get_messages(run_id)
            summary = service.get_run(run_id)
            self.assertIsInstance(summary.get("model_prompt"), dict)
            self.assertEqual(summary["model_prompt"]["system"]["status"], "records_match")

            for role, filename in (("system", "system_prompt.md"), ("user", "stdin.txt")):
                with self.subTest(role=role):
                    (run / "attempts/001/records" / filename).write_bytes(b"changed")
                    refreshed = service.get_run(run_id)
                    self.assertEqual(refreshed["model_prompt"][role]["status"], "mismatch")
            self.assertEqual(service.get_messages(run_id), before)
            self.assertEqual(summary["metrics"]["message_count"], before["total"])

    def test_missing_process_still_fails_the_existing_complete_run_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run = make_moh_run(Path(temp) / "run")
            add_model_prompt(run)
            (run / "attempts/001/records/process.json").unlink()

            with self.assertRaisesRegex(ValueError, "Missing .*process.json"):
                MohV1Adapter().load(run)


if __name__ == "__main__":
    unittest.main()
