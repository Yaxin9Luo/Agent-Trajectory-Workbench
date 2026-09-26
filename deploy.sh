#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export TYPESAFE_API_KEY="${TYPESAFE_API_KEY:-}"

REGISTRY="${REGISTRY:-$HOME/.agent-trajectory-workbench/registry.json}"
RUNS_DIR="${RUNS_DIR:-/Users/yaxinluo/Desktop/prov demo show/outputs}"

echo "=== Trajectory Workbench Deploy ==="
echo "Registry: $REGISTRY"
echo "Runs dir: $RUNS_DIR"

# Ensure registry exists with real runs
if [ ! -f "$REGISTRY" ] || [ "$(python3 -c "import json; print(len(json.load(open('$REGISTRY'))['entries']))" 2>/dev/null || echo 0)" -lt 8 ]; then
  echo "Registering real prov runs..."
  uv run python3 -c "
from pathlib import Path
from trajectory_workbench.registry import Registry
from trajectory_workbench.service import WorkbenchService
import os

registry = Registry(Path('$REGISTRY'))
service = WorkbenchService(registry)
runs_dir = Path('$RUNS_DIR')
for run in sorted(runs_dir.iterdir()):
    if not run.is_dir():
        continue
    try:
        service.import_run(str(run), run.name)
        print(f'  registered: {run.name}')
    except Exception as e:
        print(f'  skip: {run.name}: {e}')
print(f'total: {len(service.list_runs())} runs')
"
fi

# Start server
echo "Starting server on http://127.0.0.1:8877 ..."
uv run ./trajectory-workbench serve --registry "$REGISTRY" --port 8877
