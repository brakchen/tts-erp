"""Vector regression tests for the HMAC-SHA256 TikTok Shop signing.

Pinned by AGENTS.md §2.2. The canonical string format MUST stay
byte-for-byte identical to the legacy :mod:`tts_signing.py` — these
vectors exist to catch any drift.

Each vector's ``expected_sig`` is *derived* from the canonical-string
assertions in the same module. If the canonical format changes, the
assertion tests fail first, surfacing the change before we let an
unrelated signature change slip through.
"""
from __future__ import annotations

import hashlib
import hmac

import pytest

from tts_erp_v2.proxy.tts_shop.signing import build_canonical, sign_request


# NOTE: marked `layer_integration` (not `layer_unit`) because importing
# `tts_erp_v2.proxy.tts_shop.signing` indirectly triggers
# `tts_erp_v2.db.base._resolve_db_url()`, which raises if TTS_ERP_DB_URL
# isn't set. The test bodies themselves are pure signing math, but the
# module-level import forces the DB env var.
pytestmark = [pytest.mark.domain_proxy, pytest.mark.layer_integration]

SECRET = "abc123"
PATH = "/order/202309/orders/search"
QUERY_GET = {"app_key": "test_key", "shop_cipher": "sc", "timestamp": "1700000000"}
BODY_POST = '{"order_status":"UNSHIPPED"}'

# The exact canonical strings AGENTS.md §2.2 prescribes. Kept as
# raw string literals so a typo fails the test loud and clear.
EXPECTED_CANONICAL_GET = (
    f"{SECRET}{PATH}"
    f"app_keytest_keyshop_ciphersc"
    f"timestamp1700000000"
    f"{SECRET}"
)
EXPECTED_CANONICAL_POST = (
    f"{SECRET}{PATH}"
    f"app_keytest_keyshop_ciphersc"
    f"timestamp1700000000"
    f"{BODY_POST}"
    f"{SECRET}"
)

# Expected signatures: derived from the canonical above. If these don't
# match what the implementation produces, either the implementation
# drifted or the canonical above drifted — either way the test fails.
EXPECTED_SIG_GET = hmac.new(
    SECRET.encode("utf-8"),
    EXPECTED_CANONICAL_GET.encode("utf-8"),
    hashlib.sha256,
).hexdigest()
EXPECTED_SIG_POST = hmac.new(
    SECRET.encode("utf-8"),
    EXPECTED_CANONICAL_POST.encode("utf-8"),
    hashlib.sha256,
).hexdigest()


def test_canonical_get_format_matches_agents_md_2_2() -> None:
    """AGENTS.md §2.2 GET: {secret}{path}{kv}{secret}."""
    c = build_canonical(SECRET, PATH, QUERY_GET, body=None)
    assert c == EXPECTED_CANONICAL_GET


def test_canonical_post_body_in_middle_not_at_end() -> None:
    """AGENTS.md §2.2 POST: {secret}{path}{kv}{body}{secret}.

    This is the most error-prone rule — putting body after the trailing
    secret produces 106001 invalid sign.
    """
    c = build_canonical(SECRET, PATH, QUERY_GET, body=BODY_POST)
    assert c == EXPECTED_CANONICAL_POST


def test_get_signature_matches_canonical_vector() -> None:
    sig = sign_request(SECRET, PATH, QUERY_GET, body=None)
    assert sig == EXPECTED_SIG_GET


def test_post_signature_matches_canonical_vector() -> None:
    sig = sign_request(SECRET, PATH, QUERY_GET, body=BODY_POST)
    assert sig == EXPECTED_SIG_POST


@pytest.mark.parametrize(
    "params,expected_fragment",
    [
        ({"app_key": "k", "timestamp": "1"}, "app_keyktimestamp1"),
        ({"timestamp": "1", "app_key": "k"}, "app_keyktimestamp1"),
        (
            {"z_last": "z", "a_first": "a", "m_mid": "m"},
            "a_firstam_midmz_lastz",
        ),
    ],
)
def test_key_sort_order_is_alphabetical(params, expected_fragment) -> None:
    """Order of input dict must NOT affect the canonical string."""
    c = build_canonical("S", "/p", params, body=None)
    assert c == f"S/p{expected_fragment}S"


def test_body_with_unicode_not_url_encoded() -> None:
    """Body must be raw JSON — URL-encoding characters breaks the sign."""
    body = '{"name":"测试"}'  # ensure_ascii=False raw
    c = build_canonical("S", "/p", {"app_key": "k", "timestamp": "1"}, body=body)
    # If someone URL-encodes the body, "%" or "u" chars would appear in the
    # middle; raw "{" should appear right after the kv segment.
    assert c.endswith(f'app_keyktimestamp1{body}S')


def test_empty_body_equals_no_body() -> None:
    """Empty body string is treated the same as None (no body segment)."""
    a = build_canonical("S", "/p", {"app_key": "k", "timestamp": "1"}, body="")
    b = build_canonical("S", "/p", {"app_key": "k", "timestamp": "1"}, body=None)
    assert a == b
