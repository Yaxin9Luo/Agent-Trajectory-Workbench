from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trajectory_workbench.registry import Registry
from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import Store
from tests.fixtures import make_claude_session, make_moh_run, make_sft_export, write_json, write_jsonl


class NoJev:
    def available(self) -> bool:
        return False


def service_for(base: Path) -> WorkbenchService:
    return WorkbenchService(Store(base / "index.db"), analyzer=NoJev(), reviewer="tester")


class WorkbenchServiceTest(unittest.TestCase):
    def test_import_list_load_and_filter_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_moh_run(base / "run")
            service = service_for(base)

            report = service.import_path(str(run), "Demo")
            listed = service.list_trajectories(collection="Demo")
            trajectory_id = listed["items"][0]["id"]
            normalized = service.get_trajectory(trajectory_id)
            bash_only = service.get_messages(trajectory_id, tool="Bash", offset=0, limit=20)
            searched = service.get_messages(trajectory_id, search="Inspect files", offset=0, limit=20)
            by_step = service.get_messages(trajectory_id, steps={1})

        self.assertEqual(report["trajectories"], 1)
        self.assertEqual(report["adapters"], {"moh-v1": 1})
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["items"][0]["outcome_status"], "fail")
        self.assertEqual(normalized["metrics"]["tool_calls"], 2)
        self.assertEqual(
            [item["name"] for item in normalized["tool_catalog"]],
            ["Bash", "Slides Workbench", "Task", "WebSearch"],
        )
        self.assertEqual(normalized["native_tool_surfaces"], ["default"])
        self.assertEqual(bash_only["total"], 1)
        self.assertEqual(bash_only["items"][0]["tools"][0]["name"], "Bash")
        self.assertEqual(searched["total"], 1)
        self.assertEqual(by_step["total"], 1)

    def test_payload_excludes_messages_but_indexes_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            service = service_for(base)
            service.import_path(str(make_moh_run(base / "run")), None)
            trajectory_id = service.list_trajectories()["items"][0]["id"]
            payload = service.get_trajectory(trajectory_id)
        self.assertNotIn("messages", payload)
        self.assertNotIn("tools", payload)
        self.assertEqual(len(payload["step_index"]), payload["metrics"]["message_count"])
        self.assertEqual([tool["name"] for tool in payload["tool_index"]], ["Bash", "Slides Workbench"])
        self.assertIn("signals", payload)

    def test_rejects_relative_missing_and_unrecognized_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = service_for(Path(temp))
            with self.assertRaisesRegex(ValueError, "absolute"):
                service.import_path("relative/run", None)
            with self.assertRaisesRegex(ValueError, "does not exist"):
                service.import_path(temp + "/missing", None)
            with self.assertRaisesRegex(ValueError, "No trajectories recognized"):
                service.import_path(temp, None)

    def test_file_path_rejects_escape_from_registered_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_moh_run(base / "run")
            (base / "secret.txt").write_text("secret", encoding="utf-8")
            service = service_for(base)
            service.import_path(str(run), None)
            trajectory_id = service.list_trajectories()["items"][0]["id"]

            artifact = service.resolve_file(trajectory_id, "attempts/001/workspace/artifact.html")
            with self.assertRaises(PermissionError):
                service.resolve_file(trajectory_id, "../secret.txt")
        self.assertEqual(artifact.name, "artifact.html")

    def test_media_is_served_only_when_the_transcript_references_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            path = make_sft_export(base / "sft")
            text = path.read_text(encoding="utf-8").replace("/data/images/ab.png", str(base / "shot.png"))
            path.write_text(text, encoding="utf-8")
            (base / "shot.png").write_bytes(b"png")
            (base / "other.png").write_bytes(b"png")
            service = service_for(base)
            service.import_path(str(path), None)
            trajectory_id = service.list_trajectories(search="tides")["items"][0]["id"]
            page = service.get_messages(trajectory_id)
            image = next(tool["result"]["images"][0] for message in page["items"] for tool in message["tools"] if tool["result"]["images"])
            served = service.resolve_media(trajectory_id, str(base / "shot.png"))
            with self.assertRaises(PermissionError):
                service.resolve_media(trajectory_id, str(base / "other.png"))
            with self.assertRaises(PermissionError):
                service.resolve_media(trajectory_id, str(base / "sft" / "dialog.jsonl"))
        self.assertTrue(image["url"].startswith(f"/api/trajectories/{trajectory_id}/media?path="))
        self.assertEqual(served.name, "shot.png")

    def test_inline_images_are_served_on_demand_and_repeats_are_marked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            service = service_for(base)
            service.import_path(str(make_claude_session(base / "s.jsonl")), "C")
            trajectory_id = service.list_trajectories()["items"][0]["id"]
            page = service.get_messages(trajectory_id)
            tools = [tool for message in page["items"] for tool in message["tools"]]
            second = next(tool for tool in tools if tool["id"] == "t2")
            ref = second["result"]["images"][0]["url"].split("ref=")[1].split("&")[0].replace("%3A", ":")
            content, media_type = service.inline_image(trajectory_id, ref, 0)
            with self.assertRaises(KeyError):
                service.inline_image(trajectory_id, ref, 5)
        # No base64 in the page payload; the image has its own URL.
        self.assertNotIn("data", second["result"]["images"][0])
        # Addressed by the call's position (t2 is the second call), not its id.
        self.assertEqual(second["result"]["images"][0]["url"], f"/api/trajectories/{trajectory_id}/inline-image?ref=t%3A1&i=0")
        self.assertEqual(media_type, "image/png")
        self.assertTrue(content.startswith(b"\x89PNG"))
        # The retry of the same pytest command points back at the first call.
        self.assertEqual(second["repeat_of"]["id"], "t1")

    def test_source_changes_invalidate_the_cached_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_moh_run(base / "run")
            service = service_for(base)
            service.import_path(str(run), None)
            trajectory_id = service.list_trajectories()["items"][0]["id"]
            service.get_trajectory(trajectory_id)
            manifest_path = run / "resolved_run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["execution_profile"]["agent"]["allowed_tools"].append("future_search")
            write_json(manifest_path, manifest)
            refreshed = service.get_trajectory(trajectory_id)
        self.assertIn("future_search", [item["name"] for item in refreshed["tool_catalog"]])

    def test_groups_and_reviews_survive_reimport(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            path = make_sft_export(base / "sft")
            service = service_for(base)
            service.import_path(str(path), "SFT")
            items = service.list_trajectories(roles=["main", "segment", "subagent"], sort="task")["items"]
            segment = next(item for item in items if item["group_role"] == "segment" and item["segment"] == 2)
            saved = service.save_review(segment["id"], {"human_status": "fail", "labels": ["no_verify", " "], "turning_step": "3", "intervention": "hook",
                                                         "label_details": {"no_verify": {"severity": "high", "decisive": True}}})
            with self.assertRaises(ValueError):
                service.save_review(segment["id"], {"intervention": "prayer"})
            with self.assertRaises(ValueError):
                service.save_review(segment["id"], {"label_details": {"no_verify": {"severity": "extreme"}}})
            service.import_path(str(path), "SFT")
            detail = service.get_trajectory(segment["id"])
            tree = service.get_episode(segment["id"])
            exported = service.export_reviews("SFT")

        # The post-compaction segment inherits its group's title and task.
        self.assertTrue(segment["title"].startswith("Build a 9 slide deck about volcanoes"))
        self.assertEqual(saved["labels"], ["no_verify"])
        self.assertEqual(saved["label_details"], {"no_verify": {"severity": "high", "decisive": True}})
        self.assertEqual(saved["turning_step"], 3)
        self.assertEqual(detail["review"]["intervention"], "hook")
        self.assertEqual([lane["role"] for lane in tree["threads"]], ["segment", "segment"])
        self.assertEqual(len(tree["subagents"]), 1)
        self.assertEqual(json.loads(exported)["human_status"], "fail")

    def test_legacy_registry_is_migrated_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_moh_run(base / "run")
            Registry(base / "registry.json").register(run, "Legacy", "moh-v1")
            service = WorkbenchService(Store(base / "index.db"), analyzer=NoJev(), reviewer="t", legacy_registry=base / "registry.json")
            first = service.list_trajectories()["total"]
            again = WorkbenchService(Store(base / "index.db"), analyzer=NoJev(), reviewer="t", legacy_registry=base / "registry.json")
            second = again.list_trajectories()["total"]
            # The single-trajectory id matches the old registry id, so old links keep working.
            legacy_id = Registry(base / "registry.json").list_entries()[0].id
            found = again.store.get_trajectory(legacy_id)
        self.assertEqual((first, second), (1, 1))
        self.assertIsNotNone(found)

    def test_queue_and_stats_use_the_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            service = service_for(base)
            service.import_path(str(make_sft_export(base / "sft", results_root=base / "trials")), "SFT")
            queue = service.queue("SFT", "2026-09-27", 10)
            stats = service.stats("SFT")
        self.assertGreater(queue["size"], 0)
        # Two tasks: t-002's post-compaction segment and subagent belong to its episode.
        self.assertEqual(stats["outcomes"], {"pass": 1, "fail": 1})
        self.assertEqual(stats["total"], 2)


def edit_sample(sample_id: str, *, task: str | None, calls: list[tuple[str, str, dict]], tag: dict | None = None) -> dict:
    """A chat sample whose assistant makes the given (call id, tool, input) calls and stops."""
    messages = [{"role": "user", "content": task or "This session is being continued from a previous conversation."}]
    for call_id, name, arguments in calls:
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}]})
        messages.append({"role": "tool", "tool_call_id": call_id, "content": "ok"})
    messages.append({"role": "assistant", "content": "Done."})
    return {"id": sample_id, "tag": tag or {}, "messages": messages}


