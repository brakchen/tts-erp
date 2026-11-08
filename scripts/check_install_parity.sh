#!/usr/bin/env bash
# check_install_parity.sh — 【只读】装配核对：一次性回答「我以为的 == 实际的 == 吗」
#
# ★ 通则 22（验证工具不得复制被验证的规则）：
#   本脚本【不自己实现】配置来源优先级链 —— 改为向 owner
#   （tts_erp_v2.runtime_settings）询问 (value, source)。
#   owner 侧新增 get_with_source()（纯增量，与 get() 行为完全一致）。
#
# ★ 方法论（2026-11-09 实测修正）：
#   **「生效值」必须在【运行中进程的环境】下问 owner。**
#   同一个键在脚本环境与进程环境里来源可能不同（实测 AUTH_MODE：
#   脚本环境 = process_env，真实进程 = runtime_file）。
#   ⇒ 本脚本读 /proc/<pid>/environ，临时灌进 os.environ，再问 owner。
#
# ★ 属性③（跳过显形）：读不到就是「未能判定」，**不得**用 unit 声明的
#   Environment 冒充进程真实环境（那是造假不是兜底）。
set +e
export LC_ALL=C
UNITS="${*:-tts-erp.service tts-erp-sync.service tts-erp-publish.service}"
PY=/home/schan/tts-erp/.venv/bin/python
RELEASE=/home/schan/tts-erp-releases/erp-runtime-secret-84ec0a49
export PYTHONPATH="$RELEASE:${PYTHONPATH:-}"

echo "################ 装配核对（只读） ################"
echo "时间: $(date -u +%Y-%m-%dT%H:%M:%SZ)   主机: $(hostname)   用户: $(id -un) uid=$(id -u)"
echo "owner: $RELEASE  ($(git -C "$RELEASE" rev-parse --short HEAD 2>/dev/null))"
echo

echo "################ 1-3. unit 实际形态与属主 ################"
for u in $UNITS; do
  echo "--- $u"
  systemctl --user show "$u" -p WorkingDirectory -p ExecStart -p EnvironmentFiles \
    -p FragmentPath -p MainPID --no-pager 2>/dev/null | sed 's/^/    /'
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
         /home/schan/tts-erp/.env "$RELEASE/.env"; do
  if [ -e "$f" ]; then echo "  存在  $(stat -c '%U:%G %a' "$f")  $f"
  else echo "  缺失  -  $f"; fi
done
echo

echo "################ 4-9. 生效值（问 owner）/ 来源链 / cwd .env / 授权开关 ################"
$PY - <<'PYEOF' 2>&1 | sed 's/^/  /'
import os, subprocess, sys
from pathlib import Path

RELEASE = "/home/schan/tts-erp-releases/erp-runtime-secret-84ec0a49"
sys.path.insert(0, RELEASE)

def sec(t): print("\n--- " + t)

UNITS = ["tts-erp.service", "tts-erp-sync.service", "tts-erp-publish.service"]

# owner：只问，不复制规则
from tts_erp_v2.runtime_settings import get_setting_with_source
OWNER_REF = subprocess.run(["git", "-C", RELEASE, "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
OWNER_BLOB = subprocess.run(["git", "-C", RELEASE, "hash-object",
                              "tts_erp_v2/runtime_settings.py"],
                             capture_output=True, text=True).stdout.strip()
print(f"  owner_ref  = {OWNER_REF}")
print(f"  owner_blob = {OWNER_BLOB}")

def proc_env(unit):
    """返回 (env, status)；status ∈ ok / not_running / unreadable。

    ★ 绝不回退到 unit 声明的 Environment —— 那是【冒充】。
    """
    try:
        pid = subprocess.run(["systemctl", "--user", "show", unit, "-p", "MainPID",
                              "--value"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return {}, "unreadable"
    if not pid or pid == "0":
        return {}, "not_running"
    try:
        data = Path(f"/proc/{pid}/environ").read_bytes()
    except Exception as exc:
        return {}, f"unreadable: {type(exc).__name__}"
    env = {}
    for kv in data.split(b"\0"):
        if b"=" in kv:
            k, v = kv.decode("utf-8", "replace").split("=", 1)
            env[k] = v
    return env, "ok"

unresolved = []
for unit in UNITS:
    sec(f"unit: {unit}")
    env, status = proc_env(unit)
    cwd = subprocess.run(["systemctl", "--user", "show", unit, "-p", "WorkingDirectory",
                          "--value"], capture_output=True, text=True).stdout.strip() or os.getcwd()
    print(f"  进程环境状态 = {status}   WorkingDirectory = {cwd}")
    print(f"  cwd 下隐式 .env 存在 = {Path(cwd, '.env').is_file()}")
    if status != "ok":
        unresolved.append({"unit": unit, "status": status})
        print("  ⚠ 未能判定该 unit 的生效值（不冒充 unit 声明的环境）")
        continue

    # 把真实进程环境灌进 os.environ，再问 owner
    saved = dict(os.environ)
    os.environ.clear(); os.environ.update(env)
    try:
        for key, default in (("TTS_ERP_AUTH_MODE", "off"),
                              ("TTS_ERP_RUNTIME_ENV_FILE", ""),
                              ("TTS_ERP_DOTENV_FILE", ""),
                              ("TTS_ERP_DB_URL", ""),
                              ("ALLOW_PROD_DESTRUCTIVE", "<unset>"),
                              ("ALLOW_PROD_PURGE", "<unset>"),
                              ("TTS_ERP_ENVIRONMENT", "<unset>")):
            val, src = get_setting_with_source(key, default)
            shown = val
            if key.endswith("DB_URL") and isinstance(val, str) and "@" in val:
                shown = val.split("://", 1)[0] + "://***:***@" + val.split("@", 1)[1]
            print(f"  {key:<28} 生效值={shown!r}")
            print(f"  {'':<28} 来源  ={src!r}   (owner 判定)")
        # DB URL 两侧
        gs, gsrc = get_setting_with_source("TTS_ERP_DB_URL", "")
        oe = os.environ.get("TTS_ERP_DB_URL", "")
        def dbname(u):
            try:
                from urllib.parse import urlsplit
                return urlsplit(u).path.lstrip("/") or "(none)"
            except Exception:
                return "(unparsable)"
        print(f"  DB URL get_setting 侧库名 = {dbname(gs) if gs else '(empty)'} (source={gsrc})")
        print(f"  DB URL os.environ 侧库名 = {dbname(oe) if oe else '(empty)'}")
        print(f"  两侧库名一致 = {(dbname(gs) if gs else '') == (dbname(oe) if oe else '')}")
    finally:
        os.environ.clear(); os.environ.update(saved)

sec("未能判定的 unit（一等指标，正常值 0）")
print(f"  unresolved_units = {len(unresolved)}")
for u in unresolved:
    print(f"    {u['unit']}: {u['status']}")

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
  4) 本脚本的配置来源判定【向 owner 询问】，不复制优先级链（通则 22）。
EOF
echo
echo "（本脚本只读；未修改任何文件、配置或服务）"
