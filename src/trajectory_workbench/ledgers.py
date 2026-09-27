"""Ledgers for long runs: what was asked, delegated, planned and reminded, and what became
of each item.

Code finds the items (requirement sentences, Agent / spawn calls, todo and plan updates,
hook reminders, compaction summaries); Jev answers narrow questions about each one:

* requirements – which task sentences are real requirements; which step works on each;
  whether the harness instructions conflict with it; whether each compaction summary
  still carries it.
* delegations – whether the parent acts on what a subagent reported.
* hook reminders – whether the next step responds to the reminder.

Plans and todos are pure bookkeeping and need no model.
"""

from __future__ import annotations

import re
from typing import Any

from typesafe_sdk import Choice, Noul, NoulCriteria

from trajectory_workbench.adapters.common import COMPACTION_PREFIX, split_task
from trajectory_workbench.jev import DATA_NOTE, clip, step_summaries
from trajectory_workbench.jev import compact_input as compact


LEDGER_VERSION = "ledgers-v1"
MAX_REQUIREMENTS = 30
WINDOW = 60

# -- requirements ---------------------------------------------------------------------

REQUIREMENT_HINT = re.compile(
    r"\b(must|should|needs? to|required?|ensure|make sure|do not|don't|never|always|only|"
    r"at least|at most|exactly|no more than|keep|include|end with|use)\b|"
    r"必须|需要|要求|不要|不得|禁止|务必|确保|至少|最多|只能|应当|应该|保留|包含",
    re.IGNORECASE,
)
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)、])\s+")
FILE_LINE = re.compile(r"^\s*(?:[-*•]\s*)?`?\s*[\w./-]+\.\w{1,5}\s*`?\s*$")
SENTENCE_END = re.compile(r"(?<=[.!?。！？；;])\s+|(?<=[。！？；])")
def requirement_candidates(task: str, limit: int = MAX_REQUIREMENTS) -> list[str]:
    """Sentences and bullet items of the task that may state a requirement.

    Everything is a candidate (Jev decides which are requirements); when there are too
    many, sentences with requirement wording and bullets are kept first.
    """
    candidates: list[tuple[int, str]] = []
    for line in task.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or FILE_LINE.match(stripped):
            continue
        if BULLET.match(stripped):
            candidates.append((0, BULLET.sub("", stripped)))
            continue
        for sentence in SENTENCE_END.split(stripped):
            sentence = sentence.strip()
            if len(sentence) >= 12:
                candidates.append((0 if REQUIREMENT_HINT.search(sentence) else 1, sentence))
    seen: set[str] = set()
    unique = []
    for rank, sentence in candidates:
        key = sentence.casefold()
        if key not in seen:
            seen.add(key)
            unique.append((rank, sentence[:300]))
    if len(unique) > limit:
        keep = {id(item) for item in sorted(unique, key=lambda item: item[0])[:limit]}
        unique = [item for item in unique if id(item) in keep]
    return [sentence for _, sentence in unique]


def harness_text(run: Any, wrapped: str) -> str:
    """Harness guidance the agent saw: JSON-wrapped rules, system prompt, instruction and
    hook events."""
    parts = [wrapped] if wrapped else []
    for message in run.messages:
        if message["role"] == "system" and (message.get("layer") in {"instruction", "hook"} or message["step"] <= 2):
            text = message.get("text") or ""
            if text and not text.startswith(COMPACTION_PREFIX):
                parts.append(text)
    return clip("\n\n".join(parts), 60_000)


# -- compactions ------------------------------------------------------------------------


def compactions(run: Any) -> list[dict[str, Any]]:
    """Compaction points with the summary the agent continued from, if the source has it."""
    found = []
    messages = run.messages
    for index, message in enumerate(messages):
        text = message.get("text") or ""
        marker = message.get("compaction")
        if not marker and not text.startswith(COMPACTION_PREFIX):
            continue
        summary = text if text.startswith(COMPACTION_PREFIX) else ""
        if marker and not summary:
            following = next(
                (m for m in messages[index + 1 : index + 6] if (m.get("text") or "").startswith(COMPACTION_PREFIX)),
                None,
            )
            summary = following["text"] if following else ""
        if found and summary and found[-1]["summary"] == summary:
            continue  # the marker and the summary message describe the same compaction
        found.append(
            {
                "step": message["step"],
                "trigger": (marker or {}).get("trigger") or ("segment start" if not marker else None),
                "pre_tokens": (marker or {}).get("pre_tokens"),
                "post_tokens": (marker or {}).get("post_tokens"),
                "summary": summary,
                "summary_chars": len(summary),
            }
        )
    return found


# -- plans and todos ----------------------------------------------------------------------

TASK_CREATED = re.compile(r"Task #(\d+) created")


