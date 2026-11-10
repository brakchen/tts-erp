# 唯一写者锁：粒度设计说明

> 2026-11-11。实现：`tts_erp_v2/sync_worker/writer_lock.py`；
> 入口钩子：`tts_erp_v2/sync_worker/main.py:_run_one_job`；
> 检测侧：`scripts/watchdog_sync.py` 规则 (e) `concurrent_writers`。

## 0. 威胁模型

公网那台（207.57.126.199）已实测确认是**另一套独立部署**（同一 key 打
`/v2/admin/rate-limit`：hp desk = 200，公网 = 36000）。一旦它按用户指令接入
**生产库**，就有了跨机器写通道。

实测事实（2026-11-11，只读核查）：

* `pg_locks` 里 advisory lock = 0；全仓 `advisory_lock` / `pg_try_advisory` 0 命中；
* `sync_jobs` 没有 owner / lease / instance 列；
* `pg_hba` line 127 = `host all all 10.144.144.0/24 scram-sha-256`（组网段内任意
  主机只要有口令就能连生产库）。

⇒ **「同一时刻只有一个写者」今天 100% 靠部署纪律**。本设计把它变成**机制**。

## 1. 为什么必须是会话级锁（不是事务级）

作业的业务单元是**短事务**（`with session.begin():` 写一块就提交）。

* 事务级 `pg_advisory_xact_lock`：**每次提交时释放** ⇒ 两台机可以**交替**拿到锁
  ——机 A 提交 → 锁释放 → 机 B 拿到 → 提交 …… 每个 tick 都报 succeeded ⇒
  唯一写者失效，而且**没有任何失败信号**。这比没有锁更危险：它让人以为有保护。
* 会话级 `pg_advisory_lock`：绑在**连接**上，只有显式 `pg_advisory_unlock` 或
  **连接关闭**（进程退出 / 被 kill）才释放 ⇒ 锁的存活期 = 作业的存活期。

实现用 `pg_try_advisory_lock`（会话级锁家族里的**非阻塞**版本）：拿不到立刻返回
false。阻塞版 `pg_advisory_lock` 就是在**排队**，不用。

## 2. 四个键：锁什么、冲突时谁拒绝谁

| 键 | kind | scope（粒度） | 锁住什么 | 冲突时谁拒绝谁 | 留痕 |
|---|---|---|---|---|---|
| **W1** | `sync` | 作业名（如 `tiktok.orders`） | 该作业的**整个一次 tick**（含它对所有店铺的扇出） | 后到的**作业进程**拒绝启动（exit 3），不跑 | `sync_jobs` 一行 `status='skipped'` + `error_message='writer lock held: …'` |
| **W2** | `publish` | 发布目标 | 向 TikTok 发布**同一个目标**的动作 | 后到的**发布方**拒绝（不可逆 ⇒ 宁可拒绝） | 同上（发布入口处调用同一把锁） |
| **W3** | `token_refresh` | `provider:shop_id` | **某个店铺**的 refresh_token 轮换 | 后到的进程**跳过这个店铺**并留痕（`TOKEN_REFRESH_LOCKED` issue），其余店铺照常 | `sync_issues` 一条 issue |
| **W4** | `oauth_state` | `provider:shop_id` | 某个店铺的 OAuth **令牌交换 + 落库**窗口 | 后到的**回调**报错拒绝（`authorization_busy`，提示重新生成链接） | HTTP 错误响应 + 状态消费本身是原子的 |

### 2.1 W3 为什么是「按店铺」而不是「全局」

refresh_token 是**轮换式**的：刷新成功后旧值立即作废。于是：

* 两个进程刷**同一个店铺** ⇒ 一台先成功把旧值轮换掉，另一台拿失效的
  refresh_token ⇒ **整店凭证链断裂**，恢复要人工重走 OAuth。⇒ 这是真正的危险边界，
  锁必须按店铺。
* 两个进程刷**不同店铺** ⇒ 两边的凭据互不相干，没有任何冲突。⇒ 全局锁会把无关
  店铺串行化（一次 6h 作业里只要有 20 个店到期，全局锁会让它们排队，违反
  「绝不排队」）。

⇒ **粒度 = 店铺**（`provider:shop_id`）。实现：作业内部**逐店铺**取锁，拿不到就
跳过该店铺并留痕，不影响其它店铺。

W3 与 W1/W2 的机制差别也在这里：**sync/publish 是「一个实例跑、另一个不跑」；
token 轮换是「同一时刻只能有一个进程在刷某个店铺的 token」。**

### 2.2 W4：原子消费 + 按店铺的交换锁

`oauth_states` 表是共享的，state 消费**必须**是原子操作：

```sql
UPDATE integration.oauth_states
   SET consumed_at = now()
 WHERE state_hash = :h AND consumed_at IS NULL AND expires_at > now()
RETURNING id, extra
```

不是 SELECT-then-UPDATE。**仓库里已经是这个形态**（`tts_erp_v2/proxy/tiktok_oauth.py`
的 `pop_state`），本次补的是**并发护栏测试**（两个消费者只有一个拿到 `ok`）。

剩下的危险是**令牌交换本身**：两个回调各拿一个令牌，refresh_token 轮换 ⇒ 后写
覆盖先写。`target_configuration(lock=True)` 的行锁会把第二个回调**排队**等行锁；
W4 的 advisory lock 把它改成**拒绝**（`authorization_busy`）。

### 2.3 键的选取方式

```
key = int64(sha256("tts-erp/writer-lock/v1|<kind>|<scope>")[:8])
```

* **稳定**：跨进程、跨主机、跨重启都一样（**不是** Python 的 `hash()`，那个带随机
  种子，跨进程会变）；
