#!/usr/bin/env bash
# scripts/oneoff_user_account_rollout.sh — 用户账号体系（user-account-authz）上线执行脚本
#
# 【人工执行】AGENTS.md §3：生产迁移与服务重启是 human-operated，agent 只在
# 测试形态库验证。本脚本把该人工流程编排为一步，含显式确认护栏。
#
# 依据：
#   docs/design/user-account-authz-design.md §14 上线步骤
#   docs/guides/agent-safety.md §2/§3  迁移护栏（ALLOW_PROD_DESTRUCTIVE=1 仅限
#                                   本脚本的 alembic 子进程，见下）
#   scripts/envsetup/install.sh                同仓部署脚本的确认/输出约定（本脚本沿用）
#
# 前置条件：
#   在部署目录 /home/schan/tts-erp（services 的 WorkingDirectory）先同步代码：
#       cd /home/schan/tts-erp && git pull --ff-only origin master
#   合并后的 master 才含 alembic/versions/0052_user_accounts.py 与本脚本。
#
# 用法：
#   bash scripts/oneoff_user_account_rollout.sh                # 交互式
#   bash scripts/oneoff_user_account_rollout.sh --dry-run      # 只打印将执行的命令
#   bash scripts/oneoff_user_account_rollout.sh --skip-admin   # 不创建首个 admin
#   bash scripts/oneoff_user_account_rollout.sh --admin-user alice
#
# 步骤：
#   0 预检   部署目录 / .env / venv / origin 同步 / argon2-cffi 依赖
#   1 迁移   alembic upgrade head（输入库名确认；ALLOW_PROD_DESTRUCTIVE=1
#            仅注入本步子进程，与 scripts/envsetup/install.sh 的迁移步骤同模式）
#   2 种子   accounts.cli sync-permissions（幂等，兜底校准种子数据）
#   3 重启   restart.sh（API）+ tts-erp-sync.service
#   4 冒烟   /healthz、/v2/auth/login、/v2/auth/me
#   5 管理员 accounts.cli create-user <user> --role admin（getpass 交互输密码）
#   6 摘要   后续手动验证清单
#
# 安全约束：
#   * 不执行任何 DELETE/TRUNCATE/DROP；alembic 0052 为纯增量（只建新表）。
#   * 生产迁移需在提示后【逐字输入目标库名】确认；--dry-run 不触碰数据库。
#   * 本脚本不读取、不打印、不修改任何密钥；.env 只解析连接串与端口。
set -euo pipefail

DEPLOY_DIR=/home/schan/tts-erp   # 与 restart.sh 一致；services 的 WorkingDirectory
ADMIN_USER=""
SKIP_ADMIN=0
DRY_RUN=0

# ---------- 输出（与 scripts/envsetup/install.sh 同风格） ----------
if [[ -t 1 ]]; then
  C_OK=$'\033[1;32m'; C_WARN=$'\033[1;33m'; C_ERR=$'\033[1;31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_OFF=""
fi
info() { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s!%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
err()  { printf '%s✗%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; }
die()  { err "$*"; exit 1; }
step() { printf '\n%s== %s ==%s\n' "$C_DIM" "$*" "$C_OFF"; }

# dry-run 下只打印命令；预检失败降级为警告（让 dry-run 能看到完整计划）。
run() {
  printf '  %s$ %s%s\n' "$C_DIM" "$*" "$C_OFF"
  (( DRY_RUN )) && return 0
  "$@"
}
fail_pre() {
  if (( DRY_RUN )); then warn "预检失败（dry-run 仅提示，不中断）：$*"
  else die "$*"; fi
}

# ---------- 选项 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-admin) SKIP_ADMIN=1 ;;
    --admin-user) ADMIN_USER="${2:-}"; shift ;;
    --admin-user=*) ADMIN_USER="${1#*=}" ;;
    -h|--help)
      awk '/^set -euo/{exit} NR>1 && /^#/{sub(/^# ?/,""); print}' "${BASH_SOURCE[0]}"
      exit 0 ;;
    *) die "未知参数: $1（-h 看用法）" ;;
  esac
  shift
done
[[ -n "$ADMIN_USER" ]] || ADMIN_USER="admin"

# ---------- 0. 预检 ----------
step "0 preflight"
[[ "$PWD" == "$DEPLOY_DIR" || "$(pwd -P)" == "$DEPLOY_DIR" ]] \
  || { cd "$DEPLOY_DIR" 2>/dev/null || fail_pre "部署目录不存在: $DEPLOY_DIR"; }
ok "部署目录 $DEPLOY_DIR"

[[ -f "$DEPLOY_DIR/.env" ]] || fail_pre "缺 .env（运维提供，本脚本不代管）"
[[ -x "$DEPLOY_DIR/.venv/bin/alembic" ]] || fail_pre "缺 .venv/bin/alembic（先 bash scripts/envsetup/install.sh 装依赖）"

# .env 只解析两项；不 source 整个文件（避免特殊字符被 shell 解释），不回显。
env_get() {
  grep -E "^$1=" "$DEPLOY_DIR/.env" | head -1 | cut -d= -f2- \
    | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//'
}
DB_URL="$(env_get TTS_ERP_DB_URL)"
PORT="${TTS_ERP_PORT:-$(env_get TTS_ERP_PORT)}"
PORT="${PORT:-9877}"
[[ -n "$DB_URL" ]] || fail_pre ".env 缺 TTS_ERP_DB_URL"
DB_NAME="${DB_URL##*/}"; DB_NAME="${DB_NAME%%\?*}"
ok "目标库：$DB_NAME（连接串不回显）"