def plan_ledger(run: Any) -> list[dict[str, Any]]:
    """Every todo / plan item with its status history.

    Understands Claude Code TodoWrite (full list each call), TaskCreate / TaskUpdate (one
    item per call) and Codex update_plan (full list each call).
    """
    items: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def touch(key: str, text: str, status: str | None, step: int) -> None:
        item = items.get(key)
        if item is None:
            item = items[key] = {"text": text, "created_step": step, "history": [], "status": None}
            order.append(key)
        if status and status != item["status"]:
            item["history"].append({"step": step, "status": status})
            item["status"] = status

    for tool in run.tools:
        name = tool["name"].split("__")[-1]
        data = tool.get("input") if isinstance(tool.get("input"), dict) else {}
        step = tool["step"]
        if name in {"TodoWrite", "todo_write"} and isinstance(data.get("todos"), list):
            listed = set()
            for todo in data["todos"]:
                if isinstance(todo, dict) and todo.get("content"):
                    key = "todo:" + str(todo["content"])
                    listed.add(key)
                    touch(key, str(todo["content"]), todo.get("status"), step)
            for key in order:
                if key.startswith("todo:") and key not in listed and items[key]["status"] != "removed":
                    touch(key, items[key]["text"], "removed", step)
        elif name == "update_plan" and isinstance(data.get("plan"), list):
            for entry in data["plan"]:
                if isinstance(entry, dict) and entry.get("step"):
                    touch("plan:" + str(entry["step"]), str(entry["step"]), entry.get("status"), step)
        elif name == "TaskCreate":
            created = TASK_CREATED.search((tool.get("result") or {}).get("text") or "")
            key = "task:" + (created.group(1) if created else f"s{step}")
            touch(key, str(data.get("subject") or data.get("description") or "")[:300], "pending", step)
        elif name == "TaskUpdate" and data.get("taskId") is not None:
            key = "task:" + str(data["taskId"])
            if key in items or data.get("subject"):
                touch(key, str(data.get("subject") or items.get(key, {}).get("text", "")), data.get("status"), step)
    ledger = []
    for key in order:
        item = items[key]
        done = next((h["step"] for h in item["history"] if h["status"] == "completed"), None)
        ledger.append({**item, "kind": key.split(":", 1)[0], "completed_step": done})
    return ledger


# -- delegations ----------------------------------------------------------------------------

DELEGATION_TOOLS = {"Agent", "Task", "spawn_agent"}
# Claude Code appends "agentId: …" to every Agent result; only this line means the
# report will arrive later.
ASYNC_LAUNCH = re.compile(r"Async agent launched[\s\S]{0,400}?agentId:\s*(\w+)|Async agent launched")
ENCRYPTED = re.compile(r"^gAAAA[\w-]{20,}")


def delegations(run: Any) -> list[dict[str, Any]]:
    """Calls that hand work to a subagent: the brief, the returned report, and the step
    where the parent first sees that report."""
    found = []
    for tool in run.tools:
        if tool["name"].split("__")[-1] not in DELEGATION_TOOLS and not tool.get("spawned"):
            continue
        data = tool.get("input") if isinstance(tool.get("input"), dict) else {}
        brief = str(data.get("prompt") or data.get("message") or "")
        if ENCRYPTED.match(brief):
            brief = ""
        result = (tool.get("result") or {}).get("text") or ""
        result_step = tool["step"]
        launched = ASYNC_LAUNCH.search(result)
        if launched or result.lstrip().startswith('{"task_name"'):
            # Asynchronous: the report arrives later as a notification or agent message.
            # Only identifiers (agent id, task name) find the report; a free-text
            # description could match an unrelated later message.
            named = re.search(r'"task_name"\s*:\s*"([^"]+)"', result)
            handle = (launched.group(1) if launched and launched.group(1) else None) or (named.group(1) if named else None) or str(data.get("task_name") or "")
            later = next(
                (
                    m for m in run.messages
                    if m["step"] > tool["step"] and m["role"] in {"system", "user"} and handle and handle in (m.get("text") or "")
                ),
                None,
            )
            result = later["text"] if later else ""
            result_step = later["step"] if later else None
        found.append(
            {
                "step": tool["step"],
                "call_id": tool["id"],
                "tool": tool["name"],
                "subagent_type": data.get("subagent_type") or data.get("agent_type"),
                "description": data.get("description") or data.get("task_name"),
                "brief": brief,
                "brief_encrypted": bool(ENCRYPTED.match(str(data.get("prompt") or data.get("message") or ""))),
                "result": result,
                "result_step": result_step,
                "spawned": tool.get("spawned"),
                "_next": _next_agent_steps(run, result_step) if result_step else [],
            }
        )
    return found


