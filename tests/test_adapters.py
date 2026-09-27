from __future__ import annotations

import json

import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.adapters import (
    AtifAdapter,
    ClaudeCodeAdapter,
    CodexAdapter,
    OpenAIChatAdapter,
    TraceLabTrialAdapter,
    discover,
)
from trajectory_workbench.adapters.common import task_key
from tests.fixtures import (
    atif_document,
    make_claude_session,
    make_codex_rollout,
    make_harbor_trial,
    make_moh_run,
    make_sft_export,
    make_tracelab_trial,
    PNG_BASE64,
    write_json,
)


class ClaudeCodeAdapterTest(unittest.TestCase):
    def test_session_merges_split_blocks_and_pairs_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = make_claude_session(Path(temp) / "session.jsonl")
            adapter = ClaudeCodeAdapter()
            self.assertTrue(adapter.detect(path))
            run = adapter.load(path, None)

        roles = [m["role"] for m in run.messages]
        # The reminder-only prefix is stripped from the task; the hook attachment and the
        # slash-command echo are harness events, not user turns.
        self.assertEqual(run.task["instruction"], "Fix the failing parser test in src/parse.py")
        self.assertEqual(roles.count("user"), 1)
        hook = next(m for m in run.messages if m.get("layer") == "hook")
        self.assertIn("lint-hook", hook["text"])
        self.assertEqual(run.messages[-1]["role"], "system")
        # Thinking and tool_use rows that share message.id are one step.
        first = next(m for m in run.messages if m["tools"])
        self.assertEqual(first["thinking"], "Run the tests first.")
        self.assertEqual([t["name"] for t in run.tools], ["Bash", "Bash", "Edit"])
        self.assertTrue(run.tools[0]["result"]["is_error"])
        self.assertEqual(run.tools[1]["result"]["images"][0]["media_type"], "image/png")
        self.assertEqual(run.metrics["total_cost_usd"], 0.5)
        self.assertEqual(run.meta["model"], "claude-test")
        self.assertEqual(run.metrics["max_offset_ms"], 40_000)
        self.assertEqual(run.outcome["status"], "unknown")

    def test_moh_stream_json_is_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = make_moh_run(Path(temp) / "run")
            trace = run_dir / "attempts/001/records/trajectory.jsonl"
            run = ClaudeCodeAdapter().load(trace, None)
        self.assertEqual(run.meta["model"], "kimi-k3")
        self.assertEqual(run.messages[-1]["role"], "result")


class CodexAdapterTest(unittest.TestCase):
    def test_rollout_pairs_calls_and_marks_injected_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = make_codex_rollout(Path(temp) / "rollout-1.jsonl")
            adapter = CodexAdapter()
            self.assertTrue(adapter.detect(path))
            self.assertFalse(ClaudeCodeAdapter().detect(path))
            run = adapter.load(path, None)

        self.assertEqual(run.task["instruction"], "Add a --dry-run flag to the CLI")
        injected = [m for m in run.messages if m["role"] == "system"]
        self.assertEqual(injected[0].get("layer"), "instruction")
        self.assertEqual([t["name"] for t in run.tools], ["shell", "apply_patch"])
        self.assertEqual(run.tools[0]["input"], {"command": ["cat", "cli.py"]})
        self.assertEqual(run.tools[0]["result"]["text"], "def main(): ...")
        self.assertFalse(run.tools[0]["result"]["is_error"])
        self.assertTrue(run.tools[1]["result"]["is_error"])
        # A call issued after a tool output starts a new step.
        self.assertLess(run.tools[0]["step"], run.tools[1]["step"])
        tool_step = next(m for m in run.messages if m["tools"])
        self.assertEqual(tool_step["thinking"], "Look at cli.py first")
        self.assertEqual(run.meta["model"], "gpt-test")
        self.assertEqual(run.metrics["total_tokens"], 4321)


