"""Rule-based behaviour signals, computed once per trajectory at import.

Each signal is a cheap, explainable heuristic over the normalized transcript. It keeps
the evidence (step numbers) so the reader can jump to it and judge whether the rule
fired correctly. These are the checks that let a collection be watched without reading
every trajectory; semantic judgments that need language understanding live in `jev.py`.

Flags:
    loop          same tool with identical input 3+ times in a row
    blind_retry   a failed call repeated with identical input
    no_verify     files were changed and nothing checked them afterwards
    test_edit     a test / eval file was modified (possible grader gaming)
    harness_ref   the model's own turns call, cite or name a component its harness added
                  (see harness.py; whether the reasoning depends on it is Jev's call)
    infra_error   tool output looks like an environment or service failure
    redundancy    repeated paragraphs or apology / reassurance filler
    lang_mix      assistant prose switches language between turns
    subagent_edit a subagent (e.g. a reviewer) modified files
"""

from __future__ import annotations

import json
import re
from typing import Any

from trajectory_workbench import harness


MUTATING_TOOLS = {
    "write",
    "edit",
    "multiedit",
    "notebookedit",
    "apply_patch",
    "write_file",
    "edit_file",
    "create_file",
    "str_replace",
    "str_replace_editor",
    "str_replace_based_edit_tool",
    "replace",
    "patch",
}
SHELL_TOOLS = {
    "bash",
    "shell",
    "exec_command",
    "local_shell",
    "run_command",
    "execute_command",
    "terminal",
    "exec",
    "run_shell_command",
}
SHELL_MUTATION = re.compile(
    r"(?:^|[^<>&=0-9-])>{1,2}\s*(?!/dev/null)[\"']?[\w~.-]*[./][\w./~-]+|\btee\b|\bsed\s+-i|\bperl\s+-p?i|"
    r"\bgit\s+apply\b|\bapply_patch\b|\bpatch\s+-p|\bcp\s|\bmv\s|\brm\s|\bmkdir\s|\btouch\s|"
    r"write_text\(|open\([^)]*['\"]w",
)
CHECK_COMMAND = re.compile(
    r"\b(pytest|unittest|tox|nox|npm\s+(run\s+)?test|yarn\s+test|pnpm\s+test|node\s+--test|"
    r"node\s+--check|jest|vitest|mocha|go\s+test|go\s+vet|cargo\s+(test|check|clippy)|"
    r"make\s+(test|check)|ruff|mypy|pyright|tsc|eslint|flake8|py_compile|compileall|"
    r"playwright|puppeteer|chromium|headless|screenshot|lighthouse|html-validate|validator|"
    r"curl\s|wget\s|diff\s|cmp\s|sha256sum|shasum|python3?\s+-c|node\s+-e|"
    r"verify|validate|lint|\w*test\w*)",
    re.IGNORECASE,
)
CHECK_TOOLS = re.compile(
    r"screenshot|browser_|playwright|puppeteer|workbench|review|verify|validate|inspect|"
    r"^read$|^view$|read_file|^glob$|^grep$",
    re.IGNORECASE,
)
TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|eval|evals|grader|graders|verifier)(/|$)|"
    r"(^|/)(test_[^/]+\.py|[^/]+_test\.(py|go)|[^/]+\.(test|spec)\.[jt]sx?|conftest\.py)$|"
    r"(^|/)reward(\.txt|\.json)?$",
    re.IGNORECASE,
)
# Paths can sit inside JSON / JS string literals, so stop at escapes and quotes.
PATCH_PATH = re.compile(r"\*\*\* (Update|Add|Delete) File: ([^\s\\\"'`]+)")
CREATING_TOOLS = {"write", "write_file", "create_file"}
REDIRECT_PATH = re.compile(r">{1,2}\s*([\w./~-]+)")

