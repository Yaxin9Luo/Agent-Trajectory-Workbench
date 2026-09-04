from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from trajectory_workbench.registry import Registry
from trajectory_workbench.service import WorkbenchService


MAX_REQUEST_BYTES = 1_048_576
WEB_ROOT = Path(__file__).with_name("web")


def create_server(
    host: str,
    port: int,
    registry_path: Path,
) -> ThreadingHTTPServer:
    service = WorkbenchService(Registry(registry_path))

    class Handler(BaseHTTPRequestHandler):
        server_version = "TrajectoryWorkbench/0.1"

        def do_GET(self) -> None:
            try:
                self._get()
            except PermissionError as error:
                self._json_error(HTTPStatus.FORBIDDEN, str(error))
            except (KeyError, FileNotFoundError) as error:
                self._json_error(HTTPStatus.NOT_FOUND, str(error))
            except ValueError as error:
                self._json_error(HTTPStatus.BAD_REQUEST, str(error))
            except Exception as error:
                self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

        def do_POST(self) -> None:
            try:
                self._post()
            except ValueError as error:
                self._json_error(HTTPStatus.BAD_REQUEST, str(error))
            except Exception as error:
                self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

        def log_message(self, format: str, *args: object) -> None:
            return

        def _get(self) -> None:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if path == "/api/health":
                self._json({"status": "ok"})
                return
            if path == "/api/runs":
                self._json({"runs": service.list_runs()})
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
                run_id = parts[2]
                if len(parts) == 3:
                    self._json(service.get_run(run_id))
                    return
                if len(parts) == 4 and parts[3] == "messages":
                    roles_value = query.get("roles", [""])[0]
                    roles = {item for item in roles_value.split(",") if item} or None
                    tool = query.get("tool", [None])[0]
                    search = query.get("search", [None])[0]
                    offset = self._int_query(query, "offset", 0)
                    limit = self._int_query(query, "limit", 100)
                    self._json(
                        service.get_messages(
                            run_id,
                            roles=roles,
                            tool=tool,
                            search=search,
                            offset=offset,
                            limit=limit,
                        )
                    )
                    return
                if len(parts) >= 5 and parts[3] == "files":
                    relative = "/".join(parts[4:])
                    self._file(service.resolve_file(run_id, relative))
                    return
            self._static(path)

        def _post(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path != "/api/runs":
                self._json_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
                return
            payload = self._body_json()
            source_path = payload.get("path")
            if not isinstance(source_path, str) or not source_path.strip():
                raise ValueError("path must be a non-empty string")
            label = payload.get("label")
            if label is not None and not isinstance(label, str):
                raise ValueError("label must be a string")
            self._json(
                service.import_run(source_path.strip(), label),
                status=HTTPStatus.CREATED,
            )

        def _body_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValueError("Invalid Content-Length") from error
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("Request body size is invalid")
            try:
                value = json.loads(self.rfile.read(length))
            except json.JSONDecodeError as error:
                raise ValueError("Request body is not valid JSON") from error
            if not isinstance(value, dict):
                raise ValueError("Request body must be a JSON object")
            return value

        def _int_query(
            self,
            query: dict[str, list[str]],
            name: str,
            default: int,
        ) -> int:
            try:
                return int(query.get(name, [str(default)])[0])
            except ValueError as error:
                raise ValueError(f"{name} must be an integer") from error

        def _static(self, request_path: str) -> None:
            relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
            candidate = (WEB_ROOT / relative).resolve()
            root = WEB_ROOT.resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                raise FileNotFoundError(request_path)
            self._file(candidate)

        def _file(self, path: Path) -> None:
            content = path.read_bytes()
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def _json(
            self,
            payload: object,
            *,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def _json_error(self, status: HTTPStatus, message: str) -> None:
            self._json({"error": message}, status=status)

    return ThreadingHTTPServer((host, port), Handler)

