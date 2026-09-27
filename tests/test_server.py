from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from trajectory_workbench.server import create_server
from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import Store
from tests.fixtures import make_moh_run


class NoJev:
    def available(self) -> bool:
        return False


class ServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.run = make_moh_run(self.base / "run")
        service = WorkbenchService(Store(self.base / "index.db"), analyzer=NoJev(), reviewer="tester")
        self.server = create_server("127.0.0.1", 0, self.base / "index.db", service=service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path: str, *, method: str = "GET", body: object | None = None) -> tuple[int, bytes, dict[str, str]]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.url + path, data=data, method=method, headers={"Content-Type": "application/json"} if method == "POST" else {}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read(), dict(response.headers)

    def json(self, path: str, **kwargs):
        return json.loads(self.request(path, **kwargs)[1])

    def import_run(self, collection: str = "HTTP Demo") -> str:
        status, payload, _ = self.request("/api/imports", method="POST", body={"path": str(self.run), "collection": collection})
        self.assertEqual(status, 202)
        job = json.loads(payload)
        for _ in range(100):
            job = self.json(f"/api/jobs/{job['id']}")
            if job["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "done", job.get("error"))
        return self.json("/api/trajectories?" + urllib.parse.urlencode({"collection": collection}))["items"][0]["id"]

    def test_health_import_list_trajectory_messages_and_file(self) -> None:
        self.assertEqual(self.json("/api/health"), {"status": "ok"})
        trajectory_id = self.import_run()

        collections = self.json("/api/collections")
        self.assertEqual(collections["collections"][0]["collection"], "HTTP Demo")
        self.assertFalse(collections["jev_available"])

        detail = self.json(f"/api/trajectories/{trajectory_id}")
        self.assertEqual(detail["metrics"]["workbench_calls"], 1)
        self.assertEqual(detail["outcome"]["status"], "fail")

        query = urllib.parse.urlencode({"tool": "Bash", "limit": 10})
        self.assertEqual(self.json(f"/api/trajectories/{trajectory_id}/messages?{query}")["total"], 1)

        status, payload, headers = self.request(f"/api/trajectories/{trajectory_id}/files/attempts/001/workspace/artifact.html")
        self.assertEqual(status, 200)
        self.assertIn(b"<main>", payload)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        # Artifact HTML runs sandboxed in an opaque origin.
        self.assertEqual(headers["Content-Security-Policy"], "sandbox allow-scripts")

    def test_invalid_import_traversal_and_unknown_routes_have_bounded_errors(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            self.request("/api/imports", method="POST", body={"path": "relative/path"})
        self.assertEqual(invalid.exception.code, 400)
        invalid.exception.close()

        trajectory_id = self.import_run()
        with self.assertRaises(urllib.error.HTTPError) as traversal:
            self.request(f"/api/trajectories/{trajectory_id}/files/%2e%2e/secret.txt")
        self.assertEqual(traversal.exception.code, 403)
        traversal.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as media:
            self.request(f"/api/trajectories/{trajectory_id}/media?path=/etc/passwd")
        self.assertEqual(media.exception.code, 403)
        media.exception.close()

        # A cross-origin "simple" POST (text/plain) is refused.
        simple = urllib.request.Request(
            f"{self.url}/api/trajectories/{trajectory_id}/review", data=b'{"human_status": "pass"}',
            method="POST", headers={"Content-Type": "text/plain"},
        )
        with self.assertRaises(urllib.error.HTTPError) as plain:
            urllib.request.urlopen(simple, timeout=5)
        self.assertEqual(plain.exception.code, 400)
        plain.exception.close()
        # So is a bodyless POST, which browsers also send cross-origin without a preflight.
        for headers in ({}, {"Content-Type": "text/plain", "Content-Length": "0"}):
            bare = urllib.request.Request(f"{self.url}/api/jev/batch", method="POST", headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as bodyless:
                urllib.request.urlopen(bare, timeout=5)
            self.assertEqual(bodyless.exception.code, 400)
            bodyless.exception.close()
        self.assertEqual(self.json("/api/jobs")["jobs"], [job for job in self.json("/api/jobs")["jobs"] if job["kind"] == "import"])
        self.assertIsNone(self.json(f"/api/trajectories/{trajectory_id}")["review"])

        with self.assertRaises(urllib.error.HTTPError) as missing:
            self.request("/api/nothing-here")
        self.assertEqual(missing.exception.code, 404)
        missing.exception.close()

    def test_review_round_trip_queue_stats_and_export(self) -> None:
        trajectory_id = self.import_run()
        saved = self.json(
            f"/api/trajectories/{trajectory_id}/review",
            method="POST",
            body={"predicted_status": "pass", "human_status": "fail", "labels": ["no_verify"], "turning_step": 2, "hypothesis": "h"},
        )
        self.assertEqual(saved["reviewer"], "tester")
        self.assertEqual(self.json(f"/api/trajectories/{trajectory_id}")["review"]["labels"], ["no_verify"])

        stats = self.json("/api/stats")
        self.assertEqual(stats["reviews"]["count"], 1)
        self.assertEqual(stats["reviews"]["blind_accuracy"], 0.0)
        queue = self.json("/api/queue?size=5")
        self.assertIn("items", queue)
        _, body, headers = self.request("/api/reviews/export")
        self.assertIn("ndjson", headers["Content-Type"])
        self.assertEqual(json.loads(body.decode("utf-8").splitlines()[0])["trajectory_id"], trajectory_id)

        with self.assertRaises(urllib.error.HTTPError) as bad:
            self.request(f"/api/trajectories/{trajectory_id}/review", method="POST", body={"human_status": "great"})
        self.assertEqual(bad.exception.code, 400)
        bad.exception.close()

        status, _, _ = self.request(f"/api/trajectories/{trajectory_id}/review", method="DELETE")
        self.assertEqual(status, 200)
        self.assertIsNone(self.json(f"/api/trajectories/{trajectory_id}")["review"])

    def test_static_modules_are_served_as_javascript(self) -> None:
        status, payload, headers = self.request("/views/reader.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        status, _, headers = self.request("/presentation.mjs")
        self.assertIn("javascript", headers["Content-Type"])


if __name__ == "__main__":
    unittest.main()
