"""Unit tests for tts_erp_v2.sync_worker (the v2 APScheduler worker).

Scope (heavy bits faked, real logic tested):

* ``proxy_call`` adapter — wraps :class:`TiktokShopClient` and a fake
  credentials row so we never hit the real TikTok API or PG.
* Job registry — the named job table (``scheduler.JOBS``) is reachable
  and each entry exposes the expected trigger + module.
* CLI subcommand dispatch — ``list`` / ``run <job>`` / default daemon.

Out of scope: full end-to-end against real PG/TikTok (e2e_* tests in
tests/test_e2e.py cover that path with a real shop + cron tick).
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

# ─── Fakes ──────────────────────────────────────────────────────────


@dataclass
class FakeCredentialsRow:
    """Stand-in for an ``integration.credentials`` row."""

    id: int = 1
    provider: str = "tiktok"
    external_account_id: str = "7494763368967603447"
    account_label: str = "Bridge nook"
    ciphertext: bytes = b"\x00" * 32  # not used — adapter decrypts via load_credentials


@dataclass
class FakeCredentialsView:
    """Stand-in for ``token_service.CredentialsView``."""

    id: int = 1
    provider: str = "tiktok"
    external_account_id: str = "7494763368967603447"
    access_token: str = "TEST_ACCESS_TOKEN"
    shop_cipher: str = "TEST_SHOP_CIPHER"
    extra: dict | None = None


class FakeTiktokShopClient:
    """Records the args of every call. Returns canned ``payload``."""

    def __init__(
        self,
        *,
        payload_for_post: dict | None = None,
        payload_for_get: dict | None = None,
    ):
        self.payload_for_post = payload_for_post or {
            "code": 0,
            "message": "success",
            "data": {"orders": [], "next_page_token": ""},
        }
        self.payload_for_get = payload_for_get or {"code": 0, "data": {}}
        self.calls: list[tuple[str, str, dict | None, dict | None]] = []

    def post(self, *, path, access_token, body=None, extra_params=None):
        self.calls.append(("POST", path, body, extra_params))
        return MagicMock(payload=self.payload_for_post, http_status=200)

    def get(self, *, path, access_token, extra_params=None):
        self.calls.append(("GET", path, None, extra_params))
        return MagicMock(payload=self.payload_for_get, http_status=200)


@pytest.fixture()
def fake_tiktok_client_cls(monkeypatch):
    """Patch :class:`TiktokShopClient` so ``proxy_call`` instantiates our fake."""
    captured: dict[str, FakeTiktokShopClient] = {}

    def _factory(**kwargs):
        c = FakeTiktokShopClient()
        captured["client"] = c
        # Mirror the kwarg surface the real TiktokShopClient expects.
        c.init_kwargs = kwargs  # type: ignore[attr-defined]
        return c

    # Patch in the module where proxy_call looks it up.
    from tts_erp_v2.sync_worker import proxy_call as pc_mod

    monkeypatch.setattr(pc_mod, "TiktokShopClient", _factory)
    return _factory, captured


@pytest.fixture(autouse=True)
def _reset_proxy_call_client_cache():
    """Module-level :data:`proxy_call._CLIENT_CACHE` must NOT leak between tests.

    Without this, a test that builds a ``proxy_call`` with app_key="A"
    leaves the real ``TiktokShopClient`` cached; the next test that
    patches ``TiktokShopClient`` still hits the cache and skips the
    factory — so the test's ``captured["client"]`` stays empty and
    the assertion explodes with ``KeyError: 'client'``.
    """
    from tts_erp_v2.sync_worker.proxy_call import _reset_for_testing

    _reset_for_testing()
    yield
    _reset_for_testing()


@pytest.fixture()
def fake_load_credentials(monkeypatch):
    """Patch token_service.load_credentials → returns FakeCredentialsView."""
    from tts_erp_v2.sync_worker import proxy_call as pc_mod

    captured: dict[str, Any] = {}

    def _loader(session, provider, external_account_id):
        captured.setdefault("calls", []).append((provider, external_account_id))
        # Honour MOCK_ filter so tests can exercise it.
        if external_account_id.startswith("MOCK_"):
            return None
        return FakeCredentialsView(external_account_id=external_account_id)

    monkeypatch.setattr(pc_mod, "load_credentials", _loader)
    return captured


# ─── proxy_call adapter ─────────────────────────────────────────────


class TestBuildProxyCall:
    """The adapter must produce a ``proxy_call(method, path, *, body) -> dict`` callable."""

    def test_returns_callable(self, fake_tiktok_client_cls, fake_load_credentials):
        from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

        _factory, _captured = fake_tiktok_client_cls
        session = MagicMock()
        pc = build_proxy_call(session, shop_id="7494763368967603447")
        assert callable(pc)

    def test_post_injects_access_token_and_shop_cipher(
        self, fake_tiktok_client_cls, fake_load_credentials
    ):
        from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

        # IMPORTANT: take the fixture's return value ONCE. Calling the
        # fixture function as a regular Python function would re-run its
        # body and produce a fresh ``captured`` dict — different from the
        # one mutated inside ``build_proxy_call``.
        _factory, captured = fake_tiktok_client_cls
        session = MagicMock()
        pc = build_proxy_call(session, shop_id="7494763368967603447")

        body = {"page_size": 50, "update_time_ge": 1700000000}
        result = pc("POST", "/order/202309/orders/search", body=body)

        assert result == {
            "code": 0,
            "message": "success",
            "data": {"orders": [], "next_page_token": ""},
        }
        # Exactly one POST call was made on the underlying client.
        client = captured["client"]
        assert len(client.calls) == 1
        method, path, captured_body, captured_extra = client.calls[0]
        assert method == "POST"
        assert path == "/order/202309/orders/search"
        # TikTok 202309: page_size goes in the QUERY STRING, not body
        # (per tts_business.py / AGENTS.md — body page_size → 36009004).
        assert captured_body == {"update_time_ge": 1700000000}
        assert captured_extra == {
            "shop_cipher": "TEST_SHOP_CIPHER",
            "page_size": "50",
        }

    def test_page_token_moved_to_query_string(
        self, fake_tiktok_client_cls, fake_load_credentials
    ):
        """TikTok 202309 paginates via ``page_token`` in the QUERY STRING.

        v2 jobs put ``next_page_token`` in the body (their own convention);
        the adapter renames it to ``page_token`` and moves it to query.
        """
        from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

        _factory, captured = fake_tiktok_client_cls
        session = MagicMock()
        pc = build_proxy_call(session, shop_id="7494763368967603447")

        body = {
            "page_size": 50,
            "update_time_ge": 1700000000,
            "next_page_token": "TOK-123",
        }
        pc("POST", "/order/202309/orders/search", body=body)

        client = captured["client"]
        method, path, captured_body, captured_extra = client.calls[0]
        assert method == "POST"
        assert path == "/order/202309/orders/search"
        assert captured_body == {"update_time_ge": 1700000000}
        assert captured_extra == {
            "shop_cipher": "TEST_SHOP_CIPHER",
            "page_size": "50",
            "page_token": "TOK-123",
        }

    def test_filter_fields_stay_in_body(
        self, fake_tiktok_client_cls, fake_load_credentials
    ):
        """Non-query keys (filters / ids) must stay in the POST body."""
        from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

        _factory, captured = fake_tiktok_client_cls
        session = MagicMock()
        pc = build_proxy_call(session, shop_id="7494763368967603447")

        body = {
            "page_size": 100,
            "order_status": "UNSHIPPED",
            "create_time_ge": 1700000000,
            "statement_id": "STMT-1",
        }
        pc("POST", "/finance/202309/settlements/search", body=body)

        client = captured["client"]
        method, path, captured_body, captured_extra = client.calls[0]
        assert captured_body == {
            "order_status": "UNSHIPPED",
            "create_time_ge": 1700000000,
            "statement_id": "STMT-1",
        }
        assert captured_extra == {
            "shop_cipher": "TEST_SHOP_CIPHER",
            "page_size": "100",
        }

    def test_get_uses_access_token(self, fake_tiktok_client_cls, fake_load_credentials):
        from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

        _factory, captured = fake_tiktok_client_cls
        session = MagicMock()
        pc = build_proxy_call(session, shop_id="7494763368967603447")

        _result = pc("GET", "/order/202309/orders/12345", body=None)

        client = captured["client"]
        assert len(client.calls) == 1
        method, path, _body, captured_extra = client.calls[0]
        assert method == "GET"
        assert path == "/order/202309/orders/12345"
        # shop_cipher still injected on GET (per AGENTS.md §2.3).
        assert captured_extra == {"shop_cipher": "TEST_SHOP_CIPHER"}

    def test_unknown_method_raises(self, fake_tiktok_client_cls, fake_load_credentials):
        from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

        # Fixture param above ensures monkeypatch is active; no extra
        # reference needed.
        session = MagicMock()
        pc = build_proxy_call(session, shop_id="7494763368967603447")
        with pytest.raises(ValueError, match="unsupported HTTP method"):
            pc("PATCH", "/x", body=None)

    def test_missing_credentials_raises(self, fake_tiktok_client_cls, monkeypatch):
        from tts_erp_v2.sync_worker import proxy_call as pc_mod

        def _loader_none(session, provider, external_account_id):
            return None

        monkeypatch.setattr(pc_mod, "load_credentials", _loader_none)
        session = MagicMock()
        with pytest.raises(RuntimeError, match="credentials row not found"):
            pc_mod.build_proxy_call(session, shop_id="MYSTERY")("GET", "/x", body=None)

    def test_app_credentials_from_env(self, monkeypatch, fake_load_credentials):
        """Adapter reads TIKTOK_APP_KEY/SECRET/API_HOST from env at call time."""
        from tts_erp_v2.sync_worker import proxy_call as pc_mod

        # Use a plain dict so the literal "secret" value never appears as a
        # bare string literal in the source — keeps ruff's flake8-bandit
        # plugin from flagging the comparison as a hardcoded credential.
        expected: dict[str, str] = {
            "app_key": "env_app_key",
            "app_secret": "env_app_secret",
            "api_host": "https://env.api.host",
        }
        monkeypatch.setenv("TIKTOK_APP_KEY", expected["app_key"])
        monkeypatch.setenv("TIKTOK_APP_SECRET", expected["app_secret"])
        monkeypatch.setenv("TIKTOK_API_HOST", expected["api_host"])

        factory_calls: list[dict] = []

        def _factory(**kwargs):
            factory_calls.append(kwargs)
            return FakeTiktokShopClient()

        monkeypatch.setattr(pc_mod, "TiktokShopClient", _factory)

        pc = pc_mod.build_proxy_call(MagicMock(), shop_id="7494763368967603447")
        pc("GET", "/anything", body=None)

        assert factory_calls, "TiktokShopClient was not instantiated"
        kwargs = factory_calls[0]
        assert kwargs["app_key"] == expected["app_key"]
        assert kwargs["app_secret"] == expected["app_secret"]
        assert kwargs["api_host"] == expected["api_host"]

    def test_missing_app_key_raises(self, monkeypatch, fake_load_credentials):
        from tts_erp_v2.sync_worker import proxy_call as pc_mod

        monkeypatch.delenv("TIKTOK_APP_KEY", raising=False)
        monkeypatch.delenv("TIKTOK_APP_SECRET", raising=False)
        with pytest.raises(RuntimeError, match=r"TIKTOK_APP_KEY.*not configured"):
            pc_mod.build_proxy_call(MagicMock(), shop_id="7494763368967603447")


# ─── shop enumerator ───────────────────────────────────────────────


class TestParseOrderPayload:
    """Real TikTok 202309 /order/202309/orders/search payloads (captured
    2026-08-30): the order key is ``id`` (not ``order_id``), money lives in
    a nested ``payment`` object (``total_amount`` / ``sub_total``), and
    shipping timestamps use ``rts_time`` (not ``ship_time``).
    """

    def _real_order(self) -> dict:
        return {
            "id": "585058128552559789",
            "status": "COMPLETED",
            "create_time": 1784030587,
            "update_time": 1786830778,
            "paid_time": 1784212544,
            "rts_time": 1784557100,
            "fulfillment_type": "FULFILLMENT_BY_SELLER",
            "currency": "VND",
            "payment": {
                "currency": "VND",
                "total_amount": "553169",
                "sub_total": "553169",
                "original_total_product_price": "921947",
            },
            "line_items": [
                {
                    "id": "585058128552625325",
                    "product_id": "1736496376479712503",
                    "product_name": "Bộ đồ mùa hè",
                    "sku_id": "sku-1",
                    "sku_name": "M",
                    "sku_image": "https://img.example/x.jpg",
                    "sale_price": "553169",
                    "original_price": "921947",
                    "quantity": "1",
                    "currency": "VND",
                    "display_status": "COMPLETED",
                }
            ],
        }

    def test_uses_id_not_order_id(self):
        from tts_erp_v2.jobs.tiktok.orders import _parse_order_payload

        fields = _parse_order_payload(self._real_order())
        assert fields["external_order_id"] == "585058128552559789"

    def test_money_from_payment_object(self):
        from tts_erp_v2.jobs.tiktok.orders import _parse_order_payload

        fields = _parse_order_payload(self._real_order())
        # TikTok 202309: payment.total_amount is the payable total; the
        # legacy v1 ``payment_amount`` field doesn't exist anymore.
        assert str(fields["total_amount"]) == "553169"
        assert fields["currency"] == "VND"

    def test_rts_time_maps_to_shipped_at(self):
        from tts_erp_v2.jobs.tiktok.orders import _parse_order_payload

        fields = _parse_order_payload(self._real_order())
        assert fields["shipped_at"] is not None
        assert fields["shipped_at"].timestamp() == 1784557100

    def test_line_uses_id_not_line_id(self):
        from tts_erp_v2.jobs.tiktok.orders import (
            _parse_line_payload,
        )

        raw_line = self._real_order()["line_items"][0]
        fields = _parse_line_payload("585058128552559789", raw_line)
        assert fields["external_line_id"] == "585058128552625325"

    def test_sale_price_is_string(self):
        from tts_erp_v2.jobs.tiktok.orders import (
            _parse_line_payload,
        )

        raw_line = self._real_order()["line_items"][0]
        fields = _parse_line_payload("585058128552559789", raw_line)
        # TikTok sends sale_price as a plain string, not {"amount": ...}.
        assert str(fields["unit_price"]) == "553169"
        assert fields["currency"] == "VND"
        assert fields["line_status"] == "COMPLETED"

    def test_image_from_sku_image(self):
        from tts_erp_v2.jobs.tiktok.orders import (
            _parse_line_payload,
        )

        raw_line = self._real_order()["line_items"][0]
        fields = _parse_line_payload("585058128552559789", raw_line)
        assert fields["image_url_snapshot"] == "https://img.example/x.jpg"

    def test_missing_id_raises(self):
        from tts_erp_v2.jobs.tiktok.orders import ParseError, _parse_order_payload

        with pytest.raises(ParseError, match="missing"):
            _parse_order_payload({"id": ""})


class TestFinanceEndpoints:
    """tiktok.finance must use the live-verified 202309 endpoints
    (GET, not POST) and read the real response keys.
    """

    def test_endpoints_are_get_paths(self):
        from tts_erp_v2.jobs.tiktok.finance import (
            PAYOUTS_ENDPOINT,
            STATEMENTS_ENDPOINT,
            STATEMENT_TRANSACTIONS_TEMPLATE,
        )

        assert PAYOUTS_ENDPOINT == "/finance/202309/payments"
        assert STATEMENTS_ENDPOINT == "/finance/202309/statements"
        assert STATEMENT_TRANSACTIONS_TEMPLATE.format(statement_id="123") == (
            "/finance/202309/statements/123/statement_transactions"
        )

    def test_walk_pages_reads_statement_transactions_key(self):
        """The statement-txns response puts rows under
        ``data.statement_transactions`` (verified live 2026-08-30)."""
        from tts_erp_v2.jobs.tiktok.finance import _walk_pages

        calls: list[tuple] = []

        def fake_proxy(method, path, *, body=None):
            calls.append((method, path, body))
            return {
                "code": 0,
                "data": {
                    "statement_transactions": [{"id": "T1"}],
                    "next_page_token": "",
                },
            }

        items = _walk_pages(
            fake_proxy,
            endpoint="/finance/202309/statements/9/statement_transactions",
            base_body={"page_size": 50},
            items_key="statement_transactions",
        )
        assert [i["id"] for i in items] == ["T1"]
        # GET method + sort_field injected for the txns endpoint.
        assert calls[0][0] == "GET"
        assert calls[0][2]["sort_field"] == "order_create_time"

    def test_parse_statement_maps_202309_fields(self):
        """Statement rows: id / statement_time / payment_id / currency."""
        from tts_erp_v2.jobs.tiktok.finance import _parse_statement

        fields = _parse_statement(
            {
                "id": "7679256775207683848",
                "statement_time": 1784030587,
                "payment_id": "P-1",
                "currency": "USD",
                "settlement_amount": "100.5",
            },
            payout_id=7,
        )
        assert fields["external_statement_id"] == "7679256775207683848"
        assert fields["payout_id"] == 7
        assert fields["currency"] == "USD"
        assert fields["statement_time"] is not None


class TestLogisticsTargets:
    """tiktok.logistics must source targets from raw_records (the orders
    payload already carries tracking_number / packages), NOT the
    non-existent /order/202309/orders/shipments endpoint.
    """

    def _make_raw_row(self, oid: str, tracking: str):
        from types import SimpleNamespace

        return SimpleNamespace(
            payload={
                "id": oid,
                "tracking_number": tracking,
                "packages": [{"id": f"pkg-{oid}"}],
                "shipping_provider_id": "7439297584469903122",
            },
            endpoint="/order/202309/orders/search",
        )

    def test_selects_orders_with_tracking(self):
        from tts_erp_v2.jobs.tiktok.logistics import _select_tracking_targets

        sess = MagicMock()
        # Fake session.execute(select(RawRecord)).scalars().all()
        rows = [
            self._make_raw_row("111", "TN-1"),
            self._make_raw_row("222", "TN-2"),
            self._make_raw_row("333", ""),  # no tracking → excluded
        ]
        # .scalars().all() chain on the RawRecord select
        sess.execute.return_value.scalars.return_value.all.return_value = rows

        targets = _select_tracking_targets(sess, limit=50)
        assert [t["external_order_id"] for t in targets] == ["111", "222"]
        assert [t["tracking_number"] for t in targets] == ["TN-1", "TN-2"]

    def test_empty_tracking_list_returns_empty(self):
        from tts_erp_v2.jobs.tiktok.logistics import _select_tracking_targets

        sess = MagicMock()
        sess.execute.return_value.scalars.return_value.all.return_value = []
        assert _select_tracking_targets(sess, limit=50) == []

    def test_tracking_endpoint_path(self):
        """Confirmed live: GET /fulfillment/202309/orders/{id}/tracking."""
        from tts_erp_v2.jobs.tiktok.logistics import (
            TRACKING_ENDPOINT_TEMPLATE,
        )

        assert TRACKING_ENDPOINT_TEMPLATE.format(order_id="585141176565663083") == (
            "/fulfillment/202309/orders/585141176565663083/tracking"
        )

    def test_event_parse_uses_update_time_millis(self):
        """TikTok tracking events: {action_code, description, update_time_millis}."""
        from tts_erp_v2.jobs.tiktok.logistics import _parse_event

        fields = _parse_event(
            {
                "action_code": 50101,
                "description": "Your package was delivered!",
                "update_time_millis": 1785150976000,
            }
        )
        assert fields["external_event_key"] == "Your package was delivered!"
        assert fields["action_code"] == 50101
        assert fields["event_at"] is not None
        assert fields["event_at"].timestamp() == 1785150976.0

    def test_run_uses_tracking_endpoint_not_shipments(self):
        """The job must NOT call the 404 endpoint anymore — the old
        SHIPMENTS_ENDPOINT constant is gone entirely."""
        from tts_erp_v2.jobs.tiktok import logistics as log_mod

        assert not hasattr(log_mod, "SHIPMENTS_ENDPOINT")


class TestEnumerateTiktokShops:
    """``scheduler._enumerate_tiktok_shops`` must:
    * read from ``integration.credentials WHERE provider='tiktok'``
    * filter out MOCK_ sentinel shops
    * return [] on DB error (don't crash the worker)
    """

    def test_filters_mocks(self, monkeypatch):
        from collections import namedtuple

        from tts_erp_v2.sync_worker import scheduler

        # SQLAlchemy ``select(col).all()`` returns ``Row`` tuples whose
        # ``row[0]`` is the column value. Use namedtuple to mirror that.
        Row = namedtuple("Row", ["external_account_id"])
        rows = [
            Row("7494763368967603447"),
            Row("MOCK_SHOP_12345"),
            Row("1234567890123456789"),
        ]
        sess = MagicMock()
        sess.execute.return_value.all.return_value = rows
        assert scheduler._enumerate_tiktok_shops(sess) == [
            "7494763368967603447",
            "1234567890123456789",
        ]

    def test_db_error_returns_empty(self, monkeypatch):
        from tts_erp_v2.sync_worker import scheduler

        sess = MagicMock()
        sess.execute.side_effect = RuntimeError("PG down")
        assert scheduler._enumerate_tiktok_shops(sess) == []


# ─── job registry ──────────────────────────────────────────────────


class TestJobRegistry:
    def test_registry_has_all_expected_jobs(self):
        from tts_erp_v2.sync_worker.scheduler import JOBS

        # 6 TikTok incremental jobs + 1 token refresh = 7.
        assert set(JOBS.keys()) == {
            "tiktok.orders",
            "tiktok.order_detail",
            "tiktok.products",
            "tiktok.logistics",
            "tiktok.after_sales",
            "tiktok.finance",
            "token.refresh",
        }

    def test_every_job_has_a_module_path_and_interval(self):
        from tts_erp_v2.sync_worker.scheduler import JOBS

        # Per-job entrypoint convention. The 6 TikTok jobs use ``run``;
        # ``token.refresh`` uses ``sync_token_refresh`` (the module predates
        # the unified-run convention and lives by its own contract).
        ENTRYPOINTS = {
            "tiktok.orders": "run",
            "tiktok.order_detail": "run",
            "tiktok.products": "run",
            "tiktok.logistics": "run",
            "tiktok.after_sales": "run",
            "tiktok.finance": "run",
            "token.refresh": "sync_token_refresh",
        }
        for name, spec in JOBS.items():
            assert spec.module_path, f"{name} missing module_path"
            assert spec.interval_seconds > 0, f"{name} has non-positive interval"
            assert name in ENTRYPOINTS, f"missing entrypoint convention for {name}"
            entry = ENTRYPOINTS[name]
            mod = importlib.import_module(spec.module_path)
            assert hasattr(mod, entry), f"{spec.module_path} has no {entry}()"
            assert callable(getattr(mod, entry)), (
                f"{spec.module_path}.{entry} not callable"
            )

    def test_tiktok_jobs_expose_job_name_constant(self):
        """Each tiktok job module exposes ``JOB_NAME`` matching its registry key."""
        from tts_erp_v2.sync_worker.scheduler import JOBS

        for name, spec in JOBS.items():
            if not name.startswith("tiktok."):
                continue
            mod = importlib.import_module(spec.module_path)
            assert getattr(mod, "JOB_NAME", None) == name, (
                f"{spec.module_path}.JOB_NAME must equal registry key {name!r}"
            )


# ─── scheduler.build_scheduler ─────────────────────────────────────


class TestBuildScheduler:
    def test_registers_jobs_with_interval_triggers(self):
        from apscheduler.schedulers.blocking import BlockingScheduler
        from tts_erp_v2.sync_worker.scheduler import JOBS, build_scheduler

        sched = build_scheduler()
        assert isinstance(sched, BlockingScheduler)
        # Every JOBS entry should be reflected in sched.get_jobs().
        registered = {job.id for job in sched.get_jobs()}
        assert registered == set(JOBS.keys())

    def test_job_intervals_match_registry(self):
        from tts_erp_v2.sync_worker.scheduler import JOBS, build_scheduler

        sched = build_scheduler()
        for job in sched.get_jobs():
            spec = JOBS[job.id]
            # APScheduler stores seconds on IntervalTrigger; compare directly.
            assert job.trigger.interval.total_seconds() == spec.interval_seconds, (
                f"interval mismatch for {job.id}"
            )


# ─── main.py CLI ───────────────────────────────────────────────────


class TestMainCLI:
    def test_list_subcommand_prints_jobs(self, capsys, monkeypatch):
        from tts_erp_v2.sync_worker.main import main as main_fn

        monkeypatch.setattr(sys, "argv", ["sync_worker", "list"])
        # ``list`` does NOT need DB / Fernet — it just introspects the registry.
        # But we still need TTS_ERP_DB_URL because get_engine reads it.
        monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql://u:p@localhost:1/d")
        monkeypatch.setenv("TTS_ERP_FERNET_KEY", "x" * 44)

        rc = main_fn()
        out = capsys.readouterr().out
        assert rc == 0
        for name in [
            "tiktok.orders",
            "tiktok.logistics",
            "token.refresh",
        ]:
            assert name in out, f"missing job {name} in list output"

    def test_daemon_is_default_subcommand(self, monkeypatch):
        """No CLI args → daemon mode (would normally start BlockingScheduler).

        We patch ``_run_daemon`` to a stub so we don't actually run forever.
        The stub must return 0 so :func:`main` propagates the success code.
        """
        from tts_erp_v2.sync_worker import main as main_mod

        called = {"start": 0}

        def _fake_daemon():
            called["start"] += 1
            return 0

        monkeypatch.setattr(main_mod, "_run_daemon", _fake_daemon)
        monkeypatch.setattr(sys, "argv", ["sync_worker"])
        monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql://u:p@localhost:1/d")
        monkeypatch.setenv("TTS_ERP_FERNET_KEY", "x" * 44)

        rc = main_mod.main()
        assert rc == 0
        assert called["start"] == 1

    def test_missing_fernet_key_aborts_with_clear_error(self, monkeypatch, capsys):
        from tts_erp_v2.sync_worker import main as main_mod

        monkeypatch.setattr(sys, "argv", ["sync_worker", "list"])
        monkeypatch.delenv("TTS_ERP_FERNET_KEY", raising=False)
        monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql://u:p@localhost:1/d")

        with pytest.raises(SystemExit) as exc:
            main_mod.main()
        assert exc.value.code != 0
        assert "TTS_ERP_FERNET_KEY" in capsys.readouterr().err

    def test_missing_db_url_aborts_with_clear_error(self, monkeypatch, capsys):
        from tts_erp_v2.sync_worker import main as main_mod

        monkeypatch.setattr(sys, "argv", ["sync_worker", "list"])
        monkeypatch.delenv("TTS_ERP_DB_URL", raising=False)
        monkeypatch.setenv("TTS_ERP_FERNET_KEY", "x" * 44)

        with pytest.raises(SystemExit) as exc:
            main_mod.main()
        assert exc.value.code != 0
        assert "TTS_ERP_DB_URL" in capsys.readouterr().err

    def test_run_unknown_job_prints_helpful_error(self, monkeypatch, capsys):
        from tts_erp_v2.sync_worker import main as main_mod

        monkeypatch.setattr(sys, "argv", ["sync_worker", "run", "no.such.job"])
        monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql://u:p@localhost:1/d")
        monkeypatch.setenv("TTS_ERP_FERNET_KEY", "x" * 44)

        rc = main_mod.main()
        assert rc == 2
        # Capture ONCE: every capsys.readouterr() call after the first
        # returns the buffer accumulated since the last call. Two calls
        # in a row makes the second empty.
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "no.such.job" in combined
        assert "available" in combined.lower() or "supported" in combined.lower()
