"""TDD test suite for sync_returns + sync_cancellations.

These two endpoints share 99% structure (both POST search to
return_refund/202309/{kind}/search with create_time_ge/lt in body).
Differences are endpoint path and response key.
"""
from __future__ import annotations

from typing import Any

import pytest

from domain import Creds


class FakeHttpClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, path, *, body=None, extra_params=None, timeout=30):
        self.calls.append({
            "method": method, "path": path, "body": body,
            "extra_params": dict(extra_params or {}), "timeout": timeout,
        })
        if not self._responses:
            raise AssertionError(f"FakeHttpClient exhausted on call #{len(self.calls)}: {method} {path}")
        return self._responses.pop(0)


class FakeReturnRepository:
    def __init__(self):
        self.items: list[tuple[str, dict]] = []

    def upsert(self, shop_id: str, raw: dict[str, Any]) -> bool:
        rid = raw.get("return_id") or raw.get("id")
        if not rid:
            return False
        self.items.append((shop_id, dict(raw)))
        return True


class FakeCancellationRepository:
    def __init__(self):
        self.items: list[tuple[str, dict]] = []

    def upsert(self, shop_id: str, raw: dict[str, Any]) -> bool:
        cid = raw.get("cancel_id") or raw.get("cancellation_id") or raw.get("id")
        if not cid:
            return False
        self.items.append((shop_id, dict(raw)))
        return True


@pytest.fixture()
def creds():
    return Creds(access_token="tok", shop_cipher="cipher", region="VN", shop_id="shop-1")


# ─── sync_returns ─────────────────────────────────────────────────────


