from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trajectory_workbench.models import NormalizedRun, ProbeResult


class MohV1Adapter:
    adapter_id = "moh-v1"

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

    def load(self, path: Path) -> NormalizedRun:
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

        return NormalizedRun(
            adapter_id=self.adapter_id,
            source_path=str(root),
            run_id=root.name,
            title=root.name,
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
            },
            timeline=timeline,
            messages=messages,
            tools=tools,
            tool_catalog=tool_catalog,
            native_tool_surfaces=native_tool_surfaces,
            artifact_states=artifact_states,
            workbench=workbench,
            runtime=runtime,
        )

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
            text = str(row.get("result") or row.get("error") or "")
        elif row_type == "system" and not text:
            text = json.dumps(row, ensure_ascii=False)

        if not text and not thinking and not normalized_tools:
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
                }
        return results

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
            if isinstance(parsed, dict) and parsed.get("contact_sheet_path"):
                relative = (
                    Path("attempts")
                    / attempt_id
                    / "workspace"
                    / str(parsed["contact_sheet_path"])
                )
                asset_path = relative.as_posix()
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
        return {
            "process_terminal_reason": process.get(
                "terminal_reason", process.get("process_terminal_reason")
            ),
            "exit_code": process.get("exit_code"),
            "agent_result_status": terminal.get("agent_result_status"),
            "classification": payload.get(
                "classification", terminal.get("classification", "completed")
            ),
            "failure_message": payload.get("exception_message"),
        }

    def _tool_name(self, name: str) -> str:
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
