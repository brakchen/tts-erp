# 架构评审结论复核（2026-11-10）

> 复核人：Chief（研发协作线）｜对象：架构评审组第二十二轮汇报中的三条技术结论
> 测试机：HP desk `schan@192.168.1.2`（`ssh hp-desk`）
> 本记录中每条结论均附实测命令与真实输出；未执行的项标注「未执行」。

## 0. 结论速览

| 汇报中的结论 | 复核结果 |
| --- | --- |
| master 上 `pages.py` 有「潜伏炸弹」：两个函数引用了已删除的类，导入不报错、直接调用才炸 | **成立，已实测复现** |
| 生产库版本 0076 不在 release 树可达范围（release 只到 0073），一行 `alembic stamp head` 即可抹掉版本号，造成不可逆损坏 | **不成立**——生产 release 目录的 `alembic/env.py` 已有生产破坏性守卫，实测 `stamp` 被拒绝，且库版本实测未变 |
| master 缺生产入口 / 凭据读取层 / 告警脱敏，连生产形态都跑不起来 | 未执行（需在生产形态下实跑，属生产侧动作） |

---

## 1. master 的「潜伏炸弹」——成立

`tts_erp_v2/api/v2/pages.py` 的 `_sidebar()` / `_sidebar_css()` 引用了 `_TrustedHtml`，而该名字在 P6 退役 Jinja 模板时随 `from jinja2 import ...` 一起被删掉了。

**为什么常规检查发现不了**：文件头有 `from __future__ import annotations`，**函数注解在运行时求值为字符串，不实际求值**，所以模块能正常导入、应用能正常启动、页面能正常打开。

**怎么发现的**：AST 扫描（文本搜索抓不到，因为名字确实在源码里出现过）。扫描 `tts_erp_v2/**/*.py` 全树，未定义名只有 5 处，其中 4 处是 `__file__`（合法），**只有 `pages.py:236` 的 `_TrustedHtml` 是真问题**。

实测复现（master `d31fafb7`）：

```
$ /home/schan/tts-erp/.venv/bin/python -c "
from tts_erp_v2.api.v2 import pages
print('import OK — 注解不求值，导入不报错')
try:
    pages._sidebar('dashboard')
except NameError as exc:
    print(f'直接调用炸了: {type(exc).__name__}: {exc}')"
import OK — 注解不求值，导入不报错
直接调用炸了: NameError: name '_TrustedHtml' is not defined
```

**当前无生产调用者**：全仓只有 `scripts/oneoff_p6_collapse_migrated_routes.py` 的字符串模板里提到 `_shared_sidebar_css` / `_shared_sidebar`（不同名字，且是一次性脚本的历史文本），没有任何路由或作业调用 `_sidebar()` / `_sidebar_css()`。⇒ 目前是**死代码里的死引用**，不是活跃缺陷，但确实会在有人复用时炸。

**处置建议**：两个函数本身是「复用参考」而非渲染路径（`docs/design/reuse-evaluation-2026-10.md` 决定原样保留，测试直接断言）。最小修法是给它们一个真实存在的返回类型（例如 `-> str`），或删掉这两个无调用者的包装函数、只保留 `_sidebar_html` / `_SIDEBAR_CSS`。**不要**在生产侧动它。

**同类问题的通用抓法**：AST 扫未定义名。本仓库已经有过一次教训——删常量漏改调用点，导致整个套件在收集阶段中断。建议把 AST 扫描做成门禁，见 §3。

---

## 2. 「一行 `alembic stamp head` 抹掉生产库版本号」——不成立

### 2.1 事实核对

| 项 | 实测值 |
| --- | --- |
| 生产库 `alembic_version.version_num` | `0076_media_packs`（经 `runtime.env` 的 DSN 直查） |
| 生产 release 目录 `/home/schan/tts-erp-releases/erp-master-01a16e3a/` 的迁移文件 | 最高 `0073_media_assets.py`，**无 `0076_media_packs.py`** |
| 该目录 `alembic heads` | `0073_media_assets (head)` |
| 该目录 `alembic/env.py` | **有** `require_destructive_script_guard(...)`（第 91 行），`deps.py` 里 `is_prod_shaped_db()` 与守卫函数都在 |

⇒ 「版本号不在 release 树可达范围」这个**前提成立**。

### 2.2 但结论不成立：守卫已在必经路径上

守卫写在 `alembic/env.py` 的**模块级**，而 alembic 的**每一个**子命令（`upgrade` / `downgrade` / `stamp` / `history` / `current` …）都会加载 env.py。所以 `stamp` 同样会经过这道闸——`script_name="alembic upgrade"` 只是打印时用的标签。

