from __future__ import annotations

import hashlib
import re
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import Request, Response
from sqlalchemy import Table
from sqlalchemy.orm import Session

from tts_erp_v2.accounts.pages import required_page_permission
from tts_erp_v2.api.v2 import video_publish
from tts_erp_v2.api.v2.video_publish import _conditional, _etag
from tts_erp_v2.publishing.artemis_client import ArtemisResult
from tts_erp_v2.publishing.dispatcher import (
    PublishDependencies,
    _query_after_submit_transport_error,
)
from tts_erp_v2.publishing.domain import classify_failure
from tts_erp_v2.publishing.object_store import MinioVideoStore, video_store_from_env
from tts_erp_v2.storage.minio_client import MinioClient

pytestmark = [pytest.mark.domain_publishing]

ROOT = Path(__file__).parents[2]


def test_failed_artemis_result_requires_read_only_verification() -> None:
    result = classify_failure(artemis_status="failed", steps_count=4)
    assert result.requires_verification is True
    assert result.retry_safe is None


def test_missing_artemis_session_is_terminal_for_classification() -> None:
    result = ArtemisResult(session_id=uuid4(), status="missing")
    assert result.terminal is True
    classification = classify_failure(artemis_status="missing")
    assert classification.requires_verification is True


def test_detail_api_returns_304_for_matching_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"taskId": "task", "status": "running"}
    tag = _etag(payload)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v2/video-publish/tasks/task",
            "headers": [(b"if-none-match", tag.encode())],
            "api_key_role": "admin",
        }
    )
    monkeypatch.setattr(video_publish, "_task", lambda session, task_id: object())
    monkeypatch.setattr(video_publish, "_snapshot", lambda task, **kwargs: payload)
    monkeypatch.setattr(video_publish, "_queue_position", lambda session, task: None)
    response = video_publish.detail(
        uuid4(), request, cast(Session, None), Response(), include_diagnostics=False
    )
    assert isinstance(response, Response)
    assert response.status_code == 304


def test_detail_conditional_response_returns_304() -> None:
    payload = {"taskId": "task", "status": "running"}
    tag = _etag(payload)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v2/video-publish/tasks/task",
            "headers": [(b"if-none-match", tag.encode())],
        }
    )
    cached = _conditional(request, Response(), payload)
    assert cached is not None
    assert cached.status_code == 304


def test_etag_ignores_fresh_server_time_metadata() -> None:
    first = _etag({"items": [], "serverTime": "2026-10-04T00:00:00Z"})
    second = _etag({"items": [], "serverTime": "2026-10-04T00:00:05Z"})
    assert first == second


def test_video_publish_api_requires_page_permission_for_session_users() -> None:
    assert required_page_permission("/v2/video-publish/tasks") == "page:video-publish"
    assert required_page_permission("/v2/pages/video-publish") == "page:video-publish"


def test_publish_store_rejects_non_dedicated_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    with pytest.raises(ValueError, match="PUBLISH_BUCKET_MISMATCH"):
        MinioVideoStore(cast(MinioClient, SimpleNamespace(bucket="general")))


