"""Claude Code transcripts: interactive session files and `--output-format stream-json`.

Session files live in `~/.claude/projects/<project>/<session>.jsonl`. Each API response is
split into one row per content block that shares `message.id`; rows are merged back into
one assistant step. stream-json output (also used inside MoH records) has the same
`user` / `assistant` rows plus `system:init` and a terminal `result` row.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from trajectory_workbench.adapters.common import (
    HARNESS_TEXT_PREFIXES,
    TranscriptBuilder,
    content_images,
    content_text,
    finish_run,
    iter_jsonl,
    make_outcome,
    parse_timestamp_ms,
    sniff_jsonl,
)
from trajectory_workbench.models import NormalizedRun


IGNORED_TYPES = {
    "atis-latch",
    "last-prompt",
    "queue-operation",
    "cost-state",
    "file-history-snapshot",
    "custom-title",
}


# User-role rows that the harness writes, not the person: slash commands, their output,
# background-task notifications and interrupt markers.
HARNESS_EVENT_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<local-command-caveat>",
    "<task-notification>",
    "<bash-input>",
    "<bash-stdout>",
    "[Request interrupted",
)


def attachment_layer(kind: str) -> str | None:
    """Harness layer an attachment row belongs to, or None for bookkeeping rows."""
    if kind.startswith("hook"):
        return "hook"
    if kind in {"instructions", "nested_memory", "claude_md"}:
        return "instruction"
    return None


def looks_like_claude_code(rows: list[dict[str, Any]]) -> bool:
    for row in rows:
        if "payload" in row or "messages" in row:
            return False
        kind = row.get("type")
        if kind == "system" and row.get("subtype") == "init" and "session_id" in row:
            return True
        if kind in {"user", "assistant"} and isinstance(row.get("message"), dict):
            if any(key in row for key in ("sessionId", "uuid", "session_id", "parent_tool_use_id")):
                return True
    return False


def session_links(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    """Episode keys for a session file. Subagent transcripts live in
    `<session-id>/subagents/agent-<id>.jsonl` next to an `agent-<id>.meta.json` that names
    the parent's Agent / Task call (`toolUseId`)."""
    session = meta.get("session_id")
    if not session:
        return {}
    root = f"claude:{session}"
    if not meta.get("agent_id"):
        return {"self_key": root, "group_key": root, "group_role": "main"}
    links: dict[str, Any] = {
        "self_key": f"{root}:{meta['agent_id']}",
        "parent_key": root,
        "group_key": root,
        "group_role": "subagent",
    }
    sidecar = path.with_name(path.stem + ".meta.json")
    if sidecar.is_file():
        try:
            info = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        if isinstance(info, dict):
            if info.get("toolUseId"):
                links["parent_call_id"] = str(info["toolUseId"])
            if info.get("agentType"):
                links["subagent_type"] = info["agentType"]
            if info.get("description"):
                links["description"] = info["description"]
    return links


