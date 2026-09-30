from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench import rewrite_annotations, rewrite_review
from trajectory_workbench.adapters.openai_chat import OpenAIChatAdapter
from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import Store
from tests.fixtures import write_jsonl


class NoJev:
    def available(self) -> bool:
        return False


STOCK = "You are Claude Code, Anthropic's official CLI for Claude.\n\n# Doing tasks\nDo the task.\n\n# Environment\n - cwd: /w\n"
ADDED = "\n# Deck authoring\nRead the cards in `inputs/guidance/bundle.zip` before designing.\n"
MCP = "mcp__deck_bench__deck_bench"


def tool(name: str, description: str = "") -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object"}}}


def call(call_id: str, name: str, arguments: dict) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def original_sample(sample_id: str = "deck-1_m_attempt_01") -> dict:
    """A harness trajectory: reads a guidance bundle, plans, writes, checks with an MCP bench."""
    return {
        "id": sample_id,
        "tools": [tool("Bash"), tool("Write"), tool("TodoWrite"), tool(MCP, "Checks the deck against the harness rules.")],
        "messages": [
            {"role": "system", "content": STOCK + ADDED},
            {"role": "user", "content": "Make a 5 slide deck about tides."},
            {"role": "assistant", "content": "", "reasoning_content": "Per the harness rules I read the guidance cards first.",
             "tool_calls": [call("b1", "Bash", {"command": "unzip -p inputs/guidance/bundle.zip cards/tides.json"})]},
            {"role": "tool", "tool_call_id": "b1", "content": "{\"card\": \"use a tide chart\"}"},
            {"role": "assistant", "content": "", "reasoning_content": "The deck needs 5 slides with a tide chart.",
             "tool_calls": [call("t1", "TodoWrite", {"todos": [{"content": "Read guidance cards", "status": "completed"},
                                                               {"content": "Write deck", "status": "pending"}]}),
                            call("w1", "Write", {"file_path": "deck.html", "content": "<html>tides</html>"})]},
            {"role": "tool", "tool_call_id": "t1", "content": "ok"},
            {"role": "tool", "tool_call_id": "w1", "content": "written"},
            {"role": "assistant", "content": "", "reasoning_content": "Now the bench must not report any overflow.",
             "tool_calls": [call("m1", MCP, {"file": "deck.html"})]},
            {"role": "tool", "tool_call_id": "m1", "content": "no overflow"},
            {"role": "assistant", "content": "Done: deck.html has 5 slides.", "reasoning_content": "Finished."},
        ],
    }


def rewritten_sample(sample_id: str = "deck-1_m_attempt_01") -> dict:
    """The same trajectory for a target harness without the bundle and the bench: the bundle
    read is removed (its step merged into the next), the todo item remapped, the attribution
    edited; the bench call and one mention of the bundle were left behind."""
    sample = original_sample(sample_id)
    messages = sample["messages"]
    sample["tools"] = [tool("Bash"), tool("Write"), tool("TodoWrite")]
    messages[0] = {"role": "system", "content": STOCK}
    merged = dict(messages[4])
    merged["reasoning_content"] = "I decide the design myself.\n\nThe deck needs 5 slides with a tide chart."
    merged["tool_calls"] = [call("t1", "TodoWrite", {"todos": [{"content": "Plan the design", "status": "completed"},
                                                               {"content": "Write deck", "status": "pending"}]}),
                            messages[4]["tool_calls"][1]]
    last = dict(messages[7])
    last["reasoning_content"] = "Now the bench must report no overflow; bundle.zip is not needed."
    sample["messages"] = [messages[0], messages[1], merged, messages[5], messages[6], last, messages[8], messages[9]]
    return sample


INDEX_MAP = [0, 1, 4, 5, 6, 7, 8, 9]


def load(sample: dict):
    return OpenAIChatAdapter().from_sample(sample, path=Path("/tmp/rewrite.jsonl"), row_index=0)


