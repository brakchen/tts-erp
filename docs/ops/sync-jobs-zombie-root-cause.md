# 断因清单：影子库 与 僵尸 running 行

> 依据：2026-11-10 可靠性评审实测。生产事实一律用 `git show <ref>:<path>` 读 release 真件；
> 引用给 owner_ref + owner_blob + symbol + line_digest（line_digest = 该行段文本的 sha256 前 16 位）。
> owner_ref 全为 `1a3199784d2d9001008570fb3619c5a864d56f46`（release 树 HEAD）。

## 通用顺序（纪律）

> **移除触发条件 → 断因 → 清理。**

理由（每一步都不能提前）：

1. **先移除触发条件**：止血优先，且这一步通常低风险、可立即做。不先做这步，清理就是在给一台还在漏水的桶擦干。
2. **再断因**：断因是机制级修改（改代码路径），比止血慢、风险高；止血之后再做，过程中不会白清。
3. **最后清理**：清理只在"不会再产生"之后做，否则清完的窗口里它又回来，而你以为已经收口。
   实证：影子库那套 schema 2026-10-20 就在那儿生成了，直到 11-09 才被发现；
   僵尸 running 行 11-08 2 行 → 11-09 7 行 → 11-10 1 行，**一直在长**，清过几次都没用。

---

# A. 让影子库不再产生

## A0 触发条件（要移除的那个）

**迁移目标库完全由环境变量决定，没有任何目标库核对。**

- `alembic/env.py` L67 `_db_url = os.environ.get("TTS_ERP_DB_URL")` → L98 `config.set_main_option("sqlalchemy.url", _db_url)`
  ⇒ 打哪个库 = 那个环境变量的值。
- 现有的破坏性守卫 `require_destructive_script_guard(..., allow_env="ALLOW_PROD_DESTRUCTIVE")`（L91-96）
  **只拦 prod 形态的库名**；维护库 `postgres` 不 prod 形态 ⇒ 迁移打进去**不触发任何拦截**。
- 来源（owner_blob `a8c68d126ee58d08d664bdf3802169f547cfa956`）：
  `alembic_db_url_source` L64-72 / `641859894d5422f2`；`alembic_destructive_guard` L91-98 / `25deeb623ddd98cd`。
- 实测：影子库 `postgres` 非空表 4 张（含 **46 行 RBAC 旧种子**），`integration.sync_jobs` **表存在但 0 行**；
  真库同表 100,057 行 ⇒ 同一句 `select count(*) ... where status='running'` 在影子库返 **0**、真库返 **7**。

## A1 移除触发条件（先做，不清理）

1. **alembic 入口加目标库核对**：解析 URL 的库名段，不在显式允许集合（`tts_erp` 或测试库名）⇒ 拒绝执行。
   连上之后再比对 `current_database()`（**事实 vs 声称双问**）。
2. **巡检侧常态化**：`scripts/check_jobs_actually_run.py` 的 `_assert_db_identity` 已落地
   ⇒ 连库身份在**任何业务查询之前**断言，`current_database()` != 期望库即 exit 5。

## A2 断因（机制级）

- 迁移目标不再由"环境变量隐式决定" ⇒ 改成**显式目标**（命令/参数），环境变量只提供连接信息。
- 入口核对做成**双问**：声称的库名 vs 事实的 `current_database()`，两者不一致即拒。

## A3 清理（必须晚于 A1 + A2）

- **按 13 个业务 schema 逐个 `DROP SCHEMA <s> CASCADE` + `DROP TABLE public.alembic_version`**。
  ⚠️ **只清 public 会漏 68 张**：public 里只有 3 张表（`alembic_version` + 两个 `pg_stat_statements*` 扩展对象），
  69 张业务表分布在 `after_sales`(2) commerce(5) config(4) finance(4) fulfillment(2) fx(2) integration(7)
  miaoshou(8) plugin(19) procurement(4) reporting(4) security(7) public(1)。
- 另需断言 60 个 sequence + 6 个 function 一并消失。
- **不删库**：`scripts/_test_db_connections.py` 的 `derive()` 硬编码维护库 `postgres`，
  删库 ⇒ createdb/dropdb 全断 ⇒ 隔离测试全断（"不可删除 ≠ 不可清理"）。

## A4 验收（唯一判据）

> **「那个曾经骗人的查询现在必须失败」**：
> `docker exec postgres psql -U postgres -c "select count(*) from integration.sync_jobs where status='running'"`
> ⇒ 必须报 **relation does not exist**，而不是返 0。

---

# B. 让僵尸 running 行不再增长

## B0 触发条件（要移除的那个）

**running 行被独立事务提交（已持久化），而终态写入与失败会话耦合。**

`run_job`（owner_blob `7599aa77cf4d1939b355f151dbd105ba6131a0cb`）：

| 行段 | symbol | line_digest | 事实 |
|---|---|---|---|
| L125-132 | `run_job_running_commit` | `c42cccfe244d200c` | `with session.begin(): start_job(...)` ⇒ **running 行当场提交**，进程死了也在 |
| L103-161 | `run_job` | `787f38685d0ccb24` | 上下文管理器整体 |
| L144-161 | `run_job_failure_audit` | `bf18ea1fd6923b1c` | 成功/失败两条终态路径**都用同一个 session** 的 `session.begin()`；失败路径的 `except Exception:` **只 log 一行** `job %s failure audit could not be persisted` ⇒ **行永远停在 running** |

