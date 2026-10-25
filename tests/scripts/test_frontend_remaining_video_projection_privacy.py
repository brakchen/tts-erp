"""Privacy of the pytest-rewritten explanation behind the video projection flags.

Pure assertion-rewrite test: no clone, no HTTP, no database, no owned/probe/
nonce/auth guard and nothing mocked. The canaries are synthetic TEST strings,
so a failure here shows the leak shape without exposing anything real.

The helper is imported through the module (not the clone fixture alias), so
this file creates no second child, engine or session factory.
"""

from __future__ import annotations

import pytest

from tests.scripts.test_frontend_remaining_video_live_api import _flag

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_unit]

SERIAL_CANARY = "TEST_PRIVACY_CANARY_SERIAL_5f21c8"
URL_CANARY = "TEST_PRIVACY_CANARY_URL_9ab4e7"
ETAG_CANARY = "TEST_PRIVACY_CANARY_ETAG_2c6d1a"
ETAG_EXPECTED_CANARY = "TEST_PRIVACY_CANARY_ETAG_EXPECTED_71b3d9"

# The canaries are the payload's own keys and values, so every predicate below
# is genuinely false and the explanation has something to echo.
PAYLOAD = {
    SERIAL_CANARY: {"deviceSerialMasked": SERIAL_CANARY},
    URL_CANARY: {"url": URL_CANARY + "/capability-path"},
}
BODY = "{" + '"rawSerial": "' + SERIAL_CANARY + '", "url": "' + URL_CANARY + '"}'


def test_failed_flag_explanation_does_not_echo_the_payload() -> None:
    """A failing flag must explain the bool only, never the payload."""
    with pytest.raises(AssertionError) as failure:
        assert _flag(SERIAL_CANARY not in PAYLOAD) is True
    message = str(failure.value)
    assert SERIAL_CANARY not in message
    assert URL_CANARY not in message
    assert ETAG_CANARY not in message


def test_failed_flag_explanation_does_not_echo_a_serialised_body() -> None:
    """The same shape for the serialised body, which holds the bearer path."""
    with pytest.raises(AssertionError) as failure:
        assert _flag(URL_CANARY not in BODY) is True
    message = str(failure.value)
    assert URL_CANARY not in message
    assert SERIAL_CANARY not in message


def test_failed_flag_explanation_does_not_echo_an_etag_operand() -> None:
    """The etag comparison must fail on the bool, not on the stored hash."""
    with pytest.raises(AssertionError) as failure:
        assert _flag(ETAG_CANARY == ETAG_EXPECTED_CANARY) is True
    assert ETAG_CANARY not in str(failure.value)


def test_false_flag_still_fails_for_the_existing_caller() -> None:
    """Equivalence: a false predicate still fails the same caller assert."""
    with pytest.raises(AssertionError):
        assert _flag(False) is True


def test_true_flag_still_passes_for_the_existing_caller() -> None:
    """Equivalence: a true predicate still passes the same caller assert."""
    assert _flag(True) is True
