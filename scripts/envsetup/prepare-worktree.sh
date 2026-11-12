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

note() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }

if [[ "$ROOT" -ef "$MAIN_CHECKOUT" ]]; then
  echo "[SKIP] 当前就是主 checkout ($ROOT)。"
  echo "       若主 checkout 落后 origin/master，先同步并执行 npm ci --prefix frontend。"
  exit 0
fi

# 1) Python venv —— 复用主 checkout 的解释器
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  echo "[OK]   python venv 已就绪"
elif [[ -x "$MAIN_CHECKOUT/.venv/bin/python" ]]; then
  ln -sfn "$MAIN_CHECKOUT/.venv" "$ROOT/.venv"
  echo "[LINK] python venv -> $MAIN_CHECKOUT/.venv"
else
  warn "主 checkout 没有可用 venv；请先在 $MAIN_CHECKOUT 执行 install-test-deps.sh"
fi

# 2) 测试连接串 —— 复用主 checkout 的 .env.test
if [[ -f "$ROOT/.env.test" ]]; then
  echo "[OK]   测试连接串 .env.test 已就绪"
elif [[ -f "$MAIN_CHECKOUT/.env.test" ]]; then
  ln -sfn "$MAIN_CHECKOUT/.env.test" "$ROOT/.env.test"
  echo "[LINK] .env.test -> $MAIN_CHECKOUT/.env.test"
else
  warn "缺少 .env.test；请从运维处取得并放在 worktree 根目录"
fi

# 3) 前端依赖 —— 必须按本 worktree 自己的 package.json 安装。
#    不用软链接：Node 的 ESM 解析会走软链接的真实路径，
#    主 checkout 与 worktree 的 package.json 版本不同会导致模块找不到。
if [[ -d "$ROOT/frontend/node_modules/vitest" && -d "$ROOT/frontend/node_modules/semver" ]]; then
  echo "[OK]   前端依赖已就绪"
elif [[ -f "$ROOT/frontend/package-lock.json" ]]; then
  note "安装前端依赖（按本 worktree 的 package-lock.json）..."
  if (cd "$ROOT/frontend" && npm ci); then
    echo "[OK]   前端依赖安装完成"
  else
    warn "npm ci 失败；前端相关测试将无法运行"
  fi
else
  warn "缺少 frontend/package-lock.json，无法安装前端依赖"
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
if [[ ! -d "$ROOT/frontend/node_modules/vitest" ]]; then
  warn "frontend/node_modules/vitest 缺失；前端相关测试会失败"
  status=1
fi

if [[ "$status" -eq 0 ]]; then
  echo "[DONE] worktree 测试环境已就绪: $ROOT"
else
  echo "[FAIL] 环境准备不完整" >&2
fi
exit "$status"