调用方 `run_with_sync_job`（owner_blob `89d0358432d6cc9b25822685c5514ff6e0ff5747`，L95-119，line_digest `19bcf74e20448ef7`）
在 docstring L108-109 写明契约：**「inner must leave no active transaction on either exit path」** ——
而**没有任何东西强制它**。

⇒ **三条可复现的僵尸路径**：

| # | 路径 | 为什么现有代码挡不住 |
|---|---|---|
| ① | **失败审计自己失败**（DB/网络不可达、session 已坏） | 失败审计复用同一个 session ⇒ 作业失败的原因正好让审计也失败；失败被 `except Exception` 吞掉，只留一行日志 |
| ② | **终态写入时 session 仍有活动事务** | `session.begin()` 抛 `InvalidRequestError` ⇒ 成功路径与失败路径**同时**失效 ⇒ running 行永久留下（契约靠文档，不靠断言） |
| ③ | **进程被信号杀死** | `main.py` 只在 daemon 模式装了 SIGTERM/SIGINT handler（owner_blob `76cf032aef7ef6ff4f17e7cd8fa493f2fccca646`，L222-250，line_digest `6f62ddf6c3820997`）；而每个 tick 是**独立的一次性子进程**（`python -m tts_erp_v2.sync_worker.main run <job>`）⇒ 子进程被杀 ⇒ running 行留在原地 |

实测（2026-11-10 巡检，`scripts/check_jobs_actually_run.py` 清单②，窗口 2 天）：
**9 行**超 2×interval 仍无终态 —— `tiktok.finance` ×5、`tiktok.logistics`、`miaoshou.move_collect`、
`spu.image_mirror`、`tiktok.products`。
（简报口径合计 10 行；差的那 1 行未超阈值或落在窗口外 ⇒ **口径差 1 属未核实**，以巡检输出为准。）

**正确的形态仓库里已经有了**：`scheduler.py` 的 `_record_failed_tick`
（owner_blob `9845b5decc1018b8e2a8185bf9c87e6bb6b19e4e`，L511-536，line_digest `0716294dc616107d`）
= **fresh session + fresh transaction 写一行 sentinel 'failed'**，docstring 明写
「Operators MUST be able to see the tick happened even when the inner code crashed — silent loss of bookkeeping is exactly the bug this whole Lane-1 fix is meant to eliminate」。
⚠️ **但它只用在 `_run_token_refresh_job` 一条路径上**（同 blob，`_run_tiktok_job` L392-415 / `cb29e877bc65d238`、
`_run_tiktok_job_for_shop` L418-466 / `13b645bd12ac4d7c` 是 TikTok 分店扇出与 5s 退避重试路径）——
**产生僵尸的正是这些路径，而它们没有这层兜底。**

## B1 移除触发条件（先做，不清理）

1. **把 sentinel 形态下沉**：终态写入改用**独立 session**（照抄 `_record_failed_tick`），
   让 TikTok 扇出与系统作业路径也拿到"崩溃也能看见"的兜底。
2. **终态写入前先 rollback 残留事务**（或用独立 session）⇒ 路径 ② 不再成立。
3. **给 one-shot 子进程装 SIGTERM 兜底**：被杀时把当前 running 行落终态；或 worker 启动时做一次
   "上一世代遗留 running 行"的世代回收（概念已在 watchdog 里）。

## B2 断因（机制级）

- running 行与终态行**必须同生共死**：要么 running 行不提前提交（不可行——它要能在网络 I/O 期间可见），
  要么终态写入**不依赖任何可能已经坏掉的东西**（独立连接 + 独立事务 + 独立重试）。
- **审计写不进去也要留痕**：不能只 log 一行；要把"失败审计没能持久化"落成可查询的证据
  （`integration.sync_issues` 或独立表），否则巡检永远看不见它。
- 契约 `inner must leave no active transaction` 要从**注释**变成**断言**。

## B3 清理（必须晚于 B1 + B2）

- 把当前 9–10 行 running 行按**世代边界**改成 failed（留台账：行 id、started_at、判定依据）。
- **收敛判据是"新产生量 == 0"，不是"当前是 0"。**
  ⚠️ 现版本的脚本**测不到这一点**：`--state-file` 的跨轮 diff 只覆盖清单④（陈旧表），
  清单②（僵尸行）只报**当前绝对条数**，没有新增量 ⇒ 需要补一个"僵尸新增量"指标，
  否则"清完又长"这件事在报告里看不见。

## B4 验收（唯一判据）

- 巡检脚本清单②的**新增**条数连续 N 轮为 0（需先补上新增量指标）。
- 每一次 sync worker 异常中断后，行数**必须**变为 failed/succeeded，不能停在 running。
- 单点验证：`SELECT count(*) FROM integration.sync_jobs WHERE status='running' AND started_at < <世代起点>` ⇒ 恒为 0。

---

## 顺序理由小结（一句话版）

> **先止血（迁移不再能选中维护库 / running 行不再新产生），再断因（机制改成不可能复发），
> 最后才清理；反过来做，清掉的会被立刻重建，而你以为已经收口。**
