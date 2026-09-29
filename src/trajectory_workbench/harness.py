"""What a trajectory's harness adds on top of the stock base agent, and where the
model's own turns depend on it.

A harness built on Claude Code, Codex or pi adds components the stock agent does not
have: MCP servers, extra tools, skills, hooks, instruction sections, and files it puts
in the workspace for the agent. A model trained on turns that call or cite those
components learns to rely on them, and does worse in a stock agent. This module finds
the components from the trajectory itself (declared and called tools, the system prompt
compared with the stock agent's sections, injected messages), never from harness names,
and marks the trained steps that use them:

    calls     the step calls an added tool, MCP server or skill
    files     a tool call's arguments name a file the harness instructions provide
    mentions  the step's reasoning or message names an added component
    outputs   the step reads or names a file an added tool produced earlier (a generated
              image, a saved inspection report), which the step alone would not reveal

Whether a step's reasoning *depends* on the harness (plans around it, cites its rules)
is a semantic judgment; Jev asks it per step with this inventory as context (jev.py).
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any

from trajectory_workbench.adapters.common import split_task


# Tools each stock agent ships with. Anything else a trajectory declares or calls was
# added by its harness. (Claude Code's `Skill` tool is stock; the skills it loads are not.)
NATIVE_TOOLS: dict[str, set[str]] = {
    "claude-code": {
        "agent", "task", "bash", "bashoutput", "killshell", "killbash", "glob", "grep", "ls", "read",
        "edit", "multiedit", "write", "notebookedit", "notebookread", "webfetch", "websearch",
        "todowrite", "todoread", "exitplanmode", "enterplanmode", "skill", "slashcommand",
        "taskcreate", "taskget", "tasklist", "taskoutput", "taskstop", "taskupdate", "croncreate",
        "crondelete", "cronlist", "enterworktree", "exitworktree", "schedulewakeup", "sendmessage",
        "workflow", "reportfindings", "askuserquestion", "toolsearch", "monitor", "lsp",
        "listmcpresourcestool", "readmcpresourcetool", "pushnotification", "remotetrigger",
    },
    "codex": {
        "shell", "local_shell", "exec", "exec_command", "write_stdin", "apply_patch", "update_plan",
        "view_image", "web_search", "read_file", "list_dir", "grep_files", "spawn_agent",
        "send_input", "wait", "close_agent", "request_user_input", "request_user_input_async",
        "unified_exec", "sleep", "wait_agent", "send_message", "followup_task", "list_agents",
        "interrupt_agent",
    },
    "pi": {"read", "bash", "edit", "write", "grep", "find", "ls"},
}
# Level-1 sections of the stock system prompts (across recent versions). A section with
# another heading was added by the harness (`--append-system-prompt`, SDK options).
STOCK_SECTIONS: dict[str, set[str]] = {
    "claude-code": {
        "harness", "session-specific guidance", "memory", "auto memory", "environment",
        "context management", "tone and style", "proactiveness", "following conventions",
        "code style", "task management", "doing tasks", "tool usage policy", "code references",
        "system", "using your tools", "executing actions with care", "output efficiency",
        "professional objectivity", "planning without timelines", "asking questions as you work",
        "language", "system reminders", "committing changes with git", "creating pull requests",
        "other common operations", "looking up your own documentation",
    },
}
SKILL_TOOLS = {"skill", "use_skill", "load_skill"}
# Subagent types a fresh Claude Code install ships with; any other `subagent_type` passed
# to its Agent/Task tool was defined by the harness.
SPAWN_TOOLS = {"agent", "task"}
STOCK_SUBAGENTS = {"general-purpose", "explore", "plan", "statusline-setup", "claude-code-guide", "output-style-setup"}
SKILL_FIELDS = ("skill", "name", "command", "skill_name")
# Instruction files the agent loads (Claude Code quotes their path, Codex a heading or tag).
INSTRUCTION_FILE = re.compile(r"Contents of (\S+?(?:CLAUDE|AGENTS)(?:\.local)?\.md)|# (AGENTS\.md) instructions|<(user_instructions)>")
HOOK_NAME = re.compile(r"\b([A-Z][A-Za-z]+(?::[\w.*-]+)?) hook\b")
# `path/to/file.ext`, `dir/sub/` or `file.ext` inside backticks (spaces trimmed).
BACKTICK = re.compile(r"`\s*([^`\s]{3,120})\s*`")
# A file needs a file-type extension, so code such as `location.hash` is not one.
FILE_EXTENSIONS = (
    "html|htm|json|jsonl|zip|md|txt|csv|tsv|yaml|yml|toml|xml|pdf|pptx|docx|xlsx|png|jpe?g|webp|"
    "gif|svg|mp4|webm|py|js|mjs|ts|css|sh|ipynb|log"
)
# Directories (`tests/`) are left out: their names are everyday words.
PATH_LIKE = re.compile(r"^(?:[\w.-]+/)*[\w-][\w.-]*\.(?:" + FILE_EXTENSIONS + r")$", re.IGNORECASE)
KIND_LABELS = {"mcp": "MCP", "tool": "工具", "skill": "Skill", "subagent": "子代理类型", "hook": "Hook", "instruction": "指令", "file": "harness 文件"}
USES = ("calls", "files", "mentions", "outputs")
USE_LABELS = {"calls": "调用", "files": "参数里", "mentions": "提到", "outputs": "用到其产出"}
# Files named in an added tool's result that the agent had not used before the call.
OUTPUT_PATH = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w-][\w.-]*\.(?:png|jpe?g|webp|gif|avif|svg|json|html|txt|md|csv|mp4|webm))(?![\w-])", re.IGNORECASE)


def _key(name: str) -> str:
    return name.split("__")[-1].casefold()


def base_agent(run: Any) -> str | None:
    """The stock agent the harness runs on, from the format, system prompt or tool set."""
    fmt = (run.meta or {}).get("format") or ""
    if fmt.startswith("claude"):
        return "claude-code"
    if fmt == "codex":
        return "codex"
    system = _system_text(run)
    if "You are Claude Code" in system or "Claude Agent SDK" in system:
        return "claude-code"
    if "You are Codex" in system or "running in the Codex CLI" in system:
        return "codex"
    names = {_key(item["name"]) for item in run.tool_catalog} | {_key(t["name"]) for t in run.tools}
    raw = {item["name"] for item in run.tool_catalog} | {t.get("raw_name") or t["name"] for t in run.tools}
    if {"Bash", "Read", "Edit"} <= raw:
        return "claude-code"
    if "apply_patch" in names and names & {"shell", "exec_command", "local_shell"}:
        return "codex"
    if {"read", "bash", "edit", "write"} <= raw:
        return "pi"
    return None


def _system_text(run: Any) -> str:
    """The system prompt: the first system message, or the prompt a harness recorded
    separately (MoH keeps it in `model_prompt`)."""
    message = next((m for m in run.messages if m["role"] == "system"), None)
    if message is not None and message.get("text"):
        return message["text"]
    prompt = ((run.model_prompt or {}).get("system") or {}) if hasattr(run, "model_prompt") else {}
    return prompt.get("text") or "" if isinstance(prompt, dict) else ""


def _sections(text: str) -> list[tuple[str, str]]:
    """(level-1 heading, body) pairs; text before the first heading has heading ''."""
    parts: list[tuple[str, str]] = []
    heading, lines = "", []
    for line in text.splitlines():
        if line.startswith("# ") and not line.startswith("# #"):
            parts.append((heading, "\n".join(lines)))
            heading, lines = line[2:].strip(), []
        else:
            lines.append(line)
    parts.append((heading, "\n".join(lines)))
    return parts


def added_instructions(run: Any, base: str | None) -> list[dict[str, Any]]:
    """Instruction text the harness added: system-prompt sections the stock agent does
    not have, instruction files and harness rules wrapped around the task."""
    found: list[dict[str, Any]] = []
    stock = STOCK_SECTIONS.get(base or "")
    system = next((m for m in run.messages if m["role"] == "system"), None)
    if stock:
        seen_stock = False
        for heading, body in _sections(_system_text(run)):
            if not heading:
                continue
            if heading.casefold() in stock:
                seen_stock = True
                continue
            # Headings before any stock section belong to a replaced prompt; still added.
            topics = [line.lstrip("#").strip() for line in body.splitlines() if line.startswith("## ")]
            topics += [line.strip() for line in body.splitlines() if re.fullmatch(r"[A-Z][A-Z0-9 &/-]{5,}", line.strip())]
            found.append({"name": heading, "topics": topics[:20], "text": body, "after_stock": seen_stock})
    for message in run.messages:
        if message["role"] not in {"system", "user"} or message is system:
            continue
        text = message.get("text") or ""
        # Other injected developer text (Codex's <multi_agent_mode>, plugin lists) is stock.
        for match in INSTRUCTION_FILE.finditer(text):
            name = (match.group(1) or match.group(2) or "AGENTS.md").rsplit("/", 1)[-1]
            if not any(item["name"] == name for item in found):
                found.append({"name": name, "topics": [], "text": text[match.end() : match.end() + 8000], "step": message["step"]})
    _, wrapped = split_task((run.task or {}).get("instruction"))
    if wrapped:
        found.append({"name": "task wrapper", "topics": [], "text": wrapped})
    return found


def _files(instructions: list[dict[str, Any]], task_text: str) -> list[str]:
    """Workspace files the harness instructions name that the user's task does not."""
    files: list[str] = []
    for item in instructions:
        for match in BACKTICK.finditer(item["text"]):
            token = match.group(1).strip("'\".,;:")
            if "://" in token or not PATH_LIKE.match(token) or token in task_text or token in files:
                continue
            if re.fullmatch(r"[\d.x]+", token):
                continue
            files.append(token)
    return files


