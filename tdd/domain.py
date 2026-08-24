"""tts-erp domain types and protocols.

Pure domain layer — no I/O, no framework, no DB. This module is the
contract every other layer (HTTP client, token provider, repositories,
business functions, FastAPI app) hangs off of.

Layering:
    domain.py           (this file — types only)
    ↓
    tts_business.py     (pure business functions: sync_orders, etc.)
    ↓ uses
    repositories.py     (Repository protocols + PG implementations)
    ↓ uses
    http_client.py      (HttpClient protocol + TikTokHttpClient)
    ↓ uses
    tts_signing.py      (HMAC signing + raw HTTP)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# ─── Value objects ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Creds:
    """Credentials needed to call TikTok Partner API for one shop.

    Returned by TokenProvider.get(shop_id). The access_token is
    short-lived; shop_cipher is the encrypted-shop identifier used
    as a query param.
    """

    access_token: str
    shop_cipher: str
    region: str = ""  # e.g. "VN", "US"; not used by signing but kept for callers
    shop_id: str = ""


@dataclass
class SyncResult:
    """Outcome of one sync_* business function call.

    Invariant: error is None → ok; error is not None → failed.
    saved/total/pages may be 0 on error.
    """

    saved: int
    total: int
    pages: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "saved": self.saved,
            "total": self.total,
            "pages": self.pages,
        }
        if self.error is not None:
            d["error"] = self.error
        return d


# ─── Protocols (dependency boundaries) ───────────────────────────────


class HttpClient(Protocol):
    """Single-responsibility HTTP client interface.

    TikTok-specific concerns (signing, query encoding) live in the
    concrete TikTokHttpClient. The protocol is shape-only so business
    code can be tested with a FakeHttpClient.
    """

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        extra_params: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Return parsed JSON body.

        Errors are NOT raised — they're embedded in the response:
        - On HTTPError: {code: <int>, message: ..., ...}
        - On URLError: {code: -1, message: "network error: ..."}
        - On success: {code: 0, data: {...}, ...}
        """
        ...


class TokenProvider(Protocol):
    """Source of access_token + shop_cipher for a given shop_id.

    Production: LocalTokenProvider (in-process call to oauth_receiver_core).
    Test: FakeTokenProvider (returns a fixed Creds).
    """

    def get(self, shop_id: str) -> Creds:
        """Return creds for the shop, or raise a TokenError."""
        ...


class TokenError(Exception):
    """Raised when TokenProvider cannot return valid creds for a shop."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status  # HTTP status to return to the caller


# ─── Sync type enum (constants) ──────────────────────────────────────

SYNC_TYPE_ORDERS_SEARCH = "orders_search"
SYNC_TYPE_PAYMENTS = "payments"
SYNC_TYPE_STATEMENTS = "statements"
SYNC_TYPE_RETURNS = "returns"
SYNC_TYPE_CANCELLATIONS = "cancellations"
SYNC_TYPE_ORDER_DETAIL = "order_detail"

ALL_SYNC_TYPES: tuple[str, ...] = (
    SYNC_TYPE_ORDERS_SEARCH,
    SYNC_TYPE_PAYMENTS,
    SYNC_TYPE_STATEMENTS,
    SYNC_TYPE_RETURNS,
    SYNC_TYPE_CANCELLATIONS,
)
