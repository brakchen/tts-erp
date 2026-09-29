"""契约测试：店铺级平台抽成费率在 GET /v2/analytics/spu-roi 的落地。

feature/shop-fee-rate —— 费率解析优先级 ``页面覆写 > 店铺实测 > 全局基线``：

* ``reporting.shop_fee_rate_estimates`` 有**未过期**快照的店铺 →
  ``meta.fee.source="shop_estimate"``、行级 ``fee_source="shop_estimate"``、
  ``fee_rate_used=店铺费率``；``meta.fee.per_shop[0].estimate`` 携带样本量、
  覆盖率、窗口与快照日。
* 无快照 → ``baseline`` + ``fallback_reason="no_estimate"``。
* 快照超过 ``MAX_ESTIMATE_AGE_DAYS`` → ``baseline`` +
  ``fallback_reason="stale_estimate"``。
* ``?fee_rate=`` 覆写优先于一切 → ``user_override``。

独立于 ``test_spu_roi_api.py``（该文件由其他 SPU-ROI lane 占用）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

FX_SEED_TS = "2099-09-29T00:00:00+00:00"


def _seed_fx(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection
        sid = conn.execute(
            text(
                "INSERT INTO fx.exchange_rate_snapshots "
                "(base_code, upstream_last_update, next_update_at, fetched_at, rates_count) "
                "VALUES ('USD', :ts, :ts2, now(), 3) RETURNING id"
            ),
            {"ts": FX_SEED_TS, "ts2": "2099-09-30T00:00:00+00:00"},
        ).scalar()
        for code, rate in [("USD", "1"), ("VND", "26330"), ("CNY", "6.7686473")]:
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(
                    "INSERT INTO fx.exchange_rates "
                    "(snapshot_id, base_code, target_code, rate) "
                    "VALUES (:sid, 'USD', :c, :r)"
                ),
                {"sid": sid, "c": code, "r": rate},
            )


def _wipe(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM reporting.shop_fee_rate_estimates WHERE shop_pk IN ("
                "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_FEEAPI_%')"
            )
        )
        conn.execute(
            text("DELETE FROM commerce.products_spu WHERE spu_id LIKE 'TEST_FEEAPI_%'")
        )
        conn.execute(
            text("DELETE FROM commerce.shops WHERE shop_id LIKE 'TEST_FEEAPI_%'")
        )
        conn.execute(
            text(
                "DELETE FROM fx.exchange_rate_snapshots "
                "WHERE base_code = 'USD' AND upstream_last_update = :ts"
            ),
            {"ts": FX_SEED_TS},
        )


@pytest.fixture(autouse=True)
def _isolate(db_engine, _isolate_state):
    _wipe(db_engine)
    yield
    _wipe(db_engine)


@pytest.fixture()
def _fx(db_engine):
    _seed_fx(db_engine)
    yield


def _seed_shop_with_spu(db_engine, shop_id: str, spu_id: str) -> int:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection
        shop_pk = conn.execute(
            text(
                "INSERT INTO commerce.shops (platform, shop_id, account_name, status) "
                "VALUES ('tiktok', :sid, :name, 'active') RETURNING id"
            ),
            {"sid": shop_id, "name": f"{shop_id} 店铺"},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO commerce.products_spu "
                "(shop_pk, spu_id, title, status) "
                "VALUES (:shop, :sid, :title, 'ACTIVATE')"
            ),
            {"shop": shop_pk, "sid": spu_id, "title": f"{spu_id} 标题"},
        )
    return shop_pk


def _seed_estimate(
    db_engine,
    shop_pk: int,
    *,
    rate: str = "0.3590",
    calculated_on_offset_days: int = 0,
) -> None:
    """写入一份店铺费率快照；``calculated_on_offset_days`` 用于造过期快照。"""
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "INSERT INTO reporting.shop_fee_rate_estimates "
                "(shop_pk, calculated_on, lookback_days, fee_rate, "
                " kept_order_count, kept_line_gmv, window_line_gmv, "
                " kept_share, total_fee, currency, calculation_version) "
                "VALUES (:shop, (CURRENT_DATE - :off), 180, :rate, "
                "        128, 182340000, 190000000, 0.959684, 65463060, "
                "        'VND', 'fee-v1')"
            ),
            {"shop": shop_pk, "rate": rate, "off": calculated_on_offset_days},
        )


def _get(api_client, key: str, **params) -> dict:
    resp = api_client.get(
        "/v2/analytics/spu-roi",
        params={"include_all": "true", **params},
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_shop_estimate_applies(api_client, readonly_key, _fx, db_engine) -> None:
    shop_pk = _seed_shop_with_spu(db_engine, "TEST_FEEAPI_A", "TEST_FEEAPI_SPU_A")
    _seed_estimate(db_engine, shop_pk, rate="0.3590")

    payload = _get(api_client, readonly_key, shop_pk=shop_pk)

    fee = payload["meta"]["fee"]
    assert fee["source"] == "shop_estimate"
    assert fee["rate"] == "0.3590"
    assert fee["override"] is None
    assert len(fee["per_shop"]) == 1
    entry = fee["per_shop"][0]
    assert entry["shop_pk"] == shop_pk
    assert entry["source"] == "shop_estimate"
    assert entry["rate"] == "0.3590"
    assert entry["fallback_reason"] is None
    est = entry["estimate"]
    assert est is not None
    assert est["kept_order_count"] == 128
    assert est["lookback_days"] == 180
    assert est["kept_share"] == "0.9597"
    assert est["currency"] == "VND"
    assert est["calculated_on"]

    row = payload["items"][0]
    assert row["fee_source"] == "shop_estimate"
    assert row["fee_rate_used"] == "0.3590"


def test_missing_estimate_falls_back_to_baseline(
    api_client, readonly_key, _fx, db_engine
) -> None:
    shop_pk = _seed_shop_with_spu(db_engine, "TEST_FEEAPI_B", "TEST_FEEAPI_SPU_B")

    payload = _get(api_client, readonly_key, shop_pk=shop_pk)

    fee = payload["meta"]["fee"]
    assert fee["source"] == "baseline"
    assert fee["rate"] == "0.3080"
    entry = fee["per_shop"][0]
    assert entry["source"] == "baseline"
    assert entry["fallback_reason"] == "no_estimate"
    assert entry["estimate"] is None
    row = payload["items"][0]
    assert row["fee_source"] == "baseline"
    assert row["fee_rate_used"] == "0.3080"


def test_stale_estimate_falls_back_to_baseline(
    api_client, readonly_key, _fx, db_engine
) -> None:
    """超过 MAX_ESTIMATE_AGE_DAYS 的快照不再采用（job 失活时的兜底）。"""
    shop_pk = _seed_shop_with_spu(db_engine, "TEST_FEEAPI_D", "TEST_FEEAPI_SPU_D")
    _seed_estimate(db_engine, shop_pk, rate="0.3590", calculated_on_offset_days=30)

    payload = _get(api_client, readonly_key, shop_pk=shop_pk)

    fee = payload["meta"]["fee"]
    assert fee["source"] == "baseline"
    assert fee["rate"] == "0.3080"
    entry = fee["per_shop"][0]
    assert entry["source"] == "baseline"
    assert entry["fallback_reason"] == "stale_estimate"
    row = payload["items"][0]
    assert row["fee_source"] == "baseline"
    assert row["fee_rate_used"] == "0.3080"


def test_override_beats_shop_estimate(api_client, readonly_key, _fx, db_engine) -> None:
    shop_pk = _seed_shop_with_spu(db_engine, "TEST_FEEAPI_C", "TEST_FEEAPI_SPU_C")
    _seed_estimate(db_engine, shop_pk, rate="0.3590")

    payload = _get(api_client, readonly_key, shop_pk=shop_pk, fee_rate="0.5")

    fee = payload["meta"]["fee"]
    assert fee["source"] == "user_override"
    assert fee["rate"] == "0.5000"
    assert fee["override"] == "0.5000"
    entry = fee["per_shop"][0]
    assert entry["source"] == "user_override"
    assert entry["estimate"] is None
    row = payload["items"][0]
    assert row["fee_source"] == "user_override"
    assert row["fee_rate_used"] == "0.5000"
