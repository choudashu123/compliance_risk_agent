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

# --- pick the interpreter (prefer the project venv) --------------------------
if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  PY="$(command -v python3 || command -v python)"
  echo "[run] .venv not found — using $PY (create it with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)"
fi

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
