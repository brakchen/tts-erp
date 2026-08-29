"""tts_erp_v2 middleware: AuthMiddleware + RateLimitMiddleware.

These are plain ASGI middleware (NOT BaseHTTPMiddleware) so that
``scope['api_key_hash']`` propagates to the rate limiter downstream.
The pattern matches the legacy ``tdd/auth.py`` + ``tdd/rate_limit.py``
exactly; only the data source changed from raw psycopg to ORM/SQLAlchemy
2.0 against ``security.api_keys``.
"""
