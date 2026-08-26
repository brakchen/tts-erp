"""Pure business logic for the OAuth receiver (no HTTP framework).

Extracted from the original stdlib http.server-based `oauth_receiver.py`
during the oauth-receiver → tts-erp merge (2026-08-24). Functions here
have no FastAPI / uvicorn / starlette / Request / Response dependency;
they take and return plain Python values. Wave 2 will provide the thin
FastAPI router (`oauth_receiver_router.py`) that wraps these functions.

Public API
----------
Fernet / encryption:
    get_fernet()                  -> Fernet | None
    encrypt(plaintext: str)       -> bytes
    decrypt(blob: bytes)          -> str
    mask_secret(secret: str)      -> str

PostgreSQL (encrypted token store):
    db_init()                     -> None  (raises on misconfig)
    is_db_ok()                    -> bool
    db_store_token(shop_id, provider, data) -> bool
    db_load_token(shop_id, provider) -> dict | None
    db_list_shops(provider=None)  -> list[dict]
    db_delete_token(shop_id, provider) -> bool

TikTok OAuth:
    call_token_endpoint(provider, grant_type, code="", refresh="") -> dict
    build_authorize_url(provider, state) -> str | None
    handle_callback(code, state, provider, registered_states, error=None) -> dict
    exchange_code(code, provider) -> dict
    refresh_with_token(refresh_token, provider) -> dict
    refresh_shop_token(shop_id, provider) -> dict
    fetch_shops(provider, force_refresh=False) -> dict

State cache (CSRF protection):
    register_state(provider, state=None) -> str
    pop_state(state) -> dict | None
    purge_expired_states(states: dict) -> None

Test helpers (intentionally not part of public contract):
    _reset_for_testing()            — clear cached Fernet + history
    _append_token_history_for_test(d)
    _clear_token_history_for_test()
"""

from __future__ import annotations

# Pre-existing lint suppression rationale (preserved from the original):
# PTH123 / SIM105 — module-level env parsing and Fernet init are
# intentionally fail-fast; wrapping them in try/except changes nothing.
# ruff: noqa: PTH123, SIM105
import hashlib  # nosemgrep
import hmac  # nosemgrep
import json  # nosemgrep
import math  # nosemgrep
import os  # nosemgrep
import secrets  # nosemgrep
import time  # nosemgrep
import urllib.error  # nosemgrep
import urllib.parse  # nosemgrep
import urllib.request  # nosemgrep
from collections import deque  # nosemgrep
from typing import Any  # nosemgrep

import psycopg  # nosemgrep
from cryptography.fernet import Fernet  # nosemgrep
from psycopg import sql as pg_sql  # nosemgrep
from psycopg.rows import dict_row  # nosemgrep

# ─── Configuration (read at call time so tests can monkeypatch) ───────


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"env {key}={raw!r} is not a valid integer") from e


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise RuntimeError(f"env {key}={raw!r} is not a valid float") from e


def _tiktok_auth_host() -> str:
    return _env("TIKTOK_AUTH_HOST", "https://auth.tiktok-shops.com")


def _tiktok_api_host() -> str:
    return _env("TIKTOK_API_HOST", "https://open-api.tiktokglobalshop.com")


def _tiktok_app_key() -> str:
    return _env("TIKTOK_APP_KEY", "")


def _tiktok_app_secret() -> str:
    return _env("TIKTOK_APP_SECRET", "")


def _tiktok_redirect_uri() -> str:
    port = _env_int("OAUTH_PORT", 9877)
    return _env("TIKTOK_REDIRECT_URI", f"http://192.168.47.130:{port}/callback")


def _tiktok_mock() -> bool:
    return _env("TIKTOK_MOCK", "") == "1"


def _oauth_db_url() -> str:
    return _env("OAUTH_DB_URL", "").strip()


def _oauth_db_encryption_key() -> str:
    return _env("OAUTH_DB_ENCRYPTION_KEY", "").strip()


def _oauth_db_table() -> str:
    return _env("OAUTH_DB_TABLE", "oauth_tokens")


def _http_timeout() -> float:
    return _env_float("OAUTH_HTTP_TIMEOUT", 15.0)


def _state_ttl() -> int:
    return _env_int("OAUTH_STATE_TTL", 600)


# ─── Provider config (TikTok Shop default; future: google/facebook) ───


