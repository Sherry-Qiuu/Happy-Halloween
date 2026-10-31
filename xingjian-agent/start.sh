#!/usr/bin/env sh
# Start the Xingjian agent service.
#   ./start.sh              # http://127.0.0.1:8000
#   PORT=8080 ./start.sh    # another port
#   AGENT_MOCK=1 ./start.sh # offline demo, no API key needed
set -eu

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  echo "Python 3.9+ is required: https://www.python.org/downloads/" >&2
  exit 1
fi

PYTHON=python3
command -v python3 >/dev/null 2>&1 || PYTHON=python

if [ ! -f .env ] && [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "No .env found. Copy .env.example to .env and add DEEPSEEK_API_KEY," >&2
  echo "or run: AGENT_MOCK=1 ./start.sh   (offline demo)" >&2
fi

exec "$PYTHON" server.py