class AnnotationTest(unittest.TestCase):
    def test_annotations_round_trip_and_feed_the_markdown_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            write_jsonl(base / "dialog.jsonl", [edit_sample("a_k3_attempt_01", task="Fix the parser", calls=[
                ("B1", "Bash", {"command": "pytest -q"}), ("E1", "Edit", {"file_path": "p.py", "old_string": "a", "new_string": "b"}),
            ])])
            service = service_for(base)
            service.import_path(str(base / "dialog.jsonl"), "A")
            trajectory_id = service.list_trajectories()["items"][0]["id"]
            first = service.save_annotation(trajectory_id, {"step": 2, "note": "Runs tests before reading the code", "label": "premature_stop"})
            service.save_annotation(trajectory_id, {"step": 3, "note": "Edits blind"})
            service.save_annotation(trajectory_id, {"id": first["id"], "step": 2, "note": "Runs tests first — good"})
            with self.assertRaises(ValueError):
                service.save_annotation(trajectory_id, {"step": 99, "note": "x"})
            service.save_review(trajectory_id, {"turning_step": 3, "labels": ["no_verify"]})
            default = service.excerpt(trajectory_id, None)
            ranged = service.excerpt(trajectory_id, "1-2", hide_outcome=True)
            listed = service.annotations(trajectory_id)
            service.delete_annotation(trajectory_id, first["id"])
            remaining = service.annotations(trajectory_id)

        self.assertEqual([item["note"] for item in listed], ["Runs tests first — good", "Edits blind"])
        self.assertEqual(len(remaining), 1)
        self.assertIn("### Step 2 · tool", default)
        self.assertIn("### Step 3 · tool · turning point", default)
        self.assertIn("**Note (tester, premature_stop):** Runs tests first — good", default)
        self.assertIn("pytest -q", default)
        self.assertNotIn("### Step 1", default)
        self.assertIn("### Step 1", ranged)
        self.assertNotIn("grader", ranged)


