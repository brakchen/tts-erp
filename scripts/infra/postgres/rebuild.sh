#!/usr/bin/env bash
# rebuild.sh — 按 container-spec.yaml 重建 PG 容器。
#
# ⚠ 这是破坏性操作：会停 PG 10~30 秒 ⇒ frp 隧道断 ⇒ 207 的 API 连不上库
#    ⇒ 公网短暂 500。执行前确认有备份、且在变更窗口内。
#
# 用法：
#   bash scripts/infra/postgres/rebuild.sh --dry-run     # 只打印命令
#   bash scripts/infra/postgres/rebuild.sh --confirm     # 真正执行
set -euo pipefail
SPEC="$(dirname "$0")/container-spec.yaml"
[ -f "$SPEC" ] || { echo "找不到 $SPEC"; exit 1; }

echo "=== 目标规格（来自 container-spec.yaml）==="
grep -E '^\s+- |image:|restart_policy:|network:|pgdata:' "$SPEC" | sed 's/^/  /'

if [ "${1:-}" != "--confirm" ]; then
  cat <<'DRY'
--- DRY RUN ---
真正执行会做：
  1. 记录当前容器完整参数（docker inspect）到 /root/pg-rebuild-backup-<ts>/
  2. docker stop postgres && docker rm postgres
  3. docker run -d --name postgres --network postgres_default --restart unless-stopped
        -p 127.0.0.1:5432:5432
        -v 1b98586f4daf2b00092c4c9e700d411d15d8dbfa599fd228a0e4c994784953a2:/var/lib/postgresql
        -e POSTGRES_USER -e POSTGRES_PASSWORD -e PGDATA ... postgres:18
  4. 验证：容器内 pg_isready / SELECT version() / 应用连接数恢复 / 公网 /tts/healthz 200
  5. 验证【边界】：从组网机连 <宿主机>:5432 必须失败
DRY
  exit 0
fi

TS=$(date +%Y%m%d_%H%M%S)
BK=/root/pg-rebuild-backup-$TS
mkdir -p "$BK"
docker inspect postgres > "$BK/inspect.json" && echo "已备份 inspect → $BK/inspect.json"
docker exec postgres pg_dump -U postgres --no-owner --no-privileges tts_erp | gzip > "$BK/tts_erp_pre_rebuild.sql.gz" \
  && echo "已备份数据库 → $BK/tts_erp_pre_rebuild.sql.gz"

echo "=== 停止并移除旧容器 ==="
docker stop postgres && docker rm postgres

echo "=== 按 spec 重建（端口绑 127.0.0.1）==="
# ★ 口令来源：必须【从容器当前环境取回】，不能假设它在 runtime.env 里
#   （实测：runtime.env 里没有 POSTGRES_PASSWORD；它只在容器的 Env 里）
#   ⇒ 顺带的好处：轮换口令就在这里换（改这一个变量即可）
CUR_ENV=$(docker inspect postgres --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null)
CUR_PW=$(printf '%s\n' "$CUR_ENV"   | grep '^POSTGRES_PASSWORD=' | head -1 | cut -d= -f2-)
CUR_USER=$(printf '%s\n' "$CUR_ENV"  | grep '^POSTGRES_USER='    | head -1 | cut -d= -f2-)
NEW_PW="${NEW_POSTGRES_PASSWORD:-$CUR_PW}"
[ -n "$NEW_PW" ] || { echo "取不到 POSTGRES_PASSWORD（容器 Env 与 NEW_POSTGRES_PASSWORD 都为空）"; exit 1; }
if [ -n "${NEW_POSTGRES_PASSWORD:-}" ]; then
  echo "※ 使用 NEW_POSTGRES_PASSWORD（口令将在本次重建时轮换）"
  # 轮换后必须同步：受控 runtime 文件 + 指纹凭据 + 验收（遍历全部来源断言旧值不存在）
  echo "※ 轮换后待办：更新 runtime.env、更新 systemd credential 指纹、按 shadowed 为空验收"
fi
set -a; . /home/schan/.config/tts-erp/runtime.env 2>/dev/null || true; set +a
docker run -d --name postgres \
  --network postgres_default \
  --restart unless-stopped \
  -p 127.0.0.1:5432:5432 \
  -v 1b98586f4daf2b00092c4c9e700d411d15d8dbfa599fd228a0e4c994784953a2:/var/lib/postgresql \
  -e POSTGRES_USER="${CUR_USER:-postgres}" \
  -e POSTGRES_PASSWORD="$NEW_PW" \
  -e PGDATA=/var/lib/postgresql/18/docker \
  postgres:18

echo "=== 验证 ==="
sleep 10
docker exec postgres pg_isready
docker exec postgres psql -U postgres -d tts_erp -tAc "select version();"
docker exec postgres psql -U postgres -d tts_erp -tAc "select version_num from alembic_version;"
echo "--- 应用连接"
curl -s --max-time 6 -o /dev/null -w "  127.0.0.1:9877/healthz -> %{http_code}\n" http://127.0.0.1:9877/healthz
echo "--- 边界验证（从组网机 aliyun-50 连，必须失败）"
ssh -o BatchMode=yes -o ConnectTimeout=12 aliyun-50 \
  'timeout 5 bash -c "echo > /dev/tcp/10.144.144.3/5432" 2>/dev/null && echo "  ⚠ 仍然 OPEN（收口未生效）" || echo "  ✓ closed（收口生效）"' 2>&1
echo "--- 公网"
curl -s --max-time 8 -o /dev/null -w "  公网 /tts/healthz -> %{http_code}\n" https://chen.daqiangplatform.com/tts/healthz 2>&1
