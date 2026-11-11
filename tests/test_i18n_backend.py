from __future__ import annotations

import json
import unittest
from pathlib import Path

from trajectory_workbench import harness, jev, practice, readiness, rewrite_review, signals


BACKEND = Path(__file__).parents[1] / "src" / "trajectory_workbench" / "web" / "locales" / "en" / "backend.mjs"


def has_cjk(text: str) -> bool:
    return any("一" <= char <= "鿿" for char in text)


class BackendLabelsTest(unittest.TestCase):
    def test_every_chinese_label_the_server_sends_has_english(self) -> None:
        """The interface translates server labels by their Chinese text: a new or reworded
        label with no entry would show in Chinese in the English interface."""
        table = BACKEND.read_text(encoding="utf-8")
        labels: set[str] = set()
        for group, label, _ in practice.LABELS.values():
            labels.update((group, label))
        for mapping in (
            signals.FLAG_LABELS,
            readiness.ISSUE_LABELS,
            practice.LEGACY_LABELS,
            practice.SEVERITIES,
            practice.INTERVENTIONS,
            practice.ATTRIBUTIONS,
            practice.BUCKETS,
            rewrite_review.ERROR_TYPES,
            rewrite_review.MISS_TYPES,
            harness.KIND_LABELS,
            harness.USE_LABELS,
            jev.STEP_FLAG_LABELS,
            {key: spec["label"] for key, spec in jev.STEP_NOULS.items()},
        ):
            labels.update(mapping.values())
        chinese = sorted(label for label in labels if has_cjk(label))
        self.assertGreater(len(chinese), 80)
        for label in chinese:
            with self.subTest(label=label):
                self.assertIn(json.dumps(label, ensure_ascii=False) + ":", table)


if __name__ == "__main__":
    unittest.main()
