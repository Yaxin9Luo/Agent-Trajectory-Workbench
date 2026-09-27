"""Export a filtered set of trajectories as training data, with a data card.

`raw` copies each sample's original JSONL line byte for byte (chat / SFT exports and
TraceLab trials, where a trajectory is one line of a file). `chat` converts any
trajectory to an OpenAI-chat sample (system / user / assistant with tool_calls / tool),
which also covers Claude Code sessions, Codex rollouts, ATIF and MoH runs.

The data card records what went in: the filters, counts per collection / adapter /
outcome, training readiness, token statistics and what was skipped and why.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

LINE_ADAPTERS = {"openai-chat", "tracelab"}


def raw_line(row: dict[str, Any]) -> bytes | None:
    """The original JSONL line of a one-line-per-sample trajectory (without newline)."""
    locator = row.get("locator") or {}
    if row.get("adapter_id") not in LINE_ADAPTERS or not isinstance(locator, dict) or "offset" not in locator:
        return None
    path = Path(row["path"])
    if row["adapter_id"] == "tracelab":
        path = path / "dialog.jsonl"
    with path.open("rb") as handle:
        handle.seek(int(locator["offset"]))
        return handle.readline().rstrip(b"\r\n")


def chat_sample(run: Any, row: dict[str, Any]) -> dict[str, Any]:
    """An OpenAI-chat sample from a normalized trajectory."""
    messages: list[dict[str, Any]] = []
    for message in run.messages:
        role = message["role"]
        if role in {"assistant", "tool"}:
            entry: dict[str, Any] = {"role": "assistant", "content": message.get("text") or ""}
            if message.get("thinking"):
                entry["reasoning_content"] = message["thinking"]
            if message["tools"]:
                entry["tool_calls"] = [
                    {
                        "id": tool["id"],
                        "type": "function",
                        "function": {
                            "name": tool["raw_name"] or tool["name"],
                            "arguments": tool["input"] if isinstance(tool["input"], str) else json.dumps(tool["input"], ensure_ascii=False),
                        },
                    }
                    for tool in message["tools"]
                ]
            messages.append(entry)
            for tool in message["tools"]:
                if tool.get("result") is not None:
                    result = {"role": "tool", "tool_call_id": tool["id"], "content": tool["result"].get("text") or ""}
                    if tool["result"].get("is_error"):
                        result["is_error"] = True
                    messages.append(result)
        elif role in {"user", "system"}:
            messages.append({"role": role, "content": message.get("text") or ""})
        # `result` rows are harness bookkeeping, not conversation.
    outcome = run.outcome or {}
    return {
        "id": row.get("run_id") or run.run_id,
        "messages": messages,
        "tools": [{"type": "function", "function": {"name": item["name"]}} for item in run.tool_catalog if item.get("available_in_session")],
        "meta": {
            "source": row.get("path"),
            "adapter": row.get("adapter_id"),
            "collection": row.get("collection"),
            "model": row.get("model"),
            "harness": row.get("harness"),
            "task_id": row.get("task_id"),
            "outcome": {key: outcome.get(key) for key in ("status", "score", "reason")},
        },
    }


def export(
    rows: Iterable[dict[str, Any]],
    target: Path,
    *,
    mode: str,
    load_run: Callable[[str], Any],
    filters: dict[str, Any],
    progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    if mode not in {"raw", "chat"}:
        raise ValueError("mode must be raw or chat")
    target.mkdir(parents=True, exist_ok=True)
    data_path = target / "dialog.jsonl"
    if data_path.exists():
        raise ValueError(f"{data_path} already exists")
    rows = list(rows)
    written: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    digest = hashlib.sha256()
    with data_path.open("wb") as handle:
        for index, row in enumerate(rows, start=1):
            try:
                if mode == "raw":
                    line = raw_line(row)
                    if line is None:
                        skipped["no raw line (not a one-sample-per-line source; use chat mode)"] += 1
                        continue
                else:
                    line = json.dumps(chat_sample(load_run(row["id"]), row), ensure_ascii=False).encode("utf-8")
            except (OSError, KeyError, ValueError) as error:
                skipped[f"unreadable: {type(error).__name__}"] += 1
                continue
            handle.write(line + b"\n")
            digest.update(line + b"\n")
            written.append(row)
            if progress and index % 50 == 0:
                progress(done=index, total=len(rows))
    card = data_card(written, skipped, filters, mode, digest.hexdigest())
    (target / "data_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "data_card.md").write_text(card_markdown(card), encoding="utf-8")
    return {"path": str(target), "written": len(written), "skipped": sum(skipped.values()), "card": card}


def _quantiles(values: list[int]) -> dict[str, int | None]:
    ordered = sorted(values)
    pick = lambda q: ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))] if ordered else None  # noqa: E731
    return {"p50": pick(0.5), "p90": pick(0.9), "max": ordered[-1] if ordered else None, "total": sum(ordered)}


def data_card(rows: list[dict[str, Any]], skipped: Counter, filters: dict[str, Any], mode: str, sha256: str) -> dict[str, Any]:
    readiness = [row.get("readiness") or {} for row in rows]
    issues: Counter[str] = Counter()
    for item in readiness:
        for issue in item.get("issues", []):
            issues[issue["key"]] += 1
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "filters": {key: value for key, value in filters.items() if value not in (None, "", [])},
        "samples": len(rows),
        "sha256": sha256,
        "episodes": len({(row.get("collection"), row.get("group_key") or row["id"]) for row in rows}),
        "by_collection": dict(Counter(row.get("collection") for row in rows)),
        "by_adapter": dict(Counter(row.get("adapter_id") for row in rows)),
        "by_role": dict(Counter(row.get("group_role") or "main" for row in rows)),
        "models": dict(Counter(row.get("model") or "?" for row in rows)),
        "harnesses": dict(Counter(row.get("harness") or "?" for row in rows)),
        "outcomes": dict(Counter(row.get("outcome_status") or "unknown" for row in rows)),
        "readiness": {
            "ready": sum(1 for item in readiness if item.get("ready")),
            "issues": dict(issues),
            "tokens": _quantiles([item.get("tokens") or 0 for item in readiness]),
            "trained_tokens": sum(item.get("trained_tokens") or 0 for item in readiness),
        },
        "skipped": dict(skipped),
    }


def card_markdown(card: dict[str, Any]) -> str:
    def table(title: str, counts: dict[str, int]) -> list[str]:
        if not counts:
            return []
        lines = [f"### {title}", "", "| | count |", "| --- | ---: |"]
        lines += [f"| {key} | {value} |" for key, value in sorted(counts.items(), key=lambda pair: -pair[1])]
        return lines + [""]

    tokens = card["readiness"]["tokens"]
    lines = [
        "# Data card",
        "",
        f"- Generated: {card['generated_at']}",
        f"- Mode: `{card['mode']}`",
        f"- Samples: **{card['samples']}** ({card['episodes']} episodes)",
        f"- SHA-256 of dialog.jsonl: `{card['sha256']}`",
        f"- Ready to train as-is: {card['readiness']['ready']} / {card['samples']}",
        f"- Estimated tokens: p50 {tokens['p50']}, p90 {tokens['p90']}, max {tokens['max']}, total {tokens['total']} (trained {card['readiness']['trained_tokens']})",
        "",
        "## Filters",
        "",
        "```json",
        json.dumps(card["filters"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Composition",
        "",
    ]
    for title, key in (("Collections", "by_collection"), ("Adapters", "by_adapter"), ("Roles", "by_role"), ("Models", "models"), ("Harnesses", "harnesses"), ("Grader outcomes", "outcomes")):
        lines += table(title, card[key])
    lines += table("Readiness issues", card["readiness"]["issues"])
    lines += table("Skipped", card["skipped"])
    return "\n".join(lines).rstrip() + "\n"
