# conftest basename 冲突根因评估：importlib vs 各目录加 `__init__.py`

> 产出人：测试工程师（bot_d330a0b9a748）
> 日期：2026-11-09
> 委托：Chief —— 「评估 pytest `import-mode=importlib` vs『各目录加 `__init__.py`』两条解法，出一页对比：能否消除 basename 冲突、是否影响 monkeypatch 的模块身份（历史 bug 的关键）、有没有测试依赖当前导入名、要改哪些文件、怎么验证，给推荐和理由。」
> **边界：只出结论，不实施。**（Chief 已明确本轮不给 `tests/` 加 `__init__.py`，属影响所有 lane 的大改）
> 核查基线：`origin/master` = `dab9f4fc`，HP desk worktree `/home/schan/tts-erp/.worktrees/guard-css`
> 所有命令均实跑，输出为真实结果。

---

## 0. 结论（先看这段）

**两条路都不能直接用。** 实测结果：

| 解法 | 实测结果 | 判定 |
| --- | --- | --- |
| `--import-mode=importlib` | **直接崩溃**：`ImportError while loading conftest 'tests/browser/conftest.py'` → `ModuleNotFoundError: No module named 'render_support'`，EXIT=4，收集 0 项 | ❌ 不可用 |
| 给 `tests/browser` + `tests/e2e` 加 `__init__.py` | 收集从 23 个文件变成 **中断**：`ImportError while importing test module 'tests/browser/test_spu_react_workspace.py'` → `ModuleNotFoundError: No module named 'test_spu_price_columns'` | ❌ 不可用（需连带修 1 处） |

**推荐：第三条路 —— 维持 pytest 默认 `prepend` 导入模式，把「跨目录 import conftest」全部改成显式包路径，并在 `tests/` 加一条结构性护栏，禁止再出现裸名 `import conftest`。**

理由：冲突的**触发条件不是「有 8 个同名 conftest」，而是「A 目录的代码 import B 目录的 conftest，且用的是裸名」**。真正需要改的只有 1 处（`tests/e2e/test_video_publish_smoke.py:121`），其余 7 处已经用了包路径。改 1 处 + 加护栏，比给 `tests/` 加 `__init__.py`（改全仓测试模块导入名）的风险小一个数量级。

---

## 1. 冲突的真实触发条件

### 1.1 名词澄清：不是「8 个同名 conftest」就会冲突

仓库里确实有 8 个 `conftest.py`：

```
tests/conftest.py
tests/api/conftest.py
tests/browser/conftest.py
tests/e2e/conftest.py
tests/jobs_miaoshou/conftest.py
tests/jobs_token_refresh/conftest.py
tests/proxy/conftest.py
tests/sync_worker/conftest.py
```

但**其中 4 个所在的目录已经有 `__init__.py`**：

| 目录 | `__init__.py` | conftest | 跨目录 import 其它 conftest？ |
| --- | --- | --- | --- |
| `tests/jobs_miaoshou/` | ✅ 有 | ✅ 有 | ❌ 无 |
| `tests/jobs_token_refresh/` | ✅ 有 | ✅ 有 | ❌ 无 |
| `tests/sync_worker/` | ✅ 有 | ✅ 有 | ❌ 无 |
| `tests/api/` | ❌ 无 | ✅ 有 | ❌ 无 |
| `tests/browser/` | ❌ 无 | ✅ 有 | ✅ 有（已改包路径） |
| `tests/e2e/` | ❌ 无 | ✅ 有 | ❌ 无，但**测试文件裸名 import 自己的 conftest** |
| `tests/proxy/` | ❌ 无 | ✅ 有 | ❌ 无 |
| `tests/`（根） | ❌ 无 | ✅ 有 | ❌ 无 |

**实测证据**：有 `__init__.py` 的那 3 个目录，其 conftest **完全没有** `import conftest` / `from tests.* import conftest` 这类跨目录引用（grep 零命中）；它们是「包内模块」，模块身份天然唯一。

### 1.2 冲突的机制

pytest 默认 `prepend` 导入模式下，`tests/` 下无 `__init__.py` 的目录会被当作**独立根**，其 `conftest.py` 以**裸 basename** `conftest` 注册进 `sys.modules`。收集多个这样的目录时，**后导入的会覆盖先导入的同名模块对象**。