class ClaudeCodeAdapter:
    adapter_id = "claude-code"
    label = "Claude Code"

    def detect(self, path: Path) -> bool:
        return path.is_file() and path.suffix == ".jsonl" and looks_like_claude_code(
            sniff_jsonl(path)
        )

    def iter_runs(self, path: Path) -> Iterator[tuple[dict[str, Any] | None, NormalizedRun]]:
        yield None, self.load(path, None)

    def load(self, path: Path, locator: dict[str, Any] | None) -> NormalizedRun:
        return self.from_rows(iter_jsonl(path), source_path=path, run_id=path.stem)

    def from_rows(
        self,
        rows: Iterator[tuple[int, dict[str, Any]]],
        *,
        source_path: Path,
        run_id: str,
        adapter_id: str | None = None,
    ) -> NormalizedRun:
        builder = TranscriptBuilder()
        available: list[str] = []
        meta: dict[str, Any] = {"format": "claude-code", "harness": "Claude Code"}
        outcome = make_outcome()
        cost: float | None = None
        tokens = 0
        seen_usage: set[str] = set()
        thinking_token_events = 0
        last_assistant: dict[str, Any] | None = None
        last_api_id: str | None = None

        for line_number, row in rows:
            kind = row.get("type")
            if kind in IGNORED_TYPES:
                if kind == "cost-state" and isinstance(row.get("totalCostUSD"), (int, float)):
                    cost = float(row["totalCostUSD"])
                continue
            stamp = parse_timestamp_ms(row.get("timestamp"))
            if kind == "attachment":
                attachment = row.get("attachment") if isinstance(row.get("attachment"), dict) else {}
                layer = attachment_layer(str(attachment.get("type", "")))
                if layer is not None:
                    body = attachment.get("content")
                    if isinstance(body, list):
                        body = "\n".join(str(item) for item in body)
                    if not isinstance(body, str) or not body:
                        body = json.dumps(
                            {k: v for k, v in attachment.items() if k != "type"}, ensure_ascii=False
                        )[:4000]
                    builder.add_message(
                        "system",
                        text=f"[{attachment.get('type')}] {body}",
                        offset_ms=stamp,
                        line_number=line_number,
                        extra={"layer": layer},
                    )
                    last_assistant = None
                continue
            meta.setdefault("session_id", row.get("sessionId") or row.get("session_id"))
            if row.get("isSidechain") and row.get("agentId"):
                meta.setdefault("agent_id", row["agentId"])
            if row.get("version") and "harness_version" not in meta:
                meta["harness_version"] = row.get("version")
            if row.get("cwd") and "cwd" not in meta:
                meta["cwd"] = row["cwd"]

            if kind == "system":
                subtype = row.get("subtype")
                if subtype == "thinking_tokens":
                    thinking_token_events += 1
                    continue
                if subtype == "init":
                    meta["model"] = row.get("model") or meta.get("model")
                    available = [name for name in row.get("tools", []) if isinstance(name, str)]
                    continue
                if subtype == "compact_boundary":
                    info = row.get("compactMetadata") if isinstance(row.get("compactMetadata"), dict) else {}
                    compaction = {
                        "trigger": info.get("trigger"),
                        "pre_tokens": info.get("preTokens"),
                        "post_tokens": info.get("postTokens"),
                    }
                    before, after = compaction["pre_tokens"], compaction["post_tokens"]
                    detail = f" {before:,} → {after:,} tokens" if isinstance(before, int) and isinstance(after, int) else ""
                    builder.add_message(
                        "system",
                        text=f"[compact_boundary] 上下文压缩（{compaction['trigger'] or '?'}）{detail}",
                        offset_ms=stamp,
                        line_number=line_number,
                        extra={"compaction": compaction},
                    )
                    last_assistant = None
                    continue
                text = row.get("content") if isinstance(row.get("content"), str) else ""
                summary = f"[{subtype or 'system'}] {text}".strip()
                if not text:
                    summary += " " + json.dumps(
                        {k: v for k, v in row.items() if k not in {"uuid", "parentUuid", "sessionId"}},
                        ensure_ascii=False,
                    )[:2000]
                builder.add_message(
                    "system",
                    text=summary,
                    offset_ms=stamp,
                    line_number=line_number,
                    extra={"layer": "hook"} if "hook" in str(subtype) else None,
                )
                last_assistant = None
                continue

            if kind == "summary":
                builder.add_message(
                    "system",
                    text="[summary] " + str(row.get("summary", "")),
                    offset_ms=stamp,
                    line_number=line_number,
                )
                continue

            if kind == "result":
                cost = row.get("total_cost_usd", cost)
                usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
                if usage:
                    tokens = sum(
                        int(usage.get(key) or 0)
                        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                    )
                text = row.get("result") if isinstance(row.get("result"), str) else json.dumps(
                    row.get("result") or row.get("error") or "", ensure_ascii=False
                )
                builder.add_message(
                    "result",
                    text=text,
                    offset_ms=stamp,
                    line_number=line_number,
                    extra={"native_result": row},
                )
                is_error = bool(row.get("is_error")) or str(row.get("subtype", "")).startswith("error")
                outcome = make_outcome(
                    "error" if is_error else "unknown",
                    reason=None if not is_error else str(row.get("subtype") or "error"),
                    source="claude-code result",
                    detail={
                        "subtype": row.get("subtype"),
                        "num_turns": row.get("num_turns"),
                        "duration_ms": row.get("duration_ms"),
                    },
                )
                meta["terminal"] = row.get("subtype")
                last_assistant = None
                continue

            message = row.get("message") if isinstance(row.get("message"), dict) else None
            if message is None or kind not in {"user", "assistant"}:
                continue
            content = message.get("content", "")
            blocks = content if isinstance(content, list) else []

            if kind == "user":
                last_assistant = None
                results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
                for block in results:
                    inner = block.get("content", "")
                    builder.set_result(
                        str(block.get("tool_use_id", "")),
                        text=content_text(inner),
                        is_error=bool(block.get("is_error", False)),
                        images=content_images(inner),
                    )
                rest = [b for b in blocks if not (isinstance(b, dict) and b.get("type") == "tool_result")]
                text = content if isinstance(content, str) else content_text(rest)
                images = content_images(rest)
                if results and not text.strip() and not images:
                    continue
                if not text.strip() and not images:
                    continue
                role = (
                    "system"
                    if row.get("isMeta")
                    or row.get("isCompactSummary")
                    or text.lstrip().startswith(HARNESS_EVENT_PREFIXES + HARNESS_TEXT_PREFIXES)
                    else "user"
                )
                builder.add_message(
                    role,
                    text=text,
                    offset_ms=stamp,
                    line_number=line_number,
                    images=images,
                    extra={"sidechain": True} if row.get("isSidechain") else None,
                )
                continue

            meta["model"] = message.get("model") or meta.get("model")
            api_id = message.get("id")
            usage = message.get("usage")
            if isinstance(usage, dict) and api_id not in seen_usage:
                seen_usage.add(api_id or f"line-{line_number}")
                tokens += sum(
                    int(usage.get(key) or 0)
                    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                )
            if last_assistant is None or not api_id or api_id != last_api_id:
                extra: dict[str, Any] = {"sidechain": True} if row.get("isSidechain") else {}
                if isinstance(usage, dict):
                    # The prompt this call saw: fresh input plus cache reads and writes.
                    extra["context_tokens"] = sum(
                        int(usage.get(key) or 0)
                        for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                    )
                    extra["output_tokens"] = int(usage.get("output_tokens") or 0)
                last_assistant = builder.add_message(
                    "assistant",
                    offset_ms=stamp,
                    line_number=line_number,
                    extra=extra or None,
                )
                last_api_id = api_id
            target = last_assistant
            for block in blocks if blocks else [{"type": "text", "text": content}]:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    target["text"] = (target["text"] + "\n" if target["text"] else "") + str(block["text"])
                    if target["role"] == "tool":
                        target["role"] = "assistant"
                elif block.get("type") in {"thinking", "redacted_thinking"}:
                    piece = str(block.get("thinking") or ("[redacted thinking]" if block.get("type") == "redacted_thinking" else ""))
                    target["thinking"] = (target["thinking"] + "\n" if target["thinking"] else "") + piece
                elif block.get("type") in {"tool_use", "server_tool_use"}:
                    raw_name = str(block.get("name", ""))
                    builder.add_tool_call(
                        target,
                        call_id=str(block.get("id", "")),
                        name=raw_name,
                        tool_input=block.get("input", {}),
                        offset_ms=stamp,
                        line_number=line_number,
                    )

        meta.update(session_links(source_path, meta))
        # Drop assistant shells that only carried usage rows.
        builder.messages[:] = [
            m for m in builder.messages
            if m["role"] not in {"assistant", "tool"} or m["text"] or m["thinking"] or m["tools"]
        ]
        for index, message in enumerate(builder.messages, start=1):
            message["step"] = index
            message["id"] = f"m-{index}"
            for tool in message["tools"]:
                tool["step"] = index
        return finish_run(
            builder,
            adapter_id=adapter_id or self.adapter_id,
            source_path=source_path,
            run_id=run_id,
            title=None,
            outcome=outcome,
            meta=meta,
            available_tools=available,
            extra_metrics={
                "total_cost_usd": cost,
                "total_tokens": tokens or None,
                "thinking_token_events": thinking_token_events,
            },
        )
