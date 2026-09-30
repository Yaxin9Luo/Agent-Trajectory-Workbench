"""OpenAI-chat style samples: SFT / distillation `dialog.jsonl` and single chat JSON files.

Each JSONL line is one sample `{id, messages: [...], tools: [...], tag: {...}, ...}`.
Large exports (several GB) are indexed by byte offset, so loading one sample seeks to its
line instead of reading the file. Sample ids of the form
`<task>_<model>_attempt_NN[_context_NN | _subagent_<hash>]` are grouped: the main
trajectory, its post-compaction context segments and the subagent runs it spawned.

When the file sits next to a `manifest.json` that lists the TraceLab inputs it was built
from, each sample's grader verdict is read from the input directory's `results.json`.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterator

from trajectory_workbench.adapters.common import (
    HARNESS_TEXT_PREFIXES,
    TranscriptBuilder,
    content_images,
    content_text,
    decode_content,
    finish_run,
    classify_failure,
    make_outcome,
    parse_arguments,
    read_json,
    score_status,
    sniff_jsonl,
)
from trajectory_workbench.models import NormalizedRun


SAMPLE_ID = re.compile(
    r"^(?P<group>.+?_attempt_\d+)(?:_context_(?P<context>\d+)|_subagent_(?P<subagent>[0-9a-f]+))?$"
)
IMAGE_FROM_TOOL = re.compile(r"Image returned by tool\s+(\S+?)\.?\s*$")
TASK_PREFIX = re.compile(r"^(?P<task>.+?)_[^_]+_attempt_\d+")


def looks_like_chat_sample(row: dict[str, Any]) -> bool:
    messages = row.get("messages")
    return (
        isinstance(messages, list)
        and bool(messages)
        and all(isinstance(m, dict) and "role" in m for m in messages[:5])
    )


def sample_group(sample_id: str, tag: dict[str, Any]) -> dict[str, Any]:
    # Sample ids repeat across exports and trial reruns, so keys are scoped to the source.
    group: dict[str, Any] = {"group_key": None, "group_role": "main", "segment": None, "episode_scope": "source"}
    match = SAMPLE_ID.match(sample_id or "")
    if match:
        group["group_key"] = match.group("group")
        if match.group("subagent"):
            group["group_role"] = "subagent"
            group["subagent_id"] = match.group("subagent")
        elif match.group("context"):
            group["group_role"] = "segment"
    segment = tag.get("context_segment")
    if segment is None and match and match.group("context"):
        segment = match.group("context")
    if segment is not None:
        try:
            group["segment"] = int(segment)
        except (TypeError, ValueError):
            pass
    if tag.get("post_compaction"):
        group["post_compaction"] = True
    if tag.get("context_reason"):
        group["context_reason"] = tag["context_reason"]
    if group["group_role"] == "subagent":
        # The Agent / Task call in the parent that launched this run.
        if tag.get("parent_tool_use_id"):
            group["parent_call_id"] = str(tag["parent_tool_use_id"])
        if tag.get("subagent_type"):
            group["subagent_type"] = tag["subagent_type"]
        if tag.get("subagent_description"):
            group["description"] = tag["subagent_description"]
    return group


def tracelab_outcome(results: dict[str, Any]) -> tuple[dict[str, Any], str | None, dict[str, Any]]:
    """Read (outcome, task_id, timing) from a TraceLab / Harbor-export `results.json`."""
    rows = results.get("results") if isinstance(results.get("results"), list) else []
    row = rows[0] if rows and isinstance(rows[0], dict) else {}
    task_id = row.get("task_id")
    score = row.get("average_score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        score = None
    failure = row.get("failure_mode")
    extra = row.get("extra_info") if isinstance(row.get("extra_info"), dict) else {}
    exception = extra.get("exception") if isinstance(extra.get("exception"), dict) else {}
    parser = row.get("parser_results") if isinstance(row.get("parser_results"), dict) else {}
    # A run that crashed before grading has no score; it is not a scored 0.
    classified = classify_failure(
        str(exception.get("exception_message") or ""), exception.get("exception_type"), failure
    ) if (exception or failure) else None
    if score is not None and score <= 0 and classified is not None and classified["status"] == "error":
        # A crash recorded next to a 0 score: the 0 is not a judgment of the rollout.
        score = None
    if isinstance(row.get("is_resolved"), bool) and row["is_resolved"]:
        status = "pass"
    elif score is not None:
        status = score_status(float(score))
    elif classified is not None:
        status = classified["status"]
    elif isinstance(row.get("is_resolved"), bool):
        status = "fail"
    else:
        status = "unknown"
    reason = None
    if status != "pass":
        reason = (classified or {}).get("name") or next((f"{k}={v}" for k, v in parser.items()), None)
    timing = {
        "start": row.get("agent_started_at") or row.get("trial_started_at"),
        "end": row.get("agent_ended_at") or row.get("trial_ended_at"),
        "tokens": (row.get("total_input_tokens") or 0) + (row.get("total_output_tokens") or 0) or None,
    }
    outcome = make_outcome(
        status,
        score=float(score) if isinstance(score, (int, float)) else None,
        reason=reason,
        source="results.json",
        infra_failure=(classified or {}).get("infra_failure") if status != "pass" else False,
        detail={
            "verify_passed": row.get("verify_passed"),
            "failure_mode": failure,
            "failure_kind": (classified or {}).get("kind") if status != "pass" else None,
            "reward_valid": score is not None,
            "exception": exception or None,
            "parser_results": parser,
            "trial_name": row.get("trial_name"),
        },
    )
    return outcome, task_id, timing


class OpenAIChatAdapter:
    adapter_id = "openai-chat"
    label = "Chat / SFT JSONL"

    def detect(self, path: Path) -> bool:
        if not path.is_file() or path.suffix not in {".jsonl", ".json"}:
            return False
        if path.suffix == ".json":
            try:
                if path.stat().st_size > 200_000_000:
                    return False
                value = read_json(path)
            except (OSError, ValueError):
                return False
            return isinstance(value, dict) and looks_like_chat_sample(value)
        rows = sniff_jsonl(path, 1, max_bytes=64_000_000)
        return bool(rows) and looks_like_chat_sample(rows[0])

    # -- scanning ---------------------------------------------------------------------

    def iter_runs(self, path: Path) -> Iterator[tuple[dict[str, Any] | None, NormalizedRun]]:
        if path.suffix == ".json":
            yield None, self.from_sample(read_json(path), path=path, row_index=0)
            return
        outcomes = ManifestOutcomes(path)
        seen: set[str] = set()
        with path.open("rb") as handle:
            index = 0
            while True:
                offset = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                if not raw.strip():
                    continue
                try:
                    sample = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    index += 1
                    continue
                if not isinstance(sample, dict) or not looks_like_chat_sample(sample):
                    index += 1
                    continue
                sample_id = str(sample.get("id") or "")
                key = sample_id if sample_id and sample_id not in seen else f"@{offset}"
                seen.add(sample_id)
                locator = {"offset": offset, "row": index, "key": key}
                run = self.from_sample(
                    sample, path=path, row_index=index, outcome_lookup=outcomes
                )
                yield locator, run
                index += 1

    def load(self, path: Path, locator: dict[str, Any] | None) -> NormalizedRun:
        if path.suffix == ".json" or locator is None:
            if path.suffix == ".json":
                return self.from_sample(read_json(path), path=path, row_index=0)
            locator = {"offset": 0, "row": 0}
        with path.open("rb") as handle:
            handle.seek(int(locator["offset"]))
            sample = json.loads(handle.readline())
        return self.from_sample(
            sample,
            path=path,
            row_index=int(locator.get("row", 0)),
            outcome_lookup=ManifestOutcomes(path),
        )

    # -- conversion -------------------------------------------------------------------

    def from_sample(
        self,
        sample: dict[str, Any],
        *,
        path: Path,
        row_index: int,
        outcome_lookup: "ManifestOutcomes | None" = None,
        outcome: dict[str, Any] | None = None,
        task_id: str | None = None,
        extra_meta: dict[str, Any] | None = None,
        timing: dict[str, Any] | None = None,
        adapter_id: str | None = None,
    ) -> NormalizedRun:
        builder = TranscriptBuilder()
        pending_images_for: dict[str, Any] | None = None
        for position, message in enumerate(sample.get("messages", []), start=1):
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = decode_content(message.get("content"))
            if role == "tool":
                builder.set_result(
                    str(message.get("tool_call_id") or ""),
                    text=content_text(content),
                    is_error=message["is_error"] if isinstance(message.get("is_error"), bool) else None,
                    images=content_images(content),
                )
                pending_images_for = builder.last_tool()
                continue
            text = content_text(content)
            images = content_images(content)
            owner = IMAGE_FROM_TOOL.match(text.strip()) if role == "user" and images else None
            if owner and builder.set_result(owner.group(1), text="", images=images):
                continue
            if role == "user" and pending_images_for is not None and images and not text.strip():
                # Chat formats cannot put images in tool messages, so exporters append a
                # user turn that only carries the tool's screenshots.
                result = pending_images_for.get("result")
                if result is not None:
                    result["images"].extend(images)
                    continue
            if role == "user" and pending_images_for is not None and images:
                texts = [p for p in content if isinstance(p, dict) and p.get("type") == "text"] if isinstance(content, list) else []
                if all(not str(p.get("text", "")).strip() or str(p.get("text", "")).strip() in {"<image>", "Image:"} for p in texts):
                    result = pending_images_for.get("result")
                    if result is not None:
                        result["images"].extend(images)
                        continue
            pending_images_for = None
            if role == "assistant":
                entry = builder.add_message(
                    "assistant",
                    text=text,
                    thinking=str(message.get("reasoning_content") or message.get("reasoning") or ""),
                    line_number=position,
                    images=images,
                )
                for call in message.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function") if isinstance(call.get("function"), dict) else {}
                    name = str(function.get("name") or call.get("name") or "")
                    builder.add_tool_call(
                        entry,
                        call_id=str(call.get("id") or ""),
                        name=name,
                        tool_input=parse_arguments(function.get("arguments", call.get("arguments"))),
                        line_number=position,
                    )
                continue
            mapped = role if role in {"system", "user"} else "system"
            if mapped == "user" and text.lstrip().startswith(HARNESS_TEXT_PREFIXES):
                mapped = "system"
            builder.add_message(mapped, text=text, line_number=position, images=images)

        sample_id = str(sample.get("id") or f"row-{row_index}")
        tag = sample.get("tag") if isinstance(sample.get("tag"), dict) else {}
        apply_request_times(builder, tag.get("ccr_requests"))
        group = sample_group(sample_id, tag)
        meta: dict[str, Any] = {
            "format": "openai-chat",
            "sample_id": sample_id,
            "row": row_index,
            "model": sample.get("synthetic_model") or sample.get("model"),
            "harness": tag.get("code_agent") or sample.get("source"),
            "session_id": tag.get("session_id"),
            "tags": {k: v for k, v in tag.items() if isinstance(v, (str, int, float, bool))},
            **group,
        }
        if isinstance(sample.get("img_list"), list):
            meta["image_count"] = len(sample["img_list"])
        meta.update(extra_meta or {})

        available = []
        # What each declared tool says it does: a rewrite that keeps a tool may still
        # redefine it (the MCP layer of the target harness).
        tool_specs: dict[str, dict[str, str]] = {}
        for tool in sample.get("tools") or []:
            if isinstance(tool, dict):
                function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
                if isinstance(function.get("name"), str):
                    available.append(function["name"])
                    definition = json.dumps(function, sort_keys=True, ensure_ascii=False, default=str)
                    tool_specs[function["name"]] = {
                        "description": str(function.get("description") or ""),
                        "sha": hashlib.sha256(definition.encode("utf-8")).hexdigest()[:16],
                    }
        if tool_specs:
            meta["tool_specs"] = tool_specs

        if outcome is None and outcome_lookup is not None:
            found = outcome_lookup.lookup(row_index, sample_id)
            if found is not None:
                outcome, task_id, timing = found
        if outcome is None:
            outcome = inline_outcome(sample)
        if task_id is None:
            match = TASK_PREFIX.match(sample_id)
            task_id = match.group("task") if match else None
        start_ms = end_ms = None
        if timing:
            from trajectory_workbench.adapters.common import parse_timestamp_ms

            start_ms = parse_timestamp_ms(timing.get("start"))
            end_ms = parse_timestamp_ms(timing.get("end"))
        run = finish_run(
            builder,
            adapter_id=adapter_id or self.adapter_id,
            source_path=path,
            run_id=sample_id,
            title=None,
            task_id=task_id,
            outcome=outcome,
            meta=meta,
            available_tools=available,
            extra_metrics={"total_tokens": (timing or {}).get("tokens")},
            start_ms=start_ms if any(m["offset_ms"] is not None for m in builder.messages) else None,
        )
        if start_ms is not None and end_ms is not None and end_ms >= start_ms:
            run.metrics["wall_ms"] = end_ms - start_ms
        return run


def apply_request_times(builder: TranscriptBuilder, requests: Any) -> None:
    """Give chat samples a clock from the proxy log: request `i` sent the first
    `message_count` messages, so its response is the message right after them."""
    if not isinstance(requests, list):
        return
    by_position = {m["line_number"]: m for m in builder.messages}
    for request in requests:
        if not isinstance(request, dict):
            continue
        count, stamp = request.get("message_count"), request.get("time")
        if not isinstance(count, int) or not isinstance(stamp, (int, float)):
            continue
        message = by_position.get(count + 1)
        if message is not None and message["offset_ms"] is None:
            message["offset_ms"] = int(stamp)
            for tool in message["tools"]:
                tool["offset_ms"] = int(stamp)


def inline_outcome(sample: dict[str, Any]) -> dict[str, Any]:
    """Use a verdict embedded in the sample itself (reward / score / passed), if any."""
    for key in ("reward", "score", "average_score"):
        value = sample.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return make_outcome(score_status(float(value)), score=float(value), source=f"sample.{key}")
    for key in ("passed", "is_resolved", "success", "resolved"):
        value = sample.get(key)
        if isinstance(value, bool):
            return make_outcome("pass" if value else "fail", source=f"sample.{key}")
    return make_outcome()


class ManifestOutcomes:
    """Map SFT rows back to the TraceLab trial they came from, via manifest.json.

    The manifest lists `input_files` in export order with a record count each. A row is
    only matched when its sample id starts with the task id from that trial's
    results.json, so a reordered export yields no verdict instead of a wrong one.
    """

    def __init__(self, path: Path) -> None:
        self.spans: list[tuple[int, int, Path]] = []
        self._cache: dict[Path, Any] = {}
        manifest = path.with_name("manifest.json")
        if not manifest.is_file():
            return
        try:
            data = read_json(manifest)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or not isinstance(data.get("input_files"), list):
            return
        start = 0
        for item in data["input_files"]:
            if not isinstance(item, dict):
                continue
            count = int(item.get("records", 0)) - int(item.get("discarded_records", 0) or 0)
            source = Path(str(item.get("path", "")))
            self.spans.append((start, start + count, source.parent / "results.json"))
            start += count

    def lookup(self, row_index: int, sample_id: str):
        for start, end, results_path in self.spans:
            if start <= row_index < end:
                return self._read(results_path, sample_id)
        return None

    def _read(self, results_path: Path, sample_id: str):
        if results_path not in self._cache:
            try:
                self._cache[results_path] = tracelab_outcome(read_json(results_path))
            except (OSError, ValueError):
                self._cache[results_path] = None
        found = self._cache[results_path]
        if found is None:
            return None
        task_id = found[1]
        if task_id and not sample_id.startswith(str(task_id)):
            return None
        return found
