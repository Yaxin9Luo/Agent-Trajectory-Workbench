from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from trajectory_workbench.adapters.common import COMPACTION_PREFIX, TranscriptBuilder, finish_run, make_outcome
from trajectory_workbench.ledgers import (
    build_ledgers,
    compactions,
    delegations,
    plan_ledger,
    requirement_candidates,
    split_task,
)


def noul(p):
    return SimpleNamespace(type="noul", noul=p)


def choice(value, probabilities=None):
    return SimpleNamespace(type="choice", choice=value, confidence=0.9, probabilities=probabilities or {value: 0.9})


class FakeAnalyzer:
    """Answers ledger questions from the state, like a very literal model."""

    def __init__(self):
        self.requests = []

    def available(self):
        return True

    def ask(self, requests):
        self.requests.extend(requests)
        responses = []
        for state, questions in requests:
            answers = {}
            for key, question in questions.items():
                if "candidates" in state:
                    answers[key] = noul(0.1 if "Background" in state["candidates"][key] else 0.9)
                elif "steps" in state:
                    requirement = state["requirements"][key]
                    word = requirement.split()[1].lower()
                    hit = next((k for k, v in state["steps"].items() if word in v.lower()), None)
                    answers[key] = choice(hit or "none", {hit: 0.8, "none": 0.2} if hit else {"none": 0.9})
                elif "summary" in state:
                    answers[key] = noul(0.9 if state["requirements"][key].split()[1].lower() in state["summary"].lower() else 0.1)
                elif "harness_instructions" in state:
                    answers[key] = noul(0.8 if "tests" in state["requirements"][key] else 0.1)
                elif key == "adoption":
                    answers[key] = choice("acts_on")
                elif key == "response":
                    answers[key] = choice("ignores")
            responses.append(SimpleNamespace(usage=SimpleNamespace(input_tokens=10), answers=answers))
        return responses


def run_with(build):
    builder = TranscriptBuilder()
    build(builder)
    return finish_run(builder, adapter_id="t", source_path="/tmp/x", run_id="r", title=None, outcome=make_outcome())


class ExtractionTest(unittest.TestCase):
    def test_json_wrapped_task_separates_request_from_harness(self) -> None:
        wrapped = 'Treat the following JSON as the task.\n\n' + json.dumps(
            {"context": "Build a clock. Use red hands.", "instructions": "Never install packages."}
        ) + "\n\nRead the inputs first."
        task, harness = split_task(wrapped)
        self.assertEqual(task, "Build a clock. Use red hands.")
        self.assertIn("Never install packages.", harness)
        self.assertIn("Read the inputs first.", harness)
        self.assertEqual(split_task("Plain task."), ("Plain task.", ""))
        inline = 'Implement a /search endpoint that accepts a body like {"query": "cats", "limit": 5} and returns matching items. It must paginate results and reject empty queries with a 400.'
        self.assertEqual(split_task(inline), (inline, ""))
        # Titles come from the user's request, not the harness framing.
        from trajectory_workbench.adapters.common import title_from

        self.assertEqual(title_from(wrapped), "Build a clock. Use red hands.")

    def test_candidates_skip_headings_and_file_listings(self) -> None:
        task = "# Title\nMake a 5 slide deck. Keep the logo.\n\n## Task attachments\n- ` inputs/a.png `\n- Use serif type"
        self.assertEqual(requirement_candidates(task), ["Make a 5 slide deck.", "Keep the logo.", "Use serif type"])

    def test_plan_ledger_tracks_todos_tasks_and_plans(self) -> None:
        def build(b):
            b.add_message("user", text="go")
            steps = [
                ("TodoWrite", {"todos": [{"content": "Write app", "status": "in_progress"}, {"content": "Test app", "status": "pending"}]}, ""),
                ("TodoWrite", {"todos": [{"content": "Write app", "status": "completed"}]}, ""),
                ("TaskCreate", {"subject": "Draft storyboard"}, "Task #1 created successfully: Draft storyboard"),
                ("TaskUpdate", {"taskId": "1", "status": "completed"}, "Updated task #1 status"),
                ("update_plan", {"plan": [{"step": "Read code", "status": "completed"}]}, ""),
            ]
            for index, (name, data, result) in enumerate(steps):
                message = b.add_message("assistant", text="")
                b.add_tool_call(message, call_id=f"c{index}", name=name, tool_input=data)
                b.set_result(f"c{index}", text=result)

        ledger = {item["text"]: item for item in plan_ledger(run_with(build))}
        self.assertEqual(ledger["Write app"]["completed_step"], 3)
        self.assertEqual(ledger["Test app"]["status"], "removed")
        self.assertEqual((ledger["Draft storyboard"]["status"], ledger["Draft storyboard"]["completed_step"]), ("completed", 5))
        self.assertEqual(ledger["Read code"]["kind"], "plan")

    def test_delegations_find_sync_and_async_reports(self) -> None:
        def build(b):
            b.add_message("user", text="go")
            sync = b.add_message("assistant", text="Review it")
            b.add_tool_call(sync, call_id="a1", name="Agent", tool_input={"prompt": "Review app.py", "subagent_type": "reviewer"})
            b.set_result("a1", text="Found 2 bugs.\nagentId: abc123 (for resuming)")
            b.add_message("assistant", text="Fixing the 2 bugs")
            spawn = b.add_message("assistant", text="")
            b.add_tool_call(spawn, call_id="a2", name="Agent", tool_input={"prompt": "Draw", "description": "drawer"})
            b.set_result("a2", text="Async agent launched successfully.\nagentId: zz9 (internal)")
            b.add_message("user", text="<task-notification>zz9 finished: picture done</task-notification>")
            b.add_message("assistant", text="Looking at the picture")
            codex = b.add_message("assistant", text="")
            b.add_tool_call(codex, call_id="a3", name="spawn_agent", tool_input={"message": "gAAAAABqsmP0IOisd7qRiMVeSiHjosLBPmd4hY3k"})
            b.set_result("a3", text='{"task_name":"/root/x"}')

        found = delegations(run_with(build))
        self.assertEqual([(d["step"], d["result_step"]) for d in found], [(2, 2), (4, 5), (7, None)])
        self.assertEqual(found[0]["subagent_type"], "reviewer")
        self.assertTrue(found[2]["brief_encrypted"])
        self.assertEqual(found[2]["brief"], "")

    def test_compaction_summary_follows_its_marker(self) -> None:
        def build(b):
            b.add_message("user", text="Build it")
            b.add_message("system", text="[compact_boundary]", extra={"compaction": {"trigger": "auto", "pre_tokens": 9, "post_tokens": 1}})
            b.add_message("system", text=COMPACTION_PREFIX + " … keep the logo …")

        [found] = compactions(run_with(build))
        self.assertEqual((found["step"], found["trigger"]), (2, "auto"))
        self.assertTrue(found["summary"].endswith("keep the logo …"))


