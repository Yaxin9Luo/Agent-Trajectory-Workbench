"""Regressions from the 2026-11-11 project audit (service, store, search, statistics,
rewrite review, reward import): each test failed before its fix."""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from trajectory_workbench import excerpt, rewrite_review, search, v4_reward
from trajectory_workbench.jev import STEP_VERSION
from trajectory_workbench.practice import build_queue
from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import Store
from tests.fixtures import make_claude_session, make_sft_export, write_json, write_jsonl
from tests.test_practice import row as practice_row
from tests.test_rewrite_review import NoJev, original_sample, rewritten_sample
from tests.test_service import edit_sample


class CountingJev:
    """A stand-in analyzer that counts paid calls and takes a moment, like a real scan."""

    def __init__(self, *, failing: bool = False) -> None:
        self.step_scans = 0
        self.task_checks = 0
        self.failing = failing
        self.lock = threading.Lock()

    def available(self) -> bool:
        return True

    def analyze_steps(self, run):
        with self.lock:
            self.step_scans += 1
        time.sleep(0.2)
        if self.failing:
            return {"model": "m", "input_tokens": 0, "errors": 2, "summary": {"analyzed_steps": 0}, "steps": []}
        return {"model": "m", "input_tokens": 1000, "summary": {"analyzed_steps": 3, "flag_counts": {}}, "steps": []}

    def analyze_task(self, run):
        with self.lock:
            self.task_checks += 1
        return {"model": "m", "input_tokens": 10, "final_claim": {"choice": "complete"}, "task_ambiguity": {"score": 0.1}}


def chat_file(base: Path, count: int = 3) -> Path:
    path = base / "dialog.jsonl"
    write_jsonl(path, [
        edit_sample(f"s{i}_k3_attempt_01", task=f"Task number {i}", calls=[("B", "Bash", {"command": f"echo {i}"})])
        for i in range(count)
    ])
    return path


class ServiceCase(unittest.TestCase):
    def service(self, analyzer=None, reviewer: str = "t") -> tuple[WorkbenchService, Path]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        service = WorkbenchService(Store(base / "index.db"), analyzer=analyzer or NoJev(), reviewer=reviewer)
        self.addCleanup(service.store.close)
        return service, base