class WordDiffTest(unittest.TestCase):
    def test_unchanged_text_is_one_equal_segment(self) -> None:
        self.assertEqual(rewrite_review.word_diff("same", "same"), [["=", "same"]])
        self.assertEqual(rewrite_review.word_diff("", ""), [])

    def test_words_and_chinese_characters(self) -> None:
        self.assertEqual(
            rewrite_review.word_diff("Per the harness rules I will check it.", "I will check it."),
            [["-", "Per the harness rules "], ["=", "I will check it."]],
        )
        diff = rewrite_review.word_diff("我先读取参考卡片再选方向。", "我先自己选方向。")
        self.assertEqual("".join(t for op, t in diff if op != "+"), "我先读取参考卡片再选方向。")
        self.assertEqual("".join(t for op, t in diff if op != "-"), "我先自己选方向。")
        self.assertIn(["=", "我先"], diff)

    def test_short_unchanged_islands_join_the_change(self) -> None:
        self.assertEqual(rewrite_review.word_diff("a b c", "x y z"), [["-", "a b c"], ["+", "x y z"]])

    def test_a_rewritten_passage_is_replaced_whole(self) -> None:
        before = "Now let me read the guidance references and pick a direction card."
        after = "Now let me settle the design approach on my own terms."
        self.assertEqual(rewrite_review.word_diff(before, after), [
            ["=", "Now let me "], ["-", "read the guidance references and pick a direction card"],
            ["+", "settle the design approach on my own terms"], ["=", "."],
        ])
        # A light edit stays word by word.
        self.assertEqual(rewrite_review.word_diff("I will check the deck twice.", "I will check the deck once."),
                         [["=", "I will check the deck "], ["-", "twice"], ["+", "once"], ["=", "."]])

    def test_only_changed_lines_are_diffed(self) -> None:
        before = "line one\nline two\nline three\n"
        after = "line one\nline 2\nline three\n"
        self.assertEqual(rewrite_review.word_diff(before, after), [["=", "line one\nline "], ["-", "two"], ["+", "2"], ["=", "\nline three\n"]])


class AlignTest(unittest.TestCase):
    def check(self, rows) -> None:
        kinds = [(row["after"]["step"] if row["after"] else None, [m["step"] for m in row["before"]]) for row in rows]
        # system, user, merged model step (original #3 folded into #4), bench step, final step
        self.assertEqual(kinds, [(1, [1]), (2, [2]), (3, [3, 4]), (4, [5]), (5, [6])])

    def test_with_the_pipeline_index_map(self) -> None:
        rows, how = rewrite_review.align(load(original_sample()).messages, load(rewritten_sample()).messages, INDEX_MAP)
        self.assertEqual(how, "index_map")
        self.check(rows)

    def test_inferred_matches_the_index_map(self) -> None:
        rows, how = rewrite_review.align(load(original_sample()).messages, load(rewritten_sample()).messages)
        self.assertEqual(how, "inferred")
        self.check(rows)

    def test_removed_and_added_steps(self) -> None:
        before = load(original_sample())
        rewritten = rewritten_sample()
        rewritten["messages"].insert(2, {"role": "user", "content": "A new note."})
        rewritten["messages"] = [m for m in rewritten["messages"] if m.get("content") != "Make a 5 slide deck about tides."]
        rows, _ = rewrite_review.align(before.messages, load(rewritten).messages)
        removed = [row for row in rows if row["after"] is None]
        added = [row for row in rows if not row["before"]]
        self.assertEqual([m["text"] for row in removed for m in row["before"]], ["Make a 5 slide deck about tides."])
        self.assertEqual([row["after"]["text"] for row in added], ["A new note."])


class ReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.before, self.after = load(original_sample()), load(rewritten_sample())

    def test_rows_changes_tools_and_harness(self) -> None:
        result = rewrite_review.review(self.before, self.after, {"index_map": INDEX_MAP})
        rows = result["rows"]
        self.assertTrue(rows[0]["system_prompt"])
        self.assertEqual([row["kind"] for row in rows[1:]], ["same", "merged", "changed", "same"])
        merged = rows[2]
        removed = [t for t in merged["tools"] if t["change"] == "removed"]
        self.assertEqual([(t["id"], t["name"]) for t in removed], [("b1", "Bash")])
        self.assertIn("bundle.zip", removed[0]["input"])
        todo = next(t for t in merged["tools"] if t["id"] == "t1")
        self.assertEqual(todo["change"], "changed")
        self.assertIn(["-", "Read guidance cards"], [segment[:2] for segment in todo["input"]])
        # The merged step is diffed against both originals joined by a blank line.
        thinking = merged["fields"]["thinking"]
        self.assertEqual("".join(s[1] for s in thinking if s[0] != "+"),
                         "Per the harness rules I read the guidance cards first.\n\nThe deck needs 5 slides with a tide chart.")
        delta = result["harness"]
        self.assertEqual({(c["kind"], c["name"]) for c in delta["removed"]},
                         {("mcp", "deck_bench"), ("instruction", "Deck authoring"), ("file", "inputs/guidance/bundle.zip")})
        sections = {s["heading"]: s["change"] for s in delta["sections"]}
        self.assertEqual(sections["Deck authoring"], "removed")
        self.assertEqual(sections["Doing tasks"], "same")
        self.assertTrue(next(s for s in delta["sections"] if s["heading"] == "Doing tasks")["stock"])
        self.assertEqual({t["name"]: t["change"] for t in delta["tools"]}[MCP], "removed")

    def test_residue_is_what_the_rewrite_removed_but_still_uses(self) -> None:
        result = rewrite_review.review(self.before, self.after)
        residue = {item["name"]: item for item in result["residue"]}
        self.assertEqual(residue["deck_bench"]["uses"], {"calls": [4]})
        self.assertEqual(residue["inputs/guidance/bundle.zip"]["uses"], {"mentions": [4]})
        self.assertIn("bundle.zip", residue["inputs/guidance/bundle.zip"]["terms"])
        self.assertEqual(result["rows"][3]["residue"], ["r:mcp:deck_bench", "r:file:inputs/guidance/bundle.zip"])

    def test_kept_tools_are_not_residue_and_redefinitions_show(self) -> None:
        after = rewritten_sample()
        after["tools"].append(tool(MCP, "Checks overflow."))
        result = rewrite_review.review(self.before, load(after))
        self.assertNotIn("deck_bench", {item["name"] for item in result["residue"]})
        bench = next(t for t in result["harness"]["tools"] if t["name"] == MCP)
        self.assertEqual(bench["change"], "redefined")
        self.assertIn(["-", "the deck against the harness rules"], [s[:2] for s in bench["diff"]])

    def test_pipeline_edits_attach_to_their_hunks(self) -> None:
        annotation = {
            "index_map": INDEX_MAP,
            "changes": [{
                "key": "2:rewrite:0", "message": 2, "field": "thinking", "old": "Per the harness rules I read the guidance cards first.",
                "new": "I decide the design myself.", "category": "removed_rule", "reason": "no such rule in the target harness",
                "refs": ["plan-1"], "warnings": ["drops_negation: not"],
            }],
        }
        result = rewrite_review.review(self.before, self.after, annotation)
        merged = result["rows"][2]
        change = next(c for c in result["changes"] if c["id"] == "e:2:rewrite:0")
        self.assertEqual([edit["category"] for edit in change["annotations"]], ["removed_rule"])
        self.assertEqual(change["added"], "I decide the design myself")
        self.assertTrue(any(segment[-1] == "e:2:rewrite:0" for segment in merged["fields"]["thinking"] if segment[0] != "="))
        # The edit in the last step was not recorded by the pipeline: it has a change of its own.
        unrecorded = [c for c in result["changes"] if c["id"].startswith("h:4:thinking:")]
        self.assertEqual(len(unrecorded), 1)
        self.assertEqual(unrecorded[0]["annotations"], [])
        self.assertEqual(result["unplaced"], [])

    def test_neighbouring_edits_keep_their_own_changes(self) -> None:
        before, after = original_sample(), original_sample()
        before["messages"][9]["reasoning_content"] = "Per the rules I read card A. Per the rules I read card B."
        after["messages"][9]["reasoning_content"] = "I chose A. I chose B."
        annotation = {"changes": [
            {"key": "a", "message": 9, "field": "thinking", "old": "Per the rules I read card A.", "new": "I chose A."},
            {"key": "b", "message": 9, "field": "thinking", "old": "Per the rules I read card B.", "new": "I chose B."},
        ]}
        result = rewrite_review.review(load(before), load(after), annotation)
        self.assertEqual({c["id"] for c in result["changes"]}, {"e:a", "e:b"})
        self.assertEqual(result["unplaced"], [])

    def test_residue_catches_a_card_named_without_its_path(self) -> None:
        before, after = original_sample(), rewritten_sample()
        before["messages"][0]["content"] += "Cards: `inputs/cards/tide-chart-card.json`.\n"
        after["messages"][-1]["reasoning_content"] = "Following tide-chart-card, the chart goes first."
        names = {item["name"]: item for item in rewrite_review.review(load(before), load(after))["residue"]}
        self.assertEqual(names["inputs/cards/tide-chart-card.json"]["uses"], {"mentions": [5]})

    def test_an_edit_that_cannot_be_found_is_reported(self) -> None:
        annotation = {"changes": [{"key": "k", "message": 2, "field": "thinking", "old": "not in the text", "new": "nor this"}]}
        result = rewrite_review.review(self.before, self.after, annotation)
        self.assertEqual([edit["key"] for edit in result["unplaced"]], ["k"])

    def test_change_ids_are_stable(self) -> None:
        first = rewrite_review.review(self.before, self.after)
        second = rewrite_review.review(load(original_sample()), load(rewritten_sample()))
        self.assertEqual([c["id"] for c in first["changes"]], [c["id"] for c in second["changes"]])


