from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import Store
from tests.fixtures import make_harbor_trial, write_json


class NoJev:
    def available(self) -> bool:
        return False


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def make_trial(root: Path, task: str) -> Path:
    trial = make_harbor_trial(root, 1.0)
    result = json.loads((trial / "result.json").read_text())
    result["task_name"] = task
    write_json(trial / "result.json", result)
    return trial


def make_scoring_root(root: Path) -> Path:
    """Synthetic scoring root: one tier-4 deck with a failed L1 item, one masked deck."""
    good = root / "work" / "deck-a"
    write_json(good / "reward.json", {
        "artifact_id": "deck-a", "rubric": "slides-v4", "scorer": "test", "slides": 2,
        "ledger": [
            {"layer": "L1", "id": "craft.text_overflow", "pass": False, "severity": "minor", "slides": [2],
             "evidence": {"note": "spills", "viewport": "1920x1080", "boxes": [{"box": [10, 20, 30, 40], "text": "x", "role": "target"}]}},
            {"layer": "L1", "id": "craft.ok", "pass": True, "severity": "major", "slides": [1]},
            {"layer": "L2", "id": "watch", "pass": False, "severity": "monitor", "slides": [1]},
        ],
        "derived": ["d.one"], "derived_items": [{"layer": "L3", "id": "d.one", "pass": False, "severity": "normal", "evidence": "plain"}],
        "unknown": [], "masked": False, "tier": 4, "tier_name": "clean", "m": 1.0, "a_L3": 0.5, "q": 0.9, "R": 0.75,
        "eval_total": 1, "seconds": 12.5, "timing": {"L0-L1": 5.0, "stages": {"render": 2.0}, "L2": 3.0, "L3": 4.0},
        "failed_stage": None, "deck_fault": None, "env": {"platform": "test"},
    })
    for slide in ("01", "02"):
        shot = good / "check" / "shots" / "deck-a" / "1920x1080" / f"{slide}.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        shot.write_bytes(PNG)
    write_json(good / "l3" / "score-raw.json", {"critique": "fine"})
    write_json(root / "work" / "deck-b" / "reward.json", {
        "artifact_id": "deck-b", "rubric": "slides-v4", "ledger": [], "masked": True, "tier": None, "R": None,
        "unknown": [{"reason": "render failed", "stage": "L0-L1", "error": "timeout"}],
        "failed_stage": "L0-L1", "timing": {"L0-L1": 60.0, "retried": True}, "seconds": 61.0,
    })
    write_json(root / "work" / "_cache" / "reward.json", {"artifact_id": "_cache"})
    (root / "secret.png").write_bytes(PNG)
    return root


