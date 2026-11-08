# 护栏补 CSS：本轮执行记录

> 产出人：测试工程师（bot_d330a0b9a748）
> 日期：2026-11-09
> 任务：Chief —— 「护栏补 CSS，这条我定了：补。`tests/support/retired_frontend_assets.py` 现在只有 12 个 `RETIRED_PAGE_SCRIPTS`，加上已删的 4 个 CSS（ad-daily / runtime-configs / sync-jobs / spu-profit-deterioration），断言口径写清『任何 served document 不得引用已退役资产』，覆盖 JS + CSS 两类；`console.js` 继续排除并保留理由（public static mount）。」
> 分支：`chore/retired-assets-guard-css`（已推送，基于 `origin/master` @ `dab9f4fc`）
> HP desk worktree：`/home/schan/tts-erp/.worktrees/guard-css`

---

## 0. 结论

**已完成并实测验证。** 护栏从「只管 JS」扩展为「管 JS + CSS 两类资产」，新增 4 条断言，全部通过；两个变异测试证明护栏**确实能拦住回归**。

| 项 | 结果 |
| --- | --- |
| 提交 | `b81eb81c` `test: 护栏补齐已退役页面样式表，按 JS 与 CSS 两类资产统一断言`（5 文件 +125/−21） |
| 护栏测试实跑 | **12 passed**，EXIT=0 |
| 消费方实跑 | **109 passed**，EXIT=0 |
| pilots 实跑 | 99 passed / 6 failed —— **6 项为存量失败，与本改动无因果关系**（见 §4 对照实验） |
| 变异测试 | ✅ 放回退役 CSS → 2 条断言立即红；把退役 CSS 引用加回 `pages.py` → 1 条断言立即红 |

---

## 1. 改动内容

### 1.1 `tests/support/retired_frontend_assets.py`

| 常量 | 内容 | 说明 |
| --- | --- | --- |
| `RETIRED_PAGE_SCRIPTS` | 12 个（不变） | 原有名单 |
| `RETIRED_PAGE_STYLES` | **新增** 4 个：`ad-daily.css`、`runtime-configs.css`、`spu-profit-deterioration.css`、`sync-jobs.css` | 本轮补入 |
| `RETIRED_ASSETS` | **新增** = 两者并集 | 「任何 served document 不得引用已退役资产」的唯一入口 |

`console.js` **继续排除**，理由写进模块 docstring：它是 `app.py` 上的 public static mount（不是页面资产），本轮仍随仓库发布。

模块 docstring 同步更新：退役范围从「JS + Jinja 模板」扩到「JS + CSS + Jinja 模板」。

### 1.2 `tests/api/test_retired_frontend_assets.py`（新增 4 条断言）

| 断言 | 内容 |
| --- | --- |
| `test_retired_page_stylesheets_are_gone_from_disk` | 4 个退役样式表不得回到磁盘 |
| `test_only_kept_stylesheets_remain_under_static_css` | `static/css/` 下只允许 4 个活文件：`common.css`、`navigation.css`、`tokens.css`、`video-publish.css`；出现未登记文件即失败 |
| `test_retired_assets_cover_scripts_and_styles` | 合并名单必须同时含 `.js` 与 `.css`，且两个子名单不得有交集（防两份名单漂移） |
| `test_page_modules_do_not_point_at_retired_stylesheets` | `pages.py` / `ad_daily.py` 不得指向已退役样式表 |

### 1.3 三个消费方统一改用 `RETIRED_ASSETS`

| 文件 | 位置 | 改动 |
| --- | --- | --- |
| `tests/api/test_frontend_pages.py` | L17, L72 | import + 循环 |
| `tests/api/test_frontend_remaining_pages.py` | L12, L68, L101 | import + 两处循环 |
| `tests/browser/test_frontend_pilots.py` | L12, L122 | import + 循环 |

注释措辞同步：`retired per-page script` → `retired per-page asset`。

---

## 2. 一处必须修正的过度断言（实测发现）

**第一版我把断言写成匹配 `static/css/` 目录前缀，结果误伤了活引用：**