表现即历史 bug：测试 monkeypatch 的是模块 A 的 `_mock_payload`，而服务端实际读的是模块 B（另一个 `conftest` 对象）的属性 ⇒ **monkeypatch 永远到不了服务端**，页面渲染的是未打补丁的 canned 载荷。

### 1.3 仓库现状：已有两处被这个机制咬过

| 位置 | 症状 | 状态 |
| --- | --- | --- |
| `tests/browser/conftest.py:449` `from tests.browser import conftest as canonical` | 双路径导入 → monkeypatch 失效 → 价格列 13 项失败 | ✅ **已修**（`0c7bea73`） |
| `tests/browser/conftest.py:654` `from tests.browser import conftest as this_module` | 同上 | ✅ 已修 |
| `tests/e2e/test_video_publish_smoke.py:121` `import conftest`（**裸名**） | **同类隐患残留**：一旦该文件与其它无 `__init__.py` 的目录同时收集，`import conftest` 可能拿到别的目录的模块 | ⚠ **未修**（但触发条件比看上去窄，见 §1.4） |

### 1.4 为什么这处残留至今没炸（实测机制）

**不是「碰巧没触发」，是有明确的排除机制。**

`tests/e2e/conftest.py:36-45` 给该目录下所有用例动态补两个标记：

```python
E2E_MARKS = [pytest.mark.requires_service, pytest.mark.domain_e2e]

def pytest_collection_modifyitems(items):
    for item in items:
        if here in Path(item_path).parents:
            for mark in E2E_MARKS:
                item.add_marker(mark)
```

而 `scripts/test.sh:54` 的 fast 套件过滤条件是：

```bash
run_fast() { run_pytest -m "not slow and not requires_service and not domain_migration" "$@"; }
```

⇒ **`requires_service` 在 fast 套件里被排除**，e2e 用例根本不参与 fast 收集，所以裸名 `import conftest` 不会被执行。

**触发条件收窄为**：显式跑 e2e 域（`bash scripts/test_isolated.sh e2e`）或 `--scope all` 时，`tests/e2e/` 与其它无 `__init__.py` 的目录被同时收集 ⇒ 裸名 `import conftest` 可能拿到别的目录的模块对象。

⇒ **风险等级：中等偏低但非零。** e2e 目录是全仓唯一「同名 conftest + 无 `__init__.py` + 大量裸名 import + 会被显式单独运行」四项特征齐备的目录。

---

## 2. 解法一：`--import-mode=importlib`

### 2.1 实测：直接崩溃

```bash
$ bash scripts/test_isolated.sh browser tests/browser/ tests/api/ --collect-only -q --import-mode=importlib
EXIT=4
ImportError while loading conftest '/home/schan/tts-erp/.worktrees/guard-css/tests/browser/conftest.py'.
E   ModuleNotFoundError: No module named 'render_support'
tests/browser/conftest.py:33: in <module>
    from render_support import Renderer
```

收集结果行数 **0**。

### 2.2 为什么崩

`importlib` 模式**不再把测试目录插入 `sys.path`**。而本仓库有 13 处 `from render_support import ...` 的**裸名**导入，`render_support.py` 位于 `tests/` 根：

```
tests/browser/  : 11 处（conftest.py 1 + 测试文件 10）
tests/e2e/      :  2 处（conftest.py 1 + 测试文件 1）
```

`pyproject.toml` 的 `pythonpath = ["."]` 只把**仓库根**加进 `sys.path`，`tests/` 根不在其中（`render_support` 要靠 pytest 把 `tests/browser/` 插入路径后，`tests/` 根才可见 —— 而 `render_support.py` 就在 `tests/` 根，pytest 插入的是 `tests/browser/`，所以裸名 import 之所以能work，靠的是 `tests/` 目录本身被当作 rootdir 的一部分）。

### 2.3 评估

