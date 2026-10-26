"""Owned, bounded loopback uploads; never a MinIO/network fallback.

Uses Python's standard ThreadingHTTPServer solely for isolated browser tests:
https://docs.python.org/3/library/http.server.html (Python stdlib / PSF licence).
Only capability paths minted by this in-memory store accept bytes. No payload,
credential value or upload URL is logged or persisted.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, RLock, Thread
from types import TracebackType
from typing import Self
from urllib.parse import urlsplit

from tts_erp_v2.publishing.object_store import ObjectVersionMismatch
from tts_erp_v2.storage.minio_client import ObjectNotFound

_MAX_BYTES = 32 * 1024 * 1024


class ControlledObjectStore:
    bucket = "TEST_video"
    default_expiry = timedelta(seconds=120)

    def __init__(self) -> None:
        self._lock = RLock()
        self._origins: set[str] = set()
        self._tickets: dict[str, str] = {}
        self._objects: dict[str, dict] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self._release = Event()
        self._release.set()
        self._pause_next = False
        self._observations = {"optionsCount": 0, "putCount": 0, "completePuts": 0,
                              "abortedPuts": 0, "bytesReceived": 0, "paused": False,
                              "credentialsReceived": False, "lastSize": 0, "lastSha256": ""}

    def __enter__(self) -> Self:
        store = self

        class Upload(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass  # Base handler otherwise logs capability URLs.

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(3)

            def answer(self, status: int) -> None:
                self.send_response(status)
                origin = self.headers.get("Origin", "")
                with store._lock:
                    if origin in store._origins:
                        self.send_header("Access-Control-Allow-Origin", origin)
                        self.send_header("Vary", "Origin")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()

            def permitted(self) -> bool:
                with store._lock:
                    return (
                        self.path in store._tickets
                        and self.headers.get("Origin") in store._origins
                        and not any(
                            self.headers.get(key) is not None
                            for key in (
                                "Cookie",
                                "Authorization",
                                "X-API-Key",
                                "X-Requested-With",
                                "X-CSRF-Token",
                            )
                        )
                    )

            def do_OPTIONS(self) -> None:
                with store._lock:
                    store._observations["optionsCount"] += 1
                requested = {
                    part.strip().lower()
                    for part in self.headers.get(
                        "Access-Control-Request-Headers", ""
                    ).split(",")
                    if part.strip()
                }
                if (
                    not self.permitted()
                    or self.headers.get("Access-Control-Request-Method") != "PUT"
                    or requested - {"content-type"}
                ):
                    self.answer(403)
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "PUT")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()

            def do_PUT(self) -> None:
                with store._lock:
                    store._observations["putCount"] += 1
                    store._observations["credentialsReceived"] |= any(
                        self.headers.get(key) is not None for key in
                        ("Cookie", "Authorization", "X-API-Key", "X-Requested-With", "X-CSRF-Token"))
                if not self.permitted():
                    self.answer(403)
                    return
                try:
                    size = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    self.answer(400)
                    return
                if not 0 < size <= _MAX_BYTES or self.headers.get("Transfer-Encoding"):
                    self.answer(413)
                    return
                if self.headers.get("Content-Type") != "video/mp4":
                    self.answer(415)
                    return
                try:
                    received = 0
                    hasher = hashlib.sha256()
                    with store._lock:
                        pause = store._pause_next
                        store._pause_next = False
                    while received < size:
                        chunk = self.rfile.read(min(65536, size - received))
                        if not chunk:
                            with store._lock:
                                store._observations["abortedPuts"] += 1
                            self.close_connection = True
                            return
                        received += len(chunk)
                        hasher.update(chunk)
                        with store._lock:
                            store._observations["bytesReceived"] += len(chunk)
                        if pause:
                            with store._lock:
                                store._observations["paused"] = True
                            store._release.wait(timeout=8)
                            with store._lock:
                                store._observations["paused"] = False
                            pause = False
                    digest = hasher.hexdigest()
                    with store._lock:
                        key = store._tickets[self.path]
                        store._observations["completePuts"] += 1
                        store._observations["lastSize"] = size
                        store._observations["lastSha256"] = digest
                        store._objects[key] = {
                            "size": size,
                            "etag": digest[:32],
                            "sha256": digest,
                            "content_type": "video/mp4",
                            "cookieReceived": False,
                        }
                    self.answer(200)
                except (OSError, KeyError):
                    # A read timeout is not evidence of browser cancellation.
                    self.close_connection = True

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Upload)
        self._server.daemon_threads = True
        self._thread = Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._release.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._server = None
        with self._lock:
            self._objects.clear()
            self._tickets.clear()
            self._origins.clear()

    def observations(self) -> dict:
        self.check_available()
        with self._lock:
            return dict(self._observations)

    def control(self, action: str) -> dict:
        self.check_available()
        with self._lock:
            if action == "pause" and not self._pause_next and not self._observations["paused"]:
                self._pause_next = True
                self._release.clear()
            elif action == "release":
                self._pause_next = False
                self._release.set()
            else:
                raise ValueError("TEST_UPLOAD_CONTROL_REFUSED")
        return self.observations()

    @property
    def origin(self) -> str:
        self.check_available()
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_port}"

    def allow_origin(self, origin: str) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.netloc != f"127.0.0.1:{parsed.port}"
        ):
            raise ValueError("TEST_UPLOAD_ORIGIN_REFUSED")
        with self._lock:
            self._origins.add(origin)

    def presign_put(self, key: str, content_type: str) -> str:
        if not key or len(key) > 512 or content_type != "video/mp4":
            raise ValueError("TEST_UPLOAD_TICKET_REFUSED")
        path = "/TEST_upload/" + secrets.token_urlsafe(24)
        with self._lock:
            self._tickets[path] = key
        return self.origin + path

    def stat(self, key: str) -> dict:
        self.check_available()
        with self._lock:
            if key not in self._objects:
                raise ObjectNotFound("TEST_OBJECT_NOT_FOUND")
            return dict(self._objects[key])

    def check_available(self) -> None:
        if self._server is None:
            raise RuntimeError("TEST_OBJECT_STORE_CLOSED")

    def download(self, key: str, destination: Path, expected_etag: str) -> str:
        raise RuntimeError("TEST_WORKER_DOWNLOAD_DISABLED")

    def remove(self, key: str, expected_etag: str | None = None) -> None:
        self.check_available()
        with self._lock:
            item = self._objects.get(key)
            if item and expected_etag and item["etag"] != expected_etag.strip('"'):
                raise ObjectVersionMismatch("TEST_OBJECT_VERSION_MISMATCH")
            self._objects.pop(key, None)