INFRA_PATTERN = re.compile(
    r"ETIMEDOUT|ECONNRESET|ECONNREFUSED|connection (reset|refused|lost|aborted|closed)|"
    r"502 Bad Gateway|503 Service|504 Gateway|gateway time-?out|rate[ _-]?limit|429 Too Many|"
    r"No space left on device|\bOOMKilled\b|out of memory|Cannot allocate memory|"
    r"network is unreachable|Temporary failure in name resolution|Could not resolve host|"
    r"overloaded_error|API Error|internal server error|sandbox (error|unavailable|crashed)|"
    r"Killed\s*$",
    re.IGNORECASE | re.MULTILINE,
)
FILLER_PATTERN = re.compile(
    r"\b(sorry|apologi[sz]e|my apologies|let me double[- ]check|let me re-?verify|"
    r"to be safe, let me|I (have|'ve) (now )?(verified|confirmed) (again|once more))\b|"
    r"抱歉|对不起|让我再(确认|检查)一下",
    re.IGNORECASE,
)
CODE_SPAN = re.compile(r"```.*?```|`[^`]*`|https?://\S+|(?:/[\w.-]+){2,}", re.DOTALL)
CJK = re.compile(r"[㐀-鿿豈-﫿぀-ヿ가-힯]")
LATIN_WORD = re.compile(r"[A-Za-z]{2,}")

FLAG_LABELS = {
    "loop": "原地打转",
    "blind_retry": "报错后原样重试",
    "no_verify": "改完未验证",
    "test_edit": "改动测试/评分文件",
    "harness_ref": "用到 harness 组件",
    "infra_error": "基础设施报错",
    "redundancy": "重复/道歉",
    "lang_mix": "语言混杂",
    "subagent_edit": "子代理改文件",
}
FLAG_SEVERITY = {
    "loop": "warn",
    "blind_retry": "warn",
    "no_verify": "warn",
    "test_edit": "bad",
    "harness_ref": "info",
    "infra_error": "info",
    "redundancy": "info",
    "lang_mix": "info",
    "subagent_edit": "info",
}


def base_name(name: str) -> str:
    """`mcp__server__tool` -> `tool`, lower-cased, for rule matching."""
    return name.split("__")[-1].casefold()


