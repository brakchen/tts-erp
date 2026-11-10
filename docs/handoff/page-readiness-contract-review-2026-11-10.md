# 就绪等待契约与三条护栏：验收记录（2026-11-10）

> 执行人：测试工程师（bot_d330a0b9a748）｜委托：Chief
> 测试机：HP desk `schan@192.168.1.2`（`ssh hp-desk`）
> 验收对象：开发工程师的报告（分支 `test/page-readiness-contract`，已合入 master @ `0c8660b0`）
> **本轮只做验收与结论，未改任何生产代码。** 探针是一次性文件，跑完已从仓库移出。

---

## 0. 结论速览

| # | 结论 |
| --- | --- |
| 1 | 开发工程师报告中**可核实的项全部成立**（逐条见 §1，含真实输出） |
| 2 | **我欠的 G2 口径判断：他们的口径对，我的原表述错。** 「剔除 docstring 与字符串字面量」对这个护栏会让它**永远不红** ⇒ 违背 G4。他们的实现（`ast` 取「除 docstring 外的全部字符串字面量」）同时排除散文、保留选择器 ⇒ G2 能触发 ⇒ 满足 G4 |
| 3 | **新发现（实测坐实）：G2 有一个覆盖缺口**——它只扫 `tests/support/page_readiness.py` 与 `frontend/src/**`，**不扫 `tests/browser/**` 与 `tests/e2e/**`**；而那 53 项回归恰恰出在 `tests/browser/test_spu_deterioration_alert_page.py` 里直接用 `#alert-rows`/`#alert-status`。实测：往 `tests/browser/` 放一个含 `page.locator("#alert-rows")` 的文件，G2 护栏 **EXIT=0 全绿** ⇒ **这条护栏挡不住真正发生过的那次回归** |

---

## 1. 逐条验收

### 1.1 browser 那 53 项的根因 —— 与我独立定位完全一致 ✅

开发工程师：根因是断言了 React 从不渲染的 legacy `#alert-status`（call log 里 React 的筛选标签全都已找到，唯一等不到的就是那个 id）。

我 2026-11-10 独立定位的结论（`docs/handoff/browser53-rootcause-2026-11-10.md`）：53 项只等**两个** locator，
22 项等 `#alert-rows [data-spu-pk='1001']`、31 项等 `#alert-status`；超时 5000ms（Playwright expect 默认）；
报错附带的 Aria 快照里 `form "对比范围"` / `region "已应用对比范围"` / `region "完整分析范围统计"` /
`grid "SPU 两期盈利对比"` **全部存在** ⇒ 页面渲染正常，是断言指向了已退役的 Jinja DOM。
修复 `6e07b394` 在我 11-09 的基线之后才进 master ⇒ 那 53 是「改写前」的失败数。

⇒ **双方独立得出同一结论，可以结案。**（我这边已实测当前 master 该文件 `EXIT=0 / 79s / 0 失败`。）

### 1.2 e2e scope 不构建前端 —— 已修 ✅

```
$ git show origin/master:tests/support/test_runtime.py | grep -nE "tests/browser|tests/e2e|startswith"
50:        # tests/e2e/ drives the real React bundle through a live API child, so
51:        # it needs the frontend build exactly like tests/browser/ does. Without
54:        or item.nodeid.startswith(("tests/browser/", "tests/e2e/"))
```

⇒ 构建触发条件已含 `tests/e2e/` ✅

### 1.3 PAGE_BROWSER 登记 —— 确实早已登记 ✅

```
$ git show origin/master:scripts/_test_selection.py | grep -n "test_spu_comparison_drilldown_dates"
83:        "tests/browser/test_spu_comparison_drilldown_dates.py",
```

`test_spu_deterioration_alert_page.py` 也在 `spu-profit-deterioration` 组（`_test_selection.py:199` 的断言里），
现在还补进了 e2e 文件 ⇒ 真正缺的只有 e2e 那个，已补上 ✅

### 1.4 两个同 subject merge 不是重复 ✅

```
$ git log --format="%h %p %s" -1 23c69681
23c69681 6c99ecc3 82a3bd1b fix: 移除选测器里遗留的已退役前端路径引用
$ git log --format="%h %p %s" -1 dab9f4fc
dab9f4fc 999ba2a9 23c69681 fix: 移除选测器里遗留的已退役前端路径引用
$ git diff --stat 23c69681..dab9f4fc
 docs/handoff/p6-retirement-parallel-plan.md | 18 +++++++++++++++++-
 1 file changed, 17 insertions(+), 1 deletion(-)
```

⇒ 外层 merge 只多了**一份 docs 计划文档**，代码修复**只应用了一次** ✅ 删不得。
（重复的只是 commit subject——那是可读性问题，不是内容重复。）

### 1.5 `TTS_ERP_TEST_SELECTED_NO_BUILD` 被 unset ✅

与我此前独立记录的一致（我也在别处踩过：靠环境变量强制构建无效，白跑一轮）。修法只能改构建触发条件 ✅

---

## 2. 我欠的判断：G2 口径 —— **他们是对的，我的原表述是错的**

我原来写的 G2 是「计数要剔除 docstring **与字符串字面量**」。
**对这个护栏来说，剔除字符串字面量是错的**：退役 id 在代码里**只可能以选择器字符串出现**
（`page.locator("#alert-rows")`），剔掉字面量，这条护栏就永远不可能红 ⇒ 直接违背 G4「改坏必须红」。

他们的实现是对的，而且比字面表述更严谨：

