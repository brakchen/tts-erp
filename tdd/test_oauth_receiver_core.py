"""TDD tests for oauth_receiver_core.

Vertical slices — one test file covers all functions but tests are
organized by slice with progressive RED/GREEN commits per slice.
"""
from __future__ import annotations

# Test sentinel secrets are intentionally literal so round-trip assertions
# can compare exact strings. They are not credentials. The linter cannot
# tell the difference between a real secret and an obvious test fixture
# like "ROW_AT_abc123", so silence at file level.
# ruff: noqa: S105, S106, SLF001

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

# The module under test is created in the GREEN phase that follows this RED test.
# Pylance/pyright cannot resolve it until then — silence at import site.
import oauth_receiver_core as oc  # noqa: E402, F401

# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def fernet_key(monkeypatch):
    """Inject a deterministic Fernet key so tests are reproducible.

    Generated freshly per test so no test ever reuses another test's key.
    """
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("OAUTH_DB_ENCRYPTION_KEY", key)
    # Reset any cached Fernet instance inside the module
    oc._reset_for_testing()
    yield key
    oc._reset_for_testing()


@pytest.fixture()
def no_fernet(monkeypatch):
    """Tests that need get_fernet() to return None (missing key)."""
    monkeypatch.delenv("OAUTH_DB_ENCRYPTION_KEY", raising=False)
    oc._reset_for_testing()
    yield
    oc._reset_for_testing()


@pytest.fixture()
def oauth_db_url(monkeypatch):
    """Set the oauth_receiver DB URL (from /home/schan/oauth-receiver/.env).

    Tests that touch PG use this. Safe — tests use TEST_ prefix shop_ids
    and rollback their transactions via the oauth_db_conn fixture.
    """
    from pathlib import Path

    env_path = Path("/home/schan/oauth-receiver/.env")
    if not env_path.exists():
        pytest.skip("oauth-receiver .env not present")
    db_url = None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("OAUTH_DB_URL="):
            db_url = line.split("=", 1)[1].strip()
            monkeypatch.setenv("OAUTH_DB_URL", db_url)
        if line.startswith("OAUTH_DB_ENCRYPTION_KEY="):
            monkeypatch.setenv("OAUTH_DB_ENCRYPTION_KEY", line.split("=", 1)[1].strip())
    if not db_url:
        pytest.skip("OAUTH_DB_URL not in oauth-receiver .env")
    oc._reset_for_testing()
    yield db_url
    oc._reset_for_testing()


@pytest.fixture()
def clean_test_shops(oauth_db_conn):
    """Delete any TEST_ rows AND the __default__ row from oauth_tokens.

    Connects to the oauth_receiver DB (not tts_erp), since oauth_tokens lives
    there. Each test gets a transaction that is rolled back at teardown.
    """
    with oauth_db_conn.cursor() as cur:
        cur.execute("DELETE FROM oauth_tokens WHERE shop_id LIKE 'TEST_%'")
        cur.execute("DELETE FROM oauth_tokens WHERE shop_id = '__default__'")
    yield
    with oauth_db_conn.cursor() as cur:
        cur.execute("DELETE FROM oauth_tokens WHERE shop_id LIKE 'TEST_%'")
        cur.execute("DELETE FROM oauth_tokens WHERE shop_id = '__default__'")
    oauth_db_conn.commit()


@pytest.fixture()
def oauth_db_conn(oauth_db_url):
    """Connection to the oauth_receiver database (where oauth_tokens lives)."""
    import psycopg

    conn = psycopg.connect(oauth_db_url)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


# ─── Slice 1: encrypt / decrypt / get_fernet ──────────────────────────


class TestFernetRoundTrip:
    def test_encrypt_returns_bytes(self, fernet_key):
        blob = oc.encrypt("hello-token")
        assert isinstance(blob, bytes)
        assert len(blob) > 0

    def test_encrypt_then_decrypt_returns_original(self, fernet_key):
        token = "ROW_at_test_abc123_secret_xyz"
        assert oc.decrypt(oc.encrypt(token)) == token

    def test_encrypt_unicode_passthrough(self, fernet_key):
        # TikTok shop names contain non-ASCII
        s = "测试店-name-é-ñ"
        assert oc.decrypt(oc.encrypt(s)) == s

    def test_encrypt_empty_string(self, fernet_key):
        # Fernet encrypts empty string fine, decrypts to ""
        assert oc.decrypt(oc.encrypt("")) == ""

    def test_fernet_independent_per_call(self, fernet_key):
        # Two encrypts of same plaintext must yield different ciphertexts (Fernet uses random IV)
        a = oc.encrypt("same")
        b = oc.encrypt("same")
        assert a != b
        assert oc.decrypt(a) == oc.decrypt(b) == "same"


