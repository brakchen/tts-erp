"""The retired per-page frontend assets (P6 retirement, 2026-11-08).

The old per-page implementations — one Jinja template plus one hand-written
script per page — are gone. Every registered page now renders through the
shared React frontend release, so no served document may reference them.

``console.js`` is deliberately **not** listed: it is mounted as a public
static asset on ``app.py`` (not a page asset) and is still shipped in this
retirement round.
"""

from __future__ import annotations

RETIRED_PAGE_SCRIPTS = (
    "ad-daily.js",
    "dashboard.js",
    "enum-map.js",
    "intercept-configs.js",
    "intercept-requests.js",
    "intercept-stats.js",
    "runtime-configs.js",
    "shops.js",
    "spu-profit-deterioration.js",
    "sync-jobs.js",
    "users.js",
    "video-publish.js",
)
