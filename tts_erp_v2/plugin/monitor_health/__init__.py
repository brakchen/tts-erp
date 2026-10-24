"""Minimal monitor-health domain; parent owns router and JobSpec registration."""

from __future__ import annotations

from tts_erp_v2.plugin.monitor_health.job import run

__all__ = ["run"]