class SummarySyncTest(unittest.TestCase):
    def segments(self, summary: str, continuation: str):
        up = {"id": "s_m_attempt_01_context_01", "messages": [
            {"role": "user", "content": "Work."},
            {"role": "user", "content": "CRITICAL: Respond with TEXT ONLY. Summarize."},
            {"role": "assistant", "content": f"<analysis>x</analysis>\n<summary>{summary}</summary>"},
        ]}
        down = {"id": "s_m_attempt_01_context_02", "messages": [
            {"role": "user", "content": f"This session is being continued from a previous conversation.\n\nSummary:\n{continuation}"},
            {"role": "assistant", "content": "Continuing."},
        ]}
        return load(up), load(down)

    def test_a_rewrite_that_edits_only_one_side_breaks_the_pair(self) -> None:
        original = self.segments("Per the harness, 5 slides.", "Per the harness, 5 slides.")
        kept = self.segments("5 slides.", "5 slides.")
        broken = self.segments("5 slides.", "Per the harness, 5 slides.")
        self.assertEqual(rewrite_review.summary_sync(original, kept)["ok"], True)
        self.assertEqual(rewrite_review.summary_sync(original, broken)["ok"], False)
        unpaired = self.segments("One text.", "Another text entirely.")
        self.assertEqual(rewrite_review.summary_sync(unpaired, unpaired), {"paired": False, "ok": None, "up_step": 3, "down_step": 1})


class GateTest(unittest.TestCase):
    change = {"id": "e:1", "annotations": [{"warnings": ["drops_negation: not"]}]}

    def test_order_and_overrides(self) -> None:
        gate = rewrite_review.gate
        self.assertEqual(gate(status={"value": "rewritten"}, changes=[], residue_items=[], syncs=[], verdicts={})["decision"], "include")
        self.assertEqual(gate(status={"value": "blocked"}, changes=[], residue_items=[], syncs=[], verdicts={})["decision"], "exclude")
        held = gate(status=None, changes=[self.change], residue_items=[{"key": "r:x"}], syncs=[{"ok": False}], verdicts={})
        self.assertEqual(held["decision"], "review")
        self.assertEqual(len(held["reasons"]), 3)
        judged = {"e:1": {"verdict": "correct"}, "r:x": {"verdict": "ignore"}}
        self.assertEqual(gate(status=None, changes=[self.change], residue_items=[{"key": "r:x"}], syncs=[], verdicts=judged)["decision"], "include")
        wrong = {"e:1": {"verdict": "error"}}
        self.assertEqual(gate(status=None, changes=[self.change], residue_items=[], syncs=[], verdicts=wrong)["decision"], "exclude")
        forced = {**wrong, "record": {"verdict": "include", "note": "fixed by hand"}}
        result = gate(status=None, changes=[self.change], residue_items=[], syncs=[], verdicts=forced)
        self.assertEqual(result["decision"], "include")
        self.assertEqual(result["reasons"][0]["level"], "override")
        # Warning types outside the gate list do not hold a record.
        other = {"id": "e:2", "annotations": [{"warnings": ["drops_protected: 8"]}]}
        self.assertEqual(gate(status=None, changes=[other], residue_items=[], syncs=[], verdicts={})["decision"], "include")