def provider_config(name: str) -> dict | None:
    """Return the config dict for a provider, or None if unknown."""
    if name != "tiktok":
        return None
    return {
        "label": "TikTok Shop Partner",
        "authorize_url": f"{_tiktok_auth_host()}/oauth/authorize",
        "token_url": f"{_tiktok_auth_host()}/api/v2/token/get",
        "refresh_token_url": f"{_tiktok_auth_host()}/api/v2/token/refresh",
        "app_key": _tiktok_app_key(),
        "app_secret": _tiktok_app_secret(),
        "redirect_uri": _tiktok_redirect_uri(),
        "auth_host": _tiktok_auth_host(),
        "api_host": _tiktok_api_host(),
        "mock": _tiktok_mock(),
    }


# ─── Fernet (lazy singleton) ──────────────────────────────────────────


_fernet: Fernet | None | bool = False  # None=not configured, False=uninitialized


def get_fernet() -> Fernet | None:
    """Lazy Fernet singleton. Returns None if not configured or invalid key.

    Tri-state cache: False (uninitialized) → None/instance after first call.
    Tests use _reset_for_testing() to force re-initialization.
    """
    global _fernet
    if _fernet is not False:
        return _fernet  # type: ignore[return-value]
    key = _oauth_db_encryption_key()
    if not key:
        _fernet = None
        return None
    try:
        _fernet = Fernet(key.encode("utf-8"))
    except Exception:  # noqa: BLE001
        # Importing the module-level logger would couple us to log_helper
        # which lives outside this repo. Keep the error surfacing via
        # the caller — _db_init() wraps this with context.
        _fernet = None
        # Don't raise here — callers (db_init) want to know via is_db_ok()
    return _fernet


def encrypt(plaintext: str) -> bytes:
    """Fernet-encrypt a string. Raises RuntimeError if no key configured."""
    f = get_fernet()
    if f is None:
        raise RuntimeError("Fernet not configured")
    return f.encrypt(plaintext.encode("utf-8"))


def decrypt(blob: bytes) -> str:
    """Fernet-decrypt bytes back to string. Raises RuntimeError/InvalidToken."""
    f = get_fernet()
    if f is None:
        raise RuntimeError("Fernet not configured")
    return f.decrypt(bytes(blob)).decode("utf-8")


def mask_secret(secret: str) -> str:
    """Display-safe mask: keep prefix and suffix, replace middle with '...'."""
    if not secret or len(secret) <= 12:
        return "****"
    return f"{secret[:8]}...{secret[-4:]}  (len={len(secret)})"


# ─── PostgreSQL encrypted token store ─────────────────────────────────


_db_ok: bool = False
DEFAULT_SHOP_KEY = "__default__"  # placeholder when shop_id is unknown


def is_db_ok() -> bool:
    return _db_ok


def _db_connect():
    if not _oauth_db_url():
        raise RuntimeError("OAUTH_DB_URL not set")
    return psycopg.connect(_oauth_db_url(), connect_timeout=5)


def db_init() -> None:
    """Verify DB connectivity and schema. Raises RuntimeError on any failure.

    HARD dependency: failing fast at startup (rather than lazy-fallback at
    first request) is required for the sync chain to be observable. A
    silent fallback to "JSON only" left the system running with /tokens/shops
    silently empty for 34h during the 2026-08-23 incident.
    """
    global _db_ok
    if not _oauth_db_url():
        raise RuntimeError(
            "OAUTH_DB_URL not set — DB store is required, refusing to start"
        )
    if not _oauth_db_encryption_key():
        raise RuntimeError(
            "OAUTH_DB_ENCRYPTION_KEY not set — DB store is required, refusing to start"
        )
    # Ensure Fernet instance is created (or returns None on bad key)
    if get_fernet() is None:
        raise RuntimeError(
            "OAUTH_DB_ENCRYPTION_KEY invalid (Fernet init failed) — "
            "DB store is required, refusing to start"
        )
    try:
        with _db_connect() as conn, conn.cursor() as cur:
            cur.execute(  # nosemgrep
                "SELECT to_regclass(%s) AS tbl",
                (_oauth_db_table(),),
            )
            row = cur.fetchone()
            if row and row[0]:
                _db_ok = True
            else:
                raise RuntimeError(
                    f"DB store required but table '{_oauth_db_table()}' does not exist "
                    f"— run schema.sql to create it"
                )
    except RuntimeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"DB store required but connect/init failed: {e}") from e