class V4RewardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.service = WorkbenchService(Store(self.base / "index.db"), analyzer=NoJev(), reviewer="tester")
        self.runs = self.base / "runs"
        make_trial(self.runs / "deck-a__1", "deck-a")
        make_trial(self.runs / "deck-b__1", "deck-b")
        # Same task id, other runs root: must not match.
        make_trial(self.base / "other" / "deck-a__1", "deck-a")
        # Same prefix as a string but not a path prefix ("runs2" vs "runs").
        make_trial(self.base / "runs2" / "deck-a__1", "deck-a")
        self.score = make_scoring_root(self.base / "score")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def items(self, **filters):
        return self.service.list_trajectories(**filters)["items"]

    def by_path(self, items):
        return {Path(item["path"]).relative_to(self.base).as_posix(): item for item in items if item.get("group_role") in (None, "main")}

    def test_import_matches_by_task_id_and_path_prefix(self) -> None:
        self.service.import_path(str(self.runs), "Runs")
        self.service.import_path(str(self.base / "other"), "Other")
        self.service.import_path(str(self.base / "runs2"), "Runs2")
        report = self.service.import_v4_reward(str(self.score), "v1", str(self.runs) + "/")
        self.assertEqual((report["decks"], report["masked"], report["matched_decks"]), (2, 1, 2))
        rows = self.by_path(self.items(limit=100))
        self.assertEqual(rows["runs/deck-a__1"]["v4_r"], 0.75)
        self.assertEqual(rows["runs/deck-a__1"]["v4_tier"], 4)
        self.assertIsNone(rows["runs/deck-b__1"]["v4_r"])
        self.assertTrue(rows["runs/deck-b__1"]["v4_masked"])
        self.assertTrue(rows["runs/deck-b__1"]["v4_errors"])
        self.assertIsNone(rows["other/deck-a__1"]["v4_label"])
        self.assertIsNone(rows["runs2/deck-a__1"]["v4_label"])

        detail = self.service.get_trajectory(rows["runs/deck-a__1"]["id"])["v4_reward"]
        self.assertEqual([item["id"] for item in detail["failed"]], ["craft.text_overflow"])
        self.assertEqual([item["id"] for item in detail["derived_items"]], ["d.one"])
        self.assertEqual(detail["stills"]["1920x1080"], ["check/shots/deck-a/1920x1080/01.png", "check/shots/deck-a/1920x1080/02.png"])
        self.assertEqual(detail["critique"], "fine")
        self.assertEqual(detail["timing"]["L2"], 3.0)
        masked = self.service.get_trajectory(rows["runs/deck-b__1"]["id"])["v4_reward"]
        self.assertIsNone(masked["R"])
        self.assertTrue(masked["masked"])
        self.assertEqual(masked["unknown"][0]["reason"], "render failed")

    def test_filters_and_sorting(self) -> None:
        self.service.import_path(str(self.runs), "Runs")
        self.service.import_v4_reward(str(self.score), "v1", str(self.runs))
        tasks = lambda items: [item["task_id"] for item in items if item.get("group_role") in (None, "main")]  # noqa: E731
        self.assertEqual(tasks(self.items(v4_tier="4")), ["deck-a"])
        self.assertEqual(tasks(self.items(v4_tier="masked")), ["deck-b"])
        self.assertEqual(tasks(self.items(v4_errors=True)), ["deck-b"])
        self.assertEqual(tasks(self.items(v4_errors=False)), ["deck-a"])
        self.assertEqual(tasks(self.items(v4_r_min=0.7, v4_r_max=0.8)), ["deck-a"])
        self.assertEqual(tasks(self.items(v4_r_min=0.9)), [])
        self.assertEqual(tasks(self.items(sort="v4_r"))[0], "deck-a")
        self.assertEqual(tasks(self.items(sort="v4_seconds"))[0], "deck-b")
        self.assertEqual(tasks(self.items(episodes=True, v4_tier="4")), ["deck-a"])

    def test_survives_trajectory_reimport_with_new_ids_and_is_idempotent(self) -> None:
        self.service.import_path(str(self.runs), "Runs")
        trajectory = self.by_path(self.items())["runs/deck-a__1"]
        self.service.save_review(trajectory["id"], {"human_status": "pass"})
        self.service.import_v4_reward(str(self.score), "v1", str(self.runs))
        # Another session removes the collection and re-imports it under a new name from
        # a moved copy: new ids, new collection, same task ids.
        self.service.store.remove_collection("Runs")
        moved = self.base / "runs-moved"
        self.runs.rename(moved)
        self.service.import_path(str(moved), "Runs renamed")
        self.assertIsNone(self.by_path(self.items())["runs-moved/deck-a__1"]["v4_label"])
        again = self.service.import_v4_reward(str(self.score), "v1", str(moved))
        self.assertEqual(again["replaced"], 2)
        fresh = self.by_path(self.items())["runs-moved/deck-a__1"]
        self.assertNotEqual(fresh["id"], trajectory["id"])
        self.assertEqual(fresh["v4_r"], 0.75)
        self.assertEqual(len(self.service.store.v4_labels()), 1)
        # A second label is its own import; re-importing v1 leaves it alone.
        self.service.import_v4_reward(str(self.score), "v2", str(moved))
        self.service.import_v4_reward(str(self.score), "v1", str(moved))
        self.assertEqual(sorted(item["label"] for item in self.service.store.v4_labels()), ["v1", "v2"])
        self.assertEqual(self.service.store.query_trajectories(collection="Runs renamed", limit=None)[0], len(self.items(limit=1000)))

    def test_reward_import_never_touches_trajectories_or_reviews(self) -> None:
        self.service.import_path(str(self.runs), "Runs")
        before = self.service.store.all_trajectories()
        trajectory = before[0]
        self.service.save_review(trajectory["id"], {"human_status": "fail"})
        self.service.import_v4_reward(str(self.score), "v1", str(self.runs))
        self.service.import_v4_reward(str(self.score), "v1", str(self.runs))
        self.assertEqual(self.service.store.all_trajectories(), before)
        self.assertEqual(self.service.store.get_review(trajectory["id"], "tester")["human_status"], "fail")

    def test_stills_endpoint_only_serves_the_reward_work_dir(self) -> None:
        self.service.import_v4_reward(str(self.score), "v1", str(self.runs))
        path = self.service.resolve_v4_still("v1", "deck-a", "check/shots/deck-a/1920x1080/01.png")
        self.assertEqual(path.read_bytes(), PNG)
        for bad in ("../../secret.png", "../deck-b/reward.json", str(self.score / "secret.png"), "", "reward.json"):
            with self.subTest(bad=bad), self.assertRaises(PermissionError):
                self.service.resolve_v4_still("v1", "deck-a", bad)
        link = self.score / "work" / "deck-a" / "link.png"
        link.symlink_to(self.score / "secret.png")
        with self.assertRaises(PermissionError):
            self.service.resolve_v4_still("v1", "deck-a", "link.png")
        with self.assertRaises(KeyError):
            self.service.resolve_v4_still("nope", "deck-a", "check/shots/deck-a/1920x1080/01.png")
        with self.assertRaises(FileNotFoundError):
            self.service.resolve_v4_still("v1", "deck-a", "check/shots/deck-a/1920x1080/09.png")

    def test_http_import_and_stills_endpoint(self) -> None:
        import threading
        import urllib.error
        import urllib.parse
        import urllib.request

        from trajectory_workbench.server import create_server

        server = create_server("127.0.0.1", 0, self.base / "index.db", service=self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            body = json.dumps({"root": str(self.score), "label": "v1", "runs_root": str(self.runs)}).encode()
            request = urllib.request.Request(url + "/api/v4-rewards", data=body, method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(json.loads(response.read())["decks"], 2)
            with urllib.request.urlopen(url + "/api/v4-rewards", timeout=5) as response:
                self.assertEqual(json.loads(response.read())["labels"][0]["label"], "v1")
            good = "/api/v4-rewards/v1/deck-a/still?path=" + urllib.parse.quote("check/shots/deck-a/1920x1080/02.png")
            with urllib.request.urlopen(url + good, timeout=5) as response:
                self.assertEqual((response.status, response.headers["Content-Type"]), (200, "image/png"))
            for bad in ("../../secret.png", str(self.score / "secret.png")):
                with self.subTest(bad=bad), self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(url + "/api/v4-rewards/v1/deck-a/still?path=" + urllib.parse.quote(bad), timeout=5)
                self.assertEqual(caught.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(url + "/api/trajectories?v4_r_min=abc", timeout=5)
            self.assertEqual(caught.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_import_validates_arguments(self) -> None:
        with self.assertRaises(ValueError):
            self.service.import_v4_reward("relative", "v1", str(self.runs))
        with self.assertRaises(ValueError):
            self.service.import_v4_reward(str(self.score), "", str(self.runs))
        with self.assertRaises(ValueError):
            self.service.import_v4_reward(str(self.score), "v1", "relative/runs")
        with self.assertRaises(ValueError):
            self.service.import_v4_reward(str(self.base), "v1", str(self.runs))


if __name__ == "__main__":
    unittest.main()
