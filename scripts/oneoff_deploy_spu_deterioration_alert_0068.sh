#!/usr/bin/env bash
# oneoff_deploy_spu_deterioration_alert_0068.sh
# SPU 利润劣化预警：人工部署 + 验证 runbook
# （0068 生产迁移 + 服务重启 + live e2e 冒烟）
#
# 用法（都由人执行）：
#   bash scripts/oneoff_deploy_spu_deterioration_alert_0068.sh               # 交互式全流程
#   bash scripts/oneoff_deploy_spu_deterioration_alert_0068.sh --yes         # 跳过确认提示
#   bash scripts/oneoff_deploy_spu_deterioration_alert_0068.sh --verify-only # 只跑验证段（第 5-6 步）
#
# 流程：
#   1) 同步 /home/schan/tts-erp 到 origin/master（服务从这个目录跑代码，
#      不同步的话重启也部署不上新功能）
#   2) 对生产库 tts_erp 执行 alembic upgrade head
#      （0068：新增 analytics schema + spu_deterioration_alerts 表）
#   3) 重启 API（restart.sh）
#   4) 重启 sync-worker（预警日更物化 job 在 tts_erp_v2/jobs/，不重启调度器不跑）
#   5) 校验 /endpoints 出现新路由（无需凭据）
#   6) .env 里有 TTS_ERP_SERVICE_KEY 时跑 live e2e 冒烟（readonly key 即可）
#
# 安全约束：
#   - 只作用于生产库 tts_erp；检测到库名不是 tts_erp 会直接中止
#   - 迁移幂等：已是 0068 则跳过；不是预期的 0067/0068 则中止
#   - 本地 master 上 video-publish lane 的未推送提交 1738f0e 用 rebase 保留，
#     本脚本不会推送它（是否推送由该 lane 自己决定）
set -euo pipefail

REPO="/home/schan/tts-erp"
EXPECTED_PRE="0067_merge_price_and_publish"
EXPECTED_POST="0068_spu_deterioration_alert"
ASSUME_YES=0
VERIFY_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --yes) ASSUME_YES=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    *) echo "未知参数: $arg" >&2; exit 64 ;;
  esac
done

log()  { echo; echo "== $* =="; }
die()  { echo "中止：$*" >&2; exit 1; }
confirm() {
  [[ $ASSUME_YES == 1 ]] && return 0
  read -r -p "$1 [y/N] " ans
  [[ $ans == y || $ans == Y ]]
}

prod_version() {
  docker exec postgres psql -U postgres -d tts_erp -tAc \
    "SELECT version_num FROM alembic_version" | tr -d '[:space:]'
}

cd "$REPO"

# ---------- 前置检查 ----------
log "前置检查"
[[ -f .env ]] || die ".env 不存在"
[[ -x .venv/bin/alembic ]] || die ".venv/bin/alembic 不存在"
docker exec postgres true 2>/dev/null || die "docker 容器 postgres 不可用"

# shellcheck disable=SC1091
set -a; source .env; set +a
: "${TTS_ERP_DB_URL:?TTS_ERP_DB_URL 未设置}"
PORT="${TTS_ERP_PORT:-9877}"

DB_NAME="${TTS_ERP_DB_URL##*/}"; DB_NAME="${DB_NAME%%\?*}"
[[ "$DB_NAME" == "tts_erp" ]] || die "TTS_ERP_DB_URL 指向库 '$DB_NAME'，本脚本只允许操作 tts_erp"

CUR="$(prod_version)"
echo "当前生产 alembic 版本: $CUR"

