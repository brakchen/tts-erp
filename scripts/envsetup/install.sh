#!/usr/bin/env bash
# scripts/envscripts/envsetup/install.sh — tts-erp v2 一键安装 / 部署脚本（幂等，可重复执行）
#
# 依据：
#   docs/ops/tts-erp.md         部署流程、systemd 托管、端口、健康检查、故障排查
#   docs/ops/analytics-sync.md  进程管理、token 签发、错误契约
#   README.md                本地环境与启动（venv + pip install -e .）
#
# 用法：
#   bash scripts/envscripts/envsetup/install.sh              # 交互式（生产库迁移需逐次确认）
#   bash scripts/envscripts/envsetup/install.sh -y           # 免交互（生产库迁移仍需显式 --migrate）
#   bash scripts/envscripts/envsetup/install.sh --migrate    # 显式执行数据库迁移
#   bash scripts/envscripts/envsetup/install.sh --dry-run    # 只打印将要执行的命令，不改动系统
#   bash scripts/envscripts/envsetup/install.sh -h           # 全部选项
#
# 步骤：
#   1 preflight  .env 存在 / 0600 / 必需变量 / Python>=3.13 / systemd --user
#   2 目录       logs/ tests/
#   3 依赖       .venv + pip install -e .
#   4 权限       restart.sh 可执行
#   5 单元       systemd user 单元（tts-erp / tts-erp-sync / watchdog / pgbackup）
#   6 PG 检查    docker 容器 pg_isready（仅警告，不中断）
#   7 迁移       .venv/bin/alembic upgrade head（默认交互确认，见安全约束）
#   8 启动       systemctl --user enable + start/restart
#   9 健康检查   GET /healthz 必须返回 service=tts-erp-v2
#  10 摘要       端口 / URL / 日志 / 常用命令
#
# 安全约束（AGENTS.md §3、docs/guides/agent-safety.md）：
#   * 生产形态库（tts_erp / tts_erp_prod / tts_erp_prod_*）上的
#     `alembic upgrade head` 属人工操作：非交互模式下必须显式传
#     --migrate 才会执行；执行时仅对本次 alembic 子进程设置
#     ALLOW_PROD_DESTRUCTIVE=1（绕过 env.py 的 prod-shape guard）。
#   * 脚本不创建、不修改任何密钥；.env 由运维提供，只校验存在性与权限。
#   * 不触碰测试库，不执行 DELETE/TRUNCATE/DROP。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PG_DOCKER="${PG_DOCKER:-postgres}"
HEALTH_URL_BASE_DEFAULT=9877

# ---------- 选项 ----------
DRY_RUN=0
YES=0
WITH_DEPS=1
WITH_UNITS=1
WITH_SERVICES=1
FORCE_UNITS=0
MIGRATE_MODE="ask"   # ask | always | never

# ---------- 输出 ----------
if [[ -t 1 ]]; then
  C_OK=$'\033[1;32m'; C_WARN=$'\033[1;33m'; C_ERR=$'\033[1;31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_OFF=""
fi
info()  { printf '%s\n' "$*"; }
ok()    { printf '%s✓%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn()  { printf '%s!%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
err()   { printf '%s✗%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; }
die()   { err "$*"; exit 1; }
step()  { printf '\n%s== %s ==%s\n' "$C_DIM" "$*" "$C_OFF"; }

# 在 dry-run 下只打印命令；否则原样执行。
run() {
  printf '  %s$ %s%s\n' "$C_DIM" "$*" "$C_OFF"
  (( DRY_RUN )) && return 0
  "$@"
}

usage() {
  cat <<'EOF'
用法: bash scripts/envscripts/envsetup/install.sh [选项]

选项:
  -y, --yes          免交互执行（生产库迁移仍需显式 --migrate）
  --migrate          显式执行 .venv/bin/alembic upgrade head
  --skip-migrate     跳过数据库迁移
  --skip-deps        跳过 venv / pip 安装（要求 .venv 已存在）
  --skip-units       跳过 systemd 单元安装
  --skip-services    跳过服务启用 / 启动
  --force-units      覆盖已存在的 systemd 单元（旧文件先备份为 *.bak.<ts>）
  --dry-run          只打印将要执行的命令，不做任何改动
  -h, --help         显示本帮助

说明:
  脚本幂等，可反复执行；已满足的步骤会跳过。完成后应看到
  /healthz 返回 {"status":"ok","service":"tts-erp-v2",...}。
  详细部署文档: docs/ops/tts-erp.md
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes)            YES=1; shift ;;
    --migrate)           MIGRATE_MODE="always"; shift ;;
    --skip-migrate)      MIGRATE_MODE="never"; shift ;;
    --skip-deps)         WITH_DEPS=0; shift ;;
    --skip-units)        WITH_UNITS=0; shift ;;
    --skip-services)     WITH_SERVICES=0; shift ;;
    --force-units)       FORCE_UNITS=1; shift ;;
    --dry-run)           DRY_RUN=1; shift ;;
    -h|--help)           usage; exit 0 ;;
    *)                   usage >&2; die "未知选项: $1" ;;
  esac
