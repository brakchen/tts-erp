# Vendored frontend assets

| File | Package | Version | License | Source |
| --- | --- | --- | --- | --- |
| `bootstrap.min.css` | Bootstrap | 5.3.8 | MIT | <https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/> |
| `tom-select.complete.min.js` | Tom Select | 2.6.2 | Apache-2.0 | <https://registry.npmjs.org/tom-select/-/tom-select-2.6.2.tgz> |
| `tom-select.bootstrap5.min.css` | Tom Select | 2.6.2 | Apache-2.0 | <https://registry.npmjs.org/tom-select/-/tom-select-2.6.2.tgz> |
| `tom-select.LICENSE` | Tom Select | 2.6.2 | Apache-2.0 | package `LICENSE` |

Self-hosted deliberately: operators reach this service over a private NAT
tunnel; third-party CDN links would leak operator IPs and break offline.
No Bootstrap JS — pages use Bootstrap utility/component classes. The SPU ROI
page enhances a native `<select multiple>` with Tom Select's Bootstrap 5 theme;
application-specific behavior remains in `/static/js/spu-roi.js`.

Bootstrap MIT license text: <https://github.com/twbs/bootstrap/blob/main/LICENSE>
(copyright 2011-2025 The Bootstrap Authors). Tom Select's Apache-2.0 license is
vendored verbatim as `tom-select.LICENSE`.
