#!/usr/bin/env bash
# 为一个新 worktree 准备测试环境（venv / .env.test / 前端依赖）。
#
# 用法（在 worktree 根目录执行）：
#   bash scripts/envsetup/prepare-worktree.sh
#
# 背景：.venv、.env.test、frontend/node_modules 都是 gitignored，
# 新建的 git worktree 不会自动带上它们。缺任何一个，
# scripts/test_isolated.sh 都会失败：
#   - 缺 .venv/bin/python      -> "missing project Python"
#   - 缺 .env.test             -> "TTS_ERP_DB_URL_TEST is unset; create .env.test"
#   - 缺 frontend/node_modules -> "Frontend dependencies missing"
#
# 在开发机运行，从 Git 主 checkout 复用 venv 和测试连接串。
# TTS_ERP_MAIN_CHECKOUT 可指定已有环境的 checkout。前端依赖按当前 worktree 安装。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMMON_DIR="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
MAIN_CHECKOUT="${TTS_ERP_MAIN_CHECKOUT:-$(dirname "$COMMON_DIR")}"
SCOPE=fast
BASE=''
CHECK=0
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope) SCOPE="${2:?missing scope}"; shift 2 ;;
    --base) BASE="${2:?missing base}"; shift 2 ;;
    --check) CHECK=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      echo 'Usage: prepare-worktree.sh [--scope fast|backend|frontend|auto --base SHA] [--check|--dry-run]'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$SCOPE" in fast|backend|frontend|auto) ;; *) echo 'Invalid scope' >&2; exit 2 ;; esac
[[ "$SCOPE" != auto || -n "$BASE" ]] || { echo 'auto requires --base' >&2; exit 2; }

note() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }

# 1) Python venv —— 复用主 checkout 的解释器
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  echo "[OK]   python venv 已就绪"
elif [[ -x "$MAIN_CHECKOUT/.venv/bin/python" ]]; then
  if [[ "$CHECK" -eq 0 && "$DRY_RUN" -eq 0 ]]; then ln -s "$MAIN_CHECKOUT/.venv" "$ROOT/.venv"; fi
  echo "[LINK] python venv -> $MAIN_CHECKOUT/.venv"
else
  warn "主 checkout 没有可用 venv；请先在 $MAIN_CHECKOUT 执行 install-test-deps.sh"
fi

# 2) 测试连接串 —— 复用主 checkout 的 .env.test
if [[ -f "$ROOT/.env.test" ]]; then
  echo "[OK]   测试连接串 .env.test 已就绪"
elif [[ -f "$MAIN_CHECKOUT/.env.test" ]]; then
  if [[ "$CHECK" -eq 0 && "$DRY_RUN" -eq 0 ]]; then ln -s "$MAIN_CHECKOUT/.env.test" "$ROOT/.env.test"; fi
  echo "[LINK] .env.test -> $MAIN_CHECKOUT/.env.test"
else
  warn "缺少 .env.test；请从运维处取得并放在 worktree 根目录"
fi

PYTHON="$ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$MAIN_CHECKOUT/.venv/bin/python"
NEEDS_FRONTEND=1
if [[ "$SCOPE" == backend ]]; then NEEDS_FRONTEND=0
elif [[ "$SCOPE" == auto ]]; then
  PLAN="$("$PYTHON" "$ROOT/scripts/_test_selection.py" --scope auto --base "$BASE")"
  NEEDS_FRONTEND="$(printf '%s' "$PLAN" | "$PYTHON" -c 'import json,sys; p=json.load(sys.stdin); print(int(p["frontend"] or p["build"]))')"
fi
FRONTEND_OK=1
if [[ "$NEEDS_FRONTEND" -eq 0 ]]; then
  echo '[SKIP] 选测不需要前端，无需 Node 或 npm 安装'
elif [[ "$DRY_RUN" -eq 1 ]]; then
  echo '[PLAN] 校验独立前端安装指纹；变化时 npm ci'
else
  # shellcheck source=scripts/envsetup/test-runtime.sh
  . "$ROOT/scripts/envsetup/test-runtime.sh"
  [[ ! -L "$ROOT/frontend/node_modules" ]] || { warn '禁止共享 node_modules symlink'; exit 1; }
  # Runtime+lockfile fingerprint, not directory-existence evidence. No secrets.
  FINGERPRINT="$("$PYTHON" - "$ROOT" <<'PY'
import hashlib
import subprocess
import sys
from pathlib import Path
root = Path(sys.argv[1])
digest = hashlib.sha256()
for filename in ('package.json', 'package-lock.json'):
    digest.update((root / 'frontend' / filename).read_bytes())
for command in (['node', '--version'], ['npm', '--version']):
    digest.update(subprocess.check_output(command, timeout=15))
print(digest.hexdigest())
PY
  )"
  MARKER="$ROOT/frontend/node_modules/.tts-test-install"
  if [[ -d "$ROOT/frontend/node_modules/vitest" && -d "$ROOT/frontend/node_modules/semver" &&
        -f "$MARKER" && "$(cat "$MARKER")" == "$FINGERPRINT" ]]; then
    echo '[OK]   前端依赖指纹匹配'
  elif [[ "$CHECK" -eq 1 ]]; then
    warn '前端安装缺失或指纹过期；运行 prepare-worktree.sh 安装'
    FRONTEND_OK=0
  else
    note '按本 worktree 锁文件安装前端依赖'
    (cd "$ROOT/frontend" && npm ci --prefer-offline --no-audit --fund=false --engine-strict)
    printf '%s\n' "$FINGERPRINT" > "$MARKER"
  fi
  if [[ "$FRONTEND_OK" -eq 1 ]]; then node "$ROOT/frontend/scripts/check-runtime.mjs"; fi
fi

# 4) 自检
status=0
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  warn ".venv/bin/python 不可用"
  status=1
fi
if [[ ! -f "$ROOT/.env.test" ]]; then
  warn ".env.test 缺失"
  status=1
fi
if [[ "$NEEDS_FRONTEND" -eq 1 && "$FRONTEND_OK" -eq 0 ]]; then
  warn "frontend/node_modules/vitest 缺失；前端相关测试会失败"
  status=1
fi
if [[ "$DRY_RUN" -eq 1 ]]; then echo '[PLAN] dry-run: no files changed'; exit 0; fi

if [[ "$status" -eq 0 ]]; then
  echo "[DONE] worktree 测试环境已就绪: $ROOT"
else
  echo "[FAIL] 环境准备不完整" >&2
fi
exit "$status"
