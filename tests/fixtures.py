from __future__ import annotations

import json
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def make_moh_run(
    root: Path, *, extra_tool_names: tuple[str, ...] = ()
) -> Path:
    records = root / "attempts" / "001" / "records"
    workspace = root / "attempts" / "001" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "artifact.html").write_text("<main>deck</main>", encoding="utf-8")
    sheet = workspace / ".rsi-moh-slides-workbench" / "contact-sheet-001.png"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"png")

    workbench_payload = {
        "status": "completed",
        "artifact_sha256": "bbb",
        "contact_sheet_path": ".rsi-moh-slides-workbench/contact-sheet-001.png",
        "diagnostics": {"returned_count": 4, "total_count": 9},
        "timings_ms": {"total": 1234},
    }
    trajectory = [
        {
            "type": "system",
            "subtype": "init",
            "model": "kimi-k3",
            "tools": [
                "Task",
                "Bash",
                "WebSearch",
                "mcp__slides_workbench__slides_workbench",
            ],
        },
        {"type": "system", "subtype": "thinking_tokens", "count": 8},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Inspect files"}]}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {"command": "pwd"}}
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"}]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "wb-1",
                        "name": "mcp__slides_workbench__slides_workbench",
                        "input": {"action": "inspect"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "wb-1",
                        "content": [{"type": "text", "text": json.dumps(workbench_payload)}],
                    }
                ]
            },
        },
    ]
    for tool_index, extra_tool_name in enumerate(extra_tool_names, start=1):
        tool_id = f"future-{tool_index}"
        trajectory.extend(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": extra_tool_name,
                                "input": {"prompt": "draw a diagram"},
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": "future tool result",
                            }
                        ]
                    },
                },
            ]
        )
    trajectory.append(
        {"type": "result", "subtype": "success", "total_cost_usd": 1.25, "result": "done"}
    )
    write_jsonl(records / "trajectory.jsonl", trajectory)
    write_jsonl(
        records / "trajectory_index.jsonl",
        [
            {"line_number": index, "received_offset_ms": index * 1000}
            for index in range(1, len(trajectory) + 1)
        ],
    )
    write_json(
        records / "artifact_state_index.json",
        {
            "observations": [
                {
                    "received_offset_ms": 4000,
                    "net_change": "created",
                    "size_bytes": 10,
                    "artifact_sha256": "aaa",
                },
                {
                    "received_offset_ms": 5000,
                    "net_change": "modified",
                    "size_bytes": 10,
                    "artifact_sha256": "aaa",
                },
                {
                    "received_offset_ms": 7000,
                    "net_change": "modified",
                    "size_bytes": 11,
                    "artifact_sha256": "bbb",
                },
            ]
        },
    )
    write_json(records / "process.json", {"terminal_reason": "completed", "exit_code": 0})
    write_json(records / "attempt_terminal.json", {"agent_result_status": "record_invalid"})
    write_jsonl(
        root / "events.jsonl",
        [
            {
                "sequence": 1,
                "event_type": "run.failed",
                "payload": {
                    "classification": "artifact_invalid",
                    "exception_message": "sealed observation missing",
                },
            }
        ],
    )
    write_json(
        root / "resolved_run_manifest.json",
        {
            "agent": {
                "adapter_facts": {"native_tool_surface": ["default"]},
                "capability_bindings": {
                    "bindings": [
                        {"capability_id": "slides_workbench"},
                        {"capability_id": "web_search"},
                    ]
                },
            },
            "execution_profile": {
                "agent": {"allowed_tools": ["slides_workbench", "web_search"]}
            },
        },
    )
    return root


PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def make_claude_session(path: Path) -> Path:
    """A Claude Code session file: split assistant blocks, a hook attachment, a failed
    tool call retried with the same input, and a slash-command echo."""
    common = {"sessionId": "sess-1", "cwd": "/work", "version": "2.1.0"}
    rows = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "sess-1"},
        {**common, "type": "user", "uuid": "u1", "timestamp": "2026-09-01T10:00:00Z",
         "message": {"role": "user", "content": "<system-reminder>ctx</system-reminder>\nFix the failing parser test in src/parse.py"}},
        {**common, "type": "attachment", "uuid": "a1", "timestamp": "2026-09-01T10:00:01Z",
         "attachment": {"type": "hook_additional_context", "content": ["[lint-hook] remember to run ruff"]}},
        {**common, "type": "assistant", "uuid": "x1", "timestamp": "2026-09-01T10:00:05Z",
         "message": {"id": "api-1", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "thinking", "thinking": "Run the tests first."}],
                     "usage": {"input_tokens": 100, "output_tokens": 10}}},
        {**common, "type": "assistant", "uuid": "x2", "timestamp": "2026-09-01T10:00:06Z",
         "message": {"id": "api-1", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest tests/test_parse.py"}}],
                     "usage": {"input_tokens": 100, "output_tokens": 10}}},
        {**common, "type": "user", "uuid": "r1", "timestamp": "2026-09-01T10:00:09Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "Exit code 1\nFAILED"}]}},
        {**common, "type": "assistant", "uuid": "x3", "timestamp": "2026-09-01T10:00:10Z",
         "message": {"id": "api-2", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pytest tests/test_parse.py"}}]}},
        {**common, "type": "user", "uuid": "r2", "timestamp": "2026-09-01T10:00:12Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": [
             {"type": "text", "text": "Exit code 1"},
             {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_BASE64}}]}]}},
        {**common, "type": "assistant", "uuid": "x4", "timestamp": "2026-09-01T10:00:20Z",
         "message": {"id": "api-3", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "tool_use", "id": "t3", "name": "Edit", "input": {"file_path": "src/parse.py", "old_string": "a", "new_string": "b"}}]}},
        {**common, "type": "user", "uuid": "r3", "timestamp": "2026-09-01T10:00:21Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t3", "content": "ok"}]}},
        {**common, "type": "assistant", "uuid": "x5", "timestamp": "2026-09-01T10:00:30Z",
         "message": {"id": "api-4", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "text", "text": "Fixed the parser. All done."}]}},
        {**common, "type": "user", "uuid": "c1", "timestamp": "2026-09-01T10:00:40Z",
         "message": {"role": "user", "content": "<command-name>/compact</command-name>"}},
        {"type": "cost-state", "sessionId": "sess-1", "totalCostUSD": 0.5},
    ]
    write_jsonl(path, rows)
    return path


def make_codex_rollout(path: Path) -> Path:
    rows = [
        {"timestamp": "2026-09-02T08:00:00Z", "type": "session_meta",
         "payload": {"id": "thread-1", "cwd": "/repo", "cli_version": "0.150.0", "originator": "codex_cli"}},
        {"timestamp": "2026-09-02T08:00:00Z", "type": "turn_context", "payload": {"model": "gpt-test", "effort": "high"}},
        {"timestamp": "2026-09-02T08:00:01Z", "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [
             {"type": "input_text", "text": "# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>be careful</INSTRUCTIONS>"},
             {"type": "input_text", "text": "<environment_context><cwd>/repo</cwd></environment_context>"}]}},
        {"timestamp": "2026-09-02T08:00:02Z", "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Add a --dry-run flag to the CLI"}]}},
        {"timestamp": "2026-09-02T08:00:03Z", "type": "response_item",
         "payload": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Look at cli.py first"}]}},
        {"timestamp": "2026-09-02T08:00:04Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "shell", "call_id": "c1", "arguments": "{\"command\": [\"cat\", \"cli.py\"]}"}},
        {"timestamp": "2026-09-02T08:00:05Z", "type": "response_item",
         "payload": {"type": "function_call_output", "call_id": "c1", "output": "{\"output\": \"def main(): ...\", \"metadata\": {\"exit_code\": 0}}"}},
        {"timestamp": "2026-09-02T08:00:06Z", "type": "response_item",
         "payload": {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c2",
                     "input": "*** Begin Patch\n*** Update File: tests/test_cli.py\n@@\n-assert x\n*** End Patch"}},
        {"timestamp": "2026-09-02T08:00:07Z", "type": "response_item",
         "payload": {"type": "custom_tool_call_output", "call_id": "c2", "output": "Exit code: 1\nWall time: 0.1 seconds\nOutput:\npatch failed"}},
        {"timestamp": "2026-09-02T08:00:08Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 4321}}}},
        {"timestamp": "2026-09-02T08:00:09Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Added the flag."}]}},
        {"timestamp": "2026-09-02T08:00:10Z", "type": "event_msg", "payload": {"type": "task_complete"}},
    ]
    write_jsonl(path, rows)
    return path


def chat_sample(sample_id: str, task: str, *, extra_user: list | None = None, tag: dict | None = None) -> dict:
    return {
        "id": sample_id,
        "source": "moh-slides",
        "synthetic_model": "kimi-test",
        "tag": {"code_agent": "moh-slides", "context_segment": "1", **(tag or {})},
        "tools": [{"type": "function", "function": {"name": "Read"}}, {"type": "function", "function": {"name": "Bash"}}],
        "messages": [
            {"role": "system", "content": "You are Claude Code."},
            {"role": "user", "content": "<system-reminder>\ntoday\n</system-reminder>\n"},
            {"role": "user", "content": task},
            {"role": "assistant", "content": "", "reasoning_content": "Look at the input.",
             "tool_calls": [{"id": "Read_0", "type": "function", "function": {"name": "Read", "arguments": {"file_path": "shot.png"}}}]},
            {"role": "tool", "tool_call_id": "Read_0", "name": "Read", "content": "Image result attached in the following user message."},
            {"role": "user", "content": [{"type": "text", "text": "Image returned by tool Read_0."},
                                         {"type": "image_url", "image_url": {"url": "/data/images/ab.png"}}]},
            *(extra_user or []),
            {"role": "assistant", "content": "Deck written.", "reasoning_content": "Done."},
        ],
    }