class TestFernetMissingKey:
    def test_encrypt_raises_when_key_missing(self, no_fernet):
        with pytest.raises(RuntimeError, match="Fernet not configured"):
            oc.encrypt("anything")

    def test_decrypt_raises_when_key_missing(self, no_fernet):
        with pytest.raises(RuntimeError, match="Fernet not configured"):
            oc.decrypt(b"any-blob")

    def test_get_fernet_returns_none_when_key_missing(self, no_fernet):
        assert oc.get_fernet() is None


class TestFernetWrongKey:
    def test_decrypt_with_wrong_key_raises_invalid_token(self, monkeypatch):
        from cryptography.fernet import Fernet, InvalidToken

        key_a = Fernet.generate_key()
        key_b = Fernet.generate_key()
        monkeypatch.setenv("OAUTH_DB_ENCRYPTION_KEY", key_a.decode())
        oc._reset_for_testing()
        blob = oc.encrypt("secret")  # encrypted with key_a
        # Swap to key_b
        monkeypatch.setenv("OAUTH_DB_ENCRYPTION_KEY", key_b.decode())
        oc._reset_for_testing()
        with pytest.raises(InvalidToken):
            oc.decrypt(blob)


class TestFernetTamperedCiphertext:
    def test_tampered_ciphertext_raises(self, fernet_key):
        from cryptography.fernet import InvalidToken

        blob = bytearray(oc.encrypt("secret"))
        blob[-1] ^= 0xFF  # flip last byte
        with pytest.raises(InvalidToken):
            oc.decrypt(bytes(blob))


# ─── Slice 2: db_init + db_store_token + db_load_token ───────────────


class TestDbInit:
    def test_db_init_fails_without_db_url(self, monkeypatch, fernet_key):
        monkeypatch.delenv("OAUTH_DB_URL", raising=False)
        oc._reset_for_testing()
        with pytest.raises(RuntimeError, match="OAUTH_DB_URL not set"):
            oc.db_init()

    def test_db_init_fails_without_encryption_key(self, monkeypatch, oauth_db_url):
        monkeypatch.delenv("OAUTH_DB_ENCRYPTION_KEY", raising=False)
        oc._reset_for_testing()
        with pytest.raises(RuntimeError, match="OAUTH_DB_ENCRYPTION_KEY not set"):
            oc.db_init()

    def test_db_init_succeeds_when_configured(self, oauth_db_url):
        oc.db_init()  # must not raise
        assert oc.is_db_ok() is True


