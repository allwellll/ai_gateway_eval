#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  echo '缺少 .env：请复制 .env.example 并填写数据库连接。' >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
# Migrations run at service startup. One worker owns the in-memory job queue.
exec .venv/bin/python -m uvicorn backend.app:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8080}" --no-access-log
