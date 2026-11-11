from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from trajectory_workbench.server import create_server
from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import Store
from tests.test_v4_reward import NoJev, make_scoring_root, make_trial


class MatrixTest(unittest.TestCase):
    """GET /api/matrix: tasks (rows) against collections (columns), matched by task_key."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.service = WorkbenchService(Store(self.base / "index.db"), analyzer=NoJev(), reviewer="tester")
        # A: rewarded (deck-a tier 4 R 0.75, deck-b masked), deck-c has no reward.
        runs_a = self.base / "a"
        for task in ("deck-a", "deck-b", "deck-c"):
            make_trial(runs_a / f"{task}__1", task)
        # B: no rewards; deck-a twice, deck-c missing.
        runs_b = self.base / "b"
        make_trial(runs_b / "deck-a__1", "deck-a")
        make_trial(runs_b / "deck-a__2", "deck-a")
        make_trial(runs_b / "deck-b__1", "deck-b")
        # Shares no task with anyone.
        make_trial(self.base / "solo" / "solo-task__1", "solo-task")
        self.service.import_path(str(runs_a), "A")
        self.service.import_path(str(runs_b), "B, retrained")
        self.service.import_path(str(self.base / "solo"), "Solo")
        self.service.import_v4_reward(str(make_scoring_root(self.base / "score")), "v1", str(runs_a))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def task(self, payload, key):
        return next(task for task in payload["tasks"] if task["task_key"] == key)

    def test_default_columns_are_the_collections_that_share_tasks(self) -> None:
        payload = self.service.matrix()
        self.assertEqual(payload["rule"], "shared")
        self.assertEqual(sorted(column["name"] for column in payload["collections"]), ["A", "B, retrained"])
        self.assertEqual(payload["shared_tasks"], 2)

    def test_cells_attempts_and_rewards(self) -> None:
        payload = self.service.matrix(["A", "B, retrained"])
        self.assertEqual(payload["rule"], "given")
        a, b = payload["collections"]
        self.assertEqual((a["name"], a["episodes"], a["tasks"], a["unkeyed"]), ("A", 3, 3, 0))
        self.assertEqual((a["rewarded"], a["scored"], a["masked"], a["r_mean"]), (2, 1, 1, 0.75))
        self.assertEqual(a["tiers"], {"0": 0, "1": 0, "2": 0, "3": 0, "4": 1})
        self.assertEqual((b["episodes"], b["tasks"], b["rewarded"], b["scored"], b["r_mean"]), (3, 2, 0, 0, None))
        self.assertEqual([task["task_key"] for task in payload["tasks"]], ["id:deck-a", "id:deck-b", "id:deck-c"])

        deck_a = self.task(payload, "id:deck-a")
        self.assertEqual(deck_a["task_id"], "deck-a")
        self.assertTrue(deck_a["title"])
        in_a, in_b = deck_a["cells"]
        self.assertEqual(len(in_a), 1)
        reward = in_a[0]["reward"]
        self.assertEqual(
            (reward["R"], reward["tier"], reward["masked"], reward["label"], reward["task_id"], reward["still"]),
            (0.75, 4, False, "v1", "deck-a", "check/shots/deck-a/1920x1080/01.png"),
        )
        self.assertLessEqual({"id", "steps", "tool_errors", "duration_ms", "outcome_status"}, set(in_a[0]))
        # Two attempts in one collection: both kept, no reward, verifier outcome present.
        self.assertEqual(len(in_b), 2)
        self.assertEqual(len({attempt["id"] for attempt in in_b}), 2)
        self.assertTrue(all(attempt["reward"] is None and attempt["outcome_status"] == "pass" for attempt in in_b))
        # Only one scored cell: no spread.
        self.assertIsNone(deck_a["spread"])
        self.assertFalse(deck_a["tiers_differ"])

        deck_b = self.task(payload, "id:deck-b")
        masked = deck_b["cells"][0][0]["reward"]
        self.assertTrue(masked["masked"])
        self.assertIsNone(masked["R"])
        # A task missing from a collection gives an empty cell.
        self.assertEqual(self.task(payload, "id:deck-c")["cells"][1], [])

    def test_spread_and_tier_difference_compare_the_best_scored_attempt(self) -> None:
        # Score B's deck-a: one attempt only (the reward's runs root is that attempt's dir).
        runs_b = self.base / "b"
        score_b = self.base / "score-b"
        reward = json.loads((self.base / "score" / "work" / "deck-a" / "reward.json").read_text())
        reward.update(R=0.2, tier=2, tier_name="L1 major")
        target = score_b / "work" / "deck-a" / "reward.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(reward))
        self.service.import_v4_reward(str(score_b), "v1-b", str(runs_b / "deck-a__2"))
        deck_a = self.task(self.service.matrix(["A", "B, retrained"]), "id:deck-a")
        rewards = [attempt["reward"] for attempt in deck_a["cells"][1]]
        self.assertEqual(sorted(r["R"] for r in rewards if r), [0.2])
        self.assertEqual(rewards.count(None), 1)
        self.assertEqual(deck_a["spread"], 0.55)
        self.assertTrue(deck_a["tiers_differ"])

    def test_rows_without_a_task_key_are_counted_not_matched(self) -> None:
        store = self.service.store
        with store._locked():
            store._db.execute("UPDATE trajectories SET task_key = NULL WHERE collection = 'A' AND task_id = 'deck-c'")
            store._db.commit()
        payload = self.service.matrix(["A", "B, retrained"])
        a = payload["collections"][0]
        self.assertEqual((a["episodes"], a["unkeyed"], a["tasks"]), (3, 1, 2))
        self.assertNotIn("id:deck-c", [task["task_key"] for task in payload["tasks"]])
        # Its reward still counts for the column (it is an episode of A).
        self.assertEqual(a["rewarded"], 2)

    def test_unknown_collection_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            self.service.matrix(["A", "nope"])

    def test_http_endpoint(self) -> None:
        server = create_server("127.0.0.1", 0, self.base / "index.db", service=self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/api/matrix"
        try:
            # A comma inside a name travels encoded; the separators are literal commas.
            names = ",".join(urllib.parse.quote(name, safe="") for name in ("B, retrained", "A"))
            with urllib.request.urlopen(url + "?collections=" + names, timeout=5) as response:
                payload = json.loads(response.read())
            self.assertEqual([column["name"] for column in payload["collections"]], ["B, retrained", "A"])
            self.assertEqual(len(self.task(payload, "id:deck-a")["cells"][0]), 2)
            with urllib.request.urlopen(url, timeout=5) as response:
                self.assertEqual(json.loads(response.read())["rule"], "shared")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(url + "?collections=A,nope", timeout=5)
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