class SourceAndJevTest(ServiceCase):
    def test_a_source_rewritten_in_place_is_refused_not_shown_as_another_sample(self) -> None:
        service, base = self.service()
        path = chat_file(base)
        service.import_path(str(path), "S")
        first = next(r for r in service.store.all_trajectories("S") if r["run_id"].startswith("s0_"))
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source changed since import"):
            service.get_trajectory(first["id"])

    def test_two_requests_for_the_same_scan_pay_for_one(self) -> None:
        jev = CountingJev()
        service, base = self.service(jev)
        service.import_path(str(make_claude_session(base / "s.jsonl")), "Demo")
        trajectory_id = service.store.all_trajectories("Demo")[0]["id"]
        threads = [threading.Thread(target=service.analyze, args=(trajectory_id,)) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(jev.step_scans, 1)
        self.assertEqual(jev.task_checks, 1)

    def test_a_second_batch_for_the_same_collection_joins_the_running_one(self) -> None:
        jev = CountingJev()
        service, base = self.service(jev)
        service.import_path(str(chat_file(base, 4)), "S")
        first = service.start_batch_analysis("S", 100)
        second = service.start_batch_analysis("S", 100)
        self.assertEqual(second["id"], first["id"])
        other = service.start_batch_analysis(None, 100)
        self.assertNotEqual(other["id"], first["id"])
        deadline = time.time() + 20
        while time.time() < deadline and any(job["status"] == "running" for job in service.jobs.list()):
            time.sleep(0.05)
        self.assertEqual(jev.step_scans, 4)

    def test_a_scan_in_which_every_request_failed_is_a_failure_and_costs_no_task_check(self) -> None:
        jev = CountingJev(failing=True)
        service, base = self.service(jev)
        service.import_path(str(chat_file(base, 4)), "S")
        result = service.run_batch(service.batch_rows("S", 100))
        self.assertEqual(result["analyzed"], 0)
        self.assertEqual(len(result["failed"]), 3)
        self.assertIn("stopped after 3 failures", result["stopped"])
        self.assertEqual(jev.task_checks, 0)


class ListingTest(ServiceCase):
    def test_sidebar_read_count_is_this_reviewers(self) -> None:
        service, base = self.service(reviewer="me")
        service.import_path(str(chat_file(base)), "S")
        rows = service.store.all_trajectories("S")
        service.store.save_review(rows[0]["id"], "colleague", {"human_status": "pass"})
        self.assertEqual(service.list_collections()["collections"][0]["reviewed"], 0)
        service.save_review(rows[1]["id"], {"human_status": "fail"})
        self.assertEqual(service.list_collections()["collections"][0]["reviewed"], 1)

    def test_the_page_payload_does_not_stat_every_source(self) -> None:
        service, base = self.service()
        service.import_path(str(chat_file(base)), "S")
        self.assertNotIn("sources", service.list_collections())
        self.assertEqual(len(service.list_collections(sources=True)["sources"]), 1)

    def test_library_search_takes_percent_and_underscore_literally(self) -> None:
        service, base = self.service()
        service.import_path(str(chat_file(base)), "S")
        self.assertEqual(service.list_trajectories(collection="S", search="%")["total"], 0)
        self.assertEqual(service.list_trajectories(collection="S", search="number_1")["total"], 0)
        self.assertEqual(service.list_trajectories(collection="S", search="number 1")["total"], 1)

    def test_removal_preview_counts_everything_removal_deletes(self) -> None:
        service, base = self.service()
        service.import_path(str(chat_file(base)), "S")
        row = service.store.all_trajectories("S")[0]
        service.save_annotation(row["id"], {"step": 1, "note": "n"})
        service.store.put_jev(row["id"], "steps", STEP_VERSION, row["fingerprint"], "m", 1234, {"steps": []})
        preview = service.store.collection_contents("S")
        removed = service.store.remove_collection("S")
        self.assertEqual(preview["jev_input_tokens"], 1234)
        for key in ("trajectories", "reviews", "annotations", "jev_results", "sources"):
            self.assertEqual(preview[key], removed[key], key)
        self.assertEqual(preview["annotations"], 1)


class ReaderTest(ServiceCase):
    def test_reader_search_looks_at_text_not_field_names(self) -> None:
        service, base = self.service()
        service.import_path(str(make_claude_session(base / "s.jsonl")), "Demo")
        trajectory_id = service.store.all_trajectories("Demo")[0]["id"]
        total = service.get_messages(trajectory_id, limit=500)["total"]
        for word in ("line_number", "offset_ms", "is_error", "images"):
            with self.subTest(word=word):
                self.assertEqual(service.get_messages(trajectory_id, search=word)["total"], 0)
        hits = service.get_messages(trajectory_id, search="PYTEST")
        self.assertTrue(0 < hits["total"] < total)

    def test_an_offset_past_the_end_shows_the_last_message(self) -> None:
        service, base = self.service()
        service.import_path(str(make_claude_session(base / "s.jsonl")), "Demo")
        trajectory_id = service.store.all_trajectories("Demo")[0]["id"]
        page = service.get_messages(trajectory_id, offset=99_999, limit=20)
        self.assertEqual(page["offset"], page["total"] - 1)
        self.assertEqual(len(page["items"]), 1)

    def test_turning_step_must_be_a_step_of_the_trajectory(self) -> None:
        service, base = self.service()
        service.import_path(str(make_claude_session(base / "s.jsonl")), "Demo")
        row = service.store.all_trajectories("Demo")[0]
        for bad in (0, row["steps"] + 1, "x"):
            with self.subTest(step=bad), self.assertRaises(ValueError):
                service.save_review(row["id"], {"turning_step": bad})
        self.assertEqual(service.save_review(row["id"], {"turning_step": row["steps"]})["turning_step"], row["steps"])

    def test_excerpt_escapes_title_and_grader_reason(self) -> None:
        service, base = self.service()
        service.import_path(str(make_claude_session(base / "s.jsonl")), "Demo")
        row = service.store.all_trajectories("Demo")[0]
        _, run = service._load(row["id"])
        run.outcome = {"status": "fail", "reason": "<img src=x>\n# heading"}
        text = excerpt.markdown_excerpt(run, {**row, "title": "<script>alert(1)</script>"}, [1], [], {"human_status": "fail", "hypothesis": "<b>x</b>"})
        self.assertNotIn("<script>", text)
        self.assertNotIn("<img", text)
        self.assertNotIn("<b>", text)
        self.assertNotIn("\n# heading", text)


class SearchSnippetTest(unittest.TestCase):
    def test_snippet_shows_the_term_when_case_folding_changes_length(self) -> None:
        text = "ß" * 200 + " the TARGET word " + "tail " * 60
        self.assertIn("TARGET", search.snippet({"text": text}, ["target"])["text"])


class RewriteTest(ServiceCase):
    def test_a_rewrite_with_no_original_is_excluded_not_passed(self) -> None:
        service, base = self.service()
        write_jsonl(base / "orig" / "dialog.jsonl", [original_sample("deck-2_m_attempt_01")])
        write_jsonl(base / "new" / "dialog.jsonl", [rewritten_sample("deck-1_m_attempt_01")])
        service.import_path(str(base / "orig" / "dialog.jsonl"), "orig")
        service.create_rewrite_batch("b", "orig", rewritten_path=str(base / "new" / "dialog.jsonl"))
        gate = service.rewrite_record("b", "deck-1_m_attempt_01")["gate"]
        self.assertEqual(gate["decision"], "exclude")
        self.assertIn("无法比较", gate["reasons"][-1]["text"])
        self.assertEqual(service.rewrite_overview("b")["decisions"], {"include": 0, "review": 0, "exclude": 1})

    def test_word_diff_of_a_long_lightly_edited_line_is_fast_and_marks_words(self) -> None:
        words = [f"w{i % 700}" for i in range(2500)]
        before = " ".join(words)
        after = " ".join("new" if i % 25 == 0 else word for i, word in enumerate(words))
        started = time.perf_counter()
        diff = rewrite_review.word_diff(before, after)
        self.assertLess(time.perf_counter() - started, 3)  # was about 13 s for 10,000 characters
        self.assertEqual("".join(text for op, text in diff if op != "+"), before)
        self.assertEqual("".join(text for op, text in diff if op != "-"), after)
        self.assertGreater(len(diff), 100)  # word by word, not one block replaced
        self.assertTrue(all(not text[-1].isspace() for op, text in diff if op in "-+"))

    def test_token_lists_too_long_to_match_are_replaced_whole(self) -> None:
        before = " ".join(f"a{i}" for i in range(9000))
        after = " ".join(f"b{i}" for i in range(9000))
        started = time.perf_counter()
        diff = rewrite_review.word_diff(before, after)
        self.assertLess(time.perf_counter() - started, 3)
        self.assertEqual([op for op, _ in diff], ["-", "+"])


class StatisticsTest(ServiceCase):
    def test_an_episode_read_on_two_segments_is_one_review_in_the_statistics(self) -> None:
        service, base = self.service()
        service.import_path(str(make_sft_export(base / "sft", results_root=base / "results")), "S")
        by_run = {row["run_id"]: row for row in service.store.all_trajectories("S")}
        for run_id in ("t-002_k3_attempt_01_context_01", "t-002_k3_attempt_01_context_02"):
            service.save_review(by_run[run_id]["id"], {"human_status": "pass", "labels": ["overclaim"], "predicted_status": "fail"})
        stats = service.stats("S")
        self.assertEqual(stats["reviews"]["count"], 1)
        self.assertEqual(stats["reviews"]["blind_total"], 1)
        self.assertEqual([item["count"] for item in stats["reviews"]["labels"] if item["key"] == "overclaim"], [1])

    def test_jev_statistics_leave_out_summaries_from_older_question_versions(self) -> None:
        service, base = self.service()
        service.import_path(str(make_sft_export(base / "sft", results_root=base / "results")), "S")
        head = next(row for row in service.store.all_trajectories("S") if row["episode_head"])
        service.store.set_jev_summary(head["id"], {"version": "steps-v1", "flag_counts": {"filler": 3}})
        self.assertEqual(service.stats("S")["jev"]["analyzed"], 0)
        service.store.set_jev_summary(head["id"], {"version": STEP_VERSION, "flag_counts": {"polish": 1}})
        self.assertEqual(service.stats("S")["jev"]["analyzed"], 1)


class QueueTest(unittest.TestCase):
    def rows(self) -> list[dict]:
        rows = [practice_row(i, "pass", steps=20 + i) for i in range(12)]
        rows += [practice_row(20, "pass", steps=3), practice_row(21, "pass", flags=["test_edit"])]
        rows += [practice_row(30 + i, "fail", reason="ValueError") for i in range(5)]
        rows += [practice_row(40, "fail", reason="Timeout")]
        rows += [practice_row(50, "pass", task="k1"), practice_row(51, "fail", task="k1")]
        rows += [practice_row(60, "fail", task="k2", model="old"), practice_row(61, "fail", task="k2", model="new")]
        return rows

    def test_queue_is_never_longer_than_asked_and_keeps_pairs_whole(self) -> None:
        for size in (4, 5, 6, 7, 8, 10):
            with self.subTest(size=size):
                items = build_queue(self.rows(), {}, date="2026-11-11", seed_scope="c", size=size)["items"]
                self.assertLessEqual(len(items), size)
                ids = {item["trajectory"]["id"] for item in items}
                self.assertTrue(all(item["pair_with"] in ids for item in items if item["pair_with"]))

    def test_failure_reason_shows_one_count_per_type(self) -> None:
        items = build_queue(self.rows(), {}, date="2026-11-11", seed_scope="c", size=20)["items"]
        reasons = {item["reason"] for item in items if item["bucket"] == "failure" and "ValueError" in item["reason"]}
        self.assertEqual(len(reasons), 1)


class TaxonomyTest(unittest.TestCase):
    def test_jev_suggests_only_keys_a_review_accepts(self) -> None:
        """The two tables are written separately: a key in one and not the other would make
        Jev suggest an intervention or attribution that save_review rejects."""
        from trajectory_workbench import jev, practice

        self.assertEqual(set(jev.INTERVENTIONS), set(practice.INTERVENTIONS))
        self.assertEqual(set(jev.ATTRIBUTIONS), set(practice.ATTRIBUTIONS))


class RewardImportTest(unittest.TestCase):
    def test_a_critique_file_that_is_not_utf8_does_not_drop_the_deck(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / "work" / "deck-1"
            write_json(work / "reward.json", {"R": 0.5, "tier": 2, "task_id": "deck-1"})
            (work / "l3").mkdir()
            (work / "l3" / "score-raw.json").write_bytes(b'{"critique": "\xff\xfe"}')
            rows, errors = v4_reward.read_root(Path(temp), "/runs")
        self.assertEqual(errors, [])
        self.assertEqual([row["task_id"] for row in rows], ["deck-1"])


if __name__ == "__main__":
    unittest.main()
