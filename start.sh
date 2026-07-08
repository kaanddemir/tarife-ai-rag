#!/usr/bin/env bash
# TarifeAI — start the whole app (backend + frontend).
# The database is remote (Supabase); the connection string lives in backend/.env.
# Usage:  ./start.sh      Ctrl+C stops backend and frontend.
#         Stop later from another terminal with ./stop.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "▶ Starting backend (http://localhost:8000)..."
cd "$ROOT/backend"
# shellcheck disable=SC1091
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!
echo "$BACKEND_PID" > /tmp/tarifeai_api.pid

echo "▶ Starting frontend (http://localhost:3000)..."
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > /tmp/tarifeai_web.pid

cleanup() {
  echo
  echo "▶ Stopping..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  rm -f /tmp/tarifeai_api.pid /tmp/tarifeai_web.pid
}
trap cleanup EXIT INT TERM

echo
echo "──────────────────────────────────────────────"
echo "  UI:   http://localhost:3000   ← open this"
echo "  API:  http://localhost:8000/docs"
echo "  (Ctrl+C here, or run ./stop.sh elsewhere, to stop)"
echo "──────────────────────────────────────────────"

wait
