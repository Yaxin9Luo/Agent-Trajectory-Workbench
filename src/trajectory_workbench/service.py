from __future__ import annotations

import base64
import binascii
import getpass
import hashlib
import json
import os
import re
import threading
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from trajectory_workbench import adapters as adapter_registry
from trajectory_workbench import excerpt, explorer, exporter, harness, practice, readiness, rewrite, search, signals
from trajectory_workbench.insights import artifact_diff, compare_runs, error_aggregation
from trajectory_workbench.jev import (
    HARNESS_FLAG,
    STEP_FLAG_LABELS,
    STEP_NOULS,
    UNVALIDATED,
    flag_threshold,
    STEP_VERSION,
    TASK_VERSION,
    JevAnalyzer,
    JevUnavailable,
    outcome_consistency,
)
from trajectory_workbench.adapters.common import estimate_tokens
from trajectory_workbench.ledgers import LEDGER_VERSION, build_ledgers
from trajectory_workbench.models import NormalizedRun
from trajectory_workbench.registry import Registry
from trajectory_workbench.store import Store, now, trajectory_id


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# Run metadata worth keeping on the index row for the episode tree.
EXTRA_META = ("subagent_type", "description", "context_reason", "post_compaction", "spawn_depth")
SPAWN_TOOLS = {"Agent", "Task", "spawn_agent"}
EXPORT_ROOT = Path(os.environ.get("TRAJECTORY_WORKBENCH_EXPORT_ROOT", str(Path.home() / "trajectory-exports"))).expanduser()
EXPORT_NAME = re.compile(r"[\w.-]{1,120}")
SNIPPET_TRAJECTORIES = 20
# Index columns the list, queue and statistics need (the signals JSON, instruction and
# per-call data are the heavy ones and are left out).
SUMMARY_COLUMNS = (
    "id", "collection", "adapter_id", "path", "title", "run_id", "task_key", "task_id", "model", "harness",
    "group_key", "group_role", "segment", "outcome_status", "outcome_score", "outcome_reason", "infra_failure",
    "steps", "tool_calls", "tool_errors", "duration_ms", "tokens", "flags", "jev_summary", "imported_at",
    "episode_head", "episode", "extra", "ready",
)
CACHE_SIZE = 24


def default_reviewer() -> str:
    """Reviews are keyed by account name; USER can be unset or wrong under launchers."""
    configured = os.environ.get("TRAJECTORY_WORKBENCH_REVIEWER")
    if configured:
        return configured
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        return getpass.getuser() or "me"


