"""TDD tests for jobs.tiktok.spu_image_mirror — mirror TikTok CDN main
image URLs into local MinIO.

Design (user-approved, minimal):
* Storage = ONE column ``products_spu.mirror_object_key`` (no status
  state machine, no multi-row history, no separate source-hash column).
* Dedupe: object key embeds ``sha1(main_image_url)[:16]`` so the key
  itself IS the dedupe — unchanged URL → key matches → skipped; changed
  URL → key differs → re-download + re-put + UPDATE the column.
* Failure → SyncIssue; column keeps its old value; next run retries.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    Credentials,
    SyncIssue,
    SyncJob,
)
from tts_erp_v2.jobs.tiktok import spu_image_mirror as mirror
from tts_erp_v2.sync_worker.job_runner import run_with_sync_job

pytestmark = [pytest.mark.domain_commerce, pytest.mark.layer_integration]

URL_A = "https://p16.example.com/tos-aaa/orig-a.jpeg?x=1"
URL_B = "https://p16.example.com/tos-aaa/orig-b.jpeg?x=1"


def _make_account(session) -> ChannelAccount:
    cred = Credentials(
        provider="tiktok",
        external_account_id="TEST_TT_MIRROR_SHOP",
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        shop_id="TEST_TT_MIRROR_SHOP",
        credential_id=cred.id,
        status="active",
        data_source="api",
    )
    session.add(acct)
    session.flush()
    return acct


def _make_spu(session, *, account: ChannelAccount, spu_id: str, url: str | None) -> int:
    spu = ChannelProduct(
        shop_pk=account.id,
        spu_id=spu_id,
        title=f"TEST {spu_id}",
        status="ACTIVATE",
        main_image_url=url,
    )
    session.add(spu)
    session.flush()
    return spu.id


def _mirror_key_of(session, spu_pk: int) -> str | None:
    return session.execute(
        select(ChannelProduct.mirror_object_key).where(ChannelProduct.id == spu_pk)
    ).scalar_one()


class FakeDownloader:
    """Deterministic bytes; records requested URLs; can fail one URL."""

    def __init__(self, *, fail_url: str | None = None):
        self.calls: list[str] = []
        self.fail_url = fail_url

    def __call__(self, url: str) -> tuple[bytes, str]:
        self.calls.append(url)
        if self.fail_url and url == self.fail_url:
            raise RuntimeError(f"download failed for {url}")
        return b"\xff\xd8\xff" + url.encode()[:16], "image/jpeg"


class FakeMinio:
    def __init__(self):
        self.puts: list[tuple[str, bytes, str]] = []
        self.bucket_ensured = False

    def put_bytes(self, object_key: str, data: bytes, content_type: str) -> None:
        self.puts.append((object_key, data, content_type))

    def ensure_bucket(self) -> None:
        self.bucket_ensured = True


def test_object_key_embeds_url_hash() -> None:
    """mirror/<spu>/<sha1(url)[:16]>.jpg — URL change → key change."""
    k1 = mirror._object_key_for(spu_pk=42, url=URL_A)
    k2 = mirror._object_key_for(spu_pk=42, url=URL_B)
    k3 = mirror._object_key_for(spu_pk=43, url=URL_A)
    assert k1.startswith("mirror/42/")
    assert k1.endswith(".jpg")
    assert k1 != k2, "URL change must change the key (that IS the dedupe)"
    assert k1 != k3, "different SPU must not collide"
    assert mirror._object_key_for(spu_pk=42, url=URL_A) == k1, "deterministic"


def test_first_mirror_sets_column_and_puts(db_session) -> None:
    account = _make_account(db_session)
    spu_pk = _make_spu(db_session, account=account, spu_id="SPU1", url=URL_A)
    dl = FakeDownloader()
    m = FakeMinio()

    _, result = run_with_sync_job(
        db_session,
        job_name=mirror.JOB_NAME,
        inner=mirror.run,
        inner_kwargs={"downloader": dl, "minio": m, "spu_pks": [spu_pk]},
    )
    assert result.rows_total == 1
    assert result.rows_inserted == 1
    assert dl.calls == [URL_A]
    assert len(m.puts) == 1
    assert m.bucket_ensured

    key = m.puts[0][0]
    assert _mirror_key_of(db_session, spu_pk) == key
    # content-type sniffed from the URL (jpeg).
    assert m.puts[0][2] == "image/jpeg"


def test_unchanged_url_is_skipped(db_session) -> None:
    account = _make_account(db_session)
    spu_pk = _make_spu(db_session, account=account, spu_id="SPU1", url=URL_A)
    dl = FakeDownloader()
    m = FakeMinio()
    mirror.run(db_session, downloader=dl, minio=m, spu_pks=[spu_pk])
    db_session.commit()
    assert len(m.puts) == 1

    # Same URL → derived key matches the stored column → no re-download.
    dl2 = FakeDownloader()
    m2 = FakeMinio()
    _, result = run_with_sync_job(
        db_session,
        job_name=mirror.JOB_NAME,
        inner=mirror.run,
        inner_kwargs={"downloader": dl2, "minio": m2, "spu_pks": [spu_pk]},
    )
    assert result.rows_total == 1
    assert result.rows_inserted == 0
    assert dl2.calls == []
    assert m2.puts == []


def test_changed_url_remirrors_and_updates_column(db_session) -> None:
    account = _make_account(db_session)
    spu_pk = _make_spu(db_session, account=account, spu_id="SPU1", url=URL_A)
    dl = FakeDownloader()
    m = FakeMinio()
    mirror.run(db_session, downloader=dl, minio=m, spu_pks=[spu_pk])
    db_session.commit()
    old_key = m.puts[0][0]

    # URL changes on the upstream product → new derived key.
    spu = db_session.execute(
        select(ChannelProduct).where(ChannelProduct.id == spu_pk)
    ).scalar_one()
    spu.main_image_url = URL_B
    db_session.commit()

    dl2 = FakeDownloader()
    m2 = FakeMinio()
    _, result = run_with_sync_job(
        db_session,
        job_name=mirror.JOB_NAME,
        inner=mirror.run,
        inner_kwargs={"downloader": dl2, "minio": m2, "spu_pks": [spu_pk]},
    )
    assert result.rows_inserted == 1
    assert dl2.calls == [URL_B]
    new_key = m2.puts[0][0]
    assert new_key != old_key
    # Column now points at the new mirror (single value, no history rows).
    assert _mirror_key_of(db_session, spu_pk) == new_key


def test_failure_writes_sync_issue_keeps_old_value(db_session) -> None:
    account = _make_account(db_session)
    spu_ok = _make_spu(db_session, account=account, spu_id="OK", url=URL_A)
    spu_bad = _make_spu(db_session, account=account, spu_id="BAD", url=URL_B)
    dl = FakeDownloader(fail_url=URL_B)
    m = FakeMinio()

    _, result = run_with_sync_job(
        db_session,
        job_name=mirror.JOB_NAME,
        inner=mirror.run,
        inner_kwargs={
            "downloader": dl,
            "minio": m,
            "spu_pks": [spu_ok, spu_bad],
        },
    )
    assert result.rows_total == 2
    assert result.rows_inserted == 1  # good SPU mirrored
    assert result.rows_failed == 1  # bad SPU failed
    assert len(m.puts) == 1
    assert _mirror_key_of(db_session, spu_ok) is not None
    assert _mirror_key_of(db_session, spu_bad) is None  # column stays NULL

    issues = db_session.execute(
        select(SyncIssue).where(
            SyncIssue.job_name == mirror.JOB_NAME,
            SyncIssue.issue_type == "MIRROR_DOWNLOAD_FAILED",
        )
    ).scalars().all()
    assert len(issues) == 1
    assert issues[0].external_id == "BAD"


def test_spu_without_main_image_is_skipped(db_session) -> None:
    account = _make_account(db_session)
    spu_pk = _make_spu(db_session, account=account, spu_id="NOIMG", url=None)
    dl = FakeDownloader()
    m = FakeMinio()
    _, result = run_with_sync_job(
        db_session,
        job_name=mirror.JOB_NAME,
        inner=mirror.run,
        inner_kwargs={"downloader": dl, "minio": m, "spu_pks": [spu_pk]},
    )
    assert result.rows_total == 0
    assert dl.calls == []
    assert m.puts == []


def test_run_scheduled_writes_sync_jobs_row(db_session) -> None:
    """Scheduler entrypoint leaves a durable sync_jobs run record.

    Mirrors the miaoshou.* / reporting.* system-job convention: the job
    body runs inside run_job, so operators can see every tick (status +
    counters) instead of only a bare scheduler commit. The scheduled
    path does a full-DB scan by design, so we mock ``run`` itself and
    assert the lifecycle wrapper behaviour, not real mirroring.
    """
    import tts_erp_v2.jobs.tiktok.spu_image_mirror as mirror_mod
    from tts_erp_v2.sync_worker.job_runner import JobResult as _JR

    calls = []
    real_run = mirror_mod.run

    def _fake_run(session):
        calls.append(session)
        return _JR(rows_total=7, rows_inserted=5, rows_failed=2)

    mirror_mod.run = _fake_run
    try:
        out = mirror.run_scheduled(db_session)
    finally:
        mirror_mod.run = real_run
    assert out == {"rows_total": 7, "rows_inserted": 5, "rows_failed": 2}
    assert len(calls) == 1

    rows = db_session.execute(
        select(SyncJob)
        .where(SyncJob.job_name == mirror.JOB_NAME)
        .order_by(SyncJob.id.desc())
        .limit(1)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "succeeded"
    assert rows[0].rows_total == 7
    assert rows[0].rows_inserted == 5
    assert rows[0].rows_failed == 2


def test_record_sync_issue_dedupes_across_ticks(db_session) -> None:
    """A persistently failing URL updates the open issue, not a new row."""
    account = _make_account(db_session)
    spu_pk = _make_spu(db_session, account=account, spu_id="BAD2", url=URL_A)
    dl = FakeDownloader(fail_url=URL_A)
    m = FakeMinio()

    for _ in range(2):  # two ticks against the same failing SPU
        _, result = run_with_sync_job(
            db_session,
            job_name=mirror.JOB_NAME,
            inner=mirror.run,
            inner_kwargs={"downloader": dl, "minio": m, "spu_pks": [spu_pk]},
        )
        assert result.rows_failed == 1

    issues = db_session.execute(
        select(SyncIssue)
        .where(
            SyncIssue.job_name == mirror.JOB_NAME,
            SyncIssue.issue_type == "MIRROR_DOWNLOAD_FAILED",
            SyncIssue.external_id == "BAD2",
        )
    ).scalars().all()
    assert len(issues) == 1, "same open failure must not append a new row"


def test_success_after_failure_resolves_open_issue(db_session) -> None:
    """A URL that failed on earlier ticks and then mirrors closes the issue.

    Otherwise a transient outage would leave a permanently-open
    MIRROR_DOWNLOAD_FAILED row on the ops dashboard.
    """
    account = _make_account(db_session)
    spu_pk = _make_spu(db_session, account=account, spu_id="RECOVER", url=URL_A)

    # Tick 1: fail (issue opens).
    dl_fail = FakeDownloader(fail_url=URL_A)
    m = FakeMinio()
    _, result = run_with_sync_job(
        db_session,
        job_name=mirror.JOB_NAME,
        inner=mirror.run,
        inner_kwargs={"downloader": dl_fail, "minio": m, "spu_pks": [spu_pk]},
    )
    assert result.rows_failed == 1

    # Tick 2: same URL now succeeds.
    dl_ok = FakeDownloader()
    m2 = FakeMinio()
    _, result = run_with_sync_job(
        db_session,
        job_name=mirror.JOB_NAME,
        inner=mirror.run,
        inner_kwargs={"downloader": dl_ok, "minio": m2, "spu_pks": [spu_pk]},
    )
    assert result.rows_inserted == 1

    open_issues = db_session.execute(
        select(SyncIssue)
        .where(
            SyncIssue.job_name == mirror.JOB_NAME,
            SyncIssue.issue_type == "MIRROR_DOWNLOAD_FAILED",
            SyncIssue.external_id == "RECOVER",
            SyncIssue.resolved_at.is_(None),
        )
    ).scalars().all()
    assert open_issues == [], "successful mirror must resolve the open issue"


def test_empty_string_main_image_is_skipped(db_session) -> None:
    """An empty-string main_image_url must not be downloaded each tick."""
    account = _make_account(db_session)
    spu_pk = _make_spu(db_session, account=account, spu_id="EMPTYURL", url="")
    dl = FakeDownloader()
    m = FakeMinio()
    _, result = run_with_sync_job(
        db_session,
        job_name=mirror.JOB_NAME,
        inner=mirror.run,
        inner_kwargs={"downloader": dl, "minio": m, "spu_pks": [spu_pk]},
    )
    assert result.rows_total == 0
    assert dl.calls == []
    assert m.puts == []
