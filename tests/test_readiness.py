from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trajectory_workbench import readiness
from trajectory_workbench.adapters.common import TranscriptBuilder, finish_run, make_outcome


def run_with(build):
    builder = TranscriptBuilder()
    build(builder)
    return finish_run(builder, adapter_id="t", source_path="/tmp/x", run_id="r", title=None, outcome=make_outcome())


def keys(result):
    return {issue["key"]: issue for issue in result["issues"]}


class ReadinessTest(unittest.TestCase):
    def test_clean_sample_is_ready_and_counts_trained_share(self) -> None:
        def build(b):
            b.add_message("system", text="You are a coding agent. " * 20)
            b.add_message("user", text="Fix the bug in app.py")
            call = b.add_message("assistant", text="Reading the file.")
            b.add_tool_call(call, call_id="c1", name="Read", tool_input={"file_path": "app.py"})
            b.set_result("c1", text="print('x')")
            b.add_message("assistant", text="Fixed the bug by changing the print call.")

        result = readiness.check(run_with(build))
        self.assertTrue(result["ready"])
        self.assertEqual(result["issues"], [])
        self.assertGreater(result["trained_share"], 0)
        self.assertLess(result["trained_share"], 1)

    def test_blocking_problems(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            present = Path(temp) / "shot.png"
            present.write_bytes(b"x")

            def build(b):
                b.add_message("user", text="Do it")
                first = b.add_message("assistant", text="")
                b.add_tool_call(first, call_id="a", name="Bash", tool_input='{"command": "ls"')  # broken JSON
                b.set_result("a", text="ok", images=[{"path": str(present)}, {"path": temp + "/gone.png"}])
                second = b.add_message("assistant", text="Checking")
                b.add_tool_call(second, call_id="b", name="Bash", tool_input={"command": "pwd"})  # never answered
                b.set_result("", text="stray result")
                last = b.add_message("assistant", text="")
                b.add_tool_call(last, call_id="c", name="Bash", tool_input={"command": "make"})

            result = readiness.check(run_with(build))
        found = keys(result)
        self.assertFalse(result["ready"])
        self.assertEqual(found["truncated_end"]["steps"], [4])
        # The final call's missing result is part of the truncated ending, not counted twice.
        self.assertEqual(found["unpaired_calls"]["steps"], [3])
        self.assertIn("orphan_results", found)
        self.assertEqual(found["bad_arguments"]["steps"], [2])
        self.assertEqual(found["missing_images"]["steps"], [2])

    def test_too_long_and_harness_use_in_trained_turns(self) -> None:
        def build(b):
            b.add_message("system", text="You are Claude Code. Use mcp__review__inspect_work to have your work reviewed.")
            b.add_message("user", text="Build it")
            step = b.add_message("assistant", text="Asking the reviewer. " + "x" * 400)
            b.add_tool_call(step, call_id="c1", name="mcp__review__inspect_work", tool_input={})
            b.set_result("c1", text="looks fine")
            b.add_message("assistant", text="Built it.")

        with mock.patch.object(readiness, "MAX_SEQ_LEN", 50):
            result = readiness.check(run_with(build))
        found = keys(result)
        self.assertIn("too_long", found)
        # Only the model's own turn counts; the system prompt naming the tool is context.
        self.assertEqual(found["residue_in_trained"]["steps"], [3])
        self.assertEqual(found["residue_in_trained"]["severity"], "warn")
        self.assertEqual(result["residue"]["trained"], {"calls": [3]})


if __name__ == "__main__":
    unittest.main()
