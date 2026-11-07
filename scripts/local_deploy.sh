#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf '%s\n' \
    '用法：bash scripts/local_deploy.sh [--frontend-only]' \
    '默认构建前端并前台启动 http://127.0.0.1:9877，后端读取 .env.local。' \
    '服务已运行时，用 --frontend-only 只更新前端包。'
}

if (( $# > 1 )); then
  usage >&2
  exit 2
fi

case "${1:-}" in
  ''|--frontend-only) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [[ "${1:-}" != --frontend-only ]]; then
  if [[ ! -f .env.local ]]; then
    printf '%s\n' '缺少仓库根目录下的 .env.local，请先准备本机服务配置。' >&2
    exit 1
  fi
  if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' '找不到 uv，请安装后重新运行。' >&2
    exit 1
  fi
fi

if [[ ! -d frontend/node_modules/vite ]]; then
  npm --prefix frontend ci
fi
bash scripts/build_frontend.sh

if [[ "${1:-}" == --frontend-only ]]; then
  printf '%s\n' '前端构建完成，刷新页面即可。'
  exit 0
fi

exec uv run --python 3.14 --env-file .env.local python -m uvicorn tts_erp_v2.app:app --host 127.0.0.1 --port 9877
