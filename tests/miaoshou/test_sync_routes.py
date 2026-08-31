"""Round 7 - 3 new persist functions + 3 new sync routes tests.

⚠️  SKIPPED 2026-08-31 — these tests target the retired ``tdd/``
codebase's ``miaoshou_sync`` module (sync_miaoshou_price_templates /
sync_miaoshou_collect_box_details / sync_miaoshou_move_collect_tasks)
and the legacy ``tts_erp.persist_miaoshou_*`` helpers. After Wave 4.1
``tdd/`` was deleted; the modern equivalents live under
``tts_erp_v2.jobs.miaoshou.{collect_box,move_collect}.sync_*`` but
have different (async) signatures and don't preserve the original
``(code, body)`` return contract. These tests need a rewrite to
target the v2 jobs — tracked as a follow-up.
"""

from __future__ import annotations

import pytest

# TODO: port to tts_erp_v2.jobs.miaoshou.{collect_box,move_collect}.sync_*
# (modern equivalents have async signatures + sync_jobs lifecycle, so this
# is not a 1:1 translation — needs new test design).
pytestmark = [
    pytest.mark.domain_miaoshou,
    pytest.mark.layer_integration,
    pytest.mark.skip(
        reason="miaoshou_sync retired with tdd/ codebase; tests need rewrite to "
        "tts_erp_v2.jobs.miaoshou.{collect_box,move_collect,shops}.sync_*"
    ),
]

# (original imports retained below so the rewrite has its starting point)
# from unittest.mock import MagicMock, patch
#
# from tdd import miaoshou_sync as m_sync
# from tts_erp import (
#     persist_miaoshou_collect_box_detail,
#     persist_miaoshou_move_collect_task,
#     persist_miaoshou_price_template,
# )

# ===== 测 3 个 persist 函数 =====


class _FakeCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_fake_conn(sql_log, params_log):
    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            sql_log.append(str(sql))
            params_log.append(params)

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self, row_factory=None):
            return _Cur()

        def commit(self):
            pass

    return _Conn()


def test_persist_miaoshou_price_template_insert_upserts():
    """🔵 价格模板 UPSERT：ON CONFLICT (price_template_id) DO UPDATE."""
    sql_log, params_log = [], []
    fake_template = MagicMock(platform="tiktok")
    fake_template.priceTemplateId = 123
    fake_template.model_dump.return_value = {"priceTemplateId": 123}
    with patch("tts_erp.db_connect", return_value=_make_fake_conn(sql_log, params_log)):
        assert persist_miaoshou_price_template("tiktok", fake_template) is True
    assert "INSERT INTO miaoshou_price_templates" in sql_log[0]
    assert "ON CONFLICT (price_template_id)" in sql_log[0]
    assert "DO UPDATE SET" in sql_log[0]
    assert params_log[0][0] == 123  # price_template_id 在最前


def test_persist_miaoshou_collect_box_detail_insert_upserts():
    """🔵 采集箱详情 UPSERT：PK = (platform, common_collect_box_detail_id)."""
    sql_log, params_log = [], []
    fake_d = MagicMock(platform="tiktok")
    fake_d.commonCollectBoxDetailId = 987
    fake_d.model_dump.return_value = {"commonCollectBoxDetailId": 987}
    with patch("tts_erp.db_connect", return_value=_make_fake_conn(sql_log, params_log)):
        assert persist_miaoshou_collect_box_detail("tiktok", fake_d) is True
    assert "INSERT INTO miaoshou_collect_box_details" in sql_log[0]
    assert "ON CONFLICT (platform, common_collect_box_detail_id)" in sql_log[0]
    # PK 顺序: platform, detail_id
    assert params_log[0][0] == "tiktok"
    assert params_log[0][1] == 987


