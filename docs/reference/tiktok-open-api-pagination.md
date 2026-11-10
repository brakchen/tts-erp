# TikTok Open API 202309 —— page_size 与分页游标实测

> 实测日期：2026-11-10。方式：**只读**探测（GET / POST 搜索类接口），
> 走作业自己的 `tts_erp_v2/jobs/tiktok/*._walk_pages` + `build_proxy_call`
> （真实签名、真实 `shop_cipher`、真实 `next_page_token → page_token` 映射）。
> 探测只读凭证，不写业务库。
> 店铺：`7494763368967603447`（下称 A 店）、`7494864868604150914`（下称 B 店）。

## 结论

| 端点 | 上游 page_size 合法区间 | 区间内是否固定游标 | 我们用的默认值 | 依据（见下） |
| --- | --- | --- | --- | --- |
| `/finance/202309/payments` | `1..100` | **是**：任何 < 100 的值都返回同一个游标 | **100** | §1 |
| `/finance/202309/statements` | `1..100` | 否，正常翻页 | 100（与 payouts 共用参数） | §2 |
| `/order/202309/orders/search` | `1..100` | 否，正常翻页 | 100（不变） | §3 |
| `/product/202309/products/search` | `≤100` | 否，正常翻页 | 50（不变） | §3 |
| `/return_refund/202309/returns/search` | **`10..50`** | 否，正常翻页 | 50（不变，**不能改 100**） | §4 |
| `/return_refund/202309/cancellations/search` | **`10..50`** | 否，正常翻页 | 50（不变，**不能改 100**） | §4 |
| `/fulfillment/202309/orders/{id}/tracking` | 不分页 | — | 不适用 | §5 |

合法区间不是我方推测——是上游自己在报错里写出来的：

* finance：`PageSize is invalid, check validation rule is: $this>=1 && $this<=100.`
* orders：`PageSize is invalid, check validation rule is: $this > 0 && $this <= 100.`
* products：`PageSize is invalid, check validation rule is: $this <=100.`
* after_sales：`98001004 Value Out Of Range. The parameter 'page_size' exceeds the
  allowed range (10 to 50), got '100'. Please adjust and retry.`

## §1 `/finance/202309/payments` —— page_size < 100 时游标被冻住

A 店、B 店表现一致（page_size=50）：

```
page 1: items=50  next_page_token len=244  sha=87171104ac79
page 2: items=50  next_page_token len=244  sha=87171104ac79   ← 与 page 1 完全相同
        items_total=100  items_unique=50  items_duplicate=50  cross_page_overlap=50
```

第 2 页带上一页的游标请求，拿回的是**同一个游标**和**同一批 50 条**——游标永远
不前进，走页不可能结束。游标解码后指向同一条记录：

```json
{"next_cursor":{"payment_order_id":3676432477862069495,"create_time":1786082821638},
 "prev_cursor":{"payment_order_id":0,"create_time":0},
 "first_from_new":false,"last_from_new":true}
```

走作业自己的 `_walk_pages` 时，这会变成一次明确失败（而不是无限跑）：

```
UpstreamJobError: /finance/202309/payments pagination did not advance:
next_page_token repeated (upstream loop); aborting the walk instead of running forever
```

page_size=100 时（A 店 / B 店）：

```
A 店: items=76  unique=76  duplicate=0   → 一页取完，无游标
B 店: items=50  unique=50  duplicate=0   → 一页取完，无游标
```

⇒ `tiktok.finance` 的 `page_size` 默认值从 50 改为 100。

## §2 `/finance/202309/statements` —— 正常翻页

page_size=50 走 3 页 / 112 条（唯一 112，重复 0），游标逐页变化；
page_size=100 走 2 页 / 同样 112 条。与 payouts 共用同一个 `page_size` 参数，
改成 100 对 statements 无副作用。

## §3 orders / products —— 正常翻页

* orders：page_size=50 连续走 8 页 400 条，游标逐页变化、唯一 400、跨页重复 0；
  page_size=100 同样正常。
* products：page_size=50 走 6 页 / 259 条（唯一 259，重复 0），page_size=100 走 3 页。

⇒ 两个端点都没有固定游标缺陷，`page_size` 保持原值。

## §4 after_sales —— 100 会被上游直接拒收

```
code=98001004  message=Value Out Of Range. The parameter `page_size` exceeds
the allowed range (10 to 50), got `100`. Please adjust and retry.
```

returns/search 与 cancellations/search 都是 10..50。page_size=50 时翻页正常
（A 店 returns 3 页 85 条，B 店 4 页 131 条）。⇒ **保持 50**；改成 100 会让
每个 tick 直接失败。

> 顺带记录（不是本次改动范围）：after_sales 的上游数据本身有少量重复 id
> （A 店 85 条里 3 条、B 店 131 条里 7 条，重复出现在同一页内），与游标无关。

## §5 logistics —— 它的 page_size 不是上游 page_size

