"""Training samples from reviewers' corrections (rewrite review lives in rewrite_review).

Corrections: a reviewer marks the turning step and writes what the agent should have
done there. That yields an SFT sample (context up to the step + the correction) and a DPO
pair (same context; chosen = correction, rejected = what the agent actually did).
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from trajectory_workbench.exporter import chat_sample


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
