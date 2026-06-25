#!/usr/bin/env bash
# Render the kiji-safeguard demo GIFs from the VHS tapes in ./tapes.
#
#   ./demo/render.sh                 # render every tape
#   ./demo/render.sh 01-rug-pull     # render one tape (with or without .tape)
#
# Prereqs: vhs (https://github.com/charmbracelet/vhs) and uv. The script
# creates a throwaway venv (.venv-demo) with the dev extras, then for each tape
# starts a *fresh* registry on a private port with an empty SQLite DB so the
# "first sight -> registered" narrative is reproducible every time.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# Use the default registry port so the tapes need no --registry / env override:
# both the CLI and the autosign hook fall back to http://127.0.0.1:8000.
PORT="${KIJI_DEMO_PORT:-8000}"
REGISTRY="http://127.0.0.1:${PORT}"
DB="$(mktemp -d)/demo_registry.db"

cd "$ROOT"

command -v vhs >/dev/null 2>&1 || {
  echo "error: vhs not found. Install it: https://github.com/charmbracelet/vhs" >&2
  exit 1
}
command -v uv >/dev/null 2>&1 || {
  echo "error: uv not found. Install it: https://docs.astral.sh/uv/" >&2
  exit 1
}

echo "› preparing demo venv (.venv-demo)…"
uv venv --python 3.12 .venv-demo >/dev/null 2>&1 || uv venv .venv-demo >/dev/null 2>&1
# shellcheck disable=SC1091
source .venv-demo/bin/activate
uv pip install -e ".[dev]" >/dev/null

REG_PID=""
stop_registry() {
  if [ -n "$REG_PID" ]; then kill "$REG_PID" >/dev/null 2>&1 || true; fi
  REG_PID=""
}
start_registry() {
  stop_registry
  rm -f "$DB"
  KIJI_SAFEGUARD_DB="$DB" kiji-safeguard serve --port "$PORT" >/dev/null 2>&1 &
  REG_PID=$!
  for _ in $(seq 1 50); do
    if curl -sf "$REGISTRY/servers?limit=1" >/dev/null 2>&1; then return; fi
    sleep 0.2
  done
  echo "error: registry did not come up on $REGISTRY" >&2
  exit 1
}
trap stop_registry EXIT

mkdir -p demo/gif

if [ "$#" -gt 0 ]; then
  tapes=()
  for arg in "$@"; do tapes+=("${arg%.tape}.tape"); done
else
  tapes=(01-rug-pull.tape 02-magic-import.tape 03-cli-tour.tape 04-proxy-tripwire.tape)
fi

for tape in "${tapes[@]}"; do
  echo "› rendering demo/tapes/$tape"
  start_registry
  vhs "demo/tapes/$tape"
done

stop_registry
echo "✓ done — GIFs are in demo/gif/"
