"""ERP API for generation; RunningHub secrets and signed receipts stay server-side."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from minio.error import S3Error
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from tts_erp_v2.api.deps import get_session, require_cookie_csrf, require_role_at_least
from tts_erp_v2.db.models.media import DigitalHuman
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
from tts_erp_v2.media.repository import asset_ids_for_digital_human
from tts_erp_v2.proxy.errors import DecryptionError, SigningError
from tts_erp_v2.proxy.token_service import load_runninghub_api_key
from tts_erp_v2.runninghub.client import RemoteError, RunningHubClient
from tts_erp_v2.runninghub.media import MediaError, ObjectStore
from tts_erp_v2.runninghub.planner import (
    PlanError,
    asset_snapshot,
    batch_summary,
    build_plan,
    config_snapshot,
    create_batch,
    max_batch_jobs,
    template_versions,
)
from tts_erp_v2.runninghub.repository import (
    STOPPING_BATCH_STATES,
    complete_batch,
    request_termination,
)
from tts_erp_v2.runninghub.workflow import SPEC, WorkflowError, digest, validate_graph
from tts_erp_v2.storage.minio_client import MinioConfigError

SessionDep = Annotated[Session, Depends(get_session)]


class SafeInputRoute(APIRoute):
    """Pydantic input/ctx may echo credentials accidentally posted by a client."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {
                                "loc": item["loc"],
                                "type": item["type"],
                                "msg": "Invalid request field",
                            }
                            for item in error.errors()
                        ]
                    },
                )

        return safe_handler


def gate(request: Request) -> None:
    role = "readonly" if request.method == "GET" else "readwrite"
    require_role_at_least(request, role)
    if request.method != "GET":
        require_cookie_csrf(request)


router = APIRouter(
    prefix="/v2/runninghub",
    tags=["runninghub"],
    dependencies=[Depends(gate)],
    route_class=SafeInputRoute,
)


def get_store() -> ObjectStore:
    return ObjectStore()


def _profile(session: Session) -> GenerationProfile:
    row = session.get(GenerationProfile, "default")
    if not row:
        raise HTTPException(503, "RUNNINGHUB_MIGRATION_REQUIRED")
    return row


def _client(session: Session, profile: GenerationProfile) -> RunningHubClient:
    try:
        key = load_runninghub_api_key(session, profile.credential_ref)
        return RunningHubClient(
            key, os.environ.get("RUNNINGHUB_BASE_URL", "https://www.runninghub.cn")
        )
    except (SigningError, DecryptionError, ValueError):
        raise HTTPException(503, "CREDENTIAL_UNAVAILABLE") from None


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProfileIn(Input):
    credentialRef: str = Field(default="env:RUNNINGHUB_API_KEY", max_length=150)
    workflowId: str = "2111762608670990337"
    instanceType: Literal["default", "plus", "ultra"] = "plus"
    concurrency: int = Field(default=5, ge=1, le=5)


class CheckIn(Input):
    graph: dict[str, Any] | None = None


class TemplateIn(Input):
    humanId: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    name: Literal["A", "B", "C"]
    assetIds: list[Annotated[str, Field(pattern=r"^[1-9][0-9]{0,18}$")]] = Field(
        min_length=1
    )


class ImageIn(Input):
    source: Literal["media", "spu"] = "media"
    assetId: str = Field(pattern=r"^[1-9][0-9]*$")
    skuId: str = Field(min_length=1, max_length=120)
    style: str = Field(min_length=1, max_length=120)
    skuLabel: str | None = Field(default=None, max_length=120)


class PlanIn(Input):
    images: list[ImageIn] = Field(min_length=1, max_length=200)
    templateIds: list[str] = Field(min_length=1, max_length=100)
    single: bool = False


class BatchIn(PlanIn):
    requestId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    mode: Literal["dry-run", "live"] = "dry-run"
    instructions: dict[Literal["173", "174", "185"], str] = Field(default_factory=dict)


class StartIn(Input):
    confirmPaid: bool = False


class TerminateIn(Input):
    confirmTerminate: Literal[True]


class ResolutionIn(Input):
    taskId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")
    confirmedNotCreated: bool = False
    note: str = Field(min_length=1, max_length=500)


