# P6 退役与收口 —— 并行执行方案

> 建立时间：2026-11-08 · 总管：Chief
> 依据：`docs/plans/frontend-progressive-migration.md` §7（P6 批次定义）、§5.1（renderer 选择）、§6.3（发布产物）

---

## 一、为什么现在做 P6

前端渐进迁移的 P2–P5 **实质已经完成**，方案文档的状态说明已过时。实测依据：

| 事实 | 位置 |
| --- | --- |
| `MIGRATED_PAGE_IDS` 已含 19 个页面 | `tts_erp_v2/api/v2/frontend_pages.py` |
| `PAGES` 注册表共 19 个页面，全部已迁移 | `tts_erp_v2/accounts/pages.py:26` |
| 19 个页面目录都有真实 React/TS 代码，合计约 1.4 万行 | `frontend/src/pages/` |
| 12 个旧 Jinja 模板仍在 | `tts_erp_v2/templates/pages/` |
| 13 个旧 JS 脚本仍在 | `tts_erp_v2/static/js/` |
| 旧 renderer 分支仍在代码里 | `tts_erp_v2/api/v2/pages.py`（如 `:309` 的 `_render_page("shops.html", ...)`） |

**结论**：剩余工作就是 P6 —— 退役旧实现、清理无消费者资源、收口 renderer。

---

## 二、并行切分原则

P6 最适合并行，因为退役对象是**文件**，天然可以按页面组切成互不重叠的写域。

三条硬规则：

1. **一个文件只有一个 writer**。三个 lane 的文件清单不得有交集。
2. **共享文件只有一处能改**。见下方「共享资源」一节，由 lane A 独家负责。
3. **不改 `MIGRATED_PAGE_IDS` 内容**。它已经是 19 个页面全集，本次不增不减。

### 共享资源（只有 lane A 能碰）

| 文件 | 为什么共享 |
| --- | --- |
| `tts_erp_v2/api/v2/pages.py` | 所有页面的 renderer 路由都在这一个文件 |
| `tts_erp_v2/static/js/console.js` | 挂在 `app.py` 的 public static mount 上，多页面共用 |
| `tts_erp_v2/static/vendor/*` | 无消费者的 vendor 判断要全局看 |
| `frontend/src/app/*` | AppShell / navigation / page-loader 共享层 |

---

## 三、三个 lane 的写域

### lane A —— 退役路由与共享资源（由 lane A owner 写）

**独占写域：**
- `tts_erp_v2/api/v2/pages.py`
- `tts_erp_v2/static/js/console.js`
- `tts_erp_v2/static/vendor/`
- `frontend/src/app/`

**任务：**
1. 删除 `pages.py` 里每个已迁移页面的 `else: return _render_page("xxx.html", ...)` 分支，只保留 `MIGRATED_PAGE_IDS` 命中时的 React renderer
2. 同步删除已无意义的 `_render_page` 辅助函数（若确认没有非迁移页面还在用）
3. 判断 `console.js` 是否还有非页面消费者；若没有则删除，并同步处理 `app.py` 的 mount 与 `templates/pages/manual-costs.html` 的引用（后者归 lane B 删除，所以**先与 lane B 协调**：lane B 删除 manual-costs 模板后，lane A 再删 console.js 和 app.py 的相关 mount）
4. 清理无消费者的 `vendor/` 资源

**顺序约束：** 本 lane 的第 3 步依赖 lane B 删除 manual-costs 模板后开始。

### lane B —— 旧模板退役（基础设置组）

**独占写域：**
- `tts_erp_v2/templates/pages/dashboard.html`
- `tts_erp_v2/templates/pages/shops.html`
- `tts_erp_v2/templates/pages/enum-map.html`
- `tts_erp_v2/templates/pages/runtime-configs.html`
- `tts_erp_v2/templates/pages/sync-jobs.html`
- `tts_erp_v2/templates/pages/manual-costs.html`

**任务：** 逐个确认没有消费者后删除，同时删掉对应的旧脚本：
- `tts_erp_v2/static/js/dashboard.js`
- `tts_erp_v2/static/js/shops.js`
- `tts_erp_v2/static/js/enum-map.js`
- `tts_erp_v2/static/js/runtime-configs.js`
- `tts_erp_v2/static/js/sync-jobs.js`

（`manual-costs` 没有同名旧脚本。）

### lane C —— 旧模板退役（数据工具与内容资产组）

**独占写域：**
- `tts_erp_v2/templates/pages/intercept-configs.html`
- `tts_erp_v2/templates/pages/intercept-requests.html`
- `tts_erp_v2/templates/pages/intercept-stats.html`
- `tts_erp_v2/templates/pages/spu-profit-deterioration.html`
- `tts_erp_v2/templates/pages/video-publish.html`
- `tts_erp_v2/static/js/intercept-configs.js`
- `tts_erp_v2/static/js/intercept-requests.js`
- `tts_erp_v2/static/js/intercept-stats.js`
- `tts_erp_v2/static/js/spu-profit-deterioration.js`
- `tts_erp_v2/static/js/video-publish.js`
- `tts_erp_v2/static/js/ad-daily.js`

**任务：** 同 lane B，逐个确认无消费者后删除模板 + 旧脚本。

**注意**：`ad-daily.js` 的旧页面 HTML 内联在 `tts_erp_v2/api/v2/ad_daily.py`（不是 templates 目录）。该文件属于 lane A 的 `pages.py` 之外的独立文件，**由 lane C 单独认领**：`tts_erp_v2/api/v2/ad_daily.py`。

---

## 四、退役前的强制检查

删除任何旧资源前，必须逐项确认：

```bash
# 1. 该模板/脚本还有没有引用
git grep -n "shops.html" -- tts_erp_v2/ tests/
git grep -n "shops.js" -- tts_erp_v2/ tests/

# 2. 删掉之后有没有测试会挂（测试可能直接读模板内容）
git grep -n "templates/pages" -- tests/

# 3. 页面确实在 MIGRATED_PAGE_IDS 里
```

三项都干净才删。**有引用就先改引用方，不要先删文件。**

---

## 五、验收标准

每个 lane 独立满足：

1. 自己写域内的旧模板和旧脚本全部删除
2. `git grep` 确认无残留引用
3. `bash scripts/test_isolated.sh fast` 通过（不引入新失败）
4. `bash scripts/test_baseline.sh --scope auto --base "$target_sha"` 通过
5. 提交信息：`chore: 退役 <范围> 旧前端实现` + 中文说明

---

## 六、合并顺序

lane B、C 相互独立，可先合。

lane A 依赖 lane B 删除 `manual-costs.html`（因为 `console.js` 的最后一个页面级引用在那里）。合并顺序：

```
lane B  ──┐
          ├──> lane A ──> 最终验证 ──> master
lane C  ──┘
```

---

## 七、待用户确认

- 三个 lane 是否都直接推 master，还是都要先走 PR
- `console.js` 和 `vendor/` 是否要一起清（涉及 app.py 的 public static mount，改动会影响未登录页面的静态资源可达性）
