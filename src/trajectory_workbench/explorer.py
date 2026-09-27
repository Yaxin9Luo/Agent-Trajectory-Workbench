"""Tool-call and error-template explorer across collections.

At import each trajectory keeps per-tool call / error counts and the normalized first
line of every failed call ("error template": paths, numbers, hashes and quoted strings
replaced by placeholders). Here they are aggregated per collection so two collections
(two checkpoints, two harness versions) can be compared tool by tool and error by error.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any


EXIT_LINE = re.compile(
    r"^\s*(?:Exit code:?\s*-?\d+|Process exited with code -?\d+|Script (?:failed|error|completed)[^\n]*|"
    r"Script running with cell ID[^\n]*|Wall time[^\n]*|Output:|Chunk ID[^\n]*)\s*$",
    re.IGNORECASE,
)
ERROR_WORDS = re.compile(
    r"error|exception|fail|not found|no such|denied|cannot|can't|invalid|refused|timed? ?out|"
    r"unknown|missing|not allowed|blocked|requires approval|does not exist|undefined",
    re.IGNORECASE,
)
PLACEHOLDERS = [
    (re.compile(r"<tool_use_error>|</tool_use_error>"), ""),
    (re.compile(r"(?:[A-Za-z]:)?(?:~|\.{1,2})?/[\w.@%+~\-/]+"), "<path>"),
    (re.compile(r"\b[\w.-]+\.(?:py|js|ts|tsx|jsx|mjs|json|html|css|md|txt|png|jpg|svg|yaml|yml|toml|sh|go|rs|java)\b"), "<file>"),
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{8,}\b"), "<hex>"),
    (re.compile(r"'[^'\n]{0,200}'|\"[^\"\n]{0,200}\"|`[^`\n]{0,200}`"), "<str>"),
    (re.compile(r"\d+(?:\.\d+)?"), "<n>"),
    (re.compile(r"\s+"), " "),
]
MAX_TEMPLATES_PER_RUN = 40
# Index columns the explorer reads (not the heavy signals / transcript fields).
COLUMNS = ("id", "title", "outcome_status", "tool_calls", "tool_stats", "error_templates")


def error_template(text: str) -> str:
    """The first informative line of an error output with the variable parts masked."""
    lines = [line.strip() for line in (text or "").splitlines()[:200] if line.strip() and not EXIT_LINE.match(line)]
    if lines and lines[0].startswith("{"):
        # An MCP result object: use the text it carries.
        try:
            value = json.loads("\n".join(lines))
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and isinstance(value.get("content"), list):
            inner = " ".join(str(part.get("text", "")) for part in value["content"] if isinstance(part, dict))
            lines = [line.strip() for line in inner.splitlines() if line.strip()] or lines
    line = lines[0] if lines else ""
    if line.startswith("Traceback"):
        # The exception is the last line of the traceback.
        line = next((item for item in reversed(lines) if not item.startswith(("File ", "^", "~"))), line)
    elif not ERROR_WORDS.search(line):
        # Commands print output before failing; the line that names the error is better.
        line = next((item for item in lines[:60] if ERROR_WORDS.search(item)), lines[-1] if lines else "")
    line = line[:240]
    for pattern, replacement in PLACEHOLDERS:
        line = pattern.sub(replacement, line)
    return line.strip()[:160] or "(empty error output)"


def run_tool_stats(run: Any) -> tuple[dict[str, list[int]], list[dict[str, Any]]]:
    """({tool: [calls, errors]}, [{tool, template, step}]) for one trajectory."""
    stats: dict[str, list[int]] = {}
    templates: list[dict[str, Any]] = []
    for tool in run.tools:
        entry = stats.setdefault(tool["name"], [0, 0])
        entry[0] += 1
        result = tool.get("result") or {}
        if result.get("is_error"):
            entry[1] += 1
            if len(templates) < MAX_TEMPLATES_PER_RUN:
                templates.append({"tool": tool["name"], "template": error_template(result.get("text") or ""), "step": tool["step"]})
    return stats, templates


FAILED = {"fail", "error"}


def summarize_collection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-tool usage and per-template error counts for one collection's trajectories."""
    total = len(rows)
    outcomes = Counter(row.get("outcome_status") or "unknown" for row in rows)
    tools: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, "errors": 0, "trajectories": 0, "pass": 0, "fail": 0})
    templates: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        status = row.get("outcome_status") or "unknown"
        for name, (calls, errors) in (row.get("tool_stats") or {}).items():
            entry = tools[name]
            entry["calls"] += calls
            entry["errors"] += errors
            entry["trajectories"] += 1
            entry["pass"] += status == "pass"
            entry["fail"] += status in FAILED
        seen: set[tuple[str, str]] = set()
        for item in row.get("error_templates") or []:
            key = (item["tool"], item["template"])
            entry = templates.setdefault(key, {"tool": key[0], "template": key[1], "count": 0, "trajectories": 0, "pass": 0, "fail": 0, "examples": []})
            entry["count"] += 1
            if key not in seen:
                seen.add(key)
                entry["trajectories"] += 1
                entry["pass"] += status == "pass"
                entry["fail"] += status in FAILED
                if len(entry["examples"]) < 5:
                    entry["examples"].append({"id": row["id"], "step": item["step"], "title": row.get("title"), "outcome_status": status})
    # Rows imported before tool statistics were kept: re-importing fills them in.
    stale = sum(1 for row in rows if row.get("tool_stats") is None and (row.get("tool_calls") or 0) > 0)
    tool_rows = []
    for name, entry in tools.items():
        tool_rows.append({
            "tool": name,
            **entry,
            "calls_per_trajectory": round(entry["calls"] / total, 3) if total else 0,
            "error_rate": round(entry["errors"] / entry["calls"], 3) if entry["calls"] else 0,
            "usage_share": round(entry["trajectories"] / total, 3) if total else 0,
        })
    tool_rows.sort(key=lambda item: -item["calls"])
    template_rows = sorted(templates.values(), key=lambda item: (-item["trajectories"], -item["count"]))
    for item in template_rows:
        item["share"] = round(item["trajectories"] / total, 3) if total else 0
    return {"trajectories": total, "outcomes": dict(outcomes), "tools": tool_rows, "templates": template_rows[:200], "stale": stale}
