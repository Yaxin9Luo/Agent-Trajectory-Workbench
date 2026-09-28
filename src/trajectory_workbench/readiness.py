"""Training readiness of a trajectory as an SFT sample (code only, computed at import).

In chat-format SFT the model is trained on assistant turns (their text, reasoning and tool
calls); system, user and tool-result messages are context. The check answers: would this
sample train cleanly, and what would it teach?

Blocking issues make a sample unfit as-is: longer than the training sequence length, an
ending cut off mid-task, tool calls without results (or results without calls), tool
arguments that are not valid JSON, images the sample points to but that do not exist.
Warnings are things worth a look: trained turns that use components the harness added
(harness.py), empty assistant turns, a very small trained share.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from trajectory_workbench.adapters.common import IMAGE_TOKENS, estimate_tokens
from trajectory_workbench import harness


MAX_SEQ_LEN = int(os.environ.get("TRAJECTORY_WORKBENCH_MAX_SEQ_LEN", str(256 * 1024)))
TRAINED_ROLES = {"assistant", "tool"}
BLOCKING = {"too_long", "truncated_end", "unpaired_calls", "orphan_results", "bad_arguments", "missing_images"}


def _json_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def check(run: Any, trace: dict[str, Any] | None = None) -> dict[str, Any]:
    trained = context = images = 0
    issues: list[dict[str, Any]] = []
    empty_turns: list[int] = []
    bad_arguments: list[int] = []
    missing: list[int] = []
    unpaired: list[int] = []

    for message in run.messages:
        step = message["step"]
        text = message.get("text") or ""
        thinking = message.get("thinking") or ""
        message_images = message.get("images") or []
        images += len(message_images)
        size = estimate_tokens(text) + IMAGE_TOKENS * len(message_images)
        if message["role"] in TRAINED_ROLES:
            call_text = "\n".join(_json_text(tool.get("input")) for tool in message["tools"])
            trained += size + estimate_tokens(thinking) + estimate_tokens(call_text)
            if not text.strip() and not thinking.strip() and not message["tools"]:
                empty_turns.append(step)
        else:
            context += size
        for tool in message["tools"]:
            tool_input = tool.get("input")
            if isinstance(tool_input, str) and tool_input.lstrip().startswith(("{", "[")):
                bad_arguments.append(step)  # parse_arguments left it as a string
            result = tool.get("result")
            if result is None:
                unpaired.append(step)
                continue
            result_images = result.get("images") or []
            images += len(result_images)
            context += estimate_tokens(result.get("text") or "") + IMAGE_TOKENS * len(result_images)
        for image in message_images + [image for tool in message["tools"] for image in ((tool.get("result") or {}).get("images") or [])]:
            path = image.get("path")
            if path and not Path(path).is_file():
                missing.append(step)
                break

    total = trained + context
    last = next((m for m in reversed(run.messages) if m["role"] not in {"system", "result"}), None)
    if total > MAX_SEQ_LEN:
        issues.append({"key": "too_long", "detail": f"约 {total:,} tokens，超过 {MAX_SEQ_LEN:,}", "steps": []})
    if last is None or last["role"] not in TRAINED_ROLES:
        issues.append({"key": "truncated_end", "detail": "最后一条不是模型的回复", "steps": [last["step"]] if last else []})
    elif last["tools"]:
        issues.append({"key": "truncated_end", "detail": "以工具调用结束，没有最后的回复", "steps": [last["step"]]})
    # A final call without a result is the truncated ending already reported above.
    unpaired = [step for step in unpaired if not (last is not None and step == last["step"] and last["tools"])]
    if unpaired:
        issues.append({"key": "unpaired_calls", "detail": f"{len(unpaired)} 个工具调用没有返回", "steps": sorted(set(unpaired))[:50]})
    orphans = (run.metrics or {}).get("orphan_results") or 0
    if orphans:
        issues.append({"key": "orphan_results", "detail": f"{orphans} 条工具返回找不到对应调用", "steps": []})
    if bad_arguments:
        issues.append({"key": "bad_arguments", "detail": f"{len(bad_arguments)} 个工具参数不是合法 JSON", "steps": sorted(set(bad_arguments))[:50]})
    if missing:
        issues.append({"key": "missing_images", "detail": f"{len(missing)} 步引用的图片文件不存在", "steps": missing[:50]})
    # Trained turns that call, cite or name what the harness added (only trained turns
    # are scanned: the added instructions themselves sit in the system prompt, as context).
    trace = trace or harness.trace(run)
    used = [c for c in trace["components"] if any(c.get(how) for how in harness.USES)]
    residue = {"trained": {how: steps for how, steps in trace["steps"].items()}}
    if used:
        steps = sorted({step for group in trace["steps"].values() for step in group})
        issues.append({"key": "residue_in_trained", "detail": "训练 token 里用到 harness 组件：" + harness.describe(used), "steps": steps[:50]})
    if empty_turns:
        issues.append({"key": "empty_turns", "detail": f"{len(empty_turns)} 个空的模型回合", "steps": empty_turns[:50]})
    share = round(trained / total, 3) if total else 0.0
    if total and share < 0.05:
        issues.append({"key": "low_trained_share", "detail": f"训练 token 只占 {share:.1%}", "steps": []})
    for issue in issues:
        issue["severity"] = "block" if issue["key"] in BLOCKING else "warn"
    return {
        "tokens": total,
        "trained_tokens": trained,
        "trained_share": share,
        "images": images,
        "max_seq_len": MAX_SEQ_LEN,
        "ready": not any(issue["severity"] == "block" for issue in issues),
        "issues": issues,
        "residue": residue,
    }


ISSUE_LABELS = {
    "too_long": "超长",
    "truncated_end": "结尾被截断",
    "unpaired_calls": "调用没有返回",
    "orphan_results": "返回没有调用",
    "bad_arguments": "参数不是 JSON",
    "missing_images": "图片缺失",
    "residue_in_trained": "训练 token 用到 harness 组件",
    "empty_turns": "空回合",
    "low_trained_share": "训练 token 占比过低",
}
