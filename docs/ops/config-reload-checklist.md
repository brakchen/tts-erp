# 配置「重新读取动作」检查清单

> 依据：2026-11-10 可靠性评审实测。生产事实一律用 `git show <ref>:<path>` 读 release 真件；
> 引用给 owner_ref + owner_blob + symbol + line_digest（line_digest = 该行段文本的 sha256 前 16 位）。

## 通则

> **每个被管配置项必须归到 hot-read / reset-required / restart-required 三值之一，
> 并写清对应的那个动作；指不出动作 ⇒ 没生效。**

三值的判据与症状：

| 值 | 判据 | 典型症状（最危险的一种） |
|---|---|---|
| **hot-read** | 每次读，或按 inode/size/mtime 感知 | 改完即生效（唯一安全的默认值） |
| **reset-required** | 进程内单例 + 有显式 reset 端点 | 改完行为不变，**且不报错** |
| **restart-required** | 启动时读一次进进程环境 | 改完行为不变，**且不报错** |

「不报错」是这三类共同的危害：变更单上写着"改 X 即生效"，实际没生效，而巡检看不出来。

## 清单

| # | 配置项 | 值 | 动作 | 验证判据（唯一能证明生效的） | owner_ref / owner_blob / symbol / line_digest |
|---|---|---|---|---|---|
| 1 | `TTS_ERP_RATE_LIMIT_PER_MIN`（runtime.env） | **reset-required** | `POST /v2/admin/reset-rate-limit`，body `{}` | `GET /v2/admin/rate-limit` 的 `limit` **==** `env_var_effective_value`；不等 = 没生效 | `1a3199784d2d9001008570fb3619c5a864d56f46` / `1a12d9c3c1555e7fa24bf87fd9a0f6c7358ffdc2` / `_env_limit` / L88-100 / `61b04b2a3a7fef50` |
| 2 | `runtime_settings` 管的其它键（`TTS_ERP_AUTH_MODE`、`TTS_ERP_DOTENV_FILE`、`TTS_ERP_RUNTIME_ENV_FILE` …） | **hot-read** | 无（自动） | 改 runtime.env 后，下一次读到的值 == 期望值 | `1a319978…` / `ceb7f6abbbd7268705eec145a9a8c5808f9ea5c6` / `_FileSignature_read_file` / L95-153 / `41be90f9b6264b25` |
| 3 | systemd drop-in（`95-runtime-config` / `96-pgappname` / `97-deployed-commit`） | **restart-required** | ① `systemctl --user daemon-reload` ② `systemctl --user restart <unit>` | **回读 `/proc/<pid>/environ`**：`TTS_ERP_DEPLOYED_COMMIT` 与 `PGAPPNAME` 必须**同时**在。`systemctl show -p Environment` 只证明 systemd 侧读到，**不证明进程侧生效** | 生产 user scope drop-in（不在 git 里） |
| 4 | `TTS_ERP_DEPLOYED_COMMIT`（进程环境变量） | **restart-required** | 同 #3 | 进程自证 commit == 磁盘 HEAD | 同 #3 |
| 5 | `TTS_ERP_DB_URL` | **restart-required**（服务侧） | 改 runtime.env 后重启服务 | `current_database()` == 期望库（不是 DSN 里写了什么） | `1a319978…` / `ec0485ecde3a4e7bafccfda3235a5c55739d8fc3` / `_resolve_db_url` / L78-96 / `157ad78c39e231bc` |
| 6 | `pg_hba.conf` | **restart-required**（PG 侧） | `SELECT pg_reload_conf();` | 新规则的实际行为改变（拒绝/放行），不靠 PG restart | 运维项 |

## 三个本轮实测的案例

1. **systemd drop-in**：改完文件不 `daemon-reload`，`systemctl --user show -p Environment` 仍返回**旧值**；`daemon-reload` 之后**立刻**变成新值。
   ⇒ systemd 是用**内存里已 load 的 unit 配置**去启动新进程的 ⇒ 漏了 daemon-reload，**重启也拿旧值，而且不报错**。
2. **Python 进程内单例**：`rate_limit.py` 的 `_SHARED` 只在**首次创建**时读 env（`_env_limit` 在 `_SHARED is None` 时被调）。
   ⇒ 改 env 不生效、**且不报错**；必须调 reset 端点。
3. **PostgreSQL**：改 `pg_hba.conf` 必须 `pg_reload_conf()`，否则新规则不生效。

## 脆弱点（必须进检查清单）

**四个 unit 靠 drop-in `95-runtime-config.conf` 里一行空的 `EnvironmentFile=` 才不会被 unit 里那个
不存在的 `/home/schan/tts-erp/.env` 拖住。** 谁重建 unit 漏了这行 ⇒ 四个服务一起起不来。

⇒ 检查项（逐 unit 核对，不能假设一致）：

| 检查 | 判据 |
|---|---|
| drop-in 存在且含 `EnvironmentFile=`（空值） | 缺 ⇒ unit 会去读不存在的 `.env` ⇒ 起不来 |
| drop-in 含 `WorkingDirectory=`（指向 release 树） | 缺 ⇒ 进程从主 checkout 加载代码 |
| drop-in 含 `ExecStart=`（空）+ `ExecStart=`（真实命令） | 缺 ⇒ unit 的旧 ExecStart 生效 |
| **每个 unit 的 drop-in 集合一致** | 实测：`tts-erp-watchdog` 无 `96-pgappname.conf`；`tts-erp-pgbackup` 无 `96`/`97` ⇒ "哪个 unit 声明了什么身份"必须逐个核对 |

实测现状（2026-11-10，release 树 = `1a319978…`）：

- base unit `tts-erp.service` 仍写着 `WorkingDirectory=/home/schan/tts-erp` 与
  `EnvironmentFile=/home/schan/tts-erp/.env` —— **全靠 drop-in 覆盖**。
- `97-deployed-commit.conf` 声明已是 `1a319978…`，但 `tts-erp` 与 `tts-erp-publish` 的**进程里没有**这个变量
  （服务启动早于 drop-in 写入 ⇒ 改了没重启）；`tts-erp-sync` 进程里仍是 `f88fb713…`（旧代际）。
  ⇒ 这正是"改了 ≠ 生效"的三种形态同时出现在生产上。

## 归属与边界

- 本清单只覆盖**被管的配置项**。每个新配置项进生产前，必须先在台账里填上"值 + 动作 + 验证判据"三格；
  填不出动作 ⇒ 该配置项不许上线。
- 验证判据必须是**可执行的读取**（回读 environ / 回读 `current_database()` / 回读 `/healthz` 的具体字段），
  不接受"配置文件改好了"作为验收。
