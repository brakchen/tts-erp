"""Shared fixtures for jobs_token_refresh tests.

Sets the Fernet key env var so upsert_credentials / refresh_if_needed
can encrypt + decrypt the seed credentials row.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture()
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", key)
    return key


@pytest.fixture(autouse=True)
def _ensure_fernet_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Defensive — if the test imports happen before fernet_key is set,
    upsert_credentials will fail. Set here too as a backstop.
    """
    os.environ.setdefault(
        "TTS_ERP_FERNET_KEY",
        "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
    )
    yield


@pytest.fixture(autouse=True)
def _isolate_test_credentials(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Scope ``_query_due_credentials`` to TEST_-prefixed rows only.

    Why: prod's ``integration.credentials`` holds 1 miaoshou row with
    ``expires_at IS NULL`` (treated as 'due' by the conservative
    due-window clause). Without this patch, tests that expect
    ``scanned == 0`` see ``scanned == 1`` from that prod row,
    regardless of whether they inserted their own TEST_-prefix rows.

    The patch wraps ``tts_erp_v2.jobs.token_refresh._query_due_credentials``
    to filter by ``external_account_id LIKE 'TEST_%'``. Production
    behaviour is untouched; only the test process sees the filtered
    view. ``monkeypatch`` restores the original at teardown.

    Note: the production function is module-level with explicit
    ``session`` as the first positional arg and ``providers``,
    ``window``, ``now`` as keyword-only — see
    ``token_refresh.py:92-120``. The wrapper preserves the same arity
    and just post-filters the result.
    """
    from tts_erp_v2.jobs import token_refresh as tr_module

    original = tr_module._query_due_credentials

    def _scoped_query_due_credentials(
        session,
        *,
        providers: tuple[str, ...],
        window,
        now,
    ):
        rows = original(session, providers=providers, window=window, now=now)
        return [
            r
            for r in rows
            if r.external_account_id is not None
            and r.external_account_id.startswith("TEST_")
        ]

    monkeypatch.setattr(
        tr_module, "_query_due_credentials", _scoped_query_due_credentials
    )
    yield