def _next_agent_steps(run: Any, after: int, count: int = 2) -> list[dict[str, Any]]:
    """The parent's first agent steps after it could see a report (a synchronous call's
    own step reasoned before the result arrived, so it is excluded)."""
    picked = []
    for message in run.messages:
        if message["step"] > after and message["role"] in {"assistant", "tool"}:
            picked.append(message)
            if len(picked) >= count:
                break
    return picked


# -- hook reminders ---------------------------------------------------------------------------


def hook_events(run: Any) -> list[dict[str, Any]]:
    events = []
    for index, message in enumerate(run.messages):
        if message.get("layer") != "hook" or len(message.get("text") or "") < 30:
            continue
        following = next((m for m in run.messages[index + 1 :] if m["role"] in {"assistant", "tool"}), None)
        events.append({"step": message["step"], "text": message["text"], "_next": following})
    return events


# -- Jev ----------------------------------------------------------------------------------------

ADOPTION = {
    "acts_on": "Acts on the report: changes, fixes or decisions in the next steps follow from what it reported",
    "acknowledges": "Mentions the report but does not change course or act on it",
    "rejects": "Explicitly disagrees with or sets aside the report, giving a reason",
    "ignores": "Carries on with no sign of having read the report",
    "unclear": "The next steps give too little to tell",
}
HOOK_RESPONSE = {
    "acts": "Does what the reminder asks, or changes course because of it",
    "acknowledges": "Mentions the reminder without acting on it",
    "ignores": "Shows no sign of the reminder",
    "nothing_asked": "The reminder only informs and asks for nothing specific",
}


def _step_view(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "reasoning": clip(message.get("thinking"), 1500),
        "message": clip(message.get("text"), 800),
        "tool_calls": [{"tool": t["name"], "input": compact(t.get("input"), 400)} for t in message["tools"][:4]],
    }


