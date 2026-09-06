"""spu.image_mirror — mirror ``products_spu.main_image_url`` into local MinIO.

2026-09-05 page-rework lane: the manual-costs page no longer asks the
operator to upload supplier reference photos. Instead it shows the TikTok
main image (``main_image_url``, synced by ``tiktok.products``) rendered
from a LOCAL MinIO mirror, so the frontend never hits the TikTok CDN on
every page load.

Design (user-approved, minimal):
- Storage: one column on ``products_spu`` — ``mirror_object_key``.
- Dedupe: the object key EMBEDS a hash of the source URL
  (``mirror/<spu_pk>/<sha1(main_image_url)[:16]>.jpg``). The key itself
  IS the dedupe: if the URL is unchanged the derived key matches the
  stored column and the SPU is skipped; if the URL changed the key
  differs and the job re-downloads + re-puts + UPDATEs the column.
  No ``status`` column, no multi-row history — this is a system atomic
  operation, not the browser awaiting_upload→confirm flow.
- Failures: download/put errors write a SyncIssue and leave the column
  at its previous value (NULL on first failure) so the next run retries
  naturally. The run never aborts mid-way.
- Key-only storage: read endpoints resolve the key to presigned/public
  URLs on demand (see reporting.list_missing_cost_products), so MinIO
  access config can evolve without a backfill.

The job is system-wide (``is_tiktok=False``): it scans every
``commerce.products_spu`` row regardless of shop.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ChannelProduct, SyncIssue
from tts_erp_v2.jobs.runner import record_sync_issue, run_job
from tts_erp_v2.storage.minio_client import MinioClient
from tts_erp_v2.sync_worker.job_runner import JobResult

JOB_NAME = "spu.image_mirror"

# Content-Type sniff from the URL extension (TikTok CDN main images are
# jpeg in practice; fall back to jpeg for anything unparseable).
_EXT_RE = re.compile(r"\.([a-zA-Z0-9]+)(?:$|[?#])")
_CT_BY_EXT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}
_DEFAULT_CT = "image/jpeg"

# Downloader returns (bytes, content_type). Callable-typed so tests can
# inject a fake; production default is an httpx GET.
Downloader = Callable[[str], tuple[bytes, str]]


def _content_type_for(url: str | None) -> str:
    if not url:
        return _DEFAULT_CT
    m = _EXT_RE.search(url)
    if not m:
        return _DEFAULT_CT
    return _CT_BY_EXT.get(m.group(1).lower(), _DEFAULT_CT)


def _object_key_for(spu_pk: int, url: str) -> str:
    """Compose ``mirror/<spu>/<sha1(url)[:16]>.jpg`` — key = URL hash.

    Deterministic for a given (spu_pk, url). The embedded hash is the
    entire dedupe mechanism: a changed URL yields a different key, so
    the mirror job knows to re-fetch without storing the source URL
    anywhere (the source URL itself lives on products_spu.main_image_url).
    """
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    ext = _content_type_for(url).split("/")[-1]
    # Normalise jpeg → jpg so the key extension is stable and tidy.
    ext = "jpg" if ext == "jpeg" else ext
    if ext not in {"jpg", "png", "webp", "gif"}:
        ext = "jpg"
    return f"mirror/{spu_pk}/{digest}.{ext}"


def _default_downloader() -> Downloader:
    """Production downloader: httpx GET with a size cap."""
    import httpx

    def _dl(url: str) -> tuple[bytes, str]:
        # 8 MiB cap mirrors the spu_images CHECK constraint and the
        # 8 MiB upload cap the page used to enforce for manual uploads.
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.content
            if len(data) > 8 * 1024 * 1024:
                raise RuntimeError(f"image too large: {len(data)} bytes")
            ct = resp.headers.get("content-type") or _content_type_for(url)
            return data, ct

    return _dl


def _iter_spus_with_main_image(session: Session, spu_pks: list[int] | None = None):
    # is_not(None) alone would let an empty-string main_image_url through
    # (sha1("") download failure every tick); exclude empty strings too.
    stmt = select(ChannelProduct).where(
        ChannelProduct.main_image_url.is_not(None),
        ChannelProduct.main_image_url != "",
    )
    if spu_pks is not None:
        stmt = stmt.where(ChannelProduct.id.in_(spu_pks))
    return session.execute(stmt).scalars()


def _current_object_keys(session: Session) -> dict[int, str]:
    """Map spu_pk → mirror_object_key for rows that already have one."""
    rows = session.execute(
        select(ChannelProduct.id, ChannelProduct.mirror_object_key).where(
            ChannelProduct.mirror_object_key.is_not(None)
        )
    ).all()
    return {r.id: r.mirror_object_key for r in rows}


def run(
    session: Session,
    *,
    downloader: Downloader | None = None,
    minio: MinioClient | None = None,
    spu_pks: list[int] | None = None,
) -> JobResult:
    """Mirror SPUs whose main_image_url has no current local copy.

    ``spu_pks=None`` (production / scheduler path) scans every SPU with a
    main image. Pass an explicit id list to bound the run to a subset —
    used by tests for isolation against the shared DB and available for
    targeted operator re-runs (e.g. one SPU whose mirror failed).

    Returns counters only; failures write SyncIssues and are counted in
    ``rows_failed``. The caller (run_with_sync_job) commits.
    """
    if downloader is None:
        downloader = _default_downloader()
    if minio is None:
        minio = MinioClient.from_env()
    minio.ensure_bucket()

    rows_total = 0
    rows_inserted = 0  # mirrored (or confirmed current)
    rows_failed = 0
    existing = _current_object_keys(session)

    for spu in _iter_spus_with_main_image(session, spu_pks):
        rows_total += 1
        url = spu.main_image_url or ""
        key = _object_key_for(spu.id, url)

        if existing.get(spu.id) == key:
            # Already mirrored with the current URL — skip (no download,
            # no put, no UPDATE).
            continue

        try:
            data, content_type = downloader(url)
            minio.put_bytes(key, data, content_type)
        except Exception as exc:  # noqa: BLE001 — boundary, one SPU
            rows_failed += 1
            # record_sync_issue dedupes by (job, type, external_id,
            # still-open) so a persistently failing URL does not append a
            # new row every tick — same convention as miaoshou.* jobs.
            record_sync_issue(
                session,
                job_name=JOB_NAME,
                issue_type="MIRROR_DOWNLOAD_FAILED",
                external_id=str(spu.spu_id),
                details={"url": url, "error": str(exc)},
            )
            continue

        spu.mirror_object_key = key
        rows_inserted += 1
        _resolve_open_issues(session, spu.spu_id)

    return JobResult(
        rows_total=rows_total,
        rows_inserted=rows_inserted,
        rows_failed=rows_failed,
    )


def _resolve_open_issues(session: Session, spu_id: str) -> int:
    """Close open MIRROR_DOWNLOAD_FAILED issues once mirroring succeeds.

    Same convention as ``order_detail._resolve_issues``: a URL that
    failed for N ticks and then mirrored would otherwise leave a
    permanently-open issue row on the ops dashboard.
    """
    from datetime import UTC, datetime

    rows = session.execute(
        select(SyncIssue)
        .where(SyncIssue.job_name == JOB_NAME)
        .where(SyncIssue.issue_type == "MIRROR_DOWNLOAD_FAILED")
        .where(SyncIssue.external_id == str(spu_id))
        .where(SyncIssue.resolved_at.is_(None))
    ).scalars().all()
    now = datetime.now(UTC)
    for row in rows:
        row.resolved_at = now
    return len(rows)


def run_scheduled(session: Session) -> dict:
    """Scheduler entrypoint for ``spu.image_mirror`` (system job).

    Wraps the full-DB mirror pass in the shared ``sync_jobs`` lifecycle
    (same convention as the miaoshou.* / reporting.* system jobs), so
    every tick leaves a durable run record with counters instead of
    relying on the bare scheduler commit.
    """
    with run_job(session, job_name=JOB_NAME) as job:
        result = run(session)
        job.rows_total = result.rows_total
        job.rows_inserted = result.rows_inserted
        job.rows_failed = result.rows_failed
    return {
        "rows_total": result.rows_total,
        "rows_inserted": result.rows_inserted,
        "rows_failed": result.rows_failed,
    }


__all__ = [
    "JOB_NAME",
    "Downloader",
    "_content_type_for",
    "_object_key_for",
    "run",
    "run_scheduled",
]
