from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trajectory_workbench import service as service_module
from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import Store
from tests.fixtures import make_claude_session, make_sft_export


class NoJev:
    def available(self) -> bool:
        return False


class ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.service = WorkbenchService(Store(self.base / "index.db"), analyzer=NoJev(), reviewer="t")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_raw_export_copies_original_lines_and_expands_episodes(self) -> None:
        source = make_sft_export(self.base / "sft")
        self.service.import_path(str(source), "SFT")
        head = self.service.list_trajectories(episodes=True, search="volcanoes")["items"][0]
        result = self.service.export({"episodes": True, "search": "volcanoes"}, self.base / "out", "raw")
        written = (self.base / "out" / "dialog.jsonl").read_bytes().splitlines()
        original = source.read_bytes().splitlines()
        card = json.loads((self.base / "out" / "data_card.json").read_text())
        markdown = (self.base / "out" / "data_card.md").read_text()
        # The episode (two segments and a subagent) is exported whole, byte for byte.
        self.assertEqual(result["written"], 3)
        self.assertEqual(set(written), set(original[1:]))
        self.assertEqual(card["by_role"], {"segment": 2, "subagent": 1})
        self.assertEqual(card["episodes"], 1)
        self.assertTrue(head["group_key"])
        self.assertIn("# Data card", markdown)
        with self.assertRaises(ValueError):
            self.service.export({"episodes": True}, self.base / "out", "raw")  # refuses to overwrite

    def test_chat_mode_converts_any_format_and_raw_skips_what_it_cannot_copy(self) -> None:
        self.service.import_path(str(make_claude_session(self.base / "s.jsonl")), "C")
        raw = self.service.export({"collection": "C"}, self.base / "raw", "raw")
        chat = self.service.export({"collection": "C"}, self.base / "chat", "chat")
        [line] = (self.base / "chat" / "dialog.jsonl").read_text().splitlines()
        sample = json.loads(line)
        self.assertEqual(raw["written"], 0)
        self.assertEqual(list(raw["card"]["skipped"].values()), [1])
        self.assertEqual(chat["written"], 1)
        roles = [message["role"] for message in sample["messages"]]
        self.assertEqual(roles[0], "user")
        calls = [message for message in sample["messages"] if message.get("tool_calls")]
        self.assertEqual(calls[0]["tool_calls"][0]["function"]["name"], "Bash")
        results = [message for message in sample["messages"] if message["role"] == "tool"]
        self.assertTrue(results[0]["is_error"])
        self.assertEqual(results[0]["tool_call_id"], calls[0]["tool_calls"][0]["id"])

    def test_browser_exports_go_under_the_export_root_only(self) -> None:
        with mock.patch.object(service_module, "EXPORT_ROOT", self.base / "exports"):
            for bad in ("../escape", "a/b", "", "x" * 200):
                with self.assertRaises(ValueError):
                    self.service.start_export({}, bad, "raw")


if __name__ == "__main__":
    unittest.main()