```
FAILED tests/api/test_retired_frontend_assets.py::test_page_modules_do_not_point_at_old_assets[path2-static/css/-pages.py ...]
```

根因：`tts_erp_v2/api/v2/pages.py:247` 有一行**存活的**引用

```python
'@import url("../../static/css/navigation.css?v=' + _css_version("navigation.css") + '");\n'
```

`navigation.css` 是 React 侧 `frontend/src/styles.css:4` 正在 import 的活资产，不能拦。

**修正**：改为**逐个退役样式表名**匹配（而不是目录前缀），并在断言 docstring 里写明原因。修正后 `12 passed`。

⇒ 这条本身是个好提醒：**「静态/css/ 目录前缀」不是「已退役」的正确判据，「退役名单」才是。**

---

## 3. 实跑证据

全部走官方隔离入口 `bash scripts/test_isolated.sh ...`。

| # | 命令 | 实际结果 |
| --- | --- | --- |
| 1 | `bash scripts/test_isolated.sh api tests/api/test_retired_frontend_assets.py -o addopts=-ra --tb=short`（修正前） | **1 failed, 11 passed**，EXIT=1 —— 失败项即 §2 的过度断言 |
| 2 | 同上（修正后） | **12 passed, 1 warning in 0.71s**，EXIT=0 |
| 3 | 变异测试 1：把 `tts_erp_v2/static/css/sync-jobs.css` 放回磁盘后重跑 | **2 failed, 10 passed** —— `test_retired_page_stylesheets_are_gone_from_disk` + `test_only_kept_stylesheets_remain_under_static_css` 立即红 |
| 4 | 变异测试 2：把 `pages.py:247` 的 `navigation.css` 改成 `sync-jobs.css` 后重跑 | **1 failed, 11 passed** —— `test_page_modules_do_not_point_at_retired_stylesheets[path0]` 立即红 |
| 5 | `bash scripts/test_isolated.sh api tests/api/test_frontend_pages.py tests/api/test_frontend_remaining_pages.py tests/api/test_ad_daily.py tests/api/test_pages.py -o addopts=-ra --tb=short` | **109 passed, 1 warning in 10.43s**，EXIT=0 |
| 6 | `bash scripts/test_isolated.sh browser tests/browser/test_frontend_pilots.py -o addopts=-ra --tb=line`（等空窗后跑） | **6 failed, 99 passed in 285.07s**，EXIT=1 —— 见 §4 |
| 7 | 对照实验：未改动的 master（`dab9f4fc`）worktree 跑同样 6 个 media-assets 用例<br>`bash scripts/test_isolated.sh browser "tests/browser/test_frontend_pilots.py::test_pilot_uses_shared_shell" -k "media-assets" -o addopts=-ra --tb=line` | **6 failed, 90 deselected in 195.67s**，EXIT=1 —— **用例 ID 与我分支上完全相同** ⇒ 存量失败确认 |

**每次实跑的临时库均已 drop**（日志可见 `dropping ephemeral DB: tts_erp_test_chore_retired_assets_guard_css_*`）。

---

## 4. pilots 的 6 项失败：存量问题，与本改动无因果关系

### 4.1 失败清单

```
FAILED tests/browser/test_frontend_pilots.py::test_pilot_uses_shared_shell[media-assets-资产管理-375-]
FAILED tests/browser/test_frontend_pilots.py::test_pilot_uses_shared_shell[media-assets-资产管理-375-/tts]
FAILED tests/browser/test_frontend_pilots.py::test_pilot_uses_shared_shell[media-assets-资产管理-1440-]
FAILED tests/browser/test_frontend_pilots.py::test_pilot_uses_shared_shell[media-assets-资产管理-1440-/tts]
FAILED tests/browser/test_frontend_pilots.py::test_pilot_uses_shared_shell[media-assets-资产管理-2560-]
FAILED tests/browser/test_frontend_pilots.py::test_pilot_uses_shared_shell[media-assets-资产管理-2560-/tts]
```

每个用例耗时 30.6 秒 —— Playwright 超时。

### 4.2 三条独立的排除证据