def inventory(run: Any) -> dict[str, Any]:
    """Components the harness added: [{kind, name, tools?, topics?, steps?}]."""
    base = base_agent(run)
    native = NATIVE_TOOLS.get(base or "", set())
    components: dict[tuple[str, str], dict[str, Any]] = {}

    def add(kind: str, name: str, **extra: Any) -> dict[str, Any]:
        item = components.setdefault((kind, name), {"kind": kind, "name": name})
        for key, value in extra.items():
            if isinstance(value, list):
                bucket = item.setdefault(key, [])
                bucket.extend(v for v in value if v not in bucket)
            else:
                item.setdefault(key, value)
        return item

    names = [item["name"] for item in run.tool_catalog] + [t.get("raw_name") or t["name"] for t in run.tools]
    # Some sources also list an MCP tool under its short name (`js` for `mcp__repl__js`).
    mcp_short = {raw.split("__")[-1] for raw in names if raw.startswith("mcp__")}
    for raw in names:
        if raw.startswith("mcp__"):
            _, server, *rest = raw.split("__")
            add("mcp", server, tools=[raw])
        elif base and _key(raw) not in native and raw not in mcp_short:
            add("tool", raw, tools=[raw])
    for tool in run.tools:
        if _key(tool["name"]) in SKILL_TOOLS:
            skill = _skill_name(tool.get("input"))
            if skill:
                add("skill", skill, tools=[tool.get("raw_name") or tool["name"]])
        kind = _custom_subagent(tool, base)
        if kind:
            add("subagent", kind, tools=[tool.get("raw_name") or tool["name"]])
    for message in run.messages:
        if message.get("layer") == "hook":
            match = HOOK_NAME.search(message.get("text") or "")
            add("hook", match.group(1) if match else "hook", steps=[message["step"]])
    instructions = added_instructions(run, base)
    for item in instructions:
        add("instruction", item["name"], topics=item["topics"])
    task_text = (run.task or {}).get("instruction") or ""
    request, _ = split_task(task_text)
    for path in _files(instructions, request or task_text):
        add("file", path)
    return {"base": base, "components": list(components.values())}


