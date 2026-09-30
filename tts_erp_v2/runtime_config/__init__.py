"""Versioned runtime configuration with secret-reference resolution."""

from __future__ import annotations

from tts_erp_v2.runtime_config.resolver import resolve_runtime_config, select_payload

__all__ = ["resolve_runtime_config", "select_payload"]