**证据一：master 的历史基线日志里已存在完全相同的 6 项失败**

`/tmp/TEST_baseline_fast_master.log`（master @ `0b92d97` 时点）L2961-2966：

```
FAILED tests/browser/test_frontend_pilots.py::test_pilot_uses_shared_shell[media-assets-资产管理-375-]
...（6 项，用例 ID 完全一致）
```

⇒ 这 6 项**在 master 上先于本改动就红着**。

**证据二：失败点在第 68 行，我的断言在第 117-124 行**

`tests/browser/test_frontend_pilots.py`：

```python
66  def test_pilot_uses_shared_shell(...):
67      page = frontend_browser_renderer.open(f"{prefix}/v2/pages/{slug}", width=width)
68      page.get_by_role("heading", level=1, name=title, exact=True).wait_for(timeout=10_000)   ← 失败在这里
...
117     # P6: the rendered document must not reference a retired per-page asset.
118     document = page.content()
119     for retired in RETIRED_ASSETS:
120         assert retired not in document          ← 我的断言在这里
```

media-assets 的 slug 来自 `PAGES` 注册表（`tests/browser/test_frontend_pilots.py:62-65`），失败是**H1 标题等待超时**——页面渲染不出标题或标题与注册 label 不匹配。

**执行顺序上根本走不到我的断言**，因果关系不成立。

**证据三：本改动对该文件的完整 diff 只有 2 处 import/变量改名**

```diff
-from tests.support.retired_frontend_assets import RETIRED_PAGE_SCRIPTS
+from tests.support.retired_frontend_assets import RETIRED_ASSETS
...
-    for retired in RETIRED_PAGE_SCRIPTS:
+    for retired in RETIRED_ASSETS:
         assert retired not in document
```

不触碰等待逻辑、夹具、渲染来源。

### 4.3 定性

**测试侧问题（存量）**，不是代码缺陷、不是本改动引入。media-assets 页面的 H1 标题与注册表 label 不一致，或该页在 React 渲染下没有产出预期标题。

⇒ **建议单开一条 lane 查 media-assets 的 H1/label 契约**，不要混进 P6 或本护栏改动。它也不影响「零失败基线」之外的判断——它本身就在 master 的失败集里。

---

## 5. 分类（按测试纪律）

| 类别 | 条目 | 说明 |
| --- | --- | --- |
| **代码缺陷** | 无 | 本轮未发现产品代码缺陷 |
| **测试自身问题** | pilots 6 项（media-assets H1 等待） | 存量，master 上先于本改动就红；需单独 lane |
| **环境问题** | 无 | 模板库 / 生产形状库均 `0076_media_packs` 三方一致；`prepare-worktree.sh` 退出码 0；跑前已等空窗（`IDLE after 40s`），负载 0.97 时独占跑 pilots |

---

## 6. 需要 Chief 拍板

1. **media-assets 的 6 项**：单开 lane 查，还是并入当前 P6 收口？（我建议单开 —— 它与 SPU 页面族的 legacy DOM 断言问题不是同一个根因）
2. **合并**：本分支 `chore/retired-assets-guard-css` 含 4 个提交（护栏 1 + 文档 3），按你之前定的流程由开发工程师走 lock 合并。

---

## 7. 提交清单

```
52b83b50 docs: 补充 conftest 评估的 e2e 触发条件实测与证据行
eafdf8a1 docs: 补 conftest 导入冲突评估、基线重建方案与测试文档职责对照
b81eb81c test: 护栏补齐已退役页面样式表，按 JS 与 CSS 两类资产统一断言
4cb99f31 docs: 更新职责对照的问题清单复核结果（问题 A/B/E 已消解）
```

远端：`origin/chore/retired-assets-guard-css`
PR 入口：`https://github.com/brakchen/tts-erp/pull/new/chore/retired-assets-guard-css`

**未提交任何生产库变更，未删除任何测试库（核查发现的 `tts_erp_test__schan_*` 库对应活跃测试进程，非残留，按 §1.1 未动）。**

---

*本文档为执行记录，所有命令与输出均为 2026-11-09 实跑结果。*
