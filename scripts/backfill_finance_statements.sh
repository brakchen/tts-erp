#!/usr/bin/env bash
# scripts/backfill_finance_statements.sh
# 回拨 finance cursor 到 07-01，让 job 重新拉全量 statements，
# 35 个之前因 payment_id 缺失被跳过的 statement 会以 NULL payout_id 入库。
# 安幂等：ON CONFLICT DO UPDATE，已入库的 statement 不会重复。

set -euo pipefail
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true
export ALLOW_PROD_DESTRUCTIVE=1

ALEMBIC=/home/schan/tts-erp/.venv/bin/alembic
PYTHON=/home/schan/tts-erp/.venv/bin/python

echo "=== backfill finance statements ==="

# 1. 回拨 finance cursor 到 07-01
echo "--- 回拨 finance cursor ---"
psql "$TTS_ERP_DB_URL" -c "
  UPDATE integration.sync_cursors
  SET cursor_epoch_ms = 1782864000000
  WHERE job_name = 'tiktok.finance.statements'
    AND scope = '7494763368967603447';
"
echo "✓ cursor 已回拨到 2026-07-01"

# 2. 等下一轮 finance job 跑（每小时一次，重启后应很快触发）
echo "--- 等待 finance job 跑一轮（约 90 秒）---"
sleep 90

# 3. 验证
echo "--- 验证结果 ---"
psql "$TTS_ERP_DB_URL" -c "
  SELECT 'NULL-payout statements' AS metric, count(*) AS value
  FROM finance.settlement_statements WHERE payout_id IS NULL
  UNION ALL
  SELECT 'NULL-payout transactions', count(*)
  FROM finance.settlement_transactions st
  JOIN finance.settlement_statements ss ON ss.id = st.settlement_statement_id
  WHERE ss.payout_id IS NULL
  UNION ALL
  SELECT 'orders gaining real settlement', count(DISTINCT st.order_pk)
  FROM finance.settlement_transactions st
  JOIN finance.settlement_statements ss ON ss.id = st.settlement_statement_id
  WHERE ss.payout_id IS NULL AND st.order_pk IS NOT NULL
  UNION ALL
  SELECT 'unresolved PAYMENT_ID_MISSING', count(*)
  FROM integration.sync_issues
  WHERE issue_type = 'STATEMENT_PAYMENT_ID_MISSING' AND resolved_at IS NULL;
"

echo "=== 完成 ==="
echo "预期：NULL-payout statements ≈ 35，orders gaining real settlement ≈ 31"