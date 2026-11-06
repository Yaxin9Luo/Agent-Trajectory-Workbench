"""Read a slides-v4 reward scoring root into rows for the `v4_rewards` table.

A scoring root holds `work/<task>/reward.json`, one per deck, plus the stills the scorer
measured (`check/shots/<task>/<viewport>/NN.png`) and, optionally, the raw L3 judge output
(`l3/score-raw.json`). Rows are matched to trajectories later, at read time, by task id
and source-path prefix (see `store.V4_MATCH`); nothing here touches trajectory rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VIEWPORTS = ("1920x1080", "1366x768")
SCALARS = (
    "R", "masked", "tier", "tier_name", "a_L3", "m", "p", "q", "n_minor", "n_l2_normal", "eval_total",
    "scorer", "rubric", "env", "seconds", "timing", "failed_stage", "unknown", "deck_fault", "slides", "derived",
)
STILL_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def failed_items(items: Any) -> list[dict[str, Any]]:
    """Ledger items a reader should see: failed ones, without `monitor` severity."""
    return [
        item for item in (items or [])
        if isinstance(item, dict) and item.get("pass") is False and item.get("severity") != "monitor"
    ]


def has_errors(reward: dict[str, Any]) -> bool:
    timing = reward.get("timing") if isinstance(reward.get("timing"), dict) else {}
    return bool(
        reward.get("masked") or reward.get("failed_stage") or reward.get("deck_fault")
        or timing.get("retried") or timing.get("L3_dropped")
    )


def stills(work_dir: Path, task_id: str) -> dict[str, list[str]]:
    """Per viewport, the still paths relative to the work dir, in slide order."""
    found: dict[str, list[str]] = {}
    for viewport in VIEWPORTS:
        folder = work_dir / "check" / "shots" / task_id / viewport
        if folder.is_dir():
            files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in STILL_SUFFIXES)
            found[viewport] = [p.relative_to(work_dir).as_posix() for p in files]
    return found


def read_deck(work_dir: Path, runs_root: str) -> dict[str, Any]:
    task_id = work_dir.name
    reward_path = work_dir / "reward.json"
    reward = json.loads(reward_path.read_text(encoding="utf-8"))
    if not isinstance(reward, dict):
        raise ValueError("reward.json is not an object")
    row: dict[str, Any] = {key: reward.get(key) for key in SCALARS}
    row.update(
        task_id=task_id,
        artifact_id=reward.get("artifact_id"),
        runs_root=runs_root,
        work_dir=str(work_dir),
        reward_path=str(reward_path),
        # Masked is not zero: R stays null and the reasons go to the reader.
        masked=bool(reward.get("masked")) or reward.get("R") is None,
        failed=failed_items(reward.get("ledger")),
        derived_items=failed_items(reward.get("derived_items")),
        has_errors=has_errors(reward),
        stills=stills(work_dir, task_id),
    )
    raw = work_dir / "l3" / "score-raw.json"
    if raw.is_file():
        try:
            judged = json.loads(raw.read_text(encoding="utf-8"))
            critique = judged.get("critique") if isinstance(judged, dict) else None
            if isinstance(critique, str) and critique.strip():
                row["critique"] = critique
        except (OSError, json.JSONDecodeError):
            pass
    return row


def read_root(root: Path, runs_root: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    work = root / "work"
    if not work.is_dir():
        raise ValueError(f"No work/ directory under {root}")
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for work_dir in sorted(work.iterdir()):
        if not work_dir.is_dir() or work_dir.name.startswith("_"):
            continue
        if not (work_dir / "reward.json").is_file():
            errors.append({"task_id": work_dir.name, "error": "no reward.json"})
            continue
        try:
            row = read_deck(work_dir, runs_root)
        except (OSError, ValueError) as error:
            errors.append({"task_id": work_dir.name, "error": str(error)[:300]})
            continue
        if row["artifact_id"] and row["artifact_id"] != row["task_id"]:
            errors.append({"task_id": row["task_id"], "error": f"artifact_id is {row['artifact_id']!r}; matched by directory name"})
        rows.append(row)
    return rows, errors
