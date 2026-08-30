"""Single-source access log for every v2 request.

What this replaces
------------------
Before this middleware landed, every request produced two log lines
that the operator had to mentally stitch together:

* ``logs/stdout.log`` — uvicorn's default access log, which sees the
  *direct* peer (the docker bridge / NGINX container) instead of the
  real client. To find the real client IP you had to ``docker exec
  nginx-gw cat /var/log/nginx/access.log`` and grep by timestamp.
* ``logs/stderr.log`` — only the [auth] denied lines from
  AuthMiddleware; everything else (200s, 4xx, login attempts, cookie
  vs bearer) was invisible.

After
-----
One structured line per request, written to stdout (so it lands in
``logs/stdout.log`` next to whatever else uvicorn is logging), with
every piece of context an operator needs to answer "who hit what,
how, when, and what was the auth outcome":

    2026-08-30T22:15:30+08:00 123.118.222.5:54321 \\
        method=POST path=/v2/auth/login status=401 dur=0.023s \\
        auth=cookie key=- role=- \\
        xfp=/tts/ xfrp=https body=87 \\
        ua=Mozilla/5.0 ... ttfb=0.018

Every field is ``key=value`` so a single ``grep key_prefix=ttserp_rw_D9PE``
(or ``awk -F'[= ]' '/path=\\/v2\\/auth\\/login/ && /result=invalid_key/'``)
answers the question without opening NGINX.

Design notes
------------
* Runs as the OUTERMOST middleware (last ``add_middleware`` call per
  FastAPI's reverse-wrap convention) so it sees the final response
  status, including ones set by the inner RateLimit/Auth chain.
* Real client IP resolution: ``X-Forwarded-For`` first hop, then
  ``X-Real-IP``, then ``scope["client"]`` as a last resort (covers
  the ``curl 127.0.0.1`` case). The trust chain matches NGINX
  config: only the NAT proxy in front of us is allowed to set these
  headers.
* ``key_prefix`` is the first 12 chars of the API key hash (a SHA-256
  hex, so 12 chars ≈ 48 bits — enough to identify a key in logs
  without leaking anything reversible). Empty when no key was
  presented.
* Disabling: ``TTS_ERP_ACCESS_LOG=0`` (env). Tests set this so the
  test output isn't drowned in access lines.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import time

logger = logging.getLogger("tts_erp_v2.access")

# uvicorn's logging.config.dictConfig only attaches handlers to its
# own loggers (uvicorn / uvicorn.error / uvicorn.access). The root
# logger has no handler in that config, so any other logger that
# only inherits from root would be a silent no-op. Attach a stdout
# StreamHandler here so the structured line actually reaches
# stdout (and via systemd's StandardOutput=append:, logs/stdout.log).
# ``propagate=False`` prevents the same line from also being sent
# to stderr through the lastResort handler.
#
# The level is also explicitly set: Python loggers default to WARNING,
# and uvicorn's dictConfig doesn't touch our namespace, so without
# an explicit setLevel, logger.info(...) is filtered BEFORE the
# handler ever runs and the whole middleware is a no-op. (Bitten by
# this in production 2026-08-30 — the service was running, requests
# were being served, stdout.log was just empty for the new format.)
logger.setLevel(logging.INFO)
if not any(
    isinstance(h, logging.StreamHandler) and h.stream is sys.stdout
    for h in logger.handlers
):
    _stdout = logging.StreamHandler(sys.stdout)
    _stdout.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_stdout)
    logger.propagate = False

# Env-var name to disable (used by tests and any operator who wants
# a quieter stdout under a heavy-traffic incident).
_DISABLED_ENV = "TTS_ERP_ACCESS_LOG"


def _is_enabled() -> bool:
    raw = os.environ.get(_DISABLED_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _client_ip(scope: dict) -> str:
    """Resolve the real client IP from the standard reverse-proxy chain.

    Priority:
      1. ``X-Forwarded-For`` first hop (the original client per RFC 7239).
      2. ``X-Real-IP`` (set by NGINX when the proxy chain doesn't include
         multiple hops).
      3. ``scope["client"]`` — the direct TCP peer (NGINX container IP
         in production, ``127.0.0.1`` in dev). Last resort only.

    Trust model: only the NGINX container in front of us is allowed to
    set these headers. The docker network makes that enforceable.
    """
    for hk, hv in scope.get("headers") or []:
        if hk.lower() != b"x-forwarded-for":
            continue
        raw = hv.decode("latin-1").strip()
        if raw:
            # First IP in the comma-separated chain is the original
            # client; everything after is an intermediate proxy.
            return raw.split(",", 1)[0].strip()
    for hk, hv in scope.get("headers") or []:
        if hk.lower() == b"x-real-ip":
            raw = hv.decode("latin-1").strip()
            if raw:
                return raw
    client = scope.get("client")
    return client[0] if client else "-"


def _header(scope: dict, name: str) -> str:
    target = name.encode("latin-1").lower()
    for hk, hv in scope.get("headers") or []:
        if hk.lower() == target:
            return hv.decode("latin-1").strip()
    return ""


def _truncate_ua(ua: str, limit: int = 60) -> str:
    if not ua:
        return ""
    if len(ua) <= limit:
        return ua
    return ua[: limit - 1] + "…"


def key_prefix(plaintext_key: str) -> str:
    """First 12 hex chars of ``sha256(plaintext_key)``.

    Operators grep ``key=abc123def456`` to correlate a specific key
    across the access log (where the value comes from the cookie's
    hash) AND the auth.login event log (where the value comes from
    the plaintext in the login body). The 12-hex-char format is
    the same in both places so a single grep ties the two together.

    Lives here rather than in the login router because the contract
    is "log-correlation token derived from an API key", and the
    access log is the canonical source of those tokens.
    """
    return hashlib.sha256(plaintext_key.encode()).hexdigest()[:12]


# Backwards-compat alias for the name used by tts_erp_v2.api.v2.auth
# before the helper moved here. Kept so any third-party reader of
# the auth module that imported _key_prefix still resolves; the
# login handler itself has been updated to use the new public name.
_key_prefix = key_prefix


class AccessLogMiddleware:
    """One-line structured access log per HTTP request.

    Register LAST in ``build_app()`` so it wraps the whole stack
    outermost — that's the only position where the final response
    status and total duration are both visible.
    """

    def __init__(self, app):
        self.app = app
        self._enabled = _is_enabled()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self._enabled:
            await self.app(scope, receive, send)
            return

        # Snapshot the fields the auth middleware sets on scope. The
        # inner stack hasn't run yet, so these are unset on the way
        # in; we read them on the way out, after ``await send`` returns.
        method = scope["method"]
        path = scope["path"]
        raw_qs = scope.get("query_string", b"")
        full_path = path + (("?" + raw_qs.decode("latin-1")) if raw_qs else "")
        client_ip = _client_ip(scope)
        # Preserve the peer port when the request came in directly
        # (e.g. curl in dev) — useful for log correlation. When the
        # connection came through NGINX the port is 0 (NAT strips it).
        client_port = (scope.get("client") or (None, None))[1] or ""
        if client_port and client_port != "0":
            client = f"{client_ip}:{client_port}"
        else:
            client = client_ip
        ua = _truncate_ua(_header(scope, "user-agent"))
        body_len = _header(scope, "content-length") or "0"
        xfp = _header(scope, "x-forwarded-prefix")  # /tts/ when proxied
        xfrp = _header(scope, "x-forwarded-proto")  # https when proxied

        started = time.monotonic()
        # status + first-byte timing are captured via a custom send
        # wrapper so the access log records the FINAL status (after
        # RateLimit / Auth short-circuits) and a useful TTFB for
        # spotting a slow upstream even on a 200.
        status_holder = {"code": 500, "first_byte_at": None}

        async def _send(event):
            if event["type"] == "http.response.start":
                status_holder["code"] = event["status"]
                if status_holder["first_byte_at"] is None:
                    status_holder["first_byte_at"] = time.monotonic()
            await send(event)

        try:
            await self.app(scope, receive, _send)
        except Exception:
            # Unhandled exception escaping the whole stack. Log it as
            # a 500 and re-raise so the existing error-handler
            # machinery can still wrap it.
            dur = time.monotonic() - started
            logger.exception(
                "request crashed: %s %s client=%s dur=%.3fs",
                method,
                full_path,
                client,
                dur,
            )
            raise

        dur = time.monotonic() - started
        ttfb = (
            status_holder["first_byte_at"] - started
            if status_holder["first_byte_at"] is not None
            else dur
        )
        scope_auth = scope.get("auth_method") or "-"
        scope_role = scope.get("api_key_role") or "-"
        scope_key_hash = scope.get("api_key_hash") or ""
        key_prefix = scope_key_hash[:12] if scope_key_hash else "-"

        # ISO-8601 local timestamp is unambiguous to grep + sort
        # and doesn't depend on locale or uvicorn's access-log
        # format. Wall-clock, not monotonic, so it correlates
        # with journalctl and the stderr tracebacks.
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        # Single space-separated line; whitespace in UA is preserved
        # by quoting (we strip nothing — UA tokens are ASCII).
        logger.info(
            "%s %s method=%s path=%s status=%d dur=%.3fs ttfb=%.3fs "
            "auth=%s key=%s role=%s "
            "xfp=%s xfrp=%s body=%s ua=%s",
            ts,
            client,
            method,
            full_path,
            status_holder["code"],
            dur,
            ttfb,
            scope_auth,
            key_prefix,
            scope_role,
            xfp or "-",
            xfrp or "-",
            body_len,
            ua,
        )


__all__ = ["AccessLogMiddleware"]
