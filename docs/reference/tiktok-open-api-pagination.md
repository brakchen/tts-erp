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
