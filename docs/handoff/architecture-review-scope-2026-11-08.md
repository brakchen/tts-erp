# 架构评审结论的适用范围核实（Chief，2026-11-08 21:05）

> 目的：团队里另有一个「TTS-ERP 架构评审组」（架构师、安全工程师、数据存储专家、可靠性工程师、测试质量评审员、领域产品评审员），
> 其产出在 `F:\MiniMax Work Result\tts-erp-review\`。本文核实这些结论**对当前生产是否适用**，
> 避免后续 agent 把基于 v1 代码的结论当成现行风险来处理。

---

## 一、结论先行

**评审组产出质量高、方法论可靠**（自查纠正简报前提、做对照实验、区分「实测」与「推断」、密钥不复述只给长度）。但它的评审对象是 **v1 代码快照**，不是当前 v2 主线。

**当前生产真实成立的暴露面只有一条**：HP desk 的 `9877` 绑定在 `0.0.0.0`，绕开 nginx 的 `/tts` 前缀归一化与网关策略。

**其余 P0 均指向已退役的 v1 组件，当前生产不存在。**

---

## 二、评审对象与当前主线的差异

| | 评审组简报依据 | 实际在跑的系统 |
| --- | --- | --- |
| 路径 | `F:\MiniMax Work Result\tts-erp*`（4 个目录） | `F:\MiniMax Work Result\agents\mavis\workspace\tts-erp` |
| 性质 | 2026-08-16 v1 旧快照，**无 `.git`** | `git@github.com:brakchen/tts-erp.git` 本地克隆 |
| 状态 | v1 路由全 404，v1 DB 数据 2026-09-05 归档删除 | v2 于 2026-08-29 切流生产 |

F 盘上 `tts-erp`、`tts-erp-fastapi`、`tts-erp-tdd`、`tts-erp-cron-sync`、`oauth-receiver` 五个目录
**全部停在 2026-08-16，且都没有版本控制**。它们是历史快照，不是当前代码。

---

## 三、逐条核实结果

### 3.1 oauth-receiver 的 P0 —— **当前生产不成立**

评审报告 P0-1 称「oauth-receiver 是零鉴权的凭证金库，显式绑定全部网卡，暴露 refresh_token」。

**实测（2026-11-08 21:00，HP desk）**：

```
$ ps aux | grep "[o]auth"          → 无进程
$ ss -ltnp | grep 9876             → 9876 未监听
$ ls -d /home/schan/oauth-receiver → 不存在
```

**结论**：oauth-receiver 已完全退役。它绑 `0.0.0.0`、无鉴权、`/token` 返回明文 token 这几项，
在当前生产**都不存在**。不要按 P0-1 去"修"一个已经不在运行的组件。

### 3.2 v1 FastAPI 的写端点与 PII —— **当前生产不成立**

P0-4（5 个写端点无鉴权，可改真实订单）、P0-5（`/db/orders/{id}` 返回 `SELECT *` 含买家 PII）
指向 `tts_erp_fastapi.py`。该文件属于 v1 快照，v1 路由早已 404。

当前 v2 是**只读分析系统**，`AGENTS.md` §3 明文禁止新增写店铺状态的接口。

### 3.3 v2 鉴权 —— **已生效，安全工程师的 v1 结论不适用**

实测：

```
$ curl -s http://127.0.0.1:9877/healthz
{"status":"ok","service":"tts-erp-v2","auth_mode":"enforce"}

$ curl -o /dev/null -w "%{http_code}" "http://127.0.0.1:9877/v2/commerce/sales-orders?limit=1"
401
```

无凭据 401、错误 API key 同样 401。v2 的 access module 确实在强制鉴权。

### 3.4 **真实存在的那一条：9877 绑在 0.0.0.0**

```
$ ss -ltnp | grep 9877
LISTEN 0 2048 0.0.0.0:9877 0.0.0.0:*  users:(("python",pid=3951624,fd=12))
```

**影响**：局域网内任意主机可直接访问 `http://192.168.1.2:9877/...`，
**绕开了 nginx 的 `/tts` 前缀归一化、CORS 与其它网关策略**。
公网入口是 `daqiang.nat100.top` 的 nginx `/tts/*`，走 127.0.0.1:9878。

**注意 AGENTS.md §2 与实际不一致**：文档写「本机直连使用 `http://127.0.0.1:9877/v2/...`」，
但 HP desk 上这个服务实际监听 `0.0.0.0`。生产在 207 上是 `127.0.0.1:9878`（systemd 托管），
HP desk 这台的 `tts-erp.service` 是 v1 时代遗留的 `--host 0.0.0.0`。

**建议**（需用户确认，属于改生产服务配置）：
1. 把 HP desk 的 `tts-erp.service` 绑定改回 `127.0.0.1`——业务方都在同机走 loopback。
2. 外部访问一律经 nginx `/tts` 前缀，不直连 9877。

### 3.5 密钥明文散落 —— **部分成立，需区分**

P0-3 称 `app_secret` 与 PG 口令明文在 F 盘快照的 `.env`、`handoff.md`、`setup/*.md` 里。

**成立的部分**：F 盘 `MiniMax Work Result\` 是共享工作目录，2026-08-16 的 v1 快照里确有明文 `.env`。

**需要重新评估的部分**：当前 v2 主线已按 `AGENTS.md` §3 走 `token_service` 单一凭证出口，
`.env` 在仓库外且权限 0600。**轮换密钥是高风险动作**，且评审报告自己也强调：
「在鉴权修好之前先轮换，等于把新密钥换到一个仍然敞开的入口上」——
而 v2 的鉴权**已经是 enforce**，这条前提在 v2 上不成立。

**因此**：不建议按评审报告的顺序去轮换密钥。是否需要轮换，取决于 F 盘旧快照的明文是否
真的泄露过（共享目录、快照复制范围），这需要用户判断，不该由 agent 自动执行。

---

## 四、给后续 agent 的处理原则

1. **评审组的产出先看 `LIVE_FACTS_2026-11-08.md`** —— 它自己做了"评审对象更正"，那一份才是基于 v2 实机的。
2. **涉及 v1 组件的发现（oauth-receiver、tts_erp_fastapi.py、tts_erp.py）默认不适用于当前生产**，除非实测确认该组件仍在运行。
3. **唯一已实测成立的暴露面是 9877 的 `0.0.0.0` 绑定**，修改需要用户确认。
4. **不要基于旧快照直接执行密钥轮换**——先确认泄露范围，且注意顺序：先封入口，再轮换。

---

## 五、相关位置

| 内容 | 路径 |
| --- | --- |
| 评审组产出目录 | `F:\MiniMax Work Result\tts-erp-review\` |
| 基于 v2 实机的核查 | `F:\MiniMax Work Result\tts-erp-review\LIVE_FACTS_2026-11-08.md` |
| 安全评审（v1 为主） | `F:\MiniMax Work Result\tts-erp-review\SECURITY_REVIEW_CREDENTIAL_AUTH.md` |
| 当前主线仓库 | `F:\MiniMax Work Result\agents\mavis\workspace\tts-erp`（测试机 `/home/schan/tts-erp`） |
