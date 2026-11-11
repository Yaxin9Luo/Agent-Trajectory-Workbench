"""What the agent built, step by step.

A design trajectory is read by what it produced, not only by what it typed: this module
replays the file-writing calls of a run (Write, Edit, MultiEdit, str_replace, apply_patch)
into the successive versions of each file, so a reader can render the deliverable as it
stood after any step. From the same pass it derives the design behaviours of the run
(changing the deliverable, rendering it, looking at the result) and, given the reward's
failed checks, the step that last changed the text a check points at and whether the agent
looked at that slide afterwards.

Everything here is computed from the transcript. Changes made outside the edit tools (a
shell redirect, a script) are not replayed: the file is then marked inexact, and when the
kept deliverable is on disk the last version is compared with it.
"""

from __future__ import annotations

import difflib
import html
import re
import posixpath
from pathlib import Path
from typing import Any

from trajectory_workbench.scriptlet import Aborted, Scriptlet, Unsupported

HTML_SUFFIXES = (".html", ".htm")
PATCH_START = re.compile(r"^\s*\*\*\* Begin Patch")
PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+?)\s*$")
# A shell command that may write a file without going through an edit tool.
SHELL_WRITE = re.compile(r">>?\s*\S|\bsed\s+(-\S+\s+)*-i|\btee\b|\bcp\s|\bmv\s|\brm\s|\bopen\([^)]*['\"][wa]|write_text|writeFile|\bpatch\b|\bgit\s+(apply|checkout|restore)\b")
# A shell command that renders the deliverable or takes screenshots of it.
RENDER = re.compile(r"playwright|puppeteer|chrom(e|ium)|selenium|screenshot|webkit|wkhtmlto|\.png\b|slides_workbench", re.IGNORECASE)
SHELL_NAMES = {"bash", "shell", "local_shell", "exec", "exec_command", "run_shell_command"}
READ_NAMES = {"read", "view", "read_file"}
NUMBERED = re.compile(r"^\s*(\d+)(?:→|\t)(.*)$")
# command … <<'TAG' ⏎ body ⏎ TAG
HEREDOC = re.compile(r"(?P<head>[^\n;&|]*?)<<-?[ \t]*(?P<quote>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)(?P<rest>[^\n]*)\n(?P<body>.*?)\n[ \t]*(?P=tag)[ \t]*(?=\n|$)", re.DOTALL)
CAT_TO = re.compile(r"^\s*(?:cat|tee)\s*(?P<mode>>>?|-a\s+)?\s*(?P<path>[^\s<>|;&]+)\s*$")
PYTHON_STDIN = re.compile(r"(?:^|[\s/])python[0-9.]*\s*(?:-u\s*)?-?\s*$")
# sed -i 's<sep>old<sep>new<sep>[g]' file, with one expression and one file.
SED_INPLACE = re.compile(r"(?:^|&&|;|\n)\s*sed\s+-i(?:\s+-e)?\s+(?P<quote>['\"])s(?P<sep>[|/#,@])(?P<old>.*?)(?P=sep)(?P<new>.*?)(?P=sep)(?P<flags>g?)(?P=quote)\s+(?P<path>[^\s<>|;&]+)", re.DOTALL)
SED_META = re.compile(r"[\\\[\]*.^$&]")
CAT_CONCAT = re.compile(r"(?:^|&&|;|\n)\s*cat\s+(?P<sources>(?:[^\s<>|;&]+\s+)+)>\s*(?P<path>[^\s<>|;&]+)")
CWD_RESET = re.compile(r"Shell cwd was reset to (/\S*)")
CD = re.compile(r"(?:^|&&|;|\n)\s*cd\s+([^\s;&|]+)")
TEXT_KEY = 24
LOOK_STEPS = 8


def _base(name: str) -> str:
    return str(name or "").split("__")[-1].casefold()


