"""TDD test suite for sync_orders business function.

After Phase 2 refactor, sync_orders lives in tts_business.py and is a
pure function: takes (creds, body, http, repo) → SyncResult. No DB, no
HTTP framework, no module-level state. This makes it unit-testable.

Test cases cover the contract preserved from the original _sync_orders
in tts_erp.py (which we'll deprecate after FastAPI migration).
"""
from __future__ import annotations

from typing import Any

import pytest

from domain import Creds, HttpClient, SyncResult


# ─── Test doubles ─────────────────────────────────────────────────────


class FakeHttpClient:
    """Records every request and replays a queue of canned responses."""

    def __init__(self, responses: list[dict[str, Any]] | None = None):
        self._responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        extra_params: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        self.calls.append({
            "method": method,
            "path": path,
            "body": body,
            "extra_params": dict(extra_params or {}),
            "timeout": timeout,
        })
        if not self._responses:
            raise AssertionError(
                f"FakeHttpClient exhausted (no more canned responses); "
                f"call #{len(self.calls)}: {method} {path}"
            )
        return self._responses.pop(0)


class FakeOrderRepository:
    """In-memory order store. Counts upserts; never raises."""

    def __init__(self):
        self.orders: list[tuple[str, dict]] = []
        self.fail_order_ids: set[str] = set()  # IDs that upsert() rejects

    def upsert(self, shop_id: str, order_raw: dict[str, Any]) -> bool:
        oid = order_raw.get("id") or order_raw.get("order_id")
        if not oid or oid in self.fail_order_ids:
            return False
        self.orders.append((shop_id, dict(order_raw)))
        return True

    def get(self, order_id: str) -> dict[str, Any] | None:
        for _sid, o in self.orders:
            if o.get("id") == order_id or o.get("order_id") == order_id:
                return o
        return None

    def list(
        self,
        shop_id: str,
        *,
        status: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        out = [o for sid, o in self.orders if sid == shop_id]
        if status is not None:
            out = [o for o in out if o.get("order_status") == status]
        return out[:limit]


# ─── Shared fixtures ──────────────────────────────────────────────────


@pytest.fixture()
def creds() -> Creds:
    return Creds(access_token="tok-abc", shop_cipher="cipher-xyz", region="VN", shop_id="7494763368967603447")


def make_order(id: str, status: str = "AWAITING_SHIPMENT") -> dict[str, Any]:
    return {"id": id, "status": status, "create_time": 1_700_000_000}


# ─── Tests ────────────────────────────────────────────────────────────


class TestSyncOrdersHappyPath:
    def test_single_page_one_order_returns_saved_1(self, creds):
        http = FakeHttpClient([{
            "code": 0,
            "data": {"order_list": [make_order("o1")], "total": 1, "next_page_token": ""},
        }])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        result = sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 1
        assert result.total == 1
        assert result.pages == 1
        assert len(repo.orders) == 1

    def test_single_page_empty_returns_saved_0(self, creds):
        http = FakeHttpClient([{
            "code": 0,
            "data": {"order_list": [], "total": 0},
        }])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        result = sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 0
        assert result.total == 0
        assert result.pages == 1
        assert repo.orders == []

    def test_multiple_pages_paginates_until_no_token(self, creds):
        http = FakeHttpClient([
            {"code": 0, "data": {"order_list": [make_order("o1"), make_order("o2")],
                                "total": 4, "next_page_token": "tok-1"}},
            {"code": 0, "data": {"order_list": [make_order("o3"), make_order("o4")],
                                "total": 4, "next_page_token": None}},
        ])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        result = sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 4
        assert result.total == 4
        assert result.pages == 2
        assert len(http.calls) == 2
        # Second call should have page_token in extra_params
        assert http.calls[1]["extra_params"].get("page_token") == "tok-1"

    def test_pagination_caps_at_50_pages(self, creds):
        # 51 responses, all with non-empty next_page_token — should stop at 50
        responses = [
            {"code": 0, "data": {"order_list": [make_order(f"o{i}")],
                                "total": 999, "next_page_token": f"tok-{i}"}}
            for i in range(60)
        ]
        # Replace last token with empty to terminate (but we'll exhaust at 50 first)
        http = FakeHttpClient(responses)
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        result = sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        # Should stop at 50 pages (safety cap)
        assert result.pages == 50
        # 50 calls made, 1 initial + 49 paginations
        assert len(http.calls) == 50
        # 50 orders persisted
        assert len(repo.orders) == 50


class TestSyncOrdersPaginationStops:
    def test_pagination_stops_when_response_has_no_token(self, creds):
        # next_page_token missing entirely (not empty string) → stop
        http = FakeHttpClient([
            {"code": 0, "data": {"order_list": [make_order("o1")],
                                "total": 1, "next_page_token": "tok-1"}},
            {"code": 0, "data": {"order_list": [make_order("o2")], "total": 2}},  # no token
        ])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        result = sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.pages == 2
        assert result.saved == 2

    def test_pagination_stops_on_subsequent_page_error(self, creds):
        # First page OK, second page returns code != 0 → break, return what we have
        http = FakeHttpClient([
            {"code": 0, "data": {"order_list": [make_order("o1")], "next_page_token": "tok-1"}},
            {"code": 401, "message": "auth fail"},
        ])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        result = sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 1
        assert result.pages == 1


class TestSyncOrdersRequestShape:
    """Verify how sync_orders builds the request to TikTok."""

    def test_post_to_orders_search_path(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"order_list": []}}])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert http.calls[0]["method"] == "POST"
        assert http.calls[0]["path"] == "/order/202309/orders/search"

    def test_extra_params_include_shop_cipher_and_paging(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"order_list": []}}])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        ep = http.calls[0]["extra_params"]
        assert ep["shop_cipher"] == creds.shop_cipher
        assert ep["sort_field"] == "create_time"
        assert ep["sort_order"] == "DESC"
        assert "page_size" in ep

    def test_page_size_50_default(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"order_list": []}}])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert http.calls[0]["extra_params"]["page_size"] == "50"

    def test_page_size_capped_at_100(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"order_list": []}}])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        sync_orders(creds, {"shop_id": creds.shop_id, "page_size": 999},
                    http=http, repo=repo)

        # TikTok accepts up to 100, anything bigger → 36009004
        assert http.calls[0]["extra_params"]["page_size"] == "100"

    def test_order_status_passed_as_string_in_body(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"order_list": []}}])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        sync_orders(creds, {"shop_id": creds.shop_id, "order_status": 100},
                    http=http, repo=repo)

        # TikTok expects string, not int (per 36009004 type validation)
        assert http.calls[0]["body"]["order_status"] == "100"

    def test_create_time_ge_lt_passed_as_int_in_body(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"order_list": []}}])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        sync_orders(creds, {
            "shop_id": creds.shop_id,
            "create_time_ge": 1_700_000_000,
            "create_time_lt": 1_800_000_000,
        }, http=http, repo=repo)

        body = http.calls[0]["body"]
        assert body["create_time_ge"] == 1_700_000_000
        assert body["create_time_lt"] == 1_800_000_000
        # Must be int, not str (TikTok type validation)
        assert isinstance(body["create_time_ge"], int)
        assert isinstance(body["create_time_lt"], int)


class TestSyncOrdersErrors:
    def test_first_page_error_returns_sync_result_with_error(self, creds):
        http = FakeHttpClient([{"code": 106001, "message": "invalid sign"}])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        result = sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert not result.ok
        assert result.saved == 0
        assert "invalid sign" in result.error

    def test_repo_failure_does_not_stop_remaining_orders(self, creds):
        http = FakeHttpClient([{
            "code": 0,
            "data": {"order_list": [make_order("o1"), make_order("o2"), make_order("o3")]},
        }])
        repo = FakeOrderRepository()
        repo.fail_order_ids.add("o2")  # middle one fails
        from tts_business import sync_orders

        result = sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 2  # o1 and o3
        assert len(repo.orders) == 2

    def test_order_with_no_id_is_skipped(self, creds):
        http = FakeHttpClient([{
            "code": 0,
            "data": {"order_list": [
                make_order("o1"),
                {"status": "PENDING"},  # no id
                make_order("o3"),
            ]},
        }])
        repo = FakeOrderRepository()
        from tts_business import sync_orders

        result = sync_orders(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.saved == 2