def _skill_name(value: Any) -> str | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value.strip()[:60] or None
    if isinstance(value, dict):
        for field in SKILL_FIELDS:
            if isinstance(value.get(field), str) and value[field].strip():
                return value[field].strip().lstrip("/")[:60]
    return None


def _custom_subagent(tool: dict[str, Any], base: str | None) -> str | None:
    """The harness-defined subagent type a Claude Code Agent/Task call launches, if any."""
    if base != "claude-code" or _key(tool["name"]) not in SPAWN_TOOLS:
        return None
    value = tool.get("input")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    kind = value.get("subagent_type") if isinstance(value, dict) else None
    if isinstance(kind, str) and kind.strip() and kind.strip().casefold() not in STOCK_SUBAGENTS:
        return kind.strip()[:60]
    return None


def _terms(component: dict[str, Any], basenames: set[str]) -> set[str]:
    """Names by which a component can be referred to in text (none for instructions:
    citing a rule is judged semantically, not by matching its heading)."""
    kind, name = component["kind"], component["name"]
    if kind == "mcp":
        terms = {name} | set(component.get("tools", [])) | {tool.split("__")[-1] for tool in component.get("tools", [])}
    elif kind in {"tool", "skill", "subagent"}:
        terms = {name.split("__")[-1], name}
    elif kind == "file":
        terms = {name}
        base = name.rsplit("/", 1)[-1]
        if base in basenames:  # only when no other harness file shares it
            terms.add(base)
    else:
        return set()
    # Very short or generic words would match ordinary prose.
    return {t for t in terms if len(t) >= 5 and not t.isdigit()}


