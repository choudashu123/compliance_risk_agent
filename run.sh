#!/usr/bin/env bash
# Start (or restart) the Compliance & Risk Agent server.
#
#   ./run.sh              # restart on port 8000 with --reload
#   ./run.sh 8080         # restart on a different port
#   PORT=8080 ./run.sh    # same, via env var
#   ./run.sh --no-reload  # start without autoreload (any flags pass through to uvicorn)
#
# It frees the port first, so running it again always gives you a clean restart.
set -euo pipefail

cd "$(dirname "$0")"

# --- parse args: bare number => port; --no-reload disables autoreload;
#     anything else is passed straight through to uvicorn ---------------------
PORT="${PORT:-8000}"
RELOAD=1
UVICORN_ARGS=()
for arg in "$@"; do
  case "$arg" in
    ''|*[!0-9]*) ;;                       # not a plain number, fall through
    *) PORT="$arg"; continue ;;           # plain number => port
  esac
  case "$arg" in
    --no-reload) RELOAD=0 ;;
    --reload)    RELOAD=1 ;;
    *)           UVICORN_ARGS+=("$arg") ;;
  esac
done
[[ "$RELOAD" == 1 ]] && UVICORN_ARGS+=("--reload")

# --- auto-bootstrap .env if missing ------------------------------------------
if [[ ! -f .env && -f .env.example ]]; then
  echo "[run] .env not found — copying from .env.example"
  cp .env.example .env
fi

# --- auto-bootstrap virtual environment & requirements -----------------------
if [[ ! -x .venv/bin/python ]]; then
  echo "[run] .venv not found — creating virtual environment (.venv)..."
  python3 -m venv .venv
  echo "[run] installing dependencies from requirements.txt..."
  .venv/bin/pip install -r requirements.txt
  touch .venv/.requirements_installed
elif [[ -f requirements.txt && requirements.txt -nt .venv/.requirements_installed ]]; then
  echo "[run] requirements.txt updated — syncing dependencies..."
  .venv/bin/pip install -r requirements.txt
  touch .venv/.requirements_installed
fi

PY=.venv/bin/python

# --- free the port (kill whatever is listening on it) -----------------------
if lsof -ti "tcp:${PORT}" >/dev/null 2>&1; then
  echo "[run] port ${PORT} is in use — stopping the old server"
  lsof -ti "tcp:${PORT}" | xargs kill 2>/dev/null || true
  sleep 1
  lsof -ti "tcp:${PORT}" | xargs kill -9 2>/dev/null || true
fi

# --- ensure the sample PDFs exist ------------------------------------------
if ! ls sample_docs/*.pdf >/dev/null 2>&1; then
  echo "[run] generating sample PDFs into sample_docs/"
  "$PY" -c "import demo; demo.make_sample_pdfs()"
fi

echo "[run] starting: uvicorn app.main:app --host 0.0.0.0 --port ${PORT} ${UVICORN_ARGS[*]-}"
echo "[run] open http://localhost:${PORT}   ·   health: http://localhost:${PORT}/api/health"
exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" ${UVICORN_ARGS[@]+"${UVICORN_ARGS[@]}"}
