"""SQLite index of imported trajectories, human reviews and cached Jev results.

Source files are never copied: a trajectory row stores the source path, the adapter and
a locator (e.g. a byte offset into a multi-GB JSONL file), plus the summary and signals
computed at import so lists, queues and statistics never reparse transcripts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB_PATH = Path.home() / ".agent-trajectory-workbench" / "workbench.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    collection TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    trajectory_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trajectories (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    collection TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    path TEXT NOT NULL,
    locator TEXT,
    title TEXT,
    run_id TEXT,
    task_key TEXT,
    task_id TEXT,
    instruction TEXT,
    model TEXT,
    harness TEXT,
    group_key TEXT,
    group_role TEXT,
    segment INTEGER,
    outcome_status TEXT NOT NULL DEFAULT 'unknown',
    outcome_score REAL,
    outcome_reason TEXT,
    infra_failure INTEGER,
    steps INTEGER,
    tool_calls INTEGER,
    tool_errors INTEGER,
    duration_ms INTEGER,
    tokens INTEGER,
    flags TEXT NOT NULL DEFAULT '[]',
    signals TEXT NOT NULL DEFAULT '{}',
    jev_summary TEXT,
    fingerprint TEXT,
    imported_at TEXT NOT NULL,
    self_key TEXT,
    parent_key TEXT,
    parent_call_id TEXT,
    episode_head INTEGER NOT NULL DEFAULT 1,
    episode TEXT,
    extra TEXT,
    spawn_calls TEXT,
    tool_stats TEXT,
    error_templates TEXT,
    readiness TEXT,
    ready INTEGER
);
CREATE INDEX IF NOT EXISTS trajectories_collection ON trajectories(collection);
CREATE INDEX IF NOT EXISTS trajectories_task ON trajectories(task_key);
CREATE INDEX IF NOT EXISTS trajectories_group ON trajectories(group_key);
CREATE INDEX IF NOT EXISTS trajectories_source ON trajectories(source_id);
CREATE INDEX IF NOT EXISTS trajectories_episode ON trajectories(collection, episode_head);
CREATE INDEX IF NOT EXISTS trajectories_collection_group ON trajectories(collection, group_key);
CREATE TABLE IF NOT EXISTS reviews (
    trajectory_id TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    predicted_status TEXT,
    human_status TEXT,
    labels TEXT NOT NULL DEFAULT '[]',
    turning_step INTEGER,
    turning_note TEXT,
    hypothesis TEXT,
    intervention TEXT,
    attribution TEXT,
    correction TEXT,
    note TEXT,
    blind INTEGER NOT NULL DEFAULT 0,
    label_details TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (trajectory_id, reviewer)
);
CREATE TABLE IF NOT EXISTS step_rows (
    rowid INTEGER PRIMARY KEY,
    trajectory_id TEXT NOT NULL,
    step INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS step_rows_trajectory ON step_rows(trajectory_id);
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY,
    trajectory_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS annotations_trajectory ON annotations(trajectory_id, step);
CREATE TABLE IF NOT EXISTS jev_results (
    trajectory_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    version TEXT NOT NULL,
    fingerprint TEXT,
    model TEXT,
    input_tokens INTEGER,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (trajectory_id, kind)
);
"""

FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS step_text USING fts5("
    "body, content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2')"
)

