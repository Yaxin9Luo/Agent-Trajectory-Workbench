"""Markdown excerpts of chosen steps, for writing up a case (with the reader's notes)."""

from __future__ import annotations

import json
import re
from typing import Any

from trajectory_workbench.adapters.common import split_task


OUTPUT_LIMIT = 1200
INPUT_LIMIT = 800
TEXT_LIMIT = 4000


def parse_steps(spec: str, last: int) -> list[int]:
    """"3-9, 15" → [3, 4, …, 9, 15], clamped to 1…last (at most 200 steps)."""
    steps: set[int] = set()
    for part in re.split(r"[,\s]+", spec or ""):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(f"cannot read step range: {part}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end < start:
            start, end = end, start
        steps.update(range(max(1, start), min(last, end) + 1))
        if len(steps) > 200:
            raise ValueError("an excerpt holds at most 200 steps")
    return sorted(steps)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… [{len(text) - limit} more characters]"


def _fence(text: str, language: str = "") -> str:
    ticks = "```"
    while ticks in text:
        ticks += "`"
    return f"{ticks}{language}\n{text}\n{ticks}"


def _prose(text: str) -> str:
    """Transcript text outside code fences: HTML is escaped (Markdown viewers render it)
    and lines that would read as headings or fences are neutralised."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "\n".join("\\" + line if line.lstrip().startswith(("#", "```", "~~~")) else line for line in text.splitlines())


def _quote(text: str) -> str:
    return "\n".join("> " + line if line else ">" for line in _prose(text).splitlines())


def markdown_excerpt(
    run: Any,
    row: dict[str, Any],
    steps: list[int],
    annotations: list[dict[str, Any]],
    review: dict[str, Any] | None,
    *,
    hide_outcome: bool = False,
) -> str:
    notes: dict[int, list[dict[str, Any]]] = {}
    for item in annotations:
        notes.setdefault(item["step"], []).append(item)
    wanted = set(steps)
    outcome = run.outcome or {}
    lines = [f"# {row.get('title') or run.title}", ""]
    facts = [
        f"collection `{row.get('collection')}`",
        f"`{run.adapter_id}`",
        f"model `{row.get('model') or (run.meta or {}).get('model') or '?'}`",
        f"{len(run.messages)} steps",
    ]
    if not hide_outcome:
        facts.append(f"grader **{outcome.get('status', 'unknown')}**" + (f" ({outcome['reason']})" if outcome.get("reason") else ""))
    lines += [" · ".join(facts), "", f"Source: `{run.source_path}`" + (f" # {run.run_id}" if run.run_id else ""), ""]
    instruction = (run.task or {}).get("instruction") or row.get("instruction")
    if instruction:
        request, harness = split_task(instruction)
        lines += ["## Task", "", _quote(_clip(request, 1500)), ""]
        if harness:
            lines += ["*(The harness wrapped this request in JSON with its own instructions; they are omitted here.)*", ""]
    if review and (review.get("human_status") or review.get("labels") or review.get("turning_step") or review.get("hypothesis")):
        lines += ["## Review", ""]
        if review.get("human_status"):
            lines.append(f"- Human verdict: **{review['human_status']}**")
        if review.get("labels"):
            lines.append("- Labels: " + ", ".join(f"`{label}`" for label in review["labels"]))
        if review.get("turning_step"):
            lines.append(f"- Turning point: step {review['turning_step']}" + (f" — {review['turning_note']}" if review.get("turning_note") else ""))
        if review.get("hypothesis"):
            lines.append(f"- Hypothesis: {review['hypothesis']}")
        if review.get("intervention"):
            lines.append(f"- Intervention: `{review['intervention']}`")
        lines.append("")
    lines += ["## Steps", ""]
    previous = None
    for message in run.messages:
        step = message["step"]
        if step not in wanted:
            continue
        if previous is not None and step != previous + 1:
            lines += [f"*… {step - previous - 1} steps omitted …*", ""]
        previous = step
        heading = f"### Step {step} · {message.get('layer') or message['role']}"
        if review and review.get("turning_step") == step:
            heading += " · turning point"
        lines += [heading, ""]
        for note in notes.get(step, []):
            lines += [f"**Note ({note['reviewer']}{', ' + note['label'] if note.get('label') else ''}):** {_prose(note['note'])}", ""]
        if message.get("thinking"):
            lines += ["*Reasoning:*", "", _quote(_clip(message["thinking"], TEXT_LIMIT)), ""]
        if message.get("text"):
            lines += [_prose(_clip(message["text"], TEXT_LIMIT)), ""]
        for tool in message.get("tools") or []:
            tool_input = tool.get("input")
            rendered = tool_input if isinstance(tool_input, str) else json.dumps(tool_input, ensure_ascii=False, indent=2, default=str)
            result = tool.get("result") or {}
            status = "error" if result.get("is_error") else "result"
            lines += [f"**{tool['name']}** call:", "", _fence(_clip(rendered, INPUT_LIMIT), "json" if not isinstance(tool_input, str) else ""), ""]
            if tool.get("result") is not None:
                images = len(result.get("images") or [])
                lines += [f"{status}" + (f" (+{images} image{'s' if images > 1 else ''})" if images else "") + ":", "", _fence(_clip(result.get("text") or "", OUTPUT_LIMIT)), ""]
    return "\n".join(lines).rstrip() + "\n"
