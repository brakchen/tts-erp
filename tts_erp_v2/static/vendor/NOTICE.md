# Vendored frontend assets

| File                 | Package    | Version | License | Source                                                        |
| -------------------- | ---------- | ------- | ------- | ------------------------------------------------------------- |
| `bootstrap.min.css`  | Bootstrap  | 5.3.8   | MIT     | <https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/>      |

Self-hosted deliberately: operators reach this service over a private NAT
tunnel; third-party CDN links would leak operator IPs and break offline.
No Bootstrap JS — the page uses Bootstrap utility/component classes only,
all behaviour is plain DOM in `/static/js/console.js`.

MIT license text: <https://github.com/twbs/bootstrap/blob/main/LICENSE>
(copyright 2011-2025 The Bootstrap Authors).