TRAJECTORY_COLUMNS = (
    "id, source_id, collection, adapter_id, path, locator, title, run_id, task_key, task_id, "
    "instruction, model, harness, group_key, group_role, segment, outcome_status, "
    "outcome_score, outcome_reason, infra_failure, steps, tool_calls, tool_errors, "
    "duration_ms, tokens, flags, signals, jev_summary, fingerprint, imported_at, "
    "self_key, parent_key, parent_call_id, episode_head, episode, extra, spawn_calls, "
    "tool_stats, error_templates, readiness, ready"
)
# Columns added after the first release; older databases get them on open.
ADDED_COLUMNS = {
    "self_key": "TEXT",
    "parent_key": "TEXT",
    "parent_call_id": "TEXT",
    "episode_head": "INTEGER NOT NULL DEFAULT 1",
    "episode": "TEXT",
    "extra": "TEXT",
    "spawn_calls": "TEXT",
    "tool_stats": "TEXT",
    "error_templates": "TEXT",
    "readiness": "TEXT",
    "ready": "INTEGER",
}
JSON_COLUMNS = {
    "flags", "signals", "locator", "jev_summary", "episode", "extra", "spawn_calls", "tool_stats",
    "error_templates", "readiness",
}
ROLE_ORDER = {"main": 0, "segment": 1, "subagent": 2}
REVIEW_FIELDS = (
    "predicted_status",
    "human_status",
    "labels",
    "turning_step",
    "turning_note",
    "hypothesis",
    "intervention",
    "attribution",
    "correction",
    "note",
    "blind",
    "label_details",
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def trajectory_id(path: Path, locator_key: str | None) -> str:
    """Stable id: sha256 of the resolved path (plus the in-file key for multi-sample files).

    Single-trajectory sources keep the id the old JSON registry used, so links survive.
    """
    material = str(path) if locator_key is None else f"{path}#{locator_key}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


class Store:
    def __init__(self, path: Path = DEFAULT_DB_PATH) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            existing = {row[1] for row in self._db.execute("PRAGMA table_info(trajectories)")}
            migrated = False
            review_columns = {row[1] for row in self._db.execute("PRAGMA table_info(reviews)")}
            if review_columns and "label_details" not in review_columns:
                self._db.execute("ALTER TABLE reviews ADD COLUMN label_details TEXT")
            if existing:
                for column, kind in ADDED_COLUMNS.items():
                    if column not in existing:
                        self._db.execute(f"ALTER TABLE trajectories ADD COLUMN {column} {kind}")
                        migrated = True
            self._db.executescript(SCHEMA)
            try:
                self._db.execute(FTS_SCHEMA)
                self.text_search = True
            except sqlite3.OperationalError:
                # contentless_delete needs SQLite 3.43+; without it search is unavailable.
                self.text_search = False
            self._db.commit()
        if migrated:
            # Rows from an older build: regroup them (links that need the new adapter
            # fields appear after the next import of each source).
            for (name,) in self._db.execute("SELECT DISTINCT collection FROM trajectories").fetchall():
                self.rebuild_episodes(name)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- sources / trajectories -----------------------------------------------------

    def source_collection(self, source_id: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT collection FROM sources WHERE id = ?", (source_id,)).fetchone()
        return row[0] if row else None

    def upsert_source(self, source_id: str, path: str, adapter_id: str, collection: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO sources (id, path, adapter_id, collection, registered_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "adapter_id=excluded.adapter_id, collection=excluded.collection",
                (source_id, path, adapter_id, collection, now()),
            )
            self._db.commit()

    def replace_source_trajectories(self, source_id: str, rows: list[dict[str, Any]]) -> None:
        """Swap a source's trajectory rows in one transaction.

        Reviews and Jev results live in their own tables keyed by trajectory id, so they
        survive; the Jev summary column is carried over when the source is unchanged.
        """
        with self._lock:
            previous = {
                row[0]: row[1]
                for row in self._db.execute(
                    "SELECT id, jev_summary FROM trajectories WHERE source_id = ? AND jev_summary IS NOT NULL",
                    (source_id,),
                )
            }
            fingerprints = dict(
                self._db.execute(
                    "SELECT id, fingerprint FROM trajectories WHERE source_id = ?", (source_id,)
                ).fetchall()
            )
            for row in rows:
                if row.get("jev_summary") is None and row["id"] in previous and fingerprints.get(row["id"]) == row.get("fingerprint"):
                    row["jev_summary"] = previous[row["id"]]
            self._db.execute("DELETE FROM trajectories WHERE source_id = ?", (source_id,))
            self._db.executemany(
                f"INSERT OR REPLACE INTO trajectories ({TRAJECTORY_COLUMNS}) VALUES "
                f"({', '.join('?' for _ in TRAJECTORY_COLUMNS.split(','))})",
                [self._trajectory_tuple(row) for row in rows],
            )
            self._db.execute(
                "UPDATE sources SET trajectory_count = ? WHERE id = ?", (len(rows), source_id)
            )
            self._db.commit()

    def _trajectory_tuple(self, row: dict[str, Any]) -> tuple:
        values = []
        for column in (name.strip() for name in TRAJECTORY_COLUMNS.split(",")):
            value = row.get(column)
            if column in JSON_COLUMNS and value is not None and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            if column in {"infra_failure", "ready"} and value is not None:
                value = int(bool(value))
            values.append(value)
        return tuple(values)

    def rebuild_episodes(self, collection: str) -> None:
        """Group a collection's rows into episodes and summarize each on its head row.

        An episode is one task attempt: the main trajectory, its post-compaction context
        segments and every subagent it spawned (transitively). Subagents point at their
        parent with `parent_key` (the parent's `self_key`); segments share `group_key`.
        The head is the main trajectory or first segment; when neither was imported the
        first subagent heads the episode and `head_missing` is set.

        Signals that only make sense at the end of a trajectory (no check after the last
        edit) are suppressed on segments that a later segment continues.
        """
        with self._lock:  # held throughout, so concurrent imports cannot interleave
            self._rebuild_episodes(collection)

    def _rebuild_episodes(self, collection: str) -> None:
        with self._lock:
            rows = [
                dict(row)
                for row in self._db.execute(
                    "SELECT id, group_key, group_role, segment, run_id, self_key, parent_key, steps, "
                    "tool_calls, tool_errors, tokens, duration_ms, signals, readiness FROM trajectories "
                    "WHERE collection = ?",
                    (collection,),
                )
            ]
        by_self = {row["self_key"]: row for row in rows if row["self_key"]}

        def root_key(row: dict[str, Any]) -> str:
            current, seen = row, set()
            while current.get("parent_key") in by_self and current["parent_key"] not in seen:
                seen.add(current["parent_key"])
                current = by_self[current["parent_key"]]
            if current.get("parent_key"):
                return current["parent_key"]  # the parent was not imported
            return current.get("group_key") or current.get("self_key") or "row:" + current["id"]

        episodes: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            row["signals"] = json.loads(row["signals"] or "{}")
            row["readiness"] = json.loads(row["readiness"]) if row.get("readiness") else None
            episodes.setdefault(root_key(row), []).append(row)

        updates = []
        for key, members in episodes.items():
            members.sort(key=lambda r: (ROLE_ORDER.get(r["group_role"] or "main", 0), r["segment"] or 0, r["run_id"] or ""))
            threads = [r for r in members if (r["group_role"] or "main") != "subagent"]
            subagents = [r for r in members if (r["group_role"] or "main") == "subagent"]
            head = threads[0] if threads else members[0]
            numbers = sorted({r["segment"] for r in threads if r["segment"]})
            missing = [n for n in range(1, numbers[-1] + 1) if n not in numbers] if numbers else []
            flags: dict[str, list[str]] = {"threads": [], "subagents": []}
            for row in members:
                signals = row["signals"]
                suppressed = []
                continued = row in threads and row is not threads[-1]
                if continued:
                    suppressed.append("no_verify")
                readiness = row["readiness"]
                if readiness:
                    # A segment that compaction cut off is expected to end mid-task.
                    for issue in readiness.get("issues", []):
                        if issue["key"] == "truncated_end":
                            issue["severity"] = "info" if continued else "block"
                            issue["continued"] = continued
                    readiness["ready"] = not any(issue["severity"] == "block" for issue in readiness.get("issues", []))
                signals["suppressed"] = suppressed
                row["flags"] = [f["key"] for f in signals.get("flags", []) if f["key"] not in suppressed]
                bucket = flags["subagents" if row in subagents else "threads"]
                bucket.extend(flag for flag in row["flags"] if flag not in bucket)
            summary = None
            if len(members) > 1 or head.get("group_key") or head.get("self_key"):
                summary = {
                    "key": key,
                    "members": len(members),
                    "threads": len(threads),
                    "segments": numbers,
                    "missing_segments": missing,
                    "subagents": len(subagents),
                    "head_missing": not threads,
                    "steps": sum(r["steps"] or 0 for r in members),
                    "tool_calls": sum(r["tool_calls"] or 0 for r in members),
                    "tool_errors": sum(r["tool_errors"] or 0 for r in members),
                    "tokens": sum(r["tokens"] or 0 for r in members) or None,
                    "duration_ms": sum(r["duration_ms"] or 0 for r in threads) or None,
                    "flags": flags["threads"],
                    "subagent_flags": flags["subagents"],
                }
            for row in members:
                updates.append((
                    key if (len(members) > 1 or row.get("group_key") or row.get("self_key")) else None,
                    int(row is head),
                    json.dumps(summary, ensure_ascii=False) if row is head and summary else None,
                    json.dumps(row["flags"]),
                    json.dumps(row["signals"], ensure_ascii=False),
                    json.dumps(row["readiness"], ensure_ascii=False) if row["readiness"] else None,
                    None if not row["readiness"] else int(row["readiness"]["ready"]),
                    row["id"],
                ))
        with self._lock:
            self._db.executemany(
                "UPDATE trajectories SET group_key = ?, episode_head = ?, episode = ?, flags = ?, signals = ?, "
                "readiness = ?, ready = ? WHERE id = ?",
                updates,
            )
            self._db.commit()

    # -- full-text step index ------------------------------------------------------------

    def replace_step_text(self, trajectory_ids: set[str], entries: list[tuple[str, int, str]]) -> None:
        """Replace the text index of these trajectories with (trajectory_id, step, body)
        entries (bodies already tokenizer-ready), in one transaction."""
        if not trajectory_ids or not self.text_search:
            return
        with self._lock:
            ids = list(trajectory_ids)
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ", ".join("?" for _ in chunk)
                self._db.execute(f"DELETE FROM step_text WHERE rowid IN (SELECT rowid FROM step_rows WHERE trajectory_id IN ({marks}))", chunk)
                self._db.execute(f"DELETE FROM step_rows WHERE trajectory_id IN ({marks})", chunk)
            for trajectory_id, step, body in entries:
                cursor = self._db.execute(
                    "INSERT INTO step_rows (trajectory_id, step) VALUES (?, ?)", (trajectory_id, step)
                )
                self._db.execute("INSERT INTO step_text (rowid, body) VALUES (?, ?)", (cursor.lastrowid, body))
            self._db.commit()

    def prune_step_text(self) -> int:
        """Drop text of trajectories that no longer exist (a source shrank, or an import
        died after indexing text but before writing its rows)."""
        if not self.text_search:
            return 0
        with self._lock:
            orphans = [row[0] for row in self._db.execute(
                "SELECT r.rowid FROM step_rows r LEFT JOIN trajectories t ON t.id = r.trajectory_id WHERE t.id IS NULL"
            )]
            for start in range(0, len(orphans), 500):
                chunk = orphans[start : start + 500]
                marks = ", ".join("?" for _ in chunk)
                self._db.execute(f"DELETE FROM step_text WHERE rowid IN ({marks})", chunk)
                self._db.execute(f"DELETE FROM step_rows WHERE rowid IN ({marks})", chunk)
            self._db.commit()
        return len(orphans)

    def search_steps(self, match: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
        if not self.text_search:
            raise ValueError(f"full-text search needs SQLite 3.43 or newer (this Python has {sqlite3.sqlite_version})")
        sql = (
            "SELECT r.trajectory_id, r.step, bm25(step_text) AS score FROM step_text "
            "JOIN step_rows r ON r.rowid = step_text.rowid "
            "JOIN trajectories t ON t.id = r.trajectory_id WHERE step_text MATCH ?"
        )
        params: list[Any] = [match]
        if collection:
            sql += " AND t.collection = ?"
            params.append(collection)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def text_indexed(self, trajectory_ids: list[str]) -> set[str]:
        if not trajectory_ids:
            return set()
        with self._lock:
            rows = self._db.execute(
                f"SELECT DISTINCT trajectory_id FROM step_rows WHERE trajectory_id IN ({', '.join('?' for _ in trajectory_ids)})",
                trajectory_ids,
            ).fetchall()
        return {row[0] for row in rows}

    def remove_collection(self, collection: str) -> dict[str, int]:
        """Delete a collection from the index: its trajectory rows, sources, text index,
        Jev results, annotations and reviews. Source files are never touched."""
        with self._lock:
            ids = [row[0] for row in self._db.execute("SELECT id FROM trajectories WHERE collection = ?", (collection,))]
            counts = {"trajectories": len(ids), "reviews": 0, "annotations": 0, "jev_results": 0, "text_rows": 0}
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ", ".join("?" for _ in chunk)
                if self.text_search:
                    rowids = [row[0] for row in self._db.execute(f"SELECT rowid FROM step_rows WHERE trajectory_id IN ({marks})", chunk)]
                    counts["text_rows"] += len(rowids)
                    for part in range(0, len(rowids), 500):
                        piece = rowids[part : part + 500]
                        self._db.execute(f"DELETE FROM step_text WHERE rowid IN ({', '.join('?' for _ in piece)})", piece)
                    self._db.execute(f"DELETE FROM step_rows WHERE trajectory_id IN ({marks})", chunk)
                counts["reviews"] += self._db.execute(f"DELETE FROM reviews WHERE trajectory_id IN ({marks})", chunk).rowcount
                counts["annotations"] += self._db.execute(f"DELETE FROM annotations WHERE trajectory_id IN ({marks})", chunk).rowcount
                counts["jev_results"] += self._db.execute(f"DELETE FROM jev_results WHERE trajectory_id IN ({marks})", chunk).rowcount
            self._db.execute("DELETE FROM trajectories WHERE collection = ?", (collection,))
            counts["sources"] = self._db.execute("DELETE FROM sources WHERE collection = ?", (collection,)).rowcount
            self._db.commit()
        return counts

    def list_sources(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM sources ORDER BY registered_at").fetchall()
        return [
            {**dict(row), "available": Path(row["path"]).exists()} for row in rows
        ]

    def get_trajectory(self, trajectory_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM trajectories WHERE id = ?", (trajectory_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def query_trajectories(
        self,
        *,
        collection: str | None = None,
        outcome: str | None = None,
        flag: str | None = None,
        label: str | None = None,
        reviewed: bool | None = None,
        reviewer: str = "me",
        search: str | None = None,
        task_key: str | None = None,
        group_key: str | None = None,
        roles: Iterable[str] | None = None,
        episodes: bool = False,
        ready: bool | None = None,
        issue: str | None = None,
        jev_flag: str | None = None,
        sort: str = "imported",
        offset: int = 0,
        limit: int | None = 100,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Filtered trajectories; `limit=None` returns every match (exports)."""
        clauses: list[str] = []
        params: list[Any] = [reviewer]
        if collection:
            clauses.append("t.collection = ?")
            params.append(collection)
        if outcome:
            clauses.append("t.outcome_status = ?")
            params.append(outcome)
        if episodes:
            clauses.append("t.episode_head = 1")
        if ready is not None:
            clauses.append("t.ready = ?")
            params.append(int(ready))
        if issue:
            clauses.append("EXISTS (SELECT 1 FROM json_each(json_extract(t.readiness, '$.issues')) WHERE json_extract(json_each.value, '$.key') = ?)")
            params.append(issue)
        if jev_flag:
            # Steps Jev flagged; for an episode, in any member (segments, subagents).
            flagged = "json_extract(m.jev_summary, '$.flag_counts.\"' || ? || '\"') > 0"
            if episodes:
                clauses.append(
                    "EXISTS (SELECT 1 FROM trajectories m WHERE m.collection = t.collection "
                    "AND COALESCE(m.group_key, m.id) = COALESCE(t.group_key, t.id) AND " + flagged + ")"
                )
            else:
                clauses.append("EXISTS (SELECT 1 FROM trajectories m WHERE m.id = t.id AND " + flagged + ")")
            params.append(jev_flag)
        if flag:
            if episodes:
                # An episode matches when any member has the flag (subagent-only flags too).
                clauses.append(
                    "(EXISTS (SELECT 1 FROM json_each(COALESCE(json_extract(t.episode, '$.flags'), t.flags)) WHERE json_each.value = ?) "
                    "OR EXISTS (SELECT 1 FROM json_each(COALESCE(json_extract(t.episode, '$.subagent_flags'), '[]')) WHERE json_each.value = ?))"
                )
                params.extend([flag, flag])
            else:
                clauses.append("EXISTS (SELECT 1 FROM json_each(t.flags) WHERE json_each.value = ?)")
                params.append(flag)
        if label:
            if episodes:
                clauses.append("EXISTS (SELECT 1 FROM json_each(r.label_sets) a, json_each(a.value) b WHERE b.value = ?)")
            else:
                clauses.append("EXISTS (SELECT 1 FROM json_each(r.labels) WHERE json_each.value = ?)")
            params.append(label)
        if reviewed is True:
            clauses.append("r.reviewed_at IS NOT NULL")
        elif reviewed is False:
            clauses.append("r.reviewed_at IS NULL")
        if search:
            clauses.append("(t.title LIKE ? OR t.instruction LIKE ? OR t.run_id LIKE ? OR t.task_id LIKE ?)")
            params.extend([f"%{search}%"] * 4)
        if task_key:
            clauses.append("t.task_key = ?")
            params.append(task_key)
        if group_key:
            clauses.append("t.group_key = ?")
            params.append(group_key)
        role_list = [role for role in (roles or []) if role]
        if role_list:
            clauses.append(
                "COALESCE(t.group_role, 'main') IN (" + ", ".join("?" for _ in role_list) + ")"
            )
            params.extend(role_list)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        steps = "COALESCE(json_extract(t.episode, '$.steps'), t.steps)" if episodes else "t.steps"
        errors = "COALESCE(json_extract(t.episode, '$.tool_errors'), t.tool_errors)" if episodes else "t.tool_errors"
        order = {
            "imported": "t.imported_at DESC, t.rowid",
            "steps": f"{steps} DESC",
            "errors": f"{errors} DESC",
            "title": "t.title",
            "task": "t.task_key, t.group_key, t.segment",
            "episode": "t.group_key, CASE COALESCE(t.group_role, 'main') WHEN 'main' THEN 0 WHEN 'segment' THEN 1 ELSE 2 END, t.segment, t.run_id",
        }.get(sort, "t.rowid")
        if episodes:
            # A review of any member (a segment, a subagent) counts for the episode; the
            # latest review supplies the status, labels are pooled.
            base = (
                "FROM trajectories t LEFT JOIN (SELECT m.collection AS c, COALESCE(m.group_key, m.id) AS ek, "
                "MAX(x.updated_at) AS reviewed_at, x.human_status AS human_status, "
                "json_group_array(json(x.labels)) AS label_sets FROM reviews x "
                "JOIN trajectories m ON m.id = x.trajectory_id WHERE x.reviewer = ? GROUP BY c, ek) r "
                "ON r.c = t.collection AND r.ek = COALESCE(t.group_key, t.id) " + where
            )
            select = "t.*, r.human_status AS review_status, r.label_sets AS review_label_sets, r.reviewed_at AS reviewed_at"
        else:
            base = (
                "FROM trajectories t LEFT JOIN (SELECT trajectory_id, human_status, labels, updated_at AS reviewed_at "
                "FROM reviews WHERE reviewer = ?) r ON r.trajectory_id = t.id " + where
            )
            select = "t.*, r.human_status AS review_status, r.labels AS review_labels, r.reviewed_at AS reviewed_at"
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
            if limit is None:
                rows = self._db.execute(f"SELECT {select} {base} ORDER BY {order}", params).fetchall()
            else:
                rows = self._db.execute(
                    f"SELECT {select} {base} ORDER BY {order} LIMIT ? OFFSET ?",
                    params + [min(1000, max(1, limit)), max(0, offset)],
                ).fetchall()
        items = [self._decode(row) for row in rows]
        for item in items:
            if "review_label_sets" in item:
                sets = item.pop("review_label_sets") or []
                item["review_labels"] = sorted({label for labels in sets if isinstance(labels, list) for label in labels})
        return total, items

    def all_trajectories(
        self, collection: str | None = None, *, heads_only: bool = False, columns: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        """Every trajectory row (optionally only some columns, which is much faster on
        large collections than decoding the signals JSON of each row)."""
        known = {name.strip() for name in TRAJECTORY_COLUMNS.split(",")}
        wanted = [name for name in (columns or []) if name in known]
        sql = "SELECT " + (", ".join(wanted) if wanted else "*") + " FROM trajectories"
        clauses: list[str] = []
        params: list[Any] = []
        if collection:
            clauses.append("collection = ?")
            params.append(collection)
        if heads_only:
            clauses.append("episode_head = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [self._decode(row) for row in rows]

    def collections(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                # Outcome counts are per episode (head rows); `total` counts every trajectory.
                "SELECT collection, COUNT(*) AS total, SUM(episode_head) AS episodes, "
                "SUM(episode_head AND outcome_status = 'pass') AS pass, SUM(episode_head AND outcome_status = 'fail') AS fail, "
                "SUM(episode_head AND outcome_status = 'partial') AS partial, SUM(episode_head AND outcome_status = 'error') AS error, "
                "SUM(episode_head AND outcome_status = 'unknown') AS unknown, "
                "COUNT(DISTINCT adapter_id) AS adapters, GROUP_CONCAT(DISTINCT adapter_id) AS adapter_ids, "
                "MAX(imported_at) AS imported_at "
                "FROM trajectories GROUP BY collection ORDER BY MAX(imported_at) DESC"
            ).fetchall()
            # Reviewed episodes (a review of any member counts once), matching `episodes`.
            reviewed = dict(
                self._db.execute(
                    "SELECT t.collection, COUNT(DISTINCT COALESCE(t.group_key, t.id)) FROM reviews r JOIN trajectories t "
                    "ON t.id = r.trajectory_id GROUP BY t.collection"
                ).fetchall()
            )
        return [{**dict(row), "reviewed": reviewed.get(row["collection"], 0)} for row in rows]

    def set_jev_summary(self, trajectory_id: str, summary: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE trajectories SET jev_summary = ? WHERE id = ?",
                (json.dumps(summary, ensure_ascii=False), trajectory_id),
            )
            self._db.commit()

    def _decode(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in (*JSON_COLUMNS, "review_labels", "review_label_sets"):
            if isinstance(item.get(key), str):
                try:
                    item[key] = json.loads(item[key])
                except json.JSONDecodeError:
                    pass
        for key in ("infra_failure", "ready"):
            if item.get(key) is not None:
                item[key] = bool(item[key])
        return item

    # -- reviews ----------------------------------------------------------------------

    def get_review(self, trajectory_id: str, reviewer: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM reviews WHERE trajectory_id = ? AND reviewer = ?",
                (trajectory_id, reviewer),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["labels"] = json.loads(item["labels"] or "[]")
        item["label_details"] = json.loads(item.get("label_details") or "{}")
        item["blind"] = bool(item["blind"])
        return item

    def save_review(self, trajectory_id: str, reviewer: str, fields: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_review(trajectory_id, reviewer) or {}
        merged = {key: existing.get(key) for key in REVIEW_FIELDS}
        merged.update({key: value for key, value in fields.items() if key in REVIEW_FIELDS})
        merged["labels"] = json.dumps(list(merged.get("labels") or []), ensure_ascii=False)
        merged["label_details"] = json.dumps(merged.get("label_details") or {}, ensure_ascii=False)
        merged["blind"] = int(bool(merged.get("blind")))
        stamp = now()
        with self._lock:
            self._db.execute(
                "INSERT INTO reviews (trajectory_id, reviewer, "
                + ", ".join(REVIEW_FIELDS)
                + ", created_at, updated_at) VALUES (?, ?, "
                + ", ".join("?" for _ in REVIEW_FIELDS)
                + ", ?, ?) ON CONFLICT(trajectory_id, reviewer) DO UPDATE SET "
                + ", ".join(f"{key}=excluded.{key}" for key in REVIEW_FIELDS)
                + ", updated_at=excluded.updated_at",
                (trajectory_id, reviewer, *[merged[key] for key in REVIEW_FIELDS], existing.get("created_at") or stamp, stamp),
            )
            self._db.commit()
        return self.get_review(trajectory_id, reviewer) or {}

    def delete_review(self, trajectory_id: str, reviewer: str) -> None:
        with self._lock:
            self._db.execute(
                "DELETE FROM reviews WHERE trajectory_id = ? AND reviewer = ?", (trajectory_id, reviewer)
            )
            self._db.commit()

    def reviews(self, collection: str | None = None, reviewer: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT r.*, t.collection, t.title, t.run_id, t.task_key, t.task_id, t.model, "
            "t.harness, t.adapter_id, t.path, t.locator, t.outcome_status, t.outcome_score, "
            "t.outcome_reason, t.steps, t.flags, t.group_role "
            "FROM reviews r JOIN trajectories t ON t.id = r.trajectory_id"
        )
        clauses, params = [], []
        if collection:
            clauses.append("t.collection = ?")
            params.append(collection)
        if reviewer:
            clauses.append("r.reviewer = ?")
            params.append(reviewer)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY r.updated_at"
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("labels", "flags", "locator", "label_details"):
                if isinstance(item.get(key), str):
                    item[key] = json.loads(item[key])
            item["blind"] = bool(item["blind"])
            result.append(item)
        return result

    # -- step annotations -------------------------------------------------------------

    def annotations(self, trajectory_id: str, reviewer: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM annotations WHERE trajectory_id = ?"
        params: list[Any] = [trajectory_id]
        if reviewer:
            sql += " AND reviewer = ?"
            params.append(reviewer)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY step, id", params).fetchall()
        return [dict(row) for row in rows]

    def save_annotation(self, trajectory_id: str, reviewer: str, step: int, note: str, label: str | None, annotation_id: int | None = None) -> dict[str, Any]:
        stamp = now()
        with self._lock:
            if annotation_id is None:
                cursor = self._db.execute(
                    "INSERT INTO annotations (trajectory_id, step, reviewer, note, label, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (trajectory_id, step, reviewer, note, label, stamp, stamp),
                )
                annotation_id = cursor.lastrowid
            else:
                cursor = self._db.execute(
                    "UPDATE annotations SET note = ?, label = ?, updated_at = ? WHERE id = ? AND trajectory_id = ? AND reviewer = ?",
                    (note, label, stamp, annotation_id, trajectory_id, reviewer),
                )
                if cursor.rowcount == 0:
                    raise KeyError(annotation_id)
            self._db.commit()
            row = self._db.execute("SELECT * FROM annotations WHERE id = ?", (annotation_id,)).fetchone()
        return dict(row)

    def delete_annotation(self, trajectory_id: str, reviewer: str, annotation_id: int) -> None:
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM annotations WHERE id = ? AND trajectory_id = ? AND reviewer = ?", (annotation_id, trajectory_id, reviewer)
            )
            self._db.commit()
        if cursor.rowcount == 0:
            raise KeyError(annotation_id)

    # -- jev cache --------------------------------------------------------------------

    def get_jev(self, trajectory_id: str, kind: str, version: str, fingerprint: str | None) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM jev_results WHERE trajectory_id = ? AND kind = ?",
                (trajectory_id, kind),
            ).fetchone()
        if row is None or row["version"] != version or (fingerprint and row["fingerprint"] != fingerprint):
            return None
        return json.loads(row["result"])

    def put_jev(
        self,
        trajectory_id: str,
        kind: str,
        version: str,
        fingerprint: str | None,
        model: str | None,
        input_tokens: int | None,
        result: dict[str, Any],
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO jev_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trajectory_id,
                    kind,
                    version,
                    fingerprint,
                    model,
                    input_tokens,
                    json.dumps(result, ensure_ascii=False),
                    now(),
                ),
            )
            self._db.commit()

    def jev_usage(self) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(input_tokens), 0) FROM jev_results"
            ).fetchone()
        return {"results": row[0], "input_tokens": row[1]}
