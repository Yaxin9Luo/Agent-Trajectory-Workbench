"""Full-text search over every step of a collection (SQLite FTS5).

The index is contentless: it stores tokens, not text, so it stays small next to
multi-GB sources; snippets are cut from the transcript when results are shown. Each step
indexes a clipped body (message, reasoning, tool calls and the start of their output).

The unicode61 tokenizer keeps a run of CJK characters as one token, so CJK characters are
spaced out before indexing and queries become phrase searches over single characters.
"""

from __future__ import annotations

import json
import re
from typing import Any


CJK = re.compile(r"([぀-ヿ㐀-䶿一-鿿豈-﫿가-힯])")
LIMITS = {"text": 1500, "thinking": 800, "input": 400, "output": 400}


def spaced(text: str) -> str:
    return CJK.sub(r" \1 ", text)


def step_body(message: dict[str, Any]) -> str:
    parts = [(message.get("text") or "")[: LIMITS["text"]], (message.get("thinking") or "")[: LIMITS["thinking"]]]
    for tool in message.get("tools") or []:
        tool_input = tool.get("input")
        rendered = tool_input if isinstance(tool_input, str) else json.dumps(tool_input, ensure_ascii=False, default=str)
        parts.append(tool["name"] + " " + rendered[: LIMITS["input"]])
        parts.append(((tool.get("result") or {}).get("text") or "")[: LIMITS["output"]])
    return spaced("\n".join(part for part in parts if part))


def match_expression(query: str) -> str | None:
    """Turn what a person typed into an FTS5 query: every term must appear; a term is a
    phrase (CJK characters adjacent, punctuation ignored); "quoted text" stays together."""
    terms = re.findall(r'"([^"]+)"|(\S+)', query)
    phrases = []
    for quoted, word in terms:
        tokens = re.findall(r"\w+", spaced(quoted or word))
        if tokens:
            phrases.append('"' + " ".join(tokens) + '"')
    return " AND ".join(phrases) if phrases else None


def query_terms(query: str) -> list[str]:
    return [quoted or word for quoted, word in re.findall(r'"([^"]+)"|(\S+)', query) if (quoted or word).strip()]


def snippet(message: dict[str, Any], terms: list[str], width: int = 90) -> dict[str, Any]:
    """The first place in the step where a query term occurs, with some context."""
    fields = [("text", message.get("text") or ""), ("thinking", message.get("thinking") or "")]
    for tool in message.get("tools") or []:
        tool_input = tool.get("input")
        fields.append(("input", tool_input if isinstance(tool_input, str) else json.dumps(tool_input, ensure_ascii=False, default=str)))
        fields.append(("output", (tool.get("result") or {}).get("text") or ""))
    lowered = [term.casefold() for term in terms]
    for field, text in fields:
        folded = text.casefold()
        for term in lowered:
            index = folded.find(term)
            if index >= 0:
                start = max(0, index - width)
                end = min(len(text), index + len(term) + width)
                return {
                    "field": field,
                    "text": ("…" if start else "") + text[start:end].replace("\n", " ") + ("…" if end < len(text) else ""),
                }
    return {"field": None, "text": (message.get("text") or message.get("thinking") or "")[: width * 2].replace("\n", " ")}
