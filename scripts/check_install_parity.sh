#!/usr/bin/env bash
# check_install_parity.sh — 【只读】装配核对：一次性回答「我以为的 == 实际的 == 吗」
#
# 用途（Chief 第八轮裁定为第 0 项）：
#   装配声明唯一化之前，先让现状可核对。
#   本脚本【不修改任何东西】，只读 unit / 文件属主 / 配置生效值 / 凭据来源链。
#
# ★ 关键方法论：**"生效值"必须从【运行中进程的环境】读，不是从核对脚本自己的环境读。**
#   2026-11-09 实测：脚本自己 source 了 runtime.env，于是 get_setting 读到的
#   TTS_ERP_RUNTIME_ENV_FILE = ''（脚本环境里没有这一项），而真实进程里它有值
#   ⇒ 用脚本环境评估"生效值"会得出错误结论。本脚本改为从 /proc/<pid>/environ 读。
#
# 九个问题：
#   1. unit 实际 WorkingDirectory / ExecStart / EnvironmentFile
#   2. unit 文件属主
#   3. 锚文件属主（锚的判据是【属主】，不是【来源】）
#   4. AUTH_MODE 生效值 + 生效来源（resolved_from）
#   5. get_setting 的来源链
#   6. DB URL 在 get_setting 侧与 os.environ 侧各读到什么
#   7. cwd 下是否存在 .env
#   8. is_prod_shaped_db() 的判定输入 vs current_database()
#   9. 三个授权开关各读到什么
set +e
export LC_ALL=C
UNITS="${*:-tts-erp.service tts-erp-sync.service tts-erp-publish.service}"
PY=/home/schan/tts-erp/.venv/bin/python
# 凭据加载层只存在于 release worktree（master 没有 runtime_settings.py）。
PROD_TREE=/home/schan/tts-erp-releases/erp-runtime-secret-84ec0a49

echo "################ 装配核对（只读） ################"
echo "时间: $(date -u +%Y-%m-%dT%H:%M:%SZ)   主机: $(hostname)   用户: $(id -un) uid=$(id -u)"
echo