def canonical_input(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def command_text(tool: dict[str, Any]) -> str:
    value = tool.get("input")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("command", "cmd", "script", "input", "code"):
            item = value.get(key)
            if isinstance(item, list):
                return " ".join(str(part) for part in item)
            if isinstance(item, str):
                return item
    return ""


def touched_paths(tool: dict[str, Any]) -> list[str]:
    value = tool.get("input")
    paths: list[str] = []
    if isinstance(value, dict):
        for key in ("file_path", "path", "filename", "notebook_path", "target_file"):
            if isinstance(value.get(key), str):
                paths.append(value[key])
    text = command_text(tool)
    paths.extend(path for _, path in PATCH_PATH.findall(text))
    if base_name(tool["name"]) in SHELL_TOOLS:
        paths.extend(REDIRECT_PATH.findall(text))
    return paths


def is_mutation(tool: dict[str, Any]) -> bool:
    name = base_name(tool["name"])
    if name in MUTATING_TOOLS:
        return True
    if name in SHELL_TOOLS or name == "exec":
        return bool(SHELL_MUTATION.search(command_text(tool)))
    if isinstance(tool.get("input"), dict) and tool["input"].get("command") in {"create", "str_replace", "insert"}:
        return True
    return False


def is_check(tool: dict[str, Any], mutated: set[str]) -> str | None:
    """Return why this call counts as checking earlier changes, or None."""
    name = base_name(tool["name"])
    if name in SHELL_TOOLS or name == "exec":
        command = command_text(tool)
        if is_mutation(tool) and not CHECK_COMMAND.search(command):
            return None
        match = CHECK_COMMAND.search(command)
        if match:
            return f"命令 {match.group(0).strip()}"
        names = {path.rsplit("/", 1)[-1] for path in mutated if path}
        hit = next((n for n in names if len(n) > 3 and n in command), None)
        return f"命令查看了 {hit}" if hit else None
    if name in {"read", "view", "read_file"}:
        paths = set(touched_paths(tool))
        return "回读改过的文件" if paths & mutated or not mutated else None
    if CHECK_TOOLS.search(name) or CHECK_TOOLS.search(tool["name"]):
        return f"工具 {tool['name']}"
    return None


def harness_layer(tool: dict[str, Any]) -> str:
    """Which harness layer a call belongs to: base agent tool, MCP, skill or subagent."""
    raw = tool.get("raw_name") or tool["name"]
    if raw.startswith("mcp__") or "__" in raw and not raw.startswith("collaboration__"):
        return "mcp"
    name = base_name(raw)
    if name in {"skill", "use_skill", "load_skill"}:
        return "skill"
    if name in {"agent", "task", "spawn_agent", "send_message"} or raw.startswith("collaboration__"):
        return "subagent"
    return "base"


def compute(run: Any) -> dict[str, Any]:
    tools: list[dict[str, Any]] = run.tools
    messages: list[dict[str, Any]] = run.messages
    flags: list[dict[str, Any]] = []
    values: dict[str, Any] = {}

    def flag(key: str, steps: list[int], detail: str) -> None:
        flags.append(
            {
                "key": key,
                "label": FLAG_LABELS[key],
                "severity": FLAG_SEVERITY[key],
                "steps": sorted(set(steps))[:50],
                "detail": detail,
            }
        )

    # Loops and blind retries.
    streak, best, best_steps, current_steps = 1, 1, [], []
    previous_key = None
    blind: list[int] = []
    for index, tool in enumerate(tools):
        key = (tool["name"], canonical_input(tool.get("input")))
        if key == previous_key:
            streak += 1
            current_steps.append(tool["step"])
        else:
            streak, current_steps = 1, [tool["step"]]
        if streak > best:
            best, best_steps = streak, list(current_steps)
        previous_key = key
        result = tool.get("result") or {}
        if result.get("is_error"):
            follower = next((t for t in tools[index + 1 :] if t["name"] == tool["name"]), None)
            if follower is not None and canonical_input(follower.get("input")) == key[1]:
                blind.append(follower["step"])
    values["max_identical_streak"] = best
    if best >= 3:
        flag("loop", best_steps, f"同一调用连续 {best} 次")
    values["blind_retries"] = len(blind)
    if blind:
        flag("blind_retry", blind, f"{len(blind)} 次报错后以相同参数重试")

    # Mutation / verification.
    mutated_paths: set[str] = set()
    mutation_steps: list[int] = []
    test_steps: list[int] = []
    test_files: set[str] = set()
    last_mutation_index = None
    created: set[str] = set()
    for index, tool in enumerate(tools):
        if is_mutation(tool):
            mutation_steps.append(tool["step"])
            last_mutation_index = index
            added = {path for kind, path in PATCH_PATH.findall(command_text(tool)) if kind == "Add"}
            creates = base_name(tool["name"]) in CREATING_TOOLS or (
                isinstance(tool.get("input"), dict) and tool["input"].get("command") == "create"
            )
            for path in touched_paths(tool):
                mutated_paths.add(path)
                if creates or path in added:
                    created.add(path)
                    continue
                # Only modifying a test / grader file the agent did not create counts.
                if TEST_PATH.search(path) and path not in created:
                    test_steps.append(tool["step"])
                    test_files.add(path)
    values["mutations"] = len(mutation_steps)
    verification = None
    if last_mutation_index is not None:
        last_paths = set(touched_paths(tools[last_mutation_index]))
        for tool in tools[last_mutation_index + 1 :]:
            reason = is_check(tool, last_paths or mutated_paths)
            if reason:
                verification = {"step": tool["step"], "reason": reason}
                break
        values["last_mutation_step"] = tools[last_mutation_index]["step"]
        values["verified_after_last_change"] = verification is not None
        values["verification"] = verification
        if verification is None:
            flag("no_verify", [tools[last_mutation_index]["step"]], "最后一次改动之后没有测试、回读或截图检查")
    else:
        values["verified_after_last_change"] = None
    values["check_calls"] = sum(1 for t in tools if is_check(t, mutated_paths))
    if test_steps:
        flag("test_edit", test_steps, "改动了已有的 " + ", ".join(sorted(test_files)[:5]))
        # Editing tests is normal development work; it is a grading risk only when a
        # grader judged this trajectory.
        if (run.outcome or {}).get("status") in {None, "unknown"}:
            flags[-1]["severity"] = "info"

    # Text-level patterns.
    filler_steps: list[int] = []
    paragraphs: dict[str, int] = {}
    duplicate_steps: list[int] = []
    scripts: list[tuple[int, str]] = []
    assistant_chars = thinking_chars = 0
    for message in messages:
        if message["role"] not in {"assistant", "tool"}:
            continue
        text, thinking = message.get("text") or "", message.get("thinking") or ""
        assistant_chars += len(text)
        thinking_chars += len(thinking)
        if len(FILLER_PATTERN.findall(text + "\n" + thinking)) >= 1:
            filler_steps.append(message["step"])
        for paragraph in re.split(r"\n\s*\n", text):
            normalized = re.sub(r"\s+", " ", paragraph).strip()
            if len(normalized) < 60:
                continue
            if normalized in paragraphs:
                duplicate_steps.append(message["step"])
            else:
                paragraphs[normalized] = message["step"]
        prose = CODE_SPAN.sub(" ", text)
        # Compare in word-sized units: about 2.5 CJK characters carry one English word.
        cjk = len(CJK.findall(prose)) / 2.5
        latin = len(LATIN_WORD.findall(prose))
        if cjk + latin >= 10:
            scripts.append((message["step"], "cjk" if cjk >= latin else "latin"))
    values["assistant_chars"] = assistant_chars
    values["thinking_chars"] = thinking_chars
    # Components the harness added and where the model's turns use them.
    trace = harness.trace(run)
    values["harness"] = trace
    used = [c for c in trace["components"] if any(c.get(how) for how in harness.USES)]
    if used:
        steps = [step for c in used for how in harness.USES for step in c.get(how, [])]
        flag("harness_ref", steps, harness.describe(used))
    values["filler_steps"] = len(filler_steps)
    values["duplicate_paragraphs"] = len(duplicate_steps)
    if len(duplicate_steps) >= 2 or len(filler_steps) >= 3:
        flag("redundancy", duplicate_steps + filler_steps, f"重复段落 {len(duplicate_steps)} · 道歉/自我确认 {len(filler_steps)}")
    if scripts:
        counts = {"cjk": 0, "latin": 0}
        for _, script in scripts:
            counts[script] += 1
        minority = min(counts, key=counts.get)
        share = counts[minority] / len(scripts)
        values["lang_mix_share"] = round(share, 3)
        if counts[minority] >= 2 and share >= 0.15:
            flag("lang_mix", [step for step, script in scripts if script == minority], f"{counts[minority]}/{len(scripts)} 段正文换成另一种语言")

    # Infrastructure failures.
    infra_steps = []
    for tool in tools:
        text = (tool.get("result") or {}).get("text") or ""
        if text and INFRA_PATTERN.search(text[-4000:]):
            infra_steps.append(tool["step"])
    for message in messages:
        if message["role"] in {"system", "result"} and INFRA_PATTERN.search(message.get("text") or ""):
            infra_steps.append(message["step"])
    values["infra_hits"] = len(infra_steps)
    if infra_steps:
        flag("infra_error", infra_steps, f"{len(infra_steps)} 处输出像环境/服务故障")

    if (run.meta or {}).get("group_role") == "subagent" and mutation_steps:
        flag("subagent_edit", mutation_steps, f"子代理有 {len(mutation_steps)} 次写操作")

    # Harness layers (base agent / MCP / skill / subagent) and instruction injection.
    layers: dict[str, int] = {"base": 0, "mcp": 0, "skill": 0, "subagent": 0}
    for tool in tools:
        layers[harness_layer(tool)] += 1
    values["layers"] = {
        **layers,
        "hook_events": sum(1 for m in messages if m.get("layer") == "hook"),
        "instruction_events": sum(1 for m in messages if m.get("layer") == "instruction"),
    }

    values["steps"] = len(messages)
    values["tool_calls"] = len(tools)
    values["tool_errors"] = sum(1 for t in tools if (t.get("result") or {}).get("is_error"))
    return {"values": values, "flags": flags}
