from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from trajectory_workbench.registry import DEFAULT_REGISTRY_PATH
from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import Store


MAX_REQUEST_BYTES = 1_048_576
WEB_ROOT = Path(__file__).with_name("web")


def create_server(
    host: str,
    port: int,
    db_path: Path,
    legacy_registry: Path | None = DEFAULT_REGISTRY_PATH,
    service: WorkbenchService | None = None,
) -> ThreadingHTTPServer:
    service = service or WorkbenchService(Store(db_path), legacy_registry=legacy_registry)

    class Handler(BaseHTTPRequestHandler):
        server_version = "TrajectoryWorkbench/0.2"

        def do_GET(self) -> None:
            self._guard(self._get)

        def do_POST(self) -> None:
            self._guard(self._post)

        def do_DELETE(self) -> None:
            self._guard(self._delete)

        def _guard(self, handler) -> None:
            try:
                handler()
            except PermissionError as error:
                self._json_error(HTTPStatus.FORBIDDEN, str(error))
            except (KeyError, FileNotFoundError) as error:
                self._json_error(HTTPStatus.NOT_FOUND, f"Not found: {error}")
            except ValueError as error:
                self._json_error(HTTPStatus.BAD_REQUEST, str(error))
            except Exception as error:
                self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

        def log_message(self, format: str, *args: object) -> None:
            return

        # -- GET ------------------------------------------------------------------------

        def _get(self) -> None:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            one = lambda name, default=None: query.get(name, [default])[0]  # noqa: E731
            if path == "/api/health":
                return self._json({"status": "ok"})
            if path == "/api/collections":
                return self._json(service.list_collections())
            if path == "/api/taxonomy":
                return self._json(service.taxonomy())
            if path == "/api/trajectories":
                reviewed = one("reviewed")
                return self._json(
                    service.list_trajectories(
                        collection=one("collection") or None,
                        outcome=one("outcome") or None,
                        flag=one("flag") or None,
                        label=one("label") or None,
                        reviewed=None if reviewed in (None, "") else reviewed == "1",
                        search=one("search") or None,
                        task_key=one("task_key") or None,
                        group_key=one("group_key") or None,
                        roles=[item for item in (one("roles") or "").split(",") if item] or None,
                        episodes=one("episodes") == "1",
                        ready=None if one("ready") in (None, "") else one("ready") == "1",
                        issue=one("issue") or None,
                        jev_flag=one("jev_flag") or None,
                        sort=one("sort") or "imported",
                        offset=self._int(query, "offset", 0),
                        limit=self._int(query, "limit", 100),
                    )
                )
            if path == "/api/corrections/export":
                kind = one("kind") or "sft"
                body = service.export_corrections(one("collection") or None, kind).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="corrections-{kind}.jsonl"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/explore":
                return self._json(service.explore([item for item in query.get("collection", []) if item]))
            if path == "/api/search":
                return self._json(service.search_steps(one("q") or "", one("collection") or None, self._int(query, "limit", 60)))
            if path == "/api/queue":
                return self._json(service.queue(one("collection") or None, one("date") or None, self._int(query, "size", 20)))
            if path == "/api/stats":
                return self._json(service.stats(one("collection") or None))
            if path == "/api/reviews/export":
                body = service.export_reviews(one("collection") or None).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="reviews.jsonl"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/compare":
                ids = [item for item in query.get("id", []) if item]
                if len(ids) < 2:
                    raise ValueError("at least two trajectory ids are required")
                return self._json(service.compare(ids))
            if path == "/api/jobs":
                return self._json({"jobs": service.jobs.list()})
            if path == "/api/rewrite-batches":
                return self._json(service.rewrite_batches())
            raw = self._raw_parts()
            if len(raw) >= 3 and raw[:2] == ["api", "rewrite-batches"]:
                if len(raw) == 3:
                    return self._json(service.rewrite_batch_records(raw[2]))
                if len(raw) == 4 and raw[3] == "overview":
                    return self._json(service.rewrite_overview(raw[2]))
                if len(raw) == 4 and raw[3] == "changes":
                    return self._json(service.rewrite_changes(
                        raw[2],
                        **{key: one(key) for key in ("status", "category", "warning", "kind", "source", "ref", "recorded", "sample") if one(key)},
                        offset=self._int(query, "offset", 0),
                        limit=self._int(query, "limit", 40),
                    ))
                if len(raw) == 5 and raw[3] == "records":
                    return self._json(service.rewrite_record(raw[2], raw[4]))
            parts = [part for part in path.split("/") if part]
            if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                return self._json(service.jobs.get(parts[2]))
            if len(parts) >= 3 and parts[:2] == ["api", "trajectories"]:
                trajectory_id = parts[2]
                tail = parts[3:]
                if not tail:
                    return self._json(service.get_trajectory(trajectory_id))
                if tail == ["messages"]:
                    steps_value = one("steps")
                    steps = {int(item) for item in steps_value.split(",") if item.strip().isdigit()} if steps_value else None
                    return self._json(
                        service.get_messages(
                            trajectory_id,
                            roles={item for item in (one("roles") or "").split(",") if item} or None,
                            tool=one("tool") or None,
                            search=one("search") or None,
                            steps=steps,
                            offset=self._int(query, "offset", 0),
                            limit=self._int(query, "limit", 100),
                        )
                    )
                if tail == ["episode"]:
                    return self._json(service.get_episode(trajectory_id))
                if tail == ["ledgers"]:
                    return self._json(service.ledgers(trajectory_id))
                if tail == ["annotations"]:
                    return self._json({"items": service.annotations(trajectory_id)})
                if tail == ["excerpt.md"]:
                    body = service.excerpt(trajectory_id, one("steps"), hide_outcome=one("hide_outcome") == "1").encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if tail == ["errors"]:
                    return self._json(service.get_errors(trajectory_id))
                if tail == ["artifact-diffs"]:
                    return self._json(service.get_artifact_diffs(trajectory_id))
                if tail == ["media"]:
                    return self._file(service.resolve_media(trajectory_id, one("path") or ""))
                if tail == ["inline-image"]:
                    content, media_type = service.inline_image(trajectory_id, one("ref") or "", self._int(query, "i", 0))
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", media_type)
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Cache-Control", "private, max-age=3600")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(content)
                    return
                if tail and tail[0] == "files" and len(tail) > 1:
                    # Run artifacts are untrusted HTML: render them in an opaque origin.
                    return self._file(service.resolve_file(trajectory_id, "/".join(tail[1:])), sandbox=True)
            if path.startswith("/api/"):
                raise KeyError(path)
            self._static(path)

        # -- POST / DELETE --------------------------------------------------------------

        def _post(self) -> None:
            path = unquote(urlsplit(self.path).path)
            payload = self._body_json()
            if path == "/api/imports":
                source = payload.get("path")
                if not isinstance(source, str) or not source.strip():
                    raise ValueError("path must be a non-empty string")
                collection = payload.get("collection")
                if collection is not None and not isinstance(collection, str):
                    raise ValueError("collection must be a string")
                return self._json(
                    service.start_import(source.strip(), collection, index_text=payload.get("index_text", True) is not False),
                    status=HTTPStatus.ACCEPTED,
                )
            if path == "/api/collections/reindex":
                return self._json(service.start_reindex(str(payload.get("collection") or "")), status=HTTPStatus.ACCEPTED)
            if path == "/api/exports":
                filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
                allowed = {"collection", "outcome", "flag", "label", "search", "task_key", "group_key", "roles", "episodes", "ready", "issue", "jev_flag", "reviewed"}
                clean = {key: value for key, value in filters.items() if key in allowed}
                if isinstance(clean.get("roles"), str):
                    clean["roles"] = [item for item in clean["roles"].split(",") if item]
                for key in ("episodes", "ready", "reviewed"):
                    if key in clean and clean[key] not in (None, ""):
                        clean[key] = clean[key] in (True, 1, "1", "true")
                    else:
                        clean.pop(key, None)
                return self._json(
                    service.start_export(clean, str(payload.get("name") or ""), str(payload.get("mode") or "raw")),
                    status=HTTPStatus.ACCEPTED,
                )
            if path == "/api/jev/batch":
                return self._json(
                    service.start_batch_analysis(
                        payload.get("collection") or None,
                        int(payload.get("limit") or 50),
                        bool(payload.get("include_done")),
                    ),
                    status=HTTPStatus.ACCEPTED,
                )
            if path == "/api/rewrite-batches":
                fields = {key: payload.get(key) for key in ("name", "original", "rewritten", "rewritten_path", "annotations")}
                if any(value is not None and not isinstance(value, str) for value in fields.values()):
                    raise ValueError("name, original, rewritten, rewritten_path and annotations must be strings")
                fields = {key: value.strip() for key, value in fields.items() if value and value.strip()}
                for key in ("rewritten_path", "annotations"):
                    if key in fields and not Path(fields[key]).is_absolute():
                        raise ValueError(f"{key} must be an absolute path")
                return self._json(service.start_rewrite_batch(**fields), status=HTTPStatus.ACCEPTED)
            raw = self._raw_parts()
            if len(raw) == 4 and raw[:2] == ["api", "rewrite-batches"]:
                if raw[3] == "settings":
                    return self._json(service.set_rewrite_settings(raw[2], payload))
                if raw[3] == "export":
                    return self._json(service.start_rewrite_export(raw[2], str(payload.get("name") or "")), status=HTTPStatus.ACCEPTED)
                if raw[3] == "scan":
                    return self._json(service.start_rewrite_scan(raw[2]), status=HTTPStatus.ACCEPTED)
            if len(raw) == 6 and raw[:2] == ["api", "rewrite-batches"] and raw[3] == "records" and raw[5] == "verdicts":
                return self._json(service.save_rewrite_verdict(raw[2], raw[4], payload))
            parts = [part for part in path.split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "trajectories"]:
                trajectory_id, action = parts[2], parts[3]
                if action == "review":
                    return self._json(service.save_review(trajectory_id, payload))
                if action == "suggest":
                    return self._json(service.suggest_review(trajectory_id, {
                        key: str(payload.get(key) or "") for key in ("note", "turning_note", "hypothesis")
                    }))
                if action == "jev":
                    return self._json(service.analyze(trajectory_id, force=bool(payload.get("force"))))
                if action == "find":
                    return self._json(service.find_steps(trajectory_id, str(payload.get("query") or "")))
                if action == "annotations":
                    return self._json(service.save_annotation(trajectory_id, payload))
                if action == "ledgers":
                    return self._json(service.ledgers(trajectory_id, run_jev=True, force=bool(payload.get("force"))))
            raise KeyError(path)

        def _delete(self) -> None:
            parts = [part for part in unquote(urlsplit(self.path).path).split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "trajectories"] and parts[3] == "review":
                service.delete_review(parts[2])
                return self._json({"deleted": True})
            if len(parts) == 5 and parts[:2] == ["api", "trajectories"] and parts[3] == "annotations" and parts[4].isdigit():
                service.delete_annotation(parts[2], int(parts[4]))
                return self._json({"deleted": True})
            raise KeyError(self.path)

        # -- helpers --------------------------------------------------------------------

        def _raw_parts(self) -> list[str]:
            # Split before decoding, so an encoded "/" inside a sample id stays in its part.
            return [unquote(part) for part in urlsplit(self.path).path.split("/") if part]

        def _body_json(self) -> dict[str, Any]:
            # Every POST must declare JSON, even without a body: a page served from /files/
            # (or any site) can send a "simple" POST with no body or a text/plain body, but
            # cannot set this content type without a CORS preflight, which is never granted.
            media_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if media_type != "application/json":
                raise ValueError("Content-Type must be application/json")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValueError("Invalid Content-Length") from error
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("Request body size is invalid")
            if length == 0:
                return {}
            try:
                value = json.loads(self.rfile.read(length))
            except json.JSONDecodeError as error:
                raise ValueError("Request body is not valid JSON") from error
            if not isinstance(value, dict):
                raise ValueError("Request body must be a JSON object")
            return value

        def _int(self, query: dict[str, list[str]], name: str, default: int) -> int:
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

        def _file(self, path: Path, *, sandbox: bool = False) -> None:
            content = path.read_bytes()
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".mjs":
                media_type = "text/javascript"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if sandbox:
                self.send_header("Content-Security-Policy", "sandbox allow-scripts")
            self.end_headers()
            self.wfile.write(content)

        def _json(self, payload: object, *, status: HTTPStatus = HTTPStatus.OK) -> None:
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
