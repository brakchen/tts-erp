"""Server-owned plan, identity, naming and optional-cost aggregation."""

from __future__ import annotations

import os
import secrets
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import PurePath

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from tts_erp_v2.commerce.shop_access import authorized_shop_clause
from tts_erp_v2.db.models.media import DigitalHuman, MediaAsset
from tts_erp_v2.db.models.runninghub import (
    GenerationAttempt,
    GenerationBatch,
    GenerationJob,
    GenerationProfile,
    VideoTemplate,
    WorkflowVersion,
)
from tts_erp_v2.runninghub.media import MediaError, archive_path
from tts_erp_v2.runninghub.workflow import MAX_SEED, SPEC, digest


class PlanError(ValueError):
    pass


def max_batch_jobs() -> int:
    return int(os.environ.get("RUNNINGHUB_MAX_BATCH_JOBS", "2000"))


def template_versions():
    return select(
        VideoTemplate.id,
        func.row_number()
        .over(
            partition_by=(VideoTemplate.human_id, VideoTemplate.name),
            order_by=(VideoTemplate.created_at, VideoTemplate.id),
        )
        .label("version"),
    ).subquery()


def asset_snapshot(session: Session, asset_id: str, kind: str) -> dict:
    if not asset_id.isdecimal():
        raise PlanError("INVALID_ASSET_ID")
    asset = session.get(MediaAsset, int(asset_id))
    if not asset or asset.deleted_at or asset.status != "ready" or asset.kind != kind:
        raise PlanError("READY_ASSET_REQUIRED")
    return {
        "assetId": str(asset.id),
        "bucket": asset.bucket,
        "objectKey": asset.object_key,
        "filename": asset.filename,
        "sizeBytes": asset.size_bytes,
        "contentType": asset.content_type,
    }


