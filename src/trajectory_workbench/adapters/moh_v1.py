from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

from trajectory_workbench.adapters.common import classify_failure, make_outcome, strip_reminders, task_key, title_from
from trajectory_workbench.models import NormalizedRun, ProbeResult


# Runtime classifications written by MoH, mapped to the shared outcome vocabulary.
MOH_PASS = {"completed", "passed", "success"}


class MohV1Adapter:
    adapter_id = "moh-v1"
    label = "MoH run"

    def detect(self, path: Path) -> bool:
        return path.is_dir() and (path / "events.jsonl").is_file() and (path / "attempts").is_dir()

    def iter_runs(self, path: Path) -> Iterator[tuple[dict[str, Any] | None, NormalizedRun]]:
        yield None, self.load(path)

    def fingerprint(self, path: Path) -> tuple[tuple[str, int, int], ...]:
        root = path.expanduser().resolve()
        candidates = [root / "events.jsonl", root / "resolved_run_manifest.json"]
        candidates.extend((root / "attempts").glob("*/records/*.json"))
        candidates.extend((root / "attempts").glob("*/records/*.jsonl"))
        for filename in ("system_prompt.md", "stdin.txt"):
            candidates.extend((root / "attempts").glob("*/records/" + filename))
        rows: list[tuple[str, int, int]] = []
        for item in sorted(candidates):
            if item.is_file():
                stat = item.stat()
                rows.append((str(item.relative_to(root)), stat.st_mtime_ns, stat.st_size))
        return tuple(rows)

    def probe(self, path: Path) -> ProbeResult:
        root = path.expanduser().resolve()
        errors: list[str] = []
        if not root.is_dir():
            return ProbeResult(ok=False, errors=[f"Not a directory: {root}"])
        if not (root / "events.jsonl").is_file():
            errors.append("Missing events.jsonl")
        attempts_root = root / "attempts"
        attempts = sorted(item for item in attempts_root.glob("*") if item.is_dir())
        if not attempts:
            errors.append("Missing attempts/<attempt-id>")
            return ProbeResult(ok=False, errors=errors)
        attempt = attempts[0]
        records = attempt / "records"
        for name in (
            "trajectory.jsonl",
            "trajectory_index.jsonl",
            "artifact_state_index.json",
            "process.json",
            "attempt_terminal.json",
        ):
            if not (records / name).is_file():
                errors.append(f"Missing attempts/{attempt.name}/records/{name}")
        return ProbeResult(ok=not errors, errors=errors, attempt_id=attempt.name)

    def load(self, path: Path, locator: dict[str, Any] | None = None) -> NormalizedRun:
        root = path.expanduser().resolve()
        probe = self.probe(root)
        if not probe.ok or probe.attempt_id is None:
            raise ValueError("; ".join(probe.errors))
        attempt_id = probe.attempt_id
        records = root / "attempts" / attempt_id / "records"
        trace = self._read_jsonl(records / "trajectory.jsonl")
        trajectory_index = self._read_jsonl(records / "trajectory_index.jsonl")
        artifact_index = self._read_json(records / "artifact_state_index.json")
        process = self._read_json(records / "process.json")
        terminal = self._read_json(records / "attempt_terminal.json")
        run_events = self._read_jsonl(root / "events.jsonl")
        manifest_path = root / "resolved_run_manifest.json"
        manifest = self._read_json(manifest_path) if manifest_path.is_file() else {}

        offsets = {
            int(item["line_number"]): int(item.get("received_offset_ms", 0))
            for item in trajectory_index
            if "line_number" in item
        }
        results = self._collect_tool_results(trace)
        messages: list[dict[str, Any]] = []
        tools: list[dict[str, Any]] = []
        timeline: list[dict[str, Any]] = []
        thinking_token_events = 0
        model: str | None = None
        total_cost_usd: float | None = None

        for line_number, row in enumerate(trace, start=1):
            offset_ms = offsets.get(line_number, 0)
            row_type = str(row.get("type", "unknown"))
            if row_type == "system" and row.get("subtype") == "thinking_tokens":
                thinking_token_events += 1
                continue
            if row_type == "system" and row.get("subtype") == "init":
                model = row.get("model")
            if row_type == "result" and row.get("total_cost_usd") is not None:
                total_cost_usd = float(row["total_cost_usd"])

            message = self._normalize_message(
                row,
                line_number=line_number,
                offset_ms=offset_ms,
                results=results,
            )
            if message is None:
                continue
            messages.append(message)
            timeline.append(
                {
                    "kind": "message",
                    "role": message["role"],
                    "offset_ms": offset_ms,
                    "message_id": message["id"],
                    "label": message["role"],
                }
            )
            for tool in message["tools"]:
                tools.append(tool)
                timeline.append(
                    {
                        "kind": "tool",
                        "offset_ms": offset_ms,
                        "message_id": message["id"],
                        "label": tool["name"],
                        "tool_id": tool["id"],
                    }
                )

        artifact_states = self._artifact_states(artifact_index)
        for state in artifact_states:
            timeline.append(
                {
                    "kind": "artifact",
                    "offset_ms": state["received_offset_ms"],
                    "label": f'{state["net_change"]} {state["size_bytes"]} bytes',
                    "sha256": state["artifact_sha256"],
                }
            )

        workbench = self._workbench(tools, root, attempt_id)
        runtime = self._runtime(process, terminal, run_events)
        max_offset_ms = max(
            [0]
            + [int(item.get("received_offset_ms", 0)) for item in trajectory_index]
        )
        timeline.append(
            {
                "kind": "runtime",
                "offset_ms": max_offset_ms,
                "label": runtime["classification"],
            }
        )
        timeline.sort(key=lambda item: int(item.get("offset_ms", 0)))
        counts: dict[str, int] = {}
        for tool in tools:
            counts[tool["name"]] = counts.get(tool["name"], 0) + 1
        declared_tools, native_tool_surfaces = self._tool_declarations(manifest)
        session_tools = self._session_tools(trace)
        tool_catalog = self._tool_catalog(tools, session_tools, declared_tools)

        model_prompt = self._model_prompt(root, records, process)
        for index, message in enumerate(messages, start=1):
            message["step"] = index
            for tool in message["tools"]:
                tool["step"] = index
        instruction = None
        if model_prompt and model_prompt.get("user"):
            instruction = model_prompt["user"]["text"]
        else:
            first_user = next((m for m in messages if m["role"] == "user" and strip_reminders(m["text"])), None)
            instruction = strip_reminders(first_user["text"]) if first_user else None
        # MoH manifests carry no task id; runs of the same task pair up by instruction hash.
        task_id = None
        classification = str(runtime["classification"])
        classified = None if classification in MOH_PASS else classify_failure(classification)
        if classification in MOH_PASS:
            status = "pass"
        elif classified and classified["kind"]:
            status = classified["status"]
        else:
            status = "fail" if "fail" in classification or "invalid" in classification else "unknown"
        outcome = make_outcome(
            status,
            # The classification (not the free-text message) so failures group by type.
            reason=None if status == "pass" else classification,
            source="MoH runtime",
            infra_failure=False if status == "pass" else (classified or {}).get("infra_failure"),
            detail={
                "classification": classification,
                "exit_code": runtime.get("exit_code"),
                "failure_message": runtime.get("failure_message"),
                "failure_kind": (classified or {}).get("kind"),
            },
        )
        return NormalizedRun(
            adapter_id=self.adapter_id,
            source_path=str(root),
            run_id=root.name,
            title=title_from(instruction) or root.name,
            attempt_id=attempt_id,
            metrics={
                "model": model,
                "max_offset_ms": max_offset_ms,
                "tool_calls": len(tools),
                "tool_kinds": len(counts),
                "tool_counts": counts,
                "message_count": len(messages),
                "thinking_token_events": thinking_token_events,
                "artifact_state_count": len(artifact_states),
                "workbench_calls": len(workbench),
                "total_cost_usd": total_cost_usd,
                "has_clock": True,
                "tool_errors": sum(1 for t in tools if (t.get("result") or {}).get("is_error")),
                "assistant_turns": sum(1 for m in messages if m["role"] in {"assistant", "tool"}),
            },
            timeline=timeline,
            messages=messages,
            tools=tools,
            tool_catalog=tool_catalog,
            native_tool_surfaces=native_tool_surfaces,
            artifact_states=artifact_states,
            workbench=workbench,
            runtime=runtime,
            model_prompt=model_prompt,
            task={"id": task_id, "key": task_key(task_id, instruction), "instruction": instruction},
            outcome=outcome,
            meta={
                "format": "moh-v1",
                "harness": "MoH",
                "model": model,
                "session_id": next((row.get("session_id") for row in trace if row.get("session_id")), None),
            },
        )

    def _model_prompt(
        self, root: Path, records: Path, process: dict[str, Any]
    ) -> dict[str, Any] | None:
        request_path = records / "request.json"
        if (
            request_path.is_symlink()
            or not request_path.resolve().is_relative_to(root)
            or not request_path.is_file()
        ):
            return None
        prompt = self._read_json(request_path).get("model_prompt")
        if (
            not isinstance(prompt, dict)
            or type(prompt.get("format_version")) is not int
            or prompt["format_version"] != 1
            or not isinstance(prompt.get("system_markdown"), str)
            or not isinstance(prompt.get("user_markdown"), str)
        ):
            return None
        binding = process.get("model_prompt")
        if (
            not isinstance(binding, dict)
            or type(binding.get("format_version")) is not int
            or binding["format_version"] != 1
        ):
            binding = {}
        result: dict[str, Any] = {
            "format_version": 1,
            "scope": "moh_model_prompt_v1",
            "source": request_path.relative_to(root).as_posix(),
            "native_system_complete": False,
        }
        for role, filename in (("system", "system_prompt.md"), ("user", "stdin.txt")):
            text = prompt[role + "_markdown"]
            try:
                expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
            except UnicodeError:
                return None
            record_path = records / filename
            record_sha = None
            if (
                not record_path.is_symlink()
                and record_path.resolve().is_relative_to(root)
                and record_path.is_file()
            ):
                try:
                    with record_path.open("rb") as handle:
                        record_sha = hashlib.file_digest(handle, "sha256").hexdigest()
                except OSError:
                    pass
            process_sha = binding.get(role + "_markdown_sha256")
            if not isinstance(process_sha, str):
                process_sha = None
            checks = {
                "record_matches": None if record_sha is None else record_sha == expected,
                "process_matches": None if process_sha is None else process_sha == expected,
            }
            if role == "user":
                stdin_sha = process.get("stdin_sha256")
                checks["stdin_matches"] = None if stdin_sha is None else stdin_sha == expected
            status = "unverified"
            if any(value is False for value in checks.values()):
                status = "mismatch"
            elif all(value is True for value in checks.values()):
                status = "records_match"
            result[role] = {
                "text": text,
                "sha256": expected,
                "record_path": record_path.relative_to(root).as_posix(),
                "record_sha256": record_sha,
                "process_sha256": process_sha,
                "status": status,
                **checks,
            }
        return result

    def _normalize_message(
        self,
        row: dict[str, Any],
        *,
        line_number: int,
        offset_ms: int,
        results: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        row_type = str(row.get("type", "unknown"))
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        content = message.get("content", "")
        blocks = content if isinstance(content, list) else []

        if row_type == "user" and blocks and all(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in blocks
        ):
            return None

        text = self._text_content(content)
        thinking = "\n".join(
            str(block.get("thinking", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "thinking"
        )
        normalized_tools: list[dict[str, Any]] = []
        if row_type == "assistant":
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_id = str(block.get("id", ""))
                raw_name = str(block.get("name", ""))
                result = results.get(tool_id)
                normalized_tools.append(
                    {
                        "id": tool_id,
                        "name": self._tool_name(raw_name),
                        "raw_name": raw_name,
                        "input": block.get("input", {}),
                        "result": result,
                        "offset_ms": offset_ms,
                        "line_number": line_number,
                    }
                )

        if row_type == "result":
            text = self._result_text(row)
        elif row_type == "system" and not text:
            text = json.dumps(row, ensure_ascii=False)

        if not text and not thinking and not normalized_tools and row_type != "result":
            return None
        role = row_type
        if row_type == "assistant" and normalized_tools and not text:
            role = "tool"
        return {
            "id": f"line-{line_number}",
            "line_number": line_number,
            "role": role,
            "offset_ms": offset_ms,
            "text": text,
            "thinking": thinking,
            "tools": normalized_tools,
            **({"native_result": row} if row_type == "result" else {}),
        }

    def _collect_tool_results(
        self, trace: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for row in trace:
            if row.get("type") != "user":
                continue
            message = row.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                continue
            for block in message["content"]:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_id = str(block.get("tool_use_id", ""))
                results[tool_id] = {
                    "text": self._text_content(block.get("content", "")),
                    "is_error": bool(block.get("is_error", False)),
                    "images": self._image_content(block.get("content", "")),
                }
        return results

    def _image_content(self, content: Any) -> list[dict[str, str]]:
        images: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        if not isinstance(content, list):
            return images
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            source = block.get("source")
            if not isinstance(source, dict) or source.get("type") != "base64":
                continue
            media_type, data = source.get("media_type"), source.get("data")
            if not isinstance(media_type, str) or media_type not in {
                "image/png", "image/jpeg", "image/gif", "image/webp"
            }:
                continue
            if not isinstance(data, str) or not data:
                continue
            try:
                base64.b64decode(data, validate=True)
            except (ValueError, binascii.Error):
                continue
            key = (media_type, data)
            if key in seen:
                continue
            seen.add(key)
            images.append({"media_type": media_type, "data": data})
        return images

    def _artifact_states(self, artifact_index: dict[str, Any]) -> list[dict[str, Any]]:
        states: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in artifact_index.get("observations", []):
            if item.get("net_change") not in {"created", "modified"}:
                continue
            digest = str(item.get("artifact_sha256", ""))
            if not digest or digest in seen:
                continue
            seen.add(digest)
            states.append(
                {
                    "received_offset_ms": int(item.get("received_offset_ms", 0)),
                    "net_change": str(item.get("net_change")),
                    "size_bytes": int(item.get("size_bytes", 0)),
                    "artifact_sha256": digest,
                }
            )
        return states

    def _workbench(
        self,
        tools: list[dict[str, Any]],
        root: Path,
        attempt_id: str,
    ) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        for tool in tools:
            if tool["name"] != "Slides Workbench":
                continue
            raw = (tool.get("result") or {}).get("text", "")
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                parsed = None
            asset_path = None
            if isinstance(parsed, dict) and not (tool.get("result") or {}).get("images"):
                asset_path = self._contact_sheet_path(root, attempt_id, parsed)
            observations.append(
                {
                    "tool_id": tool["id"],
                    "offset_ms": tool["offset_ms"],
                    "observation": parsed,
                    "raw_result": raw if parsed is None else None,
                    "contact_sheet_relative_path": asset_path,
                }
            )
        return observations

    def _contact_sheet_path(
        self, root: Path, attempt_id: str, observation: dict[str, Any]
    ) -> str | None:
        supplied = observation.get("contact_sheet_path")
        if not isinstance(supplied, str) or not supplied:
            return None
        relative = Path(supplied)
        if relative.is_absolute() or ".." in relative.parts:
            return None
        attempt = root / "attempts" / attempt_id
        directories = [attempt / "workspace", attempt / "records"]
        directories.extend(sorted((attempt / "records").glob(".rsi-moh-slides-workbench*")))
        expected_sha = observation.get("contact_sheet_sha256")
        for directory in directories:
            candidate = (directory / relative).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                continue
            if expected_sha:
                with candidate.open("rb") as handle:
                    if hashlib.file_digest(handle, "sha256").hexdigest() != expected_sha:
                        continue
            return candidate.relative_to(root).as_posix()
        return None

    def _runtime(
        self,
        process: dict[str, Any],
        terminal: dict[str, Any],
        run_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        failure = next(
            (
                item
                for item in reversed(run_events)
                if item.get("event_type") == "run.failed"
            ),
            None,
        )
        payload = failure.get("payload", {}) if isinstance(failure, dict) else {}
        finalized = next(
            (item for item in reversed(run_events) if item.get("event_type") == "run.finalized"),
            None,
        )
        classification = payload.get("classification", terminal.get("classification")) or "unknown"
        if finalized is not None and "classification" in finalized.get("payload", {}):
            classification = finalized["payload"]["classification"] or "completed"
        return {
            "process_terminal_reason": process.get(
                "terminal_reason", process.get("process_terminal_reason")
            ),
            "exit_code": process.get("exit_code"),
            "agent_result_status": terminal.get("agent_result_status"),
            "classification": classification,
            "failure_message": payload.get("exception_message"),
        }

    def _tool_name(self, name: str) -> str:
        if name == "mcp__generate_image__generate_image":
            return "generate_image"
        if name == "slides_workbench" or name.endswith("__slides_workbench"):
            return "Slides Workbench"
        return name

    def _session_tools(self, trace: list[dict[str, Any]]) -> list[str]:
        tools: list[str] = []
        for row in trace:
            if row.get("type") != "system" or row.get("subtype") != "init":
                continue
            for raw_name in row.get("tools", []):
                if isinstance(raw_name, str) and raw_name:
                    tools.append(raw_name)
        return tools

    def _tool_identity(self, name: str) -> str:
        return "".join(character for character in name.casefold() if character.isalnum())

    def _tool_declarations(
        self, manifest: dict[str, Any]
    ) -> tuple[dict[str, dict[str, set[str]]], list[str]]:
        declared: dict[str, dict[str, set[str]]] = {}

        def add(raw_name: Any, source: str) -> None:
            if not isinstance(raw_name, str) or not raw_name:
                return
            name = self._tool_name(raw_name)
            entry = declared.setdefault(name, {"raw_names": set(), "sources": set()})
            entry["raw_names"].add(raw_name)
            entry["sources"].add(source)

        for source, agent in (
            (
                "execution_profile.agent.allowed_tools",
                (manifest.get("execution_profile") or {}).get("agent", {}),
            ),
            (
                "experiment_config.agent.allowed_tools",
                (manifest.get("experiment_config") or {}).get("agent", {}),
            ),
        ):
            if not isinstance(agent, dict):
                continue
            for raw_name in agent.get("allowed_tools", []):
                add(raw_name, source)

        agent = manifest.get("agent")
        if isinstance(agent, dict):
            bindings = agent.get("capability_bindings", {}).get("bindings", [])
            if isinstance(bindings, list):
                for binding in bindings:
                    if isinstance(binding, dict):
                        add(binding.get("capability_id"), "agent.capability_bindings")

        surfaces: set[str] = set()
        adapter_facts = agent.get("adapter_facts", {}) if isinstance(agent, dict) else {}
        identity = (manifest.get("execution_profile") or {}).get("agent_identity", {})
        identity_facts = identity.get("adapter_facts", {}) if isinstance(identity, dict) else {}
        for facts in (adapter_facts, identity_facts):
            if not isinstance(facts, dict):
                continue
            for surface in facts.get("native_tool_surface", []):
                if isinstance(surface, str) and surface:
                    surfaces.add(surface)
        return declared, sorted(surfaces, key=str.casefold)

    def _tool_catalog(
        self,
        tools: list[dict[str, Any]],
        session_tools: list[str],
        declared_tools: dict[str, dict[str, set[str]]],
    ) -> list[dict[str, Any]]:
        catalog: dict[str, dict[str, Any]] = {}

        def add(
            raw_name: str,
            source: str,
            *,
            call: bool = False,
            available: bool = False,
            declared: bool = False,
            allow_alias: bool = False,
        ) -> None:
            name = self._tool_name(raw_name)
            catalog_name = name
            if catalog_name not in catalog and allow_alias:
                identity = self._tool_identity(name)
                aliases = [
                    candidate
                    for candidate in catalog
                    if self._tool_identity(candidate) == identity
                ]
                if len(aliases) == 1:
                    catalog_name = aliases[0]
            entry = catalog.setdefault(
                catalog_name,
                {
                    "name": catalog_name,
                    "call_count": 0,
                    "observed": False,
                    "available_in_session": False,
                    "declared": False,
                    "raw_names": set(),
                    "sources": set(),
                },
            )
            entry["call_count"] += int(call)
            entry["observed"] = entry["observed"] or call
            entry["available_in_session"] = entry["available_in_session"] or available
            entry["declared"] = entry["declared"] or declared
            entry["raw_names"].add(raw_name)
            entry["sources"].add(source)

        for tool in tools:
            add(tool["raw_name"], "trajectory", call=True)

        for raw_name in session_tools:
            add(raw_name, "system.init.tools", available=True, allow_alias=True)

        for name, declaration in declared_tools.items():
            for raw_name in declaration["raw_names"]:
                for source in declaration["sources"]:
                    add(raw_name, source, declared=True, allow_alias=True)

        normalized: list[dict[str, Any]] = []
        for entry in catalog.values():
            normalized.append(
                {
                    **entry,
                    "raw_names": sorted(entry["raw_names"], key=str.casefold),
                    "sources": sorted(entry["sources"], key=str.casefold),
                }
            )
        return sorted(normalized, key=lambda entry: entry["name"].casefold())

    def _text_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        return "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )

    def _result_text(self, row: dict[str, Any]) -> str:
        for key in ("result", "error"):
            value = row.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                return value
            return json.dumps(value, ensure_ascii=False)
        return ""

    def _read_json(self, path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object: {path}")
        return value

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"Expected object at {path}:{line_number}")
                rows.append(value)
        return rows
