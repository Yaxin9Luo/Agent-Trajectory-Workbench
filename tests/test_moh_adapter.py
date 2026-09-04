from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.adapters.moh_v1 import MohV1Adapter
from tests.fixtures import make_moh_run


class MohV1AdapterTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