def make_sft_export(root: Path, results_root: Path | None = None) -> Path:
    """A multi-sample SFT file with a main trajectory, a post-compaction segment and a
    subagent, plus a manifest.json pointing back at TraceLab trial results."""
    root.mkdir(parents=True, exist_ok=True)
    rows = [
        chat_sample("t-001_k3_attempt_01", "Build a 5 slide deck about tides"),
        chat_sample("t-002_k3_attempt_01_context_01", "Build a 9 slide deck about volcanoes"),
        chat_sample(
            "t-002_k3_attempt_01_context_02",
            "This session is being continued from a previous conversation that ran out of context.",
            tag={"context_segment": "2", "post_compaction": True},
        ),
        chat_sample("t-002_k3_attempt_01_subagent_abc123", "Review the deck for overflow"),
    ]
    write_jsonl(root / "dialog.jsonl", rows)
    if results_root is not None:
        inputs = []
        for task, count, resolved in (("t-001", 1, True), ("t-002", 3, False)):
            trial = results_root / f"{task}_trial"
            write_json(trial / "results.json", {"results": [tracelab_result(task, resolved)]})
            inputs.append({"path": str(trial / "dialog.jsonl"), "records": count})
        write_json(root / "manifest.json", {"format": "agent-sft-jsonl", "input_files": inputs})
    return root / "dialog.jsonl"


def tracelab_result(task: str, resolved: bool, classification: str = "evaluation_failed", **extra) -> dict:
    """A TraceLab results.json row shaped like real MoH exports: a failed MoH job has no
    score and names its classification only inside the exception message."""
    row = {"task_id": task, "is_resolved": resolved, "average_score": 1.0 if resolved else None,
           "failure_mode": None if resolved else "ValueError", "extra_info": {"exception": None}}
    if not resolved:
        row["extra_info"]["exception"] = {
            "exception_type": "ValueError",
            "exception_message": f"MoH job failed: exit=10, classification='{classification}'",
        }
    row.update(extra)
    return row


def make_tracelab_trial(root: Path, *, resolved: bool, classification: str = "evaluation_failed") -> Path:
    write_jsonl(root / "dialog.jsonl", [chat_sample("x-9_k3_attempt_01", "Write a haiku deck")])
    write_json(root / "results.json", {"results": [tracelab_result(
        "x-9", resolved, classification,
        agent_started_at="2026-09-01T00:00:00Z", agent_ended_at="2026-09-01T00:10:00Z",
        total_input_tokens=1000, total_output_tokens=50)]})
    write_json(root / "run_metadata.json", {"agent_name": "moh-slides", "agent_version": "2.1", "model_name": "kimi-test", "source_schema": "ATIF-v1.7"})
    # Exports nest a second copy one level down; discovery must not import it again.
    nested = root / "x-9" / "x-9__abc"
    write_jsonl(nested / "dialog.jsonl", [chat_sample("x-9_k3_attempt_01", "Write a haiku deck")])
    write_json(nested / "results.json", {"results": [{"task_id": "x-9", "is_resolved": resolved}]})
    return root


def atif_document() -> dict:
    return {
        "schema_version": "ATIF-v1.8",
        "session_id": "s-1",
        "trajectory_id": "main",
        "agent": {"name": "claude-code", "version": "2.1", "model_name": "claude-test",
                  "tool_definitions": [{"name": "Bash"}]},
        "steps": [
            {"step_id": 1, "source": "system", "message": "You are an agent."},
            {"step_id": 2, "source": "user", "message": "Make hello.txt say hi", "timestamp": "2026-09-03T00:00:00Z"},
            {"step_id": 3, "source": "agent", "message": "", "reasoning_content": "Write the file.",
             "timestamp": "2026-09-03T00:00:02Z",
             "tool_calls": [{"tool_call_id": "call-1", "function_name": "Bash", "arguments": {"command": "echo hi > hello.txt"}}],
             "observation": {"results": [{"source_call_id": "call-1", "content": ""}]}},
            {"step_id": 4, "source": "agent", "message": "Done.", "timestamp": "2026-09-03T00:00:05Z"},
        ],
        "final_metrics": {"total_prompt_tokens": 300, "total_completion_tokens": 20, "total_cost_usd": 0.01},
        "subagent_trajectories": [
            {"schema_version": "ATIF-v1.8", "trajectory_id": "sub-1", "agent": {"name": "reviewer", "version": "1"},
             "steps": [{"step_id": 1, "source": "user", "message": "Check hello.txt"},
                       {"step_id": 2, "source": "agent", "message": "Looks right."}]}
        ],
    }


def make_harbor_trial(root: Path, reward: float, exception_type: str | None = None) -> Path:
    write_json(root / "agent" / "trajectory.json", atif_document())
    result = {"task_name": "hello-task", "trial_name": "hello-task__1",
              "verifier_result": {"rewards": {"reward": reward}}}
    if exception_type:
        result["exception_info"] = {"exception_type": exception_type, "exception_message": "boom"}
    write_json(root / "result.json", result)
    return root
