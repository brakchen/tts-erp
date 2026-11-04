#!/usr/bin/env bash
# scripts/prod_deploy.sh — 一键部署生产环境 207.57.126.199（ssh alias tts-erp-prod）。
#
# 部署内容：
#   1. git fetch 后在临时 worktree 按指定 commit 构建前端产物；
#   2. tar 管道同步代码与前端产物到 207:/opt/tts-erp（代码目录 tts_erp_v2 原子替换，
#      保留 tts_erp_v2.prev 作回滚副本；.env / .venv / logs 永不在包内）；
#   3. systemctl restart tts-erp.service（系统级服务）；
#   4. 本机+公网只读校验：healthz / 登录页 / releaseId。
#
# 用法：
#   bash scripts/prod_deploy.sh [选项] [ref]
#     ref           要部署的 git 引用，默认 origin/master（部署前自动 fetch）
#     --yes         跳过交互确认（默认需逐字输入 207.57.126.199）
#     --dry-run     只打印计划，不做任何改动
#     --verify-only 只做部署后的只读校验（207 本机 + 公网）
#     --sync-deps   pyproject 变化时在 207 执行 pip install -e .（默认只警告）
#     -h, --help    显示帮助
#
# 边界：不执行数据库迁移（生产迁移一律人工操作，检测到新迁移只警告）；
# 不覆盖 207 的 .env / .venv / logs；不在输出中打印任何密钥。
set -euo pipefail

DL_REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/deploy_lib.sh
. "$DL_REPO_ROOT/scripts/deploy_lib.sh"

PROD_HOST="${TTS_ERP_PROD_HOST:-tts-erp-prod}"
PROD_DIR="${TTS_ERP_PROD_DIR:-/opt/tts-erp}"
PUBLIC_ORIGIN="${TTS_ERP_PROD_PUBLIC_ORIGIN:-https://chen.daqiangplatform.com}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15)
CONFIRM_WORD="207.57.126.199"

REF="origin/master"
DRY_RUN=0
VERIFY_ONLY=0
SYNC_DEPS=0
ASSUME_YES=0

usage() { sed -n '2,30p' "$0"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --yes) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    --sync-deps) SYNC_DEPS=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) dl_die "未知选项：$1（-h 查看帮助）" ;;
    *) [ "$REF" = "origin/master" ] || dl_die "只能指定一个 ref"; REF="$1" ;;
  esac
  shift
done

# 207 本机只读校验（经 ssh 在 207 上 curl 本机服务）
remote_verify() {  # $1=期望 releaseId（可空）
  local expect="$1" port
  port="$(ssh "${SSH_OPTS[@]}" "$PROD_HOST" \
    "grep -E '^TTS_ERP_PORT=' '$PROD_DIR/.env' 2>/dev/null | cut -d= -f2" || true)"
  port="${port:-9878}"
  # ssh 会重新拼接参数并丢失空串参数，故用环境变量前缀传参而不是位置参数
  ssh "${SSH_OPTS[@]}" "$PROD_HOST" "PORT='$port' EXPECT='$expect' bash -s" <<'REMOTE_VERIFY'
set -u
rc=0
body="$(curl -fsS --max-time 15 "http://127.0.0.1:$PORT/healthz" 2>/dev/null)" \
  && echo "[207] 健康检查通过：$body" || { echo "[207][警告] 健康检查失败"; rc=1; }
rel="$(curl -fsS --max-time 15 "http://127.0.0.1:$PORT/tts/static/frontend/release.json" 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("releaseId",""))' 2>/dev/null)"
if [ -n "$EXPECT" ]; then
  [ "$rel" = "$EXPECT" ] && echo "[207] 前端版本一致：$rel" \
    || { echo "[207][警告] 前端版本不符：期望 $EXPECT，实际 ${rel:-未知}"; rc=1; }
else
  echo "[207] 当前前端版本：${rel:-未知}"
fi
exit "$rc"
REMOTE_VERIFY
}

if [ "$VERIFY_ONLY" = "1" ]; then
  dl_log "207 已部署 commit：$(ssh "${SSH_OPTS[@]}" "$PROD_HOST" "cat '$PROD_DIR/.deployed-commit' 2>/dev/null" || echo 未知)"
  remote_verify ""
  dl_verify "公网" "$PUBLIC_ORIGIN"
  exit $?
fi

dl_log "同步远端引用（git fetch origin）…"
git -C "$DL_REPO_ROOT" fetch origin >&2 || dl_warn "fetch 失败，按本地已有引用继续"

COMMIT="$(dl_resolve_commit "$REF")"
SUBJECT="$(git -C "$DL_REPO_ROOT" log -1 --format='%s' "$COMMIT")"
dl_log "目标 commit：${COMMIT:0:12} $SUBJECT"

