from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


DEPLOY = Path(__file__).parents[1] / "deploy.sh"
PORT = "18896"  # nothing listens here: status and stop never start a server


@unittest.skipUnless(shutil.which("bash") and shutil.which("ps"), "needs bash and ps")
class DeployPidFileTest(unittest.TestCase):
    """deploy.sh trusts a PID file only when that process is this server on this port."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "state"
        self.state.mkdir()
        self.pid_file = self.state / f"server-{PORT}.pid"
        self.processes: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait()
        self.temp.cleanup()

    def spawn(self, *args: str) -> subprocess.Popen:
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)", *args])
        self.processes.append(process)
        return process

    def deploy(self, command: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "STATE": str(self.state), "PORT": PORT, "HOME": self.temp.name}
        return subprocess.run(["bash", str(DEPLOY), command], env=env, capture_output=True, text=True, timeout=30)

    def test_stale_pid_of_unrelated_process_is_not_running_and_not_killed(self) -> None:
        unrelated = self.spawn()
        self.pid_file.write_text(f"{unrelated.pid}\n")
        status = self.deploy("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout.strip(), "not running")
        stop = self.deploy("stop")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIsNone(unrelated.poll(), "stop killed a process that is not the server")
        self.assertFalse(self.pid_file.exists())

    def test_server_on_another_port_is_not_this_server(self) -> None:
        other = self.spawn("trajectory_workbench", "serve", "--port", PORT + "1")
        self.pid_file.write_text(f"{other.pid}\n")
        self.assertEqual(self.deploy("status").stdout.strip(), "not running")
        self.deploy("stop")
        self.assertIsNone(other.poll())

    def test_matching_process_is_running_and_stopped(self) -> None:
        server = self.spawn("trajectory_workbench", "serve", "--port", PORT)
        self.pid_file.write_text(f"{server.pid}\n")
        status = self.deploy("status")
        self.assertTrue(status.stdout.startswith(f"running (pid {server.pid})"), status.stdout)
        stop = self.deploy("stop")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIsNotNone(server.poll(), "stop did not end the server process")
        self.assertFalse(self.pid_file.exists())


class DeploySyntaxTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_script_parses(self) -> None:
        result = subprocess.run(["bash", "-n", str(DEPLOY)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