done

# 读取 .env 中的变量（去引号），第二个参数为默认值。
env_get() {
  local key=$1 def="${2-}" v=""
  if [[ -f "$REPO/.env" ]]; then
    v="$(grep -E "^${key}=" "$REPO/.env" | head -1 | cut -d= -f2- || true)"
    v="${v%%\"}"; v="${v#\"}"
  fi
  printf '%s' "${v:-$def}"
}

# 交互确认：dry-run 直接视为通过；-y 视为通过；非 tty 返回失败。
confirm() {
  local prompt=$1 ans
  (( DRY_RUN )) && return 0
  (( YES )) && return 0
  [[ -t 0 ]] || return 1
  read -r -p "$prompt [y/N] " ans || return 1
  [[ "$ans" == "y" || "$ans" == "Y" ]]
}

db_name_of() {
  local url="${1%%\?*}"
  url="${url%/}"
  printf '%s' "${url##*/}"
}

is_prod_shaped() {   # 与 tts_erp_v2.api.deps.is_prod_shaped_db 保持一致
  local n; n="$(db_name_of "$1")"
  [[ -n "$n" ]] || return 0
  [[ "$n" == "tts_erp" || "$n" == "tts_erp_prod" || "$n" == tts_erp_prod_* ]]
}

PY=""
pick_python() {
  local c
  for c in python3.14 python3.13 python3; do
    if command -v "$c" >/dev/null 2>&1 \
      && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,13) else 1)' 2>/dev/null; then
      PY="$c"; return 0
    fi
  done
  return 1
}

# ============================================================
step "1/10 preflight：环境检查"
[[ "$(uname -s)" == "Linux" ]] || die "仅支持 Linux（需要 systemd user 单元）"
command -v systemctl >/dev/null 2>&1 || die "未找到 systemctl：本脚本依赖 systemd"
if ! sys_state="$(systemctl --user is-system-running 2>/dev/null)"; then
  sys_state="${sys_state:-offline}"
fi
case "$sys_state" in
  running|degraded|maintenance) : ;;
  *) die "systemd --user 不可用（state=${sys_state:-unknown}）。修复：loginctl enable-linger \$USER 后重新登录" ;;
esac
ok "systemd --user: ${sys_state}"

[[ -f "$REPO/.env" ]] || die ".env 不存在。仓库不提供含凭证的模板，请从运维处取得配置放到 $REPO/.env 并 chmod 600"

if [[ "$(stat -c '%a' "$REPO/.env")" != "600" ]]; then
  (( DRY_RUN )) && warn "[dry-run] .env 权限非 600，将执行 chmod 600" || chmod 600 "$REPO/.env"
  ok ".env 权限已修正为 600"
else
  ok ".env 权限 600"
fi

DB_URL="$(env_get TTS_ERP_DB_URL)"
[[ -n "$DB_URL" ]] || die ".env 缺少 TTS_ERP_DB_URL（服务与迁移都依赖它）"
DB_NAME="$(db_name_of "$DB_URL")"
PROD_DB=0; is_prod_shaped "$DB_URL" && PROD_DB=1
ok "数据库: ${DB_NAME}$([[ $PROD_DB == 1 ]] && echo '（生产形态库）')"

for key in TTS_ERP_HOST TTS_ERP_PORT TTS_ERP_FERNET_KEY TTS_ERP_SESSION_SECRET; do
  if [[ -z "$(env_get "$key")" ]]; then warn ".env 缺少（或为空）$key"; fi
done

if pick_python; then
  ok "Python: $PY ($("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"
else
  die "未找到 Python >= 3.13（需要 python3.14 或 python3.13）"
fi

# ============================================================
step "2/10 目录：logs/ tests/"
run mkdir -p "$REPO/logs" "$REPO/tests"
ok "目录就绪"

# ============================================================
step "3/10 依赖：venv + pip install -e ."
if (( WITH_DEPS == 0 )); then
  if [[ ! -x "$REPO/.venv/bin/python" ]] && (( DRY_RUN == 0 )); then
    die "--skip-deps 但 $REPO/.venv 不存在（先去掉 --skip-deps，或用 --dry-run 预览）"
  fi
  ok "跳过安装（--skip-deps），使用现有 .venv"
