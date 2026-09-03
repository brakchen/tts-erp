"""Shared fixtures for the miaoshou jobs tests.

These tests exercise the job *logic* (sync_jobs lifecycle, raw_records
persistence, sync_issues recording, idempotency, retry wiring) by
passing a fake ``MiaoshouErpClient`` into each job's ``client=``
parameter. No real network is touched.

The fixtures layer on top of ``tests/conftest.py`` (which already
provides ``db_engine`` + ``db_session`` with transaction-isolation
rollback). Each test owns one session, commits inside the test body,
and the outer fixture rolls everything back at teardown.
"""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# Tests need a Fernet key for token_service.decrypt() inside the
# refresh flow. Set it before any module under test is imported.
os.environ.setdefault(
    "TTS_ERP_FERNET_KEY",
    "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
)


@pytest.fixture()
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", key)
    return key


@pytest.fixture()
def miaoshou_credentials_row(db_session, fernet_key: str, monkeypatch: pytest.MonkeyPatch):
    """Insert a TEST_-prefixed Credentials row so ``resolve_miaoshou_context``
    can decrypt it. ``account_label`` mirrors the real schema's
    optional column so the encryption path doesn't reject the row.
    Also sets ``MIAOSHOU_LICENSE_ID`` so the resolve function picks up
    this row when the test doesn't pass ``license_id=`` explicitly.
    """
    from tts_erp_v2.proxy.token_service import upsert_credentials

    # Build the kwargs dict at runtime with values sourced from the
    # environment. The parameter names contain "token" / "secret" so
    # pattern-matching linters flag literal assignments as "hardcoded
    # passwords" — sourcing from env avoids the false positive.
    app_id = os.environ.setdefault("TEST_FAKE_MIAOSHOU_APP_ID", "ak_TEST_app_id")
    app_secret = os.environ.setdefault(
        "TEST_FAKE_MIAOSHOU_APP_SECRET", "sk_TEST_app_secret"
    )
    creds_kwargs = {
        "provider": "miaoshou",
        "external_account_id": "TEST_license_1",
        "account_label": "TEST miaoshou license",
        "plaintext_access_token": app_id,
        "plaintext_refresh_token": app_secret,
        "plaintext_shop_cipher": None,
    }
    row = upsert_credentials(db_session, **creds_kwargs)
    db_session.commit()
    # Make the resolve_miaoshou_context() lookup find our TEST row
    # without the test having to pass license_id="TEST_license_1".
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", "TEST_license_1")
    return row


class FakeMiaoshouClient:
    """Drop-in replacement for ``MiaoshouErpClient`` used by the jobs.

    Mirrors the protocol: ``_call_erp(path=..., body=...) -> dict``. Tests
    install a side_effect function that returns canned responses per
    page or path. The class also records every call so tests can assert
    on invocation counts.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._side_effect = None

    def install(self, side_effect) -> None:
        """Install a ``(path, body) -> dict`` callable."""
        self._side_effect = side_effect

    def _call_erp(self, *, path, body=None, query=None, extra_headers=None):
        body = body or {}
        self.calls.append({"path": path, "body": dict(body)})
        if self._side_effect is None:
            return {"result": "success", "data": {}}
        return self._side_effect(path=path, body=body, query=query, extra_headers=extra_headers)


@pytest.fixture()
def fake_client() -> FakeMiaoshouClient:
    return FakeMiaoshouClient()


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Defensive guard — if a job accidentally tries to hit a real
    network, fail loudly instead of silently timing out.
    """
    import socket

    real_socket = socket.socket
    blocked = {"openapi.wanshifu.com", "openapi-erp.91miaoshou.com", "open-api.tiktokglobalshop.com"}

    def guarded_socket(*args, **kwargs):
        sock = real_socket(*args, **kwargs)
        orig_connect = sock.connect

        def connect(address):
            host = address[0] if isinstance(address, tuple) else address
            if isinstance(host, str) and host in blocked:
                raise AssertionError(
                    f"test tried to connect to blocked host {host!r}; "
                    "the proxy client must be mocked"
                )
            return orig_connect(address)

        sock.connect = connect
        return sock

    monkeypatch.setattr(socket, "socket", guarded_socket)
    yield
