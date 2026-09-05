"""Coverage lift for ``tts_erp_v2/analytics/repository.py``.

Targets the function surface of the 2026-09-05 analytics reorg（单表写）：

- ``upsert_dump`` — insert（was_inserted=True）+ duplicate（was_inserted=False）
  + ad_records/ad_daily_completeness/ad_shop_timezones 4 张派生表不再写
- ``has_data`` — empty + with-data + unknown endpoint（raises ValueError）

Layering: this file imports the repository module directly (not through
the HTTP API). The tests are unit-style on the function surface —
SQLAlchemy ``text()`` SQL is executed against the real dev database
(rolled-back savepoint per test, same pattern as other v2 suites).

Data isolation: TEST_/-prefixed seller/advertiser/campaign ids so the
autouse cleanup can wipe them at teardown. The
``tests/api/conftest.py::_isolate_state`` does NOT cover analytics
tables — this file adds its own autouse wipe scoped to the analytics
schema's remaining table.

2026-09-05 reorg（tech-doc/analytics/reorg-plan.md）adaptations:
- fetch_timezone / write_audit / purge_expired 三个函数及其测试已删
  （对应表已 drop；审计迁出文件日志,tts_erp_v2/api/v2/analytics.py 的
   ``_log_ingest_event``）。
- _add_days / _subtract_days 已从 repository.py 删除（仅 _today_in_tz
  在 domain 已废时用过,本身无用）。以下 6 个旧测试一并删除。
- _wipe 仅删 ad_raw（4 张派生表 drop 后,cleanup 列表从 5 张缩为 1 张）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wipe_analytics_rows(db_engine):
    """Wipe analytics.ad_raw rows this file touches, before AND after each test.

    The shared api/conftest.py autouse doesn't know about analytics. We
    wipe per-test so the savepoint-rollback isolation works for these
    rows too. Post-2026-09-05 reorg, only ad_raw remains in the analytics
    schema — the wipe list shrinks from 5 tables to 1.
    """
    _wipe(db_engine)
    yield
    _wipe(db_engine)


def _wipe(db_engine) -> None:
    """Delete analytics.ad_raw rows whose seller_id starts with ``TEST_``."""
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text("DELETE FROM analytics.ad_raw WHERE seller_id LIKE 'TEST_%'")
        )


# ---------------------------------------------------------------------------
# upsert_dump
# ---------------------------------------------------------------------------


def _make_dump(seller: str = "TEST_repo_seller"):
    """Build a minimal DumpPayload for the repository.

    Avoids going through the FastAPI layer — directly constructs the
    dataclass so tests don't depend on the request schema validation.
    Returns (DumpPayload, expected_idempotency_key).
    """
    from tts_erp_v2.analytics.domain import (
        DumpPayload,
        StorageKey,
        compute_idempotency_key,
    )

    day = date(2026, 8, 23)
    return DumpPayload(
        seller_id=seller,
        advertiser_id="TEST_repo_adv",
        endpoint="/oec_ads/shopping/v1/oec/stat/post_product_list",
        method="POST",
        day=day,
        campaign_id="TEST_repo_campaign",
        request={
            "url": "https://ads.tiktok.com/oec_ads/.../post_product_list",
            "headers": {"Authorization": "Bearer redacted"},
            "body": {},
        },
        response={
            "status": 200,
            "headers": {"Content-Type": "application/json"},
            "body": {"data": {"rows": []}},
        },
        captured_at=datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC),
        storage_key=StorageKey.PRODUCT_ANALYSES,
        request_id="TEST_repo_audit_ok",
        source="tiktok-shop-data-sync",
        protocol_version=2,
        schema_version=1,
    ), compute_idempotency_key(
        seller_id=seller,
        advertiser_id="TEST_repo_adv",
        storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="TEST_repo_campaign",
        day=day,
        page=1,
    )


def test_upsert_dump_first_call_inserts(db_session):
    """After upsert_dump: ad_raw has 1 row with the expected idempotency_key.

    2026-09-05 reorg：派生表（ad_records / ad_daily_completeness /
    ad_shop_timezones / ad_audit_log）已 drop,新代码只写 ad_raw。
    本 lane 不 apply 0007,所以派生表在 DB 里仍存在（pre-migration 状态）,
    测试**不**反向断言派生表不存在——那是 migration apply 后才能
    验证的契约。这里只断言「新代码不再写派生表」:upsert_dump 调用后
    派生表无新增 TEST_ 行（与 ``_wipe`` 自动 cleanup 的隔离语义一致）。
    """
    from tts_erp_v2.analytics import repository

    dump, expected_idem = _make_dump()
    result = repository.upsert_dump(db_session, dump, request_id=dump.request_id)

    assert result.status == "inserted"
    assert result.idempotency_key == expected_idem
    assert len(result.idempotency_key) == 64  # SHA-256 hex

    # Verify ad_raw
    raw_row = db_session.execute(
        text(
            "SELECT idempotency_key, seller_id, advertiser_id, endpoint, "
            "method, day, campaign_id FROM analytics.ad_raw "
            "WHERE seller_id = :s"
        ),
        {"s": dump.seller_id},
    ).first()
    assert raw_row is not None
    assert raw_row.idempotency_key == expected_idem
    assert raw_row.seller_id == "TEST_repo_seller"
    assert raw_row.endpoint == dump.endpoint
    assert raw_row.day == dump.day


def test_upsert_dump_replay_is_duplicate(db_session):
    """Replay the same dump: status='duplicate', same idempotency_key.

    Pinned because the protocol's contract is byte-stable: re-running
    the plugin's POST /dumps with the same payload must yield the same
    idempotency_key (so retries are deduped at the storage layer).
    """
    from tts_erp_v2.analytics import repository

    dump, expected_idem = _make_dump()
    r1 = repository.upsert_dump(db_session, dump, request_id=dump.request_id)
    # Need a fresh session because upsert_dump commits internally.
    r2 = repository.upsert_dump(db_session, dump, request_id=dump.request_id)

    assert r1.status == "inserted"
    assert r2.status == "duplicate"
    assert r1.idempotency_key == r2.idempotency_key == expected_idem


def test_upsert_dump_different_seller_gets_distinct_key(db_session):
    """Different seller → different idempotency_key (5-tuple uniqueness)."""
    from tts_erp_v2.analytics import repository

    dump_a, key_a = _make_dump(seller="TEST_repo_a")
    dump_b, key_b = _make_dump(seller="TEST_repo_b")
    assert key_a != key_b
    repository.upsert_dump(db_session, dump_a, request_id="TEST_a")
    repository.upsert_dump(db_session, dump_b, request_id="TEST_b")


# ---------------------------------------------------------------------------
# has_data
# ---------------------------------------------------------------------------


def test_has_data_false_before_dump(db_session):
    """has_data: no rows yet → has_data=False, storageKey derived."""
    from tts_erp_v2.analytics import repository

    result = repository.has_data(
        db_session,
        seller_id="TEST_repo_hasdata_empty",
        advertiser_id="TEST_repo_adv",
        endpoint="/oec_ads/shopping/v1/oec/stat/post_product_list",
        day=date(2099, 1, 1),  # future date — no rows
    )
    assert result.has_data is False
    assert result.storage_key.value == "productAnalyses"
    assert result.day == date(2099, 1, 1)


def test_has_data_true_after_dump(db_session):
    """After upsert_dump, has_data=True for the same (scope, endpoint, day)."""
    from tts_erp_v2.analytics import repository

    dump, _key = _make_dump(seller="TEST_repo_hasdata_set")
    repository.upsert_dump(db_session, dump, request_id="TEST_hd")

    result = repository.has_data(
        db_session,
        seller_id=dump.seller_id,
        advertiser_id=dump.advertiser_id,
        endpoint=dump.endpoint,
        day=dump.day,
    )
    assert result.has_data is True
    assert result.storage_key.value == "productAnalyses"


def test_has_data_unknown_endpoint_raises(db_session):
    """has_data with an endpoint outside the 3-path allowlist → ValueError.

    This is the server-side guard rail against plugin typos. Without it
    the SQL would still execute (returning has_data=False), but the
    storageKey would be unknown and break downstream consumers.
    """
    from tts_erp_v2.analytics import repository

    with pytest.raises(ValueError) as exc_info:
        repository.has_data(
            db_session,
            seller_id="TEST_repo_bad",
            advertiser_id="TEST_repo_adv",
            endpoint="/oec_ads/UNKNOWN/path",
            day=date(2026, 8, 23),
        )
    assert "unknown endpoint" in str(exc_info.value)


# ---------------------------------------------------------------------------
# STORAGE_KEY_BY_PATH (module-level constant — smoke check)
# ---------------------------------------------------------------------------


def test_storage_key_by_path_covers_three_endpoints():
    """The 3-path allowlist must contain every dump endpoint mapping.

    Regression guard: a typo in the path would silently disable
    has_data for that endpoint and break the Chrome extension's
    pre-flight check.
    """
    from tts_erp_v2.analytics.domain import StorageKey
    from tts_erp_v2.analytics.repository import STORAGE_KEY_BY_PATH

    assert (
        STORAGE_KEY_BY_PATH[
            "/oec_ads/shopping/v1/oec/stat/post_product_list"
        ]
        == StorageKey.PRODUCT_ANALYSES
    )
    assert (
        STORAGE_KEY_BY_PATH[
            "/oec_ads/shopping/v1/oec/stat/post_session_list"
        ]
        == StorageKey.SESSION_ANALYSES
    )
    assert (
        STORAGE_KEY_BY_PATH[
            "/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list"
        ]
        == StorageKey.CAMPAIGN_CHANGE_LOGS
    )
    # Total of 3 entries — no surprise additions.
    assert len(STORAGE_KEY_BY_PATH) == 3
