"""Cold test-only API with independently verified child/HTTP identity.

No seed/login/canary/business write is provided by this layer. A verified child
is NOT canary acceptance. Reuses the maintained price fixture's env/port/process
lifecycle without changing its read-only browser contract. Factory interface:
https://www.uvicorn.org/ (application factories / --factory).
"""
from __future__ import annotations

import http.client
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scripts._test_db_connections import RunBinding, _process_state, load_run_binding
from tests.support.frontend_p2_binding import (
    NONCE_HEADER,
    REFUSAL,
    current_binding,
    private_directory,
    require_nonce,
    validate_application,
    verify_attestation,
    write_attestation,
)

ROOT = Path(__file__).resolve().parents[2]
LAUNCH = "TTS_ERP_TEST_P2_LAUNCH_NONCE"
ATTESTATION = "TTS_ERP_TEST_P2_ATTESTATION"
PARENT_PID = "TTS_ERP_TEST_P2_PARENT_PID"
PARENT_START = "TTS_ERP_TEST_P2_PARENT_START"
COLLABORATORS = "TTS_ERP_TEST_P2_COLLABORATORS"
_COLLABORATOR_PROFILE = "remaining-console-v1"


def child_environment(value: str, *, nonce: str, attestation: Path) -> dict[str, str]:
    try:
        binding = load_run_binding(value)
        require_nonce(nonce)
        private_directory(attestation)
        if attestation.exists() or attestation.is_symlink():
            raise ValueError(REFUSAL)
        # Lazy reuse: the ASGI factory must validate proof before app/model imports.
        from tests.support.spu_price_stats_live import api_env

        env = api_env(value)
        # Opt-in belongs to this launch, never an inherited parent environment.
        env.pop(COLLABORATORS, None)
        env.update({
            "TTS_ERP_TEST_RUN_PROOF": os.environ["TTS_ERP_TEST_RUN_PROOF"],
            "TTS_ERP_TEST_RUN_ID": binding.run_id,
            LAUNCH: nonce, ATTESTATION: str(attestation),
            PARENT_PID: str(os.getpid()), PARENT_START: _process_state(os.getpid())[1],
        })
        return env
    except (ValueError, TypeError, KeyError, OSError):
        raise ValueError(REFUSAL) from None


class NonceApp:
    """Test-only outer ASGI wrapper, not production middleware/diagnostics."""

    def __init__(self, app: ASGIApp, nonce: str) -> None:
        require_nonce(nonce)
        self.app = app
        self.nonce = nonce.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def bound_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", []) if key.lower() != NONCE_HEADER.encode("ascii")]
                message = {**message, "headers": [*headers, (NONCE_HEADER.encode("ascii"), self.nonce)]}
            await send(message)

        await self.app(scope, receive, bound_send)


def create_app() -> ASGIApp:
    """Only the launched test process uses this uvicorn factory."""
    try:
        value, binding = current_binding()
        nonce = os.environ[LAUNCH]
        attestation = Path(os.environ[ATTESTATION])
        require_nonce(nonce)
        private_directory(attestation)
        profile = os.environ.get(COLLABORATORS)
        if profile not in (None, _COLLABORATOR_PROFILE):
            raise ValueError(REFUSAL)
        parent, _ = _process_state(os.getpid())
        _, parent_start = _process_state(parent)
        if (os.environ.get("TTS_ERP_DB_URL") != value
                or os.environ.get("TTS_ERP_AUTH_MODE") != "enforce"
                or os.environ.get("TTS_ERP_TEST_NO_DOTENV") != "1"
                or os.environ.get(PARENT_PID) != str(parent)
                or os.environ.get(PARENT_START) != parent_start
                or attestation.exists() or attestation.is_symlink()):
            raise ValueError(REFUSAL)
        from tests.support.spu_price_stats_live import TEST_FERNET_KEY

        if os.environ.get("TTS_ERP_FERNET_KEY") != TEST_FERNET_KEY:
            raise ValueError(REFUSAL)
        from tts_erp_v2.db.base import get_engine, get_session_factory

        validate_application(binding, get_engine(), get_session_factory())
        from tts_erp_v2.app import build_app

        app = build_app()
        # Recheck the actual API singleton/factory after all app imports.
        validate_application(binding, get_engine(), get_session_factory())
        lifespan = None
        if profile == _COLLABORATOR_PROFILE:
            # Fixed test module only: no caller-supplied factory/import/callback.
            from tests.support.frontend_test_collaborators import install

            lifespan = install(app)
            if not callable(lifespan):
                raise ValueError(REFUSAL)
            validate_application(binding, get_engine(), get_session_factory())
        mounted = Starlette(routes=[Mount("/tts", app=app), Mount("/", app=app)], lifespan=lifespan)
        write_attestation(attestation, binding, nonce)
        return NonceApp(mounted, nonce)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, SQLAlchemyError):
        raise ValueError(REFUSAL) from None


