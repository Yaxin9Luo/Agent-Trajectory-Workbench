from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trajectory_workbench.registry import DEFAULT_REGISTRY_PATH
from trajectory_workbench.server import create_server
from trajectory_workbench.service import WorkbenchService
from trajectory_workbench.store import DEFAULT_DB_PATH, Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trajectory-workbench")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help=f"index database (default: {DEFAULT_DB_PATH})")
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY_PATH,
        help="legacy JSON registry to migrate once into an empty index",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="start the browser")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8877)
    add = commands.add_parser(
        "import",
        help="index trajectories under a file or directory (Claude Code, Codex, ATIF, TraceLab, chat/SFT JSONL, MoH)",
    )
    add.add_argument("path")
    add.add_argument("--collection", "--label", dest="collection", help="collection name (default: directory name)")
    add.add_argument(
        "--no-text-index",
        action="store_true",
        help="skip the full-text step index (saves roughly 1 KB of index per step on very large exports)",
    )
    commands.add_parser("list", help="list collections")
    remove = commands.add_parser(
        "remove-collection",
        help="delete a collection from the index (rows, text index, Jev results, notes, reviews); source files are not touched",
    )
    remove.add_argument("collection")
    remove.add_argument("--yes", action="store_true", help="really delete (otherwise only report what would go)")
    analyze = commands.add_parser("analyze", help="run the Jev step scan over trajectories not yet analyzed")
    analyze.add_argument("--collection")
    analyze.add_argument("--limit", type=int, default=100)
    export = commands.add_parser("export-reviews", help="write reviews as JSONL to stdout")
    export.add_argument("--collection")
    data = commands.add_parser("export", help="write matching trajectories as training JSONL plus a data card")
    data.add_argument("out", type=Path, help="new directory to write dialog.jsonl, data_card.json and data_card.md into")
    data.add_argument("--collection")
    data.add_argument("--mode", choices=["raw", "chat"], default="raw", help="raw: copy original lines; chat: convert to OpenAI chat")
    data.add_argument("--outcome", choices=["pass", "partial", "fail", "error", "unknown"])
    data.add_argument("--ready-only", action="store_true", help="only samples without blocking readiness issues")
    data.add_argument("--roles", help="comma-separated: main,segment,subagent (default: all)")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "serve":
        server = create_server(args.host, args.port, args.db, legacy_registry=args.registry)
        print(f"Trajectory Workbench: http://{args.host}:{server.server_port}/")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return

    service = WorkbenchService(Store(args.db), legacy_registry=args.registry)
    if args.command == "import":
        def progress(done=None, total=None, message=None):
            if message:
                print(f"\r[{done or 0}] {message[:100]:<100}", end="", file=sys.stderr, flush=True)

        result = service.import_path(args.path, args.collection, progress, index_text=not args.no_text_index)
        print(file=sys.stderr)
    elif args.command == "analyze":
        rows = [
            row for row in service.store.all_trajectories(args.collection)
            if not row.get("jev_summary")
        ][: args.limit]
        failed = []
        for index, row in enumerate(rows, start=1):
            print(f"\r[{index}/{len(rows)}] {row['title'][:80]:<80}", end="", file=sys.stderr, flush=True)
            try:
                service.analyze(row["id"])
            except Exception as error:
                failed.append({"id": row["id"], "error": str(error)[:200]})
        print(file=sys.stderr)
        result = {"analyzed": len(rows) - len(failed), "failed": failed}
    elif args.command == "export":
        filters = {
            "collection": args.collection,
            "outcome": args.outcome,
            "ready": True if args.ready_only else None,
            "roles": [item for item in (args.roles or "").split(",") if item] or None,
        }
        result = service.export({k: v for k, v in filters.items() if v is not None}, args.out.expanduser().resolve(), args.mode)
        result = {key: value for key, value in result.items() if key != "card"} | {"card": str(args.out / "data_card.md")}
    elif args.command == "remove-collection":
        names = {item["collection"] for item in service.store.collections()}
        if args.collection not in names:
            raise SystemExit(f"no collection named {args.collection!r}; have: {sorted(names)}")
        if not args.yes:
            total, _ = service.store.query_trajectories(collection=args.collection, limit=1)
            reviews = sum(1 for item in service.store.reviews(args.collection))
            result = {"would_remove": {"trajectories": total, "reviews": reviews}, "hint": "add --yes to delete"}
        else:
            result = {"removed": service.store.remove_collection(args.collection)}
    elif args.command == "export-reviews":
        sys.stdout.write(service.export_reviews(args.collection))
        return
    else:
        result = service.list_collections()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
