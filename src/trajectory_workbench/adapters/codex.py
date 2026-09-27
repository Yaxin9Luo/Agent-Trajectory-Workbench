"""Codex CLI / Desktop rollouts: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`.

Rows are `{timestamp, type, payload}`. The transcript lives in `response_item` payloads
(message, reasoning, function_call(_output), custom_tool_call(_output), ...). `event_msg`
rows carry turn lifecycle and token totals. Older rollouts wrote the items without the
`{type, payload}` wrapper; those are read the same way.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

from trajectory_workbench.adapters.common import (
    TranscriptBuilder,
    content_images,
    content_text,
    finish_run,
    iter_jsonl,
    make_outcome,
    parse_arguments,
    parse_timestamp_ms,
    sniff_jsonl,
)
from trajectory_workbench.models import NormalizedRun


ITEM_TYPES = {
    "message",
    "reasoning",
    "function_call",
    "function_call_output",
    "custom_tool_call",
    "custom_tool_call_output",
    "local_shell_call",
    "web_search_call",
}
INJECTED_USER_PREFIXES = (
    "<environment_context>",
    "<user_instructions>",
    "# AGENTS.md instructions",
    "<permissions instructions>",
    "<turn_aborted>",
)
# User-role text made only of XML-ish blocks (`<app-context>…</app-context>`, AGENTS.md,
# `<environment_context>`) is context the harness injected, not something the person typed.
TAG_BLOCK = re.compile(r"<([A-Za-z][\w-]*)[^>]*>.*?</\1>", re.DOTALL)


def is_injected_context(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith(INJECTED_USER_PREFIXES[:1]) or stripped.startswith("<"):
        rest = TAG_BLOCK.sub("", stripped)
        rest = re.sub(r"(?m)^# AGENTS\.md instructions.*$", "", rest)
        return not rest.strip() or stripped.startswith(INJECTED_USER_PREFIXES)
    return stripped.startswith(INJECTED_USER_PREFIXES)




def looks_like_codex(rows: list[dict[str, Any]]) -> bool:
    for row in rows[:5]:
        if row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
            return True
        if row.get("type") in {"response_item", "event_msg", "turn_context"} and "payload" in row:
            return True
    return False


def _output_text(output: Any) -> str:
    if isinstance(output, str):
        parsed = parse_arguments(output)
        if isinstance(parsed, dict) and "output" in parsed:
            return str(parsed.get("output", ""))
        return output
    return content_text(output)


def _output_error(output: Any) -> bool | None:
    """The exit code Codex recorded, if any; None lets the builder infer from the text."""
    parsed = parse_arguments(output) if isinstance(output, str) else None
    if isinstance(parsed, dict):
        metadata = parsed.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("exit_code"), int):
            return metadata["exit_code"] != 0
    return None


def thread_links(session_meta: dict[str, Any]) -> dict[str, Any]:
    """Episode keys from `session_meta`: a subagent thread names its parent thread in
    `source.subagent.thread_spawn.parent_thread_id`."""
    thread = session_meta.get("id") or session_meta.get("session_id")
    if not thread:
        return {}
    links: dict[str, Any] = {"self_key": f"codex:{thread}", "group_key": f"codex:{thread}", "group_role": "main"}
    source = session_meta.get("source")
    spawn = source.get("subagent") if isinstance(source, dict) else None
    if isinstance(spawn, dict):
        detail = spawn.get("thread_spawn") if isinstance(spawn.get("thread_spawn"), dict) else {}
        parent = detail.get("parent_thread_id") or session_meta.get("forked_from_id")
        links["group_role"] = "subagent"
        if parent:
            links["parent_key"] = f"codex:{parent}"
            links["group_key"] = links["parent_key"]
        if detail.get("agent_role") or session_meta.get("agent_role"):
            links["subagent_type"] = detail.get("agent_role") or session_meta.get("agent_role")
        if detail.get("agent_path"):
            links["description"] = detail["agent_path"]
        if detail.get("depth") is not None:
            links["spawn_depth"] = detail["depth"]
    return links


class CodexAdapter:
    adapter_id = "codex"
    label = "Codex"

    def detect(self, path: Path) -> bool:
        return path.is_file() and path.suffix == ".jsonl" and looks_like_codex(sniff_jsonl(path, 8))

    def iter_runs(self, path: Path) -> Iterator[tuple[dict[str, Any] | None, NormalizedRun]]:
        yield None, self.load(path, None)

    def load(self, path: Path, locator: dict[str, Any] | None) -> NormalizedRun:
        builder = TranscriptBuilder()
        meta: dict[str, Any] = {"format": "codex", "harness": "Codex"}
        outcome = make_outcome()
        tokens: int | None = None
        context_window: int | None = None
        pending_thinking: list[str] = []
        current: dict[str, Any] | None = None

        def attach_thinking(message: dict[str, Any]) -> None:
            if pending_thinking:
                joined = "\n".join(pending_thinking)
                message["thinking"] = (message["thinking"] + "\n" if message["thinking"] else "") + joined
                pending_thinking.clear()

        for line_number, row in iter_jsonl(path):
            kind = row.get("type")
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else None
            stamp = parse_timestamp_ms(row.get("timestamp"))
            if payload is None and kind in ITEM_TYPES:
                payload, kind = row, "response_item"
            if payload is None:
                continue

            if kind == "session_meta":
                if "session_id" in meta:
                    continue  # later session_meta rows describe forked-from threads
                meta["session_id"] = payload.get("id") or payload.get("session_id")
                meta["cwd"] = payload.get("cwd")
                if payload.get("cli_version"):
                    meta["harness_version"] = payload.get("cli_version")
                if payload.get("originator"):
                    meta["originator"] = payload.get("originator")
                meta.update(thread_links(payload))
                continue
            if kind == "turn_context":
                if payload.get("model"):
                    meta["model"] = payload["model"]
                if payload.get("effort") or payload.get("reasoning_effort"):
                    meta["reasoning_effort"] = payload.get("effort") or payload.get("reasoning_effort")
                continue
            if kind == "event_msg":
                event = payload.get("type")
                if event == "thread_settings_applied":
                    settings = payload.get("thread_settings") or {}
                    meta["model"] = settings.get("model") or meta.get("model")
                    meta["reasoning_effort"] = settings.get("reasoning_effort") or meta.get("reasoning_effort")
                elif event == "token_count":
                    info = payload.get("info") or {}
                    total = (info.get("total_token_usage") or {}).get("total_tokens")
                    if isinstance(total, int):
                        tokens = total
                    if isinstance(info.get("model_context_window"), int):
                        context_window = info["model_context_window"]
                    last = info.get("last_token_usage") or {}
                    # The usage of the model call that produced the latest step.
                    target = next((m for m in reversed(builder.messages) if m["role"] in {"assistant", "tool"}), None)
                    if target is not None and isinstance(last.get("input_tokens"), int) and "context_tokens" not in target:
                        target["context_tokens"] = last["input_tokens"]
                        target["output_tokens"] = last.get("output_tokens")
                elif event == "turn_aborted":
                    builder.add_message(
                        "system",
                        text=f"[turn aborted] {payload.get('reason', '')}",
                        offset_ms=stamp,
                        line_number=line_number,
                    )
                    current = None
                elif event == "task_complete":
                    meta["terminal"] = "task_complete"
                elif event == "item_completed":
                    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
                    if item.get("type") == "SubAgentActivity" and item.get("kind") == "started" and item.get("agent_thread_id"):
                        # Link the spawn_agent call to the child thread it started.
                        spawn = builder.tool(str(item.get("id") or ""))
                        if spawn is not None:
                            spawn["spawned"] = f"codex:{item['agent_thread_id']}"
                continue
            if kind == "compacted":
                builder.add_message(
                    "system",
                    text="[compacted] " + str(payload.get("message") or "context replaced by summary"),
                    offset_ms=stamp,
                    line_number=line_number,
                    extra={"compaction": {"trigger": "compacted"}},
                )
                current = None
                continue
            if kind != "response_item":
                continue

            item = payload.get("type")
            if item == "message":
                role = payload.get("role")
                content = payload.get("content")
                text = content_text(content)
                images = content_images(content)
                if role == "assistant":
                    current = builder.add_message(
                        "assistant", text=text, offset_ms=stamp, line_number=line_number
                    )
                    attach_thinking(current)
                    current = None
                    continue
                mapped = "system" if role in {"developer", "system"} else "user"
                if mapped == "user" and is_injected_context(text):
                    mapped = "system"
                layer = "instruction" if mapped == "system" and (
                    "AGENTS.md" in text[:400] or "<user_instructions>" in text[:400]
                ) else None
                builder.add_message(
                    mapped,
                    text=text,
                    offset_ms=stamp,
                    line_number=line_number,
                    images=images,
                    extra={"layer": layer} if layer else None,
                )
                current = None
            elif item == "agent_message":
                # Messages from other agents are this thread's instructions (a subagent's
                # task arrives this way), so they read as user turns.
                builder.add_message(
                    "user",
                    text=f"[agent message {payload.get('author', '')} → {payload.get('recipient', '')}] "
                    + content_text(payload.get("content")),
                    offset_ms=stamp,
                    line_number=line_number,
                )
                current = None
            elif item == "reasoning":
                summary = content_text(payload.get("summary"))
                body = content_text(payload.get("content"))
                piece = body or summary
                if not piece and payload.get("encrypted_content"):
                    piece = "[encrypted reasoning]"
                if piece:
                    pending_thinking.append(piece)
            elif item in {"function_call", "custom_tool_call", "local_shell_call", "web_search_call"}:
                if current is None:
                    current = builder.add_message("assistant", offset_ms=stamp, line_number=line_number)
                attach_thinking(current)
                if item == "function_call":
                    name = str(payload.get("name", ""))
                    if payload.get("namespace"):
                        raw = f"{payload['namespace']}__{name}"
                    else:
                        raw = name
                    tool_input = parse_arguments(payload.get("arguments"))
                elif item == "custom_tool_call":
                    name = raw = str(payload.get("name", ""))
                    tool_input = {"input": payload.get("input", "")}
                elif item == "local_shell_call":
                    name = raw = "local_shell"
                    tool_input = payload.get("action", {})
                else:
                    name = raw = "web_search"
                    tool_input = payload.get("action", {})
                builder.add_tool_call(
                    current,
                    call_id=str(payload.get("call_id") or payload.get("id") or ""),
                    name=name,
                    raw_name=raw,
                    tool_input=tool_input,
                    offset_ms=stamp,
                    line_number=line_number,
                )
                if item == "web_search_call":
                    builder.set_result(
                        str(payload.get("call_id") or payload.get("id") or ""),
                        text=str(payload.get("status", "")),
                    )
            elif item in {"function_call_output", "custom_tool_call_output"}:
                output = payload.get("output")
                text = _output_text(output)
                builder.set_result(
                    str(payload.get("call_id", "")),
                    text=text,
                    is_error=_output_error(output),
                    images=content_images(output) if isinstance(output, list) else [],
                )
                current = None

        return finish_run(
            builder,
            adapter_id=self.adapter_id,
            source_path=path,
            run_id=path.stem,
            title=None,
            outcome=outcome,
            meta=meta,
            extra_metrics={"total_tokens": tokens, "context_window": context_window},
        )