class Jobs:
    """Background jobs (imports, batch Jev analysis) with pollable progress."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, target: Callable[[Callable[..., None]], Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "kind": kind, "status": "running", "done": 0, "total": None,
               "message": "", "result": None, "error": None, "started_at": now()}
        with self._lock:
            self._jobs[job_id] = job

        def progress(done: int | None = None, total: int | None = None, message: str | None = None) -> None:
            with self._lock:
                if done is not None:
                    job["done"] = done
                if total is not None:
                    job["total"] = total
                if message is not None:
                    job["message"] = message

        def run() -> None:
            try:
                result = target(progress)
                with self._lock:
                    job.update(status="done", result=result, finished_at=now())
            except Exception as error:  # surfaced to the UI
                with self._lock:
                    job.update(status="failed", error=str(error), finished_at=now())

        threading.Thread(target=run, daemon=True).start()
        return dict(job)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return dict(self._jobs[job_id])

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(job) for job in self._jobs.values()]


class WorkbenchService:
    def __init__(
        self,
        store: Store,
        analyzer: JevAnalyzer | None = None,
        reviewer: str | None = None,
        legacy_registry: Path | None = None,
    ) -> None:
        self.store = store
        self.analyzer = analyzer or JevAnalyzer()
        self.reviewer = reviewer or default_reviewer()
        self.jobs = Jobs()
        self._cache: OrderedDict[str, tuple[tuple, NormalizedRun]] = OrderedDict()
        self._cache_lock = threading.Lock()
        if legacy_registry is not None:
            self.migrate_legacy(legacy_registry)

    # -- import -------------------------------------------------------------------------

    def migrate_legacy(self, registry_path: Path) -> int:
        """Import runs listed in the old JSON registry once, when the index is empty."""
        if self.store.list_sources() or not registry_path.expanduser().is_file():
            return 0
        imported = 0
        for entry in Registry(registry_path).list_entries():
            if entry.available:
                try:
                    self.import_path(entry.path, "MoH runs")
                    imported += 1
                except ValueError:
                    continue
        return imported

    def import_path(
        self,
        source_path: str,
        collection: str | None = None,
        progress: Callable[..., None] | None = None,
        index_text: bool = True,
    ) -> dict[str, Any]:
        supplied = Path(source_path).expanduser()
        if not supplied.is_absolute():
            raise ValueError("Path must be absolute")
        root = supplied.resolve()
        if not root.exists():
            raise ValueError(f"Path does not exist: {root}")
        name = (collection or "").strip() or root.name
        sources = list(adapter_registry.discover(root))
        if not sources:
            raise ValueError(
                "No trajectories recognized. Supported: Claude Code sessions, Codex rollouts, "
                "ATIF trajectory.json / Harbor trials, TraceLab trials, chat/SFT JSONL, MoH runs."
            )
        report = {"collection": name, "sources": 0, "trajectories": 0, "errors": [], "adapters": {}}
        # Collections that lose a source to this import need their episodes regrouped too.
        affected = {name}
        for _, path in sources:
            previous = self.store.source_collection(hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16])
            if previous:
                affected.add(previous)
        for index, (adapter, path) in enumerate(sources, start=1):
            if progress:
                progress(done=report["trajectories"], message=f"{index}/{len(sources)} {path.name}")
            try:
                rows = self._index_source(adapter, path, name, progress, report["trajectories"], index_text)
            except Exception as error:
                report["errors"].append({"path": str(path), "error": str(error)[:300]})
                continue
            report["sources"] += 1
            report["trajectories"] += len(rows)
            report["adapters"][adapter.adapter_id] = report["adapters"].get(adapter.adapter_id, 0) + len(rows)
        if report["sources"]:
            if progress:
                progress(done=report["trajectories"], message="整理 episode")
            for collection_name in sorted(affected):
                self.store.rebuild_episodes(collection_name)
            self.store.prune_step_text()
        if progress:
            progress(done=report["trajectories"], message="完成")
        return report

    def _index_source(self, adapter: Any, path: Path, collection: str, progress, offset: int, index_text: bool = True) -> list[dict[str, Any]]:
        source_id = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
        fingerprint = json.dumps(adapter_registry.fingerprint(adapter, path))
        rows: list[dict[str, Any]] = []
        stamp = now()
        # Text is replaced per trajectory as it is indexed (a run's steps never straddle a
        # flush), so a failed import leaves every trajectory with either its old or its
        # new text; text of trajectories that disappeared is pruned after the import.
        pending_ids: set[str] = set()
        pending_text: list[tuple[str, int, str]] = []
        for locator, run in adapter.iter_runs(path):
            key = None if locator is None else str(locator.get("key") or locator.get("offset"))
            row = self._row(run, trajectory_id(path, key), source_id, collection, adapter.adapter_id, path, locator, fingerprint, stamp)
            rows.append(row)
            pending_ids.add(row["id"])
            if index_text:
                pending_text.extend(
                    (row["id"], message["step"], body)
                    for message in run.messages
                    if message["role"] != "system" and (body := search.step_body(message))
                )
            if len(pending_text) >= 5000 or len(pending_ids) >= 500:
                self.store.replace_step_text(pending_ids, pending_text)
                pending_ids, pending_text = set(), []
            if progress and len(rows) % 20 == 0:
                progress(done=offset + len(rows))
        self.store.replace_step_text(pending_ids, pending_text)
        inherit_group_titles(rows)
        self.store.upsert_source(source_id, str(path), adapter.adapter_id, collection)
        self.store.replace_source_trajectories(source_id, rows)
        with self._cache_lock:
            for row in rows:
                self._cache.pop(row["id"], None)
        return rows

    def _row(self, run, row_id, source_id, collection, adapter_id, path, locator, fingerprint, stamp) -> dict[str, Any]:
        computed = signals.compute(run)
        tool_stats, error_templates = explorer.run_tool_stats(run)
        ready = readiness.check(run, computed["values"]["harness"])
        metrics = run.metrics
        meta = run.meta or {}
        outcome = run.outcome or {}
        duration = metrics.get("wall_ms")
        if duration is None and metrics.get("has_clock"):
            duration = metrics.get("max_offset_ms")
        keys = {key: meta.get(key) for key in ("group_key", "self_key", "parent_key")}
        scope = (lambda value: f"{source_id}/{value}" if value else None) if meta.get("episode_scope") == "source" else (lambda value: value)
        # Sample ids and session ids that are only unique within one file / trial get the
        # source as a prefix (spawned-thread keys on calls too, so links still match).
        keys = {key: scope(value) for key, value in keys.items()}
        role = meta.get("group_role")
        return {
            "id": row_id,
            "source_id": source_id,
            "collection": collection,
            "adapter_id": adapter_id,
            "path": str(path),
            "locator": locator,
            "title": run.title,
            "run_id": run.run_id,
            "task_key": (run.task or {}).get("key"),
            "task_id": (run.task or {}).get("id"),
            "instruction": ((run.task or {}).get("instruction") or "")[:4000],
            "model": meta.get("model") or metrics.get("model"),
            "harness": meta.get("harness"),
            "group_key": keys["group_key"],
            "group_role": role,
            "segment": meta.get("segment"),
            "outcome_status": outcome.get("status") or "unknown",
            "outcome_score": outcome.get("score"),
            "outcome_reason": outcome.get("reason"),
            "infra_failure": outcome.get("infra_failure"),
            "steps": len(run.messages),
            "tool_calls": len(run.tools),
            "tool_errors": computed["values"]["tool_errors"],
            "duration_ms": duration,
            "tokens": metrics.get("total_tokens"),
            "flags": [flag["key"] for flag in computed["flags"]],
            "signals": computed,
            "jev_summary": None,
            "fingerprint": fingerprint,
            "imported_at": stamp,
            "self_key": keys["self_key"],
            "parent_key": keys["parent_key"],
            "parent_call_id": meta.get("parent_call_id"),
            # Until the episode rebuild at the end of the import, only mains count.
            "episode_head": 0 if role in {"segment", "subagent"} else 1,
            "episode": None,
            "extra": {key: meta[key] for key in EXTRA_META if meta.get(key) not in (None, "")} or None,
            # Calls that can launch a subagent, so the episode tree never reparses sources.
            "spawn_calls": [
                {"call_id": tool["id"], "step": tool["step"], "tool": tool["name"], "spawned": scope(tool.get("spawned"))}
                for tool in run.tools
                if tool.get("spawned") or tool["name"].split("__")[-1] in SPAWN_TOOLS
            ] or None,
            "tool_stats": tool_stats or None,
            "error_templates": error_templates or None,
            "readiness": ready,
            "ready": ready["ready"],
        }

    def start_import(self, source_path: str, collection: str | None, index_text: bool = True) -> dict[str, Any]:
        supplied = Path(source_path).expanduser()
        if not supplied.is_absolute():
            raise ValueError("Path must be absolute")
        if not supplied.exists():
            raise ValueError(f"Path does not exist: {supplied}")
        return self.jobs.start("import", lambda progress: self.import_path(source_path, collection, progress, index_text))

    # -- rewrite diff & corrections -------------------------------------------------------

    def rewrite_pairs(self, before: str, after: str, limit: int = 500) -> dict[str, Any]:
        """Trajectories present in both collections under the same sample id."""
        left = {row["run_id"]: row for row in self.store.all_trajectories(before) if row.get("run_id")}
        pairs = []
        for row in self.store.all_trajectories(after):
            other = left.get(row.get("run_id"))
            if other is None:
                continue
            ra, rb = other.get("readiness") or {}, row.get("readiness") or {}
            pairs.append({
                "run_id": row["run_id"],
                "before": {"id": other["id"], "steps": other.get("steps"), "trained_tokens": ra.get("trained_tokens"), "residue": _residue_count(ra), "ready": ra.get("ready")},
                "after": {"id": row["id"], "steps": row.get("steps"), "trained_tokens": rb.get("trained_tokens"), "residue": _residue_count(rb), "ready": rb.get("ready")},
                "same_source": other.get("fingerprint") == row.get("fingerprint") and other.get("path") == row.get("path"),
            })
        pairs.sort(key=lambda item: item["run_id"])
        shown = pairs[:limit]
        # A rewrite that also drops the harness prompt or tool declarations would have an
        # empty inventory of its own: judge it against what the original had.
        for pair in shown:
            original = ((left[pair["run_id"]].get("signals") or {}).get("values") or {}).get("harness")
            if not original:
                continue
            try:
                _, run = self._load(pair["after"]["id"], cache=False)
            except (FileNotFoundError, KeyError, OSError, ValueError):
                continue
            pair["after"]["residue"] = harness.used_steps(harness.trace(run, harness.merge(original, harness.inventory(run))))
        return {"before": before, "after": after, "matched": len(pairs), "only_before": len(left) - len(pairs), "pairs": shown}

    def rewrite_diff(self, before_id: str, after_id: str) -> dict[str, Any]:
        row_a, run_a = self._load(before_id)
        row_b, run_b = self._load(after_id)
        result = rewrite.diff_runs(run_a, run_b)
        result["before"] = {"id": before_id, "title": row_a.get("title"), "collection": row_a.get("collection"), "readiness": row_a.get("readiness")}
        result["after"] = {"id": after_id, "title": row_b.get("title"), "collection": row_b.get("collection"), "readiness": row_b.get("readiness")}
        return result

    def export_corrections(self, collection: str | None, kind: str) -> str:
        """JSONL of SFT samples or DPO pairs from reviews with a turning step and correction."""
        if kind not in {"sft", "dpo"}:
            raise ValueError("kind must be sft or dpo")
        lines = []
        for review in self.store.reviews(collection):
            if not review.get("turning_step") or not (review.get("correction") or "").strip():
                continue
            try:
                row, run = self._load(review["trajectory_id"])
            except (FileNotFoundError, KeyError, OSError, ValueError):
                continue
            samples = rewrite.correction_samples(run, row, review)
            if samples:
                lines.append(json.dumps(samples[kind], ensure_ascii=False))
        return "\n".join(lines) + ("\n" if lines else "")

    # -- export -------------------------------------------------------------------------

    def export_rows(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """Every trajectory matching library filters; in episode view, every member of
        each matching episode (segments and subagents are samples of their own)."""
        filters = {key: value for key, value in filters.items() if key not in {"offset", "limit", "sort"}}
        _, rows = self.store.query_trajectories(reviewer=self.reviewer, sort="task", limit=None, **filters)
        if not filters.get("episodes"):
            return rows
        members: list[dict[str, Any]] = []
        seen: set[str] = set()
        for head in rows:
            if head.get("group_key"):
                _, group = self.store.query_trajectories(
                    reviewer=self.reviewer, collection=head["collection"], group_key=head["group_key"], sort="episode", limit=None
                )
            else:
                group = [head]
            for row in group:
                if row["id"] not in seen:
                    seen.add(row["id"])
                    members.append(row)
        return members

    def export(self, filters: dict[str, Any], target: Path, mode: str, progress: Callable[..., None] | None = None) -> dict[str, Any]:
        rows = self.export_rows(filters)
        if not rows:
            raise ValueError("no trajectories match these filters")
        return exporter.export(rows, target, mode=mode, load_run=lambda trajectory_id: self._load(trajectory_id)[1], filters=filters, progress=progress)

    def start_export(self, filters: dict[str, Any], name: str, mode: str) -> dict[str, Any]:
        """Export into a new folder under the export root (the browser never picks a path)."""
        name = (name or "").strip()
        if not name or not EXPORT_NAME.fullmatch(name):
            raise ValueError("export name may only use letters, digits, '.', '_' and '-'")
        target = EXPORT_ROOT / name
        if target.exists():
            raise ValueError(f"{target} already exists")
        return self.jobs.start("export", lambda progress: self.export(filters, target, mode, progress))

    # -- explorer -----------------------------------------------------------------------

    def explore(self, collections: list[str]) -> dict[str, Any]:
        """Tool usage and error templates per collection (every trajectory, subagents
        included), side by side when two collections are given."""
        names = [None if name in ("", "*") else name for name in collections] or [None]
        return {
            "collections": [
                {"collection": name, **explorer.summarize_collection(self.store.all_trajectories(name, columns=explorer.COLUMNS))}
                for name in names[:2]
            ]
        }

    # -- search -------------------------------------------------------------------------

    def search_steps(self, query: str, collection: str | None = None, limit: int = 60) -> dict[str, Any]:
        """Steps whose text matches every term of `query`, grouped by trajectory."""
        match = search.match_expression(query or "")
        if match is None:
            raise ValueError("query must contain a word")
        limit = max(1, min(200, limit))
        hits = self.store.search_steps(match, collection, limit)
        terms = search.query_terms(query)
        groups: dict[str, dict[str, Any]] = {}
        for hit in hits:
            group = groups.get(hit["trajectory_id"])
            if group is None:
                row = self.store.get_trajectory(hit["trajectory_id"])
                if row is None:
                    continue
                group = groups[hit["trajectory_id"]] = {"trajectory": self._list_item(row), "hits": [], "_run": None}
                # Snippets need the transcript; parse only the best-ranked trajectories.
                if len(groups) > SNIPPET_TRAJECTORIES:
                    group["_run"] = False
            if group["_run"] is None:
                try:
                    group["_run"] = self._load(hit["trajectory_id"], cache=False)[1]
                except (FileNotFoundError, KeyError, ValueError, OSError):
                    group["_run"] = False
            message = None
            if group["_run"]:
                message = next((m for m in group["_run"].messages if m["step"] == hit["step"]), None)
            group["hits"].append({
                "step": hit["step"],
                "role": message["role"] if message else None,
                "snippet": search.snippet(message, terms) if message else None,
            })
        results = []
        for group in groups.values():
            group.pop("_run")
            group["hits"].sort(key=lambda item: item["step"])
            results.append(group)
        return {"query": query, "terms": terms, "hits": len(hits), "limited": len(hits) >= limit, "results": results, "snippets_for": SNIPPET_TRAJECTORIES}

    def start_reindex(self, collection: str) -> dict[str, Any]:
        """Re-import every source of a collection (after an upgrade added index fields)."""
        sources = [item for item in self.store.list_sources() if item["collection"] == collection]
        if not sources:
            raise ValueError(f"no sources in collection {collection!r}")

        def work(progress: Callable[..., None]) -> dict[str, Any]:
            done, errors = 0, []
            for index, item in enumerate(sources, start=1):
                progress(done=index - 1, total=len(sources), message=Path(item["path"]).name)
                if not item["available"]:
                    errors.append({"path": item["path"], "error": "source no longer exists"})
                    continue
                try:
                    done += self.import_path(item["path"], collection)["trajectories"]
                except ValueError as error:
                    errors.append({"path": item["path"], "error": str(error)[:300]})
            return {"trajectories": done, "errors": errors}

        return self.jobs.start("reindex", work)

    # -- listing ------------------------------------------------------------------------

    def list_collections(self) -> dict[str, Any]:
        return {
            "collections": self.store.collections(),
            "sources": self.store.list_sources(),
            "jev_available": self.analyzer.available(),
            "reviewer": self.reviewer,
        }

    def list_trajectories(self, **filters: Any) -> dict[str, Any]:
        total, rows = self.store.query_trajectories(reviewer=self.reviewer, **filters)
        return {"total": total, "items": [self._list_item(row) for row in rows]}

    def _list_item(self, row: dict[str, Any]) -> dict[str, Any]:
        item = {
            key: value for key, value in row.items()
            if key not in {"signals", "instruction", "fingerprint", "locator", "tool_stats", "error_templates", "spawn_calls"}
        }
        if isinstance(row.get("readiness"), dict):
            # The list only needs the verdict and issue keys; the reader loads the rest.
            item["readiness"] = {
                "ready": row["readiness"].get("ready"),
                "tokens": row["readiness"].get("tokens"),
                "trained_share": row["readiness"].get("trained_share"),
                "issues": [{"key": i["key"], "severity": i["severity"]} for i in row["readiness"].get("issues", [])],
            }
        item["instruction_preview"] = (row.get("instruction") or "")[:240]
        return item

    def _episodes(self, collection: str | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Episode head rows with episode-wide steps / errors / flags, plus a map from
        every trajectory id to its episode head (so a review on any segment counts)."""
        rows = self.store.all_trajectories(collection, columns=SUMMARY_COLUMNS)
        heads: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            if row.get("episode_head"):
                heads[(row["collection"], row.get("group_key") or row["id"])] = row
        head_of: dict[str, str] = {}
        for row in rows:
            head = heads.get((row["collection"], row.get("group_key") or row["id"]))
            head_of[row["id"]] = head["id"] if head else row["id"]
        episodes = []
        for row in heads.values():
            summary = row.get("episode") or {}
            if summary:
                row = {**row, "steps": summary.get("steps", row.get("steps")),
                       "tool_errors": summary.get("tool_errors", row.get("tool_errors")),
                       "flags": summary.get("flags", row.get("flags"))}
            episodes.append(row)
        return episodes, head_of

    # -- one trajectory -----------------------------------------------------------------

    def get_trajectory(self, trajectory_id: str) -> dict[str, Any]:
        row, run = self._load(trajectory_id)
        payload = run.summary_dict()
        payload.pop("tools", None)
        payload["id"] = trajectory_id
        # The index row may carry a title / task inherited from the group's first segment.
        payload["title"] = row.get("title") or run.title
        if not (payload["task"] or {}).get("instruction") and row.get("instruction"):
            payload["task"] = {**(payload["task"] or {}), "instruction": row["instruction"], "inherited": True}
        payload["collection"] = row["collection"]
        payload["signals"] = row["signals"]
        payload["readiness"] = row.get("readiness") or readiness.check(run)
        # Rows imported before harness.py have no trace in their signals; compute it here.
        payload["harness"] = (row["signals"].get("values") or {}).get("harness") or harness.trace(run)
        # A later context segment continues this trajectory: end-of-run readings (no check
        # after the last edit, polish tail) do not apply here.
        payload["continues"] = "no_verify" in (row["signals"].get("suppressed") or [])
        payload["tool_index"] = [
            {
                "id": tool["id"],
                "step": tool["step"],
                "name": tool["name"],
                "layer": signals.harness_layer(tool),
                "is_error": bool((tool.get("result") or {}).get("is_error")),
                "mutation": signals.is_mutation(tool),
                "images": len((tool.get("result") or {}).get("images") or []),
            }
            for tool in run.tools
        ]
        payload["step_index"] = [
            {
                "step": m["step"],
                "role": m["role"],
                "tools": len(m["tools"]),
                "error": any((t.get("result") or {}).get("is_error") for t in m["tools"]),
                "layer": m.get("layer"),
                "chars": len(m.get("text") or "") + len(m.get("thinking") or ""),
                "t": m.get("offset_ms"),
                # Prompt size before this step: measured when the source logs usage.
                "context": m["context_tokens"] if isinstance(m.get("context_tokens"), int) else m.get("context_estimate"),
                "context_measured": isinstance(m.get("context_tokens"), int),
                "compaction": bool(m.get("compaction")),
                "tokens": step_tokens(m),
            }
            for m in run.messages
        ]
        payload["review"] = self.store.get_review(trajectory_id, self.reviewer)
        payload["same_task"] = self._related(row, "task_key") if row.get("task_key") else []
        payload["jev"] = self._cached_jev(trajectory_id, row)
        payload["jev_available"] = self.analyzer.available()
        return payload

    def _related(self, row: dict[str, Any], column: str) -> list[dict[str, Any]]:
        # Other attempts at the same task, one row per episode, across all collections.
        _, rows = self.store.query_trajectories(
            reviewer=self.reviewer, **{column: row[column]}, episodes=True, sort="task", limit=200
        )
        return [
            {
                "id": item["id"],
                "title": item["title"],
                "run_id": item["run_id"],
                "group_role": item.get("group_role"),
                "segment": item.get("segment"),
                "model": item.get("model"),
                "collection": item.get("collection"),
                "outcome_status": item.get("outcome_status"),
                "steps": item.get("steps"),
                "reviewed": item.get("reviewed_at") is not None,
            }
            for item in rows
            if item["id"] != row["id"]
            and not (row.get("group_key") and item.get("group_key") == row["group_key"])
        ]

    def get_episode(self, trajectory_id: str) -> dict[str, Any]:
        """The episode tree: main trajectory and context segments in order (with
        placeholders for segments missing from the import), and each subagent attached to
        the step of the call that launched it."""
        row = self.store.get_trajectory(trajectory_id)
        if row is None:
            raise KeyError(trajectory_id)
        if not row.get("group_key"):
            members = [row]
        else:
            _, members = self.store.query_trajectories(
                reviewer=self.reviewer, collection=row["collection"], group_key=row["group_key"], sort="task", limit=1000
            )
        order = {"main": 0, "segment": 1, "subagent": 2}
        members.sort(key=lambda r: (order.get(r.get("group_role") or "main", 0), r.get("segment") or 0, r.get("run_id") or ""))
        threads = [m for m in members if (m.get("group_role") or "main") != "subagent"]
        subagents = [m for m in members if (m.get("group_role") or "main") == "subagent"]

        # Find the launching call of each subagent from the calls recorded at import: by
        # call id (Claude, SFT) or by the child's thread key on the call (Codex, ATIF).
        by_call: dict[str, dict[str, Any]] = {}
        by_spawned: dict[str, dict[str, Any]] = {}
        for candidate in members:
            for call in candidate.get("spawn_calls") or []:
                link = {"trajectory_id": candidate["id"], "step": call["step"], "tool": call["tool"], "call_id": call["call_id"]}
                by_call.setdefault(call["call_id"], link)
                if call.get("spawned"):
                    by_spawned.setdefault(call["spawned"], link)
        spawns: dict[str, dict[str, Any]] = {}
        for child in subagents:
            link = by_call.get(child.get("parent_call_id") or "") or by_spawned.get(child.get("self_key") or "")
            if link is not None and link["trajectory_id"] != child["id"]:
                spawns[child["id"]] = link

        def node(item: dict[str, Any]) -> dict[str, Any]:
            extra = item.get("extra") or {}
            return {
                "id": item["id"],
                "run_id": item.get("run_id"),
                "title": item.get("title"),
                "role": item.get("group_role") or "main",
                "segment": item.get("segment"),
                "steps": item.get("steps"),
                "tool_errors": item.get("tool_errors"),
                "outcome_status": item.get("outcome_status"),
                "flags": item.get("flags") or [],
                "subagent_type": extra.get("subagent_type"),
                "description": extra.get("description"),
                "context_reason": extra.get("context_reason"),
                "spawn": spawns.get(item["id"]),
                "reviewed": item.get("reviewed_at") is not None,
                "current": item["id"] == trajectory_id,
            }

        lanes: list[dict[str, Any]] = []
        numbers = [t.get("segment") for t in threads]
        expected = 1
        for thread in threads:
            number = thread.get("segment")
            while number and expected < number:
                lanes.append({"missing": True, "segment": expected})
                expected += 1
            lanes.append(node(thread))
            expected = (number or expected) + 1
        head = next((m for m in members if m.get("episode_head")), members[0])
        return {
            "key": row.get("group_key"),
            "head_id": head["id"],
            "head_missing": not threads,
            "summary": head.get("episode"),
            "threads": lanes,
            "subagents": [node(item) for item in subagents],
            "segment_numbers": [n for n in numbers if n],
        }

    def get_messages(
        self,
        trajectory_id: str,
        *,
        roles: set[str] | None = None,
        tool: str | None = None,
        search: str | None = None,
        steps: set[int] | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        _, run = self._load(trajectory_id)
        selected: list[dict[str, Any]] = []
        query = (search or "").casefold()
        for message in run.messages:
            if steps is not None:
                if message["step"] not in steps:
                    continue
            else:
                if roles and message["role"] not in roles:
                    continue
                if tool and not any(item["name"] == tool for item in message["tools"]):
                    continue
                if query and query not in json.dumps(message, ensure_ascii=False).casefold():
                    continue
            selected.append(message)
        bounded_offset = max(0, offset)
        bounded_limit = min(500, max(1, limit))
        page = selected[bounded_offset : bounded_offset + bounded_limit]
        repeats = self._repeats(run)
        positions = {id(tool): index for index, tool in enumerate(run.tools)}
        return {
            "total": len(selected),
            "offset": bounded_offset,
            "limit": bounded_limit,
            "items": [self._with_media_urls(trajectory_id, message, repeats, positions) for message in page],
        }

    def _with_media_urls(
        self,
        trajectory_id: str,
        message: dict[str, Any],
        repeats: dict[str, dict[str, Any]] | None = None,
        positions: dict[int, int] | None = None,
    ) -> dict[str, Any]:
        """Swap image payloads for URLs (inline base64 is served on demand, so a page of
        messages stays small) and annotate tools with their harness layer."""
        base = f"/api/trajectories/{trajectory_id}"

        def convert(images: list[dict[str, Any]], owner: str) -> list[dict[str, Any]]:
            converted = []
            for index, image in enumerate(images or []):
                if "path" in image:
                    converted.append({"url": f"{base}/media?path=" + _quote(image["path"])})
                elif "data" in image:
                    converted.append({"url": f"{base}/inline-image?ref={_quote(owner)}&i={index}", "media_type": image.get("media_type")})
                else:
                    converted.append(image)
            return converted

        copy = dict(message)
        copy["images"] = convert(message.get("images"), f"m:{message['step']}")
        tools = []
        for tool in message["tools"]:
            tool_copy = dict(tool)
            if tool.get("result"):
                # Calls are addressed by position: some exporters reuse call ids.
                owner = f"t:{positions[id(tool)]}" if positions and id(tool) in positions else f"t:{tool['id']}"
                tool_copy["result"] = {**tool["result"], "images": convert(tool["result"].get("images"), owner)}
            tool_copy["layer"] = signals.harness_layer(tool)
            tool_copy["mutation"] = signals.is_mutation(tool)
            if repeats and tool["id"] in repeats:
                tool_copy["repeat_of"] = repeats[tool["id"]]
            tools.append(tool_copy)
        copy["tools"] = tools
        return copy

    @staticmethod
    def _repeats(run: NormalizedRun) -> dict[str, dict[str, Any]]:
        """For each call, the latest earlier call with the same tool and identical input."""
        last: dict[str, dict[str, Any]] = {}
        repeats: dict[str, dict[str, Any]] = {}
        for tool in run.tools:
            try:
                signature = tool["name"] + "\u0000" + json.dumps(tool.get("input"), sort_keys=True, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                continue
            if signature in last:
                repeats[tool["id"]] = last[signature]
            last[signature] = {"step": tool["step"], "id": tool["id"]}
        return repeats

    def inline_image(self, trajectory_id: str, ref: str, index: int) -> tuple[bytes, str]:
        """Bytes of a base64 image embedded in the transcript (by message step or call id)."""
        _, run = self._load(trajectory_id)
        kind, _, key = ref.partition(":")
        images: list[dict[str, Any]] = []
        if kind == "m":
            message = next((m for m in run.messages if str(m["step"]) == key), None)
            images = (message or {}).get("images") or []
        elif kind == "t":
            tool = run.tools[int(key)] if key.isdigit() and int(key) < len(run.tools) else next((t for t in run.tools if t["id"] == key), None)
            images = ((tool or {}).get("result") or {}).get("images") or []
        if not 0 <= index < len(images) or "data" not in images[index]:
            raise KeyError(ref)
        image = images[index]
        media_type = image.get("media_type")
        if media_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
            raise PermissionError("unsupported image type")
        data = image["data"]
        try:
            return base64.b64decode(data + "=" * (-len(data) % 4)), media_type
        except (ValueError, binascii.Error) as error:
            raise ValueError("image data is not valid base64") from error

    def resolve_media(self, trajectory_id: str, requested: str) -> Path:
        """Serve an image only if this trajectory's transcript references that exact path."""
        _, run = self._load(trajectory_id)
        candidate = Path(requested)
        if candidate.suffix.lower() not in IMAGE_SUFFIXES:
            raise PermissionError("Only image files can be served")
        referenced = {
            image.get("path")
            for message in run.messages
            for image in (message.get("images") or [])
        } | {
            image.get("path")
            for tool in run.tools
            for image in ((tool.get("result") or {}).get("images") or [])
        }
        if requested not in referenced:
            raise PermissionError("Image is not referenced by this trajectory")
        if not candidate.is_file():
            raise FileNotFoundError(requested)
        return candidate

    def resolve_file(self, trajectory_id: str, relative_path: str) -> Path:
        row = self.store.get_trajectory(trajectory_id)
        if row is None:
            raise KeyError(trajectory_id)
        root = Path(row["path"]).resolve()
        if not root.is_dir():
            raise FileNotFoundError(relative_path)
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root):
            raise PermissionError("Requested file is outside the registered run")
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    def get_errors(self, trajectory_id: str) -> dict[str, Any]:
        _, run = self._load(trajectory_id)
        return error_aggregation(run)

    def get_artifact_diffs(self, trajectory_id: str) -> dict[str, Any]:
        row, run = self._load(trajectory_id)
        return artifact_diff(run, Path(row["path"]))

    def compare(self, trajectory_ids: list[str]) -> dict[str, Any]:
        return compare_runs([self._load(item)[1] for item in trajectory_ids])

    # -- reviews ------------------------------------------------------------------------

    def save_review(self, trajectory_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        if self.store.get_trajectory(trajectory_id) is None:
            raise KeyError(trajectory_id)
        clean: dict[str, Any] = {}
        for key in ("predicted_status", "human_status"):
            if key in fields:
                value = fields[key]
                if value not in (None, "", *practice.HUMAN_STATUSES):
                    raise ValueError(f"{key} must be one of {practice.HUMAN_STATUSES}")
                clean[key] = value or None
        if "labels" in fields:
            labels = fields["labels"] or []
            if not isinstance(labels, list) or not all(isinstance(item, str) for item in labels):
                raise ValueError("labels must be a list of strings")
            clean["labels"] = sorted({item.strip() for item in labels if item.strip()})
        if "turning_step" in fields:
            value = fields["turning_step"]
            clean["turning_step"] = int(value) if value not in (None, "") else None
        for key in ("turning_note", "hypothesis", "correction", "note"):
            if key in fields:
                clean[key] = (fields[key] or "").strip() or None
        if "intervention" in fields:
            if fields["intervention"] not in (None, "", *practice.INTERVENTIONS):
                raise ValueError("unknown intervention")
            clean["intervention"] = fields["intervention"] or None
        if "attribution" in fields:
            if fields["attribution"] not in (None, "", *practice.ATTRIBUTIONS):
                raise ValueError("unknown attribution")
            clean["attribution"] = fields["attribution"] or None
        if "blind" in fields:
            clean["blind"] = bool(fields["blind"])
        if "label_details" in fields:
            details = fields["label_details"] or {}
            if not isinstance(details, dict):
                raise ValueError("label_details must be an object")
            clean["label_details"] = {}
            for key, value in details.items():
                if not isinstance(value, dict):
                    raise ValueError("each label detail must be an object")
                severity = value.get("severity") or None
                if severity not in (None, *practice.SEVERITIES):
                    raise ValueError(f"severity must be one of {tuple(practice.SEVERITIES)}")
                entry = {k: v for k, v in (("severity", severity), ("decisive", bool(value.get("decisive")))) if v}
                if entry:
                    clean["label_details"][str(key)] = entry
        return self.store.save_review(trajectory_id, self.reviewer, clean)

    def delete_review(self, trajectory_id: str) -> None:
        self.store.delete_review(trajectory_id, self.reviewer)

    def suggest_review(self, trajectory_id: str, notes: dict[str, str]) -> dict[str, Any]:
        if self.store.get_trajectory(trajectory_id) is None:
            raise KeyError(trajectory_id)
        definitions = {key: definition for key, (_, _, definition) in practice.LABELS.items()}
        return self.analyzer.suggest_review(notes, definitions)

    # -- step annotations & excerpts ------------------------------------------------------

    def annotations(self, trajectory_id: str) -> list[dict[str, Any]]:
        if self.store.get_trajectory(trajectory_id) is None:
            raise KeyError(trajectory_id)
        return self.store.annotations(trajectory_id)

    def save_annotation(self, trajectory_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        row = self.store.get_trajectory(trajectory_id)
        if row is None:
            raise KeyError(trajectory_id)
        note = str(fields.get("note") or "").strip()
        if not note:
            raise ValueError("note must not be empty")
        try:
            step = int(fields.get("step"))
        except (TypeError, ValueError) as error:
            raise ValueError("step must be an integer") from error
        if not 1 <= step <= (row.get("steps") or 0):
            raise ValueError("step is outside the trajectory")
        annotation_id = fields.get("id")
        if "label" in fields or not annotation_id:
            label = fields.get("label") or None
        else:
            # An edit that does not mention the label keeps it.
            label = next((item["label"] for item in self.store.annotations(trajectory_id) if item["id"] == int(annotation_id)), None)
        if label is not None and not isinstance(label, str):
            raise ValueError("label must be a string")
        return self.store.save_annotation(trajectory_id, self.reviewer, step, note[:4000], label, int(annotation_id) if annotation_id else None)

    def delete_annotation(self, trajectory_id: str, annotation_id: int) -> None:
        self.store.delete_annotation(trajectory_id, self.reviewer, annotation_id)

    def excerpt(self, trajectory_id: str, steps: str | None, hide_outcome: bool = False) -> str:
        """Markdown of the chosen steps (default: annotated steps and the turning point)."""
        row, run = self._load(trajectory_id)
        notes = self.store.annotations(trajectory_id)
        review = self.store.get_review(trajectory_id, self.reviewer)
        if steps and steps.strip():
            chosen = excerpt.parse_steps(steps, len(run.messages))
        else:
            chosen = sorted({item["step"] for item in notes} | ({review["turning_step"]} if review and review.get("turning_step") else set()))
        if not chosen:
            raise ValueError("no steps: annotate some steps or give a range such as 3-9, 15")
        return excerpt.markdown_excerpt(run, row, chosen, notes, review, hide_outcome=hide_outcome)

    def export_reviews(self, collection: str | None = None) -> str:
        lines = []
        for review in self.store.reviews(collection):
            lines.append(json.dumps(review, ensure_ascii=False))
        return "\n".join(lines) + ("\n" if lines else "")

    # -- practice & stats ---------------------------------------------------------------

    def queue(self, collection: str | None, date: str | None, size: int) -> dict[str, Any]:
        day = date or datetime.now(UTC).date().isoformat()
        rows, head_of = self._episodes(collection)
        reviews: dict[str, dict[str, Any]] = {}
        for review in self.store.reviews(collection, self.reviewer):
            head = head_of.get(review["trajectory_id"], review["trajectory_id"])
            if head not in reviews or review["updated_at"] > reviews[head]["updated_at"]:
                reviews[head] = review
        queue = practice.build_queue(
            rows, reviews, date=day, seed_scope=f"{collection or '*'}:{self.reviewer}", size=max(4, min(100, size))
        )
        for item in queue["items"]:
            item["trajectory"] = self._list_item(item["trajectory"])
        return queue

    def stats(self, collection: str | None) -> dict[str, Any]:
        episodes, _ = self._episodes(collection)
        result = practice.collection_stats(episodes, self.store.reviews(collection, self.reviewer))
        result["readiness"] = readiness_stats(self.store.all_trajectories(collection, columns=("id", "readiness")))
        result["harness"] = harness_stats(self.store.all_trajectories(collection, columns=("id", "signals", "jev_summary")))
        result["jev"]["usage"] = self.store.jev_usage()
        return result

    def taxonomy(self) -> dict[str, Any]:
        return {**practice.taxonomy(), "readiness_issues": readiness.ISSUE_LABELS, "jev_flags": STEP_FLAG_LABELS,
                "jev_thresholds": {key: flag_threshold(key) for key in (*STEP_NOULS, HARNESS_FLAG)}, "jev_unvalidated": list(UNVALIDATED)}

    # -- Jev ----------------------------------------------------------------------------

    def _cached_jev(self, trajectory_id: str, row: dict[str, Any]) -> dict[str, Any] | None:
        steps = self.store.get_jev(trajectory_id, "steps", STEP_VERSION, row.get("fingerprint"))
        task = self.store.get_jev(trajectory_id, "task", TASK_VERSION, row.get("fingerprint"))
        if steps is None and task is None:
            return None
        return {"steps": steps, "task": task, "summary": row.get("jev_summary")}

    def analyze(self, trajectory_id: str, force: bool = False) -> dict[str, Any]:
        row, run = self._load(trajectory_id)
        fingerprint = row.get("fingerprint")
        steps = None if force else self.store.get_jev(trajectory_id, "steps", STEP_VERSION, fingerprint)
        task = None if force else self.store.get_jev(trajectory_id, "task", TASK_VERSION, fingerprint)
        try:
            if steps is None:
                steps = self.analyzer.analyze_steps(run)
                self.store.put_jev(trajectory_id, "steps", STEP_VERSION, fingerprint, steps.get("model"), steps.get("input_tokens"), steps)
            if task is None:
                task = self.analyzer.analyze_task(run)
                self.store.put_jev(trajectory_id, "task", TASK_VERSION, fingerprint, task.get("model"), task.get("input_tokens"), task)
        except JevUnavailable as error:
            raise ValueError(f"Jev unavailable: {error}") from error
        summary = dict(steps.get("summary") or {})
        summary["final_claim"] = (task.get("final_claim") or {}).get("choice")
        summary["task_ambiguity"] = (task.get("task_ambiguity") or {}).get("score")
        summary["consistency"] = outcome_consistency(task.get("final_claim"), run.outcome)
        summary["version"] = STEP_VERSION
        self.store.set_jev_summary(trajectory_id, summary)
        return {"steps": steps, "task": task, "summary": summary}

    def start_batch_analysis(self, collection: str | None, limit: int, include_done: bool = False) -> dict[str, Any]:
        rows = [
            row for row in self.store.all_trajectories(collection)
            if include_done or not row.get("jev_summary") or row["jev_summary"].get("version") != STEP_VERSION
        ][: max(1, limit)]

        def work(progress: Callable[..., None]) -> dict[str, Any]:
            progress(done=0, total=len(rows))
            failed = []
            tokens = 0
            for index, row in enumerate(rows, start=1):
                try:
                    result = self.analyze(row["id"])
                    tokens += (result["steps"] or {}).get("input_tokens") or 0
                except Exception as error:
                    failed.append({"id": row["id"], "error": str(error)[:200]})
                progress(done=index, message=f"{row['title'][:60]}")
            return {"analyzed": len(rows) - len(failed), "failed": failed, "input_tokens": tokens}

        return self.jobs.start("jev", work)

    def ledgers(self, trajectory_id: str, *, run_jev: bool = False, force: bool = False) -> dict[str, Any]:
        """Requirement / delegation / plan / hook / compaction ledgers.

        Without `run_jev` this returns the cached Jev version when there is one, otherwise
        the code-only ledgers (cheap, computed on the fly).
        """
        row, run = self._load(trajectory_id)
        fingerprint = row.get("fingerprint")
        cached = None if force else self.store.get_jev(trajectory_id, "ledgers", LEDGER_VERSION, fingerprint)
        if cached is not None:
            return cached
        instruction = (run.task or {}).get("instruction") or row.get("instruction")
        if not run_jev:
            return build_ledgers(run, instruction, None)
        if not self.analyzer.available():
            raise ValueError("Jev unavailable: TYPESAFE_API_KEY is not set")
        result = build_ledgers(run, instruction, self.analyzer)
        if result.get("error"):
            raise ValueError(result["error"])
        if not result.get("incomplete"):
            self.store.put_jev(trajectory_id, "ledgers", LEDGER_VERSION, fingerprint, None, result.get("input_tokens"), result)
        return result

    def find_steps(self, trajectory_id: str, query: str) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("query must not be empty")
        _, run = self._load(trajectory_id)
        try:
            return self.analyzer.find_steps(run, query.strip())
        except JevUnavailable as error:
            raise ValueError(f"Jev unavailable: {error}") from error

    # -- loading ------------------------------------------------------------------------

    def _load(self, trajectory_id: str, *, cache: bool = True) -> tuple[dict[str, Any], NormalizedRun]:
        """The index row and the parsed transcript. `cache=False` (bulk reads such as
        search snippets) leaves the reader's small LRU cache alone."""
        row = self.store.get_trajectory(trajectory_id)
        if row is None:
            raise KeyError(trajectory_id)
        path = Path(row["path"])
        if not path.exists():
            raise FileNotFoundError(str(path))
        adapter = adapter_registry.get_adapter(row["adapter_id"])
        stamp = adapter_registry.fingerprint(adapter, path)
        with self._cache_lock:
            cached = self._cache.get(trajectory_id)
            if cached is not None and cached[0] == stamp:
                self._cache.move_to_end(trajectory_id)
                return row, cached[1]
        run = adapter.load(path, row.get("locator"))
        if not cache:
            return row, run
        with self._cache_lock:
            self._cache[trajectory_id] = (stamp, run)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return row, run


def _residue_count(result: dict[str, Any]) -> int:
    """Steps with harness residue inside trained tokens."""
    trained = (result.get("residue") or {}).get("trained") or {}
    return len({step for steps in trained.values() for step in steps})


def step_tokens(message: dict[str, Any]) -> dict[str, int]:
    """Estimated tokens of one step, split into trained (the model's own turn: text,
    reasoning, tool calls) and context (everything else, tool results included)."""
    own = estimate_tokens(message.get("text") or "") + estimate_tokens(message.get("thinking") or "")
    calls = sum(estimate_tokens(tool["input"] if isinstance(tool.get("input"), str) else json.dumps(tool.get("input"), ensure_ascii=False, default=str)) for tool in message["tools"])
    results = sum(estimate_tokens((tool.get("result") or {}).get("text") or "") for tool in message["tools"])
    if message["role"] in readiness.TRAINED_ROLES:
        return {"trained": own + calls, "context": results}
    return {"trained": 0, "context": own + calls + results}


def harness_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per trajectory (training sample): which harness components its trained turns use,
    and how many Jev judged to depend on the harness."""
    traced = [(row.get("signals") or {}).get("values", {}).get("harness") for row in rows]
    traced = [trace for trace in traced if trace]
    components: dict[tuple[str, str], dict[str, Any]] = {}
    for trace in traced:
        for item in trace["components"]:
            entry = components.setdefault((item["kind"], item["name"]), {"kind": item["kind"], "name": item["name"], "present": 0, **{how: 0 for how in harness.USES}, "any": 0})
            entry["present"] += 1
            for how in harness.USES:
                entry[how] += bool(item.get(how))
            entry["any"] += any(item.get(how) for how in harness.USES)
    analyzed = [row["jev_summary"] for row in rows if isinstance(row.get("jev_summary"), dict) and row["jev_summary"].get("version") == STEP_VERSION]
    kinds: dict[str, int] = {}
    for summary in analyzed:
        for kind, count in (summary.get("harness_counts") or {}).items():
            kind = "unclear" if kind == "none" else kind  # summaries written before the fix
            kinds[kind] = kinds.get(kind, 0) + bool(count)
    return {
        "trajectories": len(rows),
        "traced": len(traced),
        "with_components": sum(1 for trace in traced if trace["components"]),
        "rule_hits": sum(1 for trace in traced if trace.get("steps")),
        "jev_analyzed": len(analyzed),
        "jev_hits": sum(1 for summary in analyzed if (summary.get("flag_counts") or {}).get("harness_reliance")),
        "jev_kinds": kinds,
        "components": sorted(components.values(), key=lambda item: (-item["any"], -item["present"]))[:60],
    }


def readiness_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Training readiness over every trajectory (each one is a training sample)."""
    checked = [row["readiness"] for row in rows if isinstance(row.get("readiness"), dict)]
    issues: dict[str, dict[str, int]] = {}
    for item in checked:
        for issue in item.get("issues", []):
            entry = issues.setdefault(issue["key"], {"count": 0, "block": 0})
            entry["count"] += 1
            entry["block"] += issue["severity"] == "block"
    tokens = sorted(item.get("tokens") or 0 for item in checked)

    def quantile(q: float) -> int | None:
        return tokens[min(len(tokens) - 1, int(q * (len(tokens) - 1)))] if tokens else None

    shares = [item.get("trained_share") or 0 for item in checked]
    return {
        "checked": len(checked),
        "ready": sum(1 for item in checked if item.get("ready")),
        "issues": [
            {"key": key, "label": readiness.ISSUE_LABELS.get(key, key), **value}
            for key, value in sorted(issues.items(), key=lambda pair: -pair[1]["count"])
        ],
        "tokens": {"p50": quantile(0.5), "p90": quantile(0.9), "max": tokens[-1] if tokens else None, "total": sum(tokens)},
        "trained_share": round(sum(shares) / len(shares), 3) if shares else None,
        "max_seq_len": readiness.MAX_SEQ_LEN,
    }


def inherit_group_titles(rows: list[dict[str, Any]]) -> None:
    """Context segments after compaction have no task message of their own; give them
    the title, task and instruction of the first segment in the same group."""
    heads: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("group_key") and row.get("instruction") and row.get("group_role") != "subagent":
            heads.setdefault(row["group_key"], row)
    for row in rows:
        head = heads.get(row.get("group_key") or "")
        if head is None or head is row or row.get("instruction"):
            continue
        suffix = f" · 分段 {row['segment']}" if row.get("segment") else ""
        row["title"] = (head["title"] or "") + suffix
        row["instruction"] = head["instruction"]
        row["task_key"] = row.get("task_key") or head.get("task_key")


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