class AnnotationsTest(unittest.TestCase):
    def test_neutral_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "notes.jsonl"
            write_jsonl(path, [
                {"plan": [{"id": "p1", "group": "Rules", "fate": "removed", "before": "Read the cards."}], "fates": {"removed": "gone"}},
                {"sample_id": "a", "status": "rewritten", "index_map": [0, 2],
                 "changes": [{"message": 1, "field": "reasoning_content", "old": "x", "new": "y", "refs": ["p1"]}],
                 "targets": [{"message": 1, "task": "rewrite", "locator": [{"source": "jev", "score": 0.9}]}]},
            ])
            loaded = rewrite_annotations.load(path)
            with self.assertRaises(ValueError):
                write_jsonl(path, [{"changes": []}])
                rewrite_annotations.load(path)
        self.assertEqual(loaded["adapter"], "annotations-jsonl")
        self.assertEqual(loaded["plan"]["items"]["p1"]["fate"], "removed")
        record = loaded["records"]["a"]
        self.assertEqual(record["index_map"], [0, 2])
        self.assertEqual(record["changes"][0]["field"], "thinking")
        self.assertEqual(record["changes"][0]["key"], "1:thinking:0")
        self.assertEqual(record["targets"][0]["locator"][0]["source"], "jev")

    def test_rewrite_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            run = project / "runs" / "pilot"
            (project / "spec").mkdir(parents=True)
            (project / "spec" / "plan-v1.json").write_text(json.dumps({
                "statuses": {"removed": "no longer stated"},
                "clauses": [{"id": "refs.read", "section": "Guidance", "status": "removed", "old_text": "Read the cards first."}],
            }))
            (run / "chains" / "deck-1").mkdir(parents=True)
            (run / "run_config.json").write_text(json.dumps({"prompt": "abc", "locator": "kimi", "api_url": "http://secret", "spec": {"clauses": "plan-v1"}}))
            (run / "report.json").write_text(json.dumps({"edits": 1}))
            (run / "chains" / "deck-1" / "status.json").write_text(json.dumps({
                "state": "done", "records": ["deck-1_m_attempt_01", "deck-1_m_attempt_01_context_02"],
                "record_status": {"deck-1_m_attempt_01": "rewritten", "deck-1_m_attempt_01_context_02": "blocked"},
                "sync": [{"from": "deck-1_m_attempt_01", "to": "deck-1_m_attempt_01_context_02", "match": "exact", "changed": True}],
            }))
            write_jsonl(run / "chains" / "deck-1" / "results.jsonl", [{
                "record": "deck-1_m_attempt_01", "msg": 2, "task": "fusion", "parsed": True, "need_context": None,
                "hints": [{"source": "kimi", "field": "reasoning_content", "quote": "Per the harness", "reason": "cites rules", "quote_found": True}],
                "edits": [{"field": "reasoning_content", "old": "Per the harness rules I read the guidance cards first.", "new": "I decide the design myself.",
                           "category": "removed_sp_attribution", "source_type": "removed_sp", "clause_ids": ["refs.read"], "note": "no source", "warnings": []}],
                "spans": [{"field": "reasoning_content", "quote": "Per the harness rules I read the guidance cards first.", "decision": "rewrite",
                           "evidence": [{"quote": "Read the cards first.", "ok": True}]},
                          {"field": "content", "quote": "5 slides", "decision": "keep", "note": "user asked", "source_type": "user", "clause_ids": []}],
                "reports": [{"field": "content", "status": "not_found", "errors": ["quote not found"]}],
                "evidence_ok": 1, "evidence_bad": 0,
            }])
            rewritten = rewritten_sample()
            rewritten["tag"] = {"rewrite": {"skills": {"removed_calls": 1, "index_map": INDEX_MAP}}}
            write_jsonl(run / "rewritten.jsonl", [rewritten])
            loaded = rewrite_annotations.load(run)
        self.assertEqual(loaded["adapter"], "rewrite-run-dir")
        self.assertNotIn("api_url", loaded["meta"])
        self.assertEqual(loaded["meta"]["report"], {"edits": 1})
        self.assertEqual(loaded["plan"]["items"]["refs.read"]["before"], "Read the cards first.")
        record = loaded["records"]["deck-1_m_attempt_01"]
        self.assertEqual(record["status"], "rewritten")
        self.assertEqual(record["index_map"], INDEX_MAP)
        [change] = record["changes"]
        self.assertEqual((change["key"], change["field"], change["refs"]), ("2:fusion:0", "thinking", ["refs.read"]))
        self.assertEqual(change["evidence"], [{"quote": "Read the cards first.", "ok": True}])
        [target] = record["targets"]
        self.assertEqual(target["kept"][0]["quote"], "5 slides")
        self.assertEqual(target["rejected"][0]["status"], "not_found")
        self.assertEqual(loaded["records"]["deck-1_m_attempt_01_context_02"]["status"], "blocked")
        self.assertTrue(loaded["records"]["deck-1_m_attempt_01_context_02"]["sync"][0]["ok"])


