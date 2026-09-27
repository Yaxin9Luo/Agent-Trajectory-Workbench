"""Before / after comparison of a rewritten trajectory, and training samples from
reviewers' corrections.

Rewrite diff: a rewrite pass (e.g. stripping harness residue from SFT data) keeps the
sample id, so an original and a rewritten collection pair up by id. Messages are aligned
by role and content; changed ones get a line diff of their text, reasoning, tool input and
tool output, plus what happened to the trained tokens and the harness residue.

Corrections: a reviewer marks the turning step and writes what the agent should have
done there. That yields an SFT sample (context up to the step + the correction) and a DPO
pair (same context; chosen = correction, rejected = what the agent actually did).
"""

from __future__ import annotations

import difflib
import json
from dataclasses import replace
from typing import Any

from trajectory_workbench.exporter import chat_sample


def _fields(message: dict[str, Any]) -> dict[str, str]:
    fields = {"text": message.get("text") or "", "thinking": message.get("thinking") or ""}
    for index, tool in enumerate(message.get("tools") or []):
        tool_input = tool.get("input")
        fields[f"call {index + 1} · {tool['name']}"] = tool_input if isinstance(tool_input, str) else json.dumps(tool_input, ensure_ascii=False, indent=2, default=str)
        if tool.get("result") is not None:
            fields[f"result {index + 1} · {tool['name']}"] = (tool["result"] or {}).get("text") or ""
    return fields


def _signature(message: dict[str, Any]) -> str:
    fields = _fields(message)
    return message["role"] + "|" + " ".join(fields.values())[:400]


def _line_diff(before: str, after: str, limit: int = 400) -> list[dict[str, str]]:
    lines = []
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        if line.startswith("? "):
            continue
        op = {"+": "+", "-": "-"}.get(line[:1], "=")
        lines.append({"op": op, "text": line[2:]})
        if len(lines) >= limit:
            lines.append({"op": "=", "text": "… (diff truncated)"})
            break
    return lines


def diff_runs(before: Any, after: Any) -> dict[str, Any]:
    """Aligned messages of two versions of one trajectory with per-field line diffs."""
    left, right = before.messages, after.messages
    matcher = difflib.SequenceMatcher(a=[_signature(m) for m in left], b=[_signature(m) for m in right], autojunk=False)
    blocks: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            blocks.append({"kind": "same", "before": [m["step"] for m in left[i1:i2]], "after": [m["step"] for m in right[j1:j2]]})
            continue
        # Pair up replaced messages one to one where roles agree; the rest are removals / additions.
        pairs = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for k in range(pairs):
            a, b = left[i1 + k], right[j1 + k]
            fa, fb = _fields(a), _fields(b)
            changed = {
                name: _line_diff(fa.get(name, ""), fb.get(name, ""))
                for name in dict.fromkeys([*fa, *fb])
                if fa.get(name, "") != fb.get(name, "")
            }
            blocks.append({"kind": "changed", "role": b["role"], "before": [a["step"]], "after": [b["step"]], "fields": changed})
        for message in left[i1 + pairs : i2]:
            blocks.append({"kind": "removed", "role": message["role"], "before": [message["step"]], "after": [], "fields": {name: _line_diff(text, "") for name, text in _fields(message).items() if text}})
        for message in right[j1 + pairs : j2]:
            blocks.append({"kind": "added", "role": message["role"], "before": [], "after": [message["step"]], "fields": {name: _line_diff("", text) for name, text in _fields(message).items() if text}})
    counts = {kind: sum(1 for block in blocks if block["kind"] == kind) for kind in ("changed", "removed", "added")}
    counts["same"] = sum(len(block["before"]) for block in blocks if block["kind"] == "same")
    return {"counts": counts, "blocks": blocks}


# -- corrections --------------------------------------------------------------------------


def correction_message(text: str) -> dict[str, Any]:
    """The corrected assistant turn: plain text, or a JSON object with content /
    tool_calls when the reviewer wrote the call the agent should have made."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and ("content" in value or "tool_calls" in value):
            return {"role": "assistant", **{key: value[key] for key in ("content", "tool_calls", "reasoning_content") if key in value}}
    return {"role": "assistant", "content": stripped}


def correction_samples(run: Any, row: dict[str, Any], review: dict[str, Any]) -> dict[str, Any] | None:
    """SFT sample and DPO pair from a review with a turning step and a correction."""
    step = review.get("turning_step")
    correction = (review.get("correction") or "").strip()
    if not step or not correction:
        return None
    target = next((m for m in run.messages if m["step"] == step), None)
    if target is None or target["role"] not in {"assistant", "tool"}:
        return None
    context = chat_sample(replace(run, messages=[m for m in run.messages if m["step"] < step]), row)["messages"]
    rejected = [m for m in chat_sample(replace(run, messages=[target]), row)["messages"] if m["role"] == "assistant"]
    chosen = correction_message(correction)
    base_id = f"{row.get('run_id') or run.run_id}@step{step}"
    meta = {
        "source": row.get("path"),
        "trajectory_id": row["id"],
        "collection": row.get("collection"),
        "reviewer": review.get("reviewer"),
        "labels": review.get("labels") or [],
        "turning_note": review.get("turning_note"),
    }
    return {
        "sft": {"id": base_id + "#sft", "messages": context + [chosen], "meta": meta},
        "dpo": {"id": base_id + "#dpo", "prompt": context, "chosen": [chosen], "rejected": rejected, "meta": meta},
    }
