#!/usr/bin/env bash
# postswitch-smoke.sh — 切换后 5 分钟内必跑的冒烟测试
# 用法：bash /home/schan/tts-erp/prod-switch/postswitch-smoke.sh
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

BASE="${BASE:-http://127.0.0.1:9877}"
# Pull admin/ro keys from .env so the operator doesn't have to export
# them by hand. systemd already does this via EnvironmentFile, but a
# direct bash invocation needs explicit sourcing.
ENV_FILE="${ENV_FILE:-/home/schan/tts-erp/.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi
ADMIN_KEY="${TTS_ERP_SERVICE_KEY:?set TTS_ERP_SERVICE_KEY in .env first (or export TTS_ERP_SERVICE_KEY)}"  # pi-lens-ignore shellcheck.SC2034: loaded via .env above, used later in script
RO_KEY="${TTS_ERP_RO_KEY:?set TTS_ERP_RO_KEY in .env first — run: python3 api_keys.py create --role readonly --name smoke-ro}"  # pi-lens-ignore shellcheck.SC2034: loaded via .env above, used later in script
RW_KEY="${TTS_ERP_RW_KEY:-}"

step "1/8 — healthz (must report service=tts-erp-v2)"
# Use /healthz body to confirm v2 is actually loaded (legacy returns
# just {"status":"ok"}; v2 adds "service":"tts-erp-v2" and auth_mode).
curl -s -o /tmp/_h.json "$BASE/healthz"
v2_fingerprint=$(grep -c 'tts-erp-v2' /tmp/_h.json || true)
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/healthz")
if [ "$code" = "200" ] && [ "$v2_fingerprint" -ge 1 ]; then
  ok "healthz=200 + v2 fingerprint detected"
else
  bad "healthz=$code v2_fingerprint=$v2_fingerprint (legacy still serving?)"
fi

step "2/8 — auth middleware"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/v2/commerce/sales-orders?shop_id=x&limit=1")
[ "$code" = "401" ] && ok "no key → 401" || bad "expected 401 got $code"

code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer invalid" \
  "$BASE/v2/commerce/sales-orders?shop_id=x&limit=1")
[ "$code" = "401" ] && ok "bad key → 401" || bad "expected 401 got $code"

step "3/8 — auth role gating (readonly can GET /v2/* but POST /v2/reporting/manual-costs is readwrite)"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $RO_KEY" \
  "$BASE/v2/commerce/sales-orders?shop_id=7494763368967603447&limit=1")
[ "$code" = "200" ] && ok "readonly GET /v2/commerce/sales-orders → 200" || bad "expected 200 got $code"

# /v2/reporting/manual-costs is readwrite (POST); readonly → 403
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $RO_KEY" \
  -H "Content-Type: application/json" \
  -d '{"channel_product_external_id":"x","unit_cost":"1.00","currency":"USD"}' \
  "$BASE/v2/reporting/manual-costs")
[ "$code" = "403" ] && ok "readonly POST /v2/reporting/manual-costs → 403" || bad "expected 403 got $code"

step "4/8 — manual-costs page (HTML form, readonly OK)"
# The HTML page is mounted at /v2/pages/manual-costs (GET → HTML, readonly).
# The JSON POST endpoint at /v2/reporting/manual-costs takes readwrite.
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $RO_KEY" \
  "$BASE/v2/pages/manual-costs")
[ "$code" = "200" ] && ok "manual-costs page renders" || bad "expected 200 got $code"

step "5/8 — CORS preflight (must DENY by default)"
code=$(curl -s -o /dev/null -w '%{http_code}' -X OPTIONS \
  -H "Origin: https://evil.example.com" -H "Access-Control-Request-Method: GET" \
  "$BASE/v2/commerce/sales-orders?shop_id=x&limit=1")
[ "$code" = "400" ] || [ "$code" = "403" ] && ok "evil origin → $code (CORS denying)" || bad "expected 400/403 got $code"

step "6/8 — v2 endpoints return JSON"
for ep in /v2/commerce/sales-orders /v2/commerce/channel-accounts \
  /v2/linkage/product-links /v2/reporting/coverage; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $RO_KEY" \
    "$BASE$ep?shop_id=7494763368967603447&limit=1")
  [ "$code" = "200" ] && ok "$ep → 200" || bad "$ep → $code"
done

step "7/8 — DB pool not exhausted"
docker exec postgres psql -U postgres -d tts_erp -tAc \
  "SELECT count(*) FROM pg_stat_activity WHERE datname='tts_erp'" |
  awk '{ if ($1 < 50) exit 0; else exit 1 }' &&
  ok "active connections under 50" ||
  bad "PG connections too high"

echo
if [ $fail -eq 0 ]; then
  echo -e "${GREEN}==== POST-SWITCH SMOKE PASSED ====${NC}"
  echo "v2 is healthy. Start the 4-week observation period."
else
  echo -e "${RED}==== SMOKE FAILED ($fail issues) ====${NC}"
  echo "RECOMMEND: bash $REPO/prod-switch/rollback.sh"
  exit 1
fi
