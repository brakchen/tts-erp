"""TDD test suite for sync_payments business function."""

from __future__ import annotations

from typing import Any

import pytest
from domain import Creds

# ─── Fakes (reused pattern; duplicated here for self-containment) ────


class FakeHttpClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, path, *, body=None, extra_params=None, timeout=30):
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "extra_params": dict(extra_params or {}),
                "timeout": timeout,
            }
        )
        if not self._responses:
            raise AssertionError(
                f"FakeHttpClient exhausted on call #{len(self.calls)}: {method} {path}"
            )
        return self._responses.pop(0)


class FakePaymentRepository:
    def __init__(self):
        self.payments: list[tuple[str, dict]] = []
        self.fail_payment_ids: set[str] = set()

    def upsert(self, shop_id: str, payment_raw: dict[str, Any]) -> bool:
        pid = payment_raw.get("payment_id") or payment_raw.get("id")
        if not pid or pid in self.fail_payment_ids:
            return False
        self.payments.append((shop_id, dict(payment_raw)))
        return True


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def creds():
    return Creds(
        access_token="tok", shop_cipher="cipher", region="VN", shop_id="shop-1"
    )


def make_payment(id: str, amount: str = "100.00", currency: str = "VND") -> dict:
    return {
        "payment_id": id,
        "amount": amount,
        "currency": currency,
        "create_time": 1_700_000_000,
    }


# ─── Tests ────────────────────────────────────────────────────────────


class TestSyncPaymentsHappyPath:
    def test_single_page_saves_all(self, creds):
        http = FakeHttpClient(
            [
                {
                    "code": 0,
                    "data": {
                        "payments": [make_payment("p1"), make_payment("p2")],
                        "total": 2,
                    },
                }
            ]
        )
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        result = sync_payments(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 2
        assert result.total == 2
        assert result.pages == 1
        assert len(repo.payments) == 2

    def test_empty_response_returns_saved_0(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"payments": []}}])
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        result = sync_payments(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 0
        assert result.pages == 1

    def test_multi_page_pagination(self, creds):
        http = FakeHttpClient(
            [
                {
                    "code": 0,
                    "data": {
                        "payments": [make_payment("p1"), make_payment("p2")],
                        "total": 4,
                        "next_page_token": "tok-1",
                    },
                },
                {
                    "code": 0,
                    "data": {
                        "payments": [make_payment("p3"), make_payment("p4")],
                        "total": 4,
                    },
                },
            ]
        )
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        result = sync_payments(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.ok
        assert result.saved == 4
        assert result.pages == 2
        assert http.calls[1]["extra_params"].get("page_token") == "tok-1"

    def test_pagination_caps_at_50(self, creds):
        responses = [
            {
                "code": 0,
                "data": {
                    "payments": [make_payment(f"p{i}")],
                    "next_page_token": f"tok-{i}",
                },
            }
            for i in range(60)
        ]
        http = FakeHttpClient(responses)
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        result = sync_payments(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.pages == 50
        assert len(http.calls) == 50

    def test_pagination_breaks_on_subsequent_error(self, creds):
        """W1.5: a mid-pagination failure must mark the whole sync as failed
        (error set, saved rows kept) so the cron window does NOT advance
        past the gap — payments has no local watermark, the window is
        driven by sync_log status='ok'."""
        http = FakeHttpClient(
            [
                {
                    "code": 0,
                    "data": {
                        "payments": [make_payment("p1")],
                        "next_page_token": "tok-1",
                    },
                },
                {"code": 500, "message": "server error"},
            ]
        )
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        result = sync_payments(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert not result.ok
        assert result.error is not None
        assert "server error" in result.error
        assert result.saved == 1  # partial data is kept
        assert result.pages == 1


class TestSyncPaymentsRequestShape:
    def test_get_method_and_finance_payments_path(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"payments": []}}])
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        sync_payments(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert http.calls[0]["method"] == "GET"
        assert http.calls[0]["path"] == "/finance/202309/payments"
        # GET should not have body
        assert http.calls[0]["body"] is None

    def test_create_time_in_query_string_as_string_int(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"payments": []}}])
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        sync_payments(
            creds,
            {
                "shop_id": creds.shop_id,
                "create_time_ge": 1_700_000_000,
                "create_time_lt": 1_800_000_000,
            },
            http=http,
            repo=repo,
        )

        ep = http.calls[0]["extra_params"]
        # Payments API accepts string in query string (finance endpoint is lenient)
        assert ep["create_time_ge"] == "1700000000"
        assert ep["create_time_lt"] == "1800000000"

    def test_extra_params_have_shop_cipher_and_paging(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"payments": []}}])
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        sync_payments(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        ep = http.calls[0]["extra_params"]
        assert ep["shop_cipher"] == creds.shop_cipher
        assert ep["sort_field"] == "create_time"
        assert ep["sort_order"] == "DESC"
        assert ep["page_size"] == "50"

    def test_page_size_capped_at_100(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"payments": []}}])
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        sync_payments(
            creds, {"shop_id": creds.shop_id, "page_size": 500}, http=http, repo=repo
        )

        assert http.calls[0]["extra_params"]["page_size"] == "100"


class TestSyncPaymentsErrors:
    def test_first_page_error_returns_error_result(self, creds):
        http = FakeHttpClient([{"code": 401, "message": "auth fail"}])
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        result = sync_payments(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert not result.ok
        assert result.saved == 0
        assert result.error is not None
        assert "auth fail" in result.error

    def test_payment_without_id_is_skipped(self, creds):
        http = FakeHttpClient(
            [
                {
                    "code": 0,
                    "data": {
                        "payments": [
                            make_payment("p1"),
                            {"amount": "50"},
                            make_payment("p3"),
                        ]
                    },
                }
            ]
        )
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        result = sync_payments(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.saved == 2

    def test_repo_failure_does_not_stop_iteration(self, creds):
        http = FakeHttpClient(
            [
                {
                    "code": 0,
                    "data": {
                        "payments": [
                            make_payment("p1"),
                            make_payment("p2"),
                            make_payment("p3"),
                        ]
                    },
                }
            ]
        )
        repo = FakePaymentRepository()
        repo.fail_payment_ids.add("p2")
        from tts_business import sync_payments

        result = sync_payments(creds, {"shop_id": creds.shop_id}, http=http, repo=repo)

        assert result.saved == 2


class TestDirtyBodyInput:
    """W1.5/W3.3-early: garbage types in the sync body must not crash the
    business layer with ValueError (which surfaces as a 500). Non-numeric
    page_size falls back to the default; non-numeric time filters are
    dropped from the upstream request."""

    def test_non_numeric_page_size_falls_back_to_default(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"payments": []}}])
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        result = sync_payments(
            creds,
            {"shop_id": creds.shop_id, "page_size": "abc"},
            http=http,
            repo=repo,
        )

        assert result.ok
        assert http.calls[0]["extra_params"]["page_size"] == "50"

    def test_non_numeric_time_filter_is_dropped(self, creds):
        http = FakeHttpClient([{"code": 0, "data": {"payments": []}}])
        repo = FakePaymentRepository()
        from tts_business import sync_payments

        result = sync_payments(
            creds,
            {"shop_id": creds.shop_id, "create_time_ge": "not-a-number"},
            http=http,
            repo=repo,
        )

        assert result.ok
        assert "create_time_ge" not in http.calls[0]["extra_params"]
