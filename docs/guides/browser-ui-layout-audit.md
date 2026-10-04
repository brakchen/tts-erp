# 浏览器 UI 布局巡检（probe_ui_layout_*）

> 状态：稳定工具。最后更新 2026-10-02。
> 适用范围：`tts_erp_v2/templates/pages/` 与 `tts_erp_v2/static/` 下的页面布局回归。

## 用途

对全部页面做**布局缺陷**自动化巡检，覆盖 DOM 度量抓不到的三类问题：

1. **横向溢出**——`documentElement.scrollWidth > clientWidth`；
2. **填充失配**——表格容器与内容宽度差 > 80px（即 SPU ROI 主表曾出现的
   `fitData` 不填满容器、右侧留大片空白的问题）；
3. **越界元素**——右边界超出视口且**未被 `overflow` 祖先裁剪**的最外层元素。

两类已知误报被内建排除：

- 空表占位（内容宽 0）不计为填充失配——那是无数据态，不是布局缺陷；
- Ace 编辑器内部文本层（设计宽 100 万 px）被 `.ace_scroller` 裁剪，
  裁剪感知扫描会跳过它（`runtime-configs` 页的常见假阳性）。

## 用法

```bash
# 1) 渲染全部页面为静态 HTML（真实 Jinja 环境，只读不碰数据库）
.venv/bin/python scripts/probe_ui_layout_pages.py --out /tmp/ui-audit/pages

# 2) 浏览器巡检：mock 业务接口、截图、DOM 度量
NODE_PATH=/home/schan/pi-web/node_modules \
  node scripts/probe_ui_layout_audit.js
```

发现任一缺陷时退出码为 `1`，可直接用于回归拦截；干净退出 `0`。

### 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PAGES_DIR` | `/tmp/ui-audit/pages` | 页面渲染产物目录 |
| `SHOTS_DIR` | `/tmp/ui-audit/shots` | 截图输出目录 |
| `WIDTHS` | `2560,1440` | 逗号分隔的视口宽度 |
| `OUT_JSON` | `$SHOTS_DIR/../report.json` | 度量结果 JSON |
| `CHROME` | 内置 Chromium 路径 | 可执行文件路径 |

## 覆盖页面

`dashboard`、`shops`、`enum-map`、`runtime-configs`、`sync-jobs`、
`manual-costs`、`intercept-configs`、`intercept-requests`、
`intercept-stats`、`ad-daily`（内联 HTML 于 `ad_daily.py`）、
`spu-roi`、`focused-spus`（后两者自动带 `?shop_pk=7` 以触发主表数据渲染，
否则空态不构成有效巡检）。

## 字体巡检（probe_ui_font_audit.js）

布局之外，字体是否全站一致靠另一个探针度量：它不看声明，而是用 CDP
`CSS.getPlatformFontsForNode` 读**浏览器实际命中**的字体族（含 fontconfig 替换结果），
再叠加每页 computed `font-size` / `font-family` 分布与样式表声明清单。

```bash
# 1) 渲染全部页面（同上，只读）
.venv/bin/python scripts/probe_ui_layout_pages.py --out /tmp/ui-audit/pages

# 2) 字体/字号巡检（报告：/tmp/ui-audit/font-report.json）
NODE_PATH=/home/schan/pi-web/node_modules \
  node scripts/probe_ui_font_audit.js
```

判定标准：12 个页面命中**同一套**字体族（sans / mono / serif 各一支），出现只在
个别页面的族名即为不一致（如 vendor jsoneditor 的 `arial` → `Liberation Sans`）。

## 已进 e2e 的部分（lane `e2e-ui-render`，2026-10-04）

探针是 mock 驱动、不进 CI；下面两类回归已改由 live e2e 把守，
跑法 `bash scripts/test_isolated.sh e2e`：

- **多视口横向溢出**：`tests/e2e/test_ui_render_smoke.py`，13 页在 1440、
  广告日明细另在 1280/1366/1536/1920/2560 断言 `scrollWidth` 不超视口；
- **字体栈一致性**：同文件，断言每页含文字元素的 computed `font-family`
  只能来自 `tokens.css` 的 `--sans/--mono/--serif`。

本探针继续承担**截图复核**与 mock 数据下的 DOM 度量；两支探针的 ad-daily
载荷共用 `scripts/ui_audit_mocks.js`（契约见 `docs/api/ad-daily.md`）。

## 读图复核

度量是定量防线；字体截断、间距、配色类问题仍需人工过截图
（`$SHOTS_DIR/*.png`，每页 2560/1440 各一张）。发现视觉问题时按
`docs/guides/agent-git-workflow.md` 走 lane 流程修复。

## 历史

- 2026-10-02：首次落盘。源自全站 UI 巡检（tower `ui-audit-all-pages` /
  `ui-audit-deep-dive`）的一次性 harness。巡检结论：除 SPU ROI 主表
  `fitData` 空白（已由 `fix/spu-roi-table-layout` 修复为 `fitColumns`）外
  无新增布局缺陷。
- 2026-10-04：新增 `probe_ui_font_audit.js`（lane `feature/ui-font-unify`）。
  基线：12 页命中 7 套计算字族，`runtime-configs` 因 vendor 编辑器掉到
  `DejaVu Sans Mono` / `Liberation Sans`；修后收敛为同一套。