else
  if [[ ! -x "$REPO/.venv/bin/python" ]]; then
    run "$PY" -m venv "$REPO/.venv"
  else
    info "  已存在 .venv，复用"
  fi
  run "$REPO/.venv/bin/python" -m pip install -e "$REPO"
  [[ -x "$REPO/.venv/bin/alembic" || $DRY_RUN == 1 ]] || die ".venv 缺少 alembic 可执行文件"
  ok "依赖安装完成（$("$REPO/.venv/bin/python" --version 2>/dev/null || echo "venv 已创建")）"
fi

# ============================================================
step "4/10 权限：restart.sh 可执行"
run chmod +x "$REPO/restart.sh"
ok "restart.sh 可执行"

# ============================================================
step "5/10 systemd user 单元"
write_unit() {   # write_unit <name> <content>
  local name=$1
  local content=$2
  local path="$UNIT_DIR/$name"
  if [[ -e "$path" ]] && (( FORCE_UNITS == 0 )); then
    info "  已存在，保留本地版本: $name（--force-units 可覆盖）"
    return 0
  fi
  if (( DRY_RUN )); then info "  ${C_DIM}\$ 写入 $path${C_OFF}"; return 0; fi
  mkdir -p "$UNIT_DIR"
  if [[ -e "$path" ]]; then
    cp -a "$path" "$path.bak.$(date +%Y%m%d-%H%M%S)"
    info "  已备份旧单元 → $path.bak.$(date +%Y%m%d-%H%M%S)"
  fi
  printf '%s\n' "$content" > "$path"
  ok "写入 $name"
}

link_unit() {   # link_unit <name> <repo-src> —— 与仓库 scripts/systemd/ 保持一致
  local name=$1
  local src=$2
  local path="$UNIT_DIR/$name"
  [[ -f "$src" ]] || { warn "仓库缺少 $src，跳过"; return 0; }
  if [[ -e "$path" ]] && (( FORCE_UNITS == 0 )); then
    info "  已存在，保留: $name"
    return 0
  fi
  if (( DRY_RUN )); then info "  ${C_DIM}\$ ln -sfn $src $path${C_OFF}"; return 0; fi
  mkdir -p "$UNIT_DIR"
  ln -sfn "$src" "$path"
  ok "链接 $name → $src"
}

if (( WITH_UNITS == 0 )); then
  ok "跳过（--skip-units）"
else
  write_unit "tts-erp.service" "[Unit]
Description=TikTok Shop ERP proxy (schan)
After=network-online.target oauth-receiver.service
Wants=network-online.target oauth-receiver.service

[Service]
Type=simple
WorkingDirectory=$REPO
EnvironmentFile=$REPO/.env
ExecStart=$REPO/.venv/bin/python -m uvicorn tts_erp_v2.app:app --host \${TTS_ERP_HOST} --port \${TTS_ERP_PORT} --proxy-headers --no-access-log
Restart=always
RestartSec=3
StandardOutput=append:$REPO/logs/stdout.log
StandardError=append:$REPO/logs/stderr.log

[Install]
WantedBy=default.target"

  write_unit "tts-erp-sync.service" "[Unit]
Description=tts-erp v2 sync-worker (APScheduler)
After=network.target docker.service

[Service]
Type=simple
WorkingDirectory=$REPO
EnvironmentFile=$REPO/.env
ExecStart=$REPO/.venv/bin/python -m tts_erp_v2.sync_worker.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target"

  write_unit "tts-erp-watchdog.service" "[Unit]
Description=tts-erp sync watchdog (one-shot scan of integration.sync_jobs)

[Service]
Type=oneshot
WorkingDirectory=$REPO
EnvironmentFile=$REPO/.env
ExecStart=$REPO/.venv/bin/python scripts/watchdog_sync.py
StandardOutput=append:$REPO/logs/watchdog.log
StandardError=append:$REPO/logs/watchdog.log"

  write_unit "tts-erp-watchdog.timer" "[Unit]
Description=tts-erp sync watchdog timer (every 10 min)

[Timer]
OnBootSec=3min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target"

  link_unit "tts-erp-pgbackup.service" "$REPO/scripts/systemd/tts-erp-pgbackup.service"
  link_unit "tts-erp-pgbackup.timer" "$REPO/scripts/systemd/tts-erp-pgbackup.timer"

  run systemctl --user daemon-reload
  if (( DRY_RUN )); then
    info "  ${C_DIM}\$ loginctl enable-linger $USER${C_OFF}"
  elif loginctl enable-linger "$USER" 2>/dev/null; then
    ok "linger 已启用（开机自启）"
  else
    warn "loginctl enable-linger 失败（需 root/polkit 授权），关机后服务不会自启"
  fi
