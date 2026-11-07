"""Offline HTTP, PostgreSQL races, restart recovery and real FFmpeg acceptance."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from tests.support.runninghub_workflow import graph
from tts_erp_v2.api.v2 import runninghub as api
from tts_erp_v2.db.models.media import DigitalHuman, DigitalHumanAsset, MediaAsset
from tts_erp_v2.db.models.runninghub import (
    CloudQuota,
    GenerationArtifact,
    GenerationAttempt,
    GenerationBatch,
    GenerationJob,
    GenerationProfile,
    VideoTemplate,
    WorkflowVersion,
    new_id,
)
from tts_erp_v2.runninghub.client import RemoteError, RunningHubClient
from tts_erp_v2.runninghub.media import (
    MediaError,
    _run,
    archive_path,
    dry_run_video,
    finalize,
    sha256,
)
from tts_erp_v2.runninghub.planner import (
    PlanError,
    batch_summary,
    build_plan,
    config_snapshot,
    create_batch,
)
from tts_erp_v2.runninghub.repository import recover_and_claim, reserve
from tts_erp_v2.runninghub.worker import Worker
from tts_erp_v2.runninghub.workflow import (
    digest,
)

pytestmark = [pytest.mark.domain_api]


@pytest.fixture()
def setup(db_engine):
    sessions = sessionmaker(bind=db_engine, expire_on_commit=False)
    marker = "TEST_" + new_id()
    with sessions() as session, session.begin():
        version = WorkflowVersion(
            workflow_id="2111762608670990337",
            content_hash=digest({"test": marker}),
            graph=graph(),
            validation={"valid": True},
        )
        human = DigitalHuman(name=marker, status="active")
        session.add_all([version, human])
        session.flush()
        clips = []
        for index in range(6):
            asset = MediaAsset(
                kind="video",
                filename=f"{marker}_{index}.mp4",
                content_type="video/mp4",
                size_bytes=16,
                bucket="TEST_media",
                object_key=f"{marker}/{index}",
                status="ready",
            )
            session.add(asset)
            session.flush()
            session.add(DigitalHumanAsset(digital_human_id=human.id, asset_id=asset.id))
            clips.append(
                {
                    "assetId": str(asset.id),
                    "filename": asset.filename,
                    "contentType": "video/mp4",
                    "sizeBytes": 16,
                    "bucket": "TEST_media",
                    "objectKey": asset.object_key,
                    "etag": "TEST_etag",
                }
            )
        template = VideoTemplate(human_id=human.id, name="A", clips=clips)
        image = MediaAsset(
            kind="image",
            filename=marker + ".png",
            content_type="image/png",
            size_bytes=16,
            bucket="TEST_media",
            object_key=marker + "/image",
            status="ready",
        )
        session.add_all([template, image])
        session.flush()
        asset_ids = [int(clip["assetId"]) for clip in clips] + [image.id]
        profile = session.get(GenerationProfile, "default")
        previous_version_id = profile.workflow_version_id
        profile.workflow_version_id = version.id
        frozen = config_snapshot(profile, {})
        frozen["credentialRef"] = "env:RUNNINGHUB_TEST_API_KEY"
        batch_input = {"assetId": str(image.id), "skuId": marker, "style": marker}
        plan = build_plan(session, [batch_input], [template.id])
        for row in plan:
            row["inputs"]["image"]["etag"] = "TEST_etag"
        batch = create_batch(
            session, marker, "dry-run", frozen, plan, digest(marker), "TEST_operator"
        )
        batch.status = "ACTIVE"
        session.flush()
        job_ids = list(
            session.scalars(
                select(GenerationJob.id).where(GenerationJob.batch_id == marker)
            )
        )
        data = {
            "sessions": sessions,
            "batch": marker,
            "jobs": job_ids,
            "template": template.id,
            "human": str(human.id),
            "image": batch_input,
            "config": frozen,
            "extraBatches": [],
            "extraTemplates": [],
        }
    yield data
    with sessions() as session, session.begin():
        batch_ids = [marker, *data["extraBatches"]]
        owned_jobs = select(GenerationJob.id).where(
            GenerationJob.batch_id.in_(batch_ids)
        )
        owned_attempts = select(GenerationAttempt.id).where(
            GenerationAttempt.job_id.in_(owned_jobs)
        )
        session.execute(
            delete(GenerationArtifact).where(
                GenerationArtifact.attempt_id.in_(owned_attempts)
            )
        )
        session.execute(
            delete(GenerationAttempt).where(GenerationAttempt.job_id.in_(owned_jobs))
        )
        session.execute(
            delete(GenerationJob).where(GenerationJob.batch_id.in_(batch_ids))
        )
        session.execute(
            delete(GenerationBatch).where(GenerationBatch.id.in_(batch_ids))
        )
        # These rows were committed independently of db_session's rollback.
        # Release only this fixture's exact identities before the browser suite
        # checks that it cannot expose another case's media through real APIs.
        session.get(GenerationProfile, "default").workflow_version_id = previous_version_id
        session.flush()
        session.execute(
            delete(VideoTemplate).where(
                VideoTemplate.id.in_([template.id, *data["extraTemplates"]])
            )
        )
        session.execute(delete(DigitalHumanAsset).where(
            DigitalHumanAsset.digital_human_id == human.id,
            DigitalHumanAsset.asset_id.in_(asset_ids),
        ))
        session.execute(delete(DigitalHuman).where(DigitalHuman.id == human.id, DigitalHuman.name == marker))
        session.execute(delete(MediaAsset).where(MediaAsset.id.in_(asset_ids)))
        session.execute(delete(WorkflowVersion).where(WorkflowVersion.id == version.id))


def test_offline_486_jobs_81_groups_and_variants(setup):
    with setup["sessions"]() as session:
        template = session.get(VideoTemplate, setup["template"])
        ids = []
        for human_index in range(3):
            human = DigitalHuman(
                name=f"TEST_person_{new_id()}_{human_index}", status="active"
            )
            session.add(human)
            session.flush()
            for name in "ABC":
                row = VideoTemplate(human_id=human.id, name=name, clips=template.clips)
                session.add(row)
                session.flush()
                ids.append(row.id)
        images = []
        for index in range(9):
            image = MediaAsset(
                kind="image",
                filename=f"TEST_variant_{index}.png",
                content_type="image/png",
                size_bytes=16,
                bucket="TEST_media",
                object_key="TEST_image",
                status="ready",
            )
            session.add(image)
            session.flush()
            images.append(
                {
                    "assetId": str(image.id),
                    "skuId": f"TEST_{index}",
                    "style": "TEST_style",
                }
            )
        plan = build_plan(session, images, ids)
        assert len(plan) == 486
        groups = {}
        for row in plan:
            parent = row["inputs"]["archivePath"].rsplit("/", 1)[0]
            groups.setdefault(parent, []).append(row["inputs"]["clip"])
        assert len(groups) == 81
        assert all(sorted(clips) == list(range(1, 7)) for clips in groups.values())
        with pytest.raises(PlanError, match="COLLISION"):
            build_plan(session, [images[0], images[0]], ids)


def test_duplicate_request_preserves_seeds_and_immutable_config(setup):
    with setup["sessions"]() as session, session.begin():
        original = list(
            session.scalars(
                select(GenerationJob).where(GenerationJob.batch_id == setup["batch"])
            )
        )
        assert len({job.seed for job in original}) == 6
        batch = create_batch(
            session,
            setup["batch"],
            "dry-run",
            {},
            [],
            digest(setup["batch"]),
            "TEST_duplicate",
        )
        assert batch.config == setup["config"]
        assert (
            len(
                list(
                    session.scalars(
                        select(GenerationJob).where(GenerationJob.batch_id == batch.id)
                    )
                )
            )
            == 6
        )
        with pytest.raises(PlanError):
            create_batch(
                session,
                batch.id,
                "dry-run",
                {},
                [],
                "TEST_other_hash",
                "TEST_duplicate",
            )


def test_parallel_workers_share_five_slots_and_unknown_keeps_slot(setup):
    quota = "TEST_quota_" + new_id()

    def claim(job_id):
        with setup["sessions"]() as session, session.begin():
            return reserve(session, job_id, quota, 5)

    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(claim, setup["jobs"]))
    assert sum(bool(c) for c in claims) == 5
    with setup["sessions"]() as session, session.begin():
        attempt = session.get(GenerationAttempt, next(c for c in claims if c))
        session.get(GenerationJob, attempt.job_id).status = "SUBMITTING"
        attempt.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    with setup["sessions"]() as session, session.begin():
        recover_and_claim(session)
    with setup["sessions"]() as session:
        attempt = session.get(GenerationAttempt, next(c for c in claims if c))
        assert session.get(GenerationJob, attempt.job_id).status == "SUBMISSION_UNKNOWN"
        assert attempt.reserved
        remaining = next(
            j for j, c in zip(setup["jobs"], claims, strict=True) if c is None
        )
        assert reserve(session, remaining, quota, 5) is None


def test_pause_blocks_new_claim_but_known_tracking_is_recoverable(setup):
    with setup["sessions"]() as session, session.begin():
        attempt_id = reserve(session, setup["jobs"][0], "TEST_quota_" + new_id(), 5)
        attempt = session.get(GenerationAttempt, attempt_id)
        attempt.task_id, attempt.lease_until = "TEST_cloud_" + new_id(), None
        session.get(GenerationJob, attempt.job_id).status = "RUNNING"
        session.get(GenerationBatch, setup["batch"]).status = "PAUSED"
    with setup["sessions"]() as session, session.begin():
        assert reserve(session, setup["jobs"][1], "TEST_quota_" + new_id(), 5) is None
        assert recover_and_claim(session) is not None


class MemoryStore:
    def __init__(self, root: Path):
        self.root = root

    def fetch(self, source, destination):
        if source["contentType"].startswith("video"):
            shutil.copyfile(self.root / "TEST_reference.mp4", destination)
        else:
            shutil.copyfile(self.root / "TEST_image.png", destination)
        return sha256(destination)

    def put(self, path, key):
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        return {"bucket": "TEST_outputs", "objectKey": key, "sha256": sha256(path)}

    def fetch_artifact(self, artifact, destination):
        shutil.copyfile(self.root / artifact.object_key, destination)

    def freeze(self, source, key):
        return {**source, "etag": "TEST_etag_" + source["assetId"], "objectKey": key}

    def url(self, bucket, key):
        return "https://example.com/TEST.mp4"


@pytest.fixture()
def media_tools(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "RUNNINGHUB_FFMPEG",
        shutil.which("ffmpeg") or str(Path.home() / ".local/bin/ffmpeg"),
    )
    monkeypatch.setenv(
        "RUNNINGHUB_FFPROBE",
        shutil.which("ffprobe") or str(Path.home() / ".local/bin/ffprobe"),
    )
    dry_run_video(tmp_path / "TEST_reference.mp4")
    _run(
        [
            str(Path.home() / ".local/bin/ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=16x16",
            "-frames:v",
            "1",
            "-threads",
            "1",
            str(tmp_path / "TEST_image.png"),
        ]
    )
    return MemoryStore(tmp_path)


def tick(worker, setup, count=1):
    for _ in range(count):
        with setup["sessions"]() as session, session.begin():
            for attempt in session.scalars(
                select(GenerationAttempt)
                .join(GenerationJob, GenerationJob.id == GenerationAttempt.job_id)
                .where(GenerationJob.batch_id == setup["batch"])
            ):
                attempt.next_poll_at = None
        worker.run_once()


def test_dry_run_full_chain_produces_six_exact_finals_and_zip_plan(
    setup, media_tools, monkeypatch
):
    worker = Worker(
        setup["sessions"],
        media_tools,
        Mock(side_effect=AssertionError("must not call RunningHub")),
    )
    tick(worker, setup, 24)
    with setup["sessions"]() as session:
        batch = session.get(GenerationBatch, setup["batch"])
        assert batch.status == "SUCCEEDED"
        jobs = list(
            session.scalars(
                select(GenerationJob).where(GenerationJob.batch_id == batch.id)
            )
        )
        assert all(j.status == "SUCCEEDED" for j in jobs)
        assert len({archive_path(j.inputs) for j in jobs}) == 6
        for job in jobs:
            artifacts = list(
                session.scalars(
                    select(GenerationArtifact).where(
                        GenerationArtifact.attempt_id == job.current_attempt_id
                    )
                )
            )
            assert {a.kind for a in artifacts} == {"raw", "final"}
            final = next(a for a in artifacts if a.kind == "final")
            assert final.spec == {
                "width": 576,
                "height": 1024,
                "fps": "24",
                "frames": 120,
                "seconds": 5.0,
                "audio": False,
            }

    monkeypatch.setattr(api, "get_store", lambda: media_tools)
    with setup["sessions"]() as session:
        response = api.export(setup["batch"], session)
        with zipfile.ZipFile(response.path) as exported:
            assert len(exported.namelist()) == 6
            assert all(
                name.endswith(".mp4") and ".." not in name.split("/")
                for name in exported.namelist()
            )
            assert len({name.rsplit("/", 1)[0] for name in exported.namelist()}) == 1
        response.background.func()


def test_create_timeout_never_resubmits_and_poll_failure_keeps_reservation(
    setup, media_tools, monkeypatch
):
    monkeypatch.setenv("RUNNINGHUB_TEST_API_KEY", "TEST_key_" + new_id())
    fake = Mock()
    fake.key = "TEST_key"
    fake.upload.return_value = "openapi/TEST_uploaded.mp4"
    fake.create.side_effect = RemoteError("SUBMISSION_UNKNOWN", uncertain=True)
    with setup["sessions"]() as session, session.begin():
        session.get(GenerationBatch, setup["batch"]).mode = "live"
    worker = Worker(setup["sessions"], media_tools, lambda key: fake)
    tick(worker, setup, 2)
    assert fake.create.call_count == 1
    with setup["sessions"]() as session, session.begin():
        job = session.scalar(
            select(GenerationJob).where(
                GenerationJob.batch_id == setup["batch"],
                GenerationJob.status == "SUBMISSION_UNKNOWN",
            )
        )
        attempt = session.get(GenerationAttempt, job.current_attempt_id)
        assert attempt.reserved and not attempt.task_id
        attempt.task_id, job.status = "TEST_known_" + new_id(), "RUNNING"
    fake.query.side_effect = RemoteError("UPSTREAM_UNAVAILABLE")
    tick(worker, setup)
    with setup["sessions"]() as session:
        attempt = session.get(GenerationAttempt, attempt.id)
        assert attempt.reserved
        assert session.get(GenerationJob, attempt.job_id).status == "RUNNING"
        assert fake.create.call_count == 1


def test_api_roles_idempotency_resolve_retry_and_export(
    setup, media_tools, monkeypatch
):
    from tts_erp_v2.middleware.auth import AuthMiddleware

    app = FastAPI()
    app.include_router(api.router)
    app.add_middleware(AuthMiddleware)
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
    monkeypatch.setattr(api, "get_store", lambda: media_tools)
    client = TestClient(app)
    rejected = client.put(
        "/v2/runninghub/config", json={"apiKey": "TEST_never_echo_this"}
    )
    assert rejected.status_code == 422 and "TEST_never_echo_this" not in rejected.text
    body = {
        "images": [setup["image"]],
        "templateIds": [setup["template"]],
        "requestId": "TEST_api_" + new_id(),
        "single": True,
    }
    setup["extraBatches"].append(body["requestId"])
    response = client.post("/v2/runninghub/batches", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["mode"] == "dry-run" and response.json()["total"] == 6
    assert response.json()["counts"] == {"PLANNED": 1, "DEFERRED": 5}
    assert (
        client.post("/v2/runninghub/batches", json=body).json()["id"]
        == body["requestId"]
    )
    assert (
        client.post("/v2/runninghub/batches", json={**body, "mode": "live"}).status_code
        == 409
    )
    job = client.get(f"/v2/runninghub/batches/{body['requestId']}/jobs").json()[
        "items"
    ][0]
    assert client.post(f"/v2/runninghub/jobs/{job['id']}/recover").status_code == 409
    assert client.post(f"/v2/runninghub/jobs/{job['id']}/retry").status_code == 409
    assert (
        client.get(f"/v2/runninghub/batches/{body['requestId']}/export").status_code
        == 409
    )
    live = client.post(
        "/v2/runninghub/batches",
        json={**body, "requestId": "TEST_live_" + new_id(), "mode": "live"},
    ).json()
    setup["extraBatches"].append(live["id"])
    assert (
        client.post(f"/v2/runninghub/batches/{live['id']}/start", json={}).status_code
        == 409
    )
    assert (
        client.post(
            f"/v2/runninghub/batches/{live['id']}/start", json={"confirmPaid": True}
        ).status_code
        == 200
    )


def test_mock_official_chain_selects_vertical_not_first_and_local_recovery(
    setup, media_tools, monkeypatch
):
    import tts_erp_v2.runninghub.worker as worker_module

    key = "TEST_RH_" + new_id()
    monkeypatch.setenv("RUNNINGHUB_TEST_API_KEY", key)
    compare = media_tools.root / "TEST_compare.mp4"
    _run(
        [
            str(Path.home() / ".local/bin/ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=1024x576:r=24",
            "-frames:v",
            "124",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            str(compare),
        ]
    )
    counts = {"create": 0, "upload": 0}

    def handler(request):
        if request.url.path.endswith("binary"):
            counts["upload"] += 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "fileName": "openapi/TEST_uploaded" + str(counts["upload"])
                    },
                },
            )
        if request.url.path.endswith("create"):
            counts["create"] += 1
            body = json.loads(request.content)
            assert isinstance(body["workflow"], str)
            assert json.loads(body["workflow"])["136"]["inputs"]["width"] == ["167", 1]
            assert type(body["nodeInfoList"][2]["fieldValue"]) is int
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "taskId": "90071992547409931234567",
                        "taskStatus": "QUEUED",
                        "promptTips": '{"result":true,"node_errors":{}}',
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "SUCCESS",
                "usage": {"consumeCoins": "0.10"},
                "results": [
                    {
                        "url": "https://result.example/compare?sig=TEST%2Fkeep",
                        "outputType": "mp4",
                    },
                    {
                        "fileUrl": "https://result.example/main?sig=TEST%2Fkeep",
                        "fileType": "mp4",
                    },
                ],
            },
        )

    def candidate(url, destination):
        assert "TEST%2Fkeep" in url
        shutil.copyfile(
            compare if "/compare?" in url else media_tools.root / "TEST_reference.mp4",
            destination,
        )

    monkeypatch.setattr(worker_module, "download", candidate)
    with setup["sessions"]() as session, session.begin():
        session.get(GenerationBatch, setup["batch"]).mode = "live"
    worker = Worker(
        setup["sessions"],
        media_tools,
        lambda secret: RunningHubClient(secret, transport=httpx.MockTransport(handler)),
    )
    tick(worker, setup, 2)
    # The official task is complete; a download error must not regenerate it.
    monkeypatch.setattr(
        worker_module, "download", Mock(side_effect=MediaError("TEST_disk_full"))
    )
    tick(worker, setup)
    with setup["sessions"]() as session, session.begin():
        job = session.scalar(
            select(GenerationJob).where(
                GenerationJob.batch_id == setup["batch"],
                GenerationJob.status == "DOWNLOAD_FAILED",
            )
        )
        assert job
        job.status = (
            "DOWNLOADING"  # same transition as the controlled recovery endpoint
        )
        job_id, attempt_id = job.id, job.current_attempt_id
    monkeypatch.setattr(worker_module, "download", candidate)
    tick(worker, setup)
    # Raw is durable; a local encoder failure must also retain this attempt.
    real_finalize = worker_module.finalize
    monkeypatch.setattr(
        worker_module, "finalize", Mock(side_effect=MediaError("TEST_encoder"))
    )
    tick(worker, setup)
    with setup["sessions"]() as session, session.begin():
        job = session.get(GenerationJob, job_id)
        assert job.status == "FINALIZE_FAILED" and job.current_attempt_id == attempt_id
        job.status = "FINALIZING"
    monkeypatch.setattr(worker_module, "finalize", real_finalize)
    monkeypatch.delenv("RUNNINGHUB_TEST_API_KEY")
    tick(worker, setup)
    with setup["sessions"]() as session:
        assert session.get(GenerationJob, job_id).status == "SUCCEEDED"
        assert (
            session.get(GenerationAttempt, attempt_id).task_id
            == "90071992547409931234567"
        )
    assert counts == {"create": 1, "upload": 2}


def test_finalization_retains_optional_audio(media_tools):
    source = media_tools.root / "TEST_audio.mp4"
    _run(
        [
            str(Path.home() / ".local/bin/ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=576x1024:r=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "5.17",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-c:a",
            "aac",
            str(source),
        ]
    )
    spec = finalize(source, media_tools.root / "TEST_audio_final.mp4")
    assert (
        spec["audio"]
        and spec["frames"] == 120
        and spec["fps"] == "24"
        and abs(spec["seconds"] - 5) <= 0.05
    )


def test_download_does_not_leak_key_or_signed_url(tmp_path, monkeypatch, caplog):
    import tts_erp_v2.runninghub.client as module

    real_client = httpx.Client
    observed = []

    def handler(request):
        observed.append(request)
        assert "authorization" not in request.headers
        assert request.url.query == b"sig=TEST%2Fkeep"
        return httpx.Response(200, content=b"TEST_result_bytes")

    monkeypatch.setattr(module, "public_download_url", lambda url: None)
    monkeypatch.setattr(
        module.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    caplog.set_level(logging.INFO, logger="httpx")
    module.download(
        "https://result.example/main.mp4?sig=TEST%2Fkeep", tmp_path / "TEST.mp4"
    )
    assert len(observed) == 1
    assert "TEST%2Fkeep" not in caplog.text


def test_node_errors_keep_returned_task_id_and_unknown_status_holds_quota(
    setup, media_tools, monkeypatch
):
    monkeypatch.setenv("RUNNINGHUB_TEST_API_KEY", "TEST_key")
    fake = Mock()
    fake.key = "TEST_key"
    fake.upload.return_value = "openapi/TEST_file"
    task_id = "TEST_tips_" + new_id()
    fake.create.return_value = {
        "code": 0,
        "data": {
            "taskId": task_id,
            "promptTips": '{"result":false,"node_errors":{"150":"TEST_invalid"}}',
        },
    }
    fake.query.return_value = {"status": "TEST_UNKNOWN_STATE"}
    with setup["sessions"]() as session, session.begin():
        session.get(GenerationBatch, setup["batch"]).mode = "live"
    worker = Worker(setup["sessions"], media_tools, lambda key: fake)
    tick(worker, setup, 2)
    with setup["sessions"]() as session:
        attempt = session.scalar(
            select(GenerationAttempt).where(GenerationAttempt.task_id == task_id)
        )
        assert attempt.reserved and attempt.cloud_status == "TEST_UNKNOWN_STATE"
        assert attempt.receipt["data"]["promptTips"]["node_errors"] == {
            "150": "TEST_invalid"
        }
        assert session.get(GenerationBatch, setup["batch"]).status == "PAUSED"
        assert fake.create.call_count == 1


def test_resolve_known_id_recover_and_new_attempt_only_after_terminal_failure(
    setup, media_tools, monkeypatch
):
    from tts_erp_v2.middleware.auth import AuthMiddleware

    app = FastAPI()
    app.include_router(api.router)
    app.add_middleware(AuthMiddleware)
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
    with setup["sessions"]() as session, session.begin():
        quota_id = "TEST_resolution_" + new_id()
        attempt_id = reserve(session, setup["jobs"][0], quota_id, 5)
        attempt = session.get(GenerationAttempt, attempt_id)
        attempt.lease_until = None
        session.get(GenerationJob, attempt.job_id).status = "SUBMISSION_UNKNOWN"
        session.get(GenerationBatch, setup["batch"]).status = "PAUSED"
    client = TestClient(app)
    path = "/v2/runninghub/jobs/" + setup["jobs"][0]
    assert client.post(path + "/retry").status_code == 409
    task_id = "TEST_resolved_" + new_id()
    resolved = client.post(
        path + "/resolve", json={"taskId": task_id, "note": "TEST_checked_platform"}
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["attempts"][0]["taskId"] == task_id
    with setup["sessions"]() as session, session.begin():
        job = session.get(GenerationJob, setup["jobs"][0])
        job.status = "TRACKING_INTERRUPTED"
    assert client.post(path + "/recover").json()["status"] == "QUEUED"
    with setup["sessions"]() as session, session.begin():
        job = session.get(GenerationJob, setup["jobs"][0])
        job.status = "FAILED"
        attempt = session.get(GenerationAttempt, job.current_attempt_id)
        attempt.cloud_status, attempt.reserved = "FAILED", False
        attempt.usage = {"consumeCoins": "1.23"}
    assert client.post(path + "/retry").status_code == 200
    with setup["sessions"]() as session, session.begin():
        session.get(GenerationBatch, setup["batch"]).status = "ACTIVE"
        new_attempt_id = reserve(session, setup["jobs"][0], quota_id, 5)
        assert new_attempt_id != attempt_id
    detail = client.get(path).json()
    assert len(detail["attempts"]) == 2
    assert detail["attempts"][0]["usage"]["consumeCoins"] == "1.23"
    assert detail["attempts"][0]["taskId"] == task_id


def test_readonly_and_readwrite_http_gates(
    setup, monkeypatch, readonly_key, readwrite_key
):
    from tts_erp_v2.middleware.auth import AuthMiddleware

    app = FastAPI()
    app.include_router(api.router)
    app.add_middleware(AuthMiddleware)
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    client = TestClient(app)
    assert client.get("/v2/runninghub/config").status_code == 401
    assert (
        client.get(
            "/v2/runninghub/config", headers={"Authorization": "Bearer " + readonly_key}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/v2/runninghub/batches",
            headers={"Authorization": "Bearer " + readonly_key},
            json={},
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/v2/runninghub/config",
            headers={"Authorization": "Bearer " + readwrite_key},
            json={},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/v2/runninghub/batches/{setup['batch']}/terminate",
            headers={"Authorization": "Bearer " + readonly_key},
            json={"confirmTerminate": True},
        ).status_code
        == 403
    )


@pytest.fixture()
def runninghub_api(monkeypatch):
    from tts_erp_v2.middleware.auth import AuthMiddleware

    app = FastAPI()
    app.include_router(api.router)
    app.add_middleware(AuthMiddleware)
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
    return TestClient(app)


@pytest.mark.parametrize("clip_count", [1, 3])
def test_first_clip_batch_uses_frozen_template_through_resume_and_zip(
    setup,
    runninghub_api,
    media_tools,
    monkeypatch,
    clip_count,
):
    monkeypatch.setattr(api, "get_store", lambda: media_tools)
    prefix = "/v2/runninghub"
    client = runninghub_api
    sessions = setup["sessions"]
    with sessions() as session, session.begin():
        session.get(GenerationBatch, setup["batch"]).status = "PAUSED"
        original = session.get(VideoTemplate, setup["template"])
        ids = [clip["assetId"] for clip in original.clips[:clip_count]][::-1]

    def save_template(asset_ids):
        response = client.post(
            prefix + "/templates",
            json={"humanId": setup["human"], "name": "A", "assetIds": asset_ids},
        )
        assert response.status_code == 201, response.text
        template = response.json()
        setup["extraTemplates"].append(template["id"])
        return template

    template = save_template(ids)
    assert template["version"] == 2
    assert [clip["assetId"] for clip in template["clips"]] == ids
    duplicate = client.post(
        prefix + "/templates",
        json={"humanId": setup["human"], "name": "A", "assetIds": [ids[0], ids[0]]},
    )
    assert duplicate.status_code == 422
    request = {
        "images": [setup["image"]],
        "templateIds": [template["id"]],
        "single": True,
    }
    preview = client.post(prefix + "/plan", json=request).json()
    assert (preview["total"], preview["fullTotal"], preview["groups"]) == (
        1,
        clip_count,
        1,
    )
    full = client.post(
        prefix + "/plan",
        json={
            **request,
            "single": False,
            "templateIds": [template["id"], setup["template"]],
        },
    ).json()
    assert (full["total"], full["groups"]) == (clip_count + 6, 2)

    batch_id = "TEST_trial_" + new_id()
    setup["extraBatches"].append(batch_id)
    path = prefix + "/batches/" + batch_id
    created = client.post(prefix + "/batches", json={**request, "requestId": batch_id})
    assert created.status_code == 201, created.text
    assert created.json()["templates"][0]["version"] == 2
    original_config = created.json()["config"]
    jobs = client.get(path + "/jobs").json()["items"]
    assert [row["identity"]["clip"] for row in jobs] == list(range(1, clip_count + 1))
    assert [row["status"] for row in jobs] == ["PLANNED"] + ["DEFERRED"] * (
        clip_count - 1
    )
    assert (
        client.post(path + "/continue", json={"confirmPaid": True}).status_code == 409
    )
    assert client.post(path + "/start", json={}).status_code == 200

    worker = Worker(
        sessions,
        media_tools,
        Mock(side_effect=AssertionError("must not call RunningHub")),
    )
    assert all(worker.run_once() for _ in range(4))
    assert not worker.run_once()
    first = client.get(path).json()
    assert first["previewJobId"] == jobs[0]["id"]
    original_job = client.get(prefix + "/jobs/" + jobs[0]["id"]).json()
    assert original_job["status"] == "SUCCEEDED"
    if clip_count == 3:
        assert first["status"] == "FIRST_CLIP_READY"
        assert client.get(path + "/export").status_code == 409
        assert (
            client.post(path + "/resume", json={"confirmPaid": True}).status_code == 409
        )
        updated = save_template(ids[::-1])
        assert updated["version"] == 3
        with sessions() as session, session.begin():
            session.get(GenerationBatch, batch_id).mode = "live"
        assert client.post(path + "/continue", json={}).status_code == 409
        continued = client.post(path + "/continue", json={"confirmPaid": True})
        assert continued.status_code == 200, continued.text
        assert continued.json()["counts"] == {"SUCCEEDED": 1, "PLANNED": 2}
        assert continued.json()["config"] == original_config
        assert (
            client.post(path + "/continue", json={"confirmPaid": True}).status_code
            == 409
        )
        with sessions() as session, session.begin():
            session.get(GenerationBatch, batch_id).mode = "dry-run"
        assert all(worker.run_once() for _ in range(8))
        assert client.get(prefix + "/jobs/" + jobs[0]["id"]).json() == original_job
    else:
        assert first["status"] == "SUCCEEDED"
        assert (
            client.post(path + "/continue", json={"confirmPaid": True}).status_code
            == 409
        )

    assert client.get(path).json()["status"] == "SUCCEEDED"
    completed = client.get(path + "/jobs").json()["items"]
    assert [row["id"] for row in completed] == [row["id"] for row in jobs]
    archive = client.get(path + "/export")
    assert archive.status_code == 200, archive.text
    assert archive.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(archive.content)) as exported:
        assert exported.namelist() == [row["identity"]["archivePath"] for row in jobs]
        assert exported.testzip() is None


def test_termination_fences_preparation_and_waits_for_unknown_submission(
    setup,
    runninghub_api,
):
    termination_api = runninghub_api
    sessions = setup["sessions"]
    batch_path = f"/v2/runninghub/batches/{setup['batch']}"
    preparing_id, unknown_id, succeeded_id, *_ = setup["jobs"]
    with sessions() as session, session.begin():
        preparing = reserve(session, preparing_id, "TEST_stop_" + setup["batch"], 5)
        reserve(session, unknown_id, "TEST_stop_" + setup["batch"], 5)
        session.get(GenerationJob, unknown_id).status = "SUBMISSION_UNKNOWN"
        session.get(GenerationJob, succeeded_id).status = "SUCCEEDED"
        session.get(GenerationJob, setup["jobs"][3]).status = "DEFERRED"

    assert termination_api.post(batch_path + "/terminate", json={}).status_code == 422
    body = {"confirmTerminate": True}
    response = termination_api.post(batch_path + "/terminate", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "TERMINATING"
    assert response.json()["counts"] == {
        "PREPARING": 1,
        "SUCCEEDED": 1,
        "SUBMISSION_UNKNOWN": 1,
        "TERMINATED": 3,
    }
    assert termination_api.post(batch_path + "/resume", json={}).status_code == 409
    assert (
        termination_api.post(f"/v2/runninghub/jobs/{unknown_id}/retry").status_code
        == 409
    )
    worker = Worker(sessions, Mock())
    worker.process(preparing)
    with sessions() as session:
        assert not session.get(GenerationAttempt, preparing).reserved
        assert session.get(GenerationBatch, setup["batch"]).status == "TERMINATING"
    resolution = termination_api.post(
        f"/v2/runninghub/jobs/{unknown_id}/resolve",
        json={"confirmedNotCreated": True, "note": "TEST_checked_platform"},
    )
    assert resolution.status_code == 200, resolution.text
    assert resolution.json()["status"] == "TERMINATED"
    assert not resolution.json()["attempts"][0]["reserved"]
    assert termination_api.post(batch_path + "/terminate", json=body).json()[
        "counts"
    ] == {"SUCCEEDED": 1, "TERMINATED": 5}
    assert termination_api.get(batch_path).json()["status"] == "TERMINATED"


@pytest.mark.parametrize("cloud_finishes", [False, True])
def test_termination_during_create_tracks_original_id_and_preserves_usage(
    setup,
    runninghub_api,
    monkeypatch,
    cloud_finishes,
):
    termination_api = runninghub_api
    key = "TEST_key_" + new_id()
    monkeypatch.setenv("RUNNINGHUB_TEST_API_KEY", key)
    monkeypatch.setattr(Worker, "_uploaded", lambda *_args: "openapi/TEST_uploaded")
    batch_path = f"/v2/runninghub/batches/{setup['batch']}"
    task_id = "9007199254740993" + str(int(cloud_finishes))
    with setup["sessions"]() as session, session.begin():
        session.get(GenerationBatch, setup["batch"]).mode = "live"

    def create(_body):
        response = termination_api.post(
            batch_path + "/terminate", json={"confirmTerminate": True}
        )
        assert response.status_code == 200, response.text
        assert response.json()["counts"] == {"SUBMITTING": 1, "TERMINATED": 5}
        return {"code": 0, "data": {"taskId": task_id, "taskStatus": "QUEUED"}}

    fake = Mock(key=key)
    fake.create.side_effect = create
    fake.query.return_value = {"status": "RUNNING", "usage": {"consumeCoins": "1.23"}}
    fake.cancel.side_effect = RemoteError(
        "UPSTREAM_CANCEL_REJECTED", receipt={"code": 807}
    )
    worker = Worker(setup["sessions"], Mock(), lambda _key: fake)
    tick(worker, setup, 2)
    assert termination_api.get(batch_path).json()["status"] == "TERMINATING"
    with setup["sessions"]() as session:
        attempt = session.scalar(
            select(GenerationAttempt).where(GenerationAttempt.task_id == task_id)
        )
        assert attempt.reserved and attempt.receipt["cancel"]["code"] == 807
        assert attempt.next_poll_at is not None

    fake.cancel.side_effect = None
    fake.cancel.return_value = {"code": 0, "data": None}
    fake.query.return_value = {"status": "SUCCESS" if cloud_finishes else "RUNNING"}
    tick(worker, setup)
    fake.create.assert_called_once()
    assert fake.cancel.call_count == (1 if cloud_finishes else 2)
    assert all(call.args == (task_id,) for call in fake.cancel.call_args_list)
    result = termination_api.get(batch_path).json()
    assert result["costs"] == {"consumeCoins": "1.23"}
    assert result["status"] == ("TERMINATING" if cloud_finishes else "TERMINATED")
    with setup["sessions"]() as session:
        attempt = session.get(GenerationAttempt, attempt.id)
        assert not attempt.reserved
        assert session.get(GenerationJob, attempt.job_id).status == (
            "DOWNLOADING" if cloud_finishes else "TERMINATED"
        )


def test_costs_are_decimal_unknown_separate_from_currency(setup):
    with setup["sessions"]() as session, session.begin():
        quota_id = "TEST_usage_" + new_id()
        session.add(CloudQuota(id=quota_id, limit_count=5))
        session.flush()
        for index, usage in enumerate(
            [
                {"consumeCoins": ".1", "consumeMoney": "2.03", "currency": "CNY"},
                {"consumeCoins": ".2", "consumeMoney": "3.01", "currency": "USD"},
                None,
            ]
        ):
            session.add(
                GenerationAttempt(
                    job_id=setup["jobs"][index],
                    number=1,
                    quota_id=quota_id,
                    task_id="TEST_cost_" + new_id(),
                    reserved=False,
                    usage=usage,
                )
            )
        session.flush()
        summary = batch_summary(session, session.get(GenerationBatch, setup["batch"]))
        assert summary["costs"] == {
            "consumeCoins": "0.3",
            "consumeMoney:CNY": "2.03",
            "consumeMoney:USD": "3.01",
        }
        assert summary["unknownCostTasks"] == 1


@pytest.mark.parametrize("code", [804, 813])
def test_running_or_queued_code_without_task_id_is_not_retryable(
    setup, media_tools, monkeypatch, code
):
    monkeypatch.setenv("RUNNINGHUB_TEST_API_KEY", "TEST_queued_" + new_id())
    fake = Mock()
    fake.key = "TEST_queued_key"
    fake.upload.return_value = "openapi/TEST_file"
    fake.create.return_value = {"code": code, "data": None}
    with setup["sessions"]() as session, session.begin():
        session.get(GenerationBatch, setup["batch"]).mode = "live"
    worker = Worker(setup["sessions"], media_tools, lambda key: fake)
    tick(worker, setup, 2)
    with setup["sessions"]() as session:
        job = session.scalar(
            select(GenerationJob).where(
                GenerationJob.batch_id == setup["batch"],
                GenerationJob.status == "SUBMISSION_UNKNOWN",
            )
        )
        assert job and session.get(GenerationAttempt, job.current_attempt_id).reserved
    assert fake.create.call_count == 1


def test_balance_rejection_pauses_credential_until_admin_resume(
    setup, media_tools, monkeypatch
):
    from tts_erp_v2.middleware.auth import AuthMiddleware

    key = "TEST_balance_" + new_id()
    monkeypatch.setenv("RUNNINGHUB_TEST_API_KEY", key)
    monkeypatch.setenv("RUNNINGHUB_API_KEY", key)
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
    fake = Mock()
    fake.key = key
    fake.upload.return_value = "openapi/TEST_file"
    fake.create.return_value = {"code": 416, "msg": "TEST_insufficient_balance"}
    with setup["sessions"]() as session, session.begin():
        session.get(GenerationBatch, setup["batch"]).mode = "live"
    worker = Worker(setup["sessions"], media_tools, lambda secret: fake)
    tick(worker, setup)
    with setup["sessions"]() as session, session.begin():
        attempt = session.scalar(
            select(GenerationAttempt)
            .join(GenerationJob, GenerationJob.id == GenerationAttempt.job_id)
            .where(GenerationJob.batch_id == setup["batch"])
        )
        assert not attempt.reserved
        assert session.get(CloudQuota, attempt.quota_id).paused_reason
        session.get(GenerationBatch, setup["batch"]).status = "ACTIVE"
        untouched = session.scalar(
            select(GenerationJob.id).where(
                GenerationJob.batch_id == setup["batch"],
                GenerationJob.status == "PLANNED",
            )
        )
        assert reserve(session, untouched, attempt.quota_id, 5) is None
        quota_id = attempt.quota_id
    app = FastAPI()
    app.include_router(api.router)
    app.add_middleware(AuthMiddleware)
    client = TestClient(app)
    assert client.get("/v2/runninghub/config").json()["dispatchPausedReason"]
    assert client.post("/v2/runninghub/dispatch/resume", json={}).status_code == 409
    assert (
        client.post(
            "/v2/runninghub/dispatch/resume", json={"confirmPaid": True}
        ).status_code
        == 200
    )
    with setup["sessions"]() as session, session.begin():
        assert reserve(session, untouched, quota_id, 5)


def test_ambiguous_main_candidates_fail_without_final_artifact(
    setup, media_tools, monkeypatch
):
    import tts_erp_v2.runninghub.worker as worker_module

    monkeypatch.setenv("RUNNINGHUB_TEST_API_KEY", "TEST_ambiguous_" + new_id())
    fake = Mock()
    fake.key = "TEST_key"
    fake.upload.return_value = "openapi/TEST_file"
    fake.create.return_value = {
        "code": 0,
        "data": {"taskId": "TEST_ambiguous_" + new_id()},
    }
    fake.query.return_value = {
        "status": "SUCCESS",
        "results": [
            {"url": "https://result.example/TEST_1", "outputType": "mp4"},
            {"url": "https://result.example/TEST_2", "outputType": "mp4"},
        ],
    }
    monkeypatch.setattr(
        worker_module,
        "download",
        lambda url, target: shutil.copyfile(
            media_tools.root / "TEST_reference.mp4", target
        ),
    )
    with setup["sessions"]() as session, session.begin():
        session.get(GenerationBatch, setup["batch"]).mode = "live"
    worker = Worker(setup["sessions"], media_tools, lambda key: fake)
    tick(worker, setup, 3)
    with setup["sessions"]() as session:
        job = session.scalar(
            select(GenerationJob).where(
                GenerationJob.batch_id == setup["batch"],
                GenerationJob.status == "DOWNLOAD_FAILED",
            )
        )
        assert job.error == "MAIN_VIDEO_AMBIGUOUS"
        assert not list(
            session.scalars(
                select(GenerationArtifact).where(
                    GenerationArtifact.attempt_id == job.current_attempt_id
                )
            )
        )
    assert fake.create.call_count == 1


def test_full_account_does_not_starve_an_unrelated_batch(setup, monkeypatch):
    old_key, new_key = "TEST_old_" + new_id(), "TEST_new_" + new_id()
    monkeypatch.setenv("RUNNINGHUB_TEST_API_KEY", old_key)
    monkeypatch.setenv("RUNNINGHUB_OTHER_API_KEY", new_key)
    quota_id = hashlib.sha256(old_key.encode()).hexdigest()
    other_id = "TEST_other_" + new_id()
    setup["extraBatches"].append(other_id)
    with setup["sessions"]() as session, session.begin():
        session.get(GenerationBatch, setup["batch"]).mode = "live"
        for job_id in setup["jobs"][:5]:
            attempt_id = reserve(session, job_id, quota_id, 5)
            session.get(GenerationJob, job_id).status = "SUBMISSION_UNKNOWN"
            session.get(GenerationAttempt, attempt_id).lease_until = None
        reference = session.get(GenerationJob, setup["jobs"][0]).inputs
        for index in range(60):
            session.add(
                GenerationJob(
                    batch_id=setup["batch"],
                    identity="TEST_extra_" + str(index),
                    inputs=reference,
                    seed=index,
                )
            )
        other = create_batch(
            session,
            other_id,
            "live",
            {**setup["config"], "credentialRef": "env:RUNNINGHUB_OTHER_API_KEY"},
            [{"identity": "TEST_other_identity", "inputs": reference}],
            digest(other_id),
            "TEST_operator",
        )
        other.status = "ACTIVE"
    worker = Worker(setup["sessions"], Mock())
    processed = Mock()
    monkeypatch.setattr(worker, "process", processed)
    assert worker.run_once()
    with setup["sessions"]() as session:
        attempt = session.get(GenerationAttempt, processed.call_args.args[0])
        assert session.get(GenerationJob, attempt.job_id).batch_id == other_id


def test_download_query_keeps_additional_usage_without_double_counting(
    setup, media_tools, monkeypatch
):
    import tts_erp_v2.runninghub.worker as worker_module

    monkeypatch.setenv("RUNNINGHUB_TEST_API_KEY", "TEST_usage_refresh_" + new_id())
    fake = Mock()
    fake.key = "TEST_key"
    fake.upload.return_value = "openapi/TEST_file"
    fake.create.return_value = {
        "code": 0,
        "data": {"taskId": "TEST_usage_refresh_" + new_id()},
    }
    fake.query.side_effect = [
        {"status": "SUCCESS", "usage": {"consumeCoins": "0.10"}},
        {
            "status": "SUCCESS",
            "usage": {"consumeMoney": "0.20", "currency": "CNY"},
            "taskUsageList": [{"consumeCoins": "0.10"}],
            "results": [
                {"url": "https://result.example/TEST_main", "outputType": "mp4"}
            ],
        },
    ]
    monkeypatch.setattr(
        worker_module,
        "download",
        lambda url, target: shutil.copyfile(
            media_tools.root / "TEST_reference.mp4", target
        ),
    )
    with setup["sessions"]() as session, session.begin():
        session.get(GenerationBatch, setup["batch"]).mode = "live"
    worker = Worker(setup["sessions"], media_tools, lambda key: fake)
    tick(worker, setup, 3)
    with setup["sessions"]() as session:
        attempt = session.scalar(
            select(GenerationAttempt)
            .join(GenerationJob, GenerationJob.id == GenerationAttempt.job_id)
            .where(GenerationJob.batch_id == setup["batch"])
        )
        assert attempt.usage == {
            "consumeCoins": "0.10",
            "consumeMoney": "0.20",
            "currency": "CNY",
        }
        assert attempt.receipt["taskUsageList"] == [{"consumeCoins": "0.10"}]
        summary = batch_summary(session, session.get(GenerationBatch, setup["batch"]))
        assert summary["costs"] == {"consumeCoins": "0.10", "consumeMoney:CNY": "0.20"}
    assert fake.create.call_count == 1
