from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.service import BATCH_STOP_AFTER, WorkbenchService
from trajectory_workbench.store import Store


class FailedWriteTest(unittest.TestCase):
    def test_a_write_that_times_out_does_not_break_later_writes(self) -> None:
        # Another process holds the write lock, then commits twice while this
        # connection is between writes: the case that stopped a batch on 2026-09-29.
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.db"
            store = Store(path)
            store._db.execute("PRAGMA busy_timeout = 200")
            other = sqlite3.connect(path, timeout=1)
            other.execute("BEGIN IMMEDIATE")
            other.execute("CREATE TABLE x(v)")
            with self.assertRaises(sqlite3.OperationalError):
                store.put_jev("t1", "steps", "v", None, "m", 10, {"k": 1})
            self.assertFalse(store._db.in_transaction)
            other.commit()
            store.get_jev("t1", "steps", "v", None)
            other.execute("INSERT INTO x VALUES (1)")
            other.commit()
            store.put_jev("t2", "steps", "v", None, "m", 10, {"k": 2})
            self.assertEqual(store.get_jev("t2", "steps", "v", None), {"k": 2})
            other.close()
            store.close()


class BatchStopTest(unittest.TestCase):
    def test_batch_stops_after_failures_in_a_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = WorkbenchService(Store(Path(temp) / "index.db"), reviewer="t")
            calls = []

            def analyze(trajectory_id: str) -> dict:
                calls.append(trajectory_id)
                if trajectory_id == "ok":
                    return {"steps": {"input_tokens": 5}}
                raise RuntimeError("database is locked")

            service.analyze = analyze
            rows = [{"id": "ok", "title": "a"}, *({"id": f"bad{i}", "title": "b"} for i in range(10))]
            result = service.run_batch(rows)
            service.store.close()
        self.assertEqual(len(calls), 1 + BATCH_STOP_AFTER)
        self.assertEqual((result["analyzed"], len(result["failed"]), result["not_run"]), (1, BATCH_STOP_AFTER, 10 - BATCH_STOP_AFTER))
        self.assertIn("database is locked", result["stopped"])
        self.assertEqual(result["input_tokens"], 5)


if __name__ == "__main__":
    unittest.main()
