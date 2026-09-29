from __future__ import annotations

import unittest

from trajectory_workbench import harness, readiness, signals
from trajectory_workbench.adapters.common import TranscriptBuilder, finish_run, make_outcome


STOCK_PROMPT = """You are Claude Code, Anthropic's official CLI for Claude, running within the Claude Agent SDK.

# Harness
 - Tools run behind a user-selected permission mode.

# Memory
You have a persistent memory directory.

# Environment
 - Primary working directory: /workspace

# Context management
When the conversation grows long, it is summarized.
"""
ADDED = """
# Deck authoring

Write the deck to `artifact.html`; the wrapper exports it as `slides.html`.
Read the cards in `inputs/guidance/bundle.zip` first. Motion plans go in `motion_plan.json`.
Keep `location.hash` in sync and use `text/html`.

## Image budget
At most 8 calls.

VISUAL HIERARCHY
Make the reading order obvious.
"""
MCP = "mcp__deck_bench__deck_bench"


def build(system, steps, *, task="Make a 5-slide deck about tides.", available=None, adapter="openai-chat", meta=None):
    """steps: list of (text, thinking, [(name, input)])."""
    builder = TranscriptBuilder()
    if system:
        builder.add_message("system", text=system)
    builder.add_message("user", text=task)
    for index, (text, thinking, calls) in enumerate(steps):
        message = builder.add_message("assistant", text=text, thinking=thinking)
        for call_index, (name, tool_input) in enumerate(calls):
            call_id = f"c{index}-{call_index}"
            builder.add_tool_call(message, call_id=call_id, name=name, tool_input=tool_input)
            builder.set_result(call_id, text="ok")
    return finish_run(builder, adapter_id=adapter, source_path="/tmp/x", run_id="r", title=None,
                      outcome=make_outcome(), meta=meta or {}, available_tools=available)


def by_name(trace):
    return {component["name"]: component for component in trace["components"]}