```python
def _code_literals(source: str) -> list[str]:
    """Every string literal in the source that is not a docstring."""
    tree = ast.parse(source)
    docstrings = {ast.get_docstring(node, clean=False) for node in ast.walk(tree) if ...}
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value not in docstrings]
```

⇒ **`ast` 方案天然把注释排除在外**（注释根本不在 AST 里），docstring 被显式排除，
其余字符串字面量（含选择器）**全部保留** ⇒ 散文可以写、选择器不能写 ⇒ G2 能触发 ⇒ 满足 G4。

TS/TSX 侧口径一致：先用正则剥掉 `//` 行注释与 `/* */` 块，再扫剩余代码文本。

**我的结论：采纳他们的口径，我的原表述作废。**
（这条也印证了台账那条方法论：**判据要落在实现体上，不能落在散文/字面量计数上**——
`scheduler.py` 的 `kill|SIGKILL|timeout` 3 处命中全是 docstring 与日志文案。）

---

## 3. G2 的覆盖缺口（实测坐实）

### 3.1 现在的扫描范围

```python
PROJECT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = PROJECT / "frontend" / "src"
HELPER = Path(__file__).with_name("page_readiness.py")

def test_helper_has_no_retired_dom_selectors() -> None:
    assert not _retired_dom_hits(HELPER.read_text(encoding="utf-8"))

def test_react_source_has_no_retired_dom_selectors() -> None:
    hits: set[str] = set()
    for path in sorted(FRONTEND_SRC.rglob("*.ts")) + sorted(FRONTEND_SRC.rglob("*.tsx")):
        hits |= _retired_dom_hits_in_js(path.read_text(encoding="utf-8"))
    assert not hits, ...
```

⇒ 只扫**两个地方**：共享 helper（1 个文件）与 React 源码（`frontend/src/**`）。
⇒ **不扫 `tests/browser/**`、不扫 `tests/e2e/**`、不扫其它测试文件。**

### 3.2 实测（一次性探针，跑完已移出仓库）

```
# 在 tests/browser/ 放一个用退役选择器的文件
tests/browser/test_tmp_retired_probe.py:
    def anchor(page):
        return page.locator("#alert-rows")

$ bash scripts/test_isolated.sh fast tests/support/test_page_readiness_contract.py
G2 护栏 EXIT=0 ELAPSED=6s
失败行数: 0
```

⇒ **G2 全绿。护栏完全没抓到 browser 测试文件里的退役选择器。**

### 3.3 为什么这条要紧

**这正是实际发生过的回归形态**：那 53 项失败不是来自共享 helper，
而是 `tests/browser/test_spu_deterioration_alert_page.py` 里**直接**写
`page.locator("#alert-rows [data-spu-pk='1001']")` 与 `page.locator("#alert-status")`。
e2e 那个文件也有 12 处 legacy DOM 断言。

⇒ 按现在的扫描范围，**这类回归可以原样再犯一次，护栏不会响**。

### 3.4 建议

把 G2 的 Python 侧扫描范围从「只有 HELPER」扩到「**`tests/**` 下全部 `.py`**」：

- `ast` 方案已经能天然排除注释与 docstring ⇒ 扩范围**不会**把历史散文算成命中；
- 扩完后再跑一次，确认 master 上现存文件 0 命中（注释里提到退役 id 的地方，例如
  `tests/browser/test_render_smoke.py:73` 的 `# 旧的 #alert-status 元素已不存在。`，
  是 `#` 注释、不在 AST 里 ⇒ 不会被误报）；
- e2e 侧同理（`tests/e2e/**`）。

### 3.5 顺带一条给自检的建议

G2 的破坏性自检用的是**合成片段**：

```python
broken = 'def anchor(page):\n    return page.locator("#alert-rows")\n'
assert "alert-rows" in _retired_dom_hits(broken)
```

它证明的是「识别函数能认出这个片段」，**不是「护栏会扫到那个目录」**。
⇒ 扫描范围改了，自检不会发现（这正是 §3.1 那个缺口的成因）。
建议自检改成「往临时目录写一个**真文件**再跑护栏」，
这样范围一改，自检就跟着变红。

---

## 4. 未执行 / 未覆盖

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 改动任何生产代码或他们的分支 | 未执行 | 本轮只验收与结论 |
| 第三轮护栏分支的完整实跑 | 未执行 | 由开发工程师那条 lane 在跑（`.integrate-readiness-guard`）；他们报告的分支 tip `0c8660b0` 已合入 master |
| `page_size` 部署后的真实 tick 验证 | 未执行 | 生产动作，需授权；不在本轮范围 |
| G2 扩到 `tests/**` 后的实际命中数 | 未执行 | 属修法建议，未实施 |
| 其余两个护栏（G1/G3）的独立破坏性验证 | 未执行 | 我只做了 G2 的覆盖缺口实证；G1/G3 的自检在其分支上、已由他们的 lane 覆盖 |

---

## 5. 现场

| 路径 | 说明 |
| --- | --- |
| `/home/schan/tts-erp/.worktrees/probe-g2-20261110` | G2 探针 worktree（`origin/test/page-readiness-contract`），探针文件已移出，`git status` 干净 |
| `/tmp/TEST_tmp_retired_probe.py.bak` | 探针源文件（一次性，不入库） |
| `/tmp/TEST_g2_probe.log` | 探针运行原始日志 |
| `docs/handoff/browser53-rootcause-2026-11-10.md` | 我那轮 53 项根因定位（结论一致） |
