"""Replaying what a run built: file versions, design behaviours, failed checks to steps."""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

from trajectory_workbench import artifact
from trajectory_workbench.scriptlet import Aborted, Scriptlet, Unsupported
from trajectory_workbench.server import create_server
from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import Store
from tests.fixtures import atif_document, write_json

DECK = "/workspace/output/slides.html"
PAGE_1 = "<!doctype html>\n<html>\n<body>\n<section id=\"s1\"><h1>Cave guide</h1><p class=\"lede\">Stalactites hang &amp; drip</p></section>\n</body>\n</html>\n"


def call(step: int, name: str, tool_input, *, text: str = "ok", error: bool = False, images: int = 0) -> dict:
    return {"id": f"t{step}-{name}-{abs(hash(json.dumps(tool_input, sort_keys=True, default=str))) % 10000}", "step": step, "name": name, "input": tool_input,
            "result": {"text": text, "is_error": error, "images": [{"path": f"/x/{index}.png"} for index in range(images)]}}


def run_of(tools: list[dict], deliverables: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(tools=tools, task={"deliverables": [{"path": path} for path in deliverables or ["artifacts/slides.html"]]})


def deck(state: artifact.Replay) -> dict:
    return state.files[DECK]


class ReplayTest(unittest.TestCase):
    def test_write_edit_multiedit_make_versions(self) -> None:
        state = artifact.replay(run_of([
            call(2, "Write", {"file_path": DECK, "content": PAGE_1}),
            call(3, "Edit", {"file_path": DECK, "old_string": "Cave guide", "new_string": "Cave guide for kids"}),
            call(4, "MultiEdit", {"file_path": DECK, "edits": [{"old_string": "hang", "new_string": "HANG"}, {"old_string": "drip", "new_string": "DRIP"}]}),
            call(5, "Edit", {"file_path": DECK, "old_string": "<p", "new_string": "<p data-x", "replace_all": True}),
        ]))
        entry = deck(state)
        self.assertEqual([(v["n"], v["step"], v["op"]) for v in entry["versions"]], [(1, 2, "write"), (2, 3, "edit"), (3, 4, "multiedit"), (4, 5, "edit")])
        self.assertEqual(entry["contents"][0], PAGE_1)
        self.assertIn("Cave guide for kids", entry["contents"][1])
        self.assertIn("HANG &amp; DRIP", entry["contents"][2])
        self.assertIn("<p data-x class", entry["contents"][3])
        self.assertTrue(entry["exact"])
        self.assertTrue(entry["deliverable"])
        self.assertEqual((entry["versions"][1]["added"], entry["versions"][1]["removed"]), (1, 1))

    def test_a_failed_call_and_an_edit_that_does_not_apply(self) -> None:
        state = artifact.replay(run_of([
            call(2, "Write", {"file_path": DECK, "content": PAGE_1}),
            call(3, "Edit", {"file_path": DECK, "old_string": "Cave", "new_string": "X"}, error=True, text="String to replace not found"),
            call(4, "Edit", {"file_path": DECK, "old_string": "not in the file", "new_string": "X"}),
        ]))
        entry = deck(state)
        # The errored call changed nothing; the one that "succeeded" but does not apply to the
        # replayed text means the replay has lost the file.
        self.assertEqual(len(entry["versions"]), 1)
        self.assertEqual([item["step"] for item in entry["failed"]], [4])
        self.assertFalse(entry["exact"])
        self.assertEqual(artifact.history(state)[0]["uncertain_from_step"], 4)

    def test_shell_heredoc_append_and_concatenation(self) -> None:
        state = artifact.replay(run_of([
            call(2, "Bash", {"command": "mkdir -p /tmp/build && cd /tmp/build && cat > part1.html <<'EOF'\n<!doctype html>\n<body>\nEOF"}),
            call(3, "Bash", {"command": "cat > part2.html <<'EOF'\n<p>two</p>\nEOF\ncat >> part2.html << 'EOF'\n</body>\nEOF"}),
            call(4, "Bash", {"command": "cat part1.html part2.html > /workspace/output/slides.html && wc -c /workspace/output/slides.html"}),
        ]))
        self.assertEqual(deck(state)["contents"][-1], "<!doctype html>\n<body>\n<p>two</p>\n</body>\n")
        self.assertEqual(deck(state)["versions"][0]["op"], "shell")
        self.assertTrue(deck(state)["exact"])
        # The directory set by `cd` carried over to the next command.
        self.assertIn("/tmp/build/part2.html", state.files)

    def test_inline_edit_script_is_followed(self) -> None:
        script = (
            "python3 - <<'PYEOF'\n"
            "import re\n"
            "with open('output/slides.html', 'r', encoding='utf-8') as f:\n    src = f.read()\n"
            "fixes = [('Cave guide', 'Cave guide v2'), ('drip', 'drop')]\n"
            "for old, new in fixes:\n    assert old in src, 'missing ' + old\n    src = src.replace(old, new)\n"
            "src = re.sub(r'<h1>(.*?)</h1>', r'<h1 class=\"t\">\\1</h1>', src, flags=re.S)\n"
            "open('output/slides.html', 'w', encoding='utf-8').write(src)\n"
            "print('ok')\n"
            "PYEOF"
        )
        state = artifact.replay(run_of([
            call(2, "Write", {"file_path": DECK, "content": PAGE_1}),
            call(3, "Bash", {"command": script}, text="ok\nShell cwd was reset to /workspace"),
        ]))
        entry = deck(state)
        self.assertEqual([v["op"] for v in entry["versions"]], ["write", "script"])
        self.assertIn('<h1 class="t">Cave guide v2</h1>', entry["contents"][-1])
        self.assertIn("drop", entry["contents"][-1])
        self.assertTrue(entry["exact"])

    def test_a_script_the_evaluator_cannot_follow_marks_the_file_inexact(self) -> None:
        for body in (
            "import base64\ns = open('/workspace/output/slides.html').read()\ns = s.replace('X', base64.b64encode(open('/tmp/f.woff2','rb').read()).decode())\nopen('/workspace/output/slides.html','w').write(s)",
            "import subprocess\nsubprocess.run(['sed', '-i', 's/a/b/', '/workspace/output/slides.html'])\nopen('/workspace/output/slides.html','w').write('x')",
        ):
            with self.subTest(body=body[:20]):
                state = artifact.replay(run_of([
                    call(2, "Write", {"file_path": DECK, "content": PAGE_1}),
                    call(3, "Bash", {"command": "python3 - <<'EOF'\n" + body + "\nEOF"}),
                ]))
                self.assertEqual(len(deck(state)["versions"]), 1)
                self.assertFalse(deck(state)["exact"])
                self.assertEqual(deck(state)["notes"][0]["step"], 3)

    def test_a_script_that_ran_but_does_not_apply_marks_the_file_inexact(self) -> None:
        state = artifact.replay(run_of([
            call(2, "Write", {"file_path": DECK, "content": PAGE_1}),
            call(3, "Bash", {"command": "python3 - <<'EOF'\ns = open('/workspace/output/slides.html').read()\nassert 'gone' in s\nopen('/workspace/output/slides.html','w').write(s.replace('gone','x'))\nEOF"}),
            # The same script reported as failed changed nothing and proves nothing.
            call(4, "Bash", {"command": "python3 - <<'EOF'\ns = open('/workspace/output/slides.html').read()\nassert 'gone' in s\nEOF"}, error=True),
        ]))
        self.assertEqual([note["step"] for note in deck(state)["notes"]], [3])

    def test_sed_plain_substitution_and_unfollowed_shell_writes(self) -> None:
        state = artifact.replay(run_of([
            call(2, "Write", {"file_path": DECK, "content": PAGE_1}),
            call(3, "Bash", {"command": "sed -i 's|Cave guide|Cave tour|g' /workspace/output/slides.html && grep -c tour /workspace/output/slides.html"}),
            call(4, "Bash", {"command": "grep -n '<script>(.*)</script>' /workspace/output/slides.html > /tmp/out.txt"}),
        ]))
        self.assertIn("Cave tour", deck(state)["contents"][-1])
        self.assertTrue(deck(state)["exact"])
        for command in ("sed -i 's/[0-9]*px/2px/' /workspace/output/slides.html", "node build.js > /workspace/output/slides.html", "rm /workspace/output/slides.html"):
            with self.subTest(command=command):
                state = artifact.replay(run_of([call(2, "Write", {"file_path": DECK, "content": PAGE_1}), call(3, "Bash", {"command": command})]))
                self.assertFalse(deck(state)["exact"])

    def test_a_file_first_seen_in_a_read_and_a_read_that_disagrees(self) -> None:
        listing = "".join(f"{number:>6}\t{line}\n" for number, line in enumerate(PAGE_1.splitlines(), 1))
        state = artifact.replay(run_of([call(2, "Read", {"file_path": DECK}, text=listing)]))
        self.assertEqual(deck(state)["contents"][0], PAGE_1)
        self.assertFalse(deck(state)["exact"])
        changed = listing.replace("Cave guide", "Changed elsewhere")
        state = artifact.replay(run_of([
            call(2, "Write", {"file_path": DECK, "content": PAGE_1}),
            call(3, "Read", {"file_path": DECK}, text=listing),
            call(4, "Read", {"file_path": DECK, "offset": 2, "limit": 2}, text="     2\t<html>\n     3\t<body>\n"),
        ]))
        self.assertTrue(deck(state)["exact"])
        self.assertEqual(len(deck(state)["versions"]), 1)
        state = artifact.replay(run_of([call(2, "Write", {"file_path": DECK, "content": PAGE_1}), call(3, "Read", {"file_path": DECK}, text=changed)]))
        self.assertFalse(deck(state)["exact"])
        self.assertIn("Changed elsewhere", deck(state)["contents"][-1])

    def test_apply_patch(self) -> None:
        state = artifact.replay(run_of([
            call(2, "apply_patch", {"input": "*** Begin Patch\n*** Add File: /workspace/output/slides.html\n+<h1>One</h1>\n+<p>two</p>\n*** End Patch"}),
            call(3, "apply_patch", "*** Begin Patch\n*** Update File: /workspace/output/slides.html\n@@\n <h1>One</h1>\n-<p>two</p>\n+<p>three</p>\n*** End Patch"),
        ]))
        self.assertEqual(deck(state)["contents"], ["<h1>One</h1>\n<p>two</p>\n", "<h1>One</h1>\n<p>three</p>\n"])

    def test_main_file_is_a_replayed_deliverable_or_nothing(self) -> None:
        state = artifact.replay(run_of([
            call(2, "Write", {"file_path": "/tmp/build/gen.py", "content": "print('deck')\n"}),
            call(3, "Bash", {"command": "cd /tmp/build && python gen.py"}),
        ]))
        self.assertIsNone(artifact.main_file(state))
        self.assertFalse(state.files["/tmp/build/gen.py"]["deliverable"])


class ScriptletTest(unittest.TestCase):
    def run_script(self, source: str, files: dict[str, str] | None = None) -> dict[str, str]:
        files = dict(files or {})
        Scriptlet(lambda path: files.get(path), lambda path, text, append: files.__setitem__(path, (files.get(path, "") if append else "") + text)).run(source)
        return files

    def test_string_edits(self) -> None:
        out = self.run_script(
            "p = 'a.html'\ns = open(p).read()\nparts = s.split('|')\ns = '-'.join(part.strip() for part in parts)\n"
            "i = s.index('b')\ns = s[:i] + 'B' + s[i + 1:]\nn = s.count('-')\nif n == 2:\n    s += '!'\nopen(p, 'w').write(s)\n"
            "with open('b.txt', 'w') as f:\n    f.write('x')\n    f.write('y')\n",
            {"a.html": "a | b | c"},
        )
        self.assertEqual(out, {"a.html": "a-B-c!", "b.txt": "xy"})

    def test_nothing_outside_the_allow_list_runs(self) -> None:
        marker = Path(tempfile.gettempdir()) / "scriptlet-must-not-exist.txt"
        marker.unlink(missing_ok=True)
        for source in (
            "import os\nos.system('touch " + str(marker) + "')",
            "__import__('os').system('touch " + str(marker) + "')",
            "open('" + str(marker) + "', 'wb').write(b'x')",
            "eval('1+1')",
            "exec('x = 1')",
            "x = ().__class__.__bases__",
            "x = (lambda: 1)()",
            "import socket",
            "from os import system",
            "s = 'a'.format.__self__",
            "def f():\n    pass",
            "x = [1].__class__",
            "import re\nre.sub('a', lambda m: 'b', 'a')",
            "x = getattr('a', 'upper')()",
            "x = 'a' * 10**8",
            "for i in range(10**9):\n    pass",
            "while True:\n    pass",
            "import subprocess\nsubprocess.run(['ls'])",
        ):
            with self.subTest(source=source[:40]), self.assertRaises(Unsupported):
                self.run_script(source)
        self.assertFalse(marker.exists())

    def test_a_script_that_would_stop_is_aborted(self) -> None:
        for source in ("assert 'x' in 'abc'", "s = 'abc'\ni = s.index('x')", "x = [][0]", "import sys\nsys.exit(1)"):
            with self.subTest(source=source), self.assertRaises(Aborted):
                self.run_script(source)

    def test_reading_an_unknown_file_is_unsupported(self) -> None:
        with self.assertRaises(Unsupported):
            self.run_script("s = open('/etc/passwd').read()")


class BehaviourTest(unittest.TestCase):
    def tools(self) -> list[dict]:
        return [
            call(2, "Write", {"file_path": DECK, "content": PAGE_1}),
            call(3, "Write", {"file_path": "/tmp/check.py", "content": "print(1)\n"}),
            call(4, "Bash", {"command": "cd /tmp && python check.py && playwright screenshot file:///workspace/output/slides.html s1.png"}),
            call(5, "Read", {"file_path": "/tmp/s1.png"}, text="[image]", images=1),
            call(6, "Edit", {"file_path": DECK, "old_string": "Cave guide", "new_string": "Cave guide v2"}),
            call(7, "Read", {"file_path": "/tmp/shots/slide-02.png"}, text="[image]", images=1),
            call(8, "Edit", {"file_path": DECK, "old_string": "hang", "new_string": "hang down"}),
            call(8, "Edit", {"file_path": DECK, "old_string": "drip", "new_string": "drip slowly"}),
        ]

    def test_events_and_counts(self) -> None:
        state = artifact.replay(run_of(self.tools()))
        self.assertEqual([(e["step"], e["kind"]) for e in state.events], [(2, "change"), (3, "support"), (4, "render"), (5, "look"), (6, "change"), (7, "look"), (8, "change"), (8, "change")])
        self.assertEqual([e.get("slide") for e in state.events if e["kind"] == "look"], [1, 2])
        counts = artifact.behaviour(state.events, 9)
        self.assertEqual((counts["changes"], counts["change_steps"], counts["renders"], counts["looks"], counts["cycles"]), (4, 3, 1, 2, 2))
        self.assertEqual((counts["first_look_step"], counts["last_look_step"], counts["last_change_step"]), (5, 7, 8))
        self.assertEqual(counts["changes_after_last_look"], 2)
        self.assertFalse(counts["looked_after_last_change"])

    def test_no_look_at_all(self) -> None:
        counts = artifact.behaviour(artifact.replay(run_of(self.tools()[:1])).events, 2)
        self.assertEqual((counts["looks"], counts["cycles"], counts["changes_after_last_look"], counts["looked_after_last_change"]), (0, 0, 1, False))


class CheckLinkTest(unittest.TestCase):
    def test_a_failed_check_is_traced_to_the_step_that_last_changed_its_text(self) -> None:
        state = artifact.replay(run_of([
            call(2, "Write", {"file_path": DECK, "content": PAGE_1}),
            call(3, "Read", {"file_path": "/tmp/s1.png"}, images=1),
            call(4, "Edit", {"file_path": DECK, "old_string": "<p class=\"lede\">", "new_string": "<p class=\"lede\" style=\"font-size:12px\">"}),
            call(5, "Edit", {"file_path": DECK, "old_string": "<h1>", "new_string": "<h1 class=\"big\">"}),
            call(6, "Read", {"file_path": "/tmp/s2.png"}, images=1),
        ]))
        failed = [
            {"id": "delivery.small_text", "layer": "L1", "severity": "minor", "slides": [1],
             "evidence": {"note": "1 element under the floor", "boxes": [{"box": [1, 2, 3, 4], "text": "body · 12 px (floor 18, hard 12) · Stalactites hang & drip", "role": "target"}]}},
            {"id": "layout.text_overlap", "layer": "L1", "severity": "major", "slides": [2],
             "evidence": {"boxes": [{"text": "h1 · 60 px · Cave  guide"}]}},
            {"id": "nav.key_partial", "layer": "L1", "severity": "minor", "slides": [], "evidence": {"note": "keys", "boxes": []}},
            {"id": "craft.other", "layer": "L1", "severity": "minor", "slides": [3], "evidence": {"boxes": [{"text": "p · 20 px · text that is not in the deck"}]}},
        ]
        small, overlap, nav, other = artifact.link_checks(artifact.main_file(state), state.events, failed)
        # Rendered text ("hang & drip") is found in the source ("hang &amp; drip").
        self.assertEqual((small["introduced_step"], small["last_edit_step"], small["last_edit_version"]), (2, 4, 2))
        # One picture was looked at after that edit, but its name points at slide 2, not 1.
        self.assertEqual((small["looks_after"], small["seen"], small["slide_look_steps"]), (1, "other", []))
        self.assertEqual((overlap["last_edit_step"], overlap["seen"], overlap["slide_look_steps"]), (5, "slide", [6]))
        self.assertEqual((small["last_edit_via"], overlap["last_edit_via"]), ("text", "text"))
        self.assertEqual(nav["seen"], "untraceable")
        self.assertEqual(other["seen"], "untraceable")

    def test_a_style_rule_for_the_elements_class_counts_as_a_change_to_it(self) -> None:
        page = "<style>\n.lede{font-size:20px}\n.other{color:red}\n</style>\n<p class=\"lede\">Stalactites hang</p>\n"
        state = artifact.replay(run_of([
            call(2, "Write", {"file_path": DECK, "content": page}),
            call(3, "Edit", {"file_path": DECK, "old_string": ".other{color:red}", "new_string": ".other{color:blue}"}),
            call(4, "Edit", {"file_path": DECK, "old_string": ".lede{font-size:20px}", "new_string": ".lede{font-size:12px}"}),
            call(5, "Edit", {"file_path": DECK, "old_string": ".other{color:blue}", "new_string": ".other{color:green}"}),
        ]))
        failed = [{"id": "delivery.small_text", "slides": [1], "evidence": {"boxes": [{"text": "body · 12 px (floor 18) · Stalactites hang"}]}}]
        link = artifact.link_checks(artifact.main_file(state), state.events, failed)[0]
        self.assertEqual((link["last_edit_step"], link["last_edit_via"]), (4, "style"))

    def test_never_looked_after_the_last_change(self) -> None:
        state = artifact.replay(run_of([
            call(2, "Read", {"file_path": "/tmp/s1.png"}, images=1),
            call(3, "Write", {"file_path": DECK, "content": PAGE_1}),
        ]))
        failed = [{"id": "x", "slides": [1], "evidence": {"boxes": [{"text": "h1 · 60 px · Cave guide"}]}}]
        self.assertEqual(artifact.link_checks(artifact.main_file(state), state.events, failed)[0]["seen"], "never")


def trial_with_deck(root: Path, final_text: str) -> Path:
    """A Harbor trial whose agent wrote a deck in two steps; the kept deliverable is on disk."""
    document = atif_document()
    document["subagent_trajectories"] = []
    document["steps"] = [
        {"step_id": 1, "source": "user", "message": "Make a deck", "timestamp": "2026-09-03T00:00:00Z"},
        {"step_id": 2, "source": "agent", "message": "", "timestamp": "2026-09-03T00:00:02Z",
         "tool_calls": [{"tool_call_id": "c1", "function_name": "Write", "arguments": {"file_path": DECK, "content": PAGE_1}}],
         "observation": {"results": [{"source_call_id": "c1", "content": "File created"}]}},
        {"step_id": 3, "source": "agent", "message": "", "timestamp": "2026-09-03T00:00:04Z",
         "tool_calls": [{"tool_call_id": "c2", "function_name": "Edit", "arguments": {"file_path": DECK, "old_string": "Cave guide", "new_string": "Cave guide v2"}}],
         "observation": {"results": [{"source_call_id": "c2", "content": "updated"}]}},
        {"step_id": 4, "source": "agent", "message": "Done.", "timestamp": "2026-09-03T00:00:05Z"},
    ]
    write_json(root / "agent" / "trajectory.json", document)
    write_json(root / "result.json", {"task_name": "deck-a", "trial_name": "deck-a__1", "verifier_result": {"rewards": {"reward": 1.0}}})
    (root / "artifacts").mkdir(parents=True)
    (root / "artifacts" / "slides.html").write_text(final_text, encoding="utf-8")
    return root


class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.service = WorkbenchService(Store(self.base / "index.db"), reviewer="t")
        self.addCleanup(self.service.store.close)

    def import_trial(self, final_text: str) -> str:
        trial_with_deck(self.base / "runs" / "deck-a__1", final_text)
        self.service.import_path(str(self.base / "runs"), "Runs")
        return next(row["id"] for row in self.service.store.all_trajectories("Runs") if row["episode_head"])

    def test_versions_are_checked_against_the_kept_file(self) -> None:
        trajectory_id = self.import_trial(PAGE_1.replace("Cave guide", "Cave guide v2"))
        payload = self.service.artifacts(trajectory_id)
        self.assertEqual(payload["main"], DECK)
        entry = payload["files"][0]
        self.assertNotIn("contents", entry)
        self.assertEqual([v["step"] for v in entry["versions"]], [2, 3])
        self.assertEqual(entry["final"], {"path": "artifacts/slides.html", "bytes": len(PAGE_1.encode()) + 3, "matches": True})
        self.assertIsNone(entry["uncertain_from_step"])
        self.assertEqual(payload["behaviour"]["changes"], 2)
        self.assertEqual(self.service.artifact_text(trajectory_id, DECK, 1), PAGE_1)
        self.assertIn("Cave guide v2", self.service.artifact_text(trajectory_id, DECK, 2))
        for version in (0, 3):
            with self.assertRaises(KeyError):
                self.service.artifact_text(trajectory_id, DECK, version)
        with self.assertRaises(KeyError):
            self.service.artifact_text(trajectory_id, "/etc/passwd", 1)

    def test_a_kept_file_that_differs_is_reported(self) -> None:
        trajectory_id = self.import_trial("<html>built by a script the transcript does not show</html>\n")
        entry = self.service.artifacts(trajectory_id)["files"][0]
        self.assertFalse(entry["final"]["matches"])

    def test_checks_are_linked_when_the_run_has_a_reward(self) -> None:
        trajectory_id = self.import_trial(PAGE_1.replace("Cave guide", "Cave guide v2"))
        write_json(self.base / "score" / "work" / "deck-a" / "reward.json", {
            "artifact_id": "deck-a", "masked": False, "tier": 1, "tier_name": "L1 fatal", "R": 0.05, "slides": 1,
            "ledger": [{"layer": "L1", "id": "delivery.small_text", "pass": False, "severity": "minor", "slides": [1],
                        "evidence": {"boxes": [{"text": "h1 · 10 px (floor 18) · Cave guide v2"}]}}],
        })
        self.service.import_v4_reward(str(self.base / "score"), "v1", str(self.base / "runs"))
        check = self.service.artifacts(trajectory_id)["checks"][0]
        self.assertEqual((check["id"], check["last_edit_step"], check["seen"]), ("delivery.small_text", 3, "never"))

    def test_http_serves_a_version_into_a_sandbox(self) -> None:
        trajectory_id = self.import_trial(PAGE_1)
        self.service.store.close()
        server = create_server("127.0.0.1", 0, self.base / "index.db", self.base / "no-registry.json")
        self.addCleanup(server.server_close)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}/api/trajectories/{trajectory_id}"
        with urllib.request.urlopen(base + "/artifacts") as response:
            self.assertEqual(json.load(response)["main"], DECK)
        with urllib.request.urlopen(base + "/artifact?v=2&path=" + urllib.request.quote(DECK, safe="")) as response:
            self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
            self.assertEqual(response.headers["Content-Security-Policy"], "sandbox allow-scripts")
            self.assertIn("Cave guide v2", response.read().decode())
        with urllib.request.urlopen(base + "/artifact?v=1&text=1&path=" + urllib.request.quote(DECK, safe="")) as response:
            self.assertEqual(response.headers["Content-Type"], "text/plain; charset=utf-8")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + "/artifact?v=9&path=" + urllib.request.quote(DECK, safe=""))
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
