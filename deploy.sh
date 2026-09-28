#!/usr/bin/env bash
# Start, restart or stop the Workbench server in the background.
#
#   HOST=0.0.0.0 PORT=8416 ./deploy.sh        # start (or restart)
#   PORT=8416 ./deploy.sh stop
#   PORT=8416 ./deploy.sh status
#
# The index lives in $STATE (default ~/.agent-trajectory-workbench). Secrets such as
# TYPESAFE_API_KEY (needed for Jev) are read from $ENV_FILE when it exists, so they never
# go into this repository: `KEY=value` lines, chmod 600.
set -euo pipefail

cd "$(dirname "$0")"

STATE="${STATE:-$HOME/.agent-trajectory-workbench}"
DB="${DB:-$STATE/workbench.db}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8877}"
ENV_FILE="${ENV_FILE:-$HOME/.config/trajectory-workbench/env}"
PYTHON="${PYTHON:-.venv/bin/python}"
PID_FILE="$STATE/server-$PORT.pid"
LOG_FILE="$STATE/server-$PORT.log"
mkdir -p "$STATE"

running() { [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; }

stop() {
  if running; then
    kill "$(cat "$PID_FILE")"
    for _ in 1 2 3 4 5 6 7 8 9 10; do running || break; sleep 0.5; done
  fi
  rm -f "$PID_FILE"
}

case "${1:-start}" in
  stop)
    stop
    echo "stopped"
    exit 0
    ;;
  status)
    if running; then echo "running (pid $(cat "$PID_FILE")), log: $LOG_FILE"; else echo "not running"; fi
    exit 0
    ;;
  start | restart)
    stop
    ;;
  *)
    echo "usage: $0 [start|restart|stop|status]" >&2
    exit 2
    ;;
esac

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

# A native crash prints the Python stack to the log instead of only "double free".
nohup env PYTHONPATH=src PYTHONFAULTHANDLER=1 "$PYTHON" -m trajectory_workbench --db "$DB" serve --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"
sleep 1
if running && curl -fsS -m 5 "http://127.0.0.1:$PORT/api/health" >/dev/null; then
  echo "Trajectory Workbench: http://$(hostname):$PORT/  (pid $(cat "$PID_FILE"), log $LOG_FILE, Jev: $([ -n "${TYPESAFE_API_KEY:-}" ] && echo on || echo off))"
else
  echo "failed to start; last log lines:" >&2
  tail -n 20 "$LOG_FILE" >&2
  exit 1
fi