实测（在生产 release 目录、未设 `ALLOW_PROD_DESTRUCTIVE`）：

```
$ cd /home/schan/tts-erp-releases/erp-master-01a16e3a
$ export TTS_ERP_DB_URL=$(grep -oP "^TTS_ERP_DB_URL=\K.*" /home/schan/.config/tts-erp/runtime.env)
$ unset ALLOW_PROD_DESTRUCTIVE
$ python -m alembic stamp head

[alembic upgrade] REFUSED: --confirm on prod-shape dbname.
             Set ALLOW_PROD_DESTRUCTIVE=1 to override (NOT recommended;
             you are about to mutate live data).
```

**随后回读生产库版本，确认未被改动**：

```
PROD_ALEMBIC_VERSION: 0076_media_packs
```

⇒ **生产库版本号没有被抹成 0073**，数据未受损。

### 2.3 残余风险（仍然存在，但比汇报描述的小）

守卫有两个绕过口，都需要**显式**动作：

1. `ALLOW_PROD_DESTRUCTIVE=1` —— 这是设计好的运维开关，不是「一行就能触发」。
2. 绕过 env.py 的调用方式（例如直接用 SQLAlchemy 连库改 `alembic_version` 表）—— 这不是「敲一行 alembic 命令」。

⇒ 汇报里「只需一个人敲一行 ⇒ 不可逆损坏」的描述**夸大了风险**：真实情况是「需要显式设置一个写着 NOT recommended 的环境变量」。

**建议**：不改代码，只在 `docs/ops/tts-erp.md` 的迁移章节补一句实测结论（守卫覆盖 `stamp`，2026-11-10 实测拒绝且库版本未变），避免下一轮评审重复这条已被排除的风险。

---

## 3. 建议补的两道闸（与汇报一致）

### 3.1 AST 扫未定义名

能抓到 §1 的炸弹，文本搜索抓不到。建议做成 `scripts/` 下的检查脚本 + 门禁接线。

**判据要点**（避免假绿）：
- 只数**真实的未定义名**，不能把 `__file__` 之类的合法名字算进去
- 报出的是「文件 + 行号 + 名字」，便于定位
- 必须**双向验证**：真的引入一个未定义名要转红，修掉要转绿

### 3.2 迁移入口默认拒绝

汇报建议「库当前版本不在本树可达范围 ⇒ 禁止 alembic 写操作」。

**复核后修正**：这条**现在做不了**，因为 §2.2 已证明守卫拦得住写操作，而且写这个判据需要知道「本树可达范围」——而生产库版本 0076 恰好**不在**生产 release 树里（树只到 0073）。⇒ 若按「版本不在本树 ⇒ 拒绝」实现，**生产 release 目录下的任何 alembic 写操作都会被拒**（包括合法的 upgrade），等于把生产运维路径整个堵死。

⇒ **这条建议降级**：现状（守卫拦写）已覆盖主要风险；「版本可达性」判据适合放在**部署前的检查**里（确认目标 release 树能到达目标版本），而不是放在 `alembic/env.py` 这个必经的迁移入口里。

---

## 4. 本轮未执行的项

| 项 | 原因 |
| --- | --- |
| 在生产形态下实跑 master | master 缺生产入口与凭据读取层，报告已确认；实跑需生产侧准备 |
| 公网入口 `chen.daqiangplatform.com/tts/` 的版本与隐患 | 该入口指向另一台机器，SSH 拒绝公钥，进不去 |
| 9 行僵尸 `sync_jobs` 的清理 | 可靠性工程师已按 id 精确处理并复核，不重复 |
| 40 CNY 兜底退役 | 业务口径变更，��用户决定执行时机 |

---

## 5. 本轮沉淀的判断纪律

1. **「某机制未受本次事件影响」必须附实测，不能只写断言。** 本轮对 §2 做了「先在生产 release 目录实测 `stamp` → 再回读库版本」两步，确认版本未变，才敢下「不成立」的结论。
2. **评审结论同样会错，要按同一标准复核。** §2 的前提（版本不在可达范围）是对的，但结论（能抹掉）被守卫否掉了；报告本身也注明过「机制未受影响的结论要附实测」。
3. **「一行命令就能造成不可逆损坏」这种话要拆成「需要什么前置条件」**：`ALLOW_PROD_DESTRUCTIVE=1` 是显式开关，不是顺手一行。