def _actor(request: Request) -> str:
    return (
        "user:" + str(request.scope.get("user_id"))
        if request.scope.get("user_id")
        else "key:" + str(request.scope.get("api_key_id", "operator"))
    )


@router.get("/config")
def config(session: SessionDep) -> dict:
    profile = _profile(session)
    try:
        key = load_runninghub_api_key(session, profile.credential_ref)
        configured = True
    except (SigningError, DecryptionError):
        configured = False
        key = None
    quota = (
        session.get(CloudQuota, hashlib.sha256(key.encode()).hexdigest())
        if key
        else None
    )
    return {
        "credentialRef": profile.credential_ref,
        "credentialConfigured": configured,
        "dispatchPausedReason": quota.paused_reason if quota else None,
        "workflowId": profile.workflow_id,
        "instanceType": profile.instance_type,
        "concurrency": profile.concurrency,
        "workflowVersionId": profile.workflow_version_id,
        "spec": SPEC,
        "defaultMode": "dry-run",
    }


@router.put("/config")
def set_config(body: ProfileIn, request: Request, session: SessionDep) -> dict:
    require_role_at_least(request, "admin")
    if not re.fullmatch(
        r"env:RUNNINGHUB_[A-Z0-9_]*API_KEY|integration:[A-Za-z0-9_-]+",
        body.credentialRef,
    ):
        raise HTTPException(422, "INVALID_CREDENTIAL_REFERENCE")
    if body.workflowId != "2111762608670990337":
        raise HTTPException(422, "WORKFLOW_MAPPING_REQUIRED")
    profile = _profile(session)
    profile.credential_ref, profile.workflow_id = body.credentialRef, body.workflowId
    profile.instance_type, profile.concurrency = body.instanceType, body.concurrency
    try:
        key = load_runninghub_api_key(session, profile.credential_ref)
    except (SigningError, DecryptionError):
        key = None
    if key:
        quota = session.scalar(
            select(CloudQuota)
            .where(CloudQuota.id == hashlib.sha256(key.encode()).hexdigest())
            .with_for_update()
        )
        if quota:
            # Only an explicit admin configuration can raise a previously lowered cap.
            quota.limit_count = body.concurrency
    session.commit()
    return config(session)


@router.post("/account")
def account(request: Request, session: SessionDep) -> dict:
    require_role_at_least(request, "admin")
    client = _client(session, _profile(session))
    try:
        return client.account()
    except RemoteError:
        raise HTTPException(502, "RUNNINGHUB_ACCOUNT_CHECK_FAILED") from None
    finally:
        client.close()


@router.post("/dispatch/resume")
def resume_dispatch(body: StartIn, request: Request, session: SessionDep) -> dict:
    require_role_at_least(request, "admin")
    if not body.confirmPaid:
        raise HTTPException(409, "EXPLICIT_PAID_CONFIRMATION_REQUIRED")
    try:
        key = load_runninghub_api_key(session, _profile(session).credential_ref)
    except (SigningError, DecryptionError):
        raise HTTPException(503, "CREDENTIAL_UNAVAILABLE") from None
    quota = session.scalar(
        select(CloudQuota)
        .where(CloudQuota.id == hashlib.sha256(key.encode()).hexdigest())
        .with_for_update()
    )
    if quota:
        quota.paused_reason = None
    session.commit()
    return {"resumed": True}


@router.post("/workflow/check")
def check_workflow(body: CheckIn, request: Request, session: SessionDep) -> dict:
    require_role_at_least(request, "admin")
    profile = _profile(session)
    try:
        graph: Any = body.graph
        if graph is None:
            client = _client(session, profile)
            try:
                graph = client.workflow(profile.workflow_id)
            finally:
                client.close()
        graph = validate_graph(graph, profile.workflow_id)
    except (WorkflowError, RemoteError) as error:
        raise HTTPException(422, str(error)) from None
    content_hash = digest(graph)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:id,0))"),
        {"id": "rh-workflow:" + content_hash},
    )
    version = session.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == profile.workflow_id,
            WorkflowVersion.content_hash == content_hash,
        )
    )
    if not version:
        version = WorkflowVersion(
            workflow_id=profile.workflow_id,
            content_hash=content_hash,
            graph=graph,
            validation={"valid": True, "spec": SPEC},
        )
        session.add(version)
        session.flush()
    profile.workflow_version_id = version.id
    session.commit()
    return {
        "workflowVersionId": version.id,
        "contentHash": content_hash,
        "valid": True,
        "spec": SPEC,
    }