def test_persist_miaoshou_move_collect_task_insert_upserts():
    """🔵 发布任务 UPSERT：PK = (platform, move_collect_task_detail_id)."""
    sql_log, params_log = [], []
    fake_t = MagicMock(platform="tiktok")
    fake_t.moveCollectTaskDetailId = "TASK_123"
    fake_t.model_dump.return_value = {"moveCollectTaskDetailId": "TASK_123"}
    with patch("tts_erp.db_connect", return_value=_make_fake_conn(sql_log, params_log)):
        assert persist_miaoshou_move_collect_task("tiktok", fake_t) is True
    assert "INSERT INTO miaoshou_move_collect_tasks" in sql_log[0]
    assert "ON CONFLICT (platform, move_collect_task_detail_id)" in sql_log[0]
    assert params_log[0][0] == "tiktok"
    assert params_log[0][1] == "TASK_123"


# ===== 测 3 个 sync 方法 =====


def test_sync_price_templates_paginates_until_empty():
    """🔵 _sync_miaoshou_price_templates：分页循环 + 空页停 + UPSERT 每条."""
    # mock SDK 客户端：3 个模板分 2 页
    t1 = MagicMock(priceTemplateId=1)
    t1.model_dump.return_value = {"priceTemplateId": 1}
    t2 = MagicMock(priceTemplateId=2)
    t2.model_dump.return_value = {"priceTemplateId": 2}
    t3 = MagicMock(priceTemplateId=3)
    t3.model_dump.return_value = {"priceTemplateId": 3}
    mock_client = MagicMock()
    mock_client.tk_collect_box.get_price_template_list.side_effect = [
        MagicMock(data=MagicMock(priceTemplateList=[t1, t2], total=3)),  # page 1
        MagicMock(data=MagicMock(priceTemplateList=[t3], total=3)),  # page 2
    ]

    persist_calls = []
    # Patch where it's used: miaoshou_sync imported persist_miaoshou_price_template
    # by name at module load, so patching tts_erp.* has no effect.
    with (
        patch("miaoshou.MiaoshouErpClient.from_env", return_value=mock_client),
        patch.object(
            m_sync,
            "persist_miaoshou_price_template",
            lambda platform, t: persist_calls.append(t.priceTemplateId) or True,
        ),
    ):
        code, body = m_sync.sync_miaoshou_price_templates(
            {
                "platform": "tiktok",
                "page_size": "2",
            }
        )

    assert persist_calls == [1, 2, 3]  # 全部 UPSERT
    # 应调 SDK 2 次（page1=full, page2=不满 → 停）
    assert mock_client.tk_collect_box.get_price_template_list.call_count == 2
    # 应返 200 + saved=3
    assert code == 200
    assert body["saved"] == 3
    assert body["total"] == 3


def test_sync_collect_box_details_calls_persist_for_each():
    """🔵 _sync_miaoshou_collect_box_details：每条详情调 persist 一次."""
    d1 = MagicMock(commonCollectBoxDetailId=100)
    d1.model_dump.return_value = {"commonCollectBoxDetailId": 100}
    d2 = MagicMock(commonCollectBoxDetailId=200)
    d2.model_dump.return_value = {"commonCollectBoxDetailId": 200}
    mock_client = MagicMock()
    # 单页足够（total=2, pageSize=50）
    mock_client.tk_collect_box.search_collect_box_list.return_value = MagicMock(
        data=MagicMock(detailList=[d1, d2], collectBoxDetailId=2)
    )

    persist_calls = []
    # Patch where it's used: miaoshou_sync imported persist_miaoshou_collect_box_detail
    # by name at module load, so patching tts_erp.* has no effect.
    with (
        patch("miaoshou.MiaoshouErpClient.from_env", return_value=mock_client),
        patch.object(
            m_sync,
            "persist_miaoshou_collect_box_detail",
            lambda platform, d: (
                persist_calls.append((platform, d.commonCollectBoxDetailId)) or True
            ),
        ),
    ):
        code, body = m_sync.sync_miaoshou_collect_box_details(
            {
                "platform": "tiktok",
                "page_size": "50",
            }
        )

    assert persist_calls == [("tiktok", 100), ("tiktok", 200)]
    assert code == 200
    assert body["saved"] == 2