@functools.lru_cache(maxsize=4096)
def _boundary(term: str) -> re.Pattern[str]:
    # A file may end a longer path (`/workspace/artifact.html`); a name may not be part of
    # a longer identifier.
    return re.compile(r"(?<![\w.-])" + re.escape(term) + r"(?![\w-])")


def _found(text: str, terms: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Components named in `text`. The substring test runs first: tool arguments can hold
    whole files, and most terms are absent from most of them."""
    hits: list[dict[str, Any]] = []
    for term, owners in terms.items():
        if term in text and _boundary(term).search(text):
            hits.extend(owner for owner in owners if owner not in hits)
    return hits


def _json_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def trace(run: Any, found: dict[str, Any] | None = None) -> dict[str, Any]:
    """Where the model's own turns (trained tokens) use the added components.

    Returns {base, components: [{kind, name, calls, files, mentions}], steps:
    {how: [step, …]}} with step lists per component and per way of use.
    """
    found = found or inventory(run)
    components = found["components"]
    by_tool: dict[str, dict[str, Any]] = {}
    for component in components:
        for tool in component.get("tools", []):
            if component["kind"] not in {"skill", "subagent"}:
                by_tool[tool] = component
    skills = {c["name"]: c for c in components if c["kind"] == "skill"}
    subagents = {c["name"]: c for c in components if c["kind"] == "subagent"}
    names = [c["name"].rsplit("/", 1)[-1] for c in components if c["kind"] == "file"]
    unique = {name for name in names if names.count(name) == 1}
    prose_terms: dict[str, list[dict[str, Any]]] = {}
    file_terms: dict[str, list[dict[str, Any]]] = {}
    for component in components:
        for term in _terms(component, unique):
            prose_terms.setdefault(term, []).append(component)
            if component["kind"] == "file":
                file_terms.setdefault(term, []).append(component)
    uses: dict[tuple[str, str], dict[str, list[int]]] = {}

    # Paths an added tool returned, once the agent had not used them before (so the file
    # it inspected, e.g. its own deliverable, is not mistaken for the tool's product).
    outputs: dict[str, list[dict[str, Any]]] = {}
    seen_text: list[str] = []

    def note(component: dict[str, Any], how: str, step: int) -> None:
        steps = uses.setdefault((component["kind"], component["name"]), {}).setdefault(how, [])
        if not steps or steps[-1] != step:
            steps.append(step)

    for message in run.messages:
        if message["role"] not in {"assistant", "tool"}:
            continue
        step = message["step"]
        for tool in message["tools"]:
            raw = tool.get("raw_name") or tool["name"]
            if raw in by_tool:
                note(by_tool[raw], "calls", step)
            elif _key(tool["name"]) in SKILL_TOOLS:
                skill = _skill_name(tool.get("input"))
                if skill in skills:
                    note(skills[skill], "calls", step)
            elif _custom_subagent(tool, found["base"]) in subagents:
                note(subagents[_custom_subagent(tool, found["base"])], "calls", step)
            arguments = _json_text(tool.get("input"))
            if file_terms:
                for component in _found(arguments, file_terms):
                    note(component, "files", step)
            if outputs:
                for component in _found(arguments, outputs):
                    note(component, "outputs", step)
            seen_text.append(arguments)
        prose = (message.get("thinking") or "") + "\n" + (message.get("text") or "")
        if prose_terms and prose.strip():
            for component in _found(prose, prose_terms):
                note(component, "mentions", step)
        if outputs and prose.strip():
            for component in _found(prose, outputs):
                note(component, "outputs", step)
        seen_text.append(prose)
        for tool in message["tools"]:
            owner = by_tool.get(tool.get("raw_name") or tool["name"])
            text = (tool.get("result") or {}).get("text") or ""
            if owner is None or not text:
                continue
            earlier = "\n".join(seen_text)
            for match in OUTPUT_PATH.finditer(text[:20000]):
                path = match.group(1)
                if len(path) >= 5 and path not in earlier and path not in file_terms and path not in outputs:
                    outputs[path] = [owner]
    rows = []
    totals: dict[str, set[int]] = {how: set() for how in USES}
    for component in components:
        used = uses.get((component["kind"], component["name"]), {})
        for how, steps in used.items():
            totals[how].update(steps)
        rows.append({**{k: v for k, v in component.items() if k in {"kind", "name", "topics", "tools"}}, **used})
    return {
        "base": found["base"],
        "components": rows,
        "steps": {how: sorted(steps) for how, steps in totals.items() if steps},
    }


EVIDENCE = {
    "calls": "the step calls it",
    "files": "the step passes it in tool arguments",
    "mentions": "the step names it in reasoning or message",
    "outputs": "the step uses a file it produced earlier",
}


def step_evidence(found: dict[str, Any]) -> dict[int, list[str]]:
    """Per step, where code saw it touch an added component, as short lines for Jev."""
    lines: dict[int, list[str]] = {}
    for component in found["components"]:
        for how in USES:
            for step in component.get(how, []):
                lines.setdefault(step, []).append(f"{component['kind']} `{component['name']}`: {EVIDENCE[how]}")
    return lines


def merge(*founds: dict[str, Any]) -> dict[str, Any]:
    """One inventory from several (a rewrite judged against what the original had)."""
    components: dict[tuple[str, str], dict[str, Any]] = {}
    for found in founds:
        for component in found["components"]:
            item = components.setdefault((component["kind"], component["name"]), {"kind": component["kind"], "name": component["name"]})
            for key in ("tools", "topics"):
                if component.get(key):
                    item[key] = sorted(set(item.get(key, [])) | set(component[key]))
    base = next((found["base"] for found in founds if found.get("base")), None)
    return {"base": base, "components": list(components.values())}


def used_steps(found: dict[str, Any]) -> int:
    """Trained steps that use any added component, from a trace."""
    return len({step for steps in found.get("steps", {}).values() for step in steps})


def describe(used: list[dict[str, Any]], limit: int = 5) -> str:
    """One line for a flag: the most used components and how."""
    ranked = sorted(used, key=lambda c: -sum(len(c.get(how, [])) for how in USES))
    parts = []
    for component in ranked[:limit]:
        counts = " · ".join(f"{USE_LABELS[how]} {len(component[how])} 步" for how in USES if component.get(how))
        parts.append(f"{KIND_LABELS[component['kind']]} {component['name']}（{counts}）")
    more = len(ranked) - limit
    return "、".join(parts) + (f" 等 {len(ranked)} 个" if more > 0 else "")


def prompt_summary(found: dict[str, Any], limit: int = 40) -> dict[str, Any]:
    """The inventory as Jev state: what a stock agent would not have."""
    grouped: dict[str, list[Any]] = {}
    for component in found["components"]:
        kind = component["kind"]
        if kind == "mcp":
            entry: Any = {"server": component["name"], "tools": [t.split("__")[-1] for t in component.get("tools", [])][:8]}
        elif kind == "instruction":
            entry = {"section": component["name"], "topics": component.get("topics", [])[:12]}
        else:
            entry = component["name"]
        grouped.setdefault(kind, []).append(entry)
    labels = {
        "mcp": "mcp_servers", "tool": "extra_tools", "skill": "skills", "subagent": "custom_subagent_types", "hook": "hooks",
        "instruction": "added_instruction_sections", "file": "harness_provided_files",
    }
    return {labels[kind]: items[:limit] for kind, items in grouped.items()}
