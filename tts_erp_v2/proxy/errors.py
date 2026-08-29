"""Proxy-layer exception hierarchy.

Single import surface for all upstream-specific failures (TikTok Shop
+ Miaoshou) and the proxy-level wrapping that callers (API handlers,
sync-worker jobs) should catch.

Design notes
------------
* ``ProxyError`` is the abstract base. Catch it for any proxy-layer
  problem without enumerating concrete subclasses.
* ``TransientProxyError`` marks failures that the proxy already
  *tried* to retry internally (network blip, rate-limit) but failed.
  Callers may still want to back off at a higher level; we expose
  this so the sync-worker scheduler can drop these into its own
  retry queue without parsing error strings.
* Concrete subclasses carry enough context to log without leaking
  secrets: the upstream URL/status is safe to log; raw response
  bodies are truncated to ``body_preview`` (300 chars).
"""
from __future__ import annotations

from typing import Any


class ProxyError(RuntimeError):
    """Base for all proxy-layer failures."""


class TransientProxyError(ProxyError):
    """Network or rate-limit failure that retried internally and gave up."""


class AuthenticationError(ProxyError):
    """Token missing, expired, or rejected by upstream (401/403)."""


class RateLimitedError(TransientProxyError):
    """Upstream rate-limited us even after internal retry budget.

    Carries ``retry_after_seconds`` if the upstream supplied one.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        body_preview: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.body_preview = body_preview


class UpstreamHttpError(ProxyError):
    """HTTP 4xx/5xx from the upstream that is *not* retryable.

    Carries the upstream status code + a body preview so the caller can
    log/branch without re-fetching.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        body_preview: str | None = None,
        upstream_code: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body_preview = body_preview
        self.upstream_code = upstream_code


class SigningError(ProxyError):
    """Signing/credential misconfiguration (no app_secret, bad key, ...)."""


class DecryptionError(ProxyError):
    """Fernet decryption failed (bad key, tampered ciphertext)."""


__all__ = [
    "ProxyError",
    "TransientProxyError",
    "AuthenticationError",
    "RateLimitedError",
    "UpstreamHttpError",
    "SigningError",
    "DecryptionError",
]