@router.get("/templates")
def templates(
    session: SessionDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    versions = template_versions()
    rows = session.execute(
        select(VideoTemplate, DigitalHuman.name, versions.c.version)
        .join(DigitalHuman, DigitalHuman.id == VideoTemplate.human_id)
        .join(versions, versions.c.id == VideoTemplate.id)
        .where(DigitalHuman.deleted_at.is_(None))
        .order_by(VideoTemplate.created_at.desc(), VideoTemplate.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {"items": [_template_out(t, name, version) for t, name, version in rows]}


def _template_out(template: VideoTemplate, human_name: str, version: int) -> dict:
    return {
        "id": template.id,
        "humanId": str(template.human_id),
        "humanName": human_name,
        "name": template.name,
        "version": version,
        "clips": [
            {"assetId": clip["assetId"], "filename": clip["filename"]}
            for clip in template.clips
        ],
    }


@router.post("/templates", status_code=201)
def add_template(body: TemplateIn, session: SessionDep) -> dict:
    human = session.get(DigitalHuman, int(body.humanId))
    if not human or human.deleted_at or human.status != "active":
        raise HTTPException(422, "ACTIVE_HUMAN_REQUIRED")
    if len(body.assetIds) > max_batch_jobs():
        raise HTTPException(422, "BATCH_SIZE_EXCEEDED")
    if len(set(body.assetIds)) != len(body.assetIds):
        raise HTTPException(422, "DISTINCT_REFERENCE_CLIPS_REQUIRED")
    linked = set(asset_ids_for_digital_human(session, human.id, include_pack_videos=True))
    try:
        sources = [asset_snapshot(session, i, "video") for i in body.assetIds]
        if any(int(source["assetId"]) not in linked for source in sources):
            raise PlanError("CLIPS_MUST_BELONG_TO_HUMAN")
        template_id = new_id()
        store = get_store()
        clips = [
            store.freeze(s, f"sources/templates/{template_id}/{index}")
            for index, s in enumerate(sources, 1)
        ]
        if len({clip["etag"] for clip in clips}) != len(clips):
            raise PlanError("DISTINCT_VIDEO_CONTENTS_REQUIRED")
    except PlanError as error:
        raise HTTPException(422, str(error)) from None
    except (S3Error, MinioConfigError, MediaError, OSError):
        raise HTTPException(503, "SOURCE_SNAPSHOT_FAILED") from None
    # Publish a version in order even when multiple operators finish copying at once.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"rh-template:{human.id}:{body.name}"},
    )
    row = VideoTemplate(
        id=template_id,
        human_id=human.id,
        name=body.name,
        clips=clips,
        created_at=func.clock_timestamp(),
    )
    session.add(row)
    session.flush()
    versions = template_versions()
    version = session.scalar(select(versions.c.version).where(versions.c.id == row.id))
    result = _template_out(row, human.name, version)
    session.commit()
    return result


def _plan(body: PlanIn, session: Session) -> list[dict]:
    try:
        return build_plan(
            session,
            [image.model_dump() for image in body.images],
            body.templateIds,
            single=body.single,
        )
    except PlanError as error:
        raise HTTPException(422, str(error)) from None


@router.post("/plan")
def preview_plan(body: PlanIn, session: SessionDep) -> dict:
    plan = _plan(body, session)
    paths = [row["inputs"]["archivePath"] for row in plan]
    return {
        "total": 1 if body.single else len(plan),
        "fullTotal": len(plan),
        "groups": len({path.rsplit("/", 1)[0] for path in paths}),
        "spec": SPEC,
        "paths": paths[:1] if body.single else paths,
        "single": body.single,
    }


@router.post("/batches", status_code=201)
def add_batch(body: BatchIn, request: Request, session: SessionDep) -> dict:
    request_hash = digest(body.model_dump())
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:id,0))"),
        {"id": "rh-plan:" + body.requestId},
    )
    existing = session.get(GenerationBatch, body.requestId)
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(409, "REQUEST_ID_REUSED_WITH_DIFFERENT_PLAN")
        return batch_summary(session, existing)
    plan = _plan(body, session)
    try:
        frozen_config = config_snapshot(_profile(session), body.instructions)
        store = get_store()
        images: dict[str, dict] = {}
        for item in plan:
            source = item["inputs"]["image"]
            asset_id = source["assetId"]
            if asset_id not in images:
                images[asset_id] = store.freeze(
                    source, f"sources/batches/{body.requestId}/{asset_id}"
                )
            item["inputs"]["image"] = images[asset_id]
        batch = create_batch(
            session,
            body.requestId,
            body.mode,
            frozen_config,
            plan,
            request_hash,
            _actor(request),
            single=body.single,
        )
    except PlanError as error:
        raise HTTPException(422, str(error)) from None
    except (S3Error, MinioConfigError, MediaError, OSError, SQLAlchemyError):
        raise HTTPException(503, "SOURCE_SNAPSHOT_FAILED") from None
    session.commit()
    return batch_summary(session, batch)