OLD_COMMIT="$(ssh "${SSH_OPTS[@]}" "$PROD_HOST" "cat '$PROD_DIR/.deployed-commit' 2>/dev/null" || true)"

# 迁移与依赖变化预警（仅在已知旧部署 commit 时可比对）
if [ -n "$OLD_COMMIT" ] && git -C "$DL_REPO_ROOT" cat-file -e "$OLD_COMMIT" 2>/dev/null; then
  if git -C "$DL_REPO_ROOT" diff --name-only "$OLD_COMMIT" "$COMMIT" -- \
      alembic/versions 2>/dev/null | grep -q .; then
    dl_warn "本次部署包含新 alembic 迁移：生产迁移由人工执行，本脚本不会代跑"
  fi
  DEPS_CHANGED=0
  git -C "$DL_REPO_ROOT" diff --name-only "$OLD_COMMIT" "$COMMIT" -- \
    pyproject.toml uv.lock requirements.txt 2>/dev/null | grep -q . && DEPS_CHANGED=1 || true
else
  dl_warn "207 上无已知部署基线（.deployed-commit），跳过迁移/依赖差异检查"
  DEPS_CHANGED=0
fi

if [ "$DRY_RUN" = "1" ]; then
  OLD_DISPLAY="未知"
  [ -n "$OLD_COMMIT" ] && OLD_DISPLAY="${OLD_COMMIT:0:12}"
  cat <<EOF
[dry-run] 将部署到生产 $CONFIRM_WORD（$PROD_HOST:$PROD_DIR）：
  commit   ${COMMIT:0:12} $SUBJECT
  当前部署 $OLD_DISPLAY
  步骤：
  1. 临时 worktree 构建前端产物
  2. tar 管道同步到 $PROD_DIR（tts_erp_v2 原子替换，留 .prev 回滚副本）
  3. systemctl restart tts-erp.service
  4. 207 本机 + 公网 $PUBLIC_ORIGIN 只读校验
  不执行：数据库迁移、.env/.venv/logs 修改
EOF
  exit 0
fi

# 生产确认门：交互逐字输入主机 IP，或显式 --yes
if [ "$ASSUME_YES" != "1" ]; then
  [ -t 0 ] || dl_die "非交互环境必须显式传 --yes 才允许部署生产"
  printf '即将部署生产 %s（%s），commit %s %s\n' "$CONFIRM_WORD" "$PROD_HOST" "${COMMIT:0:12}" "$SUBJECT"
  read -r -p "请输入 $CONFIRM_WORD 以确认: " answer
  [ "$answer" = "$CONFIRM_WORD" ] || dl_die "确认字符串不匹配，已中止"
fi

trap dl_cleanup EXIT
WT="$(dl_prepare_build_worktree "$COMMIT")"
RELEASE_ID="$(dl_build_frontend "$WT")"

dl_log "同步代码与前端产物到 $PROD_HOST:$PROD_DIR …"
dl_tar_stream "$WT" | ssh "${SSH_OPTS[@]}" "$PROD_HOST" \
  "set -euo pipefail; cd '$PROD_DIR'; rm -rf .deploy-staging; mkdir .deploy-staging; tar -xf - -C .deploy-staging" \
  || dl_die "传输到 207 失败"

ssh "${SSH_OPTS[@]}" "$PROD_HOST" bash -s -- "$COMMIT" "$PROD_DIR" \
  < "$DL_REPO_ROOT/scripts/deploy_remote_apply.sh" \
  || dl_die "207 端替换失败（若代码目录损坏，可用 $PROD_DIR/tts_erp_v2.prev 人工回滚）"

if [ "${DEPS_CHANGED:-0}" = "1" ]; then
  if [ "$SYNC_DEPS" = "1" ]; then
    dl_log "同步 207 Python 依赖（pip install -e）…"
    ssh "${SSH_OPTS[@]}" "$PROD_HOST" "'$PROD_DIR/.venv/bin/pip' install -e '$PROD_DIR'" >&2 \
      || dl_die "207 依赖安装失败"
  else
    dl_warn "pyproject/依赖清单有变化；如需安装新依赖请加 --sync-deps 重跑"
  fi
fi

dl_log "重启 207 tts-erp.service …"
ssh "${SSH_OPTS[@]}" "$PROD_HOST" \
  'systemctl restart tts-erp.service && sleep 3 && systemctl is-active tts-erp.service' \
  | grep -q '^active$' \
  || dl_die "207 服务重启后未 active，请查 ssh $PROD_HOST journalctl -u tts-erp -n 50"

remote_verify "$RELEASE_ID" || dl_die "207 本机校验未通过"
dl_verify "公网" "$PUBLIC_ORIGIN" "$RELEASE_ID" || dl_die "公网校验未通过（nginx 回源或缓存问题）"

dl_log "完成：生产 $CONFIRM_WORD 已部署 ${COMMIT:0:12}，前端 $RELEASE_ID"
