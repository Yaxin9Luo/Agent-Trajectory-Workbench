from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from trajectory_workbench.server import create_server
from tests.fixtures import make_moh_run


class ServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.run = make_moh_run(self.base / "run")
        self.server = create_server(
            "127.0.0.1", 0, self.base / "registry.json"
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(
        self, path: str, *, method: str = "GET", body: object | None = None
    ) -> tuple[int, bytes, dict[str, str]]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read(), dict(response.headers)

    def test_health_import_list_run_messages_and_file(self) -> None:
        status, payload, _ = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {"status": "ok"})

        _, payload, _ = self.request(
            "/api/runs",
            method="POST",
            body={"path": str(self.run), "label": "HTTP Demo"},
        )
        run_id = json.loads(payload)["id"]

        _, payload, _ = self.request("/api/runs")
        self.assertEqual(json.loads(payload)["runs"][0]["label"], "HTTP Demo")

        _, payload, _ = self.request(f"/api/runs/{run_id}")
        self.assertEqual(json.loads(payload)["metrics"]["workbench_calls"], 1)

        query = urllib.parse.urlencode({"tool": "Bash", "limit": 10})
        _, payload, _ = self.request(f"/api/runs/{run_id}/messages?{query}")
        self.assertEqual(json.loads(payload)["total"], 1)

        status, payload, headers = self.request(
            f"/api/runs/{run_id}/files/attempts/001/workspace/artifact.html"
        )
        self.assertEqual(status, 200)
        self.assertIn(b"<main>", payload)
        self.assertIn("text/html", headers["Content-Type"])

    def test_invalid_import_and_file_traversal_have_bounded_errors(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            self.request(
                "/api/runs",
                method="POST",
                body={"path": str(self.base), "label": "invalid"},
            )
        self.assertEqual(invalid.exception.code, 400)
        invalid.exception.close()

        _, payload, _ = self.request(
            "/api/runs", method="POST", body={"path": str(self.run)}
        )
        run_id = json.loads(payload)["id"]
        with self.assertRaises(urllib.error.HTTPError) as traversal:
            self.request(f"/api/runs/{run_id}/files/%2e%2e/secret.txt")
        self.assertEqual(traversal.exception.code, 403)
        traversal.exception.close()


if __name__ == "__main__":
    unittest.main()