def test_sync_move_collect_tasks_stops_on_empty_page():
    """🔵 _sync_miaoshou_move_collect_tasks：空页停止."""
    t1 = MagicMock(moveCollectTaskDetailId="T1")
    t1.model_dump.return_value = {"moveCollectTaskDetailId": "T1"}
    mock_client = MagicMock()
    # 第 1 页有 1 条，第 2 页空 → 停
    mock_client.tk_collect_box.search_move_collect_list.side_effect = [
        MagicMock(data=MagicMock(moveCollectDetailList=[t1], total=None)),
        MagicMock(data=MagicMock(moveCollectDetailList=[], total=None)),
    ]

    persist_calls = []
    # Patch where it's used: miaoshou_sync imported persist_miaoshou_move_collect_task
    # by name at module load, so patching tts_erp.* has no effect.
    with (
        patch("miaoshou.MiaoshouErpClient.from_env", return_value=mock_client),
        patch.object(
            m_sync,
            "persist_miaoshou_move_collect_task",
            lambda platform, t: persist_calls.append(t.moveCollectTaskDetailId) or True,
        ),
    ):
        code, body = m_sync.sync_miaoshou_move_collect_tasks(
            {
                "platform": "tiktok",
                "page_size": "20",
            }
        )

    assert persist_calls == ["T1"]  # 只 1 条
    assert (
        mock_client.tk_collect_box.search_move_collect_list.call_count == 2
    )  # 调 2 次
    assert body["saved"] == 1


def test_sync_methods_call_sdk_with_correct_params():
    """🔵 3 个 sync 方法都把 platform/site/page_size 等参数正确传给 SDK."""
    captured = []

    def make_capture(data_cls, data_inst, total=1):
        m = MagicMock()
        m.data = data_inst
        m.data.total = total
        return m

    mock_client = MagicMock()
    mock_client.tk_collect_box.get_price_template_list.side_effect = lambda **kw: (
        captured.append(("price_template", kw)),
        make_capture(MagicMock(), MagicMock(priceTemplateList=[], total=0)),
    )[1]
    mock_client.tk_collect_box.search_collect_box_list.side_effect = lambda **kw: (
        captured.append(("collect_box", kw)),
        make_capture(MagicMock(), MagicMock(detailList=[], collectBoxDetailId=0)),
    )[1]
    mock_client.tk_collect_box.search_move_collect_list.side_effect = lambda **kw: (
        captured.append(("move_collect", kw)),
        make_capture(MagicMock(), MagicMock(moveCollectDetailList=[], total=0)),
    )[1]

    with (
        patch("miaoshou.MiaoshouErpClient.from_env", return_value=mock_client),
        patch.object(m_sync, "persist_miaoshou_price_template", lambda *a, **kw: True),
        patch.object(
            m_sync, "persist_miaoshou_collect_box_detail", lambda *a, **kw: True
        ),
        patch.object(
            m_sync, "persist_miaoshou_move_collect_task", lambda *a, **kw: True
        ),
    ):
        m_sync.sync_miaoshou_price_templates(
            {
                "platform": "shopee",
                "site": "PH",
                "page_size": "100",
            }
        )
        m_sync.sync_miaoshou_collect_box_details(
            {
                "platform": "tiktok",
                "page_size": "30",
                "status": "normal",
            }
        )
        m_sync.sync_miaoshou_move_collect_tasks(
            {
                "platform": "tiktok",
                "page_size": "10",
                "status": "success",
            }
        )

    # 验每个 SDK 调用收到正确参数
    # 注：platform 不传给 SDK 方法（MiaoshouErpClient tk_collect_box.* 方法不接受
    # platform kwarg）；platform 仅用于：1) persist_miaoshou_*_template/detail/task
    # 的第一参数，2) log_sync 标记，3) 响应 payload。
    assert captured[0][0] == "price_template"
    assert "platform" not in captured[0][1], "platform should not be in SDK kwargs"
    assert captured[0][1]["site"] == "PH"
    assert captured[0][1]["page_size"] == 100
    assert captured[1][0] == "collect_box"
    assert "platform" not in captured[1][1], "platform should not be in SDK kwargs"
    assert captured[1][1]["page_size"] == 30
    assert captured[1][1]["status"] == "normal"
    assert captured[2][0] == "move_collect"
    assert "platform" not in captured[2][1], "platform should not be in SDK kwargs"
    assert captured[2][1]["page_size"] == 10
    assert captured[2][1]["status"] == "success"
