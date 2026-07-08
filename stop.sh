#!/usr/bin/env bash
# TarifeAI — stop the backend (:8000) and frontend (:3000).
# The database is remote (Supabase); nothing local to stop.
# Usage:  ./stop.sh

kill_port() {
  local port="$1" name="$2"
  local pids
  pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
    echo "✔ $name stopped (port $port)."
  else
    echo "· $name was not running (port $port)."
  fi
}

# Primary: kill whatever is listening on the app ports.
kill_port 8000 "Backend"
kill_port 3000 "Frontend"

# Fallback: PID files written by start.sh.
for pidfile in /tmp/tarifeai_api.pid /tmp/tarifeai_web.pid; do
  [ -f "$pidfile" ] && kill "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null || true
  rm -f "$pidfile"
done
