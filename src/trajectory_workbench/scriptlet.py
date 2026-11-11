"""Replaying the small edit scripts agents run through the shell.

Some models change files with an inline script instead of an edit tool:

    python3 - <<'EOF'
    src = open('output/slides.html', encoding='utf-8').read()
    old = '''…'''; new = '''…'''
    assert old in src
    src = src.replace(old, new)
    open('output/slides.html', 'w', encoding='utf-8').write(src)
    EOF

To follow the file through such a step the script has to be evaluated. It is NOT executed:
this module walks its syntax tree and evaluates only a fixed set of pure operations on
strings, lists and regular expressions, with file reads and writes going to the caller's
in-memory files. Anything outside that set raises `Unsupported` and the caller treats the
step as not replayed. No name is ever looked up in Python's builtins, no attribute is
fetched from a real object unless it is in an allow-list, and nothing touches the disk.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Callable

MAX_LOOP = 20_000
MAX_TEXT = 20_000_000


class Unsupported(Exception):
    """The script uses something this evaluator does not model."""


class Aborted(Exception):
    """The script would have stopped here (a failed assert, a missing substring)."""


STR_METHODS = {
    "replace", "count", "index", "find", "rfind", "rindex", "join", "split", "rsplit", "splitlines", "strip", "lstrip",
    "rstrip", "startswith", "endswith", "lower", "upper", "title", "zfill", "ljust", "rjust", "center", "format",
    "partition", "rpartition", "isdigit", "isspace", "expandtabs", "removeprefix", "removesuffix",
}
LIST_METHODS = {"append", "extend", "insert", "pop", "index", "count", "reverse", "copy"}
DICT_METHODS = {"items", "keys", "values", "get"}
MATCH_METHODS = {"group", "groups", "start", "end", "span"}
RE_FUNCTIONS = {"sub", "subn", "search", "match", "fullmatch", "findall", "finditer", "split", "escape"}
RE_FLAGS = {"S": re.S, "DOTALL": re.S, "M": re.M, "MULTILINE": re.M, "I": re.I, "IGNORECASE": re.I, "X": re.X, "VERBOSE": re.X, "U": re.U, "A": re.A}
SAFE_BUILTINS: dict[str, Callable[..., Any]] = {
    "len": len, "str": str, "int": int, "float": float, "bool": bool, "repr": repr, "range": range, "enumerate": enumerate,
    "sorted": sorted, "list": list, "tuple": tuple, "dict": dict, "set": set, "min": min, "max": max, "sum": sum,
    "any": any, "all": all, "zip": zip, "reversed": reversed, "abs": abs, "round": round,
}
MODULES = {"re", "os", "sys", "pathlib", "io", "json", "textwrap", "shutil", "subprocess"}


class _File:
    def __init__(self, path: str, mode: str) -> None:
        self.path, self.mode = path, mode


class _Path:
    def __init__(self, path: str) -> None:
        self.path = path


class _Module:
    def __init__(self, name: str) -> None:
        self.name = name


class Scriptlet:
    """Evaluate one script against a virtual file store.

    `read(path)` returns the text of a file or None when it is not known;
    `write(path, text, append)` records a write.
    """

    def __init__(self, read: Callable[[str], str | None], write: Callable[[str, str, bool], None]) -> None:
        self.read, self.write = read, write
        self.env: dict[str, Any] = {}
        self.steps = 0

    # -- statements ---------------------------------------------------------------------

    def run(self, source: str) -> None:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError) as error:
            raise Unsupported(f"not parseable: {error}") from error
        self._block(tree.body)

    def _block(self, body: list[ast.stmt]) -> None:
        for node in body:
            self._statement(node)

    def _statement(self, node: ast.stmt) -> None:
        self.steps += 1
        if self.steps > MAX_LOOP * 10:
            raise Unsupported("too many steps")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias for alias in node.names]
            module = getattr(node, "module", None)
            for alias in names:
                root = (module or alias.name).split(".")[0]
                if root not in MODULES:
                    raise Unsupported(f"import {root}")
                if isinstance(node, ast.Import):
                    self.env[alias.asname or alias.name.split(".")[0]] = _Module(alias.name.split(".")[0])
                elif module == "pathlib" and alias.name == "Path":
                    self.env[alias.asname or "Path"] = _Module("pathlib.Path")
                else:
                    raise Unsupported(f"from {module} import {alias.name}")
        elif isinstance(node, ast.Assign):
            value = self._eval(node.value)
            for target in node.targets:
                self._assign(target, value)
        elif isinstance(node, ast.AugAssign):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
                raise Unsupported("augmented assignment")
            current = self._eval(node.target)
            self._assign(node.target, self._binary(node.op, current, self._eval(node.value)))
        elif isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant):
                return  # a docstring or a bare string
            self._eval(node.value)
        elif isinstance(node, ast.Assert):
            if not self._eval(node.test):
                raise Aborted("assert failed")
        elif isinstance(node, ast.If):
            self._block(node.body if self._eval(node.test) else node.orelse)
        elif isinstance(node, ast.For):
            if node.orelse:
                raise Unsupported("for-else")
            for count, item in enumerate(self._eval(node.iter)):
                if count >= MAX_LOOP:
                    raise Unsupported("loop too long")
                self._assign(node.target, item)
                try:
                    self._block(node.body)
                except _Break:
                    break
                except _Continue:
                    continue
        elif isinstance(node, ast.With):
            for item in node.items:
                handle = self._eval(item.context_expr)
                if not isinstance(handle, _File):
                    raise Unsupported("with on something that is not a file")
                if item.optional_vars is not None:
                    self._assign(item.optional_vars, handle)
            self._block(node.body)
        elif isinstance(node, ast.Pass):
            return
        elif isinstance(node, ast.Break):
            raise _Break()
        elif isinstance(node, ast.Continue):
            raise _Continue()
        else:
            raise Unsupported(type(node).__name__)

    def _assign(self, target: ast.expr, value: Any) -> None:
        if isinstance(target, ast.Name):
            self.env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            values = list(value)
            if len(values) != len(target.elts) or any(isinstance(item, ast.Starred) for item in target.elts):
                raise Unsupported("unpacking")
            for item, part in zip(target.elts, values):
                self._assign(item, part)
        elif isinstance(target, ast.Subscript):
            container = self._eval(target.value)
            if not isinstance(container, (list, dict)):
                raise Unsupported("item assignment")
            container[self._index(target.slice)] = value
        else:
            raise Unsupported("assignment target")

    # -- expressions --------------------------------------------------------------------

    def _index(self, node: ast.expr) -> Any:
        if isinstance(node, ast.Slice):
            return slice(*(None if part is None else self._eval(part) for part in (node.lower, node.upper, node.step)))
        return self._eval(node)

    def _binary(self, op: ast.operator, left: Any, right: Any) -> Any:
        simple = (str, int, float, list, tuple)
        if not isinstance(left, simple) or not isinstance(right, simple):
            raise Unsupported("operator on a complex value")
        if isinstance(op, ast.Add):
            result = left + right  # type: ignore[operator]
        elif isinstance(op, ast.Sub):
            result = left - right  # type: ignore[operator]
        elif isinstance(op, ast.Mult):
            if any(isinstance(side, int) and not isinstance(side, bool) and side > 100_000 for side in (left, right)) and not all(isinstance(side, (int, float)) for side in (left, right)):
                raise Unsupported("huge repetition")
            result = left * right  # type: ignore[operator]
        elif isinstance(op, ast.Mod) and isinstance(left, str):
            result = left % right
        elif isinstance(op, (ast.FloorDiv, ast.Mod, ast.Div)) and isinstance(left, (int, float)) and isinstance(right, (int, float)):
            result = left // right if isinstance(op, ast.FloorDiv) else left % right if isinstance(op, ast.Mod) else left / right
        else:
            raise Unsupported("operator")
        if isinstance(result, str) and len(result) > MAX_TEXT:
            raise Unsupported("text too large")
        return result

    def _eval(self, node: ast.expr) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bytes):
                raise Unsupported("bytes")
            return node.value
        if isinstance(node, ast.Name):
            if node.id in self.env:
                return self.env[node.id]
            if node.id in ("True", "False", "None"):
                return {"True": True, "False": False, "None": None}[node.id]
            raise Unsupported(f"name {node.id}")
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant):
                    parts.append(str(value.value))
                elif isinstance(value, ast.FormattedValue) and value.format_spec is None and value.conversion == -1:
                    parts.append(str(self._eval(value.value)))
                else:
                    raise Unsupported("f-string format")
            return "".join(parts)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            if any(isinstance(item, ast.Starred) for item in node.elts):
                raise Unsupported("starred")
            items = [self._eval(item) for item in node.elts]
            return items if isinstance(node, ast.List) else tuple(items) if isinstance(node, ast.Tuple) else set(items)
        if isinstance(node, ast.Dict):
            if any(key is None for key in node.keys):
                raise Unsupported("dict unpacking")
            return {self._eval(key): self._eval(value) for key, value in zip(node.keys, node.values)}  # type: ignore[arg-type]
        if isinstance(node, ast.BinOp):
            return self._binary(node.op, self._eval(node.left), self._eval(node.right))
        if isinstance(node, ast.UnaryOp):
            value = self._eval(node.operand)
            if isinstance(node.op, ast.Not):
                return not value
            if isinstance(node.op, ast.USub) and isinstance(value, (int, float)):
                return -value
            raise Unsupported("unary operator")
        if isinstance(node, ast.BoolOp):
            result: Any = isinstance(node.op, ast.And)
            for value in node.values:
                result = self._eval(value)
                if bool(result) != isinstance(node.op, ast.And):
                    break
            return result
        if isinstance(node, ast.IfExp):
            return self._eval(node.body if self._eval(node.test) else node.orelse)
        if isinstance(node, ast.Compare):
            left = self._eval(node.left)
            for op, right_node in zip(node.ops, node.comparators):
                right = self._eval(right_node)
                if isinstance(op, ast.In):
                    ok = left in right
                elif isinstance(op, ast.NotIn):
                    ok = left not in right
                elif isinstance(op, ast.Eq):
                    ok = left == right
                elif isinstance(op, ast.NotEq):
                    ok = left != right
                elif isinstance(op, ast.Is):
                    ok = left is right
                elif isinstance(op, ast.IsNot):
                    ok = left is not right
                elif isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) and isinstance(left, (int, float, str)) and isinstance(right, type(left)):
                    ok = {ast.Lt: left < right, ast.LtE: left <= right, ast.Gt: left > right, ast.GtE: left >= right}[type(op)]  # type: ignore[operator]
                else:
                    raise Unsupported("comparison")
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.Subscript):
            container = self._eval(node.value)
            if isinstance(container, re.Match):
                return container[self._index(node.slice)]
            if not isinstance(container, (str, list, tuple, dict)):
                raise Unsupported("subscript")
            try:
                return container[self._index(node.slice)]
            except (IndexError, KeyError, TypeError) as error:
                raise Aborted(str(error)) from error
        if isinstance(node, ast.Attribute):
            owner = self._eval(node.value)
            if isinstance(owner, _Module) and owner.name == "re" and node.attr in RE_FLAGS:
                return RE_FLAGS[node.attr]
            raise Unsupported(f"attribute {node.attr}")
        if isinstance(node, (ast.ListComp, ast.GeneratorExp)) and len(node.generators) == 1 and not node.generators[0].is_async:
            generator = node.generators[0]
            out = []
            for count, item in enumerate(self._eval(generator.iter)):
                if count >= MAX_LOOP:
                    raise Unsupported("loop too long")
                self._assign(generator.target, item)
                if all(self._eval(test) for test in generator.ifs):
                    out.append(self._eval(node.elt))
            return out
        if isinstance(node, ast.Call):
            return self._call(node)
        raise Unsupported(type(node).__name__)

    def _arguments(self, node: ast.Call) -> tuple[list[Any], dict[str, Any]]:
        if any(isinstance(arg, ast.Starred) for arg in node.args) or any(keyword.arg is None for keyword in node.keywords):
            raise Unsupported("argument unpacking")
        return [self._eval(arg) for arg in node.args], {keyword.arg: self._eval(keyword.value) for keyword in node.keywords}  # type: ignore[misc]

    def _call(self, node: ast.Call) -> Any:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id == "print":
                return None
            if func.id == "open":
                args, kwargs = self._arguments(node)
                path = args[0] if args else kwargs.get("file")
                mode = args[1] if len(args) > 1 else kwargs.get("mode", "r")
                if not isinstance(path, str) or not isinstance(mode, str) or "b" in mode or "+" in mode:
                    raise Unsupported("open mode")
                return _File(path, mode)
            target = self.env.get(func.id)
            if isinstance(target, _Module) and target.name == "pathlib.Path":
                args, _ = self._arguments(node)
                if len(args) != 1 or not isinstance(args[0], str):
                    raise Unsupported("Path()")
                return _Path(args[0])
            if func.id in SAFE_BUILTINS and func.id not in self.env:
                args, kwargs = self._arguments(node)
                if kwargs and func.id not in ("sorted", "enumerate", "int"):
                    raise Unsupported("keyword arguments")
                if any(callable(value) for value in kwargs.values()):
                    raise Unsupported("callable argument")
                if func.id == "range" and (not all(isinstance(arg, int) for arg in args) or len(range(*args)) > MAX_LOOP):
                    raise Unsupported("range too long")
                try:
                    result = SAFE_BUILTINS[func.id](*args, **kwargs)
                except (TypeError, ValueError) as error:
                    raise Aborted(str(error)) from error
                return list(result) if func.id in ("range", "enumerate", "zip", "reversed") else result
            raise Unsupported(f"call {func.id}")
        if not isinstance(func, ast.Attribute):
            raise Unsupported("call")
        owner = self._eval(func.value)
        method = func.attr
        args, kwargs = self._arguments(node)
        if isinstance(owner, _Module):
            if owner.name == "re" and method in RE_FUNCTIONS:
                if any(callable(arg) for arg in args) or any(callable(value) for value in kwargs.values()):
                    raise Unsupported("callable argument")
                if isinstance(args[0] if args else kwargs.get("pattern"), str) and len(args[0] if args else kwargs["pattern"]) > 5000:
                    raise Unsupported("pattern too long")
                try:
                    result = getattr(re, method)(*args, **kwargs)
                except (re.error, TypeError, ValueError) as error:
                    raise Aborted(str(error)) from error
                return list(result) if method == "finditer" else result
            if owner.name == "pathlib" and method == "Path" and len(args) == 1 and isinstance(args[0], str):
                return _Path(args[0])
            if owner.name == "sys" and method == "exit":
                raise Aborted("sys.exit")
            raise Unsupported(f"{owner.name}.{method}")
        if isinstance(owner, _File):
            if method == "read" and not args and "r" in owner.mode:
                return self._read(owner.path)
            if method == "readlines" and not args and "r" in owner.mode:
                return self._read(owner.path).splitlines(keepends=True)
            if method in ("write", "writelines") and len(args) == 1 and ("w" in owner.mode or "a" in owner.mode):
                text = args[0] if method == "write" else "".join(args[0])
                if not isinstance(text, str):
                    raise Unsupported("writing something that is not text")
                self.write(owner.path, text, "a" in owner.mode or getattr(owner, "written", False))
                owner.written = True  # type: ignore[attr-defined]
                return len(text)
            if method == "close":
                return None
            raise Unsupported(f"file.{method}")
        if isinstance(owner, _Path):
            if method == "read_text":
                return self._read(owner.path)
            if method == "write_text" and args and isinstance(args[0], str):
                self.write(owner.path, args[0], False)
                return len(args[0])
            raise Unsupported(f"Path.{method}")
        if isinstance(owner, re.Match) and method in MATCH_METHODS:
            return getattr(owner, method)(*args)
        allowed = STR_METHODS if isinstance(owner, str) else LIST_METHODS if isinstance(owner, list) else DICT_METHODS if isinstance(owner, dict) else {"count", "index"} if isinstance(owner, tuple) else set()
        if method not in allowed:
            raise Unsupported(f"method {method}")
        if any(callable(value) for value in (*args, *kwargs.values())):
            raise Unsupported("callable argument")
        try:
            result = getattr(owner, method)(*args, **kwargs)
        except (ValueError, IndexError, KeyError, TypeError) as error:
            # str.index of a missing substring, list.pop from empty: the script stops here.
            raise Aborted(str(error)) from error
        if isinstance(result, str) and len(result) > MAX_TEXT:
            raise Unsupported("text too large")
        return list(result) if method in ("items", "keys", "values") else result

    def _read(self, path: str) -> str:
        text = self.read(path)
        if text is None:
            raise Unsupported(f"reads a file that was not replayed: {path}")
        return text


class _Break(Exception):
    pass


class _Continue(Exception):
    pass
