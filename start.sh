#!/usr/bin/env bash
# One command to run the whole app: Postgres (Docker) + FastAPI + React.
#   ./start.sh
# Ctrl+C stops the API and frontend. Postgres is left running (it's a real
# database, not a dev-reload process) -- `docker compose down` to stop that too.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "No .venv found -- run this first:"
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi
if [ ! -d frontend/node_modules ]; then
  echo "frontend/node_modules missing -- run this first:"
  echo "  cd frontend && npm install"
  exit 1
fi
if [ ! -f .env ]; then
  echo "No .env found -- copy .env.example to .env and fill in SARVAM_API_KEY first."
  exit 1
fi

echo "==> Starting Postgres..."
docker compose up -d
echo "==> Waiting for Postgres to be healthy..."
until [ "$(docker compose ps db --format '{{.Health}}' 2>/dev/null)" = "healthy" ]; do
  sleep 1
done
echo "==> Postgres ready (localhost:55433)."

source .venv/bin/activate

echo "==> Starting API on http://localhost:8000 ..."
setsid uvicorn api.main:app --reload --host 0.0.0.0 --port 8000 &
API_PID=$!

echo "==> Starting frontend on http://localhost:5173 ..."
setsid bash -c 'cd frontend && exec npm run dev -- --host 0.0.0.0 --port 5173' &
FRONTEND_PID=$!

CLEANED_UP=0
cleanup() {
  [ "$CLEANED_UP" = 1 ] && return
  CLEANED_UP=1
  echo
  echo "==> Stopping API and frontend (Postgres keeps running -- 'docker compose down' to stop it too)"
  # setsid made each one its own process group leader -- killing the negative
  # pid kills that whole group (uvicorn's reloader + worker, npm + vite),
  # not just the one wrapper process `kill $PID` would reach.
  kill -- "-$API_PID" "-$FRONTEND_PID" 2>/dev/null
  wait "$API_PID" "$FRONTEND_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

echo
echo "================================================================"
echo "  API:      http://localhost:8000"
echo "  Frontend: http://localhost:5173"
echo "  Ctrl+C to stop the API and frontend."
echo "================================================================"
echo

wait
