"""analytics_sync package — Chrome extension data sync backend.

Provides handlers, Pydantic models, and DB repositories for the
``/v1/analytics/sync/*`` endpoints that the Chrome extension
(``tk-adv-cost-monitor``) uses to push analytics records and read
per-(storageKey, campaignId) cursors.

This package is NOT a standalone service anymore (the old port-9878
deployment was retired 2026-08-30). The single ``APIRouter`` exported
from ``analytics_sync.app`` is mounted by ``tts_erp_v2.app:build_app()``
via::

     app.include_router(router, prefix="/v1/analytics/sync")

Auth + rate-limit come from the v2 app's middleware stack
(``tts_erp_v2.middleware.auth.AuthMiddleware`` and
``tts_erp_v2.middleware.rate_limit.RateLimitMiddleware``).

See ``setup/analytics-sync.md`` for the deployment topology and
``analytics_sync/schema.sql`` for the DB schema (5 tables).
"""
