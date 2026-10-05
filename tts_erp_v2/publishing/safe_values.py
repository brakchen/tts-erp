"""Controlled, content-free values shared by publishing APIs and events."""

from __future__ import annotations


def mask_device_serial(value: str) -> str:
    """Mask a serial while always hiding at least one source character."""
    if not value:
        return ""
    if len(value) > 8:
        return f"{value[:4]}…{value[-4:]}"
    visible = len(value) - 1
    prefix_length = (visible + 1) // 2
    suffix_length = visible - prefix_length
    suffix = value[-suffix_length:] if suffix_length else ""
    return f"{value[:prefix_length]}…{suffix}"