echo "################ 1-3. unit 实际形态与属主 ################"
for u in $UNITS; do
  echo "--- $u"
  systemctl --user show "$u" -p WorkingDirectory -p ExecStart -p EnvironmentFiles \
    -p FragmentPath -p MainPID -p Environment --no-pager 2>/dev/null | sed 's/^/    /'
  F=$(systemctl --user show "$u" -p FragmentPath --value 2>/dev/null)
  [ -f "$F" ] && echo "    unit 文件属主: $(stat -c '%U:%G %a' "$F")  $F"
  D=~/.config/systemd/user/$(basename "$u").d
  if [ -d "$D" ]; then
    echo "    drop-in 目录: $D"
    for f in "$D"/*.conf; do
      [ -f "$f" ] && echo "      $(stat -c '%U:%G %a' "$f")  $(basename "$f")"
    done
  fi
done
echo

echo "################ 3. 候选锚文件属主（锚 ≠ 应用进程属主 才算锚） ################"
for f in /home/schan/.config/tts-erp/runtime.env /etc/tts-erp/runtime.env \
         /etc/systemd/system/tts-erp.service /etc/systemd/system/tts-erp.service.d \
         /home/schan/tts-erp/.env "$PROD_TREE/.env"; do
  if [ -e "$f" ]; then echo "  存在  $(stat -c '%U:%G %a' "$f")  $f"
  else echo "  缺失  -  $f"; fi
done
echo

echo "################ 4-9. 生效值 / 来源链 / cwd .env / 授权开关 ################"
$PY - <<'PYEOF' 2>&1 | sed 's/^/  /'
import os, subprocess, sys
from pathlib import Path

RELEASE = "/home/schan/tts-erp-releases/erp-runtime-secret-84ec0a49"
sys.path.insert(0, "/home/schan/tts-erp")
sys.path.insert(0, RELEASE)          # release 必须最前（master 的包会遮蔽它）

def sec(t): print("\n--- " + t)

UNITS = ["tts-erp.service", "tts-erp-sync.service", "tts-erp-publish.service"]

def unit_env(unit):
    """unit 声明的 Environment + 运行中进程的真实环境（/proc/<pid>/environ）。"""
    out = {}
    try:
        raw = subprocess.run(["systemctl", "--user", "show", unit, "-p", "Environment",
                              "--value"], capture_output=True, text=True, timeout=10).stdout
        for item in raw.split():
            if "=" in item:
                k, v = item.split("=", 1); out[k] = v
    except Exception:
        pass
    try:
        pid = subprocess.run(["systemctl", "--user", "show", unit, "-p", "MainPID",
                              "--value"], capture_output=True, text=True, timeout=10).stdout.strip()
        if pid and pid != "0":
            data = Path(f"/proc/{pid}/environ").read_bytes()
            for kv in data.split(b"\0"):
                if b"=" in kv:
                    k, v = kv.decode("utf-8", "replace").split("=", 1)
                    out[k] = v          # 进程真实环境优先
    except Exception:
        pass
    return out

def parse_env_file(path):
    try:
        vals = {}
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip("\"'")
        return vals
    except Exception:
        return {}

def resolved_from(key, env, cwd):
    """自己实现来源判定（runtime_settings 未暴露 source API）。"""
    dotenv = env.get("TTS_ERP_DOTENV_FILE") or str(Path(cwd) / ".env")
    dmap = parse_env_file(dotenv) if Path(dotenv).is_file() else {}
    if key in dmap:
        return f"dotenv:{dotenv}"
    runtime = env.get("TTS_ERP_RUNTIME_ENV_FILE", "")
    rmap = parse_env_file(runtime) if runtime and Path(runtime).is_file() else {}
    if key in rmap:
        return f"runtime_file:{runtime}"
    if key in env:
        return "process_env"
    return "default"

def effective(key, env, cwd, default):
    dotenv = env.get("TTS_ERP_DOTENV_FILE") or str(Path(cwd) / ".env")
    dmap = parse_env_file(dotenv) if Path(dotenv).is_file() else {}
    if key in dmap:
        return dmap[key]
    runtime = env.get("TTS_ERP_RUNTIME_ENV_FILE", "")
    rmap = parse_env_file(runtime) if runtime and Path(runtime).is_file() else {}
    if key in rmap:
        return rmap[key]
    return env.get(key, default)

for unit in UNITS:
    sec(f"unit: {unit}")
    env = unit_env(unit)
    cwd = subprocess.run(["systemctl", "--user", "show", unit, "-p", "WorkingDirectory",
                          "--value"], capture_output=True, text=True).stdout.strip() or os.getcwd()
    print(f"  运行中进程环境键数 = {len(env)}")
    print(f"  WorkingDirectory    = {cwd}")
    print(f"  cwd 下隐式 .env 存在 = {Path(cwd, '.env').is_file()}")

    for key, default in (("TTS_ERP_AUTH_MODE", "off"),
                          ("TTS_ERP_RUNTIME_ENV_FILE", ""),
                          ("TTS_ERP_DOTENV_FILE", ""),
                          ("ALLOW_PROD_DESTRUCTIVE", "<unset>"),
                          ("ALLOW_PROD_PURGE", "<unset>"),
                          ("TTS_ERP_ENVIRONMENT", "<unset>")):
        val = effective(key, env, cwd, default)
        if key.endswith("DB_URL") and isinstance(val, str) and "@" in val:
            val = val.split("://", 1)[0] + "://***:***@" + val.split("@", 1)[1]
        print(f"  {key:<28} 生效值={val!r}")
        print(f"  {'':<28} 来源  ={resolved_from(key, env, cwd)!r}")

    # DB URL 两侧
    gs = effective("TTS_ERP_DB_URL", env, cwd, "")
    oe = env.get("TTS_ERP_DB_URL", "")
    def dbname(u):
        try:
            from urllib.parse import urlsplit
            return urlsplit(u).path.lstrip("/") or "(none)"
        except Exception:
            return "(unparsable)"
    print(f"  DB URL get_setting 侧库名 = {dbname(gs) if gs else '(empty)'}")
    print(f"  DB URL os.environ 侧库名 = {dbname(oe) if oe else '(empty)'}")
    print(f"  两侧库名一致 = {(dbname(gs) if gs else '') == (dbname(oe) if oe else '')}")

# 数据库事实 vs 配置判定
sec("当前数据库事实 vs 配置判定")
try:
    from sqlalchemy import text
    from tts_erp_v2.db.base import get_session_factory
    f = get_session_factory(); s = f()
    try:
        print(f"  current_database() = {s.execute(text('SELECT current_database()')).scalar()!r}")
    finally:
        s.close()
except Exception as exc:
    print(f"  <无法读取: {exc}>")
try:
    from tts_erp_v2.api.deps import is_prod_shaped_db
    import inspect
    n = len(inspect.signature(is_prod_shaped_db).parameters)
    print(f"  is_prod_shaped_db() 参数数 = {n} → 判定 = "
          + (str(is_prod_shaped_db()) if n == 0 else "<需要参数，见源码>"))
except Exception as exc:
    print(f"  <is_prod_shaped_db 不可用: {exc}>")
PYEOF

echo
echo "################ 结论提示 ################"
cat <<'EOF'
  1) 装配声明（install.sh / deploy_lib.sh / prod_deploy.sh）三者互相冲突，
     且 HP desk 生产单元不在任何脚本覆盖内 ⇒ 本脚本是核对现状的唯一手段。
  2) 锚的判据是【属主】，不是【来源】。属主与应用进程相同的文件，应用随时可改。
     本项目现状：unit / drop-in / runtime.env 全部 schan:schan ⇒ 目前【没有锚】。
  3) 「拒绝启动」类检查必须晚于「移除触发条件】：
     install.sh 默认布局里 cwd 本来就有 .env ⇒ 先上"检测到 .env 即拒绝启动"
     会让官方安装路径产出的服务全部起不来。
EOF
echo
echo "（本脚本只读；未修改任何文件、配置或服务）"
