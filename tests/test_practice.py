from __future__ import annotations

import unittest

from trajectory_workbench.practice import build_queue, collection_stats, taxonomy


def row(index, status, *, steps=20, task=None, model="m1", collection="c", flags=(), role="main", reason=None):
    return {
        "id": f"t{index}",
        "title": f"t{index}",
        "outcome_status": status,
        "outcome_reason": reason,
        "steps": steps,
        "task_key": task,
        "model": model,
        "collection": collection,
        "flags": list(flags),
        "group_role": role,
        "jev_summary": None,
    }


class QueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [row(i, "pass", steps=20 + i) for i in range(12)]
        self.rows.append(row(20, "pass", steps=3))  # suspiciously short success
        self.rows.append(row(21, "pass", flags=["test_edit"]))
        self.rows += [row(30 + i, "fail", reason="ValueError") for i in range(5)]
        self.rows += [row(40, "fail", reason="Timeout")]
        self.rows += [row(50, "pass", task="k1"), row(51, "fail", task="k1")]
        self.rows += [row(60, "fail", task="k2", model="old"), row(61, "fail", task="k2", model="new")]
        self.rows.append(row(70, "unknown", role="subagent"))

    def test_mix_is_stable_and_explains_itself(self) -> None:
        first = build_queue(self.rows, {}, date="2026-09-27", seed_scope="c", size=20)
        again = build_queue(self.rows, {}, date="2026-09-27", seed_scope="c", size=20)
        other_day = build_queue(self.rows, {}, date="2026-09-28", seed_scope="c", size=20)
        ids = [item["trajectory"]["id"] for item in first["items"]]
        self.assertEqual(ids, [item["trajectory"]["id"] for item in again["items"]])
        self.assertNotEqual(ids, [item["trajectory"]["id"] for item in other_day["items"]])
        buckets = {item["bucket"] for item in first["items"]}
        self.assertTrue({"suspicious_pass", "failure", "pair", "checkpoint"} <= buckets)
        suspicious = {item["trajectory"]["id"]: item["reason"] for item in first["items"] if item["bucket"] == "suspicious_pass"}
        self.assertIn("t20", suspicious)
        self.assertIn("P10", suspicious["t20"])
        self.assertIn("t21", suspicious)
        failures = [item for item in first["items"] if item["bucket"] == "failure"]
        # Round-robin over failure types: the rare type is not crowded out.
        self.assertIn("Timeout", " ".join(item["reason"] for item in failures))
        pair = [item for item in first["items"] if item["bucket"] == "pair"]
        self.assertEqual({item["trajectory"]["id"] for item in pair}, {"t50", "t51"})
        self.assertNotIn("t70", ids)  # subagents are read from their parent's group
        self.assertEqual(len(set(ids)), len(ids))

    def test_reviews_before_today_leave_the_pool(self) -> None:
        reviews = {"t20": {"updated_at": "2026-09-20T10:00:00"}, "t21": {"updated_at": "2026-09-27T09:00:00"}}
        queue = build_queue(self.rows, reviews, date="2026-09-27", seed_scope="c", size=20)
        ids = {item["trajectory"]["id"]: item for item in queue["items"]}
        self.assertNotIn("t20", ids)
        self.assertTrue(ids["t21"]["done"])


class StatsTest(unittest.TestCase):
    def test_grader_gap_and_blind_accuracy(self) -> None:
        rows = [row(1, "pass", flags=["no_verify"]), row(2, "pass"), row(3, "fail", flags=["no_verify"]), row(4, "fail")]
        reviews = [
            {"trajectory_id": "t1", "outcome_status": "pass", "human_status": "fail", "predicted_status": "fail", "labels": ["reward_hack"], "turning_step": 9, "steps": 10, "intervention": "reward", "attribution": "grader", "updated_at": "2026-09-27T01:00:00",
             "label_details": {"reward_hack": {"severity": "high", "decisive": True}}},
            {"trajectory_id": "t2", "outcome_status": "pass", "human_status": "pass", "predicted_status": "pass", "labels": [], "updated_at": "2026-09-27T02:00:00"},
            {"trajectory_id": "t3", "outcome_status": "fail", "human_status": "pass", "predicted_status": "pass", "labels": ["grader_error"], "updated_at": "2026-09-26T02:00:00"},
        ]
        stats = collection_stats(rows, reviews)
        self.assertEqual(stats["total"], 4)
        no_verify = next(item for item in stats["signals"] if item["key"] == "no_verify")
        self.assertEqual((no_verify["count"], no_verify["pass_share"], no_verify["fail_share"]), (2, 0.5, 0.5))
        self.assertEqual(stats["reviews"]["inflated"], 1)
        self.assertEqual(stats["reviews"]["deflated"], 1)
        self.assertAlmostEqual(stats["reviews"]["disagreement_rate"], 0.667)
        self.assertEqual(stats["reviews"]["inflated_rate"], 0.5)
        # Predictions guess the grader: only t2 matches it.
        self.assertAlmostEqual(stats["reviews"]["blind_accuracy"], 0.333)
        self.assertEqual(stats["reviews"]["turning_histogram"][9], 1)
        self.assertEqual(stats["reviews"]["confusion"]["pass"], {"fail": 1, "pass": 1})
        labels = {item["key"]: item for item in stats["reviews"]["labels"]}
        self.assertEqual((labels["reward_hack"]["decisive"], labels["reward_hack"]["severe"]), (1, 1))
        # Reviews written before the grader label was split keep a readable name.
        self.assertEqual(labels["grader_error"]["label"], "评分器误判")

    def test_taxonomy_lists_harness_interventions(self) -> None:
        data = taxonomy()
        self.assertTrue({"skill", "mcp", "instruction", "hook"} <= set(data["interventions"]))
        keys = {item["key"] for item in data["labels"]}
        self.assertIn("reward_hack", keys)
        self.assertTrue({"overclaim", "ground_truth_access", "premature_stop", "grader_lenient", "grader_strict", "honest_report"} <= keys)
        self.assertIn("harness", data["attributions"])


if __name__ == "__main__":
    unittest.main()
