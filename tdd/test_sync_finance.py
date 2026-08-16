"""TDD test suite for sync_statements (similar to sync_payments).

sync_payments and sync_statements share 95% structure; only the time
field name, sort_field, response key, and endpoint path differ. We
test sync_statements separately to lock down its specific field names
and endpoint.
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


class FakeStatementRepository:
    def __init__(self):
        self.statements: list[tuple[str, dict]] = []

    def upsert(self, shop_id: str, statement_raw: dict[str, Any]) -> bool:
        sid = statement_raw.get("statement_id") or statement_raw.get("id")
        if not sid:
            return False
        self.statements.append((shop_id, dict(statement_raw)))
        return True


@pytest.fixture()
def creds():
    return Creds(access_token="tok", shop_cipher="cipher", region="VN", shop_id="shop-1")


def make_statement(id: str, amount: str = "500.00") -> dict:
    return {"statement_id": id, "amount": amount, "currency": "VND", "statement_time": 1_700_000_000}


class TestSyncStatementsHappyPath:
    def test_single_page_saves_all(self, creds):
        http = FakeHttpClient([{
            "code": 0,
            "data": {"statements": [make_statement("s1"), make_statement("s2")], "total": 2},
        }])
        repo = FakeStatementRepository()
        from tts_business import sync_statements

        result = sync_statements(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 2
        assert result.pages == 1

    def test_multi_page(self, creds):
        http = FakeHttpClient([
            {"code": 0, "data": {"statements": [make_statement("s1")],
                                "next_page_token": "tok-1"}},
            {"code": 0, "data": {"statements": [make_statement("s2")], "total": 2}},
        ])
        repo = FakeStatementRepository()
        from tts_business import sync_statements

        result = sync_statements(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.saved == 2
        assert result.pages == 2
        assert http.calls[1]["extra_params"]["page_token"] == "tok-1"

    def test_pagination_breaks_on_error(self, creds):
        http = FakeHttpClient([
            {"code": 0, "data": {"statements": [make_statement("s1")], "next_page_token": "tok-1"}},
            {"code": 500, "message": "boom"},
        ])
        repo = FakeStatementRepository()
        from tts_business import sync_statements

        result = sync_statements(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 1
        assert result.pages == 1


class TestSyncStatementsRequestShape:
    def test_get_finance_statements_path(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"statements": []}}])
        repo = FakeStatementRepository()
        from tts_business import sync_statements

        sync_statements(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert http.calls[0]["method"] == "GET"
        assert http.calls[0]["path"] == "/finance/202309/statements"
        assert http.calls[0]["body"] is None

    def test_statement_time_field_used(self, creds):
        # statements uses statement_time, not create_time
        http = FakeHttpClient([{"code": 0, "data": {"statements": []}}])
        repo = FakeStatementRepository()
        from tts_business import sync_statements

        sync_statements(creds, {
            "shop_id": creds.shop_id,
            "statement_time_ge": 1_700_000_000,
            "statement_time_lt": 1_800_000_000,
        }, http=http, repo=repo)

        ep = http.calls[0]["extra_params"]
        assert ep["statement_time_ge"] == "1700000000"
        assert ep["statement_time_lt"] == "1800000000"
        # Sort field is statement_time, not create_time
        assert ep["sort_field"] == "statement_time"

    def test_default_page_size_50(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"statements": []}}])
        repo = FakeStatementRepository()
        from tts_business import sync_statements

        sync_statements(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert http.calls[0]["extra_params"]["page_size"] == "50"

    def test_page_size_capped_at_100(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"statements": []}}])
        repo = FakeStatementRepository()
        from tts_business import sync_statements

        sync_statements(creds, {"shop_id": creds.shop_id, "page_size": 999},
                       http=http, repo=repo)

        assert http.calls[0]["extra_params"]["page_size"] == "100"


class TestSyncStatementsErrors:
    def test_first_page_error(self, creds):
        http = FakeHttpClient([{"code": 401, "message": "auth"}])
        repo = FakeStatementRepository()
        from tts_business import sync_statements

        result = sync_statements(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert not result.ok
        assert "auth" in result.error

    def test_statement_without_id_skipped(self, creds):
        http = FakeHttpClient([{
            "code": 0,
            "data": {"statements": [make_statement("s1"), {"amount": "0"}, make_statement("s3")]},
        }])
        repo = FakeStatementRepository()
        from tts_business import sync_statements

        result = sync_statements(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.saved == 2
