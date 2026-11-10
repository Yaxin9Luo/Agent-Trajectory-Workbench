"""Regressions from the 2026-11-10 project audit: each test failed before its fix."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from typesafe_sdk import TypeSafeAPIConnectionError

from trajectory_workbench import explorer, exporter
from trajectory_workbench.adapters import AtifAdapter, ClaudeCodeAdapter, TraceLabTrialAdapter
from trajectory_workbench.jev import STEP_VERSION, JevAnalyzer
from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import Store
from tests.fixtures import atif_document, chat_sample, make_claude_session, make_tracelab_trial, write_json, write_jsonl
from tests.test_jev import FakeClient, sample_run


class DownClient:
    async def system_one(self, state, questions):
        raise TypeSafeAPIConnectionError("Connection error: proxy down")

    async def aclose(self):
        pass


class NoUsageClient(FakeClient):
    async def system_one(self, state, questions):
        response = await super().system_one(state, questions)
        return SimpleNamespace(model=response.model, usage=SimpleNamespace(input_tokens=None), answers=response.answers)


class AdapterRegressionTest(unittest.TestCase):
    def test_claude_session_starting_with_a_summary_row_keeps_its_episode_keys(self) -> None:
        common = {"sessionId": "sess-9", "cwd": "/w", "version": "2.0"}
        rows = [
            {"type": "summary", "summary": "Earlier work", "leafUuid": "abc"},
            {**common, "type": "user", "uuid": "u1", "timestamp": "2026-09-01T10:00:00Z", "message": {"role": "user", "content": "Do the thing"}},
            {**common, "type": "assistant", "uuid": "a1", "timestamp": "2026-09-01T10:00:01Z",
             "message": {"id": "m1", "role": "assistant", "model": "c", "content": [{"type": "text", "text": "ok"}]}},
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "s.jsonl"
            write_jsonl(path, rows)
            run = ClaudeCodeAdapter().load(path, None)
        self.assertEqual(run.meta.get("session_id"), "sess-9")
        self.assertEqual(run.meta.get("self_key"), "claude:sess-9")

    def test_tracelab_samples_with_the_same_id_get_distinct_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            trial = make_tracelab_trial(Path(temp) / "trial", resolved=True)
            write_jsonl(trial / "dialog.jsonl", [chat_sample("x-9_k3_attempt_01", "first"), chat_sample("x-9_k3_attempt_01", "second")])
            keys = [locator["key"] for locator, _ in TraceLabTrialAdapter().iter_runs(trial)]
        self.assertEqual(len(set(keys)), 2)
        self.assertEqual(keys[0], "x-9_k3_attempt_01")

    def test_atif_image_path_is_listed_once(self) -> None:
        document = atif_document()
        document["steps"][1]["message"] = [
            {"type": "text", "text": "look"},
            {"type": "image", "source": {"media_type": "image/png", "path": "/abs/shot.png"}},
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "trajectory.json"
            write_json(path, document)
            run = next(run for _, run in AtifAdapter().iter_runs(path))
        self.assertEqual([image["path"] for image in run.messages[1]["images"]], ["/abs/shot.png"])


class AnalysisRegressionTest(unittest.TestCase):
    def test_a_step_scan_with_failed_requests_is_not_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            service = WorkbenchService(Store(base / "index.db"), analyzer=JevAnalyzer(client_factory=DownClient), reviewer="t")
            self.addCleanup(service.store.close)
            service.import_path(str(make_claude_session(base / "s.jsonl")), "Demo")
            row = service.store.all_trajectories("Demo")[0]
            try:
                service.analyze(row["id"])
            except ValueError:
                pass
            self.assertIsNone(service.store.get_jev(row["id"], "steps", STEP_VERSION, row["fingerprint"]))
            self.assertTrue(any(item["id"] == row["id"] for item in service.batch_rows("Demo", 100)))
            service.analyzer = JevAnalyzer(client_factory=FakeClient)
            self.assertFalse(service.analyze(row["id"])["steps"]["errors"])

    def test_a_partly_failed_step_scan_is_kept_and_not_run_again(self) -> None:
        clients: list[FakeClient] = []

        def factory() -> FakeClient:
            clients.append(FakeClient(fail_on="pytest"))
            return clients[-1]

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            service = WorkbenchService(Store(base / "index.db"), analyzer=JevAnalyzer(client_factory=factory), reviewer="t")
            self.addCleanup(service.store.close)
            service.import_path(str(make_claude_session(base / "s.jsonl")), "Demo")
            row = service.store.all_trajectories("Demo")[0]
            first = service.analyze(row["id"])
            self.assertTrue(first["steps"]["errors"])
            made = len(clients)
            self.assertEqual(service.analyze(row["id"])["steps"]["errors"], first["steps"]["errors"])
            self.assertEqual(len(clients), made)
            self.assertFalse(any(item["id"] == row["id"] for item in service.batch_rows("Demo", 100)))

    def test_missing_token_usage_does_not_stop_the_scan(self) -> None:
        result = JevAnalyzer(client_factory=NoUsageClient).analyze_steps(sample_run())
        self.assertEqual(result["input_tokens"], 0)

    def test_error_template_masks_a_url_as_one_placeholder(self) -> None:
        self.assertEqual(
            explorer.error_template("Error: could not fetch https://example.com/api/v1/items (404)"),
            explorer.error_template("Error: could not fetch http://other.test/x (500)"),
        )
        self.assertIn("<url>", explorer.error_template("ConnectionError: http://localhost:8080/health refused"))

    def test_raw_export_keeps_crlf_lines_byte_for_byte(self) -> None:
        original = b'{"id":"a","messages":[]}\r\n{"id":"b","messages":[]}\r\n'
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "dialog.jsonl"
            source.write_bytes(original)
            rows = [
                {"id": "a", "adapter_id": "openai-chat", "path": str(source), "locator": {"offset": 0}},
                {"id": "b", "adapter_id": "openai-chat", "path": str(source), "locator": {"offset": original.index(b'{"id":"b"')}},
            ]
            exporter.export(rows, Path(temp) / "out", mode="raw", load_run=None, filters={})
            self.assertEqual((Path(temp) / "out" / "dialog.jsonl").read_bytes(), original)


class ServiceRegressionTest(unittest.TestCase):
    def test_export_name_cannot_point_outside_the_export_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = WorkbenchService(Store(Path(temp) / "index.db"), reviewer="t")
            self.addCleanup(service.store.close)
            for name in ("..", ".", ".hidden"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    service.start_export({}, name, "raw")


if __name__ == "__main__":
    unittest.main()