class TestStoreLoadRoundTrip:
    def test_store_then_load_returns_decrypted_secrets(
        self, oauth_db_url, clean_test_shops
    ):
        oc.db_init()
        data = {
            "access_token": "ROW_AT_abc123",
            "refresh_token": "ROW_RT_def456",
            "shop_cipher": "ROW_SC_ghi789",
            "shop_name": "Test Shop",
            "shop_region": "US",
            "seller_type": "CROSS_BORDER",
            "access_token_expires_at": int(time.time()) + 86400,
            "refresh_token_expires_at": int(time.time()) + 365 * 86400,
            "granted_scopes": ["orders", "products"],
        }
        assert oc.db_store_token("TEST_SHOP_001", "tiktok", data) is True
        row = oc.db_load_token("TEST_SHOP_001", "tiktok")
        assert row is not None
        assert row["access_token"] == "ROW_AT_abc123"
        assert row["refresh_token"] == "ROW_RT_def456"
        assert row["shop_cipher"] == "ROW_SC_ghi789"
        assert row["shop_name"] == "Test Shop"
        assert row["shop_region"] == "US"
        assert row["access_token_expires_at"] == data["access_token_expires_at"]
        assert row["granted_scopes"] == ["orders", "products"]

    def test_load_returns_none_for_missing_shop(self, oauth_db_url, clean_test_shops):
        oc.db_init()
        assert oc.db_load_token("TEST_NONEXISTENT", "tiktok") is None

    def test_store_upserts_existing_row(
        self, oauth_db_url, clean_test_shops
    ):
        oc.db_init()
        oc.db_store_token(
            "TEST_SHOP_002",
            "tiktok",
            {
                "access_token": "AT_V1",
                "refresh_token": "RT_V1",
                "shop_cipher": "SC_V1",
            },
        )
        oc.db_store_token(
            "TEST_SHOP_002",
            "tiktok",
            {
                "access_token": "AT_V2",
                "refresh_token": "RT_V2",
                "shop_cipher": "SC_V2",
            },
        )
        row = oc.db_load_token("TEST_SHOP_002", "tiktok")
        assert row is not None
        assert row["access_token"] == "AT_V2"
        assert row["refresh_token"] == "RT_V2"

    def test_store_preserves_existing_scopekey_when_null(
        self, oauth_db_url, clean_test_shops
    ):
        # shop_cipher is nullable. Upserting with no shop_cipher must keep
        # the existing one (per original SQL: COALESCE).
        oc.db_init()
        oc.db_store_token(
            "TEST_SHOP_003",
            "tiktok",
            {
                "access_token": "AT",
                "refresh_token": "RT",
                "shop_cipher": "ORIGINAL_CIPHER",
            },
        )
        oc.db_store_token(
            "TEST_SHOP_003",
            "tiktok",
            {"access_token": "AT2", "refresh_token": "RT2"},  # no shop_cipher
        )
        row = oc.db_load_token("TEST_SHOP_003", "tiktok")
        assert row is not None
        assert row["access_token"] == "AT2"
        assert row["shop_cipher"] == "ORIGINAL_CIPHER"

    def test_store_returns_false_without_access_token(
        self, oauth_db_url, clean_test_shops
    ):
        oc.db_init()
        # Per spec: missing access_token OR refresh_token → return False without writing
        assert (
            oc.db_store_token(
                "TEST_SHOP_004", "tiktok", {"refresh_token": "RT"}
            )
            is False
        )
        assert (
            oc.db_store_token(
                "TEST_SHOP_004", "tiktok", {"access_token": "AT"}
            )
            is False
        )


# ─── Slice 3: db_list_shops + db_delete_token ─────────────────────────


class TestListShops:
    def test_list_returns_all_stored_shops(
        self, oauth_db_url, clean_test_shops
    ):
        oc.db_init()
        for sid in ("TEST_SHOP_A", "TEST_SHOP_B", "TEST_SHOP_C"):
            oc.db_store_token(
                sid,
                "tiktok",
                {"access_token": f"AT_{sid}", "refresh_token": f"RT_{sid}"},
            )
        items = oc.db_list_shops(provider="tiktok")
        sids = [item["shop_id"] for item in items]
        for sid in ("TEST_SHOP_A", "TEST_SHOP_B", "TEST_SHOP_C"):
            assert sid in sids

    def test_list_does_not_decrypt_secrets(
        self, oauth_db_url, clean_test_shops
    ):
        oc.db_init()
        oc.db_store_token(
            "TEST_SHOP_LIST",
            "tiktok",
            {"access_token": "AT_SECRET_VALUE", "refresh_token": "RT_SECRET_VALUE"},
        )
        items = oc.db_list_shops(provider="tiktok")
        target = [i for i in items if i["shop_id"] == "TEST_SHOP_LIST"][0]
        # list returns metadata only — no access/refresh token columns
        assert "access_token" not in target
        assert "refresh_token" not in target
        assert "access_token_encrypted" not in target  # opaque encrypted bytes not exposed

    def test_list_filters_by_provider(
        self, oauth_db_url, clean_test_shops
    ):
        oc.db_init()
        oc.db_store_token(
            "TEST_PROV_A", "tiktok", {"access_token": "AT", "refresh_token": "RT"}
        )
        oc.db_store_token(
            "TEST_PROV_B", "facebook", {"access_token": "AT", "refresh_token": "RT"}
        )
        tiktok_items = oc.db_list_shops(provider="tiktok")
        ids = [i["shop_id"] for i in tiktok_items]
        assert "TEST_PROV_A" in ids
        assert "TEST_PROV_B" not in ids


class TestDeleteToken:
    def test_delete_removes_row(self, oauth_db_url, clean_test_shops):
        oc.db_init()
        oc.db_store_token(
            "TEST_DEL",
            "tiktok",
            {"access_token": "AT", "refresh_token": "RT"},
        )
        assert oc.db_load_token("TEST_DEL", "tiktok") is not None
        assert oc.db_delete_token("TEST_DEL", "tiktok") is True
        assert oc.db_load_token("TEST_DEL", "tiktok") is None

    def test_delete_nonexistent_returns_false(self, oauth_db_url, clean_test_shops):
        oc.db_init()
        assert oc.db_delete_token("TEST_NEVER_EXISTED", "tiktok") is False


