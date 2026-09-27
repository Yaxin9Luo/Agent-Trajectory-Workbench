"""Adapter registry and source discovery.

An adapter implements:

    adapter_id: str
    label: str
    detect(path) -> bool                          # cheap sniff of a file or directory
    iter_runs(path) -> Iterator[(locator, run)]   # every trajectory in the source
    load(path, locator) -> NormalizedRun          # one trajectory again, by locator
    fingerprint(path) -> tuple                    # optional; defaults to stat of path

`locator` is None for single-trajectory sources, otherwise a small JSON-able dict such as
`{"offset": 123, "row": 4, "key": "sample-id"}`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from trajectory_workbench.adapters.atif import AtifAdapter
from trajectory_workbench.adapters.claude_code import ClaudeCodeAdapter
from trajectory_workbench.adapters.codex import CodexAdapter
from trajectory_workbench.adapters.moh_v1 import MohV1Adapter
from trajectory_workbench.adapters.openai_chat import OpenAIChatAdapter
from trajectory_workbench.adapters.tracelab import TraceLabTrialAdapter


DIRECTORY_ADAPTERS = [MohV1Adapter(), TraceLabTrialAdapter(), AtifAdapter()]
FILE_ADAPTERS = [ClaudeCodeAdapter(), CodexAdapter(), AtifAdapter(), OpenAIChatAdapter()]
ADAPTERS: dict[str, Any] = {
    adapter.adapter_id: adapter for adapter in DIRECTORY_ADAPTERS + FILE_ADAPTERS
}
SKIPPED_DIRECTORIES = {"node_modules", "__pycache__", ".git", ".venv", "venv", "images", "media"}
MAX_DEPTH = 8


def get_adapter(adapter_id: str) -> Any:
    try:
        return ADAPTERS[adapter_id]
    except KeyError as error:
        raise ValueError(f"Unknown adapter: {adapter_id}") from error


def detect(path: Path) -> Any | None:
    candidates = DIRECTORY_ADAPTERS if path.is_dir() else FILE_ADAPTERS
    for adapter in candidates:
        try:
            if adapter.detect(path):
                return adapter
        except OSError:
            continue
    return None


def discover(root: Path) -> Iterator[tuple[Any, Path]]:
    """Yield (adapter, source) for every recognizable trajectory source under root.

    A directory claimed by a directory adapter is not searched further, so the nested
    copies inside TraceLab and Harbor trial directories are not imported twice.
    """
    root = root.expanduser().resolve()
    adapter = detect(root)
    if adapter is not None:
        yield adapter, root
        # A Claude Code session keeps its subagent transcripts in `<session>/subagents/`.
        companions = root.with_suffix("") / "subagents"
        if adapter.adapter_id == "claude-code" and companions.is_dir():
            yield from _walk(companions, MAX_DEPTH)
        return
    if not root.is_dir():
        return
    yield from _walk(root, 0)


def _walk(directory: Path, depth: int) -> Iterator[tuple[Any, Path]]:
    if depth > MAX_DEPTH:
        return
    try:
        children = sorted(directory.iterdir())
    except OSError:
        return
    for child in children:
        if child.name.startswith(".") or child.name in SKIPPED_DIRECTORIES:
            continue
        if child.is_symlink():
            continue
        if child.is_dir():
            adapter = detect(child)
            if adapter is not None:
                yield adapter, child
            else:
                yield from _walk(child, depth + 1)
        elif child.suffix in {".jsonl", ".json"}:
            if child.name in {"results.json", "result.json", "manifest.json", "run_metadata.json"}:
                continue
            adapter = detect(child)
            if adapter is not None:
                yield adapter, child


def fingerprint(adapter: Any, path: Path) -> tuple:
    method = getattr(adapter, "fingerprint", None)
    if callable(method):
        return method(path)
    stat = path.stat()
    parts: list[tuple[str, int, int]] = [(path.name, stat.st_mtime_ns, stat.st_size)]
    if path.is_dir():
        for name in ("dialog.jsonl", "results.json", "result.json", "agent/trajectory.json"):
            item = path / name
            if item.is_file():
                item_stat = item.stat()
                parts.append((name, item_stat.st_mtime_ns, item_stat.st_size))
    return tuple(parts)


__all__ = [
    "ADAPTERS",
    "AtifAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "MohV1Adapter",
    "OpenAIChatAdapter",
    "TraceLabTrialAdapter",
    "detect",
    "discover",
    "fingerprint",
    "get_adapter",
]
