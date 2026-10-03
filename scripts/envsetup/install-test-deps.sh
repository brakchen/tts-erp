#!/usr/bin/env bash
# scripts/envscripts/envsetup/install-test-deps.sh — tts-erp 测试前置依赖一键安装（幂等，可重复执行）
#
# 目标：让 `bash scripts/test_isolated.sh ...` 在一台干净机器上能直接跑起来。
#
# 依据：
#   docs/guides/agent-testing.md    测试入口与隔离机制
#   docs/guides/commands-reference.md 命令速查
#   scripts/test_isolated.sh     唯一标准测试入口（克隆 tts_erp_test_template 跑临时库）
#
# 用法：
#   bash scripts/envscripts/envsetup/install-test-deps.sh              # 交互式
#   sudo bash scripts/envscripts/envsetup/install-test-deps.sh         # 一次性装完系统包 + venv 依赖
#   bash scripts/envscripts/envsetup/install-test-deps.sh --dry-run    # 只打印将要执行的命令，不改动系统
#   bash scripts/envscripts/envsetup/install-test-deps.sh --check      # 只做体检，不装任何东西
#   bash scripts/envscripts/envsetup/install-test-deps.sh -h           # 全部选项
#
# 会做什么：
#   1 系统包     postgresql-client（psql / createdb / dropdb —— test_isolated.sh
#                建临时测试库 clone 必需）
#   2 venv 依赖  pytest / pytest-asyncio / pytest-cov + pyproject 的 dev 依赖（tinycss2）
#   3 .env.test  缺失时按 .env 的连接串生成，库名固定 tts_erp_v3_test
#   4 体检       测试库存在性（tts_erp_test_template / tts_erp_v3_test）
#
# 安全约束（AGENTS.md §3、docs/guides/agent-safety.md）：
#   * 只装工具与测试依赖，**绝不**对任何数据库执行 DELETE/TRUNCATE/DROP/alembic。
#   * .env.test 只会指向测试形库名（含 "test"），绝不写 tts_erp / tts_erp_prod。
#   * 不创建、不修改任何密钥；.env 由运维提供，只读取连接串以派生测试连接串。
#   * 需要 root 的步骤（apt）单独列出；非 root 时打印确切命令并退出码 1，
#     由人工执行，脚本不自行提权。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

DRY_RUN=0
CHECK_ONLY=0
SKIP_APT=0

# ---------- 选项 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --check)   CHECK_ONLY=1; shift ;;
    --skip-apt) SKIP_APT=1; shift ;;
    -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1（用 -h 看全部选项）" >&2; exit 2 ;;
  esac
done

GREEN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; DIM=$'\033[2m'; RST=$'\033[0m'
ok()   { echo "  ${GREEN}[OK]${RST}   $*"; }
warn() { echo "  ${YEL}[WARN]${RST} $*"; }
err()  { echo "  ${RED}[FAIL]${RST} $*"; }
note() { echo "  ${DIM}       $*${RST}"; }
run()  {
  if [[ $DRY_RUN -eq 1 ]]; then echo "  ${DIM}[dry-run]${RST} $*"; else eval "$@"; fi
}

echo "== tts-erp 测试前置依赖安装 =="
MODE="实际执行"
if [[ $DRY_RUN -eq 1 ]]; then MODE="dry-run"; fi
if [[ $CHECK_ONLY -eq 1 ]]; then MODE="check-only"; fi
echo "   仓库: $REPO"
echo "   模式: $MODE"
echo

# ---------- 1. 系统包：PostgreSQL 客户端 ----------
echo "-- 1/4 PostgreSQL 客户端工具（test_isolated.sh 建临时库需要 psql/createdb/dropdb）"
MISSING=()
for bin in psql createdb dropdb; do
  if command -v "$bin" >/dev/null 2>&1; then
    ok "$bin 已就绪 ($(command -v "$bin"))"
  else
    err "$bin 缺失"
    MISSING+=("$bin")
  fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  if [[ $CHECK_ONLY -eq 1 || $SKIP_APT -eq 1 ]]; then
    warn "缺少 ${MISSING[*]}；跳过安装"
  elif [[ $DRY_RUN -eq 1 ]]; then
    note "[dry-run] 将执行: sudo apt-get update && sudo apt-get install -y postgresql-client"
  elif [[ $EUID -eq 0 ]]; then
    ok "以 root 运行，直接安装 postgresql-client"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-client
  else
    err "安装 postgresql-client 需要 root 权限。请手动执行下面这条命令后重跑本脚本："
    echo
    echo "      sudo apt-get update && sudo apt-get install -y postgresql-client"
    echo
    note "说明：宿主机客户端 16.x 对 Docker 里的 PostgreSQL 18.x 服务端完全够用"
    note "（createdb/dropdb/psql 是协议层操作，版本向前兼容），无需安装 18 客户端。"
    APT_BLOCKED=1
  fi
fi
echo

