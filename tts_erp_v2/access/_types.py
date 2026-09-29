"""Immutable public values for access and deployment decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


@dataclass(frozen=True, slots=True)
class DeploymentPathInput:
    """Raw deployment-path facts extracted by an HTTP adapter."""

    path: str
    raw_path: bytes | None
    root_path: str


@dataclass(frozen=True, slots=True)
class CanonicalPath:
    """Canonical downstream and route-relative forms of one request path."""

    downstream_path: str
    downstream_raw_path: bytes | None
    route_path: str
    root_path: str


class Role(StrEnum):
    READONLY = "readonly"
    READWRITE = "readwrite"
    ADMIN = "admin"

    @property
    def level(self) -> int:
        return {
            Role.READONLY: 1,
            Role.READWRITE: 2,
            Role.ADMIN: 3,
        }[self]


class AuthMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"


class AccessEffect(StrEnum):
    ALLOW = "allow"
    SHADOW_ALLOW = "shadow_allow"
    DENY = "deny"
    REDIRECT = "redirect"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True, slots=True)
class AccessRequest:
    method: str
    route_path: str
    accepts_html: bool
    client_ip: str
    session_cookie: str | None = None
    bearer_key: str | None = None
    api_key: str | None = None


@dataclass(frozen=True, slots=True)
class Credential:
    key_hash: str
    role: Role
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AccessGrant:
    mode: AuthMode
    role: Role | None = None
    key_hash: str | None = None
    scopes: tuple[str, ...] = ()
    auth_method: Literal["cookie", "bearer"] | None = None
    bypass: bool = False

    def allows(self, required: Role) -> bool:
        if self.bypass:
            return True
        return self.role is not None and self.role.level >= required.level


@dataclass(frozen=True, slots=True)
class AccessDecision:
    effect: AccessEffect
    grant: AccessGrant
    required_role: Role | None = None
    status: int | None = None
    detail: str | None = None
    challenge: bool = False
    retry_after: int | None = None
    rate_limit: int | None = None