def _path_of(tool_input: dict[str, Any]) -> str | None:
    for key in ("file_path", "path", "filename", "target_file"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _pair(item: Any) -> tuple[str, str, bool] | None:
    if not isinstance(item, dict):
        return None
    before = item.get("old_string", item.get("old_str"))
    after = item.get("new_string", item.get("new_str"))
    if isinstance(before, str) and isinstance(after, str):
        return before, after, bool(item.get("replace_all"))
    return None


def _patch_text(tool_input: Any) -> str | None:
    if isinstance(tool_input, str):
        return tool_input if PATCH_START.match(tool_input) else None
    if isinstance(tool_input, dict):
        for key in ("input", "patch", "command"):
            value = tool_input.get(key)
            if isinstance(value, str) and PATCH_START.match(value):
                return value
    return None


def _apply_pairs(content: str, pairs: list[tuple[str, str, bool]]) -> tuple[str, str | None]:
    for before, after, replace_all in pairs:
        if before == "":
            # An edit with no old text creates or prepends; treat as prepend to what is there.
            content = after + content
            continue
        if before not in content:
            return content, "old text not found"
        content = content.replace(before, after) if replace_all else content.replace(before, after, 1)
    return content, None


def _apply_patch_hunks(content: str, lines: list[str]) -> tuple[str, str | None]:
    """An apply_patch "Update File" body: blocks of context (' '), removed ('-') and added
    ('+') lines, separated by '@@' markers. Each block is located by its old lines."""
    blocks: list[list[str]] = [[]]
    for line in lines:
        if line.startswith("@@"):
            blocks.append([])
        elif line.startswith("*** End of File"):
            continue
        else:
            blocks[-1].append(line)
    for block in blocks:
        if not block:
            continue
        old = [line[1:] for line in block if line[:1] in (" ", "-")]
        new = [line[1:] for line in block if line[:1] in (" ", "+")]
        old_text, new_text = "\n".join(old), "\n".join(new)
        if not old_text:
            content = content + ("\n" if content and not content.endswith("\n") else "") + new_text + "\n"
        elif old_text in content:
            content = content.replace(old_text, new_text, 1)
        else:
            return content, "patch context not found"
    return content, None


def _patch_files(text: str) -> list[tuple[str, str, list[str]]]:
    """[(action, path, body lines)] of an apply_patch text."""
    files: list[tuple[str, str, list[str]]] = []
    for line in text.splitlines():
        header = PATCH_FILE.match(line)
        if header:
            files.append((header.group(1).lower(), header.group(2), []))
        elif line.startswith("*** "):
            continue
        elif files:
            files[-1][2].append(line)
    return files


def _numbered_text(text: str) -> str | None:
    """The file content inside a numbered listing (`cat -n` style, as file-read tools
    return it), or None when the text is not a whole listing starting at line 1."""
    lines = text.splitlines()
    out: list[str] = []
    expected = 1
    for line in lines:
        match = NUMBERED.match(line)
        if not match:
            if out and not line.strip():
                continue
            return None
        if int(match.group(1)) != expected:
            return None
        expected += 1
        out.append(match.group(2))
    return "\n".join(out) + "\n" if len(out) >= 3 else None


def _line_delta(before: str, after: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


class Replay:
    """The versions of every file the run wrote through its edit tools."""

    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.cwd: str | None = None

    def _file(self, path: str) -> dict[str, Any]:
        return self.files.setdefault(path, {"path": path, "contents": [], "versions": [], "failed": [], "notes": [], "exact": True})

    def _commit(self, path: str, content: str, tool: dict[str, Any], op: str) -> None:
        entry = self._file(path)
        previous = entry["contents"][-1] if entry["contents"] else ""
        if entry["contents"] and content == previous:
            return
        added, removed = _line_delta(previous, content)
        entry["contents"].append(content)
        entry["versions"].append({
            "n": len(entry["contents"]),
            "step": tool["step"],
            "tool_id": tool["id"],
            "op": op,
            "bytes": len(content.encode("utf-8")),
            "lines": content.count("\n") + (1 if content and not content.endswith("\n") else 0),
            "added": added,
            "removed": removed,
        })

    def _fail(self, path: str, tool: dict[str, Any], reason: str, *, inexact: bool = True) -> None:
        entry = self._file(path)
        entry["failed"].append({"step": tool["step"], "tool_id": tool["id"], "reason": reason})
        if inexact:
            entry["exact"] = False

    def feed(self, tool: dict[str, Any]) -> list[str]:
        """Apply one call; returns the paths it changed."""
        result = tool.get("result") or {}
        tool_input = tool.get("input")
        patch = _patch_text(tool_input)
        if patch is not None:
            return [] if result.get("is_error") else self._feed_patch(tool, patch)
        if not isinstance(tool_input, dict):
            return []
        path = _path_of(tool_input)
        name = _base(tool.get("name"))
        if name in SHELL_NAMES or (path is None and isinstance(tool_input.get("command"), str)):
            return self._shell(tool, str(tool_input.get("command") or tool_input.get("cmd") or ""))
        if path is None:
            return []
        if name in READ_NAMES:
            self._read(tool, path, tool_input, result)
            return []
        if result.get("is_error"):
            return []
        before = self.files[path]["contents"][-1] if path in self.files and self.files[path]["contents"] else None
        content = tool_input.get("content", tool_input.get("file_text"))
        if isinstance(content, str) and not isinstance(tool_input.get("edits"), list) and _pair(tool_input) is None:
            self._commit(path, content, tool, "write")
            return [path]
        pairs: list[tuple[str, str, bool]] | None = None
        op = "edit"
        if isinstance(tool_input.get("edits"), list):
            found = [_pair(item) for item in tool_input["edits"]]
            pairs = [item for item in found if item] if found and all(found) else None
            op = "multiedit"
        elif _pair(tool_input):
            pairs = [_pair(tool_input)]  # type: ignore[list-item]
        elif tool_input.get("command") == "insert" and isinstance(tool_input.get("new_str"), str):
            if before is None:
                self._fail(path, tool, "the file was not written through an edit tool before this change")
                return []
            lines = before.splitlines(keepends=True)
            at = max(0, min(len(lines), int(tool_input.get("insert_line") or 0)))
            self._commit(path, "".join(lines[:at]) + tool_input["new_str"].rstrip("\n") + "\n" + "".join(lines[at:]), tool, "insert")
            return [path]
        if pairs is None:
            return []
        if before is None:
            self._fail(path, tool, "the file was not written through an edit tool before this change")
            return []
        after, error = _apply_pairs(before, pairs)
        if error:
            self._fail(path, tool, error)
            return []
        self._commit(path, after, tool, op)
        return [path]

    def _feed_patch(self, tool: dict[str, Any], text: str) -> list[str]:
        changed = []
        for action, path, body in _patch_files(text):
            if action == "add":
                self._commit(path, "\n".join(line[1:] if line.startswith("+") else line for line in body) + "\n", tool, "patch")
                changed.append(path)
            elif action == "update":
                if path not in self.files or not self.files[path]["contents"]:
                    self._fail(path, tool, "the file was not written through an edit tool before this change")
                    continue
                after, error = _apply_patch_hunks(self.files[path]["contents"][-1], body)
                if error:
                    self._fail(path, tool, error)
                    continue
                self._commit(path, after, tool, "patch")
                changed.append(path)
            else:
                self._commit(path, "", tool, "delete")
                changed.append(path)
        return changed

    def _read(self, tool: dict[str, Any], path: str, tool_input: dict[str, Any], result: dict[str, Any]) -> None:
        """A whole-file read is evidence of the file's real content at that step."""
        if result.get("is_error") or result.get("images") or any(tool_input.get(key) for key in ("offset", "limit", "view_range", "pages")):
            return
        listed = _numbered_text(result.get("text") or "")
        if listed is None:
            return
        entry = self.files.get(path)
        if entry is None or not entry["contents"]:
            # Created outside the edit tools (shell, script): the read is the first version.
            self._commit(path, listed, tool, "read")
            self.files[path]["exact"] = False
            self.files[path]["notes"].append({"step": tool["step"], "note": "first seen in a file read; it was created outside the edit tools"})
            return
        if entry["contents"][-1].rstrip("\n") != listed.rstrip("\n"):
            # Readers cut long lines and long files: only a listing that is not a prefix-wise
            # cut of the replayed text counts as a real difference.
            replayed = entry["contents"][-1].splitlines()
            seen = listed.splitlines()
            cut = len(seen) <= len(replayed) and all(line == full or (len(line) >= 1000 and full.startswith(line.rstrip("…. "))) for line, full in zip(seen, replayed))
            if not cut:
                entry["exact"] = False
                entry["notes"].append({"step": tool["step"], "note": "a later read of the file differs from the replayed text: something else changed it"})
                self._commit(path, listed, tool, "read")

    def _resolve(self, path: str, cwd: str | None) -> str:
        """An absolute path for what a shell command names. Without a known working
        directory, a relative path is matched against the files already replayed."""
        if path.startswith("/"):
            return posixpath.normpath(path)
        here = posixpath.normpath(posixpath.join(cwd, path)) if cwd and cwd.startswith("/") else None
        if here in self.files:
            return here
        # The tracked directory can be wrong (a `cd` inside a subshell, a reset the
        # transcript does not show): a relative name that fits exactly one replayed file
        # is that file.
        tail = "/" + posixpath.normpath(path).lstrip("./")
        matches = [known for known in self.files if known.endswith(tail)]
        if len(matches) == 1:
            return matches[0]
        return here or posixpath.normpath(posixpath.join(self.cwd or "/", path))

    def _text(self, path: str) -> str | None:
        entry = self.files.get(path)
        return entry["contents"][-1] if entry and entry["contents"] else None

    def _shell(self, tool: dict[str, Any], command: str) -> list[str]:
        """Follow the file writes a shell command makes when they can be read off the
        command: a here-document written with cat or tee, files concatenated into another,
        and an inline Python script made of string edits (see scriptlet.py). A command that
        seems to change a replayed file in any other way marks that file inexact."""
        if not command:
            return []
        changed: list[str] = []
        handled_spans: list[tuple[int, int]] = []
        failed = bool((tool.get("result") or {}).get("is_error"))

        def cwd_at(position: int) -> str | None:
            cwd = self.cwd
            for match in CD.finditer(command, 0, position):
                target = match.group(1).strip("'\"")
                cwd = posixpath.normpath(posixpath.join(cwd or "/", target)) if not target.startswith("/") else posixpath.normpath(target)
            return cwd

        for match in HEREDOC.finditer(command):
            head, body = match.group("head"), match.group("body")
            cwd = cwd_at(match.start())
            target = CAT_TO.match(head)
            if target:
                handled_spans.append(match.span())
                if failed:
                    continue
                path = self._resolve(target.group("path").strip("'\""), cwd)
                append = (target.group("mode") or "").strip() in (">>", "-a")
                self._commit(path, ((self._text(path) or "") if append else "") + body + "\n", tool, "shell")
                changed.append(path)
            elif PYTHON_STDIN.search(head) and not match.group("rest").strip().startswith(("|", ">")):
                handled_spans.append(match.span())
                if failed:
                    continue
                changed.extend(self._script(tool, body, cwd))
        for match in CAT_CONCAT.finditer(command):
            if any(start <= match.start() < end for start, end in handled_spans):
                continue
            cwd = cwd_at(match.start())
            sources = [self._text(self._resolve(source.strip("'\""), cwd)) for source in match.group("sources").split()]
            path = self._resolve(match.group("path").strip("'\""), cwd)
            handled_spans.append(match.span())
            if failed:
                continue
            if all(source is not None for source in sources):
                self._commit(path, "".join(sources), tool, "shell")  # type: ignore[arg-type]
                changed.append(path)
            elif path in self.files:
                self._inexact(path, tool, "files were concatenated into this one, and not all of them were replayed")
        for match in SED_INPLACE.finditer(command):
            cwd = cwd_at(match.start())
            path = self._resolve(match.group("path").strip("'\""), cwd)
            old, new = match.group("old"), match.group("new")
            text = self._text(path)
            handled_spans.append(match.span())
            if failed or text is None:
                continue
            # Only a substitution of plain text is replayed: sed patterns are regular
            # expressions with their own rules.
            if SED_META.search(old) or SED_META.search(new) or "\n" in old or old not in text:
                self._inexact(path, tool, "a sed command changed the file; only plain-text substitutions are replayed")
                continue
            if match.group("flags"):
                self._commit(path, text.replace(old, new), tool, "shell")
            else:
                self._commit(path, "\n".join(line.replace(old, new, 1) for line in text.split("\n")), tool, "shell")
            changed.append(path)
        # A `cd` outlives the command (the shell session is kept between calls) unless the
        # harness says it reset the directory, which replay() reads from the result.
        if not failed:
            self.cwd = cwd_at(len(command))
        # Whatever is left of the command: does it look like it writes a replayed file?
        rest = command
        for start, end in sorted(handled_spans, reverse=True):
            rest = rest[:start] + " " + rest[end:]
        if SHELL_WRITE.search(rest):
            for path, entry in self.files.items():
                name = re.escape(path.rsplit("/", 1)[-1])
                if entry["contents"] and re.search(
                    r"((?:^|\s)\d?>>?\s*[\w./~-]*" + name + r"|sed\s+(-\S+\s+)*-i[^|;&\n]*" + name + r"|tee\s+(-a\s+)?[^\s|;&]*" + name
                    + r"|\b(rm|mv|cp)\s[^|;&\n]*" + name + r"|open\([^)]*" + name + r"[^)]*['\"][wa]|" + name + r"['\"]\)\.write_text)", rest
                ):
                    self._inexact(path, tool, "a shell command may have changed the file; that change is not replayed")
        return changed

    def _inexact(self, path: str, tool: dict[str, Any], note: str) -> None:
        entry = self._file(path)
        entry["exact"] = False
        entry["notes"].append({"step": tool["step"], "note": note})

    def _script(self, tool: dict[str, Any], source: str, cwd: str | None) -> list[str]:
        """Evaluate an inline edit script against the replayed files (never executed)."""
        pending: dict[str, str] = {}

        def read(path: str) -> str | None:
            resolved = self._resolve(path, cwd)
            return pending.get(resolved, self._text(resolved))

        def write(path: str, text: str, append: bool) -> None:
            resolved = self._resolve(path, cwd)
            pending[resolved] = (pending.get(resolved, self._text(resolved) or "") if append else "") + text

        try:
            Scriptlet(read, write).run(source)
        except Aborted:
            # The real script ran without error, so stopping here means the replayed files
            # no longer match what it saw.
            for path in {self._resolve(name, cwd) for name in re.findall(r"['\"]([^'\"\n]+\.[A-Za-z0-9]{1,5})['\"]", source)} & set(self.files):
                self._inexact(path, tool, "an inline script changed the file, but it does not apply to the replayed text")
            return []
        except Unsupported as error:
            for path in {self._resolve(name, cwd) for name in re.findall(r"['\"]([^'\"\n]+\.[A-Za-z0-9]{1,5})['\"]", source)} & set(self.files):
                if re.search(r"['\"]w['\"]|write_text", source):
                    self._inexact(path, tool, f"an inline script may have changed the file; it is not replayed ({error})")
            return []
        for path, text in pending.items():
            self._commit(path, text, tool, "script")
        return list(pending)


def _is_deliverable(path: str, deliverable_names: set[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    if deliverable_names:
        return name in deliverable_names
    return "/output/" in path or path.endswith(HTML_SUFFIXES)


def _slide_of(path: str) -> int | None:
    """The slide a screenshot file name points at: s2.png, slide-03.png, 02.png."""
    name = path.rsplit("/", 1)[-1]
    match = re.search(r"(?:^|[^a-z])(?:s|slide|page|p)[-_ ]?0*(\d{1,3})(?!\d)", name, re.IGNORECASE) or re.match(r"0*(\d{1,3})\.[a-z]+$", name, re.IGNORECASE)
    return int(match.group(1)) if match else None


def replay(run: Any) -> Replay:
    """Replay the run and record its design behaviours in order:

    change  a call that changed a deliverable file
    support a call that changed another file (a check script, notes)
    render  a shell command that renders or screenshots
    look    a call whose result carries an image (the agent saw a picture)
    """
    state = Replay()
    task = getattr(run, "task", None) or {}
    deliverable_names = {str(item.get("path", "")).rsplit("/", 1)[-1] for item in task.get("deliverables") or [] if isinstance(item, dict)}
    deliverable_names.discard("")
    for tool in getattr(run, "tools", None) or []:
        # The shell says where it is ("Shell cwd was reset to /workspace"); relative paths
        # in later commands are resolved against that.
        if state.cwd is None and isinstance(tool.get("input"), dict):
            path = _path_of(tool["input"]) or ""
            if "/output/" in path:
                state.cwd = path.split("/output/")[0] or "/"
        changed = state.feed(tool)
        reset = CWD_RESET.search((tool.get("result") or {}).get("text") or "")
        if reset:
            state.cwd = reset.group(1)
        for path in changed:
            state.events.append({"step": tool["step"], "tool_id": tool["id"], "kind": "change" if _is_deliverable(path, deliverable_names) else "support", "path": path})
        result = tool.get("result") or {}
        tool_input = tool.get("input") if isinstance(tool.get("input"), dict) else {}
        command = str(tool_input.get("command") or tool_input.get("cmd") or "")
        if (_base(tool.get("name")) in SHELL_NAMES or command) and RENDER.search(command) and not _patch_text(tool.get("input")):
            state.events.append({"step": tool["step"], "tool_id": tool["id"], "kind": "render", "failed": bool(result.get("is_error"))})
        if result.get("images"):
            path = _path_of(tool_input) or ""
            state.events.append({"step": tool["step"], "tool_id": tool["id"], "kind": "look", "path": path, "slide": _slide_of(path) if path else None, "images": len(result["images"])})
    for entry in state.files.values():
        entry["deliverable"] = _is_deliverable(entry["path"], deliverable_names)
    return state


def behaviour(events: list[dict[str, Any]], last_step: int) -> dict[str, Any]:
    """Counts a reader compares across runs. A cycle is: the deliverable changes, the agent
    then looks at a picture, and changes the deliverable again."""
    changes = [event for event in events if event["kind"] == "change"]
    looks = [event for event in events if event["kind"] == "look"]
    renders = [event for event in events if event["kind"] == "render"]
    cycles = 0
    phase = "start"
    for event in events:
        if event["kind"] == "change":
            if phase == "looked":
                cycles += 1
            phase = "changed"
        elif event["kind"] == "look" and phase == "changed":
            phase = "looked"
    last_look = looks[-1]["step"] if looks else None
    last_change = changes[-1]["step"] if changes else None
    return {
        "changes": len(changes),
        "change_steps": len({event["step"] for event in changes}),
        "renders": len(renders),
        "looks": len(looks),
        "images": sum(event.get("images", 1) for event in looks),
        "cycles": cycles,
        "first_look_step": looks[0]["step"] if looks else None,
        "last_look_step": last_look,
        "last_change_step": last_change,
        # Changes to the deliverable that no later look could have checked.
        "changes_after_last_look": sum(1 for event in changes if last_look is None or event["step"] > last_look),
        "looked_after_last_change": bool(last_look is not None and last_change is not None and last_look > last_change),
        "steps": last_step,
    }


def main_file(state: Replay) -> dict[str, Any] | None:
    """The deliverable a reader most likely wants: the replayed deliverable with the most
    versions, an HTML page first. None when no deliverable was replayed (for example a deck
    built by a generator script: its versions are not in the transcript)."""
    candidates = [entry for entry in state.files.values() if entry["contents"] and entry.get("deliverable")]
    if not candidates:
        return None
    return max(candidates, key=lambda entry: (entry["path"].endswith(HTML_SUFFIXES), len(entry["contents"])))


def compare_with_disk(entry: dict[str, Any], run_root: Path, deliverables: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Whether the last replayed version is the file the run kept."""
    name = entry["path"].rsplit("/", 1)[-1]
    for item in deliverables or []:
        relative = str(item.get("path", ""))
        if relative.rsplit("/", 1)[-1] != name:
            continue
        candidate = (run_root / relative).resolve()
        try:
            if not candidate.is_relative_to(run_root.resolve()) or not candidate.is_file():
                continue
            kept = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        last = entry["contents"][-1]
        return {"path": relative, "bytes": len(kept.encode("utf-8")), "matches": kept.rstrip("\n") == last.rstrip("\n")}
    return None


def history(state: Replay) -> list[dict[str, Any]]:
    """The files and their versions, without the contents. `uncertain_from_step` is the
    first step at which something changed the file that the replay could not follow:
    versions before it are what the file held, later ones may not be."""
    files = []
    for entry in state.files.values():
        if not entry["versions"]:
            continue
        doubts = [item["step"] for item in (*entry["notes"], *entry["failed"])]
        files.append({**{key: value for key, value in entry.items() if key not in ("contents", "_prepared")}, "uncertain_from_step": min(doubts) if doubts else None})
    files.sort(key=lambda entry: (not entry.get("deliverable"), not entry["path"].endswith(HTML_SUFFIXES), -len(entry["versions"]), entry["path"]))
    return files


# -- failed checks and the steps behind them -------------------------------------------------

TAG = re.compile(r"<(script|style)\b[^>]*>.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)
NOT_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _squash(text: str) -> str:
    """Letters and digits only, lower-cased: rendered text and source text compare equal
    whatever happened to spacing, punctuation and entities in between."""
    return NOT_WORD.sub("", html.unescape(text)).casefold()


def evidence_texts(item: dict[str, Any], limit: int = 4) -> list[str]:
    """The on-slide texts a failed check points at. A box text reads
    "role · size (floor …) · the text"; the text is what follows the last separator."""
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    texts: list[str] = []
    for box in evidence.get("boxes") or []:
        raw = box.get("text") if isinstance(box, dict) else None
        if not isinstance(raw, str):
            continue
        text = raw.split(" · ")[-1].strip() if raw.count(" · ") >= 2 else raw.strip()
        if len(_squash(text)) >= 4 and text not in texts:
            texts.append(text)
        if len(texts) >= limit:
            break
    return texts


CLASS_ATTR = re.compile(r"""class\s*=\s*["']([^"']*)["']""")


def _element_at(content: str, text: str, rule_lines: list[tuple[str, str]] | None = None) -> tuple[str, tuple[str, ...]] | None:
    """The element that holds `text` in the source: its opening tag and its own text (up
    to the next tag), and the lines of the style rules written for its classes. Together
    they are what has to change for that text to look different."""
    # The first words of the rendered text, with any spacing between them and either the
    # literal or the escaped form of & < >.
    words = text.split()[:4] or [text]
    if len(words) == 1:
        words = [words[0][:12]]
    either = {"&": "(?:&|&amp;)", "<": "(?:<|&lt;)", ">": "(?:>|&gt;)"}
    pieces = ["".join(either.get(char) or re.escape(char) for char in word) for word in words]
    found = re.search(r"\s+".join(pieces), content)
    if found is None:
        return None
    start = found.start()
    opening = content.rfind("<", 0, start)
    closing = content.find("<", start)
    element = content[max(0, opening) : closing if closing >= 0 else len(content)]
    classes = (CLASS_ATTR.search(element[: element.find(">") + 1]) or [None, ""])[1].split()[:4]
    if rule_lines is None:
        rule_lines = _rule_lines(content)
    rules: list[str] = []
    for name in classes:
        pattern = re.compile(r"\." + re.escape(name) + r"(?![\w-])")
        rules.extend(line for selector, line in rule_lines if "." + name in selector and pattern.search(selector))
    return element, tuple(rules)


def _rule_lines(content: str) -> list[tuple[str, str]]:
    """(selector part, whole line) of every line that opens a style rule."""
    return [(line.split("{", 1)[0], line.strip()) for line in content.splitlines() if "{" in line]


def _prepared(entry: dict[str, Any]) -> list[tuple[str, list[tuple[str, str]]]]:
    """Per version, what tracing needs and is the same for every text: the page text with
    tags and punctuation removed, and its style-rule lines. Kept on the entry."""
    cached = entry.get("_prepared")
    if cached is None or len(cached) != len(entry["contents"]):
        cached = entry["_prepared"] = [(_squash(TAG.sub(" ", content)), _rule_lines(content)) for content in entry["contents"]]
    return cached


def trace_text(entry: dict[str, Any], text: str) -> dict[str, Any] | None:
    """Where a text entered the file, and the last version that changed the element holding
    it (its tag or its words) or a style rule for one of that element's classes."""
    key = _squash(text)[:TEXT_KEY]
    if len(key) < 4:
        return None
    introduced = last = None
    via = "text"
    previous: tuple[str, tuple[str, ...]] | None = None
    for content, version, (plain, rule_lines) in zip(entry["contents"], entry["versions"], _prepared(entry)):
        if key not in plain:
            previous = None
            continue
        current = _element_at(content, text, rule_lines)
        if introduced is None:
            introduced = last = version
        elif current is not None and previous is not None and current != previous:
            last = version
            via = "text" if current[0] != previous[0] else "style"
        previous = current if current is not None else previous
    if introduced is None:
        return None
    return {
        "text": text[:120],
        "introduced": {"step": introduced["step"], "version": introduced["n"]},
        "last_edit": {"step": last["step"], "version": last["n"], "via": via},
    }


def link_checks(entry: dict[str, Any] | None, events: list[dict[str, Any]], failed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each failed reward check: the step that last changed the text it points at, and
    the looks the agent took after that step (all of them, and those whose file name names
    one of the check's slides).

    `seen` says what the transcript shows, not what the agent noticed:
      never        no picture was looked at after the last change to that text
      other        pictures were looked at afterwards, none named for the check's slides
      slide        a picture named for one of the check's slides was looked at afterwards
      untraceable  the check carries no text, or the text is not in the replayed file
    """
    looks = [event for event in events if event["kind"] == "look"]
    links = []
    traces: dict[str, dict[str, Any] | None] = {}

    def trace(text: str) -> dict[str, Any] | None:
        if text not in traces:
            traces[text] = trace_text(entry, text)  # type: ignore[arg-type]
        return traces[text]

    for index, item in enumerate(failed or []):
        slides = [slide for slide in item.get("slides") or [] if isinstance(slide, int)]
        traced = [found for found in (trace(text) for text in evidence_texts(item)) if found] if entry else []
        link: dict[str, Any] = {"index": index, "id": item.get("id"), "layer": item.get("layer"), "severity": item.get("severity"), "slides": slides, "texts": traced}
        if not traced:
            link["seen"] = "untraceable"
            links.append(link)
            continue
        latest = max(traced, key=lambda found: found["last_edit"]["version"])["last_edit"]
        last_step, last_version = latest["step"], latest["version"]
        after = [event for event in looks if event["step"] > last_step]
        on_slide = [event for event in after if event.get("slide") in slides]
        link.update(
            last_edit_step=last_step,
            last_edit_version=last_version,
            last_edit_via=latest["via"],
            introduced_step=min(found["introduced"]["step"] for found in traced),
            looks_after=len(after),
            look_steps=sorted({event["step"] for event in after})[:LOOK_STEPS],
            slide_look_steps=sorted({event["step"] for event in on_slide})[:LOOK_STEPS],
            seen="slide" if on_slide else "other" if after else "never",
        )
        links.append(link)
    return links
