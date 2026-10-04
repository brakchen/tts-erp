"""Small Artemis HTTP adapter with explicit session-id idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx


class ArtemisTransportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ArtemisResult:
    session_id: UUID
    status: str
    output: dict[str, Any] | None = None
    error: str | None = None
    steps_count: int | None = None
    final_publish_observed: bool = False

    @property
    def terminal(self) -> bool:
        return self.status in {"success", "failed", "cancelled", "rejected"}


class ArtemisClient:
    def __init__(
        self, base_url: str, *, token: str | None = None, timeout: float = 30
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.timeout = timeout

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, headers=self._headers, timeout=self.timeout
            ) as client:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
        except (httpx.HTTPError, ValueError) as exc:
            raise ArtemisTransportError(str(exc)[:500]) from exc

    async def submit(
        self,
        *,
        goal: str,
        session_id: UUID,
        device_serial: str,
        app_package: str,
        profile: str = "pro",
        verification_level: str = "strict",
    ) -> ArtemisResult:
        payload = await self._request(
            "POST",
            "/api/run",
            json={
                "goal": goal,
                "session_id": str(session_id),
                "device_serial": device_serial,
                "locked_app_package": app_package,
                "profile": profile,
                "verification_level": verification_level,
                "ingress": "tts-erp-video-publish",
            },
        )
        return self._result(session_id, payload)

    async def get_task(self, session_id: UUID) -> ArtemisResult:
        payload = await self._request("GET", f"/api/sessions/{session_id}")
        return self._result(session_id, payload)

    async def get_steps_count(self, session_id: UUID) -> int | None:
        payload = await self._request("GET", f"/api/sessions/{session_id}/steps")
        steps = payload.get("steps")
        return len(steps) if isinstance(steps, list) else None

    @staticmethod
    def _result(session_id: UUID, payload: dict[str, Any]) -> ArtemisResult:
        raw = str(payload.get("status", payload.get("state", "running"))).lower()
        mapping = {
            "completed": "success",
            "success": "success",
            "error": "failed",
            "canceled": "cancelled",
        }
        status = mapping.get(raw, raw)
        return ArtemisResult(
            session_id,
            status,
            payload.get("output") if isinstance(payload.get("output"), dict) else None,
            str(payload.get("error"))[:500] if payload.get("error") else None,
            payload.get("steps_count"),
        )
