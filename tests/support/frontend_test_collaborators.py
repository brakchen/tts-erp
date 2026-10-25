"""One fixed test-only task/storage/device profile, never production setup.

Uses FastAPI dependency overrides, Starlette's lifespan context and pytest's
MonkeyPatch cleanup; all are existing project dependencies/public test seams.
No session, role, database getter, scheduler registry or business fact is faked.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from pytest import MonkeyPatch
from starlette.applications import Starlette

from tests.support.frontend_p2_binding import (
    REFUSAL,
    current_binding,
    validate_application,
)
from tests.support.frontend_p2_live import _COLLABORATOR_PROFILE, COLLABORATORS
from tests.support.frontend_test_object_store import ControlledObjectStore


@dataclass
class Collaborators:
    """Observable in-process effects only; never a claim of real job completion."""

    store: ControlledObjectStore
    jobs: list[tuple[str, str | None]] = field(default_factory=list)


def install(app: FastAPI) -> Callable[[Starlette], AbstractAsyncContextManager[None]]:
    """Return the test root's lifecycle; mounted application hooks stay unchanged.

    FastAPI lifespans run only on the main app, not mounted sub-applications:
    https://fastapi.tiangolo.com/advanced/events/#sub-applications
    """
    if os.environ.get(COLLABORATORS) != _COLLABORATOR_PROFILE:
        raise ValueError(REFUSAL)
    _value, binding = current_binding()
    from tts_erp_v2.db.base import get_engine, get_session_factory

    validate_application(binding, get_engine(), get_session_factory())
    if getattr(app.state, "frontend_test_profile", None) is not None:
        raise ValueError(REFUSAL)

    from tts_erp_v2.api.v2 import admin, video_publish
    from tts_erp_v2.publishing.artemis_client import (
        ArtemisClient,
        ArtemisTransportError,
    )

    @asynccontextmanager
    async def configured(_outer: Starlette) -> AsyncIterator[None]:
        with ControlledObjectStore() as store, MonkeyPatch.context() as patch:
            state = Collaborators(store)

            def record_job(job_name: str, *, shop_id: str | None = None) -> None:
                state.jobs.append((job_name, shop_id))

            async def device_request(
                _client: ArtemisClient, method: str, path: str, **kwargs: Any
            ) -> dict:
                if method != "GET" or path != "/api/devices" or kwargs:
                    raise ArtemisTransportError("TEST_DEVICE_OPERATION_DISABLED")
                return {
                    "devices": [
                        {
                            "serial": "TEST_device",
                            "model": "TEST_model",
                            "product": "TEST_product",
                            "state": "device",
                            "is_busy": False,
                            "is_emulator": True,
                        }
                    ]
                }

            def request_store(request: Request) -> ControlledObjectStore:
                listener = request.scope.get("server")
                if (
                    not listener
                    or listener[0] != "127.0.0.1"
                    or request.url.scheme != "http"
                    or request.url.netloc != f"127.0.0.1:{listener[1]}"
                ):
                    raise ValueError("TEST_UPLOAD_ORIGIN_REFUSED")
                store.allow_origin(f"http://127.0.0.1:{listener[1]}")
                return store

            # Patch exact collaborators, not authentication/session/DB providers.
            patch.setattr(admin, "run_scheduled_job_once", record_job)
            patch.setattr(ArtemisClient, "_request", device_request)
            patch.setitem(
                app.dependency_overrides, video_publish.get_store, request_store
            )
            patch.setenv("ARTEMIS_BASE_URL", store.origin)
            patch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
            patch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", store.bucket)
            patch.setattr(
                app.state, "frontend_test_collaborators", state, raising=False
            )
            validate_application(binding, get_engine(), get_session_factory())
            yield

    app.state.frontend_test_profile = _COLLABORATOR_PROFILE
    return configured
