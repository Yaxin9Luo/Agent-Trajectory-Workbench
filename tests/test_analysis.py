from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.adapters.moh_v1 import MohV1Adapter
from trajectory_workbench.analysis import TrajectoryAnalyzer
from trajectory_workbench.models import NormalizedRun
from trajectory_workbench.registry import Registry
from trajectory_workbench.service import WorkbenchService
from tests.fixtures import make_moh_run


class FakeAnalyzer:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = 0

    def analyze_run(self, normalized: NormalizedRun) -> dict:
        self.calls += 1
        return self.result


def _normalized_run() -> NormalizedRun:
    return NormalizedRun(
        adapter_id="moh-v1",
        source_path="/tmp/run",
        run_id="run",
        title="run",
        attempt_id="001",
        metrics={"tool_calls": 2, "tool_kinds": 2, "message_count": 5},
        timeline=[],
        messages=[],
        tools=[],
        tool_catalog=[],
        native_tool_surfaces=[],
        artifact_states=[],
        workbench=[],
        runtime={"classification": "completed"},
        model_prompt=None,
    )


class TrajectoryAnalyzerTest(unittest.TestCase):
    def test_analyze_run_extracts_score_choice_and_noul_answers(self) -> None:
        class FakeScore:
            score = 7.5
            confidence = 0.88
            probabilities = {"failed": 0.0, "poor": 0.1, "fair": 0.2, "good": 0.6, "excellent": 0.1}

        class FakeChoice:
            choice = "success"
            confidence = 0.9
            probabilities = {"success": 0.9, "failure": 0.1}

        class FakeNoul:
            noul = 0.15

        class FakeClient:
            def system_one(self, *, state, questions):
                names = list(questions)
                assert len(names) == 3
                assert names == ["health_score", "needs_human", "error_severity"]

                class Resp:
                    answers = {
                        "health_score": FakeScore(),
                        "needs_human": FakeNoul(),
                        "error_severity": FakeChoice(),
                    }

                return Resp()

        analyzer = TrajectoryAnalyzer(client=FakeClient())  # type: ignore[arg-type]
        result = analyzer.analyze_run(_normalized_run())

        self.assertEqual(result["run"]["health_score"]["score"], 7.5)
        self.assertEqual(result["run"]["health_score"]["confidence"], 0.88)
        self.assertEqual(result["run"]["needs_human"]["probability"], 0.15)
        self.assertEqual(result["run"]["error_severity"]["choice"], "success")
        self.assertEqual(result["tool_error_count"], 0)
        self.assertNotIn("failure_category", result["errors"])


class AnalysisServiceTest(unittest.TestCase):
    def test_analyze_run_is_cached_and_exposes_expected_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_moh_run(base / "run")
            service = WorkbenchService(Registry(base / "registry.json"))
            run_id = service.import_run(str(run), "Analysis")["id"]

            result = {
                "run": {
                    "health_score": {"score": 6.0, "confidence": 0.8, "probabilities": {}},
                    "needs_human": {"probability": 0.3},
                    "error_severity": {"choice": "recoverable", "confidence": 0.7, "probabilities": {}},
                },
                "errors": {"failure_category": {"choice": "validation", "confidence": 0.6, "probabilities": {}}},
                "tool_error_count": 1,
            }
            service.analyzer = FakeAnalyzer(result)

            first = service.analyze_run(run_id)
            second = service.analyze_run(run_id)

            self.assertEqual(first, result)
            self.assertIs(first, second)
            self.assertEqual(service.analyzer.calls, 1)

    def test_analyze_run_raises_for_unknown_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = WorkbenchService(Registry(Path(temp) / "registry.json"))
            with self.assertRaises(KeyError):
                service.analyze_run("missing")


if __name__ == "__main__":
    unittest.main()