def build_ledgers(run: Any, instruction: str | None, analyzer: Any | None) -> dict[str, Any]:
    """All ledgers for one trajectory. Jev parts are skipped when `analyzer` is None."""
    task, wrapped_harness = split_task(instruction or (run.task or {}).get("instruction"))
    candidates = requirement_candidates(task)
    found_compactions = compactions(run)
    found_delegations = delegations(run)
    found_hooks = hook_events(run)
    result: dict[str, Any] = {
        "version": LEDGER_VERSION,
        "task_is_wrapped": bool(wrapped_harness),
        "requirements": [{"text": text} for text in candidates],
        "plans": plan_ledger(run),
        "compactions": [{k: v for k, v in item.items() if k != "summary"} for item in found_compactions],
        "delegations": [_public(item) for item in found_delegations],
        "hooks": [_public(item) for item in found_hooks],
        "jev": False,
        "input_tokens": 0,
    }
    if analyzer is None or not analyzer.available():
        return result

    tokens = 0
    # Phase 1: which candidates are requirements.
    requirements: list[str] = []
    if candidates:
        state = {"about": DATA_NOTE, "task": clip(task, 12_000), "candidates": {f"c{i}": text for i, text in enumerate(candidates)}}
        questions = {
            f"c{i}": Noul(
                instructions=f"Is `candidates.c{i}` a requirement or constraint that the deliverable of `task` must satisfy?",
                criteria=NoulCriteria(
                    true="It states something the result must include, do, look like or avoid",
                    false="It is background, motivation, a file listing, or freedom the author grants",
                ),
            )
            for i in range(len(candidates))
        }
        [response] = analyzer.ask([(state, questions)])
        if isinstance(response, Exception):
            # Without knowing which sentences are requirements nothing else can be asked.
            result["error"] = f"Jev request failed: {response}"[:300]
            return result
        tokens += response.usage.input_tokens
        for i, text in enumerate(candidates):
            p = round(response.answers[f"c{i}"].noul, 3)
            result["requirements"][i]["p_requirement"] = p
            if p >= 0.5:
                requirements.append(text)
    kept = [item for item in result["requirements"] if item.get("p_requirement", 0) >= 0.5]
    result["requirements"] = kept

    # Phase 2: independent questions, sent together.
    requests: list[tuple[str, Any, dict[str, Any], dict[str, Any]]] = []
    labels = {f"r{i}": text for i, text in enumerate(requirements)}
    if requirements:
        summaries = step_summaries(run)
        ids = list(summaries)
        for start in range(0, len(ids), WINDOW):
            chunk = ids[start : start + WINDOW]
            state = {"about": DATA_NOTE, "requirements": labels, "steps": {key: summaries[key] for key in chunk}}
            questions = {}
            for key in labels:
                criteria = {step: None for step in chunk}
                criteria["none"] = f"No entry in `steps` works on or checks `requirements.{key}`"
                questions[key] = Choice(
                    instructions=f"Which entry in `steps` most directly works on or checks `requirements.{key}`?",
                    criteria=criteria,
                )
            requests.append(("coverage", start, state, questions))
        harness = harness_text(run, wrapped_harness)
        if harness:
            state = {"about": DATA_NOTE, "requirements": labels, "harness_instructions": harness}
            questions = {
                key: Noul(
                    instructions=f"Do `harness_instructions` demand or forbid something that conflicts with `requirements.{key}` from the user's task?",
                    criteria=NoulCriteria(
                        true="Following the harness instructions would make it hard or impossible to satisfy this requirement",
                        false="The harness instructions are compatible with it or say nothing about it",
                    ),
                )
                for key in labels
            }
            requests.append(("harness", None, state, questions))
        for index, item in enumerate(found_compactions):
            if len(item["summary"]) < 200:
                continue
            state = {"about": DATA_NOTE, "requirements": labels, "summary": clip(item["summary"], 60_000)}
            questions = {
                key: Noul(
                    instructions=f"`summary` replaced the conversation when the context was compacted. Does it still carry `requirements.{key}`, by stating it or by saying it is already done?",
                    criteria=NoulCriteria(true="The requirement is stated, implied or reported as done in `summary`", false="`summary` does not mention it"),
                )
                for key in labels
            }
            requests.append(("retention", index, state, questions))
    for index, item in enumerate(found_delegations):
        if not item["result"] or not item["_next"]:
            continue
        state = {
            "about": DATA_NOTE,
            "brief": clip(item["brief"] or item["description"] or "", 2000),
            "subagent_report": clip(item["result"], 4000),
            "next_steps": [_step_view(m) for m in item["_next"]],
        }
        questions = {
            "adoption": Choice(
                instructions="A parent agent delegated `brief` to a subagent, which returned `subagent_report`. What do `next_steps` of the parent do with that report?",
                criteria=ADOPTION,
            )
        }
        requests.append(("delegation", index, state, questions))
    for index, item in enumerate(found_hooks):
        if item["_next"] is None:
            continue
        state = {"about": DATA_NOTE, "reminder": clip(item["text"], 3000), "next_step": _step_view(item["_next"])}
        questions = {
            "response": Choice(
                instructions="`reminder` was injected automatically by the agent's harness. How does `next_step` respond to it?",
                criteria=HOOK_RESPONSE,
            )
        }
        requests.append(("hook", index, state, questions))

    responses = analyzer.ask([(state, questions) for _, _, state, questions in requests]) if requests else []
    coverage: dict[str, tuple[float, int]] = {}
    failed = 0
    for (kind, index, _, _), response in zip(requests, responses):
        if isinstance(response, Exception):
            failed += 1
            continue
        tokens += response.usage.input_tokens
        answers = response.answers
        if kind == "coverage":
            for key in labels:
                answer = answers.get(key)
                if answer is None:
                    continue
                for step_key, p in answer.probabilities.items():
                    if step_key != "none" and p > coverage.get(key, (0.0, 0))[0]:
                        coverage[key] = (p, int(step_key.split("-", 1)[1]))
        elif kind == "harness":
            for i, key in enumerate(labels):
                kept[i]["p_harness_conflict"] = round(answers[key].noul, 3)
        elif kind == "retention":
            dropped = [i for i, key in enumerate(labels) if answers[key].noul < 0.5]
            result["compactions"][index]["dropped"] = dropped
            result["compactions"][index]["checked"] = True
        elif kind == "delegation":
            answer = answers["adoption"]
            result["delegations"][index]["adoption"] = {"choice": answer.choice, "confidence": round(answer.confidence, 3)}
        elif kind == "hook":
            answer = answers["response"]
            result["hooks"][index]["response"] = {"choice": answer.choice, "confidence": round(answer.confidence, 3)}
    for i, key in enumerate(labels):
        p, step = coverage.get(key, (0.0, None))
        kept[i]["step"] = step if p >= 0.3 else None
        kept[i]["p_step"] = round(p, 3)
    result["jev"] = True
    result["input_tokens"] = tokens
    if failed:
        # Some answers are missing: show what came back, but do not cache it as final.
        result["incomplete"] = failed
    return result


def _public(item: dict[str, Any]) -> dict[str, Any]:
    """Drop working fields and clip long text for the API payload."""
    public = {k: v for k, v in item.items() if not k.startswith("_")}
    for key in ("brief", "result", "text"):
        if isinstance(public.get(key), str):
            public[key] = clip(public[key], 1500)
    if "_next" in item:
        following = item["_next"]
        if isinstance(following, list):
            public["next_steps"] = [m["step"] for m in following]
        elif following is not None:
            public["next_steps"] = [following["step"]]
    return public