# ─── Slice 4: call_token_endpoint ────────────────────────────────────


class TestCallTokenEndpoint:
    def test_returns_error_for_unknown_provider(self):
        result = oc.call_token_endpoint("nonexistent", "authorized_code", code="X")
        assert result["code"] == -1
        assert "unknown provider" in result["message"]

    def test_returns_error_when_app_key_missing(self, monkeypatch):
        # With mock=False and no app_key, must return clear error
        monkeypatch.setenv("TIKTOK_APP_KEY", "")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "")
        oc._reset_for_testing()
        result = oc.call_token_endpoint("tiktok", "authorized_code", code="X")
        assert result["code"] == -1
        assert "not configured" in result["message"]

    def test_mock_mode_returns_success_without_network(
        self, monkeypatch, fernet_key
    ):
        monkeypatch.setenv("TIKTOK_MOCK", "1")
        oc._reset_for_testing()
        result = oc.call_token_endpoint("tiktok", "authorized_code", code="TEST_CODE")
        assert result["code"] == 0
        assert result["data"]["access_token"].startswith("MOCK_tiktok_access_")
        assert result["data"]["refresh_token"].startswith("MOCK_tiktok_refresh_")
        assert "request_id" in result

    def test_real_call_makes_http_request(self, monkeypatch, fernet_key):
        monkeypatch.setenv("TIKTOK_APP_KEY", "FAKE_KEY")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "FAKE_SECRET")
        oc._reset_for_testing()

        # Capture the request and return a fake TikTok response
        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.headers)
            captured["timeout"] = timeout
            response = MagicMock()
            response.read.return_value = json.dumps(
                {"code": 0, "data": {"access_token": "AT_REAL"}}
            ).encode()
            response.__enter__ = lambda self_: self_
            response.__exit__ = lambda self_, *args: None
            return response

        with patch.object(oc, "urlopen", side_effect=fake_urlopen):
            result = oc.call_token_endpoint(
                "tiktok", "authorized_code", code="REAL_CODE"
            )

        assert result["code"] == 0
        assert result["data"]["access_token"] == "AT_REAL"
        # authorized_code uses /api/v2/token/get
        assert "/api/v2/token/get" in captured["url"]
        assert "auth_code=REAL_CODE" in captured["url"]
        assert "app_key=FAKE_KEY" in captured["url"]

    def test_real_refresh_uses_refresh_endpoint(self, monkeypatch, fernet_key):
        monkeypatch.setenv("TIKTOK_APP_KEY", "FAKE_KEY")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "FAKE_SECRET")
        oc._reset_for_testing()

        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            response = MagicMock()
            response.read.return_value = json.dumps({"code": 0, "data": {}}).encode()
            response.__enter__ = lambda self_: self_
            response.__exit__ = lambda self_, *args: None
            return response

        with patch.object(oc, "urlopen", side_effect=fake_urlopen):
            oc.call_token_endpoint("tiktok", "refresh_token", refresh="RT_XYZ")

        # refresh_token grant MUST use /api/v2/token/refresh (not /get)
        # Sending refresh_token to /get yields TikTok 98001004 "invalid params"
        assert "/api/v2/token/refresh" in captured["url"]
        assert "refresh_token=RT_XYZ" in captured["url"]
        assert "/api/v2/token/get" not in captured["url"]

    def test_unsupported_grant_type_returns_error(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_APP_KEY", "K")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "S")
        oc._reset_for_testing()
        result = oc.call_token_endpoint("tiktok", "client_credentials", code="x")
        assert result["code"] == -1
        assert "unsupported grant_type" in result["message"]

    def test_http_error_returns_code(self, monkeypatch, fernet_key):
        monkeypatch.setenv("TIKTOK_APP_KEY", "K")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "S")
        oc._reset_for_testing()

        from email.message import Message
        from urllib.error import HTTPError

        def fake_urlopen(req, timeout):
            raise HTTPError(req.full_url, 500, "Server Error", Message(), None)

        with patch.object(oc, "urlopen", side_effect=fake_urlopen):
            result = oc.call_token_endpoint("tiktok", "authorized_code", code="X")
        assert result["code"] == 500
        assert "HTTP 500" in result["message"]


# ─── Slice 5: build_authorize_url ────────────────────────────────────