@dataclass(frozen=True)
class VerifiedChild:
    """Identity/readiness only. Callers still need authenticated canary acceptance."""

    base: str
    port: int
    pid: int
    start: str
    nonce: str
    attestation: Path
    binding: RunBinding
    process: subprocess.Popen[bytes] = field(repr=False, compare=False)

    def require_live(self) -> None:
        """Read exit status through the launch owner's retained Popen handle."""
        if self.process.pid != self.pid or self.process.poll() is not None:
            raise ValueError(REFUSAL)


def _wait_ready(child: VerifiedChild, *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        child.require_live()
        connection = http.client.HTTPConnection("127.0.0.1", child.port, timeout=1)
        try:
            connection.request("GET", "/healthz")
            response = connection.getresponse()
            raw = response.read(4097)
            if response.status == 200:
                try:
                    payload = json.loads(raw) if len(raw) <= 4096 else None
                except (ValueError, TypeError, RecursionError):
                    raise ValueError(REFUSAL) from None
                if not isinstance(payload, dict) or payload.get("auth_mode") != "enforce":
                    raise ValueError(REFUSAL)
                verify_attestation(child.attestation, child.binding, nonce=child.nonce,
                                   child_pid=child.pid, child_start=child.start,
                                   http_nonce=response.getheader(NONCE_HEADER))
                child.require_live()
                return
        except (OSError, http.client.HTTPException):
            pass  # Bounded cold-start readiness only, never a command retry.
        finally:
            connection.close()
        time.sleep(0.05)
    raise ValueError(REFUSAL)


@contextmanager
def live_api_server(value: str, *, collaborators: bool = False) -> Iterator[VerifiedChild]:
    """No yield until actual child/file/HTTP all match this fresh launch."""
    from tests.support.spu_price_stats_live import _free_port, _stop_process_group

    if type(collaborators) is not bool:
        raise ValueError(REFUSAL)
    binding = load_run_binding(value)
    with tempfile.TemporaryDirectory(prefix="tts-erp-p2-child-") as directory:
        path = Path(directory) / "TEST_child_attestation.json"
        nonce = secrets.token_hex(32)
        env = child_environment(value, nonce=nonce, attestation=path)
        if collaborators:
            env[COLLABORATORS] = _COLLABORATOR_PROFILE
        port = _free_port()
        command = [sys.executable, "-m", "uvicorn", "tests.support.frontend_p2_live:create_app",
                   "--factory", "--host", "127.0.0.1", "--port", str(port),
                   "--log-level", "warning", "--no-access-log"]
        # Never retain/relay raw startup tracebacks or request parameters as evidence.
        proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            parent, start = _process_state(proc.pid)
            if parent != os.getpid():
                raise ValueError(REFUSAL)
            child = VerifiedChild(base=f"http://127.0.0.1:{port}", port=port, pid=proc.pid,
                                  start=start, nonce=nonce, attestation=path, binding=binding, process=proc)
            _wait_ready(child)
            yield child
        finally:
            _stop_process_group(proc)
