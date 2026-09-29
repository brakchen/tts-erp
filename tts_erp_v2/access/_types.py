"""Immutable public values for access and deployment decisions."""

from __future__ import annotations

from dataclasses import dataclass


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
