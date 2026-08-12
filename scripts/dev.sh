#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_pid=""
frontend_pid=""

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM

  if [[ -n "${backend_pid}" ]]; then
    kill "${backend_pid}" 2>/dev/null || true
  fi
  if [[ -n "${frontend_pid}" ]]; then
    kill "${frontend_pid}" 2>/dev/null || true
  fi

  wait 2>/dev/null || true
  exit "${exit_code}"
}

trap cleanup EXIT INT TERM

if [[ ! -x "${repo_root}/backend/.venv/bin/uvicorn" ]]; then
  printf 'Backend environment is missing. Run `make setup` first.\n' >&2
  exit 1
fi

if [[ ! -d "${repo_root}/frontend/node_modules" ]]; then
  printf 'Frontend dependencies are missing. Run `make setup` first.\n' >&2
  exit 1
fi

(
  cd "${repo_root}/backend"
  exec .venv/bin/uvicorn main:app --reload --port 8688
) &
backend_pid=$!

(
  cd "${repo_root}/frontend"
  exec pnpm exec vite dev
) &
frontend_pid=$!

printf 'Backend: http://localhost:8688\n'
printf 'Frontend: use the Vite URL shown above\n'
printf 'Press Ctrl-C to stop both processes.\n'

while kill -0 "${backend_pid}" 2>/dev/null && kill -0 "${frontend_pid}" 2>/dev/null; do
  sleep 1
done

if ! kill -0 "${backend_pid}" 2>/dev/null; then
  wait "${backend_pid}"
else
  wait "${frontend_pid}"
fi
