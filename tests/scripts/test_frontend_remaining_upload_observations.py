"""Native wire completion only; opaque bytes are not MP4/UI/readiness proof."""

from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4

import pytest
from playwright.sync_api import Error as PlaywrightError
from sqlalchemy.exc import SQLAlchemyError

from tests.scripts import test_frontend_remaining_video_live_api as video
from tests.support.frontend_p2_browser import fill_sensitive, live_browser
from tests.support.frontend_remaining_targets import RemainingTargets

# Original decorated fixtures, not wrappers or a second lifecycle owner.
clone = video.clone
worker = video.worker
pytestmark = [
    pytest.mark.domain_frontend,
    pytest.mark.layer_integration,
    pytest.mark.requires_browser,
]
SIZE = 24 * 1024 * 1024


def evaluate(page, script, argument=None):
    try:
        return page.evaluate(script, argument)
    except PlaywrightError:
        raise AssertionError("Wire browser operation refused") from None


def wait_store(page, gate, field, expected):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        facts = gate.observe_store()
        if facts[field] == expected:
            return facts
        page.wait_for_timeout(50)
    raise AssertionError("Wire store observation deadline exceeded")


@pytest.mark.parametrize("case", ["complete"], ids=["complete"])
def test_wire_upload_complete(clone: video.OwnedClone, worker: str, case: str):
    assert case == "complete"
    marker = "TEST_wire_" + uuid4().hex
    client_id = str(uuid4())
    intent = video._intent(clone, "admin", client_id, marker + ".mp4")
    hasher = hashlib.sha256()
    for _ in range(24):
        hasher.update(b"T" * (1024 * 1024))
    expected_hash = hasher.hexdigest()
    runtime = {"putObserved": False, "putCount": 0, "requestFailed": False, "failedCount": 0}
    try:
        with live_browser(
            clone.probe,
            clone.accounts["admin"],
            shop_id="9900123456789012",
            service_id=marker,
            enum_value=marker,
            width=1440,
            remaining=RemainingTargets(marker=marker),
        ) as (page, gate):

            def observe_request(request):
                if request.method == "PUT":
                    runtime["putObserved"] = True
                    runtime["putCount"] += 1

            def request_failed(request):
                if request.method == "PUT":
                    runtime["requestFailed"] = True
                    runtime["failedCount"] += 1

            page.on("request", observe_request)
            page.on("requestfailed", request_failed)
            pause_requested = False
            failure = None
            phase = "login-document"
            release_attempted = False
            release_succeeded = False
            try:
                response = page.goto(clone.child.base + clone.prefix + "/v2/auth/login")
                login_document_ok = response is not None and response.status == 200
                assert login_document_ok
                phase = "credential-fill"
                fill_sensitive(
                    page.get_by_label("用户名", exact=True),
                    clone.accounts["admin"].username,
                )
                fill_sensitive(
                    page.get_by_label("密码", exact=True),
                    clone.accounts["admin"].password,
                )
                # Credentials are read inside the real document, never evaluate arguments.
                phase = "login-fetch"
                login = evaluate(
                    page,
                    """async () => {
                    const form = document.querySelector('form');
                    const username = form.querySelector('input:not([type="password"])');
                    const password = form.querySelector('input[type="password"]');
                    try {
                        const response = await fetch(location.pathname, {method: 'POST', credentials: 'same-origin',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({username: username.value, password: password.value})});
                        return {status: response.status, ok: response.ok};
                    } catch { return {status: 0, ok: false}; }
                    finally { username.value = ''; password.value = ''; }
                }""",
                )
                login_ok = login == {"status": 200, "ok": True}
                assert login_ok
                phase = "credential-clear-check"
                blank_inputs = evaluate(
                    page,
                    """() => Array.from(document.querySelectorAll('form input')).every(input => input.value === '')""",
                )
                assert blank_inputs
                phase = "owned-heartbeat"
                video._arm_worker(clone, worker, fresh=True)
                phase = "owned-task-create"
                ready = evaluate(
                    page,
                    """async ({marker, client, size}) => {
                    const root = location.pathname.slice(0, -'/v2/auth/login'.length);
                    const response = await fetch(root + '/v2/video-publish/tasks', {method: 'POST', credentials: 'same-origin',
                        headers: {'Content-Type': 'application/json', 'X-Requested-With': 'tts-erp'},
                        body: JSON.stringify({clientRequestId: client, filename: marker + '.mp4', caption: marker,
                                              contentType: 'video/mp4', sizeBytes: size})});
                    const data = await response.json();
                    const ready = response.status === 201 && data.clientRequestId === client && data.filename === marker + '.mp4'
                        && String(data.sizeBytes) === String(size) && data.upload?.method === 'PUT' && typeof data.upload.url === 'string';
                    window.TEST_wire = {ticket: ready ? data.upload : null, size, events: 0, loaded: 0, total: 0,
                                        status: 0, done: false, error: false, aborted: false};
                    return {status: response.status, ready};
                }""",
                    {"marker": marker, "client": client_id, "size": SIZE},
                )
                ticket_ready = ready == {"status": 201, "ready": True}
                assert ticket_ready
                phase = "owned-ticket-ready"
                before = gate.observe_store()
                # Record intent before control I/O: a lost control response still releases.
                pause_requested = True
                phase = "pause-control"
                gate.store_control(page, "pause")
                phase = "xhr-start"
                started = evaluate(
                    page,
                    """() => {
                    const state = window.TEST_wire;
                    if (!state?.ticket) return false;
                    const request = new XMLHttpRequest(); state.xhr = request;
                    request.open('PUT', state.ticket.url); request.withCredentials = false;
                    request.setRequestHeader('Content-Type', 'video/mp4');
                    request.upload.onprogress = event => {
                        if (event.lengthComputable) { state.events++; state.loaded = event.loaded; state.total = event.total; }
                    };
                    request.onload = () => { state.status = request.status; state.done = true; };
                    request.onerror = () => { state.error = true; state.done = true; };
                    request.onabort = () => { state.aborted = true; state.done = true; };
                    request.send(new Blob([new Uint8Array(state.size).fill(84)], {type: 'video/mp4'}));
                    return true;
                }""",
                )
                assert started
                phase = "receiver-pause"
                wait_store(page, gate, "paused", True)
                phase = "native-progress"
                page.wait_for_function(
                    """() => {
                    const s = window.TEST_wire; return s.events > 0 && s.loaded > 0 && s.loaded < s.total;
                }""",
                    timeout=5000,
                )
                phase = "progress-release"
                gate.store_control(page, "release")
                phase = "xhr-completion"
                page.wait_for_function("() => window.TEST_wire.done", timeout=10000)
                phase = "wire-facts"
                wire = evaluate(
                    page,
                    """() => {
                    const s = window.TEST_wire;
                    return {status: s.status, done: s.done, error: s.error, aborted: s.aborted,
                            events: s.events, loaded: s.loaded, total: s.total};
                }""",
                )
                wire_ok = (
                    wire["status"] == 200
                    and wire["done"]
                    and not wire["error"]
                    and not wire["aborted"]
                )
                assert wire_ok
                phase = "receiver-completion"
                facts = wait_store(
                    page, gate, "completePuts", before["completePuts"] + 1
                )
                phase = "completion-assertions"
                progress_ok = (
                    wire["events"] > 0
                    and wire["loaded"] == SIZE
                    and wire["total"] == SIZE
                )
                counts_ok = (
                    facts["putCount"] == before["putCount"] + 1
                    and facts["optionsCount"] == before["optionsCount"] + 1
                )
                bytes_ok = (
                    facts["bytesReceived"] - before["bytesReceived"] == SIZE
                    and facts["lastSize"] == SIZE
                )
                hash_ok = facts["lastSha256"] == expected_hash
                credentials_absent = facts["credentialsReceived"] is False
                no_abort = facts["abortedPuts"] == before["abortedPuts"]
                no_confirmation = (
                    sum(
                        method == "POST" and path.endswith("/confirm-upload")
                        for method, path, _csrf in gate.calls
                    )
                    == 0
                )
                one_create = (
                    sum(
                        method == "POST" and path == "/v2/video-publish/tasks"
                        for method, path, _csrf in gate.calls
                    )
                    == 1
                )
                assert progress_ok
                assert counts_ok
                assert bytes_ok
                assert hash_ok
                assert credentials_absent
                assert no_abort
                assert no_confirmation
                assert one_create
            except (
                AssertionError, ValueError, TypeError, OSError, KeyError,
                PlaywrightError, SQLAlchemyError,
            ) as error:
                own_line = 0
                trace = error.__traceback__
                while trace is not None:
                    if trace.tb_frame.f_code.co_filename == __file__:
                        own_line = trace.tb_lineno
                    trace = trace.tb_next
                failure = {"phase": phase, "exceptionClass": type(error).__name__,
                           "ownLine": own_line,
                           "contextClass": type(error.__context__).__name__ if error.__context__ is not None else "",
                           "runtime": runtime, "violations": len(gate.violations)}
                try:
                    failure["store"] = gate.observe_store()
                except (AssertionError, ValueError, TypeError, OSError, KeyError, PlaywrightError, SQLAlchemyError):
                    failure["storeObservationRefused"] = True
            finally:
                if pause_requested:
                    release_attempted = True
                    try:
                        gate.store_control(page, "release")
                        release_succeeded = True
                    except (AssertionError, ValueError, TypeError, OSError, KeyError, PlaywrightError, SQLAlchemyError) as error:
                        if failure is None:
                            own_line = 0
                            trace = error.__traceback__
                            while trace is not None:
                                if trace.tb_frame.f_code.co_filename == __file__:
                                    own_line = trace.tb_lineno
                                trace = trace.tb_next
                            failure = {"phase": "finally-release", "exceptionClass": type(error).__name__, "ownLine": own_line}
            if failure is not None:
                failure["releaseAttempted"] = release_attempted
                failure["releaseSucceeded"] = release_succeeded
                raise AssertionError("Wire completion boundary failed: " + json.dumps(failure, sort_keys=True)) from None
    finally:
        # Known intent predates create, even if the response never reached Python.
        video._purge(clone, [intent])
