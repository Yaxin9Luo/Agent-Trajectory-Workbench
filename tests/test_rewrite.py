from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import Store
from tests.fixtures import write_jsonl


class NoJev:
    def available(self) -> bool:
        return False


RUN_DIR = "/workspace/output/moh-0123456789abcdef0123456789abcdef/runs/run-fedcba9876543210fedcba9876543210/attempts/001/workspace"


def sample() -> dict:
    return {
        "id": "t-1_k3_attempt_01",
        "messages": [
            {"role": "user", "content": "Make the chart"},
            {"role": "assistant", "content": "Reading the data.", "tool_calls": [
                {"id": "r1", "type": "function", "function": {"name": "Read", "arguments": {"file_path": RUN_DIR + "/data.csv"}}}]},
            {"role": "tool", "tool_call_id": "r1", "content": "a,b\n1,2"},
            {"role": "assistant", "content": "Asking chart_review to check it.", "tool_calls": [
                {"id": "w1", "type": "function", "function": {"name": "mcp__chart_review__chart_review", "arguments": {"file": "chart.html"}}}]},
            {"role": "tool", "tool_call_id": "w1", "content": "written"},
            {"role": "assistant", "content": "Done: chart.html draws the data."},
        ],
    }


class CorrectionTest(unittest.TestCase):
    def test_review_correction_becomes_sft_and_dpo_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            write_jsonl(base / "dialog.jsonl", [sample()])
            service = WorkbenchService(Store(base / "index.db"), analyzer=NoJev(), reviewer="t")
            service.import_path(str(base / "dialog.jsonl"), "S")
            trajectory_id = service.list_trajectories()["items"][0]["id"]
            service.save_review(trajectory_id, {"turning_step": 3, "correction": "Writing the chart, then I will open it to check it renders.", "labels": ["overclaim"]})
            sft = [json.loads(line) for line in service.export_corrections("S", "sft").splitlines()]
            dpo = [json.loads(line) for line in service.export_corrections("S", "dpo").splitlines()]
            with self.assertRaises(ValueError):
                service.export_corrections("S", "ppo")

        [sample_sft] = sft
        [pair] = dpo
        self.assertEqual([m["role"] for m in sample_sft["messages"]], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(sample_sft["messages"][-1]["content"], "Writing the chart, then I will open it to check it renders.")
        self.assertEqual(pair["prompt"], sample_sft["messages"][:-1])
        self.assertIn("chart_review", pair["rejected"][0]["content"])
        self.assertEqual(pair["rejected"][0]["tool_calls"][0]["function"]["name"], "mcp__chart_review__chart_review")
        self.assertEqual(pair["meta"]["labels"], ["overclaim"])


class ExcerptEscapingTest(unittest.TestCase):
    def test_transcript_html_and_headings_do_not_leak_into_the_markdown(self) -> None:
        from trajectory_workbench.adapters.common import TranscriptBuilder, finish_run, make_outcome
        from trajectory_workbench.excerpt import markdown_excerpt

        builder = TranscriptBuilder()
        builder.add_message("user", text="Build <b>it</b>")
        builder.add_message("assistant", text="<script>alert(1)</script>\n# not a heading\n```")
        run = finish_run(builder, adapter_id="t", source_path="/tmp/x", run_id="r", title=None, outcome=make_outcome())
        text = markdown_excerpt(run, {"id": "x", "title": "T"}, [2], [], None)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertIn("\\# not a heading", text)
        self.assertIn("\\```", text)
        self.assertNotIn("<script>", text)


if __name__ == "__main__":
    unittest.main()