`tiktok.logistics` 不翻上游页：它的上游调用只有
`GET /fulfillment/202309/orders/{order_id}/tracking`（单个订单，无游标），
订单来源是 `raw_records` 里 orders job 落下的原始载荷。它的 `page_size`
是**本 tick 处理订单数的本地上限**（`limit = page_size if page_size > 0 else
TARGET_LIMIT`），与上游分页无关，不受本次上游缺陷影响。

## §6 page_size=100 是「绕过」，不是「修复」——它有硬上限

**必须先说清楚这一条：`page_size=100` 没有修好上游，它只是让「当前数据量」一页
取完。**

- 上游 `/finance/202309/payments` 的游标在 page_size < 100 时是冻结的（§1）；
- 100 只是该端点允许的**最大值**，在这个值上游「恰好」一次返回全部 payments、
  不再需要翻页，所以冻结的游标碰不到；
- 今天能跑通，是因为两个店铺当前窗口的 payments 条数都 **< 100**
  （实测 A 店 76 条、B 店 50 条）。

⇒ **任一店铺、任一窗口的 payments 超过 100 条时，`tiktok.finance` 必然失败。**
100 条一过，第 2 页就会拿到那个固定的游标，走页抛
`UpstreamJobError: pagination did not advance`。

这次失败是**显式**的，不是静默挂死：

| | 表现 |
| --- | --- |
| 失败方式 | `UpstreamJobError` ⇒ `integration.sync_jobs` 记 `status='failed'`，带完整错误文案 |
| watermark | **不动**（失败时不推进游标）⇒ 数据不会被跳过、也不会重复写 |
| 内存 | 走页是生成器，一页一页释放 ⇒ 不会像 2026-11-06 之前那样撑到 2 GB 被 OOM 杀 |
| 后果 | 该店铺的财务数据**卡在这个窗口**，直到窗口数据量降下来，或改走 §7 的替代取数 |

也就是说：这是「看得见的失败」，不是「数据悄悄丢了」——但财务数据确实会停住，
所以窗口变大之前必须有处置预案（§7）。

## §7 看到 `pagination did not advance` 时的处置路径

报错文案：

```
UpstreamJobError: /finance/202309/payments pagination did not advance:
next_page_token repeated (upstream loop); aborting the walk instead of running forever
```

**不要盲目重试。** 同一窗口重试必然再次命中同一个冻结游标——只会不断刷出
`failed` 行，不会有任何前进（watermark 不动 ⇒ 重试也不会跳过数据）。

处置顺序：

1. **先确认是不是「窗口 payments 条数 > 100」**。用同一店铺、同一起止时间统计
   payments 条数（上游 `/finance/202309/payments` 按 `create_time` 排序，按窗口
   过滤后计数）。条数 ≤ 100 ⇒ 不是 §6 这条，另有原因（凭证、鉴权、上游 5xx 等），
   按常规上游错误处理。
2. **若确实 > 100** ⇒ 二选一：
   - **拆分窗口**：把当前窗口按 `create_time` 切成每段 < 100 条的子窗口，逐段推进
     watermark（每一段都能一页取完，走页正常结束）；
   - **走替代取数**：用其它能覆盖该区间的端点补数据（例如按 statement 维度反查，
     再回填 payments），把卡住的窗口跳过去。
3. **记录 issue**：把「窗口起止、店铺、payments 条数、判断依据、采取的处置」
   写进 `integration.sync_issues`（`job_name='tiktok.finance'`）。不要只留一行
   `sync_jobs` 失败日志——下次窗口变大时，还得重新推一遍同样的判断。

## §8 待办（本轮不实现）

- **「窗口内 payments 条数接近 100」的监控/告警**。现在只能在作业**失败之后**
  才发现窗口太大；等失败时，财务数据已经卡住。建议：按店铺统计窗口内 payments
  条数，达到阈值（例如 ≥ 80）就提前告警，而不是等 §6 的必然失败。
  落点建议：`scripts/watchdog_sync.py` 加一条规则 + `sync_jobs` 行级统计。
  **状态：待办，本轮不实现。**
- 上游缺陷修复后（游标不再冻结），把 `page_size` 降回一个更小的值以减少单页压力，
  并重跑 §1 的实测确认。

---

## 复现方式

只读探针（仓库外，不落代码）：

```bash
# 环境
export TTS_ERP_RUNTIME_ENV_FILE=/home/schan/.config/tts-erp/runtime.env
export TTS_ERP_DOTENV_FILE=/dev/null
export PYTHONPATH=<worktree>

# 1) 端点 × page_size 矩阵：看 next_page_token 是否重复
python probe_cursor.py --mode matrix --page-sizes 50,100 --max-pages 3

# 2) 走作业自己的 _walk_pages：看是否终止、是否重复
python lane-ps-07-walkcheck.py

# 3) 各端点 page_size 合法区间（读上游自己的报错）
python probe_cursor.py --mode matrix --page-sizes 1,9,10,25,50,100,101,500 --max-pages 1
```

判据：`next_page_token` 逐页相同 = 上游固定游标；走页以空游标结束 = 正常。
