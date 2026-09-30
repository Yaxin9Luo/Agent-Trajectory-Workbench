"""Pipeline annotations for a rewrite batch, read into one shape whatever wrote them.

A rewriting pipeline may record why it changed each span, what it reviewed but kept, each
sample's status, how rewritten messages map to original ones, and the plan it follows. The
review works without any of this (`rewrite_review` diffs the trajectories themselves); with
it, each change shows the pipeline's reasons. Two layouts are read:

- an annotations JSONL, the neutral format any pipeline can write (see the appendix of
  docs/superpowers/specs/2026-09-30-rewrite-review-design.md): one object per sample with
  `sample_id`, plus optionally one `{"plan": [...], "fates": {...}}` line;
- a rewrite run directory: `run_config.json`, `rewritten.jsonl`, `report.json` and
  `chains/<chain>/status.json` + `results.jsonl`, with the plan table in `../../spec/`.

Both become {"adapter", "path", "meta", "plan": {"items", "fates"}, "records": {sample_id:
{"status", "reason", "index_map", "changes", "targets", "sync"}}}.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# Pipeline field names → the step field they edit.
FIELDS = {
    "reasoning_content": "thinking", "reasoning": "thinking", "thinking": "thinking",
    "content": "text", "text": "text", "content.summary": "text", "content.analysis": "text",
}
# Run settings worth showing; endpoints and input paths stay out of the index.
RUN_SETTINGS = ("config_hash", "prompt", "model", "locator", "thinking", "repair_rounds", "skills", "spec", "started", "chains")
INDEX_MAP = re.compile(r'"index_map"\s*:\s*(\[[^\]]*\])')
SAMPLE_ID = re.compile(r'^\{\s*"id"\s*:\s*"((?:[^"\\]|\\.)*)"')


def load(path: Path) -> dict[str, Any]:
    root = Path(path).expanduser()
    if not root.is_absolute():
        raise ValueError("annotations path must be absolute")
    root = root.resolve()
    if root.is_dir():
        if (root / "chains").is_dir():
            return run_directory(root)
        raise ValueError(f"{root}: expected a rewrite run directory (chains/*/results.jsonl) or an annotations .jsonl file")
    if root.is_file():
        return annotations_jsonl(root)
    raise ValueError(f"annotations path does not exist: {root}")


def _empty() -> dict[str, Any]:
    return {"status": None, "reason": None, "index_map": None, "changes": [], "targets": [], "sync": []}


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


# -- neutral JSONL --------------------------------------------------------------------------


def annotations_jsonl(path: Path) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    items: dict[str, dict[str, Any]] = {}
    fates: dict[str, str] = {}
    meta: dict[str, Any] = {}
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path.name}:{number}: not JSON ({error})") from error
            if not isinstance(value, dict):
                raise ValueError(f"{path.name}:{number}: each line must be a JSON object")
            if "plan" in value and "sample_id" not in value:
                for item in value.get("plan") or []:
                    if isinstance(item, dict) and item.get("id") is not None:
                        items[str(item["id"])] = plan_item(item)
                fates.update({str(k): str(v) for k, v in (value.get("fates") or {}).items()})
                continue
            if "meta" in value and "sample_id" not in value:
                meta.update(value["meta"] if isinstance(value["meta"], dict) else {})
                continue
            sample = value.get("sample_id")
            if not isinstance(sample, str) or not sample:
                raise ValueError(f"{path.name}:{number}: sample_id is required")
            records[sample] = _neutral_record(value)
    return {"adapter": "annotations-jsonl", "path": str(path), "meta": meta, "plan": {"items": items, "fates": fates}, "records": records}


def plan_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item["id"]),
        "group": item.get("group"),
        "fate": item.get("fate"),
        "before": item.get("before"),
        "after": item.get("after"),
        "note": item.get("note"),
    }


def _neutral_record(value: dict[str, Any]) -> dict[str, Any]:
    record = _empty()
    record["status"] = value.get("status") if isinstance(value.get("status"), str) else None
    record["reason"] = value.get("reason") if isinstance(value.get("reason"), str) else None
    if isinstance(value.get("index_map"), list):
        record["index_map"] = value["index_map"]
    for index, change in enumerate(value.get("changes") or []):
        if not isinstance(change, dict):
            continue
        field = FIELDS.get(str(change.get("field") or "text"), "text")
        message, step = _int(change.get("message")), _int(change.get("step"))
        record["changes"].append({
            "key": str(change.get("id") or f"{message if message is not None else 's' + str(step)}:{field}:{index}"),
            "message": message,
            "step": step,
            "field": field,
            "old": str(change.get("old") or ""),
            "new": str(change.get("new") or ""),
            "category": change.get("category"),
            "source": change.get("source"),
            "reason": change.get("reason"),
            "refs": _strings(change.get("refs")),
            "warnings": _strings(change.get("warnings")),
            "evidence": change.get("evidence") if isinstance(change.get("evidence"), list) else [],
            "target": change.get("target"),
        })
    for target in value.get("targets") or []:
        if isinstance(target, dict):
            record["targets"].append({
                "message": _int(target.get("message")),
                "step": _int(target.get("step")),
                "task": target.get("task"),
                "locator": [item for item in target.get("locator") or [] if isinstance(item, dict)],
                "kept": [item for item in target.get("kept") or [] if isinstance(item, dict)],
            })
    record["sync"] = [item for item in value.get("sync") or [] if isinstance(item, dict)]
    return record


# -- rewrite run directory ------------------------------------------------------------------


def _json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_directory(root: Path) -> dict[str, Any]:
    config = _json_file(root / "run_config.json") or {}
    report = _json_file(root / "report.json")
    meta: dict[str, Any] = {key: config[key] for key in RUN_SETTINGS if key in config}
    if isinstance(report, dict):
        meta["report"] = report
    records: dict[str, dict[str, Any]] = {}
    for status_path in sorted((root / "chains").glob("*/status.json")):
        status = _json_file(status_path) or {}
        state = status.get("state")
        why = status.get("reason") or status.get("error")
        for sample in status.get("records") or []:
            value = (status.get("record_status") or {}).get(sample) or state
            record = records.setdefault(sample, _empty())
            record["status"] = value
            if value != "rewritten" and why:
                record["reason"] = str(why)[:500]
        for entry in status.get("sync") or []:
            item = {
                "from": entry.get("from"), "to": entry.get("to"), "ok": entry.get("match") not in (None, "mismatch"),
                "detail": entry.get("match"), "changed": entry.get("changed"),
            }
            for sample in {entry.get("from"), entry.get("to")} - {None}:
                records.setdefault(sample, _empty())["sync"].append(item)
        results = status_path.with_name("results.jsonl")
        if results.is_file():
            with results.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        result = json.loads(line)
                        _from_result(result, records.setdefault(str(result.get("record")), _empty()))
    rewritten = root / "rewritten.jsonl"
    if rewritten.is_file():
        with rewritten.open(encoding="utf-8") as handle:
            for line in handle:
                sample, index_map = _index_map(line)
                if sample and index_map is not None:
                    records.setdefault(sample, _empty())["index_map"] = index_map
    return {"adapter": "rewrite-run-dir", "path": str(root), "meta": meta, "plan": _plan(root, config), "records": records}


def _index_map(line: str) -> tuple[str | None, list[Any] | None]:
    """The sample id and the structural index map a record's rewrite tag carries, read
    without parsing the (large) record when the layout allows."""
    head = SAMPLE_ID.match(line)
    found = INDEX_MAP.search(line)
    if head and found:
        return json.loads('"' + head.group(1) + '"'), json.loads(found.group(1))
    if not line.strip() or '"index_map"' not in line:
        return None, None
    record = json.loads(line)
    skills = (((record.get("tag") or {}).get("rewrite") or {}).get("skills") or {})
    return record.get("id"), skills.get("index_map") if isinstance(skills.get("index_map"), list) else None


def _from_result(result: dict[str, Any], record: dict[str, Any]) -> None:
    """One reviewed message: its edits, the spans kept on purpose and what located it."""
    message = _int(result.get("msg"))
    task = result.get("task") or "rewrite"
    spans = [span for span in result.get("spans") or [] if isinstance(span, dict)]
    for index, edit in enumerate(result.get("edits") or []):
        evidence = next((span.get("evidence") for span in spans if span.get("quote") == edit.get("old") and span.get("evidence")), None)
        record["changes"].append({
            "key": f"{message}:{task}:{index}",
            "message": message,
            "step": None,
            "field": FIELDS.get(str(edit.get("field")), "text"),
            "old": edit.get("old") or "",
            "new": edit.get("new") or "",
            "category": edit.get("category"),
            "source": edit.get("source_type"),
            "reason": edit.get("note"),
            "refs": _strings(edit.get("clause_ids")),
            "warnings": _strings(edit.get("warnings")),
            "evidence": evidence or [],
            "target": f"{message}/{task}",
        })
    record["targets"].append({
        "message": message,
        "step": None,
        "task": task,
        "locator": [
            {"source": hint.get("source"), "field": FIELDS.get(str(hint.get("field")), hint.get("field")), "quote": hint.get("quote"),
             "reason": hint.get("reason"), "found": hint.get("quote_found")}
            for hint in result.get("hints") or [] if isinstance(hint, dict)
        ],
        "kept": [
            {"field": FIELDS.get(str(span.get("field")), "text"), "quote": span.get("quote"), "reason": span.get("note"),
             "source": span.get("source_type"), "category": span.get("category"), "refs": _strings(span.get("clause_ids"))}
            for span in spans if span.get("decision") == "keep"
        ],
        "rejected": [
            {"field": report.get("field"), "status": report.get("status"), "errors": report.get("errors")}
            for report in result.get("reports") or []
            if isinstance(report, dict) and any(word in str(report.get("status")) for word in ("reject", "not_found"))
        ],
        "need_context": result.get("need_context"),
        "parsed": result.get("parsed"),
        "evidence": {"verified": result.get("evidence_ok"), "unverified": result.get("evidence_bad")},
    })


def _plan(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """The run's plan table (`spec.clauses` names it) as plan items."""
    name = (config.get("spec") or {}).get("clauses") if isinstance(config.get("spec"), dict) else None
    if not isinstance(name, str) or not re.fullmatch(r"[\w.-]+", name):
        return {"items": {}, "fates": {}}
    table = _json_file(root.parent.parent / "spec" / f"{name}.json")
    if not isinstance(table, dict):
        return {"items": {}, "fates": {}}
    items = {}
    for clause in table.get("clauses") or []:
        if isinstance(clause, dict) and clause.get("id"):
            items[str(clause["id"])] = plan_item({
                "id": clause["id"], "group": clause.get("section"), "fate": clause.get("status"),
                "before": clause.get("old_text"), "after": clause.get("new_text") or clause.get("quote"), "note": clause.get("note"),
            })
    fates = {str(k): str(v) for k, v in (table.get("statuses") or {}).items()}
    return {"items": items, "fates": fates, "name": name}