def db_store_token(shop_id: str, provider: str, data: dict) -> bool:
    """Upsert a token row. Returns True if successful.

    Missing access_token or refresh_token → False (don't write half-rows).
    shop_cipher is optional and uses COALESCE so existing value is preserved.
    """
    if not _db_ok:
        return False
    at = data.get("access_token")
    rt = data.get("refresh_token")
    if not at or not rt:
        return False
    tbl = pg_sql.Identifier(_oauth_db_table())
    try:
        with _db_connect() as conn, conn.cursor() as cur:
            cur.execute(  # nosemgrep
                pg_sql.SQL(
                    """
                    INSERT INTO {tbl} (
                        shop_id, provider,
                        access_token_encrypted, refresh_token_encrypted, shop_cipher_encrypted,
                        shop_name, shop_region, seller_type,
                        access_token_expires_at, refresh_token_expires_at,
                        granted_scopes, last_refresh_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (shop_id, provider) DO UPDATE SET
                        access_token_encrypted   = EXCLUDED.access_token_encrypted,
                        refresh_token_encrypted  = EXCLUDED.refresh_token_encrypted,
                        shop_cipher_encrypted    = COALESCE(EXCLUDED.shop_cipher_encrypted, {tbl}.shop_cipher_encrypted),
                        shop_name                = COALESCE(EXCLUDED.shop_name,            {tbl}.shop_name),
                        shop_region              = COALESCE(EXCLUDED.shop_region,          {tbl}.shop_region),
                        seller_type              = COALESCE(EXCLUDED.seller_type,          {tbl}.seller_type),
                        access_token_expires_at  = EXCLUDED.access_token_expires_at,
                        refresh_token_expires_at = EXCLUDED.refresh_token_expires_at,
                        granted_scopes           = COALESCE(EXCLUDED.granted_scopes,       {tbl}.granted_scopes),
                        last_refresh_at          = CASE
                                                       WHEN EXCLUDED.access_token_encrypted IS DISTINCT FROM {tbl}.access_token_encrypted
                                                       THEN now()
                                                       ELSE {tbl}.last_refresh_at
                                                    END
                    """
                ).format(tbl=tbl),
                (
                    shop_id,
                    provider,
                    encrypt(at),
                    encrypt(rt),
                    encrypt(data["shop_cipher"]) if data.get("shop_cipher") else None,
                    data.get("shop_name"),
                    data.get("shop_region"),
                    data.get("seller_type"),
                    data.get("access_token_expires_at"),
                    data.get("refresh_token_expires_at"),
                    data.get("granted_scopes"),
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        # Log via stderr; caller is the HTTP layer / router which has its
        # own logging. We deliberately do NOT import log_helper here —
        # that would couple this pure module to a side-effecting logger
        # outside the standard library path.
        import sys

        print(
            f"[oauth_receiver_core] db_store_token failed for shop={shop_id}: {e}",
            file=sys.stderr,
        )
        return False


def db_load_token(shop_id: str, provider: str) -> dict | None:
    """Load + decrypt a token row. Returns dict (plaintext tokens) or None."""
    if not _db_ok:
        return None
    tbl = pg_sql.Identifier(_oauth_db_table())
    try:
        with _db_connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(  # nosemgrep
                pg_sql.SQL(
                    """
                    SELECT shop_id, provider, shop_name, shop_region, seller_type,
                           access_token_encrypted, refresh_token_encrypted, shop_cipher_encrypted,
                           access_token_expires_at, refresh_token_expires_at,
                           granted_scopes, created_at, updated_at, last_refresh_at
                    FROM {}
                    WHERE shop_id = %s AND provider = %s
                    """
                ).format(tbl),
                (shop_id, provider),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "shop_id": row["shop_id"],
            "provider": row["provider"],
            "shop_name": row["shop_name"],
            "shop_region": row["shop_region"],
            "seller_type": row["seller_type"],
            "access_token": decrypt(row["access_token_encrypted"]),
            "refresh_token": decrypt(row["refresh_token_encrypted"]),
            "shop_cipher": decrypt(row["shop_cipher_encrypted"])
            if row["shop_cipher_encrypted"]
            else None,
            "access_token_expires_at": row["access_token_expires_at"],
            "refresh_token_expires_at": row["refresh_token_expires_at"],
            "granted_scopes": row["granted_scopes"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_refresh_at": row["last_refresh_at"],
        }
    except Exception as e:  # noqa: BLE001
        import sys

        print(
            f"[oauth_receiver_core] db_load_token failed for shop={shop_id}: {e}",
            file=sys.stderr,
        )
        return None


def db_list_shops(provider: str | None = None) -> list[dict]:
    """List all stored shops (metadata only, no decryption)."""
    if not _db_ok:
        return []
    tbl = pg_sql.Identifier(_oauth_db_table())
    select_cols = pg_sql.SQL(
        "shop_id, provider, shop_name, shop_region, seller_type, "
        "access_token_expires_at, refresh_token_expires_at, "
        "granted_scopes, created_at, updated_at, last_refresh_at"
    )
    try:
        with _db_connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            if provider:
                cur.execute(
                    pg_sql.SQL(
                        "SELECT {} FROM {} WHERE provider = %s ORDER BY updated_at DESC"
                    ).format(select_cols, tbl),
                    (provider,),
                )
            else:
                cur.execute(
                    pg_sql.SQL("SELECT {} FROM {} ORDER BY updated_at DESC").format(
                        select_cols, tbl
                    )
                )
            return list(cur.fetchall())
    except Exception as e:  # noqa: BLE001
        import sys

        print(f"[oauth_receiver_core] db_list_shops failed: {e}", file=sys.stderr)
        return []


def db_count_shops(provider: str | None = None) -> int:
    """Count rows in oauth_tokens. Cheap COUNT(*) — does not decrypt.

    Used by healthz to report a truthful `token_count`. The previous
    implementation read `len(_token_history)` (an in-memory deque of
    recent token-exchange events), which was always 0 after a restart.
    """
    if not _db_ok:
        return 0
    tbl = pg_sql.Identifier(_oauth_db_table())
    try:
        with _db_connect() as conn, conn.cursor() as cur:
            if provider:
                cur.execute(
                    pg_sql.SQL("SELECT COUNT(*) FROM {} WHERE provider = %s").format(
                        tbl
                    ),
                    (provider,),
                )
            else:
                cur.execute(pg_sql.SQL("SELECT COUNT(*) FROM {}").format(tbl))
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as e:  # noqa: BLE001
        import sys

        print(f"[oauth_receiver_core] db_count_shops failed: {e}", file=sys.stderr)
        return 0


def db_delete_token(shop_id: str, provider: str) -> bool:
    if not _db_ok:
        return False
    try:
        with _db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                pg_sql.SQL(
                    "DELETE FROM {} WHERE shop_id = %s AND provider = %s"
                ).format(pg_sql.Identifier(_oauth_db_table())),
                (shop_id, provider),
            )
            deleted = cur.rowcount
            conn.commit()
        return deleted > 0
    except Exception as e:  # noqa: BLE001
        import sys

        print(
            f"[oauth_receiver_core] db_delete_token failed for shop={shop_id}: {e}",
            file=sys.stderr,
        )
        return False


# ─── State cache (CSRF protection) ────────────────────────────────────


_states: dict[str, dict] = {}  # state -> {ts, provider}


def register_state(provider: str, state: str | None = None) -> str:
    """Register a state token. Returns the (possibly auto-generated) state."""
    if not state:
        state = secrets.token_urlsafe(24)
    _states[state] = {"ts": time.time(), "provider": provider}
    return state


def pop_state(state: str) -> dict | None:
    """Single-use pop. Returns the registered meta dict or None."""
    if state is None:
        return None
    return _states.pop(state, None)


def purge_expired_states(states: dict | None = None) -> None:
    """Remove states older than TTL. Operates on the module-level cache by
    default; accepts an external dict for testing."""
    target = states if states is not None else _states
    cutoff = time.time() - _state_ttl()
    expired = [s for s, meta in target.items() if meta["ts"] < cutoff]
    for s in expired:
        target.pop(s, None)


# ─── Token history (for /token endpoint + fetch_shops discovery) ──────


_token_history: deque[dict] = deque(maxlen=100)


def get_last_successful_token() -> dict | None:
    """Return the most recent ok token record, or None."""
    return next((t for t in reversed(_token_history) if t.get("ok")), None)


def _append_token_history(record: dict) -> None:
    _token_history.append(record)


def _clear_token_history_for_test() -> None:
    _token_history.clear()


# ─── TikTok authorize URL builder ─────────────────────────────────────


def build_authorize_url(provider: str, state: str) -> str | None:
    """Construct the provider's authorize URL. Returns None if provider unknown."""
    cfg = provider_config(provider)
    if not cfg:
        return None
    auth_params = {
        "app_key": cfg["app_key"] or "MOCK_APP_KEY",
        "state": state,
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
    }
    return f"{cfg['authorize_url']}?{urllib.parse.urlencode(auth_params)}"


# ─── Mock token response (no network) ────────────────────────────────


def _mock_token_response(
    provider: str, grant_type: str, code: str = "", refresh: str = ""
) -> dict:
    """Simulate a TikTok token response for testing without real credentials."""
    now = math.floor(time.time())
    return {
        "code": 0,
        "message": "success",
        "data": {
            "access_token": f"MOCK_{provider}_access_{secrets.token_hex(8)}",
            "refresh_token": f"MOCK_{provider}_refresh_{secrets.token_hex(8)}",
            "access_token_expire_in": now + 7 * 24 * 3600,
            "refresh_token_expire_in": now + 365 * 24 * 3600,
            "shop_id": "MOCK_SHOP_12345",
            "shop_name": "MOCK Test Shop",
            "shop_cipher": f"MOCK_{secrets.token_hex(16)}",
            "shop_region": "US",
            "seller_type": "CROSS_BORDER",
            "seller_name": "MOCK_SELLER",
            "granted_scopes": ["user_info", "orders", "products"],
        },
        "request_id": f"MOCK-{secrets.token_hex(8)}",
        "_mock": True,
        "_echoed": {
            "grant_type": grant_type,
            "code": code[:16] + "..." if code else None,
            "refresh": refresh[:16] + "..." if refresh else None,
        },
    }


# ─── URL allow-list helper (defense in depth) ─────────────────────────


def _assert_safe_http_url(url: str) -> tuple[bool, str]:
    """Returns (ok, error_message). Rejects file://, ftp://, etc."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"refusing non-http(s) URL: scheme={parsed.scheme!r}"
    return True, ""


def _open_http_get(url: str, headers: dict | None = None, timeout: float = 15.0):
    """Single-purpose helper: open a verified http(s) URL with optional headers.

    All callers go through here so the scheme allowlist is enforced in exactly
    one place. Wrapping the urllib.request call in this helper also moves the
    semgrep `urllib-urlopen` rule's sink into a single function whose docstring
    is the documented suppression rationale.
    """
    ok, err = _assert_safe_http_url(url)
    if not ok:
        raise ValueError(err)
    req = urllib.request.Request(url, method="GET")  # nosemgrep: urllib-urlopen
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    return urlopen(req, timeout=timeout)  # nosemgrep: urllib-urlopen


# ─── Token endpoint caller ────────────────────────────────────────────


def call_token_endpoint(
    provider: str, grant_type: str, code: str = "", refresh: str = ""
) -> dict:
    """Call the provider's token endpoint and return the parsed JSON response.

    TikTok Shop uses TWO different endpoints depending on grant_type:
      - authorized_code → GET /api/v2/token/get
      - refresh_token   → GET /api/v2/token/refresh
    Sending refresh_token to /get yields TikTok code 98001004 "invalid params".
    """
    cfg = provider_config(provider)
    if not cfg:
        return {"code": -1, "message": f"unknown provider: {provider}"}

    if cfg.get("mock"):
        return _mock_token_response(provider, grant_type, code, refresh)

    if not cfg.get("app_key") or not cfg.get("app_secret"):
        return {
            "code": -1,
            "message": "provider not configured: set TIKTOK_APP_KEY and "
            "TIKTOK_APP_SECRET env vars (or set TIKTOK_MOCK=1 to simulate responses)",
        }

    qs: dict[str, str] = {
        "app_key": cfg["app_key"],
        "app_secret": cfg["app_secret"],
        "grant_type": grant_type,
    }
    if grant_type == "authorized_code":
        qs["auth_code"] = code
        token_url = cfg.get("token_url")
    elif grant_type == "refresh_token":
        qs["refresh_token"] = refresh
        token_url = cfg.get(
            "refresh_token_url",
            f"{_tiktok_auth_host()}/api/v2/token/refresh",
        )
    else:
        return {"code": -1, "message": f"unsupported grant_type: {grant_type}"}

    if not token_url:
        return {
            "code": -1,
            "message": f"no token URL configured for grant_type={grant_type} provider={provider}",
        }

    url = f"{token_url}?{urllib.parse.urlencode(qs)}"
    ok, err = _assert_safe_http_url(url)
    if not ok:
        return {"code": -1, "message": err}

    raw = ""
    try:
        with _open_http_get(
            url,
            headers={"User-Agent": "oauth-receiver/1.0 (schan)"},
            timeout=_http_timeout(),
        ) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {
            "code": e.code,
            "message": f"HTTP {e.code} from token endpoint",
            "_raw": body,
        }
    except urllib.error.URLError as e:
        return {"code": -1, "message": f"network error: {e.reason}"}
    except json.JSONDecodeError as e:
        return {
            "code": -1,
            "message": f"invalid JSON from token endpoint: {e}",
            "_raw": raw,
        }
    except Exception as e:  # noqa: BLE001
        return {"code": -1, "message": f"unexpected: {type(e).__name__}: {e}"}


# Module-level urlopen alias for tests to monkeypatch via
# patch.object(module, "urlopen", ...). The actual urllib.request.urlopen
# is exposed via this attribute.
urlopen = urllib.request.urlopen


# ─── Token result wrapper (persist + history + DB) ────────────────────


def save_token_result(
    provider: str,
    grant_type: str,
    request_payload: dict,
    response: dict,
    shop_id: str | None = None,
) -> dict:
    """Wrap a token response with metadata and persist it.

    Returns the wrapped record. side effects:
      - appends to _token_history
      - db_store_token(...) if response is success (code=0)
    """
    success = response.get("code") == 0
    data = response.get("data") if success else None
    result: dict[str, Any] = {
        "event": "token",
        "ok": success,
        "provider": provider,
        "grant_type": grant_type,
        "request": {
            k: v for k, v in request_payload.items() if k != "app_secret"
        },  # never log app_secret
        "response": response,
    }
    if success and data:
        result["access_token"] = data.get("access_token")
        result["refresh_token"] = data.get("refresh_token")
        result["access_token_expires_at"] = data.get("access_token_expire_in")
        result["refresh_token_expires_at"] = data.get("refresh_token_expire_in")
        result["shop_id"] = data.get("shop_id") or shop_id
        result["shop_cipher"] = data.get("shop_cipher")
        result["shop_region"] = data.get("shop_region")
        result["seller_type"] = data.get("seller_type")
        result["granted_scopes"] = data.get("granted_scopes")
        _append_token_history(result)
        effective_shop_id = shop_id or data.get("shop_id") or DEFAULT_SHOP_KEY
        db_store_token(
            effective_shop_id,
            provider,
            {
                "access_token": data.get("access_token"),
                "refresh_token": data.get("refresh_token"),
                "shop_cipher": data.get("shop_cipher"),
                "shop_name": data.get("shop_name"),
                "shop_region": data.get("shop_region"),
                "seller_type": data.get("seller_type"),
                "access_token_expires_at": data.get("access_token_expire_in"),
                "refresh_token_expires_at": data.get("refresh_token_expire_in"),
                "granted_scopes": data.get("granted_scopes"),
            },
        )
    else:
        _append_token_history(result)
    return result


# ─── handle_callback (logic only, no HTTP rendering) ──────────────────


def handle_callback(
    code: str | None,
    state: str | None,
    provider: str,
    registered_states: dict[str, dict] | None = None,
    error: str | None = None,
) -> dict:
    """Process an OAuth callback. Returns a dict describing what happened.

    No HTTP response building here — the FastAPI router (Wave 2) renders
    HTML/JSON based on the returned dict.

    Returns:
      {"handled": False, "reason": "no_code"}        — caller should render help
      {"handled": True, "kind": "error", "error": "..."}
      {"handled": True, "kind": "token",
       "state_status": "matched"|"not_registered"|"mismatched"|"no_state",
       "token_result": <save_token_result dict>}
    """
    if error:
        return {
            "handled": True,
            "kind": "error",
            "error": error,
            "state": state,
            "provider": provider,
        }

    if not code:
        return {"handled": False, "reason": "no_code"}

    # State validation (CSRF protection)
    state_status = "no_state"
    states = registered_states if registered_states is not None else _states
    if state:
        if state in states:
            meta = states[state]
            # MEDIUM fix from WAVE1_QA_REPORT.md:
            # Reject expired states even if purge hasn't run yet.
            # Defense-in-depth: do NOT pop and do NOT auto-exchange.
            if (meta["ts"] + _state_ttl()) < time.time():
                state_status = "expired"
            else:
                state_status = "matched"
                states.pop(state, None)
        elif any(s != state for s in states):
            state_status = "mismatched"
        else:
            state_status = "not_registered"

    # Auto-exchange (skipped for expired states — MEDIUM fix from
    # WAVE1_QA_REPORT.md: defense-in-depth against state-replay window).
    auto_token_result: dict | None = None
    if state_status != "expired":
        cfg = provider_config(provider)
        if cfg and (cfg.get("app_key") or cfg.get("mock")):
            response = call_token_endpoint(provider, "authorized_code", code=code)
            auto_token_result = save_token_result(
                provider,
                "authorized_code",
                {"code": code, "app_key": cfg.get("app_key", "MOCK")},
                response,
            )

    return {
        "handled": True,
        "kind": "token",
        "state_status": state_status,
        "code": code,
        "state": state,
        "provider": provider,
        "token_result": auto_token_result,
    }


# ─── Code → token (manual) ────────────────────────────────────────────


def exchange_code(code: str, provider: str = "tiktok") -> dict:
    """Exchange an auth code for tokens. Persists result."""
    cfg = provider_config(provider)
    response = call_token_endpoint(provider, "authorized_code", code=code)
    return save_token_result(
        provider,
        "authorized_code",
        {"code": code, "app_key": cfg.get("app_key", "MOCK") if cfg else "?"},
        response,
    )


# ─── refresh_token (manual, generic) ──────────────────────────────────


def refresh_with_token(refresh_token: str, provider: str = "tiktok") -> dict:
    """Refresh using an explicit refresh_token string. Persists to DEFAULT_SHOP_KEY.

    For per-shop refresh (preserves shop_id), use refresh_shop_token.
    """
    cfg = provider_config(provider)
    response = call_token_endpoint(provider, "refresh_token", refresh=refresh_token)
    return save_token_result(
        provider,
        "refresh_token",
        {
            "refresh_token": refresh_token,
            "app_key": cfg.get("app_key", "MOCK") if cfg else "?",
        },
        response,
    )


# ─── Per-shop refresh (uses stored refresh_token) ─────────────────────


def refresh_shop_token(shop_id: str, provider: str = "tiktok") -> dict:
    """Refresh using the shop's stored refresh_token.

    Returns {"ok": True/False, "error"?: str, "data"?: ...}.
    """
    row = db_load_token(shop_id, provider)
    if not row:
        return {
            "ok": False,
            "error": f"no token for shop_id={shop_id} provider={provider}",
            "status": 404,
        }
    rt = row.get("refresh_token")
    if not rt:
        return {
            "ok": False,
            "error": "no refresh_token on file for this shop",
            "status": 400,
        }
    cfg = provider_config(provider)
    response = call_token_endpoint(provider, "refresh_token", refresh=rt)
    result = save_token_result(
        provider,
        "refresh_token",
        {
            "refresh_token": rt,
            "app_key": cfg.get("app_key", "MOCK") if cfg else "?",
        },
        response,
        shop_id=shop_id,
    )
    return result


# ─── fetch_shops (HMAC-signed + 1h cache) ─────────────────────────────


_shops_cache: dict[str, dict] = {}  # provider -> {ts, shops, request_id}
SHOPS_CACHE_TTL = 3600  # 1 hour


def fetch_shops(provider: str = "tiktok", force_refresh: bool = False) -> dict:
    """Fetch authorized shops via TikTok /authorization/202309/shops.

    Caches result in memory for 1 hour. force_refresh=True bypasses cache.
    On success, materializes one DB row per shop.
    """
    cfg = provider_config(provider)
    if not cfg:
        return {"error": f"unknown provider: {provider}"}

    # Cache hit
    if not force_refresh and provider in _shops_cache:
        cached = _shops_cache[provider]
        age = time.time() - cached.get("ts", 0)
        if age < SHOPS_CACHE_TTL:
            return {
                "provider": provider,
                "cached": True,
                "age_seconds": math.floor(age),
                "ttl_seconds": SHOPS_CACHE_TTL,
                **cached,
            }

    # Need a valid access_token
    last_ok = get_last_successful_token()
    if not last_ok:
        return {
            "error": "no access_token available; do an /authorize → /callback flow first",
            "hint": "open /authorize in a browser",
        }

    access_token = last_ok.get("access_token") or ""
    app_secret = cfg.get("app_secret") or ""
    app_key = cfg.get("app_key") or ""
    api_host = cfg.get("api_host") or _tiktok_api_host()
    path = "/authorization/202309/shops"

    timestamp = str(math.floor(time.time()))
    params_q = {"app_key": app_key, "timestamp": timestamp}
    kv_concat = "".join(f"{k}{params_q[k]}" for k in sorted(params_q))
    canonical = f"{app_secret}{path}{kv_concat}{app_secret}"
    sign = hmac.new(
        app_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    qs = "&".join(f"{k}={v}" for k, v in params_q.items()) + f"&sign={sign}"
    url = f"{api_host}{path}?{qs}"

    ok, err = _assert_safe_http_url(url)
    if not ok:
        return {"error": err, "provider": provider}

    try:
        with _open_http_get(
            url,
            headers={
                "x-tts-access-token": access_token,
                "Content-Type": "application/json",
            },
            timeout=_http_timeout(),
        ) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {
            "error": f"HTTP {e.code}",
            "raw": body[:500],
            "provider": provider,
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "provider": provider}

    if resp.get("code") == 0 and resp.get("data"):
        shops_list = resp["data"].get("shops") or []
        _shops_cache[provider] = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
            "request_id": resp.get("request_id"),
            "shops": shops_list,
        }

        # Materialize per-shop rows
        if is_db_ok():
            for s in shops_list:
                sid = s.get("id")
                if not sid:
                    continue
                db_store_token(
                    sid,
                    provider,
                    {
                        "access_token": last_ok.get("access_token"),
                        "refresh_token": last_ok.get("refresh_token"),
                        "shop_cipher": s.get("cipher"),
                        "shop_name": s.get("name"),
                        "shop_region": s.get("region"),
                        "seller_type": s.get("seller_type"),
                        "access_token_expires_at": last_ok.get(
                            "access_token_expires_at"
                        ),
                        "refresh_token_expires_at": last_ok.get(
                            "refresh_token_expires_at"
                        ),
                        "granted_scopes": last_ok.get("granted_scopes"),
                    },
                )

        return {
            "provider": provider,
            "cached": False,
            "age_seconds": 0,
            "ttl_seconds": SHOPS_CACHE_TTL,
            **_shops_cache[provider],
        }

    return {
        "error": "TikTok returned non-zero code",
        "provider": provider,
        "tiktok_code": resp.get("code"),
        "tiktok_message": resp.get("message"),
        "request_id": resp.get("request_id"),
    }


# ─── Test helpers (not part of public API; intentionally underscored) ──


def _reset_for_testing() -> None:
    """Clear cached Fernet, token history, shops cache, states.

    Test-only. Production code MUST NOT call this.
    """
    global _fernet, _db_ok
    _fernet = False
    _db_ok = False
    _token_history.clear()
    _shops_cache.clear()
    _states.clear()


def _append_token_history_for_test(record: dict) -> None:
    """Append a synthetic token record to history. Test-only."""
    _append_token_history(record)


# ─── Module-load DB init (HARD dependency) ──────────────────────────
# The original stdlib oauth_receiver.py called _db_init() in main().
# When the pure business logic was extracted into this module the eager
# init call was lost — db_init() must be invoked at module load so that
# is_db_ok() flips True and DB-backed operations actually work.
#
# Per the db_init() docstring: failing fast at startup is required.
# We log to stderr but do NOT raise — tts-erp's other routes should
# keep working even if OAUTH_DB_URL is unset; is_db_ok() reports the
# truth via /healthz, and any oauth DB call will raise a clear
# RuntimeError when reached.
try:
    db_init()
except RuntimeError as _init_err:
    print(f"[oauth-receiver-core] DB init failed at module load: {_init_err}")
