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