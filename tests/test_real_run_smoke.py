from __future__ import annotations

import os
import unittest
from pathlib import Path

from trajectory_workbench.adapters.moh_v1 import MohV1Adapter


class RealRunSmokeTest(unittest.TestCase):
    def test_real_moh_runs_have_browsable_evidence(self) -> None:
        configured = os.environ.get("TRAJECTORY_WORKBENCH_REAL_RUNS")
        if not configured:
            self.skipTest("TRAJECTORY_WORKBENCH_REAL_RUNS is not set")

        paths = [Path(item) for item in configured.split(os.pathsep) if item]
        self.assertGreaterEqual(len(paths), 1)
        adapter = MohV1Adapter()
        for path in paths:
            with self.subTest(path=path):
                run = adapter.load(path)
                self.assertGreater(len(run.messages), 0)
                self.assertGreater(len(run.tools), 0)
                self.assertGreater(len(run.artifact_states), 0)
                self.assertGreaterEqual(len(run.workbench), 1)
                self.assertGreater(run.metrics["max_offset_ms"], 0)


if __name__ == "__main__":
    unittest.main()
