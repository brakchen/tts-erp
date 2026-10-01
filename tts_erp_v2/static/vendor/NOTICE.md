# Vendored frontend assets

| File | Package | Version | License | Source |
| --- | --- | --- | --- | --- |
| `bootstrap.min.css` | Bootstrap | 5.3.8 | MIT | <https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/> |
| `tom-select.complete.min.js` | Tom Select | 2.6.2 | Apache-2.0 | <https://registry.npmjs.org/tom-select/-/tom-select-2.6.2.tgz> |
| `tom-select.bootstrap5.min.css` | Tom Select | 2.6.2 | Apache-2.0 | <https://registry.npmjs.org/tom-select/-/tom-select-2.6.2.tgz> |
| `tom-select.LICENSE` | Tom Select | 2.6.2 | Apache-2.0 | package `LICENSE` |
| `jsoneditor.min.js` | JSONEditor | 10.4.3 | Apache-2.0 | <https://cdn.jsdelivr.net/npm/jsoneditor@10.4.3/dist/jsoneditor.min.js> (source: <https://github.com/josdejong/jsoneditor>) |
| `jsoneditor.min.css` | JSONEditor | 10.4.3 | Apache-2.0 | <https://cdn.jsdelivr.net/npm/jsoneditor@10.4.3/dist/jsoneditor.min.css> |
| `jsoneditor.LICENSE` | JSONEditor | 10.4.3 | Apache-2.0 | package `LICENSE` |
| `img/jsoneditor-icons.svg` | JSONEditor | 10.4.3 | Apache-2.0 | <https://cdn.jsdelivr.net/npm/jsoneditor@10.4.3/dist/img/jsoneditor-icons.svg> |
| `uplot.iife.min.js` | uPlot | 1.6.32 | MIT | <https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.iife.min.js> (source: <https://github.com/leeoniya/uPlot>) |
| `uplot.min.css` | uPlot | 1.6.32 | MIT | <https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.min.css> |
| `tabulator.min.js` | Tabulator | 6.3.1 | MIT | <https://cdn.jsdelivr.net/npm/tabulator-tables@6.3.1/dist/js/tabulator.min.js> (source: <https://github.com/olifolkerd/tabulator>) |
| `tabulator.min.css` | Tabulator | 6.3.1 | MIT | <https://cdn.jsdelivr.net/npm/tabulator-tables@6.3.1/dist/css/tabulator.min.css> |

Self-hosted deliberately: operators reach this service over a private NAT
tunnel; third-party CDN links would leak operator IPs and break offline.
No Bootstrap JS — pages use Bootstrap utility/component classes. The SPU ROI
page enhances a native `<select multiple>` with Tom Select's Bootstrap 5 theme;
application-specific behavior remains in `/static/js/spu-roi.js`.

Bootstrap MIT license text: <https://github.com/twbs/bootstrap/blob/main/LICENSE>
(copyright 2011-2025 The Bootstrap Authors). Tom Select's Apache-2.0 license is
vendored verbatim as `tom-select.LICENSE`; JSONEditor's Apache-2.0 license is
vendored verbatim as `jsoneditor.LICENSE`.

uPlot MIT license text: <https://github.com/leeoniya/uPlot/blob/master/LICENSE>
(copyright Leon Sorokin). Used by the intercept-stats daily bar chart
(`js/intercept-stats.js`); vendor files are immutable per pinned version and
therefore referenced without `?v=` cache-bust tokens, same as Bootstrap.
