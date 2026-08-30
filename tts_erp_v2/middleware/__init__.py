"""tts_erp_v2 middleware: AuthMiddleware + RateLimitMiddleware + AccessLogMiddleware.

These are plain ASGI middleware (NOT BaseHTTPMiddleware) so that
``scope['api_key_hash']`` propagates to the rate limiter downstream.
The pattern matches the legacy ``tdd/auth.py`` + ``tdd/rate_limit.py``
exactly; only the data source changed from raw psycopg to ORM/SQLAlchemy
2.0 against ``security.api_keys``.

``AccessLogMiddleware`` is the OUTERMOST layer (registered last in
``build_app()``) so it sees the final response status and total
duration. It writes one structured ``key=value`` line per request to
stdout — the operator's single source of truth for "who hit what,
how, when, and what was the auth outcome" without having to cross-
reference NGINX access logs.
"""

__all__ = [
    "AccessLogMiddleware",
    "AuthMiddleware",
    "RateLimitMiddleware",
]
