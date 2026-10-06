"""Small Artemis HTTP adapter with explicit session-id idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

_SAFE_PRE_ADMISSION_CODES = frozenset({"DEVICE_LOCKED", "DEVICE_BUSY"})


class ArtemisTransportError(RuntimeError):
    pass


class ArtemisSessionNotFound(ArtemisTransportError):
    pass


class ArtemisAdmissionRejected(ArtemisTransportError):
    """A pre-admission 409 that is safe to retry without budget use."""

    def __init__(self, code: str = "DEVICE_LOCKED") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ArtemisResult:
    session_id: UUID
    status: str
    output: dict[str, Any] | None = None
    error: str | None = None
    steps_count: int | None = None
    final_publish_observed: bool = False
    same_session_resubmitted: bool = False

    @property
    def terminal(self) -> bool:
        return self.status in {
            "success",
            "failed",
            "cancelled",
            "rejected",
            "missing",
            "not_found",
        }


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
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ArtemisSessionNotFound("ARTEMIS_SESSION_NOT_FOUND") from exc
            if exc.response.status_code == 409:
                try:
                    detail = exc.response.json()
                except ValueError:
                    detail = None
                code = detail.get("code") if isinstance(detail, dict) else None
                if isinstance(code, str) and code in _SAFE_PRE_ADMISSION_CODES:
                    raise ArtemisAdmissionRejected(code) from exc
                raise ArtemisTransportError("ARTEMIS_HTTP_409_AMBIGUOUS") from exc
            raise ArtemisTransportError(
                f"ARTEMIS_HTTP_{exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ArtemisTransportError("ARTEMIS_TRANSPORT_ERROR") from exc

    async def check_available(self) -> None:
        await self._request("GET", "/api/status")

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
        try:
            payload = await self._request("GET", f"/api/sessions/{session_id}")
        except ArtemisSessionNotFound:
            status = await self._request("GET", "/api/status")
            active = status.get("active_task") or status.get("activeTask")
            if isinstance(active, dict) and str(
                active.get("session_id") or active.get("sessionId")
            ) == str(session_id):
                payload = active
            else:
                return ArtemisResult(session_id, "not_found")
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
        output = (
            payload.get("output") if isinstance(payload.get("output"), dict) else None
        )
        final_observed = bool(
            payload.get("final_publish_observed")
            or payload.get("finalPublishObserved")
            or (output or {}).get("final_publish_observed")
            or (output or {}).get("finalPublishObserved")
        )
        return ArtemisResult(
            session_id,
            status,
            output,
            str(payload.get("error"))[:500] if payload.get("error") else None,
            payload.get("steps_count", payload.get("stepsCount")),
            final_observed,
        )