* **不撞车**：带命名空间前缀，不会和其他用 advisory lock 的程序撞；
* **可反查**：`describe_key()` 能把 `pg_locks` 里的 `classid`/`objid` 拆回
  (kind, scope)，排查时看得懂。

PostgreSQL 把 64 位 advisory key 拆成两个 32 位无符号整数存进
`pg_locks.classid` / `pg_locks.objid`。

## 3. 语义：拿不到就拒绝，**绝不排队**

排队 = 僵尸的温床（一个被 kill 的排队进程会留下一串等待者；每个等待者最终都可能
拿到锁并写一遍）。因此：

* 拿不到锁 ⇒ **拒绝启动 + 留痕**（`sync_jobs` 的 `skipped` 行，watchdog 规则 (f)
  能认出这个标记）；
* 拒绝是**可见**的：作业进程的退出码是 **3**（`EXIT_WRITER_LOCK_REFUSED`），
  与「未知作业」=2、「跑失败」=1 区分开；
* 作业内部（W3/W4）拿不到 ⇒ **跳过那个目标 + 留痕**，其余目标照常。

## 4. 检测侧必须带时间维度

判据（@测试质量评审员 背书，Chief 背书）：

```
test_no_two_concurrent_writers_for_one_job
  ⇒ 报【活着的写者数】，不是【running 行数】
  ⇒ 僵尸行（早于当前作业世代）不算并发
  ⇒ 用【新产生量 == 0】，不是【当前值 == 0】
```

为什么必须这样写：历史上有过 9 行僵尸行（现已清零）。若把判据写成
「running 行数 ≤ 1」，那 9 行会让它**恒红** ⇒ 变成噪音 ⇒ 被忽略。**恒红即废**，
这个形态本项目已经出现过三次（僵尸行、偏差方向、克隆库再生）。

实现（`scripts/watchdog_sync.py`）：

* **活着的写者** = `status='running'` 且年龄 ≤ 2×interval 的行（超过就是僵尸：
  它被 SIGKILL 了，**什么都没写**，不算写者）；
* **写区间**：running ⇒ `[started_at, now]`（还在飞）；有终态 ⇒
  `[started_at, finished_at]`；
* **并发事件** = 同一作业两个写区间有重叠；
* **新产生量** = 只统计**观察窗口内**（默认 1 小时）开始的事件 ⇒ 窗口之外的
  历史并发不会让判据永远红；
* 规则 (e) `concurrent_writers` 的 finding 里带 `live_writer_count`、
  `zombie_count`、`concurrent_event_count`。

**锁与断言必须同批落地**：只有锁，锁被绕过时无人知道；只有断言，历史那 9 行
无人能解释。

## 5. 实现上两个必须踩过（并已规避）的坑

锁**独占一条池连接**（不与业务 session 共用），有两个坑：

1. **不能复用调用方自己的 session 取锁**：`session.connection()` 会**隐式开启事务**
   ⇒ 作业里随后的 `with session.begin():` 直接报 `A transaction is already begun on
   this Session`（实测：`token.refresh` 的 12 项测试全红）。
2. **不能用 `session.get_bind()` 派生 sessionmaker**：测试里 session 常常直接绑在
   一条 Connection 上（`Session(bind=conn)`），那样新 session 会和调用方**共用同一条
   连接** ⇒ 锁的释放/提交会动到调用方的事务（实测：OAuth 的 savepoint 被打断，
   `savepoint sa_savepoint_4 does not exist`）。必须取 `bind.engine`。

⇒ 结论：锁一律用**独立 sessionmaker 绑引擎**，独占一条连接；释放时只
`pg_advisory_unlock` + 关掉自己的 session，**绝不替别人提交**。

## 6. 自证清单（哪些测试证明了什么）

| 断言 | 证明 |
|---|---|
| `test_lock_is_still_held_after_a_business_commit` | 会话级：业务事务提交后锁**仍然**被持有（事务级会当场释放） |
| `test_lock_is_released_when_the_holder_is_killed` | 作业被 SIGKILL 后锁**自动释放**（连接断了 ⇒ PG 放锁），不残留 |
| `test_second_instance_is_refused_and_leaves_a_trace` | 窗口内第二实例 ⇒ **拒绝** + 留痕（`skipped` 行 + 稳定标记） |
| `test_pop_state_is_atomic_under_concurrent_consumers` | 两个并发消费者只有一个拿到 `ok`（W4 原子消费） |
| `test_oauth_callback_refuses_when_the_shop_lock_is_held` | 交换锁被占 ⇒ 回调拒绝，**上游一次都没被调用** |
| `test_no_two_concurrent_writers_for_one_job` | 并发判据：命中 / 僵尸孪生 / 窗口孪生 |

每条判据都做了**双向验证**：实现移除 ⇒ 判据转红；还原 ⇒ 回绿。

## 6. 边界（明确不覆盖什么）

* **advisory lock 是按库的**：它互斥的是**同一个数据库**里的写者。另一个库
  （比如影子/克隆库）上的写者不受影响 —— 这是对的，因为它们本来就不写同一批表。
* **不覆盖 API 进程的任意写**：只有 OAuth 回调这条路径取了锁。FastAPI 里其它
  手工触发/写操作仍可能与作业并发。
* **不覆盖跨机器的「非 PostgreSQL」副作用**：发布到 TikTok 这类外部不可逆动作，
  W2 只能在**本进程内**互斥（两台机都部署时靠 W2 的 key 互斥，因为它们写同一个
  库 ⇒ 同一个 advisory 锁空间 ✓）。
* **不改凭据**：本设计只让「谁在写」可判定、可拒绝；重新登录/轮换仍归用户与
  既有作业。
* **不改分页逻辑**：与 `MAX_PAGES` 等已审过的逻辑无关。
