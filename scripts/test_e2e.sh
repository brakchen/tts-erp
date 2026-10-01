#!/usr/bin/env bash
# 页面级 Playwright E2E 测试入口。
#
# 用法:
#   bash scripts/test_e2e.sh spu-roi core          # SPU ROI 核心链路
#   bash scripts/test_e2e.sh spu-roi all           # SPU ROI 全部（core + extended）
#   bash scripts/test_e2e.sh spu-roi case C-SPUROI-07  # 指定 case
#   bash scripts/test_e2e.sh changed origin/master # 按 git diff 选择
#   bash scripts/test_e2e.sh all core              # 所有页面核心链路
#   bash scripts/test_e2e.sh all all               # 全部页面全部 tier
#
# 安全约束:
#   - 禁止直接对生产 :9877 执行
#   - 使用 scripts/test_isolated.sh e2e 自动隔离 DB
#   - TTS_ERP_AUTH_MODE=enforce，真实 session cookie
#   - 临时 uvicorn 进程 bounded，测试结束清理
set -euo pipefail

cd "$(dirname "$0")/.."

# ── Helpers ─────────────────────────────────────────
fail() { echo "[e2e] ERROR: $*" >&2; exit 1; }
info() { echo "[e2e] $*"; }

# ── Pre-flight checks ──────────────────────────────
command -v node >/dev/null 2>&1 || fail "node not found; install Node.js >= 22"
command -v npm >/dev/null 2>&1 || fail "npm not found"

NODE_VER=$(node -e 'console.log(process.versions.node.split(".")[0])')
[[ "$NODE_VER" -ge 22 ]] || fail "Node.js >= 22 required, got $NODE_VER"

# ── Parse args ──────────────────────────────────────
SUITE="${1:-}"
TIER_OR_CASE="${2:-core}"
EXTRA_ARGS=()

if [[ -z "$SUITE" ]]; then
  cat <<'EOF'
Usage:
  bash scripts/test_e2e.sh spu-roi core          # 页面核心链路
  bash scripts/test_e2e.sh spu-roi all           # 页面全部 tier
  bash scripts/test_e2e.sh spu-roi case C-SPUROI-07  # 指定 case
  bash scripts/test_e2e.sh changed origin/master # 按 git diff 选择
  bash scripts/test_e2e.sh all core              # 所有页面核心
  bash scripts/test_e2e.sh all all               # 全部
EOF
  exit 0
fi

# ── Install deps if needed ──────────────────────────
if [[ ! -d node_modules ]]; then
  info "Installing npm dependencies..."
  npm ci --ignore-scripts 2>/dev/null || npm install --ignore-scripts
fi

# Check Playwright browsers
if [[ ! -d "node_modules/.cache/playwright" ]] && \
   [[ -z "${PLAYWRIGHT_BROWSERS_PATH:-}" ]] && \
   ! npx playwright install --dry-run 2>/dev/null; then
  info "Installing Playwright Chromium browser..."
  npx playwright install chromium 2>&1 | tail -5
fi

# ── Build Playwright args ───────────────────────────
PW_ARGS=()

case "$SUITE" in
  changed)
    BASE_REF="${TIER_OR_CASE:-origin/master}"
    TIER="${3:-core}"
    info "Selecting suites affected by changes since $BASE_REF (tier=$TIER)..."
    SUITES=$(python3 scripts/select_e2e_suites.py "$BASE_REF" --tier "$TIER" --format text)
    if [[ -z "$SUITES" ]]; then
      info "No affected suites found; nothing to run."
      exit 0
    fi
    info "Affected suites:"
    echo "$SUITES" | sed 's/^/  /'
    # Run all affected suites
    for suite_line in $SUITES; do
      SUITE_NAME=$(echo "$suite_line" | awk '{print $1}')
      SUITE_TIER=$(echo "$suite_line" | awk '{print $2}')
      info "Running suite: $SUITE_NAME (tier=$SUITE_TIER)"
      bash "$0" "$SUITE_NAME" "$SUITE_TIER"
    done
    exit 0
    ;;
  all)
    # Run all page suites
    SUITE_NAMES=$(python3 -c "
import json
with open('tests/e2e/suites.json') as f:
    suites = json.load(f)
for name, cfg in suites.items():
    if 'specDir' in cfg:
        print(name)
" | sort)
    for s in $SUITE_NAMES; do
      info "Running suite: $s (tier=$TIER_OR_CASE)"
      bash "$0" "$s" "$TIER_OR_CASE"
    done
    exit 0
    ;;
  spu-roi|*)
    # Validate suite exists
    python3 -c "
import json, sys
with open('tests/e2e/suites.json') as f:
    suites = json.load(f)
if '$SUITE' not in suites or 'specDir' not in suites['$SUITE']:
    print(f'Unknown suite: $SUITE', file=sys.stderr)
    print(f'Available: {[k for k,v in suites.items() if \"specDir\" in v]}', file=sys.stderr)
    sys.exit(1)
" || fail "Invalid suite: $SUITE"
    ;;
esac

# Set project
PW_ARGS+=("--project=$SUITE")

case "$TIER_OR_CASE" in
  core)
    PW_ARGS+=("--grep=@tier:core")
    ;;
  all)
    # no tier filter
    ;;
  case)
    CASE_ID="${3:-}"
    [[ -n "$CASE_ID" ]] || fail "Usage: $0 $SUITE case <CASE_ID>"
    PW_ARGS+=("--grep=$CASE_ID")
    ;;
  extended)
    PW_ARGS+=("--grep=@tier:extended")
    ;;
  *)
    fail "Unknown tier/case: $TIER_OR_CASE (expected: core|all|extended|case)"
    ;;
esac

# Append extra args from environment or CLI
if [[ -n "${E2E_EXTRA_ARGS:-}" ]]; then
  IFS=' ' read -ra EXTRA_ARGS <<< "$E2E_EXTRA_ARGS"
fi
PW_ARGS+=("${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}")

# ── Run via Python orchestrator ─────────────────────
info "Running: npx playwright test ${PW_ARGS[*]}"
info "DB isolation: scripts/test_isolated.sh e2e ..."
info ""

# The Python orchestrator handles:
# 1. Ephemeral DB creation from template
# 2. Test data seeding (TEST_* only)
# 3. Temporary uvicorn with TTS_ERP_AUTH_MODE=enforce
# 4. Playwright execution
# 5. Cleanup
exec bash scripts/test_isolated.sh e2e \
  python3 -m pytest tests/e2e_browser/ \
    -x -q --tb=short \
    -o "e2e_playwright_args=${PW_ARGS[*]}"