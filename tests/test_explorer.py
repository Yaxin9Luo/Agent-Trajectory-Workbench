from __future__ import annotations

import unittest

from trajectory_workbench.explorer import error_template, summarize_collection


class ExplorerTest(unittest.TestCase):
    def test_templates_mask_paths_numbers_and_strings(self) -> None:
        first = error_template("Exit code 1\nls: cannot access '/tmp/ref/a/b': No such file or directory")
        second = error_template("Exit code 2\nls: cannot access '/work/x': No such file or directory")
        self.assertEqual(first, second)
        self.assertEqual(first, "ls: cannot access <str>: No such file or directory")
        self.assertEqual(
            error_template("<tool_use_error>File does not exist. Note: your current working directory is /workspace/output/moh-0123456789abcdef0123456789abcdef/runs/x.</tool_use_error>"),
            "File does not exist. Note: your current working directory is <path>",
        )
        self.assertEqual(error_template('Traceback (most recent call last):\n  File "a.py", line 3\nKeyError: 7'), "KeyError: <n>")
        # Output printed before the failure is skipped in favour of the error line.
        self.assertEqual(error_template("Exit code 1\ntotal 72\nbuild ok\nnpm ERR! missing script: test"), "npm ERR! missing script: test")
        self.assertEqual(error_template('Script completed\n{"content":[{"type":"text","text":"Browser Use rejected this action"}],"isError":true}'), "Browser Use rejected this action")

    def test_collection_summary_counts_trajectories_once_per_template(self) -> None:
        rows = [
            {"id": "a", "outcome_status": "pass", "tool_stats": {"Bash": [4, 1]},
             "error_templates": [{"tool": "Bash", "template": "boom <n>", "step": 3}, {"tool": "Bash", "template": "boom <n>", "step": 5}]},
            {"id": "b", "outcome_status": "fail", "tool_stats": {"Bash": [2, 1], "Read": [1, 0]},
             "error_templates": [{"tool": "Bash", "template": "boom <n>", "step": 2}]},
        ]
        summary = summarize_collection(rows)
        bash = next(item for item in summary["tools"] if item["tool"] == "Bash")
        self.assertEqual((bash["calls"], bash["errors"], bash["trajectories"], bash["calls_per_trajectory"]), (6, 2, 2, 3.0))
        [template] = summary["templates"]
        self.assertEqual((template["count"], template["trajectories"], template["pass"], template["fail"], template["share"]), (3, 2, 1, 1, 1.0))
        self.assertEqual([example["id"] for example in template["examples"]], ["a", "b"])


class ExploreServiceTest(unittest.TestCase):
    def test_all_collections_against_one_keeps_both_sides(self) -> None:
        import tempfile
        from pathlib import Path

        from trajectory_workbench.service import WorkbenchService
        from trajectory_workbench.store import Store
        from tests.fixtures import make_claude_session, make_sft_export

        class NoJev:
            def available(self):
                return False

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            service = WorkbenchService(Store(base / "index.db"), analyzer=NoJev(), reviewer="t")
            service.import_path(str(make_sft_export(base / "sft")), "S")
            service.import_path(str(make_claude_session(base / "c.jsonl")), "C")
            result = service.explore(["*", "C"])
        self.assertEqual([item["collection"] for item in result["collections"]], [None, "C"])
        self.assertEqual([item["trajectories"] for item in result["collections"]], [5, 1])
        self.assertEqual(result["collections"][0]["stale"], 0)


if __name__ == "__main__":
    unittest.main()