def image_snapshot(session: Session, image: dict) -> dict:
    if image.get("source", "media") == "media":
        return asset_snapshot(session, image["assetId"], "image")
    row = (
        session.execute(
            text(
                "SELECT i.* FROM procurement.spu_images i JOIN commerce.shops s ON s.id=i.shop_pk "
                "WHERE i.id=:id AND i.status='ready' AND i.deleted_at IS NULL AND "
                + authorized_shop_clause("s")
            ),
            {"id": int(image["assetId"])},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise PlanError("READY_AUTHORIZED_SPU_IMAGE_REQUIRED")
    return {
        "assetId": "spu-" + str(row["id"]),
        "bucket": os.environ.get("MINIO_BUCKET", "tts-erp-spu-images"),
        "objectKey": row["object_key"],
        "filename": row["filename"],
        "sizeBytes": row["size_bytes"],
        "contentType": row["content_type"],
    }


def build_plan(
    session: Session,
    images: list[dict],
    template_ids: list[str],
    *,
    single: bool = False,
) -> list[dict]:
    if not images or not template_ids or len(set(template_ids)) != len(template_ids):
        raise PlanError("IMAGES_AND_UNIQUE_TEMPLATES_REQUIRED")
    if single and (len(images) != 1 or len(template_ids) != 1):
        raise PlanError("FIRST_CLIP_REQUIRES_ONE_IMAGE_AND_TEMPLATE")
    versions = template_versions()
    templates = {
        template.id: (template, human, version)
        for template, human, version in session.execute(
            select(VideoTemplate, DigitalHuman, versions.c.version)
            .join(DigitalHuman, DigitalHuman.id == VideoTemplate.human_id)
            .join(versions, versions.c.id == VideoTemplate.id)
            .where(
                VideoTemplate.id.in_(template_ids),
                DigitalHuman.deleted_at.is_(None),
                DigitalHuman.status == "active",
            )
        )
    }
    if len(templates) != len(template_ids):
        raise PlanError("ACTIVE_HUMAN_TEMPLATE_REQUIRED")
    if any(not template.clips for template, _, _ in templates.values()):
        raise PlanError("REFERENCE_CLIP_REQUIRED")
    if (
        len(images) * sum(len(template.clips) for template, _, _ in templates.values())
        > max_batch_jobs()
    ):
        raise PlanError("BATCH_SIZE_EXCEEDED")
    names = Counter(
        (template.human_id, template.name) for template, _, _ in templates.values()
    )
    rows: list[dict] = []
    paths: set[str] = set()
    identities: set[str] = set()
    for image in images:
        source = image_snapshot(session, image)
        if not image["style"].strip() or not image["skuId"].strip():
            raise PlanError("STYLE_AND_SKU_REQUIRED")
        for template_id in template_ids:
            template, human, version = templates[template_id]
            for index, clip in enumerate(template.clips, 1):
                inputs = {
                    "image": source,
                    "video": clip,
                    "style": image["style"],
                    "skuId": image["skuId"],
                    "imageId": source["assetId"],
                    "originalFilename": source["filename"],
                    "skuLabel": image.get("skuLabel")
                    or PurePath(source["filename"].replace("\\", "/")).stem,
                    "humanId": str(human.id),
                    "humanName": human.name,
                    "templateId": template.id,
                    "templateName": template.name,
                    "templateVersion": version,
                    "clip": index,
                }
                if names[template.human_id, template.name] > 1:
                    inputs["archiveTemplateName"] = f"{template.name}-v{version}"
                identity = digest(
                    {
                        k: inputs[k]
                        for k in ("imageId", "skuId", "humanId", "templateId", "clip")
                    }
                )
                try:
                    path = archive_path(inputs)
                except MediaError as error:
                    raise PlanError(str(error)) from None
                if path.casefold() in paths or identity in identities:
                    raise PlanError("EXPORT_PATH_OR_JOB_COLLISION")
                paths.add(path.casefold())
                identities.add(identity)
                rows.append(
                    {"identity": identity, "inputs": {**inputs, "archivePath": path}}
                )
    return rows


def config_snapshot(profile: GenerationProfile, instructions: dict) -> dict:
    if not profile.workflow_version_id:
        raise PlanError("CHECK_WORKFLOW_FIRST")
    return {
        "credentialRef": profile.credential_ref,
        "workflowId": profile.workflow_id,
        "workflowVersionId": profile.workflow_version_id,
        "instanceType": profile.instance_type,
        "concurrency": profile.concurrency,
        "instructions": instructions,
        "spec": SPEC,
    }


def create_batch(
    session: Session,
    batch_id: str,
    mode: str,
    config: dict,
    plan: list[dict],
    request_hash: str,
    actor: str,
    *,
    single: bool = False,
) -> GenerationBatch:
    # Serialize duplicate request IDs across processes without catching transaction failure.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:id, 0))"),
        {"id": "rh-plan:" + batch_id},
    )
    existing = session.get(GenerationBatch, batch_id)
    if existing:
        if existing.request_hash != request_hash:
            raise PlanError("REQUEST_ID_REUSED_WITH_DIFFERENT_PLAN")
        return existing
    if session.get(WorkflowVersion, config["workflowVersionId"]) is None:
        raise PlanError("WORKFLOW_VERSION_MISSING")
    batch = GenerationBatch(
        id=batch_id,
        mode=mode,
        config=config,
        request_hash=request_hash,
        created_by=actor,
    )
    session.add(batch)
    session.flush()
    seeds: set[int] = set()
    for item in plan:
        seed = secrets.randbelow(MAX_SEED + 1)
        while seed in seeds:
            seed = secrets.randbelow(MAX_SEED + 1)
        seeds.add(seed)
        session.add(
            GenerationJob(
                batch_id=batch_id,
                identity=item["identity"],
                inputs=item["inputs"],
                seed=seed,
                status="DEFERRED"
                if single and item["inputs"]["clip"] > 1
                else "PLANNED",
            )
        )
    session.flush()
    return batch


def batch_summary(session: Session, batch: GenerationBatch) -> dict:
    jobs = list(
        session.scalars(select(GenerationJob).where(GenerationJob.batch_id == batch.id))
    )
    counts: dict[str, int] = {}
    groups: dict[str, dict] = {}
    templates: dict[str, dict] = {}
    preview_job = None
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
        inputs = job.inputs
        path = inputs["archivePath"].rsplit("/", 1)[0]
        group = groups.setdefault(path, {"path": path, "total": 0, "succeeded": 0})
        group["total"] += 1
        group["succeeded"] += int(job.status == "SUCCEEDED")
        templates.setdefault(
            inputs["templateId"],
            {
                "id": inputs["templateId"],
                "name": inputs["templateName"],
                "humanName": inputs["humanName"],
                "version": inputs.get("templateVersion"),
            },
        )
        if job.status == "SUCCEEDED" and (
            preview_job is None
            or inputs["archivePath"] < preview_job.inputs["archivePath"]
        ):
            preview_job = job
    attempts = list(
        session.scalars(
            select(GenerationAttempt)
            .join(GenerationJob, GenerationJob.id == GenerationAttempt.job_id)
            .where(GenerationJob.batch_id == batch.id)
        )
    )
    # One row per unique cloud taskId; top-level usage is authoritative if present.
    totals: dict[str, Decimal] = {}
    unknown = 0
    cloud_in_flight = 0
    for attempt in attempts:
        cloud_in_flight += int(attempt.reserved and attempt.task_id is not None)
        if not attempt.task_id:
            continue
        usage = attempt.usage or {}
        found = False
        for key in ("consumeCoins", "consumeMoney", "thirdPartyConsumeMoney"):
            value = usage.get(key)
            if value is not None:
                try:
                    amount = Decimal(str(value))
                    if amount.is_finite():
                        # Money in different currencies must never be combined.
                        unit = (
                            key
                            if key == "consumeCoins"
                            else key + ":" + str(usage.get("currency") or "unknown")
                        )
                        totals[unit] = totals.get(unit, Decimal(0)) + amount
                        found = True
                except InvalidOperation:
                    pass
        unknown += int(not found)
    return {
        "id": batch.id,
        "mode": batch.mode,
        "status": batch.status,
        "error": batch.error,
        "counts": counts,
        "total": len(jobs),
        "groups": list(groups.values()),
        "templates": list(templates.values()),
        "previewJobId": preview_job.id if preview_job else None,
        "terminationImpact": {
            "completed": counts.get("SUCCEEDED", 0),
            "unsubmitted": sum(
                counts.get(status, 0) for status in ("PLANNED", "DEFERRED", "PREPARING")
            ),
            "cloudInFlight": cloud_in_flight,
            "unconfirmed": counts.get("SUBMISSION_UNKNOWN", 0)
            + counts.get("SUBMITTING", 0),
        },
        "costs": {k: str(v) for k, v in totals.items()},
        "unknownCostTasks": unknown,
        "config": {k: v for k, v in batch.config.items() if k != "credentialRef"},
    }
