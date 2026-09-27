from __future__ import annotations

import os
import unittest
from pathlib import Path

from trajectory_workbench import signals
from trajectory_workbench.adapters import discover


class RealRunSmokeTest(unittest.TestCase):
    """Opt-in: TRAJECTORY_WORKBENCH_REAL_RUNS=/path/one:/path/two (files or directories)."""

    def test_real_sources_load_and_reload(self) -> None:
        configured = os.environ.get("TRAJECTORY_WORKBENCH_REAL_RUNS")
        if not configured:
            self.skipTest("TRAJECTORY_WORKBENCH_REAL_RUNS is not set")

        roots = [Path(item) for item in configured.split(os.pathsep) if item]
        loaded = 0
        for root in roots:
            for adapter, source in discover(root):
                for locator, run in adapter.iter_runs(source):
                    with self.subTest(source=str(source), run=run.run_id):
                        self.assertGreater(len(run.messages), 0)
                        steps = [message["step"] for message in run.messages]
                        self.assertEqual(steps, list(range(1, len(steps) + 1)))
                        paired = sum(1 for tool in run.tools if tool["result"] is not None)
                        # Only an interrupted final call may lack its result.
                        self.assertGreaterEqual(paired, len(run.tools) - 1)
                        signals.compute(run)
                        again = adapter.load(source, locator)
                        self.assertEqual(again.run_id, run.run_id)
                        self.assertEqual(len(again.messages), len(run.messages))
                        if adapter.adapter_id == "moh-v1":
                            self.assertGreater(len(run.artifact_states), 0)
                        loaded += 1
        self.assertGreater(loaded, 0)


if __name__ == "__main__":
    unittest.main()