class ChatSampleAdapterTest(unittest.TestCase):
    def test_multi_sample_file_groups_segments_and_joins_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            path = make_sft_export(base / "sft", results_root=base / "trials")
            adapter = OpenAIChatAdapter()
            self.assertTrue(adapter.detect(path))
            runs = list(adapter.iter_runs(path))
            again = adapter.load(path, runs[2][0])

        ids = [run.run_id for _, run in runs]
        self.assertEqual(len(ids), 4)
        main, segment, compacted, sub = (run for _, run in runs)
        self.assertEqual((main.meta["group_role"], segment.meta["group_role"], sub.meta["group_role"]), ("main", "segment", "subagent"))
        self.assertEqual(segment.meta["group_key"], compacted.meta["group_key"])
        self.assertEqual(sub.meta["group_key"], segment.meta["group_key"])
        self.assertTrue(compacted.meta["post_compaction"])
        # The continuation summary is a harness event, not the task.
        self.assertIsNone(compacted.task["instruction"])
        self.assertEqual(main.task["key"], "id:t-001")
        # Screenshots delivered as "Image returned by tool X" attach to that tool.
        self.assertEqual(main.tools[0]["result"]["images"], [{"path": "/data/images/ab.png"}])
        self.assertFalse(any(m["images"] for m in main.messages))
        # Verdicts come from the trial each row was exported from.
        self.assertEqual(main.outcome["status"], "pass")
        self.assertEqual(sub.outcome["status"], "fail")
        self.assertEqual(again.run_id, compacted.run_id)

    def test_manifest_join_refuses_rows_from_another_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            path = make_sft_export(base / "sft", results_root=base / "trials")
            manifest = base / "sft" / "manifest.json"
            # Claim the first trial holds two rows: row 2 is task t-002 and must not
            # inherit t-001's verdict.
            write_json(manifest, {"input_files": [
                {"path": str(base / "trials/t-001_trial/dialog.jsonl"), "records": 2},
                {"path": str(base / "trials/t-002_trial/dialog.jsonl"), "records": 2},
            ]})
            runs = [run for _, run in OpenAIChatAdapter().iter_runs(path)]
        self.assertEqual(runs[1].outcome["status"], "unknown")


