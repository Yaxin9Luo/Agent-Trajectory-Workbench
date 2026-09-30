"""Review of a rewrite: an original trajectory and its rewritten version, aligned step by
step and diffed word by word, plus what the rewrite took out of the harness and where the
rewritten trajectory still uses it.

Everything here is computed from the two trajectories, so it works for any harness and any
rewriting pipeline. A pipeline may add annotations (why an edit was made, which plan item it
follows, validator warnings, a message index map); `rewrite_annotations` reads them and
`review` attaches them to the diff, but nothing here requires them.
"""

from __future__ import annotations

import bisect
import difflib
import hashlib
import json
import re
from typing import Any

from trajectory_workbench import harness
from trajectory_workbench.adapters.common import COMPACTION_PREFIX, COMPACTION_REQUEST_PREFIX


# -- word diff ------------------------------------------------------------------------------

# Whitespace runs, ASCII words, then any other character on its own: Chinese has no spaces,
# so one character is the smallest change a reader notices.
TOKEN = re.compile(r"\s+|[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")
# Changed line blocks longer than this are shown as replaced whole, not diffed word by word.
TOKEN_DIFF_CHARS = 40_000
# Tool inputs and results longer than this are only compared, not diffed.
TOOL_DIFF_CHARS = 200_000
# Removed calls keep this much of their input and result for the reader.
REMOVED_TOOL_CHARS = 20_000