# ---------- 2. venv 测试依赖 ----------
echo "-- 2/4 venv 测试依赖（pytest、tinycss2 等不在 pyproject 运行时依赖里）"
VENV_PY="$REPO/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  err ".venv 不存在或不可执行: $VENV_PY"
  note "先跑 bash scripts/envsetup/install.sh（或 python3 -m venv .venv && .venv/bin/pip install -e .）"
else
  if "$VENV_PY" -c "import pytest, pytest_asyncio, tinycss2" 2>/dev/null; then
    ok "pytest 已就绪 ($("$VENV_PY" -m pytest --version 2>/dev/null | head -1))"
  elif [[ $CHECK_ONLY -eq 1 ]]; then
    err "pytest / pytest-asyncio / tinycss2 未安装"
  else
    run "$VENV_PY -m pip install -q pytest pytest-asyncio pytest-cov"
    run "$VENV_PY -m pip install -q '.[dev]'"
    ok "pytest + dev 依赖已装入 venv"
  fi
fi
echo

# ---------- 3. .env.test ----------
echo "-- 3/4 .env.test（测试连接串，gitignored）"
ENV_TEST="$REPO/.env.test"
if [[ -f "$ENV_TEST" ]]; then
  ok ".env.test 已存在"
  grep -oE 'TTS_ERP_DB_URL_TEST=.*$' "$ENV_TEST" | sed 's/:[^:@]*@/:***@/' | sed 's/^/        /' || true
elif [[ $CHECK_ONLY -eq 1 ]]; then
  err ".env.test 缺失"
else
  if [[ ! -f "$REPO/.env" ]]; then
    err ".env 不存在，无法派生测试连接串；请运维提供 .env 后重跑"
  else
    run "python3 - <<'PY'
import re, pathlib
env = pathlib.Path('$REPO/.env').read_text()
url = re.search(r'^TTS_ERP_DB_URL=(.*)\$', env, re.M).group(1).strip().strip('\"').strip(\"'\")
base = url.rsplit('/', 1)[0]
test_url = base + '/tts_erp_v3_test'
p = pathlib.Path('$ENV_TEST')
p.write_text(
    '# 测试专用连接串（gitignored）。库名固定 tts_erp_v3_test，绝不能指向 tts_erp/tts_erp_prod。\\n'
    f'TTS_ERP_DB_URL_TEST={test_url}\\n'
    f'TTS_ERP_DB_URL={test_url}\\n',
    encoding='utf-8')
p.chmod(0o600)
print('        已生成 .env.test ->', test_url.rsplit('/',1)[1])
PY"
    ok ".env.test 已生成（0600，指向 tts_erp_v3_test）"
  fi
fi
echo

# ---------- 4. 测试库体检 ----------
echo "-- 4/4 测试库存在性（缺失时只提示，不自动建）"
# 用 venv 解释器跑：psycopg 装在 .venv 里，系统 python3 没有
if [[ -x "$VENV_PY" ]] || [[ $DRY_RUN -eq 1 ]]; then
  run "\"$VENV_PY\" - <<'PY'
import pathlib, re, sys
import psycopg
# 优先 .env.test（worktree 里只有它）；回退到 .env
for cand in ('$REPO/.env.test', '$REPO/.env'):
    p = pathlib.Path(cand)
    if p.is_file():
        env = p.read_text()
        break
else:
    print('        [MISS] .env.test / .env 均不存在，无法体检')
    sys.exit(0)
m = re.search(r'^TTS_ERP_DB_URL_TEST=(.*)\$', env, re.M) or re.search(r'^TTS_ERP_DB_URL=(.*)\$', env, re.M)
url = m.group(1).strip().strip('\"').strip(\"'\") if m else ''
if not url:
    print('        [MISS] 连接串为空，无法体检')
    sys.exit(0)
admin = url.rsplit('/', 1)[0] + '/postgres'
admin = admin.replace('postgresql+psycopg://', 'postgresql://')
need = ['tts_erp_test_template', 'tts_erp_v3_test']
with psycopg.connect(admin) as c:
    have = {r[0] for r in c.execute(\"SELECT datname FROM pg_database\").fetchall()}
for db in need:
    print(('        [OK]   ' if db in have else '        [MISS] ') + db)
PY"
else
  warn "venv 解释器不可用（$VENV_PY），跳过库体检"
fi
echo

# ---------- 汇总 ----------
echo "== 汇总 =="
if [[ "${APT_BLOCKED:-0}" -eq 1 ]]; then
  err "系统包未装完（需要 root）。执行完上面的 sudo 命令后重跑本脚本验证。"
  exit 1
fi
ok "测试前置依赖就绪。下一步："
echo "        bash scripts/test_isolated.sh fast"
echo "        bash scripts/test_isolated.sh fast tests/api/test_pages.py   # 单文件"
echo
note "说明：scripts/test_isolated.sh 是**唯一标准**测试入口（每个会话克隆"
note "tts_erp_test_template 成一次性 tts_erp_test_* 库，跑完 drop，并发安全）。"
note "scripts/test.sh 直连常驻库的 shared-DB 回退路径已弃用，勿再作为常规入口。"
