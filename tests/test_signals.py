from __future__ import annotations

import unittest

from trajectory_workbench.adapters.common import TranscriptBuilder, finish_run, make_outcome
from trajectory_workbench import signals


def build(steps, *, outcome=None, meta=None):
    """steps: list of (text, thinking, [(name, input, result_text, is_error)])."""
    builder = TranscriptBuilder()
    builder.add_message("user", text="Do the task")
    for index, (text, thinking, calls) in enumerate(steps):
        message = builder.add_message("assistant", text=text, thinking=thinking)
        for call_index, (name, tool_input, result, is_error) in enumerate(calls):
            call_id = f"c{index}-{call_index}"
            builder.add_tool_call(message, call_id=call_id, name=name, tool_input=tool_input)
            builder.set_result(call_id, text=result, is_error=is_error)
    return finish_run(builder, adapter_id="test", source_path="/tmp/x", run_id="r", title=None,
                      outcome=outcome or make_outcome(), meta=meta or {})


def flags(run):
    return {flag["key"]: flag for flag in signals.compute(run)["flags"]}


class SignalTest(unittest.TestCase):
    def test_loop_and_blind_retry(self) -> None:
        same = ("Bash", {"command": "npm test"}, "Error: boom", True)
        run = build([("", "", [same]), ("", "", [same]), ("", "", [same])])
        found = flags(run)
        self.assertEqual(found["loop"]["steps"], [2, 3, 4])
        self.assertIn("blind_retry", found)

    def test_changed_retry_is_not_blind(self) -> None:
        run = build([
            ("", "", [("Bash", {"command": "npm test"}, "Error", True)]),
            ("", "", [("Bash", {"command": "npm test -- --verbose"}, "ok", False)]),
        ])
        self.assertNotIn("blind_retry", flags(run))

    def test_verification_after_last_change(self) -> None:
        unverified = build([("", "", [("Edit", {"file_path": "a.py"}, "ok", False)]), ("Done", "", [])])
        verified = build([
            ("", "", [("Edit", {"file_path": "a.py"}, "ok", False)]),
            ("", "", [("Bash", {"command": "python -m pytest -q"}, "1 passed", False)]),
        ])
        self.assertIn("no_verify", flags(unverified))
        values = signals.compute(verified)["values"]
        self.assertTrue(values["verified_after_last_change"])
        self.assertEqual(values["verification"]["step"], 3)
        # Looking at an unrelated file is not a check of the change.
        unrelated = build([
            ("", "", [("Edit", {"file_path": "a.py"}, "ok", False)]),
            ("", "", [("Bash", {"command": "cat README.md"}, "text", False)]),
        ])
        self.assertIn("no_verify", flags(unrelated))

    def test_shell_redirects_count_as_writes_but_comparisons_do_not(self) -> None:
        write = {"name": "Bash", "input": {"command": "cat > out/report.html <<'EOF'\nhi\nEOF"}}
        compare = {"name": "Bash", "input": {"command": "python -c 'print(1 > 0)' && node -e 'x => x'"}}
        self.assertTrue(signals.is_mutation(write))
        self.assertFalse(signals.is_mutation(compare))

    def test_test_edit_only_for_existing_files_and_graded_runs(self) -> None:
        created = build([("", "", [("Write", {"file_path": "tests/test_new.py"}, "ok", False)])], outcome=make_outcome("pass"))
        self.assertNotIn("test_edit", flags(created))
        edited_graded = build([("", "", [("Edit", {"file_path": "tests/test_api.py"}, "ok", False)])], outcome=make_outcome("pass"))
        self.assertEqual(flags(edited_graded)["test_edit"]["severity"], "bad")
        edited_dev = build([("", "", [("Edit", {"file_path": "tests/test_api.py"}, "ok", False)])])
        self.assertEqual(flags(edited_dev)["test_edit"]["severity"], "info")
        patch = "*** Begin Patch\\n*** Update File: /repo/tests/test_cli.py\\n@@\\n-x\\n*** End Patch"
        codex = build([("", "", [("exec", {"input": f'tools.apply_patch("{patch}")'}, "ok", False)])], outcome=make_outcome("fail"))
        self.assertIn("/repo/tests/test_cli.py", flags(codex)["test_edit"]["detail"])

    def test_harness_reference_needs_specific_patterns(self) -> None:
        task_ref = build([("The instruction says tap zones on mobile.", "", [])])
        self.assertNotIn("harness_ref", flags(task_ref))
        self.assertEqual(signals.compute(task_ref)["values"]["harness_refs"], {"instruction_ref": 1})
        budget = build([("", "I must finish before the hard limit; the soft target is one hour.", [])])
        self.assertIn("提到时间预算", flags(budget)["harness_ref"]["detail"])

    def test_infra_errors_and_language_mix(self) -> None:
        infra = build([("", "", [("WebFetch", {"url": "x"}, "curl: (7) Failed: Connection refused", True)])])
        self.assertIn("infra_error", flags(infra))
        mixed = build([
            ("我先读取全部附件材料，然后整理核心数据，再开始设计页面结构和交互细节。", "", []),
            ("Now I will write the deck head section and the navigation script for all slides.", "", []),
            ("接下来写第二部分的幻灯片内容，并检查每一页的排版是否溢出，然后再继续。", "", []),
            ("Now I will verify every slide renders and the interactive chart updates correctly.", "", []),
        ])
        self.assertIn("lang_mix", flags(mixed))

    def test_layers_and_subagent_edits(self) -> None:
        run = build([("", "", [
            ("mcp__playwright__browser_navigate", {}, "ok", False),
            ("Skill", {"skill": "pdf"}, "ok", False),
            ("Agent", {"prompt": "review"}, "ok", False),
            ("Write", {"file_path": "a.html"}, "ok", False),
        ])], meta={"group_role": "subagent"})
        layers = signals.compute(run)["values"]["layers"]
        self.assertEqual((layers["mcp"], layers["skill"], layers["subagent"], layers["base"]), (1, 1, 1, 1))
        self.assertEqual(flags(run)["subagent_edit"]["severity"], "info")


class HarnessArgumentTest(unittest.TestCase):
    def test_harness_run_directory_in_tool_arguments_is_residue(self) -> None:
        workdir = "/workspace/output/moh-0123456789abcdef0123456789abcdef/runs/run-fedcba9876543210fedcba9876543210/attempts/001/workspace"
        run = build([("Checking the file.", "", [("Read", {"file_path": workdir + "/index.html"}, "ok", False)]),
                     ("", "", [("Bash", {"command": "ls src"}, "a.py", False)])])
        found = flags(run)
        self.assertEqual(found["harness_ref"]["steps"], [2])
        self.assertIn("参数含 harness 运行目录", found["harness_ref"]["detail"])
        self.assertEqual(signals.compute(run)["values"]["harness_refs"], {"harness_path_in_args": 1})


if __name__ == "__main__":
    unittest.main()
