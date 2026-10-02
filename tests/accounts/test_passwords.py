"""密码策略与 argon2id 校验（tts_erp_v2/accounts/passwords.py）。

策略契约（tech-doc/user-account-authz-design.md §8）：
长度 ≥ 6 且 ≤ 128，必须同时含大写字母、小写字母、数字；不强制特殊字符。
哈希：argon2id PHC 串；verify 失败一律 fail closed。
"""

from __future__ import annotations

import pytest

from tts_erp_v2.accounts import passwords

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]


def test_policy_accepts_minimal_valid_password() -> None:
    passwords.validate_password_policy("Abc123")  # 恰好 6 位：大小写 + 数字


def test_policy_accepts_password_with_spaces_and_symbols() -> None:
    passwords.validate_password_policy("Ab 1!x")


def test_policy_rejects_short_password() -> None:
    with pytest.raises(passwords.PasswordPolicyError, match="长度至少 6 位"):
        passwords.validate_password_policy("Ab1cd")


def test_policy_rejects_missing_uppercase() -> None:
    with pytest.raises(passwords.PasswordPolicyError, match="需包含大写字母"):
        passwords.validate_password_policy("abc123")


def test_policy_rejects_missing_lowercase() -> None:
    with pytest.raises(passwords.PasswordPolicyError, match="需包含小写字母"):
        passwords.validate_password_policy("ABC123")


def test_policy_rejects_missing_digit() -> None:
    with pytest.raises(passwords.PasswordPolicyError, match="需包含数字"):
        passwords.validate_password_policy("Abcdef")


def test_policy_rejects_overlong_password() -> None:
    with pytest.raises(passwords.PasswordPolicyError, match="长度不超过 128 位"):
        passwords.validate_password_policy("Ab1" + "a" * 126)  # 129 位


def test_policy_reports_every_violation_at_once() -> None:
    with pytest.raises(passwords.PasswordPolicyError) as exc_info:
        passwords.validate_password_policy("abc")
    message = str(exc_info.value)
    assert "长度至少 6 位" in message
    assert "需包含大写字母" in message
    assert "需包含数字" in message


def test_hash_password_returns_argon2id_phc_and_roundtrips() -> None:
    digest = passwords.hash_password("Abc123")
    assert digest.startswith("$argon2id$")
    assert passwords.verify_password("Abc123", digest) is True
    assert passwords.verify_password("Abc124", digest) is False


def test_hash_password_enforces_policy_before_hashing() -> None:
    with pytest.raises(passwords.PasswordPolicyError):
        passwords.hash_password("abcdef")


def test_hash_password_uses_unique_salts() -> None:
    assert passwords.hash_password("Abc123") != passwords.hash_password("Abc123")


def test_verify_password_fails_closed_on_broken_hash() -> None:
    assert passwords.verify_password("Abc123", "not-a-phc-string") is False
    assert passwords.verify_password("Abc123", "") is False


def test_verify_dummy_never_raises() -> None:
    passwords.verify_dummy("whatever")
    passwords.verify_dummy("")
