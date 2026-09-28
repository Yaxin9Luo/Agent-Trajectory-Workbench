from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from typesafe_sdk import TypeSafeAPIConnectionError

from trajectory_workbench.adapters.common import TranscriptBuilder, finish_run, make_outcome
from trajectory_workbench.jev import (
    DATA_NOTE,
    JevAnalyzer,
    outcome_consistency,
    step_questions,
    step_state,
    summarize_steps,
)


def noul(value):
    return SimpleNamespace(type="noul", noul=value)


def choice(value, probabilities=None):
    probabilities = probabilities or {value: 0.9}
    return SimpleNamespace(type="choice", choice=value, confidence=max(probabilities.values()), probabilities=probabilities)


class FakeClient:
    """Answers by looking at the state it was given, like a very literal model."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[dict, dict]] = []
        self.fail_on = fail_on

    async def system_one(self, state, questions):
        self.calls.append((state, questions))
        rendered = json.dumps(state, ensure_ascii=False)
        if self.fail_on and self.fail_on in rendered:
            raise TypeSafeAPIConnectionError("boom")
        answers = {}
        for key in questions:
            if key == "phase":
                message = state["step"]["message"]
                calls = [call["tool"] for call in state["step"]["tool_calls"]]
                phase = "report" if "done" in message.lower() else "implement" if "Write" in calls else "verify" if "pytest" in rendered else "explore"
                answers[key] = choice(phase)
            elif key == "ignores_error":
                answers[key] = noul(0.9)
            elif key == "claims_done":
                answers[key] = noul(0.95 if "done" in state["step"]["message"].lower() else 0.05)
            elif key.startswith("label:"):
                answers[key] = noul(0.8 if key.endswith("no_verify") else 0.55 if key.endswith("loop") else 0.1)
            elif key in {"intervention", "attribution"}:
                answers[key] = choice("data" if key == "intervention" else "model")
            elif key == "match":
                ids = [k for k in questions[key].criteria if k != "none"]
                target = next((k for k in ids if "pytest" in state["steps"][k]), None)
                probabilities = {k: 0.0 for k in ids} | {"none": 0.02}
                if target:
                    probabilities[target] = 0.9
                answers[key] = choice(target or "none", probabilities)
            elif key == "final_claim":
                answers[key] = choice("complete")
            elif key == "work":
                again = "again" in state["step"]["reasoning"]
                answers[key] = choice("polish" if again else "required", {"polish": 0.7 if again else 0.1, "required": 0.3 if again else 0.9})
            elif key == "harness":
                uses = "deck_bench" in json.dumps(state["step"], ensure_ascii=False)
                answers[key] = choice("uses_component" if uses else "none", {"none": 0.1, "uses_component": 0.9} if uses else {"none": 0.95, "uses_component": 0.05})
            elif key == "task_ambiguity":
                answers[key] = SimpleNamespace(type="score", score=1.5, confidence=0.6, probabilities={0: 0.1, 1: 0.4, 2: 0.4, 3: 0.1})
            else:
                answers[key] = noul(0.1)
        return SimpleNamespace(model="jev-test", usage=SimpleNamespace(input_tokens=100), answers=answers)

    async def aclose(self):
        return None


def sample_run(*, final="All done.", truncated=False):
    builder = TranscriptBuilder()
    builder.add_message("user", text="Write app.py and make the tests pass. Never delete tests.")
    first = builder.add_message("assistant", text="", thinking="Write the file.")
    builder.add_tool_call(first, call_id="a", name="Write", tool_input={"file_path": "app.py"})
    builder.set_result("a", text=("x" * 5000) if truncated else "written", is_error=False)
    second = builder.add_message("assistant", text="", thinking="Run tests.")
    builder.add_tool_call(second, call_id="b", name="Bash", tool_input={"command": "pytest -q"})
    builder.set_result("b", text="FAILED test_app.py::test_x", is_error=True)
    third = builder.add_message("assistant", text="", thinking="Edit again.")
    builder.add_tool_call(third, call_id="c", name="Write", tool_input={"file_path": "app.py"})
    builder.set_result("c", text="written")
    builder.add_message("assistant", text=final)
    return finish_run(builder, adapter_id="t", source_path="/tmp/x", run_id="r", title=None, outcome=make_outcome("fail"))


class StepQuestionTest(unittest.TestCase):
    def test_questions_depend_on_available_evidence(self) -> None:
        run = sample_run()
        steps = [m for m in run.messages if m["role"] in {"assistant", "tool"}]
        first = step_state(run, steps[0], None)
        after_error = step_state(run, steps[2], steps[1])
        self.assertEqual(first["about"], DATA_NOTE)
        self.assertNotIn("previous_observation", first)
        self.assertNotIn("ignores_error", step_questions(first))
        self.assertNotIn("misreads_observation", step_questions(first))
        self.assertIn("ignores_error", step_questions(after_error))
        self.assertIn("misreads_observation", step_questions(after_error))
        self.assertIn("violates_constraint", step_questions(after_error))

    def test_truncated_or_image_evidence_skips_contradiction_question(self) -> None:
        run = sample_run(truncated=True)
        steps = [m for m in run.messages if m["role"] in {"assistant", "tool"}]
        state = step_state(run, steps[1], steps[0])
        self.assertEqual(state["previous_observation"][0]["output_note"], "Truncated here; the agent saw the full output.")
        self.assertNotIn("misreads_observation", step_questions(state))


class AnalyzerTest(unittest.TestCase):
    def test_step_scan_aggregates_in_code(self) -> None:
        client = FakeClient()
        result = JevAnalyzer(client_factory=lambda: client).analyze_steps(sample_run())
        summary = result["summary"]
        self.assertEqual(result["model"], "jev-test")
        self.assertEqual(result["input_tokens"], 400)
        self.assertEqual(len(client.calls), 4)
        self.assertEqual(summary["flagged"]["ignores_error"], [4])
        self.assertEqual(summary["turning_candidates"][0], {"step": 4, "reasons": ["忽略报错"]})
        # Implemented at step 4, claimed done at step 5 with no verify phase in between.
        self.assertEqual(summary["claims_done_unverified"], [5])
        # Only file-changing steps are asked why they change files (Write at 2 and 4).
        asked = [state["step"]["reasoning"] for state, questions in client.calls if "work" in questions]
        self.assertEqual(asked, ["Write the file.", "Edit again."])
        self.assertEqual(summary["work_counts"], {"required": 1, "polish": 1})
        self.assertEqual(summary["flagged"]["polish"], [4])

    def test_harness_reliance_is_asked_only_when_the_harness_added_something(self) -> None:
        client = FakeClient()
        JevAnalyzer(client_factory=lambda: client).analyze_steps(sample_run())
        self.assertFalse(any("harness" in questions for _, questions in client.calls))
        self.assertFalse(any("harness_added" in state for state, _ in client.calls))

        builder = TranscriptBuilder()
        builder.add_message("system", text="You are Claude Code.\n\n# Environment\nLinux\n\n# Deck authoring\nWrite `artifact.html`.")
        builder.add_message("user", text="Make a deck.")
        first = builder.add_message("assistant", text="", thinking="Check the layout with deck_bench.")
        builder.add_tool_call(first, call_id="a", name="mcp__deck_bench__deck_bench", tool_input={"action": "inspect_deck"})
        builder.set_result("a", text="ok")
        second = builder.add_message("assistant", text="", thinking="Fix the title size.")
        builder.add_tool_call(second, call_id="b", name="Edit", tool_input={"file_path": "deck.html"})
        builder.set_result("b", text="ok")
        builder.add_message("assistant", text="The deck is done.")
        run = finish_run(builder, adapter_id="t", source_path="/tmp/x", run_id="r", title=None, outcome=make_outcome(),
                         available_tools=["Bash", "Read", "Edit", "mcp__deck_bench__deck_bench"])
        client = FakeClient()
        result = JevAnalyzer(client_factory=lambda: client).analyze_steps(run)
        state = client.calls[0][0]
        self.assertEqual(state["harness_added"]["stock_agent"], "Claude Code")
        self.assertEqual(state["harness_added"]["mcp_servers"], [{"server": "deck_bench", "tools": ["deck_bench"]}])
        self.assertEqual(state["harness_added"]["added_instruction_sections"][0]["section"], "Deck authoring")
        self.assertTrue(all("harness" in questions for _, questions in client.calls))
        summary = result["summary"]
        self.assertEqual(summary["flagged"]["harness_reliance"], [3])
        self.assertEqual(summary["harness_counts"], {"uses_component": 1})
        self.assertEqual(result["steps"][0]["harness"], "uses_component")
        self.assertEqual(result["steps"][1]["p"]["harness_reliance"], 0.05)

    def test_service_errors_are_recorded_per_step(self) -> None:
        client = FakeClient(fail_on="Run tests.")
        result = JevAnalyzer(client_factory=lambda: client).analyze_steps(sample_run())
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["summary"]["analyzed_steps"], 3)

    def test_task_check_and_consistency(self) -> None:
        run = sample_run()
        task = JevAnalyzer(client_factory=FakeClient).analyze_task(run)
        self.assertEqual(task["final_claim"]["choice"], "complete")
        self.assertAlmostEqual(task["task_ambiguity"]["score"], 0.5)
        self.assertEqual(outcome_consistency(task["final_claim"], run.outcome), "overclaim")
        self.assertIsNone(outcome_consistency({"choice": "complete"}, {"status": "pass"}))
        self.assertEqual(outcome_consistency({"choice": "failed"}, {"status": "pass"}), "underclaim")

    def test_review_suggestions_use_the_stricter_threshold(self) -> None:
        result = JevAnalyzer(client_factory=FakeClient).suggest_review(
            {"turning_note": "finished without running the tests"}, {"no_verify": "no check", "loop": "repeats", "hardcode": "hard-coded"}
        )
        self.assertEqual([item["label"] for item in result["labels"]], ["no_verify"])
        self.assertEqual(result["intervention"]["choice"], "data")
        empty = JevAnalyzer(client_factory=FakeClient).suggest_review({"note": "  "}, {"loop": "x"})
        self.assertEqual(empty["labels"], [])

    def test_find_steps_ranks_across_windows(self) -> None:
        client = FakeClient()
        found = JevAnalyzer(client_factory=lambda: client).find_steps(sample_run(), "where does it run the tests", window=2)
        self.assertGreaterEqual(len(client.calls), 2)
        self.assertEqual(found["matches"][0]["step"], 3)

    def test_polish_tail_starts_after_the_last_required_step(self) -> None:
        def record(step, phase, polish=0.0):
            return {"step": step, "phase": phase, "p": {"polish": polish}}

        records = [
            record(1, "explore"), record(2, "implement"), record(3, "verify"),
            record(4, "implement", 0.9), record(5, "verify"), record(6, "implement", 0.8), record(7, "report"),
        ]
        offsets = {1: 0, 2: 60_000, 3: 120_000, 4: 180_000, 5: 240_000, 6: 300_000, 7: 600_000}
        tail = summarize_steps(records, offsets)["polish_tail"]
        self.assertEqual(tail["milestone_step"], 3)
        self.assertEqual(tail["steps"], [4, 5, 6, 7])
        self.assertEqual(tail["polish_steps"], [4, 6])
        self.assertEqual(tail["time_ms"], 420_000)
        self.assertEqual(tail["time_share"], 0.7)
        # Ending with a check and a report is not a polish tail.
        plain = [record(1, "implement"), record(2, "verify"), record(3, "report")]
        self.assertIsNone(summarize_steps(plain)["polish_tail"])

    def test_summary_without_answers(self) -> None:
        summary = summarize_steps([{"step": 1, "error": "x"}])
        self.assertEqual(summary["analyzed_steps"], 0)
        self.assertIsNone(summary["first_problem_step"])


if __name__ == "__main__":
    unittest.main()