class BuildLedgersTest(unittest.TestCase):
    def test_jev_questions_are_filtered_and_joined_in_code(self) -> None:
        def build(b):
            b.add_message("user", text="task")
            write = b.add_message("assistant", text="Adding the logo now")
            b.add_tool_call(write, call_id="w", name="Write", tool_input={"file_path": "a.html"})
            b.set_result("w", text="ok")
            b.add_message("system", text=COMPACTION_PREFIX + " summary: the deck keeps the logo " + "x" * 300, extra={"compaction": {"trigger": "auto"}})
            b.add_message("system", text="<system-reminder>PostToolUse:Write hook additional context: run the tests now please</system-reminder>", extra={"layer": "hook"})
            b.add_message("assistant", text="Moving on to colours")

        run = run_with(build)
        instruction = "Keep the logo on every slide. Use serif type. Never skip tests. Background: this is for a client."
        analyzer = FakeAnalyzer()
        ledgers = build_ledgers(run, instruction, analyzer)
        texts = [item["text"] for item in ledgers["requirements"]]
        self.assertEqual(texts, ["Keep the logo on every slide.", "Use serif type.", "Never skip tests."])
        by_text = {item["text"]: item for item in ledgers["requirements"]}
        self.assertEqual(by_text["Keep the logo on every slide."]["step"], 2)
        self.assertIsNone(by_text["Use serif type."]["step"])
        self.assertEqual(ledgers["compactions"][0]["dropped"], [1, 2])
        self.assertEqual(ledgers["hooks"][0]["response"]["choice"], "ignores")
        self.assertTrue(ledgers["jev"])
        # Without an analyzer only the code parts come back.
        plain = build_ledgers(run, instruction, None)
        self.assertFalse(plain["jev"])
        self.assertEqual(len(plain["requirements"]), 4)


class FailingAnalyzer:
    def available(self):
        return True

    def ask(self, requests):
        return [RuntimeError("service down") for _ in requests]


class LedgerFailureTest(unittest.TestCase):
    def test_failed_jev_run_is_reported_not_cached(self) -> None:
        import tempfile
        from pathlib import Path

        from trajectory_workbench.service import WorkbenchService
        from trajectory_workbench.store import Store
        from tests.fixtures import make_sft_export

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            service = WorkbenchService(Store(base / "index.db"), analyzer=FailingAnalyzer(), reviewer="t")
            service.import_path(str(make_sft_export(base / "sft")), "S")
            trajectory_id = service.list_trajectories()["items"][0]["id"]
            with self.assertRaisesRegex(ValueError, "service down"):
                service.ledgers(trajectory_id, run_jev=True)
            cached = service.ledgers(trajectory_id)
        self.assertFalse(cached["jev"])


if __name__ == "__main__":
    unittest.main()