def _batch(session: Session, batch_id: str, *, lock: bool = False) -> GenerationBatch:
    query = select(GenerationBatch).where(GenerationBatch.id == batch_id)
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, "BATCH_NOT_FOUND")
    return row


@router.get("/batches")
def batches(
    session: SessionDep,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    rows = session.scalars(
        select(GenerationBatch)
        .order_by(GenerationBatch.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return {"items": [batch_summary(session, b) for b in rows]}


@router.get("/batches/{batch_id}")
def batch_detail(batch_id: str, session: SessionDep) -> dict:
    return batch_summary(session, _batch(session, batch_id))


@router.post("/batches/{batch_id}/terminate")
def terminate_batch(batch_id: str, body: TerminateIn, session: SessionDep) -> dict:
    batch = _batch(session, batch_id, lock=True)
    if batch.status == "SUCCEEDED":
        raise HTTPException(409, "BATCH_ALREADY_COMPLETE")
    if batch.status not in STOPPING_BATCH_STATES:
        request_termination(session, batch)
        session.commit()
    return batch_summary(session, batch)


@router.post("/batches/{batch_id}/continue")
def continue_batch(batch_id: str, body: StartIn, session: SessionDep) -> dict:
    batch = _batch(session, batch_id, lock=True)
    if batch.status != "FIRST_CLIP_READY":
        raise HTTPException(409, "FIRST_CLIP_READY_REQUIRED")
    if batch.mode == "live" and not body.confirmPaid:
        raise HTTPException(409, "EXPLICIT_PAID_CONFIRMATION_REQUIRED")
    session.execute(
        update(GenerationJob)
        .where(GenerationJob.batch_id == batch.id, GenerationJob.status == "DEFERRED")
        .values(status="PLANNED")
    )
    batch.status, batch.error = "ACTIVE", None
    session.commit()
    return batch_summary(session, batch)


@router.post("/batches/{batch_id}/{action}")
def batch_action(
    batch_id: str,
    action: Literal["start", "pause", "resume"],
    body: StartIn,
    session: SessionDep,
) -> dict:
    batch = _batch(session, batch_id, lock=True)
    if batch.status == "SUCCEEDED":
        raise HTTPException(409, "BATCH_ALREADY_COMPLETE")
    if batch.status in STOPPING_BATCH_STATES:
        raise HTTPException(409, "BATCH_TERMINATION_REQUESTED")
    if batch.status == "FIRST_CLIP_READY":
        raise HTTPException(409, "EXPLICIT_CONTINUATION_REQUIRED")
    if action != "pause" and batch.mode == "live" and not body.confirmPaid:
        raise HTTPException(409, "EXPLICIT_PAID_CONFIRMATION_REQUIRED")
    batch.status, batch.error = ("PAUSED" if action == "pause" else "ACTIVE"), None
    session.commit()
    return batch_summary(session, batch)


@router.get("/batches/{batch_id}/jobs")
def jobs(
    batch_id: str,
    session: SessionDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    _batch(session, batch_id)
    rows = session.scalars(
        select(GenerationJob)
        .where(GenerationJob.batch_id == batch_id)
        .order_by(
            GenerationJob.inputs["imageId"].astext,
            GenerationJob.inputs["templateId"].astext,
            GenerationJob.inputs["clip"].as_integer(),
            GenerationJob.id,
        )
        .limit(limit)
        .offset(offset)
    )
    return {"items": [_job_out(job) for job in rows]}


def _job_out(job: GenerationJob) -> dict:
    return {
        "id": job.id,
        "batchId": job.batch_id,
        "status": job.status,
        "error": job.error,
        "seed": job.seed,
        "identity": {
            k: v for k, v in job.inputs.items() if k not in {"image", "video"}
        },
        "referenceFilename": job.inputs["video"]["filename"],
        "attemptId": job.current_attempt_id,
    }


def _job(session: Session, job_id: str) -> GenerationJob:
    row = session.get(GenerationJob, job_id)
    if not row:
        raise HTTPException(404, "JOB_NOT_FOUND")
    return row


def _lock_batch_job(
    session: Session, job_id: str
) -> tuple[GenerationBatch, GenerationJob]:
    batch = _batch(session, _job(session, job_id).batch_id, lock=True)
    job = session.scalar(
        select(GenerationJob)
        .where(GenerationJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return batch, job


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, session: SessionDep) -> dict:
    job = _job(session, job_id)
    attempts = session.scalars(
        select(GenerationAttempt)
        .where(GenerationAttempt.job_id == job.id)
        .order_by(GenerationAttempt.number)
    )
    return {
        **_job_out(job),
        "attempts": [
            {
                "id": a.id,
                "number": a.number,
                "taskId": a.task_id,
                "cloudStatus": a.cloud_status,
                "reserved": a.reserved,
                "requestHash": a.request_hash,
                "assetHashes": a.asset_hashes,
                "receipt": a.receipt,
                "usage": a.usage,
                "error": a.error,
                "lastPolledAt": a.last_polled_at,
                "resolution": a.resolution,
            }
            for a in attempts
        ],
    }


@router.post("/jobs/{job_id}/resolve")
def resolve(
    job_id: str, body: ResolutionIn, request: Request, session: SessionDep
) -> dict:
    require_role_at_least(request, "admin")
    batch, job = _lock_batch_job(session, job_id)
    if (
        job.status != "SUBMISSION_UNKNOWN"
        or bool(body.taskId) == body.confirmedNotCreated
    ):
        raise HTTPException(409, "UNKNOWN_SUBMISSION_REQUIRES_ONE_RESOLUTION")
    attempt = session.get(GenerationAttempt, job.current_attempt_id)
    if attempt.task_id or (
        body.taskId
        and session.scalar(
            select(GenerationAttempt.id).where(GenerationAttempt.task_id == body.taskId)
        )
    ):
        raise HTTPException(409, "TASK_ID_ALREADY_ASSOCIATED")
    attempt.resolution = {
        "actor": _actor(request),
        "note": body.note,
        "confirmedNotCreated": body.confirmedNotCreated,
        "at": session.scalar(select(func.now())).isoformat(),
    }
    if body.taskId:
        attempt.task_id, job.status, attempt.cloud_status = (
            body.taskId,
            "QUEUED",
            "UNVERIFIED",
        )
    else:
        attempt.reserved, job.status, attempt.cloud_status = (
            False,
            "TERMINATED" if batch.status in STOPPING_BATCH_STATES else "FAILED",
            "CONFIRMED_NOT_CREATED",
        )
    attempt.lease_token, attempt.lease_until, attempt.next_poll_at = (
        new_id(),
        None,
        None,
    )
    job.error = None
    if batch.status == "TERMINATING":
        complete_batch(session, batch)
    session.commit()
    return job_detail(job.id, session)


@router.post("/jobs/{job_id}/recover")
def recover(job_id: str, session: SessionDep) -> dict:
    _, job = _lock_batch_job(session, job_id)
    attempt = (
        session.get(GenerationAttempt, job.current_attempt_id)
        if job.current_attempt_id
        else None
    )
    phases = {
        "DOWNLOAD_FAILED": "DOWNLOADING",
        "FINALIZE_FAILED": "FINALIZING",
        "TRACKING_INTERRUPTED": "QUEUED",
    }
    if job.status not in phases or not attempt or not attempt.task_id:
        raise HTTPException(409, "KNOWN_TASK_LOCAL_OR_TRACKING_FAILURE_REQUIRED")
    job.status, job.error = phases[job.status], None
    (
        attempt.lease_token,
        attempt.lease_until,
        attempt.next_poll_at,
        attempt.poll_failures,
    ) = new_id(), None, None, 0
    session.commit()
    return job_detail(job.id, session)


@router.post("/jobs/{job_id}/retry")
def retry(job_id: str, session: SessionDep) -> dict:
    batch, job = _lock_batch_job(session, job_id)
    if batch.status in STOPPING_BATCH_STATES:
        raise HTTPException(409, "BATCH_TERMINATION_REQUESTED")
    attempt = (
        session.get(GenerationAttempt, job.current_attempt_id)
        if job.current_attempt_id
        else None
    )
    if job.status not in {"FAILED", "PREPARATION_FAILED"} or (
        attempt and attempt.reserved
    ):
        raise HTTPException(409, "ONLY_CONFIRMED_FAILURE_CAN_REGENERATE")
    # Regeneration is explicit and produces another attempt when the batch is resumed.
    job.status, job.error = "PLANNED", None
    session.commit()
    return job_detail(job.id, session)


@router.get("/jobs/{job_id}/artifacts")
def artifacts(job_id: str, session: SessionDep) -> dict:
    job = _job(session, job_id)
    rows = session.scalars(
        select(GenerationArtifact).where(
            GenerationArtifact.attempt_id == job.current_attempt_id
        )
    )
    store = get_store()
    return {
        "items": [
            {
                "id": row.id,
                "kind": row.kind,
                "spec": row.spec,
                "sha256": row.sha256,
                "url": store.url(row.bucket, row.object_key),
            }
            for row in rows
        ]
    }


@router.get("/batches/{batch_id}/export")
def export(batch_id: str, session: SessionDep) -> FileResponse:
    batch = _batch(session, batch_id)
    if batch.status != "SUCCEEDED":
        raise HTTPException(409, "EXPORT_REQUIRES_ALL_FINAL_VIDEOS")
    finals = session.execute(
        select(GenerationJob, GenerationArtifact)
        .outerjoin(
            GenerationArtifact,
            and_(
                GenerationArtifact.attempt_id == GenerationJob.current_attempt_id,
                GenerationArtifact.kind == "final",
            ),
        )
        .where(GenerationJob.batch_id == batch_id)
    ).all()
    if not finals or any(
        job.status != "SUCCEEDED" or not artifact for job, artifact in finals
    ):
        raise HTTPException(409, "FINAL_ARTIFACT_MISSING")
    # A disk-backed ZIP avoids keeping a large batch in API process memory.
    temporary = tempfile.TemporaryDirectory(
        prefix="rh-export-", dir=os.environ.get("RUNNINGHUB_SPOOL_DIR")
    )
    try:
        root = Path(temporary.name)
        archive = root / "videos.zip"
        store = get_store()
        names: set[str] = set()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as target:
            for job, artifact in sorted(
                finals,
                key=lambda row: (
                    row[0].inputs["archivePath"].rsplit("/", 1)[0],
                    row[0].inputs["clip"],
                ),
            ):
                path = job.inputs["archivePath"]
                if path.casefold() in names:
                    raise HTTPException(409, "EXPORT_PATH_COLLISION")
                names.add(path.casefold())
                local = root / (job.id + ".mp4")
                store.fetch_artifact(artifact, local)
                target.write(local, arcname=path)
                local.unlink()
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=batch_id + ".zip",
            background=BackgroundTask(temporary.cleanup),
        )
    except (S3Error, MinioConfigError, MediaError, OSError):
        temporary.cleanup()
        raise HTTPException(503, "FINAL_ARCHIVE_PREPARATION_FAILED") from None
    except Exception:
        temporary.cleanup()
        raise