class SearchTest(unittest.TestCase):
    def test_search_finds_steps_across_the_collection_and_survives_reimport(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            rows = [
                edit_sample("a_k3_attempt_01", task="Build the 渲染 pipeline", calls=[("B1", "Bash", {"command": "pytest tests/test_render.py"})]),
                edit_sample("b_k3_attempt_01", task="Write docs", calls=[("R1", "Read", {"file_path": "README.md"})]),
            ]
            rows[1]["messages"].insert(1, {"role": "assistant", "content": "渲染结果还不对，先看日志"})
            write_jsonl(base / "dialog.jsonl", rows)
            service = service_for(base)
            service.import_path(str(base / "dialog.jsonl"), "S")
            by_command = service.search_steps("pytest test_render")
            chinese = service.search_steps("渲染结果")
            service.import_path(str(base / "dialog.jsonl"), "S")
            again = service.search_steps("渲染结果")
            other = service.search_steps("渲染结果", collection="elsewhere")
            with self.assertRaises(ValueError):
                service.search_steps("  ")

        self.assertEqual([r["trajectory"]["run_id"] for r in by_command["results"]], ["a_k3_attempt_01"])
        self.assertEqual(by_command["results"][0]["hits"][0]["step"], 2)
        self.assertIn("pytest", by_command["results"][0]["hits"][0]["snippet"]["text"])
        [result] = chinese["results"]
        self.assertEqual(result["trajectory"]["run_id"], "b_k3_attempt_01")
        self.assertEqual(result["hits"][0]["snippet"]["field"], "text")
        # Re-importing replaces the index instead of duplicating hits.
        self.assertEqual(again["hits"], chinese["hits"])
        self.assertEqual(other["results"], [])

    def test_failed_reimport_keeps_text_and_crashed_import_leaves_no_duplicates(self) -> None:
        from unittest import mock

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            session = make_claude_session(base / "s.jsonl")
            service = service_for(base)
            service.import_path(str(session), "C")
            before = service.search_steps("pytest")["hits"]
            # A half-written line is skipped, not fatal.
            with session.open("ab") as handle:
                handle.write(b"\xff\xfe not utf-8\n")
            self.assertEqual(service.import_path(str(session), "C")["errors"], [])
            with mock.patch.object(service.store, "replace_source_trajectories", side_effect=RuntimeError("disk full")):
                report = service.import_path(str(session), "C")
            after = service.search_steps("pytest")["hits"]

            rows = [edit_sample(f"s{i}_k3_attempt_01", task=f"Task {i}", calls=[("B", "Bash", {"command": "make build"})]) for i in range(3)]
            write_jsonl(base / "dialog.jsonl", rows)
            with mock.patch.object(service.store, "replace_source_trajectories", side_effect=RuntimeError("killed")):
                service.import_path(str(base / "dialog.jsonl"), "D")
            service.import_path(str(base / "dialog.jsonl"), "D")
            hits = service.search_steps("make build", collection="D")

        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(before, after)
        self.assertEqual(sorted(len(group["hits"]) for group in hits["results"]), [1, 1, 1])

    def test_import_can_skip_the_text_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            service = service_for(base)
            service.import_path(str(make_sft_export(base / "sft")), "SFT", index_text=False)
            found = service.search_steps("tides")
        self.assertEqual(found["results"], [])


class EpisodeTest(unittest.TestCase):
    def test_segments_missing_placeholder_subagent_spawn_and_end_only_signals(self) -> None:
        edit = ("E", "Edit", {"file_path": "src/app.py", "old_string": "a", "new_string": "b"})
        rows = [
            edit_sample("x_k3_attempt_01_context_01", task="Build the app", calls=[
                ("Read_1", "Read", {"file_path": "spec.md"}),
                ("Agent_7", "Agent", {"description": "review", "prompt": "Review src/app.py"}),
                (edit[0] + "1", edit[1], edit[2]),
            ]),
            edit_sample("x_k3_attempt_01_context_03", task=None, calls=[(edit[0] + "3", edit[1], edit[2])],
                        tag={"context_segment": "3", "post_compaction": True}),
            edit_sample("x_k3_attempt_01_subagent_ab12", task="Review src/app.py", calls=[("Read_9", "Read", {"file_path": "src/app.py"})],
                        tag={"parent_tool_use_id": "Agent_7", "subagent_type": "CodeInspector"}),
        ]
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            write_jsonl(base / "dialog.jsonl", rows)
            service = service_for(base)
            service.import_path(str(base / "dialog.jsonl"), "SFT")
            episodes = service.list_trajectories(episodes=True)
            everything = service.list_trajectories()
            head = episodes["items"][0]
            tree = service.get_episode(head["id"])
            by_run = {item["run_id"]: item for item in everything["items"]}

        self.assertEqual((episodes["total"], everything["total"]), (1, 3))
        self.assertEqual(head["run_id"], "x_k3_attempt_01_context_01")
        self.assertEqual(head["episode"]["missing_segments"], [2])
        self.assertEqual(head["episode"]["subagents"], 1)
        self.assertEqual([lane.get("segment") for lane in tree["threads"]], [1, 2, 3])
        self.assertTrue(tree["threads"][1]["missing"])
        [sub] = tree["subagents"]
        self.assertEqual(sub["subagent_type"], "CodeInspector")
        self.assertEqual(sub["spawn"]["trajectory_id"], head["id"])
        self.assertEqual(sub["spawn"]["step"], 3)
        # "No check after the last edit" only applies where the episode really ends.
        self.assertNotIn("no_verify", by_run["x_k3_attempt_01_context_01"]["flags"])
        self.assertIn("no_verify", by_run["x_k3_attempt_01_context_03"]["flags"])
        self.assertIn("no_verify", head["episode"]["flags"])

    def test_claude_subagent_files_join_their_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            main = make_claude_session(base / "project" / "sess-1.jsonl")
            with main.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"type": "assistant", "sessionId": "sess-1", "uuid": "ag", "timestamp": "2026-09-01T10:00:29Z",
                                         "message": {"id": "api-ag", "role": "assistant", "content": [
                                             {"type": "tool_use", "id": "ag1", "name": "Agent", "input": {"description": "style check", "prompt": "Check parse.py style"}}]}}) + "\n")
            sub_dir = base / "project" / "sess-1" / "subagents"
            write_jsonl(sub_dir / "agent-a1.jsonl", [
                {"type": "user", "isSidechain": True, "agentId": "a1", "sessionId": "sess-1", "uuid": "s1",
                 "timestamp": "2026-09-01T10:00:30Z", "message": {"role": "user", "content": "Check parse.py style"}},
                {"type": "assistant", "isSidechain": True, "agentId": "a1", "sessionId": "sess-1", "uuid": "s2",
                 "timestamp": "2026-09-01T10:00:31Z", "message": {"id": "api-s", "role": "assistant", "content": [{"type": "text", "text": "Looks fine."}]}},
            ])
            write_json(sub_dir / "agent-a1.meta.json", {"agentType": "general-purpose", "description": "style check", "toolUseId": "ag1"})
            service = service_for(base)
            # Importing just the session file brings its subagents along.
            service.import_path(str(main), "Claude")
            episodes = service.list_trajectories(episodes=True)
            self.assertEqual(service.list_trajectories()["total"], 2)
            service.import_path(str(base / "project"), "Claude")
            episodes = service.list_trajectories(episodes=True)
            tree = service.get_episode(episodes["items"][0]["id"])
            main_row = next(item for item in service.list_trajectories()["items"] if item["path"] == str(main.resolve()))

        self.assertEqual(episodes["total"], 1)
        self.assertEqual(episodes["items"][0]["id"], main_row["id"])
        [sub] = tree["subagents"]
        self.assertEqual((sub["subagent_type"], sub["description"]), ("general-purpose", "style check"))
        self.assertEqual(sub["spawn"]["call_id"], "ag1")

    def test_codex_subagent_threads_nest_under_the_root_thread(self) -> None:
        def rollout(thread: str, parent: str | None, calls: list[dict]) -> list[dict]:
            source = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1, "agent_path": f"/root/{thread}", "agent_role": "explorer"}}} if parent else "cli"
            return [
                {"timestamp": "2026-09-02T08:00:00Z", "type": "session_meta", "payload": {"id": thread, "source": source}},
                {"timestamp": "2026-09-02T08:00:01Z", "type": "response_item",
                 "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"Task for {thread}"}]}},
                *calls,
            ]

        def spawn(call_id: str, child: str) -> list[dict]:
            return [
                {"timestamp": "2026-09-02T08:00:02Z", "type": "response_item",
                 "payload": {"type": "function_call", "name": "spawn_agent", "call_id": call_id, "arguments": "{}"}},
                {"timestamp": "2026-09-02T08:00:03Z", "type": "event_msg",
                 "payload": {"type": "item_completed", "item": {"type": "SubAgentActivity", "id": call_id, "kind": "started", "agent_thread_id": child}}},
                {"timestamp": "2026-09-02T08:00:03Z", "type": "response_item",
                 "payload": {"type": "function_call_output", "call_id": call_id, "output": "{\"task_name\":\"x\"}"}},
            ]

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            write_jsonl(base / "rollout-root.jsonl", rollout("root", None, spawn("c1", "child")))
            write_jsonl(base / "rollout-child.jsonl", rollout("child", "root", spawn("c2", "grandchild")))
            write_jsonl(base / "rollout-grandchild.jsonl", rollout("grandchild", "child", []))
            service = service_for(base)
            service.import_path(str(base), "Codex")
            episodes = service.list_trajectories(episodes=True)
            tree = service.get_episode(episodes["items"][0]["id"])

        self.assertEqual(episodes["total"], 1)
        self.assertEqual(episodes["items"][0]["run_id"], "rollout-root")
        spawns = {sub["run_id"]: sub["spawn"]["call_id"] for sub in tree["subagents"]}
        self.assertEqual(spawns, {"rollout-child": "c1", "rollout-grandchild": "c2"})

    def test_same_sample_ids_in_two_trial_dirs_stay_separate_episodes(self) -> None:
        from tests.fixtures import make_tracelab_trial

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            make_tracelab_trial(base / "trials" / "first", resolved=False)
            make_tracelab_trial(base / "trials" / "rerun", resolved=True)
            service = service_for(base)
            service.import_path(str(base / "trials"), "T")
            episodes = service.list_trajectories(episodes=True)
            stats = service.stats("T")
        self.assertEqual(episodes["total"], 2)
        self.assertEqual(stats["outcomes"], {"fail": 1, "pass": 1})

    def test_moving_a_source_regroups_the_collection_it_left(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            rows = [
                edit_sample("m_k3_attempt_01", task="Main task", calls=[]),
                edit_sample("m_k3_attempt_01_subagent_ab", task="Side job", calls=[], tag={"parent_tool_use_id": "x"}),
            ]
            write_jsonl(base / "one" / "dialog.jsonl", rows)
            write_jsonl(base / "two" / "dialog.jsonl", [edit_sample("n_k3_attempt_01", task="Other", calls=[])])
            service = service_for(base)
            service.import_path(str(base), "Old")
            service.import_path(str(base / "one"), "New")
            old = service.list_trajectories(collection="Old", episodes=True)
            new = service.list_trajectories(collection="New", episodes=True)
        self.assertEqual([item["run_id"] for item in old["items"]], ["n_k3_attempt_01"])
        self.assertEqual(new["total"], 1)
        self.assertEqual(new["items"][0]["episode"]["subagents"], 1)

    def test_episode_rows_see_member_reviews_and_subagent_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            edit = ("E", "Edit", {"file_path": "src/app.py", "old_string": "a", "new_string": "b"})
            rows = [
                edit_sample("r_k3_attempt_01_context_01", task="Build it", calls=[("R", "Read", {"file_path": "a"})]),
                edit_sample("r_k3_attempt_01_context_02", task=None, calls=[("R2", "Read", {"file_path": "b"})], tag={"context_segment": "2"}),
                edit_sample("r_k3_attempt_01_subagent_cd", task="Fix it", calls=[edit]),
            ]
            write_jsonl(base / "dialog.jsonl", rows)
            service = service_for(base)
            service.import_path(str(base / "dialog.jsonl"), "R")
            second = next(i for i in service.list_trajectories()["items"] if i["run_id"].endswith("context_02"))
            service.save_review(second["id"], {"human_status": "fail", "labels": ["no_verify"]})
            listed = service.list_trajectories(episodes=True)["items"][0]
            unread = service.list_trajectories(episodes=True, reviewed=False)["total"]
            labelled = service.list_trajectories(episodes=True, label="no_verify")["total"]
            subagent_flag = service.list_trajectories(episodes=True, flag="subagent_edit")["total"]
            sidebar = service.list_collections()["collections"][0]
            # A Jev flag on any member (here the subagent) finds the episode.
            subagent = next(i for i in service.list_trajectories()["items"] if "subagent" in i["run_id"])
            service.store.set_jev_summary(subagent["id"], {"version": "x", "flag_counts": {"harness_reliance": 2}})
            jev_episode = service.list_trajectories(episodes=True, jev_flag="harness_reliance")["total"]
            jev_rows = [i["run_id"] for i in service.list_trajectories(jev_flag="harness_reliance")["items"]]
            jev_other = service.list_trajectories(episodes=True, jev_flag="filler")["total"]
            service.store.put_jev(subagent["id"], "steps", "x", None, "m", 1000, {})
            usage_here, usage_elsewhere = service.store.jev_usage("R")["input_tokens"], service.store.jev_usage("other")["input_tokens"]
        self.assertIsNotNone(listed["reviewed_at"])
        self.assertEqual(listed["review_labels"], ["no_verify"])
        self.assertEqual((unread, labelled, subagent_flag), (0, 1, 1))
        self.assertEqual((jev_episode, jev_rows, jev_other), (1, ["r_k3_attempt_01_subagent_cd"], 0))
        self.assertEqual((usage_here, usage_elsewhere), (1000, 0))
        self.assertEqual((sidebar["episodes"], sidebar["reviewed"]), (1, 1))

    def test_export_jev_writes_one_line_per_trajectory(self) -> None:
        from trajectory_workbench.jev import STEP_VERSION

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            write_jsonl(base / "dialog.jsonl", [
                edit_sample("a_k3_attempt_01", task="Build it", calls=[("R", "Read", {"file_path": "a"})]),
                edit_sample("b_k3_attempt_01", task="Other", calls=[]),
            ])
            service = service_for(base)
            service.import_path(str(base / "dialog.jsonl"), "S")
            first = next(i for i in service.list_trajectories()["items"] if i["run_id"] == "a_k3_attempt_01")
            row = service.store.get_trajectory(first["id"])
            service.store.put_jev(first["id"], "steps", STEP_VERSION, row["fingerprint"], "m", 5, {"steps": [{"step": 3, "p": {}}]})
            result = service.export_jev("S", base / "out" / "jev.jsonl", "http://host:8416/")
            lines = [json.loads(line) for line in (base / "out" / "jev.jsonl").read_text().splitlines()]
            with self.assertRaises(ValueError):
                service.export_jev("S", base / "out" / "jev.jsonl")
        self.assertEqual((result["written"], result["without_jev"]), (2, 1))
        self.assertEqual([line["sample_id"] for line in lines], ["a_k3_attempt_01", "b_k3_attempt_01"])
        self.assertEqual(set(lines[0]), {"sample_id", "trajectory_id", "source_path", "source_row_0based", "source_line_1based", "trajectory_url", "jev", "step_index", "timeline"})
        self.assertEqual((lines[1]["source_row_0based"], lines[1]["source_line_1based"]), (1, 2))
        self.assertEqual(lines[0]["trajectory_url"], "http://host:8416/#/t/" + first["id"])
        self.assertEqual(lines[0]["jev"]["steps"], {"steps": [{"step": 3, "p": {}}]})
        self.assertIsNone(lines[1]["jev"])

    def test_opening_an_older_database_regroups_its_episodes(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            service = service_for(base)
            service.import_path(str(make_sft_export(base / "sft")), "SFT")
            service.store.close()
            db = sqlite3.connect(base / "index.db")
            db.execute("DROP INDEX trajectories_episode")
            for column in ("episode", "episode_head", "spawn_calls", "extra", "self_key", "parent_key", "parent_call_id"):
                db.execute(f"ALTER TABLE trajectories DROP COLUMN {column}")
            db.commit()
            db.close()
            reopened = service_for(base)
            total = reopened.list_trajectories(episodes=True)["total"]
        self.assertEqual(total, 2)

    def test_only_the_last_segment_can_be_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            first = edit_sample("s_k3_attempt_01_context_01", task="Build", calls=[("R", "Read", {"file_path": "a"})])
            first["messages"].pop()  # ends on the tool result: compaction cut it here
            last = edit_sample("s_k3_attempt_01_context_02", task=None, calls=[("R2", "Read", {"file_path": "b"})], tag={"context_segment": "2"})
            last["messages"].pop()
            write_jsonl(base / "dialog.jsonl", [first, last])
            service = service_for(base)
            service.import_path(str(base / "dialog.jsonl"), "S")
            rows = {item["run_id"]: item for item in service.list_trajectories()["items"]}
            not_ready = service.list_trajectories(ready=False)["total"]
        self.assertTrue(rows["s_k3_attempt_01_context_01"]["readiness"]["ready"])
        self.assertFalse(rows["s_k3_attempt_01_context_02"]["readiness"]["ready"])
        self.assertEqual(not_ready, 1)

    def test_removing_a_collection_clears_the_index_but_not_the_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = make_sft_export(base / "sft")
            before = source.read_bytes()
            service = service_for(base)
            service.import_path(str(source), "Gone")
            service.import_path(str(make_claude_session(base / "c.jsonl")), "Kept")
            trajectory_id = service.list_trajectories(collection="Gone")["items"][0]["id"]
            service.save_annotation(trajectory_id, {"step": 1, "note": "x"})
            removed = service.store.remove_collection("Gone")
            left = [item["collection"] for item in service.store.collections()]
            found = service.search_steps("tides")
            after = source.read_bytes()
        self.assertEqual(removed["trajectories"], 4)
        self.assertEqual(removed["annotations"], 1)
        self.assertEqual(left, ["Kept"])
        self.assertEqual(found["results"], [])
        self.assertEqual(before, after)

    def test_reimport_keeps_the_jev_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            path = make_sft_export(base / "sft")
            service = service_for(base)
            service.import_path(str(path), "SFT")
            trajectory_id = service.list_trajectories()["items"][0]["id"]
            service.store.set_jev_summary(trajectory_id, {"version": "x", "flagged": [3]})
            service.import_path(str(path), "SFT")
            kept = service.store.get_trajectory(trajectory_id)["jev_summary"]
        self.assertEqual(kept, {"version": "x", "flagged": [3]})


if __name__ == "__main__":
    unittest.main()
