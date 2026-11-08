# Vendored frontend assets

| File | Package | Version | License | Source |
| --- | --- | --- | --- | --- |
| `bootstrap.min.css` | Bootstrap | 5.3.8 | MIT | <https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/> |
| `tabulator.min.js` | Tabulator | 6.3.1 | MIT | <https://cdn.jsdelivr.net/npm/tabulator-tables@6.3.1/dist/js/tabulator.min.js> (source: <https://github.com/olifolkerd/tabulator>) |
| `tabulator.min.css` | Tabulator | 6.3.1 | MIT | <https://cdn.jsdelivr.net/npm/tabulator-tables@6.3.1/dist/css/tabulator.min.css> |

Self-hosted deliberately: operators reach this service over a private NAT
tunnel; third-party CDN links would leak operator IPs and break offline.
No Bootstrap JS — `auth.py`'s fallback login document still uses Bootstrap
utility/component classes.

SPU ROI and focused pages share `frontend/src/pages/spu-profitability/`.
Their React adapter initializes the existing Tabulator 6.3.1 inside an empty ref;
React owns filters, requests, summaries and detail content. Official integration:
<https://www.tabulator.info/docs/6.x/react/>. The adapter loads the pinned,
self-hosted JS and Vite bundles its pinned CSS; no new table wrapper dependency.

Bootstrap MIT license text: <https://github.com/twbs/bootstrap/blob/main/LICENSE>
(copyright 2011-2025 The Bootstrap Authors).

Tabulator's MIT license text ships with the npm package
<https://github.com/olifolkerd/tabulator> (copyright OLIF KERKERS).

Vendor files are immutable per pinned version and therefore referenced without
`?v=` cache-bust tokens, same as Bootstrap.

## 2026-11-08 清理（P6 退役收口）

以下 vendored 资产已删除——它们的消费者在 P6 期间已全部退役，React 侧改用
npm 依赖或共享样式，不再加载这些文件：

| 已删除 | 原消费者 | 现在的替代 |
| --- | --- | --- |
| `uplot.iife.min.js`、`uplot.min.css` | 旧 `static/js/intercept-stats.js` | npm `uplot@1.6.32` + `uplot-react@1.2.4`，`frontend/src/pages/intercept-stats/DailyChart.tsx` 直接 import |
| `jsoneditor.min.js`、`jsoneditor.min.css`、`jsoneditor.LICENSE`、`img/jsoneditor-icons.svg` | 旧 `static/js/runtime-configs.js` | npm `vanilla-jsoneditor@3.13.0` + `lossless-json@4.3.3`，`frontend/src/pages/runtime-configs/JsonEditor.tsx` |
| `tom-select.complete.min.js`、`tom-select.bootstrap5.min.css`、`tom-select.LICENSE` | 旧 `static/js/enum-map.js` | HeroUI `Select`（`frontend/src/pages/enum-map/index.tsx`） |

删除依据见 `docs/handoff/p6-test-side-retirement-plan.md`。随包删除的 LICENSE 文件
对应的是随包删除的代码，不留孤立许可证。

`common.css` 里的 `html .uplot { font-family: var(--sans); }` **保留**——npm 版
uplot 的 CSS 同样声明了 `font-family: system-ui, … Arial …`，这条特异性提升
仍然必要。
