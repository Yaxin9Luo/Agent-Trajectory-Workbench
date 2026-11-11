#!/usr/bin/env bash
# Start, restart or stop the Workbench server in the background.
#
#   HOST=0.0.0.0 PORT=8416 ./deploy.sh        # start (or restart)
#   PORT=8416 ./deploy.sh stop
#   PORT=8416 ./deploy.sh status
#
# The index lives in $STATE (default ~/.agent-trajectory-workbench). Secrets such as
# TYPESAFE_API_KEY (needed for Jev) are read from $ENV_FILE when it exists, so they never
# go into this repository: `KEY=value` lines, chmod 600. START_TIMEOUT (default 60) is how
# many seconds a start waits for /api/health.
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

# The PID file only counts when that process is still this server on this port: after a
# reboot or PID reuse the number can belong to an unrelated process, which must not be killed.
running() {
  [ -f "$PID_FILE" ] || return 1
  local pid cmdline
  pid="$(cat "$PID_FILE")"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  cmdline="$(ps -p "$pid" -o command= 2>/dev/null)" || return 1
  case "$cmdline" in
    *trajectory_workbench*" --port $PORT" | *trajectory_workbench*" --port $PORT "*) return 0 ;;
  esac
  return 1
}

stop() {
  if running; then
    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid"
    for _ in 1 2 3 4 5 6 7 8 9 10; do running || break; sleep 0.5; done
    if running; then
      echo "pid $pid did not stop; not starting" >&2
      exit 1
    fi
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

# httpx2 (inside the TypeSafe SDK) verifies TLS with `truststore` unless SSL_CERT_FILE is
# set, and truststore crashed the process (double free / segfault in _configure_context)
# when several Jev requests opened connections at once on Linux. Use the system bundle.
if [ -z "${SSL_CERT_FILE:-}" ]; then
  for bundle in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/certs/ca-bundle.crt; do
    if [ -f "$bundle" ]; then export SSL_CERT_FILE="$bundle"; break; fi
  done
fi

# A native crash prints the Python stack to the log instead of only "double free".
nohup env PYTHONPATH=src PYTHONFAULTHANDLER=1 "$PYTHON" -m trajectory_workbench --db "$DB" serve --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"

# Opening a large index can take a while: poll /api/health once a second for up to
# $START_TIMEOUT seconds, and stop early when the process has exited.
case "$HOST" in
  0.0.0.0 | :: | "") CHECK_HOST=127.0.0.1 ;;
  *:*) CHECK_HOST="[$HOST]" ;;
  *) CHECK_HOST="$HOST" ;;
esac
START_TIMEOUT="${START_TIMEOUT:-60}"
healthy=0
deadline=$((SECONDS + START_TIMEOUT))
while [ "$SECONDS" -lt "$deadline" ]; do
  sleep 1
  running || break
  if curl -fsS -m 2 "http://$CHECK_HOST:$PORT/api/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
done
if [ "$healthy" = 1 ]; then
  echo "Trajectory Workbench: http://$(hostname):$PORT/  (pid $(cat "$PID_FILE"), log $LOG_FILE, Jev: $([ -n "${TYPESAFE_API_KEY:-}" ] && echo on || echo off))"
else
  if running; then
    echo "failed to start: no answer from http://$CHECK_HOST:$PORT/api/health within ${START_TIMEOUT}s (pid $(cat "$PID_FILE") still running; ./deploy.sh stop stops it); last log lines:" >&2
  else
    echo "failed to start; last log lines:" >&2
  fi
  tail -n 20 "$LOG_FILE" >&2
  exit 1
fi
