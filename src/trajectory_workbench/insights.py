from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any


def error_aggregation(normalized: Any) -> dict[str, Any]:
    tools = normalized.tools
    errors: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        result = tool.get("result") or {}
        if not result.get("is_error"):
            continue
        recovery = _find_recovery(tools, index)
        errors.append(
            {
                "tool_id": tool["id"],
                "name": tool["name"],
                "input": tool.get("input", {}),
                "result_text": result.get("text", "")[:500],
                "offset_ms": tool.get("offset_ms", 0),
                "line_number": tool.get("line_number"),
                "recovery": recovery,
            }
        )

    by_name: dict[str, int] = {}
    for error in errors:
        by_name[error["name"]] = by_name.get(error["name"], 0) + 1

    retry_count = sum(1 for e in errors if e["recovery"] is not None)

    return {
        "error_count": len(errors),
        "errors": errors,
        "by_name": dict(sorted(by_name.items(), key=lambda item: -item[1])),
        "retry_count": retry_count,
        "recovery_rate": round(retry_count / len(errors), 3) if errors else None,
    }


def _find_recovery(tools: list[dict[str, Any]], error_index: int) -> dict[str, Any] | None:
    error_tool = tools[error_index]
    error_name = error_tool["name"]
    for follower in tools[error_index + 1 :]:
        if follower["name"] != error_name:
            continue
        follower_result = follower.get("result") or {}
        if not follower_result.get("is_error"):
            return {
                "tool_id": follower["id"],
                "offset_ms": follower.get("offset_ms", 0),
                "attempts_later": sum(
                    1
                    for middle in tools[error_index + 1 :]
                    if middle["name"] == error_name
                ),
            }
    return None


def artifact_diff(normalized: Any, root: Path) -> dict[str, Any]:
    states = normalized.artifact_states
    if len(states) < 2:
        return {"state_count": len(states), "diffs": []}

    diffs: list[dict[str, Any]] = []
    for before, after in zip(states, states[1:]):
        before_lines = _read_artifact_lines(root, before["artifact_sha256"])
        after_lines = _read_artifact_lines(root, after["artifact_sha256"])
        if before_lines is None or after_lines is None:
            diffs.append(
                {
                    "from_sha": before["artifact_sha256"][:10],
                    "to_sha": after["artifact_sha256"][:10],
                    "from_offset_ms": before.get("received_offset_ms", 0),
                    "to_offset_ms": after.get("received_offset_ms", 0),
                    "from_size": before.get("size_bytes", 0),
                    "to_size": after.get("size_bytes", 0),
                    "added": 0,
                    "removed": 0,
                    "unavailable": True,
                }
            )
            continue
        diff = list(
            difflib.unified_diff(before_lines, after_lines, lineterm="", n=0)
        )
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        diffs.append(
            {
                "from_sha": before["artifact_sha256"][:10],
                "to_sha": after["artifact_sha256"][:10],
                "from_offset_ms": before.get("received_offset_ms", 0),
                "to_offset_ms": after.get("received_offset_ms", 0),
                "from_size": before.get("size_bytes", 0),
                "to_size": after.get("size_bytes", 0),
                "added": added,
                "removed": removed,
                "unavailable": False,
            }
        )

    return {"state_count": len(states), "diffs": diffs}


def _read_artifact_lines(root: Path, sha256: str) -> list[str] | None:
    import hashlib

    candidates = sorted(root.glob("attempts/**/artifact.html"))
    candidates.extend(sorted(root.glob("attempts/**/records/**/*.html")))
    for candidate in candidates:
        try:
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest == sha256:
            try:
                return candidate.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                return None
    return None


def compare_runs(runs: list[Any]) -> dict[str, Any]:
    if len(runs) < 2:
        return {"run_count": len(runs), "comparison": None}

    metrics: dict[str, Any] = {}
    for run in runs:
        metrics[run.run_id] = {
            "label": run.run_id,
            "metrics": run.metrics,
            "runtime": run.runtime.get("classification", "unknown"),
            "tool_counts": {t["name"]: t.get("call_count", 0) for t in run.tool_catalog},
            "error_count": sum(
                1 for t in run.tools if (t.get("result") or {}).get("is_error")
            ),
        }

    all_tools: set[str] = set()
    for entry in metrics.values():
        all_tools.update(entry["tool_counts"])

    tool_diff: dict[str, Any] = {}
    for tool in sorted(all_tools):
        row: dict[str, Any] = {}
        for run_id, entry in metrics.items():
            row[run_id] = entry["tool_counts"].get(tool, 0)
        if len(set(row.values())) > 1:
            tool_diff[tool] = row

    return {
        "run_count": len(runs),
        "comparison": {
            "metrics": metrics,
            "tool_diff": tool_diff,
            "shared_tools": sorted(
                {
                    tool
                    for tool in all_tools
                    if all(entry["tool_counts"].get(tool, 0) > 0 for entry in metrics.values())
                }
            ),
        },
    }