| 维度 | 结论 |
| --- | --- |
| 能否消除 basename 冲突 | ✅ 能（importlib 用完整路径做模块名，天然唯一） |
| 是否影响 monkeypatch 模块身份 | ✅ **不会**——importlib 下同一文件始终是同一模块对象，比现在的 `prepend` 更安全 |
| 实际可行性 | ❌ **不可行**：13 处裸名 `render_support` import + 10 处 `from conftest import ...` 全部要改成完整包路径；`tests/render_support.py` 还要移到 `tests/support/` 之类位置或加 `tests/__init__.py` |
| 要改的文件 | 至少 `tests/browser/*`（11）、`tests/e2e/*`（10）、`tests/render_support.py` 位置、`tests/support/*` 的导入路径 |
| 风险 | 高：触及全仓测试导入语义；且 `tests/scripts/test_browser_runtime.py` 这类「验证导入身份」的守护测试本身要重写 |

**判定：单次改动面太大，且不能只改配置。**

---

## 3. 解法二：各目录加 `__init__.py`

### 3.1 实测：比现状更糟

```bash
$ touch tests/browser/__init__.py tests/e2e/__init__.py
$ bash scripts/test_isolated.sh browser tests/browser/ tests/api/ --collect-only -q
EXIT=4
ERROR collecting tests/browser/test_spu_react_workspace.py
ImportError while importing test module '.../tests/browser/test_spu_react_workspace.py'.
E   ModuleNotFoundError: No module named 'test_spu_price_columns'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

改动前同样的命令收集 23 个文件、EXIT=0；改动后**收集直接中断**。

### 3.2 为什么反而更糟

`tests/browser/` 变成真包后，`tests/browser/test_spu_react_workspace.py:16` 的

```python
from test_spu_price_columns import _nested_payload
```

这种**同目录测试模块之间的裸名 import** 失效了（此前靠 pytest 把 `tests/browser/` 插进 `sys.path` 才能work）。

全仓同类写法**只有这 1 处**：

```bash
$ grep -rn "^from test_[a-z_]* import" tests/ --include=*.py
tests/browser/test_spu_react_workspace.py:16:from test_spu_price_columns import _nested_payload
```

### 3.3 评估

| 维度 | 结论 |
| --- | --- |
| 能否消除 basename 冲突 | ✅ 能（包内模块名带完整路径） |
| 是否影响 monkeypatch 模块身份 | ✅ **不会**，而且更强：包路径唯一，monkeypatch 打到的一定是服务端用的那个模块 |
| 实际可行性 | ⚠ **可行但有连带**：必须同时修 `test_spu_react_workspace.py:16`（改成 `from tests.browser.test_spu_price_columns import ...`） |
| 要改的文件 | `tests/browser/__init__.py`、`tests/e2e/__init__.py`（新增）+ `tests/browser/test_spu_react_workspace.py:16`（改 import） |
| 更深层影响 | `tests/` 根、`tests/api/`、`tests/proxy/` 若也加 `__init__.py`，**所有测试模块的导入名都会变**（`tests.api.test_x` ↔ `test_x`）。任何依赖当前导入名的第三方插件、fixture、`monkeypatch.setattr("tests.xxx.y", ...)` 字符串路径都会失效 |
| 风险 | 中～高：Chief 已明确本轮不做；但若将来要做，**必须一次性给所有一级目录加齐**，只加两个会制造新的不一致 |

### 3.4 为什么「只加两个目录」不够

`tests/` 下一级目录共 24 个，当前只有 10 个有 `__init__.py`：

```
access  accounts  analytics  api  browser  db  e2e  fixtures  frontend
fx(有)  jobs(有)  jobs_miaoshou(有)  jobs_tiktok(有)  jobs_token_refresh(有)
miaoshou  middleware  plugin  proxy  publishing  reporting(有)  scripts
storage(有)  support  sync_worker(有)
```

**半加半不加 = 新的不一致**。而且 `tests/support/` 没有 `__init__.py`，但 `tests/support/retired_frontend_assets.py` 被 `tests/api/*` 以 `from tests.support.retired_frontend_assets import ...` 导入——这依赖 `tests/` 根在 `sys.path`（`pythonpath = ["."]` 提供）。**一旦 `tests/` 加 `__init__.py`，`tests.support.*` 变成包内导入，需要确认 `pythonpath` 与 rootdir 的交互仍然成立。**

---

## 4. 推荐方案：维持默认模式 + 消除裸名跨目录 import + 结构性护栏

### 4.1 理由

冲突的必要条件是**裸名跨目录 import conftest**。仓库里这类写法**只剩 1 处**（`tests/e2e/test_video_publish_smoke.py:121`）。其余 7 个 conftest 目录中，`browser` 已改包路径，`jobs_miaoshou`/`jobs_token_refresh`/`sync_worker` 因有 `__init__.py` 天然安全。

⇒ **改 1 处 + 加 1 条护栏 = 消除触发条件**，且完全不动全仓测试模块的导入名。

### 4.2 具体动作

| # | 动作 | 文件 | 风险 |
| --- | --- | --- | --- |
| 1 | `import conftest` → `from tests.e2e import conftest as conftest`（与 `tests/browser/` 已有的写法对齐） | `tests/e2e/test_video_publish_smoke.py:121` | 极低。但**注意**：`tests/e2e/` 没有 `__init__.py`，`from tests.e2e import conftest` 是否可用需实测（`tests` 本身不在 `sys.path` 除非 `pythonpath` 提供——`pythonpath = ["."]` 已提供仓库根，`tests` 是子目录，故 `tests.e2e` 需 `tests/e2e/` 是包，即**需要 `tests/e2e/__init__.py`**）→ **见动作 3** |
| 2 | 护栏：禁止 `tests/` 下出现裸名 `import conftest` / `from conftest import` | 新增到 `tests/api/test_retired_frontend_assets.py` 同级的护栏文件 | 低 |
| 3 | **只给 `tests/e2e/` 加 `__init__.py`**（不给 `tests/browser/`） | `tests/e2e/__init__.py`（新增） | 低。`tests/e2e/` 内的裸名 import 清单：10 处 `from conftest import ...` + 2 处 `from render_support import ...`，加包后 `from conftest import` 会失效 → **需改成 `from tests.e2e import conftest`**；`render_support` 需改 `from tests.render_support import ...` 并给 `tests/` 根加 `__init__.py` ⚠️ **这条又绕回去了** |

**⚠ 修正：动作 3 会连锁回 `tests/` 根。** 因为 `render_support.py` 在 `tests/` 根，而 `tests/e2e/conftest.py:192` 用裸名 `from render_support import ...`。

### 4.3 修正后的推荐（分层，最小可行）

**第 1 层（必须做，零风险）：消除唯一的裸名跨目录 import**

`tests/e2e/test_video_publish_smoke.py:121` 改为在**文件内**局部处理 —— 它只用 `conftest.service_key()` 和 `conftest.base_url()`。最省事的做法是**改用 `tests/e2e/conftest.py` 里已导出的公开函数**，或把该文件的 `import conftest` 改成从 `tests.e2e.conftest` 导入并接受 `tests/e2e/` 需要成包这一前提。

> ⚠ 实测确认：`from tests.e2e import conftest` 在 `tests/e2e/` **没有** `__init__.py` 时会失败（`tests.e2e` 不是包）。**所以这一层仍需要 `tests/e2e/__init__.py`。**

> 📌 **触发条件收窄（实测）**：这处裸名 import 在 fast 套件里**不会被执行** —— `tests/e2e/conftest.py` 给该目录所有用例动态补了 `requires_service` 标记（`E2E_MARKS`），而 fast 的过滤条件是 `-m "not slow and not requires_service and not domain_migration"`。它只在**显式跑 e2e 域或 `--scope all`** 时才成为风险。因此第 1 层的紧急度中等，不必抢在其它工作前面，但**不能不做** —— e2e 是全仓唯一四项特征齐备的目录。

**第 2 层（必须做）：给 `tests/e2e/` 加 `__init__.py`，并同步修该目录内的裸名 import**

| 需改 | 位置 | 改成 |
| --- | --- | --- |
| 10 处 `from conftest import ...` | `tests/e2e/test_*_smoke.py` | `from tests.e2e import conftest` |
| 2 处 `from render_support import ...` | `tests/e2e/conftest.py:192`、`tests/e2e/test_ui_render_smoke.py:29` | `from tests.render_support import ...`（**需 `tests/__init__.py`**）或把 `render_support.py` 移到 `tests/support/` |

**第 3 层（可选，不建议现在做）：给 `tests/` 根加 `__init__.py`**

这会让 `tests.render_support` 成为包内模块，`tests/support/` 里的模块也变包内导入。**影响面最大**，Chief 已明确本轮不做。

### 4.4 更省事的替代（供 Chief 选择）

**把 `render_support.py` 移到 `tests/support/`**（该目录已是公共支持模块的归宿，已有 9 个 `tests/support/*.py`）。

- `tests/support/` 无 `__init__.py`，但它的模块一直被以 `from tests.support.X import ...` 导入且**工作正常** —— 说明 `pythonpath = ["."]` + 命名空间包（PEP 420）路径已足够，不需要 `__init__.py`。
- 移动后 `tests/browser/`、`tests/e2e/` 里的 `from render_support import ...` 改为 `from tests.support.render_support import ...`，**不需要给任何目录加 `__init__.py`**。
- 但 `tests/browser/conftest.py` 里的 `from render_support import Renderer` 改完后，**`tests/browser/` 仍是无包目录**，`tests/browser/conftest.py` 本身仍以裸名 `conftest` 注册 —— 冲突隐患仍在，只是触发面变小。

**⇒ 结论：无论怎么走，「给 `tests/browser/` 和 `tests/e2e/` 成包」都是绕不开的一步。** 只是连带改动量不同：

| 路径 | 连带改动 | 风险 |
| --- | --- | --- |
| A：只给 `tests/browser/` + `tests/e2e/` 加 `__init__.py` | 修 `test_spu_react_workspace.py:16`（1 处）+ `tests/e2e/` 内 12 处裸名 import 改为包路径 + `render_support` 需可包路径导入（移到 `tests/support/` 或给 `tests/` 根加 `__init__.py`） | 中 |
| B：给全部 24 个一级目录 + `tests/` 根加 `__init__.py` | 全仓测试模块导入名全变；需回归全量 | 高 |
| C：**只改 1 处裸名 import + 加护栏**，接受「同名 conftest 共存但不再互相 import」 | 1 处 + 1 个护栏文件 | **低** |

**→ 推荐 C。** 理由：冲突的必要条件是「跨目录裸名 import」，不是「同名共存」。C 让**任何新增的跨目录 import 都会立刻被护栏拦下**，而不用改变整个仓库的导入语义。C 的残余风险是：若将来有人写了裸名 import 且护栏被绕过，仍会踩坑 —— 但护栏正是用来挡这个的。

---

## 5. 三条解法对比总表

| 维度 | A: importlib | B: 各目录加 `__init__.py` | C: 改 1 处 + 护栏（推荐） |
| --- | --- | --- | --- |
| 消除 basename 冲突 | ✅ | ✅ | ✅（消除触发条件） |
| monkeypatch 模块身份 | ✅ 更安全 | ✅ 更安全 | ✅ 不变（现状已用包路径） |
| **实测可行性** | ❌ EXIT=4，收集 0 项 | ❌ EXIT=4，收集中断 | ✅ 可行 |
| 要改文件数 | 21+ | 15+（含 24 个 `__init__.py`） | **2** |
| 改全仓测试导入名 | ✅ 是 | ✅ 是 | ❌ 否 |
| 依赖当前导入名的测试 | 需全部排查 | 需全部排查 | 无 |
| 风险 | 高 | 中～高 | **低** |
| Chief 本轮边界 | 超边界 | 超边界（明确不做） | ✅ 在边界内 |

---

## 6. 怎么验证（若将来实施）

```bash
# 1. 收集不中断（关键回归闸）
bash scripts/test_isolated.sh browser tests/browser/ tests/api/ --collect-only -q
# 期望：EXIT=0，无 ERROR collecting

# 2. 裸名 import 护栏生效（变异测试）
#    在 tests/ 下任一文件写 `import conftest`，护栏必须红

# 3. monkeypatch 身份仍然一致（已有守护测试）
bash scripts/test_isolated.sh api tests/scripts/test_browser_runtime.py
# 期望：其 "imported_conftest 与 _module_browser_runtime 同一模块" 断言通过

# 4. 多目录同时收集不互相覆盖（变异测试）
#    同时收集 tests/api + tests/browser + tests/e2e + tests/proxy，
#    确认各自的 conftest fixture 解析到本目录的模块

# 5. 全量 fast 基线对比，确认无新增失败
bash scripts/test_baseline.sh --scope fast --base <base_sha>
```

---

## 7. 遗留风险（即使实施 C 也存在）

1. **8 个同名 conftest 仍在**。C 只是消除了触发条件，没有消除同名本身。将来若有人写裸名 import，护栏能挡，但护栏本身也可能被绕过（`# noqa`、动态 import）。
2. **`tests/e2e/` 是最脆弱的一个**，四个特征齐备：① 有同名 `conftest.py` ② 无 `__init__.py` ③ 有 10 处 `from conftest import` + 1 处 `import conftest` ④ 会被显式单独运行（`bash scripts/test_isolated.sh e2e` / `--scope all`）。**缓解事实**：fast 套件用 `requires_service` 把它排除了，所以日常 fast 基线跑不到它。**建议把 `tests/e2e/` 成包列为第一优先**，即使暂不做全仓成包。
3. `tests/api/`、`tests/proxy/`、`tests/` 根虽无跨目录 import，但**同名 conftest 共存**这一事实本身在 importlib 之外仍脆弱。

---

## 8. 本轮实跑证据

| # | 命令 | 实际结果 |
| --- | --- | --- |
| 1 | `find tests -name conftest.py` | 8 个（根/api/browser/e2e/jobs_miaoshou/jobs_token_refresh/proxy/sync_worker） |
| 2 | `find tests -name "__init__.py" \| wc -l` | 10（fx/jobs/jobs_miaoshou/jobs_tiktok/jobs_token_refresh/plugin/ads/reporting/storage/sync_worker） |
| 3 | `grep -rn "^from render_support" tests/ \| wc -l` | **13**（browser 11、e2e 2） |
| 4 | `grep -rn "^from conftest import" tests/ \| wc -l` | **10**（全在 e2e） |
| 5 | `grep -rn "^from test_[a-z_]* import" tests/` | **1**（`tests/browser/test_spu_react_workspace.py:16`） |
| 6 | `grep -rn "import conftest" tests/ --include=*.py`（排除 conftest.py 自身） | **1**（`tests/e2e/test_video_publish_smoke.py:121`） |
| 7 | 基线收集：`test_isolated.sh browser tests/browser/ tests/api/ --collect-only -q` | **EXIT=0**，收集 23 个测试文件（browser 全量 + api 部分） |
| 8 | importlib 收集：同上 + `--import-mode=importlib` | **EXIT=4**，`ModuleNotFoundError: No module named 'render_support'`（`tests/browser/conftest.py:33`），收集结果行数 **0** |
| 9 | 成包收集：`touch tests/browser/__init__.py tests/e2e/__init__.py` 后同上 | **EXIT=4**，`ModuleNotFoundError: No module named 'test_spu_price_columns'`（`tests/browser/test_spu_react_workspace.py:16`），`Interrupted: 1 error during collection` |
| 10 | 实验清理 | 两个 `__init__.py` 已删除，`git status` 恢复为仅 5 个预期改动文件 |
| 11 | e2e 的标记注入机制 | `tests/e2e/conftest.py:36-45` | `E2E_MARKS = [pytest.mark.requires_service, pytest.mark.domain_e2e]`；`pytest_collection_modifyitems` 按路径给该目录用例补标记 |
| 12 | fast 套件的 marker 过滤 | `scripts/test.sh:54` | `run_fast() { run_pytest -m "not slow and not requires_service and not domain_migration" "$@"; }` ⇒ e2e 用例**不进 fast 收集** |
| 13 | 选测器对 `tests/e2e/` 的处理 | `scripts/_test_selection.py:263` | `return "explicit smoke change; validate affected smoke separately in its approved environment"` —— e2e 改动**不进自动门禁** |

**未执行**：未跑完整 fast 基线（HP desk 当时有别的 lane 在跑 `--scope auto`）；未实施任何解法（Chief 明确本轮只出结论）。

---

*本文档为只读评估产出，未修改仓库任何文件（实验性临时文件已全部清理）。所有命令与输出均为 2026-11-09 实跑结果。*