def word_diff(before: str, after: str) -> list[list[str]]:
    """Segments [op, text], op "=", "-" or "+", that turn `before` into `after`.

    Lines are matched first and only changed lines are diffed by word, which keeps long
    texts fast. Inside one change all removed text comes before all added text.
    """
    if before == after:
        return [["=", before]] if before else []
    a_lines, b_lines = before.splitlines(keepends=True), after.splitlines(keepends=True)
    segments: list[list[str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False).get_opcodes():
        a, b = "".join(a_lines[i1:i2]), "".join(b_lines[j1:j2])
        if tag == "equal":
            segments.append(["=", a])
        elif tag == "replace" and len(a) + len(b) <= TOKEN_DIFF_CHARS:
            segments.extend(_token_diff(a, b))
        else:
            segments.extend(segment for segment in (["-", a], ["+", b]) if segment[1])
    return _tidy(segments)


# Below this share of unchanged text, a rewritten passage reads better as removed-then-added
# whole than as dozens of word fragments.
WORD_DIFF_MIN_SHARED = 0.5


def _token_diff(a: str, b: str) -> list[list[str]]:
    ta, tb = TOKEN.findall(a), TOKEN.findall(b)
    head = 0
    while head < min(len(ta), len(tb)) and ta[head] == tb[head]:
        head += 1
    tail = 0
    while tail < min(len(ta), len(tb)) - head and ta[len(ta) - 1 - tail] == tb[len(tb) - 1 - tail]:
        tail += 1
    out: list[list[str]] = [["=", "".join(ta[:head])]] if head else []
    middle_a, middle_b = ta[head : len(ta) - tail], tb[head : len(tb) - tail]
    opcodes = difflib.SequenceMatcher(None, middle_a, middle_b, autojunk=False).get_opcodes()
    shared = sum(len("".join(middle_a[i1:i2])) for tag, i1, i2, _, _ in opcodes if tag == "equal")
    longest = max(len("".join(middle_a)), len("".join(middle_b)))
    if longest and shared < WORD_DIFF_MIN_SHARED * longest:
        out.extend(segment for segment in (["-", "".join(middle_a)], ["+", "".join(middle_b)]) if segment[1])
    else:
        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                out.append(["=", "".join(middle_a[i1:i2])])
                continue
            if i2 > i1:
                out.append(["-", "".join(middle_a[i1:i2])])
            if j2 > j1:
                out.append(["+", "".join(middle_b[j1:j2])])
    if tail:
        out.append(["=", "".join(ta[len(ta) - tail :])])
    return out


# Unchanged text this short between two changes reads better as part of one change: "I
# read the cards" → "I decide the design" is one rewrite, not three word swaps.
ISLAND_CHARS = 20


def _tidy(segments: list[list[str]]) -> list[list[str]]:
    """Readable changes: each run of removed and added text becomes one removed block
    followed by one added block, and a short stretch of unchanged text (no line break, not
    longer than the changes on either side) between two changes is folded into them."""
    hunks: list[list[str]] = []  # ["=", text] or ["~", removed, added]
    for op, text in segments:
        if not text:
            continue
        if op == "=":
            if hunks and hunks[-1][0] == "=":
                hunks[-1][1] += text
            else:
                hunks.append(["=", text])
            continue
        if not hunks or hunks[-1][0] != "~":
            hunks.append(["~", "", ""])
        hunks[-1][1 if op == "-" else 2] += text
    folded = True
    while folded:
        folded = False
        out: list[list[str]] = []
        index = 0
        while index < len(hunks):
            item = hunks[index]
            if (
                item[0] == "=" and out and out[-1][0] == "~" and index + 1 < len(hunks) and hunks[index + 1][0] == "~"
                and "\n" not in item[1] and len(item[1]) <= ISLAND_CHARS
                and len(item[1]) <= max(len(out[-1][1]), len(out[-1][2]))
                and len(item[1]) <= max(len(hunks[index + 1][1]), len(hunks[index + 1][2]))
            ):
                before, after = out.pop(), hunks[index + 1]
                out.append(["~", before[1] + item[1] + after[1], before[2] + item[1] + after[2]])
                index += 2
                folded = True
                continue
            out.append(item)
            index += 1
        hunks = out
    tidy: list[list[str]] = []
    for item in hunks:
        if item[0] == "=":
            tidy.append(["=", item[1]])
            continue
        if item[1]:
            tidy.append(["-", item[1]])
        if item[2]:
            tidy.append(["+", item[2]])
    return tidy


def changed(segments: list[list[str]]) -> bool:
    return any(segment[0] != "=" for segment in segments)


# -- alignment ------------------------------------------------------------------------------


def _content(message: dict[str, Any]) -> str:
    """What a step says and does, for telling steps apart."""
    calls = " ".join(tool["name"] + " " + _input_text(tool.get("input"))[:400] for tool in message["tools"])
    return (message.get("thinking") or "") + "\n" + (message.get("text") or "") + "\n" + calls


def _prose(message: dict[str, Any]) -> str:
    return (message.get("thinking") or "") + "\n" + (message.get("text") or "")


# A model turn is "assistant", or "tool" when it calls tools.
MODEL_ROLES = {"assistant", "tool"}


def _kind(message: dict[str, Any]) -> str:
    return "model" if message["role"] in MODEL_ROLES else message["role"]


def align(before: list[dict[str, Any]], after: list[dict[str, Any]], index_map: list[Any] | None = None) -> tuple[list[dict[str, Any]], str]:
    """Rows pairing the rewritten steps with the original ones, in rewritten order.

    Each row is {"after": step | None, "before": [steps]}. Several `before` steps mean the
    rewrite merged them into one (the last is the step it kept); none means the step is new;
    no `after` means the rewrite removed the step. Returns the rows and how they were
    aligned: from the pipeline's message index map, or inferred from the steps.
    """
    partner = _mapped(before, after, index_map) if index_map else None
    if partner is not None:
        return _rows(before, after, partner, certain=True), "index_map"
    return _rows(before, after, _inferred(before, after), certain=False), "inferred"


def _mapped(before: list[dict[str, Any]], after: list[dict[str, Any]], index_map: list[Any]) -> dict[int, dict[str, Any]] | None:
    """Partners from `index_map[j]` = the original source message index of the rewritten
    source message j (steps keep their source position as `line_number`)."""
    by_line = {message.get("line_number"): message for message in before}
    partner: dict[int, dict[str, Any]] = {}
    for message in after:
        line = message.get("line_number")
        if not isinstance(line, int):
            return None
        position = line - 1
        if 0 <= position < len(index_map) and isinstance(index_map[position], int):
            target = by_line.get(index_map[position] + 1)
            if target is not None:
                partner[message["step"]] = target
    return partner


def _inferred(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Partners without an index map: steps that kept a tool call id first (rewrites keep
    call ids), then the steps between those anchors by role and content."""
    owner: dict[str, int] = {}
    for position, message in enumerate(before):
        for tool in message["tools"]:
            if tool.get("id"):
                owner.setdefault(tool["id"], position)
    anchors: list[tuple[int, int]] = []
    for position, message in enumerate(after):
        owners = {owner[tool["id"]] for tool in message["tools"] if tool.get("id") in owner}
        if len(owners) == 1:
            anchors.append((position, owners.pop()))
    if before and after and before[0]["role"] == after[0]["role"] == "system" and all(a != 0 for a, _ in anchors):
        anchors.append((0, 0))
    chain = _increasing(anchors)
    partner = {after[a]["step"]: before[b] for a, b in chain}
    bounds = [(-1, -1), *chain, (len(after), len(before))]
    for (a0, b0), (a1, b1) in zip(bounds, bounds[1:]):
        for a, b in _pair_gap(after[a0 + 1 : a1], before[b0 + 1 : b1]):
            partner[a["step"]] = b
    return partner


def _increasing(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Longest chain of (after, before) positions increasing in both."""
    pairs = sorted(set(pairs))
    tails: list[int] = []
    tail_index: list[int] = []
    previous = [-1] * len(pairs)
    for index, (_, b) in enumerate(pairs):
        k = bisect.bisect_left(tails, b)
        if k == len(tails):
            tails.append(b)
            tail_index.append(index)
        else:
            tails[k] = b
            tail_index[k] = index
        previous[index] = tail_index[k - 1] if k else -1
    chain = []
    index = tail_index[-1] if tail_index else -1
    while index >= 0:
        chain.append(pairs[index])
        index = previous[index]
    return chain[::-1]


def _pair_gap(after: list[dict[str, Any]], before: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not after or not before:
        return []

    def key(message: dict[str, Any]) -> tuple:
        return (_kind(message), " ".join(_content(message).split()))

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    matcher = difflib.SequenceMatcher(None, [key(m) for m in after], [key(m) for m in before], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pairs.extend(zip(after[i1:i2], before[j1:j2]))
        elif tag == "replace":
            pairs.extend(_best_pairs(after[i1:i2], before[j1:j2]))
    return pairs


SIMILAR = 0.5


def _best_pairs(xs: list[dict[str, Any]], ys: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Steps of the same kind paired in order, maximising total similarity."""
    if len(xs) * len(ys) > 2500:
        pairs, j = [], 0
        for x in xs:
            for k in range(j, len(ys)):
                if _kind(x) == _kind(ys[k]) and _similarity(x, ys[k]) >= SIMILAR:
                    pairs.append((x, ys[k]))
                    j = k + 1
                    break
        return pairs
    sim = [[_similarity(x, y) if _kind(x) == _kind(y) else 0.0 for y in ys] for x in xs]
    n, m = len(xs), len(ys)
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            take = sim[i][j] + best[i + 1][j + 1] if sim[i][j] >= SIMILAR else -1.0
            best[i][j] = max(best[i + 1][j], best[i][j + 1], take)
    pairs = []
    i = j = 0
    while i < n and j < m:
        if sim[i][j] >= SIMILAR and best[i][j] == sim[i][j] + best[i + 1][j + 1]:
            pairs.append((xs[i], ys[j]))
            i, j = i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _similarity(x: dict[str, Any], y: dict[str, Any]) -> float:
    a, b = _content(x), _content(y)
    if a == b:
        return 1.0
    ta, tb = TOKEN.findall(a[:8000]), TOKEN.findall(b[:8000])
    if not ta or not tb:
        return 0.0
    matcher = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    if matcher.real_quick_ratio() < SIMILAR or matcher.quick_ratio() < SIMILAR:
        return 0.0
    return matcher.ratio()


def _absorbed(message: dict[str, Any], target: dict[str, Any], kept: dict[str, Any]) -> bool:
    """Whether a removed model step was merged into the next kept one (`target`, rewritten
    from `kept`): its words reappear there, or that step grew by a good part of its text
    (a merge that was also rewritten keeps few of the words)."""
    prose = _prose(message).strip()
    words = [w for w in TOKEN.findall(prose) if not w.isspace()][:3000]
    if not words:
        return True
    into = [w for w in TOKEN.findall(_prose(target)) if not w.isspace()][:8000]
    matched = sum(block.size for block in difflib.SequenceMatcher(None, words, into, autojunk=False).get_matching_blocks())
    grown = len(_prose(target).strip()) - len(_prose(kept).strip())
    return matched >= 0.3 * len(words) or grown >= 0.3 * len(prose)


def _rows(before: list[dict[str, Any]], after: list[dict[str, Any]], partner: dict[int, dict[str, Any]], *, certain: bool) -> list[dict[str, Any]]:
    position = {id(message): index for index, message in enumerate(before)}
    kept = {id(message) for message in partner.values()}
    # Kept original model steps in order, with the rewritten step each became: a removed
    # model step between two of them was merged into the next (its calls were removed).
    anchors = sorted(
        ((position[id(partner[m["step"]])], m) for m in after
         if m["role"] in MODEL_ROLES and m["step"] in partner and partner[m["step"]]["role"] in MODEL_ROLES),
        key=lambda item: item[0],
    )
    anchor_positions = [p for p, _ in anchors]
    merged: dict[int, list[dict[str, Any]]] = {}
    removed: list[dict[str, Any]] = []
    for index, message in enumerate(before):
        if id(message) in kept:
            continue
        target = None
        if message["role"] in MODEL_ROLES:
            k = bisect.bisect_right(anchor_positions, index)
            if k < len(anchors) and (certain or _absorbed(message, anchors[k][1], partner[anchors[k][1]["step"]])):
                target = anchors[k][1]
        if target is not None:
            merged.setdefault(target["step"], []).append(message)
        else:
            removed.append(message)
    rows: list[dict[str, Any]] = []
    cursor = 0
    for message in after:
        match = partner.get(message["step"])
        if match is None:
            rows.append({"after": message, "before": []})
            continue
        while cursor < len(removed) and position[id(removed[cursor])] < position[id(match)]:
            rows.append({"after": None, "before": [removed[cursor]]})
            cursor += 1
        rows.append({"after": message, "before": merged.get(message["step"], []) + [match]})
    rows.extend({"after": None, "before": [message]} for message in removed[cursor:])
    return rows


# -- one row: fields and tool calls ---------------------------------------------------------


def _input_text(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _result_text(tool: dict[str, Any]) -> str:
    return (tool.get("result") or {}).get("text") or ""


def _joined(messages: list[dict[str, Any]], key: str) -> str:
    """A field of one step, or of several merged steps joined by blank lines (non-empty
    parts only), which is what a merge produces."""
    values = [message.get(key) or "" for message in messages]
    if len(values) == 1:
        return values[0]
    return "\n\n".join(value for value in values if value.strip())


def _clip(text: str, limit: int = REMOVED_TOOL_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… （还有 {len(text) - limit} 字）"


def _tool_changes(before: list[dict[str, Any]], after: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Calls by id: removed (with what they did), added, changed (input / result diff)."""
    old_tools = [tool for message in before for tool in message["tools"]]
    new_tools = list(after["tools"]) if after else []
    new_ids = {tool["id"] for tool in new_tools if tool.get("id")}
    old_by_id = {tool["id"]: tool for tool in old_tools if tool.get("id")}
    # Calls without ids (rare formats) pair up by position and name.
    unnamed_old = [tool for tool in old_tools if not tool.get("id")]
    out: list[dict[str, Any]] = []
    for tool in old_tools:
        if tool.get("id") and tool["id"] not in new_ids:
            out.append({
                "id": tool["id"], "name": tool["name"], "change": "removed", "from_step": tool["step"],
                "input": _clip(_input_text(tool.get("input"))), "result": _clip(_result_text(tool)),
            })
    for tool in new_tools:
        old = old_by_id.get(tool["id"]) if tool.get("id") else (unnamed_old.pop(0) if unnamed_old and unnamed_old[0]["name"] == tool["name"] else None)
        entry: dict[str, Any] = {"id": tool.get("id") or "", "name": tool["name"], "change": "same", "preview": _input_text(tool.get("input"))[:160]}
        if old is None:
            entry.update(change="added", input=_clip(_input_text(tool.get("input"))), result=_clip(_result_text(tool)))
        else:
            for key, a, b in (("input", _input_text(old.get("input")), _input_text(tool.get("input"))), ("result", _result_text(old), _result_text(tool))):
                if a != b:
                    entry["change"] = "changed"
                    entry[key] = word_diff(a, b) if len(a) + len(b) <= TOOL_DIFF_CHARS else [["-", _clip(a, 4000)], ["+", _clip(b, 4000)]]
        out.append(entry)
    return out


def _preview(message: dict[str, Any]) -> str:
    text = " ".join(((message.get("text") or "").strip() or (message.get("thinking") or "").strip()).split())
    calls = " · ".join(tool["name"] for tool in message["tools"])
    return (text[:140] + ("…" if len(text) > 140 else "")) + ((" [" + calls + "]") if calls else "")


def _step_ref(message: dict[str, Any] | None) -> dict[str, Any] | None:
    if message is None:
        return None
    return {"step": message["step"], "role": message["role"], "line": message.get("line_number"), "layer": message.get("layer")}


# -- harness delta and residue --------------------------------------------------------------


class _Declared:
    """A run's harness as given to the agent: declared tools, not every tool the transcript
    happens to call (a call the rewrite left behind must not make a removed tool look kept).
    Skill and subagent calls stay: those components are only known from their use."""

    def __init__(self, run: Any) -> None:
        declared = [item for item in run.tool_catalog if item.get("available_in_session")]
        keep = harness.SKILL_TOOLS | harness.SPAWN_TOOLS
        self.meta, self.messages, self.task = run.meta, run.messages, run.task
        self.model_prompt = getattr(run, "model_prompt", None)
        self.tool_catalog = declared or run.tool_catalog
        self.tools = [t for t in run.tools if t["name"].split("__")[-1].casefold() in keep] if declared else run.tools


def harness_delta(before_run: Any, after_run: Any) -> dict[str, Any]:
    """What the rewrite changed in the harness: components by the four layers (removed,
    added, kept), system prompt sections and tool declarations. The original side counts
    everything its trajectory used; the rewritten side only what its harness provides."""
    found_before, found_after = harness.inventory(before_run), harness.inventory(_Declared(after_run))

    def key(component: dict[str, Any]) -> tuple[str, str]:
        return (component["kind"], component["name"])

    old = {key(c): c for c in found_before["components"]}
    new = {key(c): c for c in found_after["components"]}
    base = found_after["base"] or found_before["base"]
    return {
        "base": base,
        "removed": [c for k, c in old.items() if k not in new],
        "added": [c for k, c in new.items() if k not in old],
        "kept": [c for k, c in new.items() if k in old],
        "sections": _section_changes(before_run, after_run, base),
        "tools": _declaration_changes(before_run, after_run, base),
    }


def _section_changes(before_run: Any, after_run: Any, base: str | None) -> list[dict[str, Any]]:
    old = [(heading, body) for heading, body in harness.system_sections(before_run) if heading or body.strip()]
    new = [(heading, body) for heading, body in harness.system_sections(after_run) if heading or body.strip()]
    old_map, new_map = dict(old), dict(new)
    rows = []
    for heading in dict.fromkeys([h for h, _ in old] + [h for h, _ in new]):
        a, b = old_map.get(heading), new_map.get(heading)
        change = "removed" if b is None else "added" if a is None else "same" if a == b else "changed"
        rows.append({
            "heading": heading,
            "stock": harness.stock_section(base, heading),
            "change": change,
            "before_chars": len(a or ""),
            "after_chars": len(b or ""),
            "diff": None if change == "same" else word_diff(a or "", b or ""),
        })
    return rows


def _declaration_changes(before_run: Any, after_run: Any, base: str | None) -> list[dict[str, Any]]:
    native = harness.NATIVE_TOOLS.get(base or "", set())

    def declared(run: Any) -> dict[str, dict[str, str] | None]:
        specs = (run.meta or {}).get("tool_specs") or {}
        return {item["name"]: specs.get(item["name"]) for item in run.tool_catalog if item.get("available_in_session")}

    old, new = declared(before_run), declared(after_run)
    rows = []
    for name in dict.fromkeys([*old, *new]):
        if name not in new:
            change = "removed"
        elif name not in old:
            change = "added"
        elif (old[name] or {}).get("sha") != (new[name] or {}).get("sha"):
            change = "redefined"
        else:
            change = "same"
        row: dict[str, Any] = {"name": name, "change": change, "native": name.split("__")[-1].casefold() in native}
        if change == "redefined" and old[name] and new[name]:
            row["diff"] = word_diff(old[name]["description"], new[name]["description"])
        rows.append(row)
    return rows


def residue(after_run: Any, delta: dict[str, Any]) -> list[dict[str, Any]]:
    """Components the rewrite removed from the harness that the rewritten trajectory's own
    turns still call, pass in arguments, name, or whose products they use."""
    if not delta["removed"]:
        return []
    # Card-like files are also caught by their stem: a rewrite that drops the read often
    # keeps naming the card.
    traced = harness.trace(after_run, {"base": delta["base"], "components": delta["removed"]}, stems=True)
    terms = harness.component_terms(delta["removed"], stems=True)
    items = []
    for component in traced["components"]:
        uses = {how: component[how] for how in harness.USES if component.get(how)}
        if not uses:
            continue
        name = f"{component['kind']}:{component['name']}"
        items.append({
            "key": "r:" + name,
            "kind": component["kind"],
            "name": component["name"],
            "uses": uses,
            "terms": terms.get(name, []),
            "steps": sorted({step for steps in uses.values() for step in steps}),
        })
    return items


# -- summary sync across context segments --------------------------------------------------

SUMMARY = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)


def compaction_pair(upstream: Any, downstream: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The summary a compaction wrote at the end of one segment and the message that opens
    the next segment with it (Claude Code's compaction), or None."""
    up = None
    for index, message in enumerate(upstream.messages):
        if (message.get("text") or "").lstrip().startswith(COMPACTION_REQUEST_PREFIX):
            up = next((m for m in upstream.messages[index + 1 :] if m["role"] in MODEL_ROLES), up)
    down = next((m for m in downstream.messages if (m.get("text") or "").lstrip().startswith(COMPACTION_PREFIX)), None)
    return (up, down) if up is not None and down is not None else None


def _squash(text: str) -> str:
    return " ".join(text.split())


def _summary_body(text: str) -> str:
    match = SUMMARY.search(text)
    return _squash(match.group(1) if match else text)


def _nearly(a: str, b: str) -> bool:
    ta, tb = TOKEN.findall(a), TOKEN.findall(b)
    if not ta or not tb:
        return False
    matcher = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    return matcher.real_quick_ratio() >= 0.99 and matcher.quick_ratio() >= 0.99 and matcher.ratio() >= 0.99


def summary_sync(original: tuple[Any, Any], rewritten: tuple[Any, Any]) -> dict[str, Any] | None:
    """For two consecutive segments: whether the original continuation carried the
    compaction summary and, if so, whether the rewritten continuation carries the rewritten
    summary in the same place (the wrapper text around it unchanged)."""
    before, after = compaction_pair(*original), compaction_pair(*rewritten)
    if before is None or after is None:
        return None
    body, continuation = _summary_body(before[0].get("text") or ""), _squash(before[1].get("text") or "")
    new_body, new_continuation = _summary_body(after[0].get("text") or ""), _squash(after[1].get("text") or "")
    at = continuation.find(body) if body else -1
    if at >= 0:
        # The continuation is wrapper + summary + wrapper: rebuild it with the new summary.
        paired = True
        expected = continuation[:at] + new_body + continuation[at + len(body) :]
        ok = new_continuation == expected or _nearly(expected, new_continuation)
    else:
        # Some continuations differ slightly (template substitutions): compare what follows
        # the "Summary:" line on both sides.
        tail = continuation.split("Summary:", 1)[-1][: len(body) + 400]
        paired = _nearly(body, tail)
        new_tail = new_continuation.split("Summary:", 1)[-1][: len(new_body) + 400]
        ok = _nearly(new_body, new_tail)
    return {"paired": paired, "ok": ok if paired else None, "up_step": after[0]["step"], "down_step": after[1]["step"]}


# -- changes and annotations ----------------------------------------------------------------


def _locate(text: str, needle: str) -> tuple[int, int] | None:
    if not needle:
        return None
    index = text.find(needle)
    if index >= 0:
        return (index, index + len(needle))
    words = needle.split()
    if not words or len(words) > 2000:
        return None
    found = re.search(r"\s+".join(re.escape(word) for word in words), text)
    return (found.start(), found.end()) if found else None


def _hunks(segments: list[list[Any]]) -> list[dict[str, Any]]:
    """Runs of changed segments with the character ranges they cover in the old and new text."""
    hunks: list[dict[str, Any]] = []
    a = b = 0
    current: dict[str, Any] | None = None
    for index, (op, text, *_) in enumerate(segments):
        if op == "=":
            if current:
                hunks.append(current)
                current = None
            a += len(text)
            b += len(text)
            continue
        if current is None:
            current = {"segments": [], "a": [a, a], "b": [b, b], "removed": "", "added": ""}
        current["segments"].append(index)
        if op == "-":
            a += len(text)
            current["a"][1] = a
            current["removed"] += text
        else:
            b += len(text)
            current["b"][1] = b
            current["added"] += text
    if current:
        hunks.append(current)
    return hunks


def _overlaps(span: tuple[int, int] | None, other: list[int]) -> bool:
    return span is not None and span[0] <= other[1] and other[0] <= span[1]


def warning_types(change: dict[str, Any]) -> set[str]:
    return {str(item).split(":", 1)[0].strip() for edit in change.get("annotations") or [] for item in edit.get("warnings") or []}


def _digest(*parts: str) -> str:
    return hashlib.sha1("\u0000".join(parts).encode("utf-8")).hexdigest()[:10]


def review(before_run: Any | None, after_run: Any, annotation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rows, changes, harness delta and residue for one record (verdicts are applied by the
    caller, which lets this part be cached)."""
    annotation = annotation or {}
    before_messages = before_run.messages if before_run is not None else []
    rows, aligned_by = align(before_messages, after_run.messages, annotation.get("index_map"))
    delta = harness_delta(before_run, after_run) if before_run is not None else None
    leftovers = residue(after_run, delta) if delta else []
    residue_by_step: dict[int, list[str]] = {}
    for item in leftovers:
        for step in item["steps"]:
            residue_by_step.setdefault(step, []).append(item["key"])

    edits_by_row: dict[tuple[int, str], list[dict[str, Any]]] = {}
    targets_by_step: dict[int, list[dict[str, Any]]] = {}
    by_line = {m.get("line_number"): m["step"] for m in after_run.messages}

    def step_of(item: dict[str, Any]) -> int | None:
        if isinstance(item.get("step"), int):
            return item["step"]
        if isinstance(item.get("message"), int):
            return by_line.get(item["message"] + 1)
        return None

    for edit in annotation.get("changes") or []:
        step = step_of(edit)
        if step is not None:
            edits_by_row.setdefault((step, edit.get("field") or "text"), []).append(edit)
    for target in annotation.get("targets") or []:
        step = step_of(target)
        if step is not None:
            targets_by_step.setdefault(step, []).append(target)
    placed: set[str] = set()

    first_system = next((m["step"] for m in after_run.messages if m["role"] == "system"), None)
    out_rows: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        after, before = row["after"], row["before"]
        entry: dict[str, Any] = {"after": _step_ref(after), "before": [_step_ref(m) for m in before], "changes": []}
        if after is not None and after["step"] == first_system and before and before[-1]["role"] == "system":
            entry.update(kind="system_prompt" if (after.get("text") or "") != (before[-1].get("text") or "") else "same", system_prompt=True)
            out_rows.append(entry)
            continue
        texts = {}
        for key in ("thinking", "text"):
            old = _joined(before, key) if before else ""
            new = (after.get(key) or "") if after else ""
            if old or new:
                texts[key] = (old, new)
        tools = _tool_changes(before, after)
        if after is None:
            kind = "removed"
        elif not before:
            kind = "added"
        elif len(before) > 1:
            kind = "merged"
        elif any(old != new for old, new in texts.values()) or any(tool["change"] != "same" for tool in tools):
            kind = "changed"
        else:
            kind = "same"
        entry["kind"] = kind
        if after is not None and after["step"] in residue_by_step:
            entry["residue"] = residue_by_step[after["step"]]
        # What the pipeline reviewed here, including spans it chose to keep.
        if after is not None and after["step"] in targets_by_step:
            entry["targets"] = targets_by_step[after["step"]]
        if kind == "same":
            entry["preview"] = _preview(after)
            entry["tools"] = [{"id": tool["id"], "name": tool["name"]} for tool in tools]
            if entry.get("residue"):
                # An unchanged step that still uses a removed component is where a missed
                # rewrite hides: send its text so the page can show it in full.
                entry["fields"] = {key: [["=", new]] for key, (_, new) in texts.items() if new}
                entry["tools"] = tools
            out_rows.append(entry)
            continue
        if kind in {"removed", "added"}:
            ref = f"s:{'-' if kind == 'removed' else '+'}:{(before[0] if before else after)['step']}"
            changes.append({"id": ref, "row": index, "kind": "step", "change": kind})
            entry["changes"].append(ref)
            entry["fields"] = {key: [[op, text, ref] for op, text in word_diff(old, new)] for key, (old, new) in texts.items()}
        else:
            entry["fields"] = {
                key: _field_changes(index, after["step"], key, old, new, edits_by_row.get((after["step"], key), []), changes, entry, placed)
                for key, (old, new) in texts.items()
            }
        entry["tools"] = tools
        for tool in tools:
            if tool["change"] != "same":
                ref = f"t:{tool['id'] or tool['name']}:{tool['change']}"
                tool["change_id"] = ref
                changes.append({"id": ref, "row": index, "kind": "tool", "change": tool["change"], "tool": tool["name"]})
                entry["changes"].append(ref)
        out_rows.append(entry)

    # Pipeline edits that name a step the diff could not place (text not found).
    unplaced = [
        {**edit, "step": step_of(edit)}
        for edit in annotation.get("changes") or []
        if edit.get("key") not in placed
    ]
    return {
        "aligned_by": aligned_by,
        "rows": out_rows,
        "changes": changes,
        "unplaced": unplaced,
        "harness": delta,
        "residue": leftovers,
    }


def _edit_segments(old_text: str, new_text: str, edits: list[dict[str, Any]]) -> list[list[str]] | None:
    """The diff built edit by edit, when applying the pipeline's edits to the original text
    gives exactly the rewritten text: then every change belongs to one edit."""
    located = []
    for edit in edits:
        span = _locate(old_text, edit.get("old") or "")
        if span is None:
            return None
        located.append((span, edit))
    located.sort(key=lambda item: item[0])
    if any(second[0] < first[1] for (first, _), (second, _) in zip(located, located[1:])):
        return None
    rebuilt, cursor = [], 0
    for (start, end), edit in located:
        rebuilt += [old_text[cursor:start], edit.get("new") or ""]
        cursor = end
    if "".join(rebuilt) + old_text[cursor:] != new_text:
        return None
    segments: list[list[str]] = []
    cursor = 0
    for (start, end), edit in located:
        if start > cursor:
            segments.append(["=", old_text[cursor:start]])
        for op, text in word_diff(old_text[start:end], edit.get("new") or ""):
            segments.append([op, text] if op == "=" else [op, text, "e:" + str(edit["key"])])
        cursor = end
    if cursor < len(old_text):
        segments.append(["=", old_text[cursor:]])
    return segments


def _field_changes(
    row: int,
    step: int,
    field: str,
    old_text: str,
    new_text: str,
    edits: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    entry: dict[str, Any],
    placed: set[str],
) -> list[list[Any]]:
    """The field's diff with a change id on every changed segment: the pipeline edit it
    belongs to, or one change per field for what the pipeline did not record."""
    owners: dict[str, list[dict[str, Any]]] = {}
    segments = _edit_segments(old_text, new_text, edits) if edits else None
    if segments is not None:
        owners = {"e:" + str(edit["key"]): [edit] for edit in edits}
    else:
        # Rebuilding failed (the text changed in ways the edits do not explain): diff the
        # texts and give each changed run to the edits whose text it touches.
        segments = [list(segment) for segment in word_diff(old_text, new_text)]
        located = [(edit, _locate(old_text, edit.get("old") or ""), _locate(new_text, edit.get("new") or "")) for edit in edits]
        for hunk in _hunks(segments):
            touching = [edit for edit, a, b in located if _overlaps(a, hunk["a"]) or _overlaps(b, hunk["b"])]
            if touching:
                ref = "e:" + "+".join(str(edit["key"]) for edit in touching)
                owners[ref] = touching
                for index in hunk["segments"]:
                    segments[index].append(ref)
    loose = [segment for segment in segments if segment[0] != "=" and len(segment) == 2]
    if loose:
        ref = f"h:{step}:{field}:{_digest(*(segment[0] + segment[1] for segment in loose))}"
        owners[ref] = []
        for segment in loose:
            segment.append(ref)
    by_id: dict[str, dict[str, Any]] = {}
    last = None
    for segment in segments:
        ref = segment[2] if len(segment) > 2 else None
        if ref is None:
            last = None
            continue
        change = by_id.get(ref)
        if change is None:
            change = {"id": ref, "row": row, "kind": "text", "field": field, "removed": "", "added": "", "annotations": owners[ref]}
            by_id[ref] = change
            changes.append(change)
            entry["changes"].append(ref)
            placed.update(str(edit["key"]) for edit in owners[ref])
        side = "removed" if segment[0] == "-" else "added"
        # Several runs of one change are joined with a gap mark.
        if change[side] and last != ref:
            change[side] += " … "
        change[side] += segment[1]
        last = ref
    return segments


# -- verdicts and the training gate ---------------------------------------------------------

VERDICTS = {
    "change": {"correct", "error", "uncertain"},
    "residue": {"confirm", "ignore"},
    "miss": {"miss"},
    "record": {"include", "exclude"},
}
ERROR_TYPES = {
    "semantic_loss": "语义丢失",
    "invented": "新编内容",
    "residual_attribution": "归因残留",
    "overreach": "改过头",
    "incoherent": "不通顺",
    "other": "其他",
}
MISS_TYPES = {"attribution": "文字里的归因残留", "tool_args": "工具参数里的依赖", "other": "其他"}
DONE_STATUSES = {"rewritten", "done", "ok", "success", "passed"}
# Warning types that hold a record for review until their change is judged.
GATE_WARNINGS = ("drops_negation", "adds_status_words")


def gate(
    *,
    status: dict[str, Any] | None,
    changes: list[dict[str, Any]],
    residue_items: list[dict[str, Any]],
    syncs: list[dict[str, Any]],
    verdicts: dict[str, dict[str, Any]],
    warnings: tuple[str, ...] | list[str] = GATE_WARNINGS,
) -> dict[str, Any]:
    """Include / review / exclude for training, with the reasons in order."""
    reasons: list[dict[str, str]] = []

    def add(level: str, text: str) -> None:
        reasons.append({"level": level, "text": text})

    value = (status or {}).get("value")
    if value and value not in DONE_STATUSES:
        add("exclude", "流水线状态：" + value + ("（" + status["reason"] + "）" if status.get("reason") else ""))
    errors = [c for c in changes if (verdicts.get(c["id"]) or {}).get("verdict") == "error"]
    if errors:
        add("exclude", f"{len(errors)} 处改动判错")
    misses = [v for v in verdicts.values() if v.get("kind") == "miss"]
    if misses:
        add("exclude", f"{len(misses)} 处漏改")
    confirmed = [r for r in residue_items if (verdicts.get(r["key"]) or {}).get("verdict") == "confirm"]
    if confirmed:
        add("exclude", f"{len(confirmed)} 个残留确认是漏改")
    open_residue = [r for r in residue_items if r["key"] not in verdicts]
    if open_residue:
        add("review", f"{len(open_residue)} 个残留还没处理")
    if any(sync.get("ok") is False for sync in syncs):
        add("review", "压缩摘要和相邻段落不同步")
    wanted = set(warnings)
    flagged = [c for c in changes if c["id"] not in verdicts and warning_types(c) & wanted]
    if flagged:
        add("review", f"{len(flagged)} 处带警告（{'、'.join(sorted(wanted))}）的改动还没判定")
    decision = "exclude" if any(r["level"] == "exclude" for r in reasons) else "review" if reasons else "include"
    override = verdicts.get("record")
    if override and override.get("verdict") in {"include", "exclude"}:
        decision = override["verdict"]
        reasons.insert(0, {"level": "override", "text": "人工标为" + ("可进" if decision == "include" else "不进") + ("：" + override["note"] if override.get("note") else "")})
    return {"decision": decision, "reasons": reasons}