class TraceLabAndAtifTest(unittest.TestCase):
    def test_crashed_trial_is_an_error_without_a_score(self) -> None:
        # The MoH classification lives only in the exception message; a crash is not a 0.
        cases = {
            "agent_execution_failed": ("error", None, "agent"),
            "agent_timeout": ("fail", False, "budget"),
            "artifact_invalid": ("fail", False, "agent"),
        }
        for classification, (status, infra, kind) in cases.items():
            with self.subTest(classification), tempfile.TemporaryDirectory() as temp:
                trial = make_tracelab_trial(Path(temp) / "t", resolved=False, classification=classification)
                [(_, run)] = list(TraceLabTrialAdapter().iter_runs(trial))
                self.assertEqual(run.outcome["status"], status)
                self.assertEqual(run.outcome["reason"], classification)
                self.assertIsNone(run.outcome["score"])
                self.assertIs(run.outcome["infra_failure"], infra)
                self.assertEqual(run.outcome["detail"]["failure_kind"], kind)
                self.assertFalse(run.outcome["detail"]["reward_valid"])

    def test_scored_zero_stays_a_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            trial = make_tracelab_trial(Path(temp) / "t", resolved=True)
            write_json(trial / "results.json", {"results": [{"task_id": "x-9", "is_resolved": False, "average_score": 0.0,
                                                             "failure_mode": "reward_zero", "extra_info": {"exception": None}}]})
            [(_, run)] = list(TraceLabTrialAdapter().iter_runs(trial))
        self.assertEqual((run.outcome["status"], run.outcome["score"], run.outcome["reason"]), ("fail", 0.0, "reward_zero"))
        self.assertTrue(run.outcome["detail"]["reward_valid"])

    def test_tracelab_trial_carries_grader_and_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            trial = make_tracelab_trial(Path(temp) / "x-9_trial", resolved=False)
            adapter = TraceLabTrialAdapter()
            self.assertTrue(adapter.detect(trial))
            [(locator, run)] = list(adapter.iter_runs(trial))
        self.assertEqual(run.outcome["status"], "fail")
        self.assertEqual(run.outcome["reason"], "evaluation_failed")
        self.assertEqual(run.metrics["wall_ms"], 600_000)
        self.assertEqual(run.metrics["total_tokens"], 1050)
        self.assertEqual(run.meta["harness"], "moh-slides")
        self.assertEqual(locator["offset"], 0)

    def test_atif_file_and_harbor_trial(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            file = base / "trajectory.json"
            write_json(file, atif_document())
            adapter = AtifAdapter()
            self.assertTrue(adapter.detect(file))
            runs = list(adapter.iter_runs(file))
            trial = make_harbor_trial(base / "trial", 1.0)
            self.assertTrue(adapter.detect(trial))
            [(_, graded), (sub_locator, sub)] = list(adapter.iter_runs(trial))
            reloaded = adapter.load(trial, sub_locator)

        main = runs[0][1]
        self.assertEqual(len(runs), 2)
        self.assertEqual(main.tools[0]["name"], "Bash")
        self.assertEqual(main.tools[0]["input"], {"command": "echo hi > hello.txt"})
        self.assertEqual(main.metrics["total_tokens"], 320)
        self.assertEqual(main.outcome["status"], "unknown")
        self.assertEqual(graded.outcome["status"], "pass")
        self.assertEqual(graded.task["key"], "id:hello-task")
        self.assertEqual(sub.meta["group_role"], "subagent")
        self.assertEqual(reloaded.run_id, "sub-1")


    def test_harbor_zero_after_a_crash_is_not_a_score(self) -> None:
        cases = {
            "NonZeroAgentExitCodeError": ("error", None),
            "ApiRateLimitError": ("error", True),
            "VerifierTimeoutError": ("error", True),
            "AgentTimeoutError": ("fail", False),
        }
        for exception_type, (status, infra) in cases.items():
            with self.subTest(exception_type), tempfile.TemporaryDirectory() as temp:
                trial = make_harbor_trial(Path(temp) / "trial", 0.0, exception_type)
                graded = next(run for _, run in AtifAdapter().iter_runs(trial))
                self.assertEqual(graded.outcome["status"], status)
                self.assertIsNone(graded.outcome["score"])
                self.assertIs(graded.outcome["infra_failure"], infra)
                self.assertFalse(graded.outcome["detail"]["reward_valid"])


class ReviewRegressionTest(unittest.TestCase):
    def test_json_array_tool_output_is_not_mistaken_for_content_parts(self) -> None:
        from trajectory_workbench.adapters.common import content_text, infer_tool_error

        events = '[{"type": "PushEvent", "repo": "x"}, {"type": "WatchEvent"}]'
        self.assertEqual(content_text(events), events)
        # A JSON file that merely contains "isError": true is not a failed MCP call…
        self.assertFalse(infer_tool_error('{"cases": [{"isError": true}]}'))
        # …but a result object right after a wrapper line is.
        self.assertTrue(infer_tool_error('Script completed\nOutput:\n{"content":[{"type":"text","text":"x"}],"isError":true}'))

    def test_harbor_exception_without_type_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            trial = make_harbor_trial(Path(temp) / "trial", 0.0)
            result = json.loads((trial / "result.json").read_text())
            result["exception_info"] = {"exception_type": None, "exception_message": "agent crashed"}
            write_json(trial / "result.json", result)
            graded = next(run for _, run in AtifAdapter().iter_runs(trial))
        self.assertEqual(graded.outcome["status"], "error")
        self.assertEqual(graded.outcome["reason"], "None: agent crashed")

    def test_tracelab_zero_next_to_a_crash_and_unknown_reason_stays_short(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            trial = make_tracelab_trial(Path(temp) / "t", resolved=False, classification="agent_execution_failed")
            rows = json.loads((trial / "results.json").read_text())
            rows["results"][0]["average_score"] = 0.0
            write_json(trial / "results.json", rows)
            [(_, crashed)] = list(TraceLabTrialAdapter().iter_runs(trial))
            rows["results"][0]["extra_info"]["exception"]["exception_message"] = "Something odd happened " * 30
            write_json(trial / "results.json", rows)
            [(_, odd)] = list(TraceLabTrialAdapter().iter_runs(trial))
        self.assertEqual((crashed.outcome["status"], crashed.outcome["score"]), ("error", None))
        self.assertEqual(odd.outcome["reason"], "ValueError")

    def test_moh_campaign_and_runtime_classifications(self) -> None:
        from trajectory_workbench.adapters.common import classify_failure

        self.assertEqual(classify_failure("runtime_setup_failed")["kind"], "infra")
        self.assertEqual(classify_failure("output_limit")["status"], "fail")
        self.assertEqual(classify_failure("evaluator_failure")["kind"], "grader")
        self.assertEqual(classify_failure("candidate_failure")["status"], "fail")
        unknown = classify_failure("MoH job failed: exit=12, classification='brand_new_failure'", "ValueError")
        self.assertEqual((unknown["name"], unknown["status"]), ("brand_new_failure", "error"))


class ToolErrorInferenceTest(unittest.TestCase):
    def test_unflagged_results_are_inferred_and_explicit_flags_win(self) -> None:
        sample = {
            "id": "e-1",
            "messages": [
                {"role": "user", "content": "Fix it"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"c{i}", "type": "function", "function": {"name": "Bash", "arguments": {"command": "x"}}} for i in range(5)
                ]},
                {"role": "tool", "tool_call_id": "c0", "content": "Exit code 1\nls: cannot access"},
                {"role": "tool", "tool_call_id": "c1", "content": "<tool_use_error>File does not exist.</tool_use_error>"},
                {"role": "tool", "tool_call_id": "c2", "content": "Exit code 0\nok"},
                {"role": "tool", "tool_call_id": "c3", "content": "Exit code 2\nflaky", "is_error": False},
                {"role": "tool", "tool_call_id": "c4", "content": "Script completed\n{\"content\":[],\"isError\":true}"},
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "one.json"
            write_json(path, sample)
            [(_, run)] = list(OpenAIChatAdapter().iter_runs(path))
        results = [tool["result"] for tool in run.tools]
        self.assertEqual([r["is_error"] for r in results], [True, True, False, False, True])
        self.assertEqual([bool(r.get("error_inferred")) for r in results], [True, True, False, False, True])
        self.assertEqual(run.metrics["tool_errors"], 3)


class ContextTrackTest(unittest.TestCase):
    def test_measured_sizes_calibrate_estimates_and_compaction_resets(self) -> None:
        from trajectory_workbench.adapters.common import context_track

        messages = [
            {"role": "user", "text": "x" * 400},                              # ~100 tokens
            {"role": "assistant", "text": "y" * 400, "context_tokens": 5100},  # measured
            {"role": "user", "text": "z" * 40},
            {"role": "system", "text": "summary", "compaction": {"trigger": "auto"}},
            {"role": "assistant", "text": "after"},
        ]
        summary = context_track(messages)
        self.assertEqual(messages[0]["context_estimate"], 0)
        # The 5000-token gap (system prompt, tools) carries over to later estimates.
        self.assertEqual(messages[2]["context_estimate"], 5100 + 100)
        self.assertEqual(messages[3]["context_estimate"], 0)
        self.assertEqual(messages[4]["context_estimate"], 2)
        self.assertEqual(summary, {"context_peak": 5200, "context_measured_steps": 1, "compactions": 1})

    def test_claude_usage_and_compact_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = make_claude_session(Path(temp) / "s.jsonl")
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            rows.append({"type": "system", "subtype": "compact_boundary", "sessionId": "sess-1", "uuid": "cb",
                         "timestamp": "2026-09-01T10:01:00Z", "content": "Conversation compacted",
                         "compactMetadata": {"trigger": "auto", "preTokens": 170000, "postTokens": 12000}})
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            run = ClaudeCodeAdapter().load(path, None)
        first = next(m for m in run.messages if m["role"] in {"assistant", "tool"})
        self.assertEqual(first["context_tokens"], 100)
        boundary = run.messages[-1]
        self.assertEqual(boundary["compaction"], {"trigger": "auto", "pre_tokens": 170000, "post_tokens": 12000})
        self.assertIn("170,000 → 12,000", boundary["text"])
        self.assertEqual(run.metrics["compactions"], 1)


class JsonStringContentTest(unittest.TestCase):
    def test_images_inside_a_json_encoded_tool_content_list_are_kept(self) -> None:
        parts = [{"type": "text", "text": "screenshot taken"},
                 {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_BASE64}}]
        sample = {"id": "img-1", "messages": [
            {"role": "user", "content": "Render it"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "s1", "type": "function", "function": {"name": "Screenshot", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "s1", "content": json.dumps(parts, indent=2)},
        ]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "one.json"
            write_json(path, sample)
            [(_, run)] = list(OpenAIChatAdapter().iter_runs(path))
        result = run.tools[0]["result"]
        self.assertEqual(result["text"], "screenshot taken")
        self.assertEqual(result["images"], [{"media_type": "image/png", "data": PNG_BASE64}])


class DiscoveryTest(unittest.TestCase):
    def test_walks_mixed_directory_without_double_counting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            make_moh_run(base / "moh" / "run-1")
            make_tracelab_trial(base / "tracelab" / "x-9_trial", resolved=True)
            make_claude_session(base / "claude" / "proj" / "s.jsonl")
            make_codex_rollout(base / "codex" / "2026" / "rollout-a.jsonl")
            make_harbor_trial(base / "harbor" / "job" / "trial-1", 0.0)
            (base / "notes.json").write_text("{}", encoding="utf-8")
            found = sorted((adapter.adapter_id, path.relative_to(base).as_posix()) for adapter, path in discover(base))

        self.assertEqual(
            found,
            [
                ("atif", "harbor/job/trial-1"),
                ("claude-code", "claude/proj/s.jsonl"),
                ("codex", "codex/2026/rollout-a.jsonl"),
                ("moh-v1", "moh/run-1"),
                ("tracelab", "tracelab/x-9_trial"),
            ],
        )

    def test_task_key_ignores_trailing_context(self) -> None:
        head = "Build a deck. " * 200
        self.assertEqual(task_key(None, head + "attachment A"), task_key(None, head + "attachment B"))
        self.assertEqual(task_key("abc", "anything"), "id:abc")


if __name__ == "__main__":
    unittest.main()
