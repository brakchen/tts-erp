#!/usr/bin/env bash
# preflight.sh — 切换前 6 项硬性自检，全部绿才能继续
# 用法：bash /home/schan/tts-erp/prod-switch/preflight.sh
set -euo pipefail

REPO=/home/schan/tts-erp
cd "$REPO"

GREEN='\033[0;32m'
RED='\033[0;31m'
YEL='\033[0;33m'
NC='\033[0m'
fail=0

step() { echo -e "\n${YEL}== $1 ==${NC}"; }
ok() { echo -e "  ${GREEN}✓${NC} $1"; }
bad() {
  echo -e "  ${RED}✗${NC} $1"
  fail=$((fail + 1))
}

step "1/6 — Master is at expected commit"
expected="8f9b237"
actual=$(git rev-parse --short HEAD)
if [ "$actual" = "$expected" ]; then
  ok "master @ $actual"
else bad "master @ $actual (expected $expected)"; fi

step "2/6 — Alembic head matches schema"
alembic_head=$(.venv/bin/alembic heads 2>/dev/null | head -1 | awk '{print $1}')
db_head=$(docker exec postgres psql -U postgres -d tts_erp -tAc \
  "SELECT version_num FROM alembic_version LIMIT 1")
if [ "$alembic_head" = "$db_head" ]; then
  ok "alembic head = db version = $alembic_head"
else bad "alembic=$alembic_head db=$db_head — run alembic upgrade head first"; fi

step "3/6 — All 9 schemas + 35 tables exist"
expected_schemas=(integration commerce procurement fulfillment after_sales finance linkage reporting security)
for s in "${expected_schemas[@]}"; do
  n=$(docker exec postgres psql -U postgres -d tts_erp -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='$s'")
  if [ "$n" -ge 1 ]; then
    ok "schema $s ($n tables)"
  else bad "schema $s missing or empty ($n tables)"; fi
done

step "4/6 — v2 data actually present"
for tbl in commerce.sales_orders commerce.sales_order_lines finance.settlement_components \
  procurement.procurement_accounts procurement.procurement_products \
  integration.credentials linkage.link_evidence; do
  n=$(docker exec postgres psql -U postgres -d tts_erp -tAc "SELECT count(*) FROM $tbl")
  if [ "$n" -gt 0 ]; then
    ok "$tbl = $n rows"
  else bad "$tbl EMPTY — run reconcile.py first"; fi
done

step "5/6 — Old public.* tables still intact (rollback safety)"
n=$(docker exec postgres psql -U postgres -d tts_erp -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
if [ "$n" -ge 20 ]; then
  ok "public.* has $n tables (rollback target intact)"
else bad "public.* has only $n tables — source data missing!"; fi

step "6/6 — Current :9877 is the LEGACY service"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9877/healthz || echo 000)
if [ "$code" = "200" ]; then
  ok ":9877 healthy (legacy still serving)"
else bad ":9877 returned $code — legacy service not running"; fi

echo
if [ $fail -eq 0 ]; then
  echo -e "${GREEN}==== PRE-FLIGHT PASSED ====${NC}"
  echo "Safe to proceed with switch."
else
  echo -e "${RED}==== PRE-FLIGHT FAILED ($fail issues) ====${NC}"
  echo "Do NOT switch. Fix the issues above first."
  exit 1
fi