class InventoryTest(unittest.TestCase):
    def test_finds_added_mcp_instructions_and_files_not_stock_parts(self) -> None:
        run = build(STOCK_PROMPT + ADDED, [], available=["Bash", "Read", "Edit", "Write", "Agent", "Skill", MCP])
        found = harness.inventory(run)
        self.assertEqual(found["base"], "claude-code")
        kinds = {(c["kind"], c["name"]) for c in found["components"]}
        self.assertIn(("mcp", "deck_bench"), kinds)
        self.assertIn(("instruction", "Deck authoring"), kinds)
        # Stock sections and stock tools are not components.
        self.assertNotIn(("instruction", "Memory"), kinds)
        self.assertFalse(any(kind == "tool" for kind, _ in kinds))
        files = {name for kind, name in kinds if kind == "file"}
        self.assertEqual(files, {"artifact.html", "slides.html", "inputs/guidance/bundle.zip", "motion_plan.json"})
        instruction = next(c for c in found["components"] if c["kind"] == "instruction")
        self.assertEqual(instruction["topics"], ["Image budget", "VISUAL HIERARCHY"])

    def test_file_the_user_asked_for_is_not_a_harness_file(self) -> None:
        run = build(STOCK_PROMPT + ADDED, [], task="Make the deck and save it as `slides.html`.")
        files = {c["name"] for c in harness.inventory(run)["components"] if c["kind"] == "file"}
        self.assertNotIn("slides.html", files)
        self.assertIn("artifact.html", files)

    def test_extra_tools_skills_hooks_and_instruction_files(self) -> None:
        builder = TranscriptBuilder()
        builder.add_message("system", text=STOCK_PROMPT)
        builder.add_message("user", text="<system-reminder>\nContents of /repo/CLAUDE.md (project instructions):\n\nAlways run make lint.\n</system-reminder>\nFix the bug")
        step = builder.add_message("assistant", text="Loading the review skill.")
        builder.add_tool_call(step, call_id="s1", name="Skill", tool_input={"skill": "deep-review"})
        builder.set_result("s1", text="loaded")
        builder.add_tool_call(step, call_id="s2", name="lint_repo", tool_input={})
        builder.set_result("s2", text="clean")
        builder.add_message("system", text="PostToolUse:Edit hook additional context: run the formatter", extra={"layer": "hook"})
        builder.add_message("assistant", text="Done.")
        run = finish_run(builder, adapter_id="t", source_path="/tmp/x", run_id="r", title=None, outcome=make_outcome(),
                         available_tools=["Bash", "Read", "Skill", "lint_repo"])
        trace = harness.trace(run)
        components = by_name(trace)
        self.assertEqual(components["deep-review"]["kind"], "skill")
        self.assertEqual(components["deep-review"]["calls"], [3])
        self.assertEqual(components["lint_repo"]["kind"], "tool")
        self.assertEqual(components["lint_repo"]["calls"], [3])
        self.assertEqual(components["PostToolUse:Edit"]["kind"], "hook")
        self.assertEqual(components["CLAUDE.md"]["kind"], "instruction")

    def test_stock_codex_tools_and_developer_messages_are_not_added(self) -> None:
        builder = TranscriptBuilder()
        builder.add_message("system", text="<multi_agent_mode>Any earlier instruction enabling proactive delegation…</multi_agent_mode>", extra={"layer": "instruction"})
        builder.add_message("system", text="# AGENTS.md instructions\n\n<INSTRUCTIONS>Use uv.</INSTRUCTIONS>", extra={"layer": "instruction"})
        builder.add_message("user", text="Fix it")
        step = builder.add_message("assistant", text="")
        for call_id, name in (("a", "exec"), ("b", "collaboration__spawn_agent"), ("c", "clock__sleep"), ("d", "apply_patch")):
            builder.add_tool_call(step, call_id=call_id, name=name, tool_input={})
            builder.set_result(call_id, text="ok")
        builder.add_message("assistant", text="Done.")
        run = finish_run(builder, adapter_id="codex", source_path="/tmp/x", run_id="r", title=None, outcome=make_outcome(), meta={"format": "codex"})
        found = harness.inventory(run)
        self.assertEqual(found["base"], "codex")
        self.assertEqual([(c["kind"], c["name"]) for c in found["components"]], [("instruction", "AGENTS.md")])
        self.assertNotIn("harness_ref", {f["key"] for f in signals.compute(run)["flags"]})

    def test_system_prompt_recorded_outside_the_messages(self) -> None:
        run = build("", [("", "", [("Write", {"file_path": "artifact.html"})])], available=["Bash", "Read", "Write"])
        run.model_prompt = {"system": {"text": STOCK_PROMPT + ADDED}}
        components = by_name(harness.trace(run))
        self.assertEqual(components["Deck authoring"]["kind"], "instruction")
        # The agent writes artifact.html itself: an output naming convention, not traced.
        self.assertEqual(components["artifact.html"], {"kind": "file", "name": "artifact.html", "agent_output": True})
        # Jev's evidence keeps it (conventions=True). No system message: the model's turn is step 2.
        self.assertEqual(by_name(harness.trace(run, conventions=True))["artifact.html"]["files"], [2])

    def test_a_file_the_instructions_ask_the_agent_to_write_is_its_own_output(self) -> None:
        # A later context segment only reads the deck written in an earlier one.
        run = build(STOCK_PROMPT + ADDED, [
            ("Checking the deck from the previous segment.", "", [("Bash", {"command": "grep -c deck-slide artifact.html"})]),
            ("", "", [("Bash", {"command": "unzip -l inputs/guidance/bundle.zip"})]),
        ], available=["Bash", "Read"])
        components = by_name(harness.trace(run))
        self.assertTrue(components["artifact.html"]["agent_output"])
        self.assertTrue(components["slides.html"]["agent_output"])
        self.assertNotIn("files", components["artifact.html"])
        # Guidance the harness put in the workspace is not an output.
        self.assertEqual(components["inputs/guidance/bundle.zip"]["files"], [4])
        self.assertNotIn("agent_output", components["inputs/guidance/bundle.zip"])

    def test_directories_are_not_harness_files(self) -> None:
        rules = STOCK_PROMPT + "\n# Project rules\nPut new tests in `tests/` and scripts in `tools/`.\n"
        run = build(rules, [("Let me run the tests first.", "", [("Bash", {"command": "ls tools/"})])], available=["Bash"])
        self.assertEqual([c["kind"] for c in harness.inventory(run)["components"]], ["instruction"])
        self.assertNotIn("harness_ref", {f["key"] for f in signals.compute(run)["flags"]})

    def test_harness_defined_subagent_types(self) -> None:
        run = build(STOCK_PROMPT, [
            ("Asking the slide reviewer.", "", [("Agent", {"subagent_type": "slide-reviewer", "prompt": "review"})]),
            ("", "", [("Agent", {"subagent_type": "general-purpose", "prompt": "search"}), ("Task", {"subagent_type": "Explore", "prompt": "find"})]),
            ("The slide-reviewer found two issues.", "", []),
        ], available=["Agent", "Task", "Bash", "Read"])
        trace = harness.trace(run)
        self.assertEqual([(c["kind"], c["name"]) for c in trace["components"]], [("subagent", "slide-reviewer")])
        self.assertEqual(by_name(trace)["slide-reviewer"]["calls"], [3])
        self.assertEqual(by_name(trace)["slide-reviewer"]["mentions"], [5])
        self.assertEqual(harness.prompt_summary(harness.inventory(run)), {"custom_subagent_types": ["slide-reviewer"]})

    def test_base_agent_from_tool_set(self) -> None:
        codex = build("", [("", "", [("apply_patch", {"input": "*** Begin Patch"}), ("exec_command", {"cmd": "ls"})])])
        self.assertEqual(harness.base_agent(codex), "codex")
        self.assertEqual(harness.inventory(codex)["components"], [])
        pi = build("", [("", "", [("read", {"path": "a"}), ("bash", {"command": "ls"})])], available=["read", "bash", "edit", "write"])
        self.assertEqual(harness.base_agent(pi), "pi")
        short = build(STOCK_PROMPT, [("", "", [(MCP, {})])], available=["Bash", "Read", "Edit", MCP, "deck_bench"])
        self.assertEqual([c["kind"] for c in harness.inventory(short)["components"]], ["mcp"])
        unknown = build("", [("", "", [("run", {}), (MCP, {})])])
        self.assertIsNone(harness.base_agent(unknown))
        # Without a known base only MCP servers count as added.
        self.assertEqual([c["kind"] for c in harness.inventory(unknown)["components"]], ["mcp"])