def test_video_store_from_env_binds_dedicated_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工厂必须把全局 MINIO_BUCKET（SPU 图片桶）切到专用视频桶。

    回归：生产集成时 worker/API 曾用全局 MINIO_BUCKET 构造 store，
    触发 PUBLISH_BUCKET_MISMATCH 而无法启动。
    """
    monkeypatch.setenv("MINIO_ENDPOINT", "127.0.0.1:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_BUCKET", "tts-erp-spu-images")
    monkeypatch.setenv("MINIO_SECURE", "false")
    monkeypatch.setenv("MINIO_REGION", "us-east-1")
    # worktree 默认不带 .env（gitignored），必须自带完整必填项才能跨环境复现
    monkeypatch.setenv("MINIO_PRESIGN_EXPIRY_SECONDS", "900")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")

    store = video_store_from_env()

    assert store.bucket == "tiktok-video"
    # 预签名 URL 必须指向专用桶，而非 SPU 图片桶
    url = store.presign_put("video-publish/x/y.mp4", "video/mp4")
    assert "/tiktok-video/" in url
    assert "/tts-erp-spu-images/" not in url


def test_minio_download_streams_to_private_spool(tmp_path: Path) -> None:
    class Body:
        def stream(self, _size: int):
            yield b"video"

        def close(self) -> None:
            pass

        def release_conn(self) -> None:
            pass

    sdk = SimpleNamespace(get_object=lambda bucket, key, request_headers=None: Body())
    client = cast(
        MinioClient,
        SimpleNamespace(bucket="tiktok-video", _sdk=sdk),
    )
    destination = tmp_path / "video.mp4"
    digest = MinioVideoStore(client).download("video-key", destination, "TEST-etag")
    assert destination.read_bytes() == b"video"
    assert digest == hashlib.sha256(b"video").hexdigest()


@pytest.mark.asyncio
async def test_transport_recovery_queries_before_same_session_resubmit() -> None:
    session_id = uuid4()

    class Artemis:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_task(self, value):
            self.calls.append(f"get:{value}")
            return ArtemisResult(value, "missing")

        async def submit(self, **kwargs):
            self.calls.append(
                f"submit:{kwargs['session_id']}:{kwargs['profile']}:"
                f"{kwargs['verification_level']}"
            )
            return ArtemisResult(kwargs["session_id"], "queued")

    artemis = Artemis()
    result = await _query_after_submit_transport_error(
        cast(
            PublishDependencies,
            SimpleNamespace(
                artemis=artemis,
                artemis_profile="TEST_profile",
                artemis_verification_level="TEST_verification",
            ),
        ),
        session_id,
        "goal",
        "device",
        "com.tiktok",
        "TEST_profile",
        "TEST_verification",
    )
    assert result.status == "queued"
    assert artemis.calls == [
        f"get:{session_id}",
        f"submit:{session_id}:TEST_profile:TEST_verification",
    ]


def test_cleanup_guard_precedes_adb_and_object_deletion() -> None:
    source = (ROOT / "tts_erp_v2/publishing/dispatcher.py").read_text()
    guard = source.index('script_name="video_publish.cleanup_resources"')
    assert guard < source.index("remove_staged_video", guard)
    assert guard < source.index("deps.store.remove", guard)


def test_destructive_guard_refuses_prod_shape_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tts_erp_v2.api.deps import require_destructive_script_guard

    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql://example/tts_erp")
    monkeypatch.delenv("ALLOW_PROD_DESTRUCTIVE", raising=False)
    with pytest.raises(SystemExit):
        require_destructive_script_guard(
            script_name="video_publish.cleanup_object",
            confirmation=True,
            dangerous=True,
        )


def test_concurrency_and_migration_contracts_include_active_protection() -> None:
    repository = (ROOT / "tts_erp_v2/publishing/repository.py").read_text()
    migration = (ROOT / "alembic/versions/0053_video_publish.py").read_text()
    assert "with_for_update(skip_locked=True)" in repository
    assert ".with_for_update()" in repository
    assert "def commit_publish_transition(" in repository
    assert "VideoPublishTask.lease_expires_at > now" in repository
    assert "VideoPublishTask.row_version == token.row_version" in repository
    assert 'cte("cleanup_candidate")' in repository
    assert (
        "status IN ('created','submitting','queued','running','unknown')" in migration
    )


def test_tracked_spool_deletion_has_one_cleanup_executor_owner() -> None:
    dispatcher = (ROOT / "tts_erp_v2/publishing/dispatcher.py").read_text()
    worker = (ROOT / "tts_erp_v2/publishing/worker.py").read_text()
    assert "_mark_spool_cleanup" not in dispatcher
    assert "shutil.rmtree" not in worker
    assert "unlink(" not in worker
    assert dispatcher.count('await run(\n            "spool"') == 1


def test_worker_wires_documented_artemis_and_timing_knobs() -> None:
    source = (ROOT / "tts_erp_v2/publishing/worker.py").read_text()
    assert 'os.environ.get("ARTEMIS_PROFILE", "pro")' in source
    assert '"ARTEMIS_VERIFICATION_LEVEL", "strict"' in source
    assert 'os.environ.get("PUBLISH_POLL_INTERVAL_SECONDS", "2")' in source
    assert "PUBLISH_WORKER_POLL_SECONDS" not in source
    assert 'os.environ.get("PUBLISH_TASK_LEASE_SECONDS", "30")' in source
    assert 'os.environ.get("PUBLISH_WORKER_HEARTBEAT_SECONDS", "5")' in source
    assert "await asyncio.to_thread(" in source
    assert "_cleanup_orphan_spool" in source


def test_queued_content_warning_is_immutable_and_immediate() -> None:
    source = (ROOT / "tts_erp_v2/templates/pages/video-publish.html").read_text()
    assert "设备空闲时任务可能立即开始" in source
    assert "入队后不能修改视频和文案" in source


def test_frontend_keeps_idempotency_and_double_click_guards() -> None:
    source = (ROOT / "tts_erp_v2/static/js/video-publish.js").read_text()
    assert "state.creating=true" in source
    assert (
        "state.clientRequestId = state.clientRequestId || crypto.randomUUID()" in source
    )
    assert "!!state.upload || state.creating" in source
    assert 'headers["If-None-Match"]' in source
    assert "new AbortController()" in source
    assert "document.hidden" in source
    assert "state.currentChannel.failures" in source
    assert "state.listChannel.failures" in source
    assert "state.detailChannel.failures" in source
    assert "scheduleCurrent(mode)" in source
    assert "scheduleList(mode)" in source
    assert "scheduleDetail(mode)" in source
    assert "shared.queued ? 8" in source
    assert "function sharedPollState()" in source
    assert "localStorage.setItem(REFRESH_KEY, mode)" in source
    assert "Array.from(value).length" in source
    assert ".maxLength =" not in source
    assert "[5, 10, 30, 60]" in source
    assert 'window.addEventListener("pagehide", destroy)' in source
    assert "operationalStageStartedAt" in source
    assert "publish-preview-metadata" in source
    assert "task.createdBy" in source
    assert "navigator.clipboard.writeText" in source
    assert 'document.execCommand("copy")' in source


def test_publish_model_metadata_matches_schema_indexes_and_constraints() -> None:
    from tts_erp_v2.db.models.publishing import (
        VideoPublishAttempt,
        VideoPublishTask,
    )

    task_table = cast(Table, VideoPublishTask.__table__)
    attempt_table = cast(Table, VideoPublishAttempt.__table__)
    task_indexes = {str(index.name): index for index in task_table.indexes}
    attempt_indexes = {str(index.name): index for index in attempt_table.indexes}
    task_constraints = {str(constraint.name) for constraint in task_table.constraints}
    attempt_constraints = {
        str(constraint.name) for constraint in attempt_table.constraints
    }
    assert "DESC" in str(task_indexes["ix_video_publish_history"].expressions[0])
    assert "DESC" in str(task_indexes["ix_video_publish_history"].expressions[1])
    assert "ix_video_publish_cleanup_queue" in task_indexes
    assert "ix_video_publish_attempt_task_seq" in attempt_indexes
    assert "video_publish_task_cleanup_status_check" in task_constraints
    assert "video_publish_attempt_related_check" in attempt_constraints
    assert "uq_video_publish_attempt_task_seq" in attempt_constraints


def test_publish_responsive_accessibility_contract_uses_existing_css() -> None:
    css = (ROOT / "tts_erp_v2/static/css/video-publish.css").read_text()
    template = (ROOT / "tts_erp_v2/templates/pages/video-publish.html").read_text()
    assert "max-width:1440px" in css
    assert "max-width:900px" in css
    assert "max-width:390px" in css
    assert "prefers-reduced-motion:reduce" in css
    assert 'class="btn-icon drawer-close"' in template
    assert 'id="publish-preview-metadata"' in template
    assert 'maxlength="4000"' not in template


def _css_rules(css: str) -> dict[str, str]:
    """Flatten minified CSS into ``selector -> declaration block``.

    ``@media`` wrappers are ignored (their inner rules are kept), so an
    assertion cannot be satisfied or broken merely by where a rule sits.
    """
    rules: dict[str, str] = {}
    without_comments = re.sub(r"/\*.*?\*/", " ", css, flags=re.DOTALL)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", without_comments):
        for selector in match.group(1).split(","):
            key = " ".join(selector.split())
            rules.setdefault(key, match.group(2).strip())
    return rules


def test_modal_dialogs_use_the_shared_op_dialog_component() -> None:
    """模态弹窗的外观只有一个 owner：common.css 的 ``.op-dialog``。

    原生 ``<dialog>`` 的 UA 默认样式不消费本站令牌：白底黑框、16px padding、
    ``h2`` 落到 32px、``::backdrop`` 只有 10% 黑。页面内联一份 ``.op-dialog`` 就等于
    制造第二个 owner（曾经就发生过：shops.html 内联一份，video-publish 一份都没有）。
    """
    common = (ROOT / "tts_erp_v2/static/css/common.css").read_text()
    rules = _css_rules(common)
    surface = rules.get(".op-dialog", "")
    assert surface, "common.css 缺少 .op-dialog 共享组件"
    assert "var(--paper)" in surface and "var(--ink)" in surface
    assert "var(--rule)" in surface and "var(--radius)" in surface
    assert re.search(r"padding:\s*[^;}]+", surface)
    assert rules.get(".op-dialog::backdrop"), ".op-dialog 缺少遮罩样式"
    assert "var(--serif)" in rules.get(".op-dialog h2", "")

    for name in ("video-publish.html", "shops.html"):
        template = (ROOT / f"tts_erp_v2/templates/pages/{name}").read_text()
        assert 'class="op-dialog"' in template, f"{name} 的弹窗未接入共享组件"
        assert "common.css" in template

    # 页面模板不得再内联表面/遮罩/标题（common.css 头部约定）。
    shops = (ROOT / "tts_erp_v2/templates/pages/shops.html").read_text()
    assert ".op-dialog {" not in shops
    assert "::backdrop" not in shops
    assert ".op-dialog .form-field" in shops, "页面特有的表单字段排版应留在页面里"


def test_publish_confirm_dialog_layout_and_copy_match_design_spec() -> None:
    """确认框的内部排版归页面 CSS，文案归设计文档 §22.8。"""
    css = (ROOT / "tts_erp_v2/static/css/video-publish.css").read_text()
    rules = _css_rules(css)
    template = (ROOT / "tts_erp_v2/templates/pages/video-publish.html").read_text()

    # 表面已上移到 common.css，页面只补长文案滚动 + 两列动作行 + 元数据网格。
    # （_css_rules 按逗号拆开分组选择器，所以成对出现的选择器要合并后再断言。）
    def merged(*selectors: str) -> str:
        return ";".join(rules.get(selector, "") for selector in selectors)

    assert "max-height:calc(100vh - 48px)" in merged(
        "#publish-confirm-dialog", "#publish-action-dialog"
    )
    assert "display:grid" in merged(
        "#publish-confirm-dialog>form", "#publish-action-dialog>form"
    )
    assert "grid-column:1/-1" in rules.get("#publish-confirm-dialog h2", "")
    assert "grid-column:1/-1" in rules.get("#publish-action-dialog h2", "")
    assert "grid-column:1/-1" in rules.get("#publish-confirm-preview", "")
    assert "grid-column:1/-1" in rules.get(".publish-confirm-warning", "")
    assert "display:grid" in rules.get("#publish-confirm-dialog dl", "")
    assert "#publish-confirm-dialog button:first-of-type" in css
    assert "min-height:44px" in css

    # 设计文档 22.8「提交确认」逐项文案。
    assert "<h2>确认加入发布队列</h2>" in template
    assert "设备空闲时任务可能立即开始，入队后不能修改视频和文案。" in template
    assert 'class="btn-secondary">返回修改</button>' in template
    assert 'class="btn-primary">确认并上传</button>' in template