# origin 同步检查：不自动 pull（脚本自身在被 pull 的树里，运行中自改文件会
# 破坏 bash 的增量读取）；落后则给出明确指令退出。
if git -C "$DEPLOY_DIR" fetch origin -q 2>/dev/null; then
  if git -C "$DEPLOY_DIR" merge-base --is-ancestor origin/master HEAD; then
    ok "代码已同步 origin/master（HEAD=$(git -C "$DEPLOY_DIR" rev-parse --short HEAD)）"
  else
    fail_pre "代码落后 origin/master。请先执行：cd $DEPLOY_DIR && git pull --ff-only origin master"
  fi
else
  warn "git fetch 失败（离线？）——继续，但请确认代码已含 alembic/versions/0052_user_accounts.py"
fi
[[ -f "$DEPLOY_DIR/alembic/versions/0052_user_accounts.py" ]] \
  || fail_pre "缺 alembic/versions/0052_user_accounts.py（先同步代码）"

# argon2-cffi 依赖（账号体系的密码哈希）
if "$DEPLOY_DIR/.venv/bin/python" -c "import argon2" 2>/dev/null; then
  ok "argon2-cffi 可用"
else
  run "$DEPLOY_DIR/.venv/bin/pip" install 'argon2-cffi>=25.1,<26'
fi

# ---------- 1. 生产迁移 ----------
step "1 database migration (alembic upgrade head)"
info "0052 为纯增量迁移（仅新增 security.* 六表 + 种子，不改现有表）。"
info "alembic/env.py 对生产形态库默认拒绝执行；本步在你逐字确认库名后，"
info "仅为本步的 alembic 子进程注入 ALLOW_PROD_DESTRUCTIVE=1（同 scripts/envsetup/install.sh）。"
if (( DRY_RUN )); then
  info "（dry-run 跳过确认）"
else
  printf '请逐字输入目标库名 [%s] 以确认生产迁移（其他输入取消）: ' "$DB_NAME"
  read -r answer
  [[ "$answer" == "$DB_NAME" ]] || die "确认不匹配（输入了「$answer」），已取消"
fi
run env ALLOW_PROD_DESTRUCTIVE=1 "$DEPLOY_DIR/.venv/bin/alembic" current
run env ALLOW_PROD_DESTRUCTIVE=1 "$DEPLOY_DIR/.venv/bin/alembic" upgrade head
run env ALLOW_PROD_DESTRUCTIVE=1 "$DEPLOY_DIR/.venv/bin/alembic" current

# ---------- 2. 种子校准（幂等） ----------
step "2 sync permissions (idempotent)"
export TTS_ERP_DB_URL="$DB_URL"   # accounts.cli 不自载 .env（alembic 的 env.py 才自载）
run "$DEPLOY_DIR/.venv/bin/python" -m tts_erp_v2.accounts.cli sync-permissions

# ---------- 3. 重启服务 ----------
step "3 restart services"
info "涉及 middleware/ / api/ 改动：API 与 sync-worker 都需要重启。"
run bash "$DEPLOY_DIR/restart.sh"
run systemctl --user restart tts-erp-sync.service
run systemctl --user is-active tts-erp.service tts-erp-sync.service

# ---------- 4. 冒烟 ----------
step "4 smoke"
smoke() { # name url expect_substring
  if (( DRY_RUN )); then printf '  %s$ curl %s (expect %s)%s\n' "$C_DIM" "$2" "$3" "$C_OFF"; return 0; fi
  local body
  if body=$(curl -sf --max-time 10 "$2" 2>/dev/null) && [[ "$body" == *"$3"* ]]; then
    ok "$1"
  else
    fail_pre "$1 未通过：$2（期望包含 $3）"
  fi
}
smoke "healthz"        "http://127.0.0.1:$PORT/healthz"       "tts-erp"
smoke "登录页"          "http://127.0.0.1:$PORT/v2/auth/login" "login-form"
smoke "会话状态接口"    "http://127.0.0.1:$PORT/v2/auth/me"    "authenticated"

# ---------- 5. 首个 admin 账号 ----------
step "5 first admin account"
if (( SKIP_ADMIN )); then
  info "跳过（--skip-admin）。之后可执行："
  info "  .venv/bin/python -m tts_erp_v2.accounts.cli create-user <用户名> --role admin"
elif (( DRY_RUN )); then
  printf '  %s$ .venv/bin/python -m tts_erp_v2.accounts.cli create-user %s --role admin（交互式输密码）%s\n' \
    "$C_DIM" "$ADMIN_USER" "$C_OFF"
else
  info "即将创建管理员账号「$ADMIN_USER」（角色 admin）；密码在提示下交互输入，不进 shell 历史。"
  if "$DEPLOY_DIR/.venv/bin/python" -m tts_erp_v2.accounts.cli \
       create-user "$ADMIN_USER" --role admin; then
    ok "已创建 $ADMIN_USER"
  else
    warn "创建失败——若因「用户名已存在」，可改密码："
    info  "  .venv/bin/python -m tts_erp_v2.accounts.cli reset-password $ADMIN_USER"
  fi
fi

# ---------- 6. 摘要 ----------
step "6 done"
ok "上线流程执行完毕。后续手动验证（design §14-5）："
info "  1) 浏览器登录 → 各页面进出（侧边栏应按角色收敛）→ 登出"
info "  2) 旧 HMAC cookie（tts_session）应失效，重定向到登录页"
info "  3) API key 回归：curl -H 'Authorization: Bearer <KEY>' http://127.0.0.1:$PORT/v2/auth/me"
info "  4) 账号管理：http://127.0.0.1:$PORT/v2/pages/users（需 page:users）"
