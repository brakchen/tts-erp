"""The retired per-page frontend assets (P6 retirement, 2026-11-08).

``tts_erp_v2/static/js/*.js``, ``tts_erp_v2/static/css/*.css`` and
``tts_erp_v2/templates/pages/*.html`` were the old per-page implementations.
Every registered page now renders through the shared React frontend release,
so no served document may reference them.

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

# The per-page stylesheets retired with the same round. A stylesheet is an
# asset exactly like a script: a served document that still references one
# would pull back a page-specific owner that React already replaced.
#
# These four are the only orphans under ``static/css``. The remaining files
# there are live and must not be listed:
# ``tokens.css`` and ``navigation.css`` are imported by ``frontend/src/styles.css``,
# ``common.css`` and ``video-publish.css`` are asserted by
# ``tests/publishing/test_contracts.py``.
RETIRED_PAGE_STYLES = (
    "ad-daily.css",
    "runtime-configs.css",
    "spu-profit-deterioration.css",
    "sync-jobs.css",
)

# Every retired asset name, for the "no served document may reference a
# retired asset" assertion. Use this instead of iterating one category at a
# time, so a new retirement only has to be recorded once.
RETIRED_ASSETS = RETIRED_PAGE_SCRIPTS + RETIRED_PAGE_STYLES
