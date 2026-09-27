"""Building blocks shared by the transcript adapters.

Every adapter turns its native records into the same message / tool shape:

    message = {id, step, line_number, role, offset_ms, text, thinking, tools, images}
    tool    = {id, step, name, raw_name, input, result, offset_ms, line_number}
    result  = {text, is_error, images}

`role` is one of system, user, assistant, tool (an assistant turn that only calls tools)
or result (a harness terminal record). `offset_ms` is None when the source has no clock.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from trajectory_workbench.models import NormalizedRun


IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
# Claude Code renders hook output into a reminder: "PostToolUse:Agent hook additional context: …".
HOOK_REMINDER = re.compile(r"<system-reminder>\s*\w+(?::[\w.-]+)? hook (?:additional context|blocking error|feedback|success)\b")
# Claude Code compaction: the summary that opens the next context window, and the
# request that asks the model to write it. Both are harness events, not the task.
COMPACTION_PREFIX = "This session is being continued from a previous conversation"
COMPACTION_REQUEST_PREFIX = "CRITICAL: Respond with TEXT ONLY"
HARNESS_TEXT_PREFIXES = (COMPACTION_PREFIX, COMPACTION_REQUEST_PREFIX)


def parse_timestamp_ms(value: Any) -> int | None:
    if isinstance(value, (int, float)) and value > 0:
        # Epoch seconds or milliseconds.
        return int(value * 1000) if value < 1e11 else int(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def image_from_data_url(url: str) -> dict[str, str] | None:
    match = re.match(r"data:(image/[a-z+.-]+);base64,(.+)", url, re.DOTALL)
    if not match or match.group(1) not in IMAGE_MEDIA_TYPES:
        return None
    data = match.group(2)
    try:
        base64.b64decode(data[:64] + "=" * (-len(data[:64]) % 4), validate=True)
    except (ValueError, binascii.Error):
        return None
    return {"media_type": match.group(1), "data": data}


def image_reference(url: str) -> dict[str, str] | None:
    """Describe an image given as a data URL, absolute file path or http(s) URL."""
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("data:"):
        return image_from_data_url(url)
    if url.startswith(("http://", "https://")):
        return {"url": url}
    if url.startswith("file://"):
        url = url[len("file://") :]
    if url.startswith("/"):
        return {"path": url}
    return None


def anthropic_image(block: dict[str, Any]) -> dict[str, str] | None:
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    if source.get("type") == "base64":
        media_type, data = source.get("media_type"), source.get("data")
        if media_type in IMAGE_MEDIA_TYPES and isinstance(data, str) and data:
            return {"media_type": media_type, "data": data}
    if source.get("type") == "url":
        return image_reference(str(source.get("url", "")))
    if isinstance(source.get("path"), str):
        return image_reference(source["path"])
    return None


CONTENT_LIST_PREFIX = re.compile(r'\[\s*\{\s*"type"\s*:')
CONTENT_PART_TYPES = {
    "text", "image", "image_url", "input_text", "output_text", "input_image",
    "summary_text", "document", "tool_reference",
}


def decode_content(content: Any) -> Any:
    """Some exporters store a content-part list (text + base64 images) as a JSON string;
    return the list in that case, otherwise the content unchanged."""
    if isinstance(content, str) and CONTENT_LIST_PREFIX.match(content.lstrip()[:40]):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return content
        # Only content-part lists: a tool that printed JSON like `[{"type": "PushEvent"}]`
        # must keep its raw output.
        if isinstance(parsed, list) and parsed and all(
            isinstance(item, dict) and item.get("type") in CONTENT_PART_TYPES for item in parsed
        ):
            return parsed
    return content


def content_text(content: Any) -> str:
    """Join the text parts of Anthropic, OpenAI or ATIF style content."""
    if content is None:
        return ""
    if isinstance(content, str):
        decoded = decode_content(content)
        return content if decoded is content else content_text(decoded)
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False)
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") in {
            "text",
            "input_text",
            "output_text",
            "summary_text",
        }:
            parts.append(str(item.get("text", "")))
    return "\n".join(part for part in parts if part)


def content_images(content: Any) -> list[dict[str, str]]:
    content = decode_content(content)
    if not isinstance(content, list):
        return []
    images: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in content:
        if not isinstance(item, dict):
            continue
        image = None
        kind = item.get("type")
        if kind == "image":
            image = anthropic_image(item)
        elif kind == "image_url":
            value = item.get("image_url")
            url = value.get("url") if isinstance(value, dict) else value
            image = image_reference(str(url or ""))
        elif kind == "input_image":
            image = image_reference(str(item.get("image_url", "")))
        if image is None:
            continue
        key = json.dumps(image, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        images.append(image)
    return images


def parse_arguments(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def strip_reminders(text: str) -> str:
    return SYSTEM_REMINDER.sub("", text).strip()


def task_key(task_id: str | None, instruction: str | None) -> str | None:
    if task_id:
        return "id:" + task_id
    if not instruction:
        return None
    # Hash only the opening of the instruction: reruns of the same task often append
    # different attachments, reminders or pasted context after it.
    normalized = re.sub(r"\s+", " ", instruction).strip().casefold()[:1500]
    return "h:" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def task_from_messages(
    messages: list[dict[str, Any]], task_id: str | None = None
) -> dict[str, Any]:
    instruction = None
    for message in messages:
        if message["role"] != "user":
            continue
        text = strip_reminders(message.get("text") or "")
        if text and not text.startswith(HARNESS_TEXT_PREFIXES):
            instruction = text
            break
    return {"id": task_id, "key": task_key(task_id, instruction), "instruction": instruction}


# JSON-wrapped harness tasks keep the user's request and the harness rules in fields.
TASK_FIELDS = ("context", "prompt", "request", "task", "query", "user_request", "instruction")
HARNESS_FIELDS = ("instructions", "system", "harness_instructions", "rules")


def split_task(instruction: str | None) -> tuple[str, str]:
    """(user task, harness text) from an instruction that may wrap the task in JSON."""
    text = (instruction or "").strip()
    start = text.find("{")
    if start >= 0:
        try:
            document, end = json.JSONDecoder().raw_decode(text, start)
        except json.JSONDecodeError:
            document, end = None, start
        # Only a wrapper when the JSON *is* the message (a short framing sentence around
        # it at most) or the framing announces JSON that carries harness rules; a task
        # that merely shows an example JSON body is left alone.
        framing_text = text[:start].strip()
        wrapped = isinstance(document, dict) and len(framing_text) <= 600 and (
            (end - start) >= 0.6 * len(text)
            or ("json" in framing_text.lower() and any(isinstance(document.get(k), str) for k in HARNESS_FIELDS))
        )
        if wrapped:
            task = "\n".join(str(document[k]) for k in TASK_FIELDS if isinstance(document.get(k), str))
            harness = "\n".join(str(document[k]) for k in HARNESS_FIELDS if isinstance(document.get(k), str))
            # Text around the JSON is the harness's framing ("Treat the following JSON …").
            framing = [text[:start].strip(), text[end:].strip()]
            if task:
                return task, "\n".join(part for part in (*framing, harness) if part)
    return text, ""


def title_from(instruction: str | None) -> str | None:
    """First meaningful line of an instruction (of the user's request inside a
    JSON-wrapped harness task), for list titles."""
    for line in split_task(instruction)[0].splitlines():
        line = line.strip().lstrip("#").strip()
        if len(line) >= 4:
            return line[:120]
    return None


def make_outcome(
    status: str = "unknown",
    *,
    score: float | None = None,
    reason: str | None = None,
    source: str | None = None,
    infra_failure: bool | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "score": score,
        "reason": reason,
        "source": source,
        "infra_failure": infra_failure,
        "detail": detail or {},
    }


INFRA_EXCEPTION = re.compile(
    r"environment|docker|sandbox|container|connection|network|gateway|provision|"
    r"ratelimit|rate_limit|overloaded|apierror|api_error|stream",
    re.IGNORECASE,
)


def infra_from_exception(name: str | None) -> bool | None:
    if not name:
        return None
    return bool(INFRA_EXCEPTION.search(name))


# Why a rollout ended without a trusted score. `kind` says whose fault it is:
#   infra   environment, sandbox, API or auth broke; the rollout says nothing about the model
#   grader  the verifier crashed or produced no reward
#   budget  the agent ran out of time, context or output tokens
#   agent   the agent process failed, or finished with an invalid / failing artifact
# Harbor exception class names and MoH runtime classifications share this table.
FAILURE_KINDS: dict[str, tuple[str, str]] = {
    # MoH runtime classifications (also embedded in TraceLab exception messages)
    "evaluation_failed": ("fail", "agent"),
    "artifact_invalid": ("fail", "agent"),
    "failed": ("fail", "agent"),
    "reward_zero": ("fail", "agent"),
    "agent_execution_failed": ("error", "agent"),
    "agent_timeout": ("fail", "budget"),
    "timeout": ("fail", "budget"),
    "output_limit": ("fail", "budget"),
    "direct_execution_failed": ("error", "agent"),
    "cancelled": ("error", "infra"),
    "config_invalid": ("error", "infra"),
    "promotion_failed": ("error", "infra"),
    "process_failed": ("error", "infra"),
    "orchestration_failed": ("error", "infra"),
    "worker_launch_failed": ("error", "infra"),
    "runtime_setup_failed": ("error", "infra"),
    "runtime_cleanup_failed": ("error", "infra"),
    "runtime_record_failed": ("error", "infra"),
    "workspace_commit_uncertain": ("error", "infra"),
    # MoH campaign run classifications
    "candidate_failure": ("fail", "agent"),
    "candidate_contract_violation": ("fail", "agent"),
    "evaluator_failure": ("error", "grader"),
    "infrastructure_failure": ("error", "infra"),
    # Harbor
    "NonZeroAgentExitCodeError": ("error", "agent"),
    "AgentTimeoutError": ("fail", "budget"),
    "ContextLengthExceededError": ("fail", "budget"),
    "ContextWindowExceededError": ("fail", "budget"),
    "OutputLengthExceededError": ("fail", "budget"),
    "OutputTokenExceededError": ("fail", "budget"),
    "VerifierTimeoutError": ("error", "grader"),
    "VerifierOutputParseError": ("error", "grader"),
    "RewardFileNotFoundError": ("error", "grader"),
    "RewardFileEmptyError": ("error", "grader"),
    "DownloadVerifierDirError": ("error", "grader"),
    "AddTestsDirError": ("error", "grader"),
    "AgentSetupTimeoutError": ("error", "infra"),
    "EnvironmentStartTimeoutError": ("error", "infra"),
    "SandboxBuildFailedError": ("error", "infra"),
    "SandboxLikelyOutOfMemoryError": ("error", "infra"),
    "MemoryLimitExceededError": ("error", "infra"),
    "GKEExecStreamClosedError": ("error", "infra"),
    "HealthcheckError": ("error", "infra"),
    "RuntimeRequestError": ("error", "infra"),
    "NetworkConnectionError": ("error", "infra"),
    "ModelNotFoundError": ("error", "infra"),
    "AgentAuthenticationError": ("error", "infra"),
    "AuthenticationError": ("error", "infra"),
    "AgentSafetyRefusalError": ("error", "agent"),
}
FAILURE_KIND_INFRA = {"infra": True, "grader": True, "budget": False, "agent": False}
MOH_CLASSIFICATION = re.compile(r"classification=['\"]?([a-z_]+)")


def classify_failure(*names: str | None) -> dict[str, Any] | None:
    """Map the first recognizable failure name to {name, status, kind, infra_failure}.

    Names are tried in order (most specific first). API errors (`Api*Error`) are infra;
    unknown names fall back to the infra keyword heuristic with status "error".
    """
    for name in names:
        if not name:
            continue
        embedded = MOH_CLASSIFICATION.search(name)
        key = embedded.group(1) if embedded else name.strip()
        if key in FAILURE_KINDS:
            status, kind = FAILURE_KINDS[key]
        elif key.startswith("Api") and key.endswith("Error"):
            status, kind = "error", "infra"
        else:
            continue
        # A crashed agent process may be the model's doing or a broken API underneath.
        infra = None if (kind == "agent" and status == "error") else FAILURE_KIND_INFRA[kind]
        return {"name": key, "status": status, "kind": kind, "infra_failure": infra}
    # An unknown MoH classification is still the best reason to group by.
    for name in names:
        embedded = MOH_CLASSIFICATION.search(name or "")
        if embedded:
            return {"name": embedded.group(1), "status": "error", "kind": None, "infra_failure": None}
    # Unrecognized: report the shortest name (an exception type, not a long message) so
    # failures still group by reason.
    given = [name.strip() for name in names if name and name.strip()]
    if given:
        name = min(given, key=len)[:120]
        infra = any(infra_from_exception(item) for item in given)
        return {"name": name, "status": "error", "kind": "infra" if infra else None, "infra_failure": True if infra else None}
    return None


TOOL_ERROR_TEXT = re.compile(
    r"\A\s*(?:Exit code:? ?(?P<exit>-?\d+)\b|<tool_use_error>|Script failed\b|"
    r"failed to parse function arguments|[Ee]rror:)"
)
PROCESS_EXIT = re.compile(r"Process exited with code (-?\d+)")
# An MCP result object `{"content": [...], "isError": true}`, possibly after a short
# wrapper line such as Codex's "Script completed … Output:".
MCP_IS_ERROR = re.compile(r'\{\s*\\?"content\\?"\s*:\s*\[.*?\\?"isError\\?"\s*:\s*true', re.DOTALL)


def infer_tool_error(text: str) -> bool:
    """Guess whether a tool result without an explicit error flag reports a failure.

    Recognizes the harness conventions seen in exports: Claude Code's leading
    `Exit code N` and `<tool_use_error>`, Codex's `Process exited with code N` and
    `Script failed`, and an MCP result JSON with `"isError": true`.
    """
    if not text:
        return False
    head = text[:4000]
    match = TOOL_ERROR_TEXT.match(head)
    if match:
        return match.group("exit") is None or int(match.group("exit")) != 0
    process = PROCESS_EXIT.search(head[:600])
    if process:
        return int(process.group(1)) != 0
    mcp = MCP_IS_ERROR.search(head)
    return bool(mcp) and mcp.start() < 300


def score_status(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 1:
        return "pass"
    if score <= 0:
        return "fail"
    return "partial"


def build_tool_catalog(
    tools: list[dict[str, Any]], available: list[str]
) -> list[dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}

    def entry(name: str) -> dict[str, Any]:
        return catalog.setdefault(
            name,
            {
                "name": name,
                "call_count": 0,
                "observed": False,
                "available_in_session": False,
                "declared": False,
                "raw_names": [name],
                "sources": [],
            },
        )

    for tool in tools:
        item = entry(tool["name"])
        item["call_count"] += 1
        item["observed"] = True
        if "trajectory" not in item["sources"]:
            item["sources"].append("trajectory")
    for name in available:
        item = entry(name)
        item["available_in_session"] = True
        if "declared tools" not in item["sources"]:
            item["sources"].append("declared tools")
    return sorted(catalog.values(), key=lambda item: item["name"].casefold())


class TranscriptBuilder:
    """Accumulates messages and pairs tool calls with results that arrive later."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.tools: list[dict[str, Any]] = []
        self._by_call_id: dict[str, dict[str, Any]] = {}
        self._pending_results: dict[str, dict[str, Any]] = {}
        self.unmatched_results = 0  # results with no call id at all

    def add_message(
        self,
        role: str,
        *,
        text: str = "",
        thinking: str = "",
        offset_ms: int | None = None,
        line_number: int | None = None,
        images: list[dict[str, str]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if role == "user" and text and not images and not strip_reminders(text):
            # A user turn holding only <system-reminder> blocks is harness-injected context.
            role = "system"
            if HOOK_REMINDER.search(text) and not (extra or {}).get("layer"):
                extra = {**(extra or {}), "layer": "hook"}
        index = len(self.messages) + 1
        message = {
            "id": f"m-{index}",
            "step": index,
            "line_number": line_number if line_number is not None else index,
            "role": role,
            "offset_ms": offset_ms,
            "text": text or "",
            "thinking": thinking or "",
            "tools": [],
            "images": images or [],
        }
        if extra:
            message.update(extra)
        self.messages.append(message)
        return message

    def add_tool_call(
        self,
        message: dict[str, Any],
        *,
        call_id: str,
        name: str,
        raw_name: str | None = None,
        tool_input: Any = None,
        offset_ms: int | None = None,
        line_number: int | None = None,
    ) -> dict[str, Any]:
        tool = {
            "id": call_id or f"call-{len(self.tools) + 1}",
            "step": message["step"],
            "name": name,
            "raw_name": raw_name or name,
            "input": tool_input if tool_input is not None else {},
            "result": None,
            "offset_ms": offset_ms if offset_ms is not None else message["offset_ms"],
            "line_number": line_number if line_number is not None else message["line_number"],
        }
        message["tools"].append(tool)
        self.tools.append(tool)
        if call_id:
            self._by_call_id[call_id] = tool
            pending = self._pending_results.pop(call_id, None)
            if pending is not None:
                tool["result"] = pending
        if message["role"] == "assistant" and not message["text"]:
            message["role"] = "tool"
        return tool

    def set_result(
        self,
        call_id: str | None,
        *,
        text: str,
        is_error: bool | None = False,
        images: list[dict[str, str]] | None = None,
    ) -> bool:
        """Attach a result to its call. Returns False when no call id matches yet.

        `is_error=None` means the source has no error flag; it is then inferred from the
        text and the result is marked `error_inferred`.
        """
        result = {"text": text or "", "is_error": bool(is_error), "images": images or []}
        if is_error is None and infer_tool_error(result["text"]):
            result["is_error"] = True
            result["error_inferred"] = True
        tool = self._by_call_id.get(call_id or "")
        if tool is None:
            if call_id:
                self._pending_results[call_id] = result
            else:
                self.unmatched_results += 1
            return False
        if tool["result"] is None:
            tool["result"] = result
        else:
            if result["text"]:
                tool["result"]["text"] += ("\n" if tool["result"]["text"] else "") + result["text"]
            if result["is_error"] and not tool["result"]["is_error"]:
                tool["result"]["is_error"] = True
                if result.get("error_inferred"):
                    tool["result"]["error_inferred"] = True
            tool["result"]["images"].extend(result["images"])
        return True

    def tool(self, call_id: str) -> dict[str, Any] | None:
        return self._by_call_id.get(call_id)

    def last_tool(self) -> dict[str, Any] | None:
        return self.tools[-1] if self.tools else None

    def orphan_results(self) -> Iterator[tuple[str, dict[str, Any]]]:
        yield from self._pending_results.items()

    def timeline(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in self.messages:
            items.append(
                {
                    "kind": "message",
                    "role": message["role"],
                    "offset_ms": message["offset_ms"],
                    "step": message["step"],
                    "message_id": message["id"],
                    "label": message["role"],
                }
            )
            for tool in message["tools"]:
                items.append(
                    {
                        "kind": "tool",
                        "offset_ms": tool["offset_ms"],
                        "step": tool["step"],
                        "message_id": message["id"],
                        "label": tool["name"],
                        "tool_id": tool["id"],
                        "is_error": bool((tool.get("result") or {}).get("is_error")),
                    }
                )
        return items


IMAGE_TOKENS = 1500


def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 ASCII characters or ~1.5 CJK characters per token.

    The non-ASCII share comes from the UTF-8 length (a CJK character is 3 bytes), which
    is much faster than scanning characters on multi-GB imports.
    """
    if not text:
        return 0
    other = (len(text.encode("utf-8", "surrogatepass")) - len(text)) / 2
    return round((len(text) - other) / 4 + other / 1.5)


def _message_tokens(message: dict[str, Any]) -> int:
    total = estimate_tokens(message.get("text") or "") + estimate_tokens(message.get("thinking") or "")
    total += IMAGE_TOKENS * len(message.get("images") or [])
    for tool in message.get("tools") or []:
        tool_input = tool.get("input")
        total += estimate_tokens(tool_input if isinstance(tool_input, str) else json.dumps(tool_input, ensure_ascii=False))
        result = tool.get("result") or {}
        total += estimate_tokens(result.get("text") or "") + IMAGE_TOKENS * len(result.get("images") or [])
    return total


def context_track(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate the prompt size before every step and summarize the context curve.

    `context_estimate` is the running size of everything shown to the model before the
    step, restarting at compaction markers (the summary becomes the new context). Adapters
    that know the real prompt size set `context_tokens`, which takes precedence.
    """
    running = 0
    peak = 0
    measured = 0
    compactions = 0
    # Offset between the last measured prompt size and the character estimate there: the
    # estimate misses the system prompt and tool definitions, which stay constant.
    calibration = 0
    for message in messages:
        if message.get("compaction"):
            compactions += 1
            running = 0
            calibration = 0
        size = message.get("context_tokens")
        if isinstance(size, int):
            calibration = size - running
            measured += 1
        message["context_estimate"] = max(0, running + calibration)
        peak = max(peak, size if isinstance(size, int) else message["context_estimate"])
        running += _message_tokens(message)
    return {"context_peak": peak or None, "context_measured_steps": measured, "compactions": compactions}


def finish_run(
    builder: TranscriptBuilder,
    *,
    adapter_id: str,
    source_path: Path,
    run_id: str,
    title: str | None,
    task_id: str | None = None,
    outcome: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    available_tools: list[str] | None = None,
    extra_metrics: dict[str, Any] | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> NormalizedRun:
    """Normalize offsets, compute shared metrics and wrap the transcript."""
    messages = builder.messages
    stamps = [m["offset_ms"] for m in messages if m["offset_ms"] is not None]
    origin = start_ms if start_ms is not None else (min(stamps) if stamps else None)
    if origin is not None:
        for message in messages:
            if message["offset_ms"] is not None:
                message["offset_ms"] = max(0, message["offset_ms"] - origin)
        for tool in builder.tools:
            if tool["offset_ms"] is not None:
                tool["offset_ms"] = max(0, tool["offset_ms"] - origin)
    last = max(
        [m["offset_ms"] for m in messages if m["offset_ms"] is not None] or [0]
    )
    if end_ms is not None and origin is not None:
        last = max(last, end_ms - origin)
    counts: dict[str, int] = {}
    for tool in builder.tools:
        counts[tool["name"]] = counts.get(tool["name"], 0) + 1
    gaps = [
        later["offset_ms"] - earlier["offset_ms"]
        for earlier, later in zip(messages, messages[1:])
        if earlier["offset_ms"] is not None and later["offset_ms"] is not None
    ]
    task = task_from_messages(messages, task_id)
    meta = dict(meta or {})
    metrics = {
        "model": meta.get("model"),
        "max_offset_ms": last if stamps else None,
        "has_clock": bool(stamps),
        "tool_calls": len(builder.tools),
        "tool_kinds": len(counts),
        "tool_counts": counts,
        "tool_errors": sum(
            1 for tool in builder.tools if (tool.get("result") or {}).get("is_error")
        ),
        "message_count": len(messages),
        "assistant_turns": sum(1 for m in messages if m["role"] in {"assistant", "tool"}),
        "thinking_token_events": 0,
        "artifact_state_count": 0,
        "workbench_calls": 0,
        "total_cost_usd": None,
        "total_tokens": None,
        "longest_gap_ms": max(gaps) if gaps else None,
        # Tool results whose call never appeared (or that name no call at all).
        "orphan_results": len(builder._pending_results) + builder.unmatched_results,
        "context_window": None,
        **context_track(messages),
    }
    metrics.update(extra_metrics or {})
    first_title = title or title_from(task["instruction"]) or run_id
    return NormalizedRun(
        adapter_id=adapter_id,
        source_path=str(source_path),
        run_id=run_id,
        title=first_title,
        metrics=metrics,
        messages=messages,
        tools=builder.tools,
        timeline=builder.timeline(),
        tool_catalog=build_tool_catalog(builder.tools, available_tools or []),
        task=task,
        outcome=outcome or make_outcome(),
        meta=meta,
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield (line_number, object) for each non-empty JSON object line.

    Lines that are not valid UTF-8 JSON are skipped like any other broken line (a session
    still being written can end in half a character)."""
    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(value, dict):
                yield line_number, value


def sniff_jsonl(path: Path, limit: int = 40, max_bytes: int = 4_000_000) -> list[dict[str, Any]]:
    """Parse the first few JSON lines of a file without reading all of it."""
    rows: list[dict[str, Any]] = []
    try:
        with path.open("rb") as handle:
            read = 0
            for raw in handle:
                read += len(raw)
                if read > max_bytes and rows:
                    break
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return rows
                if isinstance(value, dict):
                    rows.append(value)
                if len(rows) >= limit:
                    break
    except OSError:
        return []
    return rows