class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        write_jsonl(self.base / "orig" / "dialog.jsonl", [original_sample(), original_sample("deck-2_m_attempt_01")])
        write_jsonl(self.base / "new" / "dialog.jsonl", [rewritten_sample()])
        write_jsonl(self.base / "notes.jsonl", [
            {"sample_id": "deck-1_m_attempt_01", "status": "rewritten", "index_map": INDEX_MAP,
             "changes": [{"message": 2, "field": "thinking", "old": "Per the harness rules I read the guidance cards first.",
                          "new": "I decide the design myself.", "warnings": ["drops_negation: not"]}]},
            {"sample_id": "deck-2_m_attempt_01", "status": "blocked", "reason": "summary mismatch"},
        ])
        self.service = WorkbenchService(Store(self.base / "index.db"), analyzer=NoJev(), reviewer="t")
        self.service.import_path(str(self.base / "orig" / "dialog.jsonl"), "orig")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_batch_record_verdicts_and_gate(self) -> None:
        created = self.service.create_rewrite_batch(
            "pilot", "orig", rewritten_path=str(self.base / "new" / "dialog.jsonl"), annotations=str(self.base / "notes.jsonl")
        )
        self.assertEqual((created["records"], created["paired"]), (2, 1))
        with self.assertRaises(ValueError):
            self.service.create_rewrite_batch("pilot", "orig", rewritten="pilot")
        listing = self.service.rewrite_batch_records("pilot")
        by_id = {r["sample_id"]: r for r in listing["records"]}
        self.assertEqual(by_id["deck-2_m_attempt_01"]["status"], "blocked")
        self.assertIsNone(by_id["deck-2_m_attempt_01"]["rewritten"])

        record = self.service.rewrite_record("pilot", "deck-1_m_attempt_01")
        self.assertEqual(record["aligned_by"], "index_map")
        edit = next(c for c in record["changes"] if c.get("annotations"))
        # A warned change and two residue items hold the record for review.
        self.assertEqual(record["gate"]["decision"], "review")
        self.service.save_rewrite_verdict("pilot", "deck-1_m_attempt_01", {"key": edit["id"], "kind": "change", "verdict": "correct"})
        for item in record["residue"]:
            self.service.save_rewrite_verdict("pilot", "deck-1_m_attempt_01", {"key": item["key"], "kind": "residue", "verdict": "ignore"})
        self.assertEqual(self.service.rewrite_record("pilot", "deck-1_m_attempt_01")["gate"]["decision"], "include")
        saved = self.service.save_rewrite_verdict("pilot", "deck-1_m_attempt_01", {
            "kind": "miss", "verdict": "miss", "error_type": "attribution", "detail": {"step": 5, "field": "text", "quote": "bench"}})
        self.assertTrue(saved["verdict"]["key"].startswith("m:"))
        self.assertEqual(saved["gate"]["decision"], "exclude")
        cleared = self.service.save_rewrite_verdict("pilot", "deck-1_m_attempt_01", {"key": saved["verdict"]["key"], "delete": True})
        self.assertEqual(cleared["gate"]["decision"], "include")

        blocked = self.service.rewrite_record("pilot", "deck-2_m_attempt_01")
        self.assertEqual(blocked["gate"]["decision"], "exclude")
        self.assertEqual(blocked["rows"], [])

    def test_verdicts_are_validated(self) -> None:
        self.service.create_rewrite_batch("b", "orig", rewritten_path=str(self.base / "new" / "dialog.jsonl"))
        bad = [
            {"key": "e:x", "kind": "change", "verdict": "include"},
            {"key": "e:x", "kind": "change", "verdict": "error"},
            {"key": "e:x", "kind": "change", "verdict": "error", "error_type": "other"},
            {"key": "r:x", "kind": "change", "verdict": "correct"},
            {"kind": "miss", "verdict": "miss"},
        ]
        for fields in bad:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.service.save_rewrite_verdict("b", "deck-1_m_attempt_01", fields)
        with self.assertRaises(KeyError):
            self.service.rewrite_record("b", "nope")

    def test_without_annotations_the_diff_still_works(self) -> None:
        self.service.create_rewrite_batch("plain", "orig", rewritten_path=str(self.base / "new" / "dialog.jsonl"), rewritten="new")
        record = self.service.rewrite_record("plain", "deck-1_m_attempt_01")
        self.assertEqual(record["aligned_by"], "inferred")
        self.assertTrue(record["changes"])
        self.assertTrue(all(not change.get("annotations") for change in record["changes"]))
        self.assertEqual(record["gate"]["decision"], "review")  # residue is not handled yet


if __name__ == "__main__":
    unittest.main()