class TraceTest(unittest.TestCase):
    def run_steps(self):
        return build(
            STOCK_PROMPT + ADDED,
            [
                ("Reading the tide data.", "", [("Read", {"file_path": "inputs/tides.csv"})]),
                ("Checking the layout.", "", [(MCP, {"action": "inspect_deck"})]),
                ("", "The deck_bench contact sheet shows slide 3 overflows.", [("Edit", {"file_path": "artifact.html", "old_string": "a", "new_string": "b"})]),
                ("Done: the deck is in artifact.html.", "", []),
                ("", "location.hash updates on arrow keys.", []),
                ("", "", [("Bash", {"command": "unzip -l inputs/guidance/bundle.zip"})]),
            ],
            available=["Bash", "Read", "Edit", "Write", MCP],
        )

    def test_calls_files_and_mentions_by_step(self) -> None:
        trace = harness.trace(self.run_steps())
        components = by_name(trace)
        self.assertEqual(components["deck_bench"]["calls"], [4])
        self.assertEqual(components["deck_bench"]["mentions"], [5])
        # The guidance archive the harness put in the workspace counts; the deliverable the
        # agent edits under the harness's file name does not.
        self.assertEqual(components["inputs/guidance/bundle.zip"]["files"], [8])
        self.assertTrue(components["artifact.html"]["agent_output"])
        self.assertNotIn("mentions", components["artifact.html"])
        # A JS property that the instructions mention is not a file, and step 3 reads a task file.
        self.assertNotIn("location.hash", components)
        self.assertEqual(trace["steps"], {"calls": [4], "files": [8], "mentions": [5]})

    def test_absolute_paths_count_and_shared_basenames_do_not(self) -> None:
        added = ADDED + "\nCards: `inputs/a/bundle.zip`, `inputs/b/bundle.zip`.\n"
        run = build(STOCK_PROMPT + added, [
            ("", "", [("Write", {"file_path": "/workspace/run-1/artifact.html", "content": "<html>" + "x" * 50000})]),
            ("The bundle.zip cards are optional.", "", [("Bash", {"command": "unzip -l inputs/b/bundle.zip"})]),
            ("", "", [(MCP, {})]),
            ("Checked with mcp__deck_bench__deck_bench.", "", []),
        ], available=["Bash", "Write", MCP])
        components = by_name(harness.trace(run))
        self.assertTrue(components["artifact.html"]["agent_output"])
        self.assertEqual(by_name(harness.trace(run, conventions=True))["artifact.html"]["files"], [3])
        # "bundle.zip" alone could be either archive, so it names neither.
        self.assertEqual(components["inputs/b/bundle.zip"], {"kind": "file", "name": "inputs/b/bundle.zip", "files": [4]})
        self.assertNotIn("mentions", components["inputs/a/bundle.zip"])
        self.assertEqual(components["deck_bench"]["mentions"], [6])

    def test_files_an_added_tool_produced_count_as_its_use(self) -> None:
        builder = TranscriptBuilder()
        builder.add_message("system", text=STOCK_PROMPT + ADDED)
        builder.add_message("user", text="Make a 5-slide deck about tides.")
        write = builder.add_message("assistant", text="Writing the deck.")
        builder.add_tool_call(write, call_id="w", name="Write", tool_input={"file_path": "artifact.html", "content": "<html>"})
        builder.set_result("w", text="ok")
        check = builder.add_message("assistant", text="")
        builder.add_tool_call(check, call_id="m", name=MCP, tool_input={"action": "inspect_deck"})
        builder.set_result("m", text='{"artifact": "artifact.html", "contact_sheet": "reports/sheet_001.png"}')
        crop = builder.add_message("assistant", text="", thinking="Crop the sheet to look at slide 3.")
        builder.add_tool_call(crop, call_id="c", name="Bash", tool_input={"command": "python3 crop.py reports/sheet_001.png"})
        builder.set_result("c", text="ok")
        builder.add_message("assistant", text="Slide 3 fixed; see reports/sheet_001.png.")
        run = finish_run(builder, adapter_id="t", source_path="/tmp/x", run_id="r", title=None, outcome=make_outcome(),
                         available_tools=["Bash", "Write", MCP])
        trace = harness.trace(run)
        bench = by_name(trace)["deck_bench"]
        self.assertEqual(bench["calls"], [4])
        # The contact sheet the tool saved is its product; artifact.html was the agent's own file.
        self.assertEqual(bench["outputs"], [5, 6])
        self.assertTrue(by_name(trace)["artifact.html"]["agent_output"])
        self.assertEqual(harness.step_evidence(trace)[5], ["mcp `deck_bench`: the step uses a file it produced earlier"])

    def test_flag_and_readiness_use_the_trace(self) -> None:
        run = self.run_steps()
        computed = signals.compute(run)
        flag = next(f for f in computed["flags"] if f["key"] == "harness_ref")
        self.assertEqual(flag["steps"], [4, 5, 8])
        self.assertIn("MCP deck_bench", flag["detail"])
        result = readiness.check(run, computed["values"]["harness"])
        issue = next(i for i in result["issues"] if i["key"] == "residue_in_trained")
        self.assertEqual(issue["steps"], [4, 5, 8])
        self.assertEqual(issue["severity"], "warn")
        self.assertEqual(result["residue"]["trained"]["calls"], [4])

    def test_naming_a_harness_is_not_depending_on_one(self) -> None:
        # A stock Claude Code session that develops a harness talks about it constantly;
        # nothing was added to its own agent, so nothing is flagged.
        run = build(STOCK_PROMPT, [
            ("The MoH IntentInspector reviewer lives in rsi-moh/inspect.py.", "Per the harness task JSON, the hard limit is 1 hour.",
             [("Read", {"file_path": "/workspace/output/moh-0123456789abcdef/runs/run-0123456789abcdef/attempts/001/x.py"})]),
        ], available=["Bash", "Read", "Edit"])
        self.assertEqual(harness.inventory(run)["components"], [])
        self.assertNotIn("harness_ref", {f["key"] for f in signals.compute(run)["flags"]})
        self.assertNotIn("residue_in_trained", {i["key"] for i in readiness.check(run)["issues"]})

    def test_prompt_summary_groups_by_kind(self) -> None:
        summary = harness.prompt_summary(harness.inventory(self.run_steps()))
        self.assertEqual(summary["mcp_servers"], [{"server": "deck_bench", "tools": ["deck_bench"]}])
        self.assertEqual(summary["added_instruction_sections"][0]["section"], "Deck authoring")
        self.assertIn("artifact.html", summary["harness_provided_files"])


if __name__ == "__main__":
    unittest.main()
