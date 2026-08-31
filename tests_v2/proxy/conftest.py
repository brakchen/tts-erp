"""Shared fixtures for proxy-layer tests (token_service, tiktok_auth, ...).

Provides the Fernet-key fixture used by tests that exercise the
``integration.credentials`` encryption path.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin TTS_ERP_FERNET_KEY to a known-good key for the test."""
    key = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", key)
    return key


@pytest.fixture(autouse=True)
def _ensure_fernet_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive backstop — set the Fernet key even if the test forgets."""
    os.environ.setdefault(
        "TTS_ERP_FERNET_KEY",
        "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
    )
