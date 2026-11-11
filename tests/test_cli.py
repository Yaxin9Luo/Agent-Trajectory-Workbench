from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fixtures import make_moh_run


ROOT = Path(__file__).parents[1]


class CliTest(unittest.TestCase):
    """Runs `python -m trajectory_workbench` as a subprocess: main() reads sys.argv itself."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = self.base / "index.db"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items() if key != "TYPESAFE_API_KEY"}
        env.update(PYTHONPATH=str(ROOT / "src"), HOME=str(self.base), TRAJECTORY_WORKBENCH_REVIEWER="t")
        command = [
            sys.executable, "-m", "trajectory_workbench",
            "--db", str(self.db),
            "--registry", str(self.base / "no-registry.json"),  # never import a legacy registry
            *args,
        ]
        return subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True, timeout=120)

    def counts(self) -> dict[str, int]:
        with sqlite3.connect(self.db) as db:
            return dict(db.execute("SELECT collection, COUNT(*) FROM trajectories GROUP BY collection").fetchall())

    def test_list_on_an_empty_index(self) -> None:
        result = self.cli("list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["collections"], [])

    def test_remove_collection_needs_yes(self) -> None:
        for name in ("keep", "drop"):
            result = self.cli("import", str(make_moh_run(self.base / name)), "--collection", name)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.counts(), {"keep": 1, "drop": 1})

        dry_run = self.cli("remove-collection", "drop")
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertEqual(self.counts(), {"keep": 1, "drop": 1})

        removed = self.cli("remove-collection", "drop", "--yes")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(self.counts(), {"keep": 1})
        self.assertTrue((self.base / "drop" / "events.jsonl").is_file(), "source files must stay")

    def test_remove_unknown_collection_fails_and_deletes_nothing(self) -> None:
        self.assertEqual(self.cli("import", str(make_moh_run(self.base / "keep")), "--collection", "keep").returncode, 0)
        result = self.cli("remove-collection", "missing", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.counts(), {"keep": 1})


if __name__ == "__main__":
    unittest.main()
