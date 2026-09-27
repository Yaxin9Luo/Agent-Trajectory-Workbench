"""ATIF (Agent Trajectory Interchange Format, Harbor) `trajectory.json` files.

Field names follow harbor 0.23 `harbor.models.trajectories` (ATIF-v1.x): steps with
`source` system / user / agent, `message`, `reasoning_content`, `tool_calls`
(`tool_call_id`, `function_name`, `arguments`) and `observation.results`
(`source_call_id`, `content`). Embedded `subagent_trajectories` are listed as their own
trajectories in the same group.

A Harbor trial directory (`result.json` + `agent/trajectory.json`) adds the verifier
reward and any exception as the outcome.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from trajectory_workbench.adapters.common import (
    TranscriptBuilder,
    content_images,
    content_text,
    finish_run,
    classify_failure,
    make_outcome,
    parse_timestamp_ms,
    read_json,
    score_status,
)
from trajectory_workbench.models import NormalizedRun


def looks_like_atif(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and str(value.get("schema_version", "")).startswith("ATIF")
        and isinstance(value.get("steps"), list)
    )


def atif_images(content: Any) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    if not isinstance(content, list):
        return images
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image":
            source = part.get("source") or {}
            if isinstance(source.get("path"), str):
                images.append({"path": source["path"], "media_type": source.get("media_type", "")})
    return images + content_images(content)


def harbor_outcome(result: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    verifier = result.get("verifier_result") if isinstance(result.get("verifier_result"), dict) else {}
    rewards = verifier.get("rewards") if isinstance(verifier.get("rewards"), dict) else {}
    score = rewards.get("reward")
    if score is None and rewards:
        score = next(iter(rewards.values()))
    exception = result.get("exception_info") if isinstance(result.get("exception_info"), dict) else None
    classified = (
        classify_failure(str(exception.get("exception_type") or ""), str(exception.get("exception_message") or "")[:200])
        if exception else None
    )
    reward_valid = isinstance(score, (int, float))
    if reward_valid and classified and score <= 0:
        # Harbor still writes reward 0 after the agent crashed or the verifier broke;
        # that 0 is not a judgment of the rollout.
        reward_valid = False
    if reward_valid:
        status = score_status(float(score))
    elif classified:
        status = classified["status"]
    else:
        status = "unknown"
    reason = None
    infra = None
    if exception and status != "pass":
        reason = f"{exception.get('exception_type')}: {str(exception.get('exception_message', ''))[:300]}"
        infra = classified["infra_failure"] if classified else None
    task_id = result.get("task_name")
    outcome = make_outcome(
        status,
        score=float(score) if reward_valid else None,
        reason=reason,
        source="harbor result.json",
        infra_failure=infra if status != "pass" else False,
        detail={
            "rewards": rewards,
            "trial_name": result.get("trial_name"),
            "reward_valid": reward_valid,
            "failure_kind": classified["kind"] if classified and status != "pass" else None,
        },
    )
    return outcome, task_id


class AtifAdapter:
    adapter_id = "atif"
    label = "ATIF trajectory"

    def detect(self, path: Path) -> bool:
        if path.is_dir():
            return (path / "result.json").is_file() and (path / "agent" / "trajectory.json").is_file()
        if not path.is_file() or path.suffix != ".json":
            return False
        try:
            with path.open("rb") as handle:
                head = handle.read(4096)
        except OSError:
            return False
        return b'"schema_version"' in head and b"ATIF" in head

    def _files(self, path: Path) -> tuple[Path, dict[str, Any] | None]:
        if path.is_dir():
            try:
                result = read_json(path / "result.json")
            except (OSError, ValueError):
                result = None
            return path / "agent" / "trajectory.json", result if isinstance(result, dict) else None
        return path, None

    def iter_runs(self, path: Path) -> Iterator[tuple[dict[str, Any] | None, NormalizedRun]]:
        file, result = self._files(path)
        document = read_json(file)
        if not looks_like_atif(document):
            return
        yield None, self._convert(document, path, result, None)
        for sub in document.get("subagent_trajectories") or []:
            if looks_like_atif(sub) and sub.get("trajectory_id"):
                locator = {"subagent": sub["trajectory_id"], "key": "sub:" + str(sub["trajectory_id"])}
                yield locator, self._convert(sub, path, result, document)

    def load(self, path: Path, locator: dict[str, Any] | None) -> NormalizedRun:
        file, result = self._files(path)
        document = read_json(file)
        if locator and locator.get("subagent"):
            for sub in document.get("subagent_trajectories") or []:
                if isinstance(sub, dict) and sub.get("trajectory_id") == locator["subagent"]:
                    return self._convert(sub, path, result, document)
            raise KeyError(locator["subagent"])
        return self._convert(document, path, result, None)

    def _convert(
        self,
        document: dict[str, Any],
        path: Path,
        result: dict[str, Any] | None,
        parent: dict[str, Any] | None,
    ) -> NormalizedRun:
        builder = TranscriptBuilder()
        for step in document.get("steps", []):
            if not isinstance(step, dict):
                continue
            source = step.get("source")
            stamp = parse_timestamp_ms(step.get("timestamp"))
            text = content_text(step.get("message"))
            images = atif_images(step.get("message"))
            line = step.get("step_id")
            if source == "agent":
                step_metrics = step.get("metrics") if isinstance(step.get("metrics"), dict) else {}
                prompt = step_metrics.get("prompt_tokens")
                entry = builder.add_message(
                    "assistant",
                    text=text,
                    thinking=str(step.get("reasoning_content") or ""),
                    offset_ms=stamp,
                    line_number=line,
                    images=images,
                    extra={"context_tokens": prompt, "output_tokens": step_metrics.get("completion_tokens")}
                    if isinstance(prompt, int) else None,
                )
                for call in step.get("tool_calls") or []:
                    if isinstance(call, dict):
                        builder.add_tool_call(
                            entry,
                            call_id=str(call.get("tool_call_id") or ""),
                            name=str(call.get("function_name") or ""),
                            tool_input=call.get("arguments", {}),
                        )
            else:
                builder.add_message(
                    "user" if source == "user" else "system",
                    text=text,
                    offset_ms=stamp,
                    line_number=line,
                    images=images,
                )
                entry = None
            observation = step.get("observation") if isinstance(step.get("observation"), dict) else {}
            for item in observation.get("results") or []:
                if not isinstance(item, dict):
                    continue
                extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
                body = content_text(item.get("content"))
                if item.get("subagent_trajectory_ref"):
                    refs = ", ".join(
                        str(ref.get("trajectory_id") or ref.get("trajectory_path"))
                        for ref in item["subagent_trajectory_ref"]
                        if isinstance(ref, dict)
                    )
                    body = (body + "\n" if body else "") + f"[subagent trajectory: {refs}]"
                    spawner = builder.tool(str(item.get("source_call_id") or ""))
                    first = next((ref for ref in item["subagent_trajectory_ref"] if isinstance(ref, dict) and ref.get("trajectory_id")), None)
                    if spawner is not None and first is not None:
                        spawner["spawned"] = f"atif:{first['trajectory_id']}"
                matched = builder.set_result(
                    item.get("source_call_id"),
                    text=body,
                    is_error=extra["is_error"] if isinstance(extra.get("is_error"), bool) else None,
                    images=atif_images(item.get("content")),
                )
                if not matched and not item.get("source_call_id"):
                    builder.add_message("system", text="[observation] " + body, offset_ms=stamp, line_number=line)
        agent = document.get("agent") if isinstance(document.get("agent"), dict) else {}
        final = document.get("final_metrics") if isinstance(document.get("final_metrics"), dict) else {}
        outcome, task_id = harbor_outcome(result) if result else (make_outcome(), None)
        meta: dict[str, Any] = {
            "format": "atif",
            "schema": document.get("schema_version"),
            "harness": agent.get("name"),
            "harness_version": agent.get("version"),
            "model": agent.get("model_name"),
            "session_id": document.get("session_id"),
            "group_role": "subagent" if parent is not None else "main",
            # Harbor agents often write a constant session id ("copilot-cli", "unknown").
            "episode_scope": "source",
        }
        root = (parent or document).get("trajectory_id") or (parent or document).get("session_id")
        if root:
            meta["group_key"] = f"atif:{root}"
            meta["self_key"] = f"atif:{document.get('trajectory_id') or root}"
            if parent is not None:
                meta["parent_key"] = f"atif:{root}"
        available = [
            str((d.get("function") or d).get("name"))
            for d in agent.get("tool_definitions") or []
            if isinstance(d, dict) and isinstance((d.get("function") or d).get("name"), str)
        ]
        tokens = sum(
            int(final.get(key) or 0) for key in ("total_prompt_tokens", "total_completion_tokens")
        ) or None
        run_id = str(document.get("trajectory_id") or document.get("session_id") or path.stem)
        return finish_run(
            builder,
            adapter_id=self.adapter_id,
            source_path=path,
            run_id=run_id,
            title=None,
            task_id=task_id,
            outcome=outcome,
            meta=meta,
            available_tools=available,
            extra_metrics={"total_cost_usd": final.get("total_cost_usd"), "total_tokens": tokens},
        )
