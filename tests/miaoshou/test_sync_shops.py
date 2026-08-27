"""tts-erp miaoshou_shops 表 + SDK 集成测试.

测：
- persist_miaoshou_shop 写 SQL 正确（含 INSERT ... ON CONFLICT ... DO UPDATE）
- 参数校验（page_no / page_size / limit 非 int → 400）
- _sync_miaoshou_shops / _db_list_miaoshou_shops 调 SDK + 返 200
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from tdd import miaoshou_sync as m_sync

# ---- Shop 模型解析 ----


def test_shop_model_parses_minimal_response():
    from miaoshou.endpoints.shop import Shop

    s = Shop.model_validate(
        {
            "shopId": 123,
            "site": "VN",
            "platformShopName": "Test",
            "platform": "tiktok",
        }
    )
    assert s.shopId == 123
    assert s.site == "VN"
    assert s.platform == "tiktok"
    assert s.isCb is None
    assert s.platformShopName == "Test"


# ---- mock Handler 工厂 ----


def make_handler():
    """绕过 tts_erp.Handler.__init__（避免 psycopg + 业务 init），只挂 _send."""

    class _FakeHandler:
        def __init__(self):
            self._sent = []

        def _send(self, code, body):
            self._sent.append((code, body))

    return _FakeHandler()


def bind_unbound(tts_erp_mod, method_name):
    """把 bound method 变 unbound function，第一个参数 = self."""
    method = getattr(tts_erp_mod.Handler, method_name)
    return method


# ---- persist_miaoshou_shop SQL 正确性 ----


def test_persist_miaoshou_shop_executes_correct_sql(monkeypatch):
    """验证 SQL 用了 INSERT ... ON CONFLICT ... DO UPDATE + 正确字段顺序."""
    from miaoshou.endpoints import shop as shop_mod

    captured = []

    class FakeCur:
        def execute(self, sql, params):
            captured.append((str(sql), params))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeConn:
        def cursor(self, row_factory=None):
            return FakeCur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("tts_erp.db_connect", lambda: FakeConn())

    shop = shop_mod.Shop.model_validate(
        {
            "shopId": 999,
            "site": "VN",
            "platformShopName": "Shop X",
            "shopNick": "n",
            "platform": "tiktok",
            "parentShopId": 0,
            "isCb": 1,
            "isCnsc": 0,
            "status": "ACTIVE",
            "gmtExpire": "2030-01-01",
            "gmtLastAuth": "2025-01-01",
        }
    )

    import tts_erp

    assert tts_erp.persist_miaoshou_shop("tiktok", "VN", shop) is True
    assert len(captured) == 1
    sql, params = captured[0]
    assert "INSERT INTO miaoshou_shops" in sql
    assert "ON CONFLICT" in sql
    assert "DO UPDATE SET" in sql
    assert params[0] == 999
    assert params[1] == "tiktok"
    assert params[2] == "VN"
    assert params[3] == "Shop X"
    assert params[4] == "n"
    assert params[5] == 0
    assert params[6] == 1
    assert params[7] == 0
    assert params[8] == "ACTIVE"
    raw_json = json.loads(params[11])
    assert raw_json["shopId"] == 999


# ---- _sync_miaoshou_shops ----


def test_sync_miaoshou_shops_validates_page_params(monkeypatch):
    """page_no 非 int：_safe_int 吃下用 default → 200（hardening 行为，避免 400/500）。

    2026-08-24 hardening (commit fe26...) changed: invalid page params no longer
    raise ValueError to 400 — _safe_int defaults + logs warning instead.
    """
    fn = m_sync.sync_miaoshou_shops

    with patch("miaoshou.MiaoshouErpClient.from_env") as mock_from_env:
        mock_from_env.return_value = MagicMock()
        code, body = fn({"page_no": ["abc"], "page_size": ["100"]})

    # New behavior: invalid page_no defaults to 1, request succeeds → 200
    assert code == 200


def test_sync_miaoshou_shops_happy_path(monkeypatch):
    """调 SDK → 拿到 shop → 调 persist → 返 200 + saved count."""
    fn = m_sync.sync_miaoshou_shops

    persisted = []
    # Patch where it's used: miaoshou_sync imported persist_miaoshou_shop
    # by name at module load, so patching tts_erp.* has no effect.
    monkeypatch.setattr(
        m_sync,
        "persist_miaoshou_shop",
        lambda platform, site, shop: persisted.append((platform, site, shop)) or True,
    )
    # Patch where it's used: miaoshou_sync imported it by name at module load,
    # so patching tts_erp.log_sync has no effect on the already-bound reference.
    monkeypatch.setattr(m_sync, "log_sync", lambda *a, **kw: None)

    mock_shop = MagicMock()
    mock_shop.shopId = 12345
    mock_result = MagicMock()
    mock_result.data.shopList = [mock_shop]
    mock_client = MagicMock()
    mock_client.shops.list.return_value = mock_result

    with patch("miaoshou.MiaoshouErpClient.from_env", return_value=mock_client):
        code, body = fn(
            {
                "platform": ["tiktok"],
                "site": ["VN"],
                "page_no": ["1"],
                "page_size": ["100"],
            }
        )

    mock_client.shops.list.assert_called_once_with(
        platform="tiktok", site="VN", page_no=1, page_size=100
    )
    assert len(persisted) == 1
    assert persisted[0] == ("tiktok", "VN", mock_shop)
    assert code == 200
    body = body
    assert body["platform"] == "tiktok"
    assert body["site"] == "VN"
    assert body["saved"] == 1
    assert body["total_in_page"] == 1


def test_sync_miaoshou_shops_handles_sdk_error(monkeypatch):
    """SDK 抛错时返 502."""
    fn = m_sync.sync_miaoshou_shops

    mock_client = MagicMock()
    mock_client.shops.list.side_effect = RuntimeError("network timeout")
    with patch("miaoshou.MiaoshouErpClient.from_env", return_value=mock_client):
        code, body = fn({"platform": ["tiktok"], "site": ["VN"]})

    assert code == 502
    assert "network timeout" in body["_error"]


def test_sync_miaoshou_shops_handles_client_init_error(monkeypatch):
    """MiaoshouErpClient.from_env 抛错时返 500."""
    fn = m_sync.sync_miaoshou_shops

    with patch(
        "miaoshou.MiaoshouErpClient.from_env", side_effect=RuntimeError("bad creds")
    ):
        code, body = fn({"platform": ["tiktok"], "site": ["VN"]})

    assert code == 500
    assert "bad creds" in body["_error"]


# ---- _db_list_miaoshou_shops ----


def test_db_list_miaoshou_shops_validates_limit():
    """limit 非 int：_safe_int 吃下用 default → 500 之前的 DB 错误或 200（hardening 行为）。

    2026-08-24 hardening: invalid limit no longer raises ValueError to 400 —
    _safe_int defaults to 100. May then succeed (200) or fail at DB layer (500),
    but never returns 400 for parameter validation.
    """
    fn = m_sync.db_list_miaoshou_shops
    code, body = fn({"limit": ["xyz"]})
    # Not 400 (that's the old contract)
    assert code != 400
