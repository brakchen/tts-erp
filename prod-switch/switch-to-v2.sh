#!/usr/bin/env bash
# switch-to-v2.sh — 停旧服务、起新 :9877、保留 :9876 用于紧急回滚
# 用法：bash /home/schan/tts-erp/prod-switch/switch-to-v2.sh
# 回滚：bash /home/schan/tts-erp/prod-switch/rollback.sh
set -euo pipefail

REPO=/home/schan/tts-erp
cd "$REPO"

GREEN='\033[0;32m'
YEL='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YEL}This script will:${NC}"
echo "  1. Stop tts-erp (legacy on :9877)"
echo "  2. Stop sync_cron (legacy)"
echo "  3. Stop oauth-receiver (legacy on :9876) — REVERSIBLE via :9877 rollback"
echo "  4. Restart tts-erp (new v2 service on :9877)"
echo "  5. Start sync-worker (new v2 service)"
echo
read -rp "Continue? type 'SWITCH' to proceed: " ans
[ "$ans" = "SWITCH" ] || {
  echo "aborted"
  exit 1
}

# 1. Stop legacy tts-erp
echo "→ stopping legacy tts-erp (:9877)"
systemctl --user stop tts-erp.service || true

# 2. Stop legacy sync_cron
echo "→ stopping legacy sync_cron"
pkill -f sync_cron.py || true
sleep 2

# 3. Stop legacy oauth-receiver (the v2 api no longer depends on :9876,
#    but we keep it RUNNING for 4 weeks to enable instant rollback)
echo "→ keeping oauth-receiver (:9876) running for 4-week rollback window"

# 4. Update systemd unit's ExecStart from legacy → v2
#    The unit's WorkingDirectory is /home/schan/tts-erp/tdd and the
#    app is tts_erp_fastapi:app (legacy). For v2 we run from the repo
#    root and load tts_erp_v2.app:app.
echo "→ updating tts-erp.service unit to v2 app"
UNIT="$HOME/.config/systemd/user/tts-erp.service"
if [ ! -f "$UNIT" ]; then
  echo -e "${RED}✗ unit file not found at $UNIT${NC}"
  exit 1
fi
# Back up the legacy unit so rollback can restore it
cp "$UNIT" "$UNIT.legacy.bak.$(date +%Y%m%d-%H%M%S)"
# Patch WorkingDirectory + ExecStart in place (using python for safety)
python3 - "$UNIT" <<'PYEOF'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text()
# Replace WorkingDirectory line
text = re.sub(
    r'^WorkingDirectory=.*$',
    'WorkingDirectory=/home/schan/tts-erp',
    text, flags=re.M,
)
# Replace ExecStart line that loads tts_erp_fastapi
text = re.sub(
    r'^ExecStart=.*tts_erp_fastapi:app.*$',
    'ExecStart=/home/schan/tts-erp/.venv/bin/python -m uvicorn tts_erp_v2.app:app --host ${TTS_ERP_HOST} --port ${TTS_ERP_PORT}',
    text, flags=re.M,
)
p.write_text(text)
PYEOF
systemctl --user daemon-reload
echo "  unit updated. Diff:"
diff -u "$UNIT.legacy.bak."* "$UNIT" | head -10 || true

# 5. Start new tts-erp v2
echo "→ starting tts-erp v2 (FastAPI on :9877)"
systemctl --user restart tts-erp.service
sleep 3

# 6. Start new sync-worker (separate systemd unit, may not exist yet)
echo "→ starting tts-erp-sync worker"
systemctl --user enable --now tts-erp-sync.service 2>/dev/null ||
  {
    echo "  NOTE: tts-erp-sync.service unit not installed yet."
    echo "  Run: bash $REPO/prod-switch/install-sync-worker.sh first."
  }

# Wait for healthz
echo "→ waiting for :9877/healthz ..."
# First confirm the v2 app actually loaded (it adds healthz payload keys
# that legacy doesn't have).
for i in {1..30}; do
  if curl -sf http://127.0.0.1:9877/healthz >/dev/null; then
    # Check for v2-specific field in healthz response
    if curl -sf http://127.0.0.1:9877/healthz 2>/dev/null | grep -q '"app_version"\|"tts_erp_v2"\|"v2"'; then
      echo -e "  ${GREEN}✓ :9877 serving v2 after ${i}s${NC}"
    else
      echo -e "  ${YEL}⚠ :9877 healthy after ${i}s but v2 fingerprint not detected — legacy still serving?${NC}"
    fi
    echo
    echo "==== SWITCH COMPLETE ===="
    echo "New v2 api:     http://127.0.0.1:9877"
    echo "Legacy backup:  http://127.0.0.1:9876 (oauth-receiver still up)"
    echo "Next: run post-switch smoke tests"
    echo "  bash $REPO/prod-switch/postswitch-smoke.sh"
    exit 0
  fi
  sleep 1
done

echo -e "${RED}✗ :9877 failed to come up in 30s${NC}"
echo "Auto-rolling back ..."
bash "$REPO/prod-switch/rollback.sh"
exit 1