fi

# ============================================================
step "6/10 PostgreSQL 就绪检查"
pg_check() {
  if command -v docker >/dev/null 2>&1 \
    && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$PG_DOCKER"; then
    if (( DRY_RUN )); then info "  ${C_DIM}\$ docker exec $PG_DOCKER pg_isready${C_OFF}"; return 0; fi
    if docker exec "$PG_DOCKER" pg_isready -q >/dev/null 2>&1; then
      ok "PG 容器 $PG_DOCKER: accepting connections"
      return 0
    fi
    warn "PG 容器 $PG_DOCKER 未就绪：docker logs --tail 50 $PG_DOCKER"
    return 1
  fi
  if command -v pg_isready >/dev/null 2>&1; then
    local host port
    host="$(printf '%s' "$DB_URL" | sed -nE 's#^[^@]*@([^:/]+).*#\1#p')"
    port="$(printf '%s' "$DB_URL" | sed -nE 's#^[^@]*@[^:/]+:([0-9]+)/.*#\1#p')"
    port="${port:-5432}"
    if [[ -n "$host" ]] && pg_isready -h "$host" -p "$port" -q; then
      ok "PG $host:$port: accepting connections"
      return 0
    fi
    warn "PG $host:$port 未就绪"
    return 1
  fi
  warn "无 docker / pg_isready，跳过 PG 就绪检查（启动失败时先查这里）"
  return 0
}
pg_check || true

# ============================================================
step "7/10 数据库迁移：alembic upgrade head"
run_migration() {
  local guard=""
  if (( PROD_DB )); then
    guard="ALLOW_PROD_DESTRUCTIVE=1 "
    warn "目标是生产形态库（$DB_NAME）：迁移属人工操作，AGENTS.md §3 责任在执行者"
    info "  将对 alembic 子进程设置 ALLOW_PROD_DESTRUCTIVE=1 以通过 env.py 的 prod guard"
  fi
  if (( DRY_RUN )); then
    info "  ${C_DIM}\$ ${guard}$REPO/.venv/bin/alembic upgrade head${C_OFF}"
    return 0
  fi
  if (( PROD_DB )); then
    ALLOW_PROD_DESTRUCTIVE=1 "$REPO/.venv/bin/alembic" upgrade head \
      || die "alembic upgrade head 失败（查看上方 alembic 输出；回滚/排查见 docs/guides/agent-safety.md）"
  else
    "$REPO/.venv/bin/alembic" upgrade head || die "alembic upgrade head 失败"
  fi
  ok "迁移完成（alembic head）"
}

case "$MIGRATE_MODE" in
  never)
    ok "跳过（--skip-migrate）"
    ;;
  always)
    run_migration
    ;;
  ask)
    if (( PROD_DB )) && (( YES )); then
      warn "生产形态库 + 非交互：跳过迁移。确认要执行请加 --migrate"
    elif confirm "执行 alembic upgrade head（目标库 $DB_NAME）？"; then
      (( DRY_RUN )) && info "  (dry-run：以上是回答 yes 后会执行的命令；非交互且未传 --migrate 时会跳过)"
      run_migration
    else
      warn "已跳过迁移。首次安装 / schema 有变更时需执行：bash scripts/envscripts/envsetup/install.sh --migrate"
    fi
    ;;
esac

# ============================================================
step "8/10 启动服务"
enable_start_unit() {   # enable_start_unit <unit>
  local unit=$1
  if (( DRY_RUN )); then info "  ${C_DIM}\$ systemctl --user enable $unit; start 或 restart${C_OFF}"; return 0; fi
  if ! systemctl --user enable "$unit" >/dev/null 2>&1; then
    warn "enable $unit 失败"
  fi
  if systemctl --user is-active --quiet "$unit"; then
    systemctl --user restart "$unit"
    ok "$unit 已重启（代码有变更）"
  else
    systemctl --user start "$unit"
    ok "$unit 已启动"
  fi
}

if (( WITH_SERVICES == 0 )); then
  ok "跳过（--skip-services）"
