#!/usr/bin/env bash
# scripts/deploy_fix-sync-pipeline-data-loss.sh
# 合并 commit: 10c1f50 merge: fix/sync-pipeline-data-loss
# 涉及 5 文件: after_sales.py / finance.py / finance model / migration / test
#
# 修复内容:
#   Fix 1 — after_sales watermark：只推成功 case，失败 case 下轮重抓，14 天兜底
#   Fix 2 — finance statement payment_id 缺失：payout_id 改 nullable，NULL 入库 + transactions 照抓
#
# 部署顺序严格要求: migrate 先，deploy 后，否则 sync job 会因 NOT NULL 违反报错
#
# 用法: bash scripts/deploy_fix-sync-pipeline-data-loss.sh [--dry-run]

set -euo pipefail

cd "$(dirname "$0")/.."
source .env 2>/dev/null || true

ALEMBIC=/home/schan/tts-erp/.venv/bin/alembic
PYTHON=/home/schan/tts-erp/.venv/bin/python
# 0038 的 ALTER COLUMN payout_id DROP NOT NULL 是放宽约束（不删数据），
# 安全操作，但 destructive guard 把所有 ALTER TABLE 一律拦。
export ALLOW_PROD_DESTRUCTIVE=1

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; exit 1; }

db_query() {
  $PYTHON -c "
import os, sys
from sqlalchemy import create_engine, text
e = create_engine(os.environ['TTS_ERP_DB_URL'])
with e.connect() as c:
    print(c.execute(text('''$1''')).scalar() or '')
" 2>/dev/null
}

# ─── 前置检查 ─────────────────────────────────────────────────────
echo "=== deploy: fix-sync-pipeline-data-loss ==="
echo ""

[[ -z "${TTS_ERP_DB_URL:-}" ]] && fail "TTS_ERP_DB_URL 未设置（source .env 后重试）"
echo "DB: $(echo "$TTS_ERP_DB_URL" | sed 's|.*@|***@|')"

# ─── Step 0: 检查 alembic 当前版本 ────────────────────────────────
echo ""
echo "--- Step 0: 检查 alembic 当前版本 ---"
alembic_current=$($ALEMBIC current 2>&1 | grep -oE '[a-f0-9]{12,}' | head -1 || true)
echo "  当前: $alembic_current"
MIGRATION_DONE=false
if [[ "$alembic_current" == *"0038"* ]]; then
  warn "0038 已经 applied 过，跳过 migration"
  MIGRATION_DONE=true
fi

# ─── Step 1: run migration ────────────────────────────────────────
echo ""
echo "--- Step 1: alembic upgrade 0038 ---"
if $DRY_RUN; then
  echo "[DRY RUN] 会执行: $ALEMBIC upgrade 0038_statement_payout_nullable"
elif $MIGRATION_DONE; then
  echo "已 applied，跳过"
else
  $ALEMBIC upgrade 0038_statement_payout_nullable
  $ALEMBIC current 2>&1 | grep -q "0038" && ok "migration 已 applied" || fail "migration 失败"
fi

# ─── Step 2: verify migration ─────────────────────────────────────
echo ""
echo "--- Step 2: 验证 DB 变更 ---"
if $DRY_RUN; then
  echo "[DRY RUN] 会执行: 检查 payout_id nullable + 旧 constraint 已删除"
else
  psql_check=$(db_query "SELECT is_nullable FROM information_schema.columns WHERE table_schema='finance' AND table_name='settlement_statements' AND column_name='payout_id'" | tr -d ' ')
  [[ "$psql_check" == "YES" ]] && ok "payout_id 已改 nullable" || fail "payout_id 仍为 NOT NULL，migration 可能失败"

  idx=$(db_query "SELECT count(*) FROM pg_indexes WHERE schemaname='finance' AND tablename='settlement_statements' AND indexname='uq_settlement_statements_ext'" | tr -d ' ')
  [[ "$idx" == "1" ]] && ok "uq_settlement_statements_ext index 存在" || warn "index 不存在"
fi

# ─── Step 3: restart sync worker ──────────────────────────────────
echo ""
echo "--- Step 3: restart tts-erp-sync ---"
if $DRY_RUN; then
  echo "[DRY RUN] 会执行: systemctl --user restart tts-erp-sync.service"
else
  systemctl --user restart tts-erp-sync.service
  sleep 3
  systemctl --user is-active tts-erp-sync.service >/dev/null 2>&1 \
    && ok "tts-erp-sync 已重启" \
    || fail "tts-erp-sync 未启动，请检查 journalctl --user -u tts-erp-sync -n 20"
fi

# ─── Step 4: 等待下一轮 finance job ─────────────────────────────
echo ""
echo "--- Step 4: 等待下一轮 finance job（约 90 秒）---"
if $DRY_RUN; then
  echo "[DRY RUN] 会等待 finance job 跑一轮"
else
  sleep 90
fi

# ─── Step 5: 验证结果 ────────────────────────────────────────────
echo ""
echo "--- Step 5: 验证结果 ---"
if $DRY_RUN; then
  echo "[DRY RUN] 会执行: 查询 NULL payout_id 的 statement 数量"
else
  n_null=$(db_query "SELECT count(*) FROM finance.settlement_statements WHERE payout_id IS NULL")
  n_txn=$(db_query "SELECT count(*) FROM finance.settlement_transactions st JOIN finance.settlement_statements ss ON ss.id=st.settlement_statement_id WHERE ss.payout_id IS NULL")
  n_orders=$(db_query "SELECT count(DISTINCT st.order_pk) FROM finance.settlement_transactions st JOIN finance.settlement_statements ss ON ss.id=st.settlement_statement_id WHERE ss.payout_id IS NULL AND st.order_pk IS NOT NULL")
  n_issues=$(db_query "SELECT count(*) FROM integration.sync_issues WHERE issue_type='STATEMENT_PAYMENT_ID_MISSING' AND resolved_at IS NULL")
  echo "  NULL-payout statements:    $n_null  (预期 ≈35)"
  echo "  NULL-payout transactions:  $n_txn"
  echo "  orders gaining settlement: $n_orders  (预期 ≈31)"
  echo "  unresolved PAYMENT_ID_MISSING: $n_issues"
fi

echo ""
echo "=== 部署完成 ==="
if ! $DRY_RUN; then
  echo ""
  echo "下一步："
  echo "  1. 等约 1 小时，观察 dashboard '实际ROI' 是否微调（更准确，< 1%）"
  echo "  2. 检查 sync_issues 表中 PAYMENT_ID_MISSING 是否减少"
  echo "  3. 如果一切正常，不需要额外操作"
fi