# ---------- 第 1 步：同步代码 ----------
if [[ $VERIFY_ONLY == 0 ]]; then
  log "第 1 步：同步本地 master 到 origin/master"
  git fetch origin master
  LOCAL="$(git rev-parse HEAD)"
  REMOTE="$(git rev-parse origin/master)"
  if [[ "$LOCAL" == "$REMOTE" ]]; then
    echo "已是最新 ($REMOTE)，跳过"
  elif git merge-base --is-ancestor origin/master HEAD; then
    echo "已同步 origin/master 且本地仅多未推送提交（如 1738f0e），跳过"
  elif git merge-base --is-ancestor HEAD origin/master; then
    git merge --ff-only origin/master
    echo "已快进到 $(git rev-parse --short HEAD)"
  else
    echo "本地 master 有未推送提交（rebase 后会保留在 origin/master 之上）："
    git log --oneline origin/master..HEAD
    confirm "用 git rebase origin/master 同步（保留上述本地提交）？" || die "用户取消"
    # 只查已跟踪文件的改动；未跟踪文件（比如本脚本自身）不阻塞 rebase
    git diff --quiet && git diff --cached --quiet || die "工作区有未提交改动，先清理再重跑"
    git rebase origin/master || die "rebase 冲突，请手动解决后重跑本脚本（git rebase --abort 可回退）"
    echo "rebase 完成，HEAD=$(git rev-parse --short HEAD)（本地提交未推送）"
  fi

  # ---------- 第 2 步：生产迁移 ----------
  log "第 2 步：生产迁移 alembic upgrade head（tts_erp）"
  if [[ "$CUR" == "$EXPECTED_POST" ]]; then
    echo "已是 $EXPECTED_POST，跳过迁移"
  else
    [[ "$CUR" == "$EXPECTED_PRE" ]] || die "当前版本 '$CUR' 不是预期的 $EXPECTED_PRE，不自动处理，请人工排查"
    echo "将执行: $EXPECTED_PRE -> $EXPECTED_POST（新增 analytics schema + spu_deterioration_alerts）"
    confirm "确认对生产库 tts_erp 执行迁移？" || die "用户取消"
    # ALLOW_PROD_DESTRUCTIVE=1 是仓库生产护栏（alembic/env.py）要求的人类显式放行，
    # 只作用于这一条命令；0068 是纯增量（CREATE SCHEMA/TABLE/INDEX，无 DROP）。
    ALLOW_PROD_DESTRUCTIVE=1 .venv/bin/alembic upgrade head
    NEW="$(prod_version)"
    [[ "$NEW" == "$EXPECTED_POST" ]] || die "迁移后版本是 '$NEW'，预期 $EXPECTED_POST"
    docker exec postgres psql -U postgres -d tts_erp -tAc \
      "SELECT to_regclass('analytics.spu_deterioration_alerts')" | grep -q spu_deterioration_alerts \
      || die "迁移后找不到 analytics.spu_deterioration_alerts 表"
    echo "迁移完成：$NEW，analytics.spu_deterioration_alerts 已存在"
  fi

  # ---------- 第 2.5 步：播种页面权限点 ----------
  # 侧边栏按会话用户角色权限过滤；新页面对应的 page:xxx 权限点不进库，
  # 浏览器会话就看不到导航入口（service key 不受限所以 e2e 发现不了）。
  # seed_builtin_roles 是幂等 upsert，只补缺失行。
  log "第 2.5 步：播种页面权限点（sync-permissions，幂等）"
  .venv/bin/python -m tts_erp_v2.accounts.cli sync-permissions

  # ---------- 第 3 步：重启 API ----------
  log "第 3 步：重启 API（bash restart.sh）"
  bash restart.sh

  # ---------- 第 4 步：重启 sync-worker ----------
  log "第 4 步：重启 sync-worker（预警物化 job 由它调度）"
  systemctl --user restart tts-erp-sync.service
  systemctl --user --no-pager status tts-erp-sync.service | head -5
fi

# ---------- 第 5 步：路由验证（无需凭据） ----------
log "第 5 步：校验 /endpoints 出现预警路由"
sleep 2
ENDPOINTS="$(curl -sf "http://127.0.0.1:$PORT/endpoints")" || die "服务不可达：http://127.0.0.1:$PORT/endpoints"
for route in "/v2/analytics/spu-profit-deterioration" "/v2/pages/spu-profit-deterioration"; do
  echo "$ENDPOINTS" | grep -q "$route" || die "路由缺失：$route（服务可能还没跑新代码）"
  echo "OK $route"
done

# ---------- 第 6 步：live e2e 冒烟（需要 readonly key） ----------
log "第 6 步：live e2e 冒烟"
if [[ -z "${TTS_ERP_SERVICE_KEY:-}" ]]; then
  cat <<EOF
.env 里没有 TTS_ERP_SERVICE_KEY，跳过 e2e。
请把 readonly service key 加进 /home/schan/tts-erp/.env：

    TTS_ERP_SERVICE_KEY=<readonly-key>

然后重跑验证段：

    bash scripts/oneoff_deploy_spu_deterioration_alert_0068.sh --verify-only
EOF
  exit 2
fi

# e2e 冒烟是纯 HTTP（不连数据库），但导入链要求 TTS_ERP_DB_URL 存在。
# 给它 test 形状的 URL（库名换成 tts_erp_test_template）：tests/conftest.py 的
# prod 护栏只对 tts_erp/tts_erp_prod* fail-fast；TTS_ERP_TEST_NO_DOTENV=1 阻止
# 根 conftest 再用 .env 里的生产 URL 覆盖。service key 由 tests/e2e/conftest.py
# 自己从仓库根 .env 读取，不受影响。
TEST_SHAPED_URL="${TTS_ERP_DB_URL%/tts_erp}/tts_erp_test_template"
[[ "$TEST_SHAPED_URL" == "$TTS_ERP_DB_URL" ]] && die "无法从 TTS_ERP_DB_URL 派生 test 形状 URL"
env -u TTS_ERP_DB_URL_TEST TTS_ERP_TEST_NO_DOTENV=1 TTS_ERP_DB_URL="$TEST_SHAPED_URL" \
  .venv/bin/python -m pytest tests/e2e/test_spu_deterioration_alert_smoke.py -v

log "全部完成"
echo "请把本输出（尤其 e2e 结果）发给 agent，用于关闭 tower-do 上的 roi-alert-review。"
