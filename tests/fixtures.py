from __future__ import annotations

import json
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def make_moh_run(root: Path) -> Path:
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
        {"type": "system", "subtype": "init", "model": "kimi-k3"},
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
        {"type": "result", "subtype": "success", "total_cost_usd": 1.25, "result": "done"},
    ]
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
    return root

