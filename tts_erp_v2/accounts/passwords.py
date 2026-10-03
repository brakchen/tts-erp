"""密码哈希与密码策略（设计：docs/design/user-account-authz-design.md §8）。

哈希：argon2id（argon2-cffi，MIT，https://github.com/hynek/argon2-cffi）。
策略：长度 ≥ 6，必须同时含大写字母、小写字母、数字；不强制特殊字符；上限 128。
"""

from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 6
MAX_LENGTH = 128

# argon2id 默认参数（m=64MiB, t=3, p=4）；PHC 串自带参数，可平滑升级。
_hasher = PasswordHasher()

# 用户不存在时也跑一次真实校验（防时序枚举）用的固定哈希。
_DUMMY_HASH = _hasher.hash("tts-erp-dummy-password-for-timing-equalization")

_UPPER = re.compile(r"[A-Z]")
_LOWER = re.compile(r"[a-z]")
_DIGIT = re.compile(r"[0-9]")


class PasswordPolicyError(ValueError):
    """密码不满足策略；message 逐条列出不满足的规则。"""


def validate_password_policy(plain: str) -> None:
    """Raise :class:`PasswordPolicyError` when the password breaks any rule."""
    problems: list[str] = []
    if len(plain) < MIN_LENGTH:
        problems.append(f"长度至少 {MIN_LENGTH} 位")
    if len(plain) > MAX_LENGTH:
        problems.append(f"长度不超过 {MAX_LENGTH} 位")
    if not _UPPER.search(plain):
        problems.append("需包含大写字母")
    if not _LOWER.search(plain):
        problems.append("需包含小写字母")
    if not _DIGIT.search(plain):
        problems.append("需包含数字")
    if problems:
        raise PasswordPolicyError("；".join(problems))


def hash_password(plain: str) -> str:
    """Validate policy then return an argon2id PHC string."""
    validate_password_policy(plain)
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    """Constant-shape verification; invalid/stored-broken hashes return False."""
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:  # noqa: BLE001 — any hasher failure = auth failure (fail closed)
        return False


def verify_dummy(plain: str) -> None:
    """Run a real argon2 verification against a throwaway hash.

    Called when the username does not exist so that the response time of
    "unknown user" matches "wrong password"（防用户枚举）.
    """
    try:
        _hasher.verify(_DUMMY_HASH, plain)
    except Exception:  # noqa: BLE001 — mismatch is the expected outcome
        return