else
  [[ -x "$REPO/.venv/bin/python" || $DRY_RUN == 1 ]] || die ".venv 不可用，无法启动服务"
  enable_start_unit "tts-erp.service"
  enable_start_unit "tts-erp-sync.service"
  if [[ -f "$UNIT_DIR/tts-erp-watchdog.timer" ]]; then
    if (( DRY_RUN )); then
      info "  ${C_DIM}\$ systemctl --user enable --now tts-erp-watchdog.timer${C_OFF}"
    else
      systemctl --user enable --now tts-erp-watchdog.timer >/dev/null 2>&1 \
        && ok "tts-erp-watchdog.timer 已启用（每 10min 巡检）" \
        || warn "启用 tts-erp-watchdog.timer 失败"
    fi
  fi
  if [[ -f "$UNIT_DIR/tts-erp-pgbackup.timer" ]]; then
    if (( DRY_RUN )); then
      info "  ${C_DIM}\$ systemctl --user enable --now tts-erp-pgbackup.timer${C_OFF}"
    else
      systemctl --user enable --now tts-erp-pgbackup.timer >/dev/null 2>&1 \
        && ok "tts-erp-pgbackup.timer 已启用（每小时备份）" \
        || warn "启用 tts-erp-pgbackup.timer 失败"
    fi
  fi
fi

# ============================================================
step "9/10 健康检查：/healthz"
PORT="$(env_get TTS_ERP_PORT "$HEALTH_URL_BASE_DEFAULT")"
HEALTHZ_URL="http://127.0.0.1:${PORT}/healthz"
HEALTHZ_BODY=""
if (( DRY_RUN )); then
  info "  ${C_DIM}\$ curl -sS $HEALTHZ_URL${C_OFF}"
elif (( WITH_SERVICES == 0 )); then
  ok "跳过（--skip-services）"
else
  for _ in $(seq 1 30); do
    if HEALTHZ_BODY="$(curl -fsS -m 2 "$HEALTHZ_URL" 2>/dev/null)"; then break; fi
    sleep 1
  done
  if [[ -z "$HEALTHZ_BODY" ]]; then
    err "健康检查失败：$HEALTHZ_URL 无响应"
    err "排查: journalctl --user -u tts-erp -n 50 ；tail -50 $REPO/logs/stderr.log"
    exit 1
  fi
  if ! printf '%s' "$HEALTHZ_BODY" | grep -Eq '"service"[[:space:]]*:[[:space:]]*"tts-erp-v2"'; then
    err "健康检查返回的不是 v2 服务：$HEALTHZ_BODY"
    err "若只有 {\"status\":\"ok\"}，说明跑的是 v1 旧 tts_erp.py —— 见 docs/ops/tts-erp.md"
    exit 1
  fi
  ok "healthz: $HEALTHZ_BODY"
  if systemctl --user is-active --quiet tts-erp-sync.service; then
    ok "tts-erp-sync.service: active"
  else
    warn "tts-erp-sync.service 未 active：物流/广告同步会停摆 → systemctl --user status tts-erp-sync.service"
  fi
fi

# ============================================================
step "10/10 安装摘要"
if (( DRY_RUN )); then
  info "dry-run 结束：以上命令均未执行。"
  exit 0
fi
printf '  %-22s %s\n' "仓库" "$REPO"
printf '  %-22s %s\n' "内网" "http://127.0.0.1:${PORT}"
printf '  %-22s %s\n' "公网" "https://daqiang.nat100.top（不带端口，路径前缀 /tts）"
printf '  %-22s %s\n' "健康检查" "$HEALTHZ_URL"
printf '  %-22s %s\n' "API 日志" "journalctl --user -u tts-erp -n 50 ；$REPO/logs/{stdout,stderr}.log"
printf '  %-22s %s\n' "同步日志" "journalctl --user -u tts-erp-sync -n 50"
printf '  %-22s %s\n' "同步巡检" "journalctl --user -u tts-erp-watchdog -n 50 ；$REPO/logs/watchdog.log"
echo
info "常用命令："
info "  systemctl --user status  tts-erp{,-sync}.service"
info "  systemctl --user restart tts-erp.service            # = bash restart.sh"
info "  systemctl --user restart tts-erp-sync.service       # 改 jobs/ 或 sync_worker/ 后必跑"
info "  bash $REPO/restart.sh"
echo
info "analytics ingest（Chrome 扩展）签发 token："
info "  $REPO/.venv/bin/python $REPO/api_keys.py create --role readwrite --name chrome-ext-prod --expires-days 365"
info "  （plaintext 只打印一次；多店铺用 --scopes \"seller:<shop-id>\"）"
echo
info "故障排查速查表：docs/ops/tts-erp.md ｜ 部署契约：docs/api/external-api.md"
ok "安装完成"