class TestSyncReturns:
    def test_single_page_saves_all(self, creds):
        http = FakeHttpClient([{
            "code": 0,
            "data": {"return_orders": [{"return_id": "r1"}, {"return_id": "r2"}], "total": 2},
        }])
        repo = FakeReturnRepository()
        from tts_business import sync_returns

        result = sync_returns(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 2
        assert result.pages == 1

    def test_post_to_returns_search(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"return_orders": []}}])
        repo = FakeReturnRepository()
        from tts_business import sync_returns

        sync_returns(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert http.calls[0]["method"] == "POST"
        assert http.calls[0]["path"] == "/return_refund/202309/returns/search"

    def test_create_time_in_body_as_int_not_query(self, creds):
        # Critical: TikTok return_refund strictly type-checks query string,
        # so create_time_ge MUST be in body as int.
        http = FakeHttpClient([{"code": 0, "data": {"return_orders": []}}])
        repo = FakeReturnRepository()
        from tts_business import sync_returns

        sync_returns(creds, {
            "shop_id": creds.shop_id,
            "create_time_ge": 1_700_000_000,
            "create_time_lt": 1_800_000_000,
        }, http=http, repo=repo)

        # Body should have int (not str) values
        body = http.calls[0]["body"]
        assert body is not None
        assert body["create_time_ge"] == 1_700_000_000
        assert body["create_time_lt"] == 1_800_000_000
        assert isinstance(body["create_time_ge"], int)
        assert isinstance(body["create_time_lt"], int)
        # And NOT in extra_params (query string)
        assert "create_time_ge" not in http.calls[0]["extra_params"]
        assert "create_time_lt" not in http.calls[0]["extra_params"]

    def test_page_size_clamped_to_10_50_range(self, creds):
        # return_refund: page_size must be in [10, 50] or TikTok 98001004
        http = FakeHttpClient([{"code": 0, "data": {"return_orders": []}}])
        repo = FakeReturnRepository()
        from tts_business import sync_returns

        # page_size=5 should be clamped up to 10
        sync_returns(creds, {"shop_id": creds.shop_id, "page_size": 5},
                     http=http, repo=repo)
        assert http.calls[0]["extra_params"]["page_size"] == "10"

        http = FakeHttpClient([{"code": 0, "data": {"return_orders": []}}])
        sync_returns(creds, {"shop_id": creds.shop_id, "page_size": 999},
                     http=http, repo=repo)
        assert http.calls[0]["extra_params"]["page_size"] == "50"

    def test_pagination(self, creds):
        http = FakeHttpClient([
            {"code": 0, "data": {"return_orders": [{"return_id": "r1"}],
                                "next_page_token": "tok-1"}},
            {"code": 0, "data": {"return_orders": [{"return_id": "r2"}], "total": 2}},
        ])
        repo = FakeReturnRepository()
        from tts_business import sync_returns

        result = sync_returns(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.saved == 2
        assert result.pages == 2
        # When no time filter, body is None on every page
        for call in http.calls:
            assert call["body"] is None
        # page_token is in extra_params on page 2
        assert http.calls[1]["extra_params"]["page_token"] == "tok-1"

    def test_pagination_keeps_body_across_pages(self, creds):
        # Critical: page 2+ must still include the time filter body
        http = FakeHttpClient([
            {"code": 0, "data": {"return_orders": [{"return_id": "r1"}],
                                "next_page_token": "tok-1"}},
            {"code": 0, "data": {"return_orders": [{"return_id": "r2"}], "total": 2}},
        ])
        repo = FakeReturnRepository()
        from tts_business import sync_returns

        sync_returns(creds, {
            "shop_id": creds.shop_id,
            "create_time_ge": 1_700_000_000,
        }, http=http, repo=repo)

        # Both pages should have body with create_time_ge
        for call in http.calls:
            assert call["body"] is not None
            assert call["body"]["create_time_ge"] == 1_700_000_000

    def test_first_page_error(self, creds):
        http = FakeHttpClient([{
            "code": 36009004,
            "message": "param create_time_ge type invalid. actual type:string, expected type:int64",
        }])
        repo = FakeReturnRepository()
        from tts_business import sync_returns

        result = sync_returns(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert not result.ok
        assert "type invalid" in result.error

    def test_return_without_id_skipped(self, creds):
        http = FakeHttpClient([{
            "code": 0,
            "data": {"return_orders": [
                {"return_id": "r1"},
                {"status": "PENDING"},  # no id
                {"return_id": "r3"},
            ]},
        }])
        repo = FakeReturnRepository()
        from tts_business import sync_returns

        result = sync_returns(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.saved == 2


# ─── sync_cancellations ───────────────────────────────────────────────


class TestSyncCancellations:
    def test_single_page_saves_all(self, creds):
        http = FakeHttpClient([{
            "code": 0,
            "data": {"cancellations": [{"cancel_id": "c1"}, {"cancel_id": "c2"}], "total": 2},
        }])
        repo = FakeCancellationRepository()
        from tts_business import sync_cancellations

        result = sync_cancellations(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 2

    def test_post_to_cancellations_search(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"cancellations": []}}])
        repo = FakeCancellationRepository()
        from tts_business import sync_cancellations

        sync_cancellations(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert http.calls[0]["method"] == "POST"
        assert http.calls[0]["path"] == "/return_refund/202309/cancellations/search"

    def test_create_time_in_body_as_int(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"cancellations": []}}])
        repo = FakeCancellationRepository()
        from tts_business import sync_cancellations

        sync_cancellations(creds, {
            "shop_id": creds.shop_id,
            "create_time_ge": 1_700_000_000,
        }, http=http, repo=repo)

        body = http.calls[0]["body"]
        assert body is not None
        assert body["create_time_ge"] == 1_700_000_000
        assert isinstance(body["create_time_ge"], int)
        assert "create_time_ge" not in http.calls[0]["extra_params"]

    def test_pagination(self, creds):
        http = FakeHttpClient([
            {"code": 0, "data": {"cancellations": [{"cancel_id": "c1"}],
                                "next_page_token": "tok-1"}},
            {"code": 0, "data": {"cancellations": [{"cancel_id": "c2"}], "total": 2}},
        ])
        repo = FakeCancellationRepository()
        from tts_business import sync_cancellations

        result = sync_cancellations(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.saved == 2
        assert result.pages == 2

    def test_first_page_error(self, creds):
        http = FakeHttpClient([{"code": 500, "message": "boom"}])
        repo = FakeCancellationRepository()
        from tts_business import sync_cancellations

        result = sync_cancellations(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert not result.ok