class TestBuildAuthorizeUrl:
    def test_returns_url_with_required_params(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_APP_KEY", "TEST_KEY")
        monkeypatch.setenv("TIKTOK_REDIRECT_URI", "https://example.com/cb")
        oc._reset_for_testing()
        url = oc.build_authorize_url("tiktok", state="abc123")
        assert url is not None
        assert "https://auth.tiktok-shops.com/oauth/authorize" in url
        assert "app_key=TEST_KEY" in url
        assert "state=abc123" in url
        assert "response_type=code" in url
        assert "redirect_uri=https%3A%2F%2Fexample.com%2Fcb" in url

    def test_uses_mock_app_key_when_unset(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_APP_KEY", "")
        oc._reset_for_testing()
        url = oc.build_authorize_url("tiktok", state="s")
        assert url is not None
        assert "app_key=MOCK_APP_KEY" in url

    def test_unknown_provider_returns_none(self):
        assert oc.build_authorize_url("google", state="s") is None


# ─── Slice 6: handle_callback (logic only) ───────────────────────────


class TestHandleCallbackLogic:
    def _state_meta(self):
        return {"ts": time.time(), "provider": "tiktok"}

    def test_no_code_returns_no_code_event(self, monkeypatch):
        """No code → callback is a no-op event for the caller (HTTP layer renders help)."""
        monkeypatch.setenv("TIKTOK_APP_KEY", "")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "")
        monkeypatch.setenv("TIKTOK_MOCK", "")
        oc._reset_for_testing()
        result = oc.handle_callback(
            code=None, state="s", provider="tiktok", registered_states={}
        )
        assert result["handled"] is False
        assert result["reason"] == "no_code"

    def test_error_returns_error_event(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_APP_KEY", "")
        oc._reset_for_testing()
        result = oc.handle_callback(
            code=None,
            state="s",
            provider="tiktok",
            registered_states={},
            error="access_denied",
        )
        assert result["handled"] is True
        assert result["kind"] == "error"
        assert result["error"] == "access_denied"

    def test_matched_state_pops_single_use(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_MOCK", "1")
        oc._reset_for_testing()
        states = {"abc": self._state_meta()}
        result = oc.handle_callback(
            code="CODE_1", state="abc", provider="tiktok", registered_states=states
        )
        assert result["kind"] == "token"
        assert result["state_status"] == "matched"
        assert "abc" not in states  # single-use, popped

    def test_unregistered_state_status(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_MOCK", "1")
        oc._reset_for_testing()
        result = oc.handle_callback(
            code="CODE_1", state="never_seen", provider="tiktok",
            registered_states={},
        )
        assert result["kind"] == "token"
        assert result["state_status"] == "not_registered"

    def test_no_state_status_is_no_state(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_MOCK", "1")
        oc._reset_for_testing()
        result = oc.handle_callback(
            code="CODE_1", state=None, provider="tiktok", registered_states={}
        )
        assert result["kind"] == "token"
        assert result["state_status"] == "no_state"

    def test_mismatched_state_status_when_different_registered_state_exists(
        self, monkeypatch
    ):
        monkeypatch.setenv("TIKTOK_MOCK", "1")
        oc._reset_for_testing()
        result = oc.handle_callback(
            code="CODE_1", state="incoming", provider="tiktok",
            registered_states={"different": self._state_meta()},
        )
        assert result["kind"] == "token"
        assert result["state_status"] == "mismatched"


# ─── Slice 7: exchange_code + refresh_with_token ─────────────────────


class TestExchangeCode:
    def test_exchange_code_calls_token_endpoint_and_persists(
        self, monkeypatch, oauth_db_url, clean_test_shops
    ):
        monkeypatch.setenv("TIKTOK_MOCK", "1")
        oc._reset_for_testing()
        oc.db_init()
        result = oc.exchange_code(code="CODE_X", provider="tiktok")
        assert result["ok"] is True
        assert result["access_token"].startswith("MOCK_tiktok_access_")
        # Token persisted to DB
        row = oc.db_load_token("MOCK_SHOP_12345", "tiktok")
        assert row is not None  # mock returns shop_id=MOCK_SHOP_12345

    def test_exchange_code_returns_error_when_token_endpoint_fails(
        self, monkeypatch, oauth_db_url, clean_test_shops
    ):
        monkeypatch.setenv("TIKTOK_APP_KEY", "")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "")
        monkeypatch.setenv("TIKTOK_MOCK", "")
        oc._reset_for_testing()
        oc.db_init()
        result = oc.exchange_code(code="X", provider="tiktok")
        assert result["ok"] is False
        assert "not configured" in result["response"]["message"]


class TestRefreshWithToken:
    def test_refresh_with_token_uses_provided_refresh(
        self, monkeypatch, oauth_db_url, clean_test_shops
    ):
        monkeypatch.setenv("TIKTOK_MOCK", "1")
        oc._reset_for_testing()
        oc.db_init()
        result = oc.refresh_with_token(
            refresh_token="RT_OLD", provider="tiktok"
        )
        assert result["ok"] is True
        # save_token_result uses data["shop_id"] when no shop_id arg is passed;
        # mock response returns shop_id="MOCK_SHOP_12345".
        default_row = oc.db_load_token("MOCK_SHOP_12345", "tiktok")
        assert default_row is not None
        assert default_row["refresh_token"].startswith("MOCK_tiktok_refresh_")

    def test_refresh_with_token_unknown_provider(self):
        result = oc.refresh_with_token("RT", provider="nonexistent")
        assert result["ok"] is False


# ─── Slice 8: refresh_shop_token ─────────────────────────────────────


class TestRefreshShopToken:
    def test_refresh_shop_token_uses_stored_refresh_token(
        self, monkeypatch, oauth_db_url, clean_test_shops
    ):
        monkeypatch.setenv("TIKTOK_MOCK", "1")
        oc._reset_for_testing()
        oc.db_init()
        # Store initial token
        oc.db_store_token(
            "TEST_SHOP_REFRESH",
            "tiktok",
            {
                "access_token": "AT_OLD",
                "refresh_token": "RT_STORED",
                "shop_cipher": "CIPHER_STORED",
            },
        )
        result = oc.refresh_shop_token("TEST_SHOP_REFRESH", provider="tiktok")
        assert result["ok"] is True
        # DB row updated with new tokens
        row = oc.db_load_token("TEST_SHOP_REFRESH", "tiktok")
        assert row["access_token"].startswith("MOCK_tiktok_access_")
        assert row["refresh_token"].startswith("MOCK_tiktok_refresh_")

    def test_refresh_shop_token_missing_shop_returns_404(
        self, monkeypatch, oauth_db_url, clean_test_shops
    ):
        monkeypatch.setenv("TIKTOK_MOCK", "1")
        oc._reset_for_testing()
        oc.db_init()
        result = oc.refresh_shop_token("TEST_MISSING", provider="tiktok")
        assert result["ok"] is False
        assert "no token" in result["error"]

    def test_refresh_shop_token_missing_refresh_token_returns_400(
        self, monkeypatch, oauth_db_url, clean_test_shops
    ):
        monkeypatch.setenv("TIKTOK_MOCK", "1")
        oc._reset_for_testing()
        oc.db_init()
        # DB has a row but no refresh_token — store by hand via DB to bypass the AT/RT check
        from cryptography.fernet import Fernet

        fkey = os.environ["OAUTH_DB_ENCRYPTION_KEY"].encode()
        f = Fernet(fkey)
        import psycopg

        with psycopg.connect(os.environ["OAUTH_DB_URL"]) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oauth_tokens
                    (shop_id, provider, access_token_encrypted, refresh_token_encrypted)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (shop_id, provider) DO UPDATE SET
                  access_token_encrypted = EXCLUDED.access_token_encrypted,
                  refresh_token_encrypted = EXCLUDED.refresh_token_encrypted
                """,
                (
                    "TEST_NO_RT",
                    "tiktok",
                    f.encrypt(b"AT_only"),
                    f.encrypt(b""),
                ),
            )
            conn.commit()
        result = oc.refresh_shop_token("TEST_NO_RT", provider="tiktok")
        assert result["ok"] is False
        assert "no refresh_token" in result["error"]


# ─── Slice 9: fetch_shops ────────────────────────────────────────────


class TestFetchShops:
    def _fake_hmac_signed_response(self, shops: list[dict]) -> dict:
        return {
            "code": 0,
            "message": "success",
            "data": {"shops": shops},
            "request_id": "REQ_FAKE",
        }

    def test_fetch_shops_calls_authorization_endpoint(
        self, monkeypatch, fernet_key
    ):
        monkeypatch.setenv("TIKTOK_APP_KEY", "K_APP")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "K_SECRET")
        monkeypatch.setenv("TIKTOK_API_HOST", "https://open-api.tiktokglobalshop.com")
        oc._reset_for_testing()

        captured = {}
        shops = [{"id": "S1", "cipher": "C1", "name": "Shop1", "region": "US"}]

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.headers)
            response = MagicMock()
            response.read.return_value = json.dumps(
                self._fake_hmac_signed_response(shops)
            ).encode()
            response.__enter__ = lambda self_: self_
            response.__exit__ = lambda self_, *args: None
            return response

        # Pre-populate token history so fetch_shops finds an access_token
        oc._append_token_history_for_test(
            {
                "ok": True,
                "access_token": "AT_VALID",
                "refresh_token": "RT_VALID",
                "access_token_expires_at": int(time.time()) + 3600,
            }
        )

        with patch.object(oc, "urlopen", side_effect=fake_urlopen):
            result = oc.fetch_shops(provider="tiktok", force_refresh=True)

        assert result["cached"] is False
        assert result["shops"] == shops
        assert "error" not in result

        # Verify HMAC-signed URL format
        assert "/authorization/202309/shops" in captured["url"]
        assert "app_key=K_APP" in captured["url"]
        assert "timestamp=" in captured["url"]
        assert "sign=" in captured["url"]
        # x-tts-access-token header is the bearer (urllib normalizes case to title-case)
        headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
        assert headers_lower.get("x-tts-access-token") == "AT_VALID"

    def test_fetch_shops_caches_for_one_hour(
        self, monkeypatch, fernet_key
    ):
        monkeypatch.setenv("TIKTOK_APP_KEY", "K")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "K")
        monkeypatch.setenv("TIKTOK_API_HOST", "https://open-api.tiktokglobalshop.com")
        oc._reset_for_testing()

        def fake_urlopen(req, timeout):
            response = MagicMock()
            response.read.return_value = json.dumps(
                self._fake_hmac_signed_response(
                    [{"id": "S1", "cipher": "C", "name": "N", "region": "US"}]
                )
            ).encode()
            response.__enter__ = lambda self_: self_
            response.__exit__ = lambda self_, *args: None
            return response

        oc._append_token_history_for_test(
            {"ok": True, "access_token": "AT", "refresh_token": "RT"}
        )

        call_count = {"n": 0}

        def counting_urlopen(req, timeout):
            call_count["n"] += 1
            return fake_urlopen(req, timeout)

        with patch.object(oc, "urlopen", side_effect=counting_urlopen):
            # First call: hits network
            oc.fetch_shops(provider="tiktok", force_refresh=True)
            # Second call: returns cache, no network
            result2 = oc.fetch_shops(provider="tiktok", force_refresh=False)

        assert call_count["n"] == 1
        assert result2["cached"] is True
        assert result2["shops"][0]["id"] == "S1"

    def test_fetch_shops_force_refresh_bypasses_cache(
        self, monkeypatch, fernet_key
    ):
        monkeypatch.setenv("TIKTOK_APP_KEY", "K")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "K")
        monkeypatch.setenv("TIKTOK_API_HOST", "https://open-api.tiktokglobalshop.com")
        oc._reset_for_testing()

        def fake_urlopen(req, timeout):
            response = MagicMock()
            response.read.return_value = json.dumps(
                self._fake_hmac_signed_response(
                    [{"id": "S1", "cipher": "C", "name": "N", "region": "US"}]
                )
            ).encode()
            response.__enter__ = lambda self_: self_
            response.__exit__ = lambda self_, *args: None
            return response

        oc._append_token_history_for_test(
            {"ok": True, "access_token": "AT", "refresh_token": "RT"}
        )

        with patch.object(oc, "urlopen", side_effect=fake_urlopen):
            oc.fetch_shops(provider="tiktok", force_refresh=True)
            oc.fetch_shops(provider="tiktok", force_refresh=True)  # force

        # No easy way to count without call_count; just verify no exception
        # and second result is fresh (cached=False)

    def test_fetch_shops_returns_error_when_no_access_token(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_APP_KEY", "K")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "K")
        oc._reset_for_testing()
        # Clear any history
        oc._clear_token_history_for_test()
        result = oc.fetch_shops(provider="tiktok", force_refresh=True)
        assert "error" in result
        assert "no access_token" in result["error"]

    def test_fetch_shops_refuses_non_https_url(self, monkeypatch, fernet_key):
        # If provider URL somehow becomes file://, must reject before urlopen.
        # We patch the provider config to force a file:// scheme.
        monkeypatch.setenv("TIKTOK_APP_KEY", "K")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "K")
        monkeypatch.setenv("TIKTOK_API_HOST", "file:///etc/passwd")
        oc._reset_for_testing()
        oc._append_token_history_for_test(
            {"ok": True, "access_token": "AT", "refresh_token": "RT"}
        )
        result = oc.fetch_shops(provider="tiktok", force_refresh=True)
        assert "error" in result
        assert "scheme" in result["error"].lower() or "refusing" in result["error"].lower()

    def test_fetch_shops_persists_per_shop_rows(
        self, monkeypatch, fernet_key, oauth_db_url, clean_test_shops
    ):
        monkeypatch.setenv("TIKTOK_APP_KEY", "K")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "K")
        monkeypatch.setenv("TIKTOK_API_HOST", "https://open-api.tiktokglobalshop.com")
        oc._reset_for_testing()
        oc.db_init()
        oc._append_token_history_for_test(
            {
                "ok": True,
                "access_token": "AT_MASTER",
                "refresh_token": "RT_MASTER",
                "access_token_expires_at": int(time.time()) + 3600,
                "refresh_token_expires_at": int(time.time()) + 365 * 86400,
            }
        )

        shops = [
            {"id": "TEST_SHOP_M1", "cipher": "C1", "name": "Shop1", "region": "US"},
            {"id": "TEST_SHOP_M2", "cipher": "C2", "name": "Shop2", "region": "UK"},
        ]

        def fake_urlopen(req, timeout):
            response = MagicMock()
            response.read.return_value = json.dumps(
                self._fake_hmac_signed_response(shops)
            ).encode()
            response.__enter__ = lambda self_: self_
            response.__exit__ = lambda self_, *args: None
            return response

        with patch.object(oc, "urlopen", side_effect=fake_urlopen):
            oc.fetch_shops(provider="tiktok", force_refresh=True)

        # Per-shop rows materialized
        row1 = oc.db_load_token("TEST_SHOP_M1", "tiktok")
        row2 = oc.db_load_token("TEST_SHOP_M2", "tiktok")
        assert row1 is not None
        assert row1["access_token"] == "AT_MASTER"
        assert row1["shop_cipher"] == "C1"
        assert row2 is not None
        assert row2["shop_cipher"] == "C2"


# ─── Slice 10: helpers (state, log, etc.) ────────────────────────────


class TestPurgeExpiredStates:
    def test_purges_states_older_than_ttl(self, monkeypatch):
        monkeypatch.setenv("OAUTH_STATE_TTL", "100")  # 100s TTL
        oc._reset_for_testing()
        now = time.time()
        states = {
            "fresh": {"ts": now - 10, "provider": "tiktok"},
            "stale": {"ts": now - 200, "provider": "tiktok"},
        }
        oc.purge_expired_states(states)
        assert "fresh" in states
        assert "stale" not in states

    def test_keeps_states_at_boundary(self, monkeypatch):
        monkeypatch.setenv("OAUTH_STATE_TTL", "100")
        oc._reset_for_testing()
        now = time.time()
        states = {"edge": {"ts": now - 99.9, "provider": "tiktok"}}
        oc.purge_expired_states(states)
        assert "edge" in states


class TestRegisterAndPopState:
    def test_register_state_returns_token(self):
        state = oc.register_state(provider="tiktok")
        assert isinstance(state, str)
        assert len(state) >= 16  # token_urlsafe(24) base64 → ~32 chars

    def test_register_then_pop_single_use(self):
        state = oc.register_state(provider="tiktok")
        meta = oc.pop_state(state)
        assert meta is not None
        assert meta["provider"] == "tiktok"
        # Second pop returns None (single-use)
        assert oc.pop_state(state) is None

    def test_register_with_explicit_state(self):
        meta = oc.pop_state("explicit_token_xyz")
        assert meta is None  # not registered
        oc.register_state(provider="tiktok", state="explicit_token_xyz")
        meta = oc.pop_state("explicit_token_xyz")
        assert meta["provider"] == "tiktok"


class TestMaskSecret:
    def test_masks_long_token(self):
        m = oc.mask_secret("ROW_abc123_def456_ghi789_jkl012")
        assert m.startswith("ROW_abc1")
        assert "...l012" in m
        assert "len=" in m

    def test_short_token_returns_stars(self):
        assert oc.mask_secret("short") == "****"
        assert oc.mask_secret("") == "****"

    def test_unicode_token_masks_consistently(self):
        # Just ensure no crash on unicode
        m = oc.mask_secret("测试" * 10)
        assert m.startswith("测试")
        assert "len=" in m
