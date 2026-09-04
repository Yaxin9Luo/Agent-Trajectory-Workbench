from __future__ import annotations

import argparse
import json
from pathlib import Path

from trajectory_workbench.registry import DEFAULT_REGISTRY_PATH, Registry
from trajectory_workbench.server import create_server
from trajectory_workbench.service import WorkbenchService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trajectory-workbench")
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY_PATH,
        help=f"registry path (default: {DEFAULT_REGISTRY_PATH})",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="start the local browser")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8877)
    add = commands.add_parser("import", help="register a MoH run directory")
    add.add_argument("path")
    add.add_argument("--label")
    commands.add_parser("list", help="list registered runs")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "serve":
        server = create_server(args.host, args.port, args.registry)
        print(f"Trajectory Workbench: http://{args.host}:{server.server_port}/")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return

    service = WorkbenchService(Registry(args.registry))
    if args.command == "import":
        result = service.import_run(args.path, args.label)
    else:
        result = {"runs": service.list_runs()}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

