"""Real Chromium XHR/CORS/progress/abort against the single owned receiver."""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from playwright.sync_api import expect

from tests.scripts.test_frontend_remaining_live_api import OwnedClone
from tests.scripts.test_frontend_remaining_live_api import clone as owned_clone
from tests.support.frontend_p2_binding import checked_session
from tests.support.frontend_p2_browser import fill_sensitive, live_browser
from tests.support.frontend_remaining_targets import RemainingTargets
from tts_erp_v2.db.models.publishing import PublishWorkerHeartbeat

clone = owned_clone
pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_integration, pytest.mark.requires_browser]


def wait_fact(page, gate, key, expected):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        facts = gate.observe_store()
        if facts[key] == expected:
            return facts
        page.wait_for_timeout(50)
    pytest.fail('Owned upload observation deadline exceeded: ' + key)


@pytest.mark.parametrize('cancel', [False, True], ids=['complete', 'native-abort'])
def test_browser_native_upload_progress_and_socket_observations(clone: OwnedClone, cancel: bool):
    marker = 'TEST_binary_' + secrets.token_hex(6)
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        session.add(PublishWorkerHeartbeat(instance_id=marker, hostname='TEST_host', pid=12345,
                    status='ready', device_status='ready', started_at=datetime.now(UTC), heartbeat_at=datetime.now(UTC)))
        session.commit()
    with live_browser(clone.probe, clone.accounts['admin'], shop_id='9900123456789012',
                      service_id=marker, enum_value=marker, width=1440,
                      remaining=RemainingTargets(marker=marker)) as (page, gate):
        path = clone.prefix + '/v2/pages/video-publish'
        response = page.goto(clone.child.base + clone.prefix + '/v2/auth/login?' + urlencode({'next': path}))
        assert response is not None and response.status == 200
        page.get_by_label('用户名', exact=True).fill(clone.accounts['admin'].username)
        fill_sensitive(page.get_by_label('密码', exact=True), clone.accounts['admin'].password)
        page.get_by_role('button', name='登录', exact=True).click()
        page.wait_for_url(clone.child.base + path)
        with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
            row = session.get(PublishWorkerHeartbeat, marker)
            assert row is not None
            row.heartbeat_at = datetime.now(UTC)
            session.commit()
        page.get_by_role('button', name='立即刷新', exact=True).click()
        before = gate.observe_store()
        gate.store_control(page, 'pause')
        # Large enough to exceed the native socket's send buffer while store pauses.
        content = b'TEST_binary_' * (2 * 1024 * 1024)
        page.get_by_label('选择 MP4 视频', exact=True).set_input_files(
            {'name': marker + '.mp4', 'mimeType': 'video/mp4', 'buffer': content})
        page.get_by_label('发布文案', exact=True).fill(marker)
        ready = page.get_by_role('button', name='上传并加入发布队列', exact=True)
        try:
            expect(ready).to_be_enabled(timeout=5000)
        except AssertionError:
            facts = page.evaluate("""() => ({
                workerUnavailable: document.body.innerText.includes('发布 Worker 不可用'),
                inputBytes: document.querySelector('input[type=file]').files[0]?.size ?? 0,
                captionLength: document.querySelector('textarea').value.length,
                configInvalid: document.body.innerText.includes('数据格式不符合约定')
            })""")
            pytest.fail('Native upload not ready: ' + str(facts))
        ready.click()
        page.get_by_role('button', name='确认并上传', exact=True).click()
        wait_fact(page, gate, 'paused', True)
        progress = page.get_by_role('progressbar', name='视频上传进度', exact=True)
        expect(progress).to_be_visible()
        # The production component updates this only from native upload.onprogress.
        page.wait_for_function("""() => {
            const p = document.querySelector('progress[aria-label="视频上传进度"]');
            return p && p.value > 0 && p.value < 100;
        }""")
        if cancel:
            page.get_by_role('button', name='取消上传', exact=True).click()
            gate.store_control(page, 'release')
            facts = wait_fact(page, gate, 'abortedPuts', before['abortedPuts'] + 1)
            assert facts['completePuts'] == before['completePuts']
            assert 0 < facts['bytesReceived'] - before['bytesReceived'] < len(content)
        else:
            gate.store_control(page, 'release')
            facts = wait_fact(page, gate, 'completePuts', before['completePuts'] + 1)
            assert facts['lastSize'] == len(content)
            assert facts['lastSha256'] == hashlib.sha256(content).hexdigest()
            expect(page.get_by_role('button', name='确认上传并入队', exact=True)).to_be_visible()
        assert facts['putCount'] == before['putCount'] + 1
        assert facts['optionsCount'] == before['optionsCount'] + 1
        assert facts['credentialsReceived'] is False
        # No queue confirmation occurs implicitly, in success or abort branches.
        assert not any(method == 'POST' and path.endswith('/confirm-upload') for method, path, _ in gate.calls)
