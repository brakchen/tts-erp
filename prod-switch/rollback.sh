#!/usr/bin/env bash
# rollback.sh — 紧急回滚到切换前状态
# 用法：bash /home/schan/tts-erp/prod-switch/rollback.sh
# 触发条件：v2 启动后 5 分钟内 healthz 不通 / 关键端点 5xx / 数据明显错误
set -euo pipefail

REPO=/home/schan/tts-erp
cd "$REPO"

RED='\033[0;31m'
YEL='\033[0;33m'
NC='\033[0m'

echo -e "${RED}=== EMERGENCY ROLLBACK ===${NC}"
echo "This will:"
echo "  1. Stop tts-erp v2 (:9877)"
echo "  2. Restart tts-erp legacy on :9877"
echo "  3. Restart legacy sync_cron"
echo "  4. Confirm :9876 + :9877 both healthy"
echo
read -rp "Roll back? type 'ROLLBACK' to proceed: " ans
[ "$ans" = "ROLLBACK" ] || {
  echo "aborted"
  exit 1
}

echo "→ stopping tts-erp v2"
systemctl --user stop tts-erp.service || true
pkill -f sync_worker || true
pkill -f tts_erp_v2 || true

echo "→ restarting legacy tts-erp"
# 旧二进制在 git 历史中: git show 3e568d0:tts_erp.py > /tmp/tts_erp_legacy.py
# 然后用 python3 /tmp/tts_erp_legacy.py 启动 ; 如果用 systemd 则:
systemctl --user start tts-erp.service || true
sleep 2

echo "→ restarting legacy sync_cron"
bash "$REPO/run_sync_cron.sh" &
sleep 3

echo "→ verifying health"
code_old=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9877/healthz || echo 000)
code_oauth=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9876/healthz || echo 000)
if [ "$code_old" = "200" ] && [ "$code_oauth" = "200" ]; then
  echo -e "${YEL}==== ROLLBACK COMPLETE — legacy stack restored ====${NC}"
  echo "  :9877 legacy tts-erp + sync_cron — OK"
  echo "  :9876 oauth-receiver               — OK"
  echo
  echo "Next steps:"
  echo "  1. Investigate why v2 failed (logs at /home/schan/tts-erp/logs/stderr.log)"
  echo "  2. Fix the root cause"
  echo "  3. Re-run preflight.sh + switch-to-v2.sh when ready"
else
  echo -e "${RED}ROLLBACK FAILED — manual intervention required${NC}"
  echo "  :9877 = $code_old  :9876 = $code_oauth"
  echo "  Check: journalctl --user -u tts-erp.service -n 50"
  exit 1
fi
