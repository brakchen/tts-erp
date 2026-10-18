"""HTTP + service tests for the managed plugin status protocol (design §6–§11).

Scope of this module
--------------------
It pins the *runtime* behaviour the pure contract/policy modules cannot
express: instance provisioning and its dedicated credential, purpose-bound
authorisation that no role can buy, one atomic report transaction, idempotent
replay, out-of-order archival, the decision lifecycle with frozen receipts,
manual latches, and the outbox/lifecycle sweep.

Boundaries that are substituted, and why
---------------------------------------
Only three, all outside the service: the webhook HTTP transport, the clock, and
an injected failure that simulates "the commit path blew up". The service
itself, the policy, the row locks and the outbox run for real against the
isolated test database.

Migrated schema
---------------
The isolated runner supplies the real 0071 schema; no test bootstrap DDL.
"""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.plugin_status import StatusDecision
from tts_erp_v2.db.models.security import ApiKey
from tts_erp_v2.middleware.auth import AuthMiddleware
from tts_erp_v2.plugin.status.contracts import (
    InstanceRegistrationRequest,
    status_report_from_wire,
)
from tts_erp_v2.plugin.status.scheduler import run_scheduled
from tts_erp_v2.plugin.status.service import (
    AuthenticatedInstance,
    StatusServiceError,
    authenticate_report,
    ingest_report,
)

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SELLER = "8800000000000000901"
ADVERTISER = "TEST_ADVERTISER_A"

#: §4.1: a binding epoch changes on invalidation, target switch or an
#: unprovable context — not on every report. Reports in one test therefore share
#: an epoch unless a test explicitly asks for a new one, exactly as a real plugin
#: would, and so a resume may confirm the epoch it was issued against.
DEFAULT_BINDING_EPOCH = "8f755b12-dbc8-4542-bf54-049f72d230fc"


def json_text(value: Any) -> str:
    return json.dumps(value, default=str)


@pytest.fixture(autouse=True)
def _clean_status_rows():
    """Wipe this module's rows both before *and* after each test.

    Two reasons it must also run at teardown: the handlers commit inside the
    request, so the per-test savepoint does not cover them; and the shop
    fixture deletes ``commerce.shops``, which the instance's ``RESTRICT`` foreign
    key would refuse while a row still points at it.
    """
    from tts_erp_v2.db.base import get_session_factory
    from tts_erp_v2.db.models.plugin_status import (
        StatusDecision,
        StatusInstance,
        StatusNotificationOutbox,
        StatusReport,
    )

    models = (
        StatusNotificationOutbox,
        StatusDecision,
        StatusReport,
        StatusInstance,
    )
    factory = get_session_factory()

    def _wipe() -> None:
        with factory() as sess:
            # Child -> parent, so the foreign keys stay satisfied while wiping.
            for model in models:
                sess.execute(delete(model))
            # The provisioned status keys are this module's own data too, and
            # they are not TEST_-prefixed because the minting code is the same
            # one production uses.
            sess.execute(delete(ApiKey).where(ApiKey.name.like("plugin_status:%")))
            sess.commit()

    _wipe()
    yield
    _wipe()


@pytest.fixture()
def session(db_engine):
    with Session(db_engine) as sess:
        yield sess


# ── App fixtures ──────────────────────────────────────────────────────


@pytest.fixture()
def status_client(db_engine, monkeypatch):
    """The status router on its own, in the project's ``off`` dev posture.

    Two reasons this fixture is deliberately *not* behind ``enforce``:

    * the report route re-derives its own credential instead of trusting the
      middleware grant, because §9.2 requires the boundary to hold in
      ``off``/``shadow`` too, where the middleware hands out a bypass grant;
    * the middleware's route matrix for ``/v2/plugin/status/report`` still
      defaults to ADMIN, because granting it readwrite is an integration-step
      change to ``access/_policy.py``.

    The admin role matrix is proved separately, against the real middleware, by
    ``status_client_auth``.
    """
    from tts_erp_v2.api.v2 import plugin_status

    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(plugin_status.router)
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def status_client_auth(db_engine, monkeypatch):
    """The status router behind the real ``AuthMiddleware``."""
    from tts_erp_v2.api.v2 import plugin_status

    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(plugin_status.router)
    with TestClient(app) as client:
        yield client


# ── Fixtures / builders ───────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def _shop(db_engine):
    """One shop row for the whole module.

    Module scope, not function scope: ``status_instances.shop_pk`` is a
    ``RESTRICT`` foreign key, so the shop row may only disappear once nothing
    points at it. Creating it per test made teardown order the deciding factor
    between two function-scoped fixtures; creating it once leaves a single
    teardown that runs after the last per-test row wipe.
    """
    with Session(db_engine) as sess:
        sess.execute(delete(ChannelAccount).where(ChannelAccount.shop_id == SELLER))
        sess.add(
            ChannelAccount(
                platform="tiktok",
                shop_id=SELLER,
                account_name="TEST_STATUS_SHOP",
                status="active",
            )
        )
        sess.commit()
    yield
    with Session(db_engine) as sess:
        sess.execute(delete(ChannelAccount).where(ChannelAccount.shop_id == SELLER))
        sess.commit()


@pytest.fixture()
def admin_key(db_engine) -> str:
    return _api_key(db_engine, "admin", "test_status_admin", "active")


@pytest.fixture()
def rw_key(db_engine) -> str:
    return _api_key(db_engine, "readwrite", "test_status_rw", "active")


def _api_key(db_engine, role: str, suffix: str, status: str) -> str:
    import hashlib

    plaintext = f"ttserp_{role}_{suffix}"
    digest = hashlib.sha256(plaintext.encode()).hexdigest()
    with Session(db_engine) as sess:
        sess.execute(delete(ApiKey).where(ApiKey.key_hash == digest))
        sess.add(
            ApiKey(
                key_hash=digest,
                key_prefix=plaintext[:16],
                name="TEST_" + suffix,
                role=role,
                status=status,
            )
        )
        sess.commit()
    return plaintext


def register(
    client: TestClient,
    admin_key: str,
    *,
    seller: str = SELLER,
    advertiser: str | None = ADVERTISER,
    capabilities: tuple[str, ...] = ("binding.recover.v1", "monitor.resume.v1"),
    plugin_id: str = "ads-monitor",
) -> dict[str, Any]:
    """Provision one instance through the admin API and return its payload."""
    scope: dict[str, Any] = {"kind": "shop", "sellerId": seller}
    if advertiser is not None:
        scope["advertiserId"] = advertiser
    resp = client.post(
        "/v2/plugin/admin/instances",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={
            "pluginId": plugin_id,
            "scope": scope,
            "capabilities": list(capabilities),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _report_body(
    instance_id: str,
    *,
    seq: int,
    observed_at: datetime,
    event_id: str | None = None,
    event_type: str = "binding_invalidated",
    reason_code: str = "TAB_CLOSED",
    incident_id: str | None = None,
    binding_phase: str = "invalid",
    candidate_count: int = 1,
    identity_verified: bool = False,
    execution_phase: str = "suspended",
    execution_reason: str = "PAGE_INVALID",
    monitor_enabled: bool = True,
    snapshot_complete: bool = False,
    snapshot_at: datetime | None = None,
    seller: str = SELLER,
    advertiser: str | None = ADVERTISER,
    epoch: str | None = None,
    decision_results: list[dict[str, Any]] | None = None,
    capabilities: tuple[str, ...] = ("binding.recover.v1", "monitor.resume.v1"),
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schemaVersion": 1,
        "pluginId": "ads-monitor",
        "pluginVersion": "0.4.26",
        "instanceId": instance_id,
        "eventId": event_id or str(uuid.uuid4()),
        "reportSeq": str(seq),
        "observedAt": observed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "eventType": event_type,
        "reasonCode": reason_code,
        "scope": {"kind": "shop", "sellerId": seller},
        "binding": {
            "epoch": epoch or DEFAULT_BINDING_EPOCH,
            "phase": binding_phase,
            "candidateCount": candidate_count,
            "identityVerified": identity_verified,
        },
        "execution": {"phase": execution_phase, "reasonCode": execution_reason},
        "capabilities": list(capabilities),
        "capabilityState": {
            "monitor": {
                "monitorEnabled": monitor_enabled,
                "autoCloseEnabled": True,
                "closeSnapshotComplete": snapshot_complete,
                "closeSnapshotObservedAt": (
                    None
                    if snapshot_at is None
                    else snapshot_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                ),
            }
        },
        "decisionResults": decision_results or [],
    }
    if advertiser is not None:
        body["scope"]["advertiserId"] = advertiser
    if incident_id is not None:
        body["incidentId"] = incident_id
    if extra:
        body.update(extra)
    return body


def post_report(
    client: TestClient,
    key: str | None,
    body: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
    raw: bytes | None = None,
):
    headers: dict[str, str] = {}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    if request_id:
        headers["x-request-id"] = request_id
    if raw is not None:
        headers["Content-Type"] = "application/json"
        return client.post("/v2/plugin/status/report", headers=headers, content=raw)
    return client.post("/v2/plugin/status/report", headers=headers, json=body)


def _instance_row(instance_id: str, session: Session):
    from tts_erp_v2.db.models.plugin_status import StatusInstance

    return session.execute(
        select(StatusInstance).where(StatusInstance.id == uuid.UUID(instance_id))
    ).scalar_one()


def _bloat(body: dict[str, Any]) -> bytes:
    """Serialise a valid body and grow it past the 32 KiB protocol cap."""
    fat = copy.deepcopy(body)
    fat["pluginVersion"] = "x" * 64
    return json.dumps(fat).encode() + b" " * (32 * 1024 + 1)


# ── §9.4 instance provisioning ────────────────────────────────────────


class TestInstanceProvisioning:
    def test_registration_mints_a_dedicated_status_key_shown_once(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)

        assert payload["schemaVersion"] == 1
        assert payload["pluginId"] == "ads-monitor"
        assert payload["scope"]["sellerId"] == SELLER
        key = payload["statusApiKey"]
        assert key["value"].startswith("ttserp_st_")
        assert key["role"] == "readwrite"

        row = _instance_row(payload["instanceId"], session)
        assert row.enabled is True
        assert row.shop_pk is not None
        api_row = session.get(ApiKey, row.status_api_key_id)
        assert api_row.role == "readwrite"
        assert api_row.status == "active"
        assert key["value"] not in (api_row.key_hash or "")

    def test_detail_never_echoes_the_plaintext_key(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        secret = payload["statusApiKey"]["value"]

        detail = status_client.get(
            f"/v2/plugin/admin/instances/{payload['instanceId']}",
            headers={"Authorization": f"Bearer {admin_key}"},
        )
        assert detail.status_code == 200
        assert secret not in detail.text
        assert "keyPrefix" in detail.text

    def test_unmapped_shop_is_refused_and_registers_nothing(
        self, status_client, admin_key
    ):
        resp = status_client.post(
            "/v2/plugin/admin/instances",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={
                "pluginId": "ads-monitor",
                "scope": {"kind": "shop", "sellerId": "8800000000000000999"},
                "capabilities": ["binding.recover.v1"],
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "SHOP_NOT_MAPPED"

        listing = status_client.get(
            "/v2/plugin/admin/instances",
            headers={"Authorization": f"Bearer {admin_key}"},
        )
        assert listing.json()["total"] == 0

    def test_installation_scope_cannot_register_page_capabilities(
        self, status_client, admin_key
    ):
        resp = status_client.post(
            "/v2/plugin/admin/instances",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={
                "pluginId": "ads-data-sync",
                "scope": {"kind": "installation"},
                "capabilities": ["monitor.resume.v1"],
            },
        )
        assert resp.status_code == 422

    def test_admin_routes_require_admin(self, status_client_auth, rw_key, admin_key):
        assert (
            status_client_auth.get(
                "/v2/plugin/admin/instances",
                headers={"Authorization": f"Bearer {rw_key}"},
            ).status_code
            == 403
        )
        assert (
            status_client_auth.get(
                "/v2/plugin/admin/instances",
                headers={"Authorization": f"Bearer {admin_key}"},
            ).status_code
            == 200
        )

    def test_list_filters_by_shop_plugin_and_connectivity(
        self, status_client, admin_key
    ):
        a = register(status_client, admin_key)
        b = register(status_client, admin_key, advertiser="TEST_ADV_B")

        listed = status_client.get(
            "/v2/plugin/admin/instances",
            headers={"Authorization": f"Bearer {admin_key}"},
            params={"pluginId": "ads-monitor", "limit": 1},
        )
        assert listed.status_code == 200
        body = listed.json()
        assert body["total"] == 2
        assert len(body["items"]) == 1

        filtered = status_client.get(
            "/v2/plugin/admin/instances",
            headers={"Authorization": f"Bearer {admin_key}"},
            params={"pluginId": "order-data-sync"},
        )
        assert filtered.json()["total"] == 0

        unknown = status_client.get(
            "/v2/plugin/admin/instances",
            headers={"Authorization": f"Bearer {admin_key}"},
            params={"connectivity": "unknown"},
        )
        assert {row["instanceId"] for row in unknown.json()["items"]} == {
            a["instanceId"],
            b["instanceId"],
        }

    def test_malformed_registration_body_is_rejected(self, status_client, admin_key):
        resp = status_client.post(
            "/v2/plugin/admin/instances",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={
                "pluginId": "ads-monitor",
                "scope": {"kind": "shop", "sellerId": SELLER, "extra": 1},
                "capabilities": [],
            },
        )
        assert resp.status_code == 422


# ── §9.1/§9.2 purpose-bound authorisation ─────────────────────────────


class TestReportAuthorisation:
    @pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
    def test_anonymous_caller_is_refused_in_every_auth_mode(
        self, status_client_auth, monkeypatch, mode
    ):
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", mode)
        from tts_erp_v2.middleware.auth import clear_cache

        clear_cache()
        resp = post_report(status_client_auth, None, {})
        # Fail-closed in every mode. Which layer refuses differs on purpose:
        # under ``enforce`` the middleware answers before the route runs, and
        # under ``off``/``shadow`` only this route's own credential check can.
        assert resp.status_code == 401, resp.text
        body = resp.json()
        if "error" in body:
            assert body["error"]["code"] == "INVALID_CREDENTIAL"

    def test_unknown_bearer_is_401(self, status_client):
        resp = post_report(status_client, "ttserp_st_nope", {})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "INVALID_CREDENTIAL"

    def test_a_plain_admin_key_cannot_obtain_recovery_permission(
        self, status_client, admin_key
    ):
        resp = post_report(status_client, admin_key, {})
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "PURPOSE_DENIED"

    def test_revoked_instance_key_is_refused(self, status_client, admin_key, db_engine):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        from tts_erp_v2.access import hash_key

        with Session(db_engine) as sess:
            sess.execute(
                update(ApiKey)
                .where(ApiKey.key_hash == hash_key(key))
                .values(status="revoked")
            )
            sess.commit()
        resp = post_report(status_client, key, {})
        assert resp.status_code == 401

    def test_disabled_instance_is_refused(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        status_client.post(
            f"/v2/plugin/admin/instances/{payload['instanceId']}/disable",
            headers={"Authorization": f"Bearer {admin_key}"},
        )
        resp = post_report(status_client, payload["statusApiKey"]["value"], {})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "INSTANCE_DENIED"

    def test_instance_key_cannot_act_for_another_instance(
        self, status_client, admin_key
    ):
        a = register(status_client, admin_key)
        b = register(status_client, admin_key, advertiser="TEST_ADV_B")
        body = _report_body(
            b["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
        )
        resp = post_report(status_client, a["statusApiKey"]["value"], body)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "INSTANCE_DENIED"

    def test_wrong_seller_is_scope_denied(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
            seller="8800000000000000777",
        )
        resp = post_report(status_client, payload["statusApiKey"]["value"], body)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "SCOPE_DENIED"

    def test_forged_plugin_id_is_scope_denied(self, status_client, admin_key):
        payload = register(
            status_client,
            admin_key,
            plugin_id="ads-data-sync",
            capabilities=("binding.recover.v1",),
        )
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
            capabilities=("binding.recover.v1",),
        )
        resp = post_report(status_client, payload["statusApiKey"]["value"], body)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "SCOPE_DENIED"


# ── §6 request framing ────────────────────────────────────────────────


class TestRequestFraming:
    def test_unknown_field_is_rejected(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
            extra={"surprise": True},
        )
        resp = post_report(status_client, payload["statusApiKey"]["value"], body)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "SCHEMA_INVALID"

    def test_malformed_json_is_reported_separately(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        resp = post_report(
            status_client, payload["statusApiKey"]["value"], raw=b"{not json"
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "MALFORMED_JSON"

    def test_oversized_body_is_refused_without_truncation(
        self, status_client, admin_key
    ):
        payload = register(status_client, admin_key)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
        )
        resp = post_report(
            status_client,
            payload["statusApiKey"]["value"],
            raw=_bloat(body),
        )
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "BODY_TOO_LARGE"

    def test_unsupported_schema_version_is_refused(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
        )
        body["schemaVersion"] = 2
        resp = post_report(status_client, payload["statusApiKey"]["value"], body)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "UNSUPPORTED_VERSION"

    def test_success_is_no_store_and_echoes_the_request_id(
        self, status_client, admin_key
    ):
        payload = register(status_client, admin_key)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
        )
        request_id = str(uuid.uuid4())
        resp = post_report(
            status_client,
            payload["statusApiKey"]["value"],
            body,
            request_id=request_id,
        )
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-store"
        assert resp.json()["requestId"] == request_id
        assert resp.json()["receipt"]["eventId"] == body["eventId"]


# ── §8 idempotency and ordering ───────────────────────────────────────


class TestReportIdempotency:
    def _first(self, client, key, instance_id, now):
        incident = str(uuid.uuid4())
        body = _report_body(instance_id, seq=1, observed_at=now, incident_id=incident)
        return post_report(client, key, body), body, incident

    def test_first_invalidation_returns_recover_binding(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        resp, _body, incident = self._first(
            status_client,
            payload["statusApiKey"]["value"],
            payload["instanceId"],
            datetime.now(UTC),
        )
        assert resp.status_code == 200
        out = resp.json()
        assert out["receipt"]["projectionUpdated"] is True
        assert out["reportAfterSeconds"] == 60
        decision = out["decision"]
        assert decision["action"] == "recover_binding"
        assert decision["revision"] == "1"
        assert decision["incidentId"] == incident
        assert decision["parameters"] == {"attempt": 1, "captureTimeoutSeconds": 90}

    def test_identical_replay_returns_the_frozen_decision_with_fresh_transport(
        self, status_client, admin_key
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        first, body, _incident = self._first(
            status_client, key, payload["instanceId"], datetime.now(UTC)
        )
        second = post_report(status_client, key, body, request_id=str(uuid.uuid4()))
        assert second.status_code == 200
        out = second.json()
        first_out = first.json()
        assert out["decision"]["decisionId"] == first_out["decision"]["decisionId"]
        assert out["decision"]["revision"] == first_out["decision"]["revision"]
        assert out["decision"]["expiresAt"] == first_out["decision"]["expiresAt"]
        assert out["receipt"]["receivedAt"] == first_out["receipt"]["receivedAt"]
        assert out["requestId"] != first_out["requestId"]

    def test_same_event_id_with_different_content_is_409(
        self, status_client, admin_key
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        _, body, _incident = self._first(
            status_client, key, payload["instanceId"], datetime.now(UTC)
        )
        mutated = copy.deepcopy(body)
        mutated["reasonCode"] = "ORIGIN_LEFT"
        resp = post_report(status_client, key, mutated)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "EVENT_CONFLICT"

    def test_same_seq_with_a_different_event_id_is_409(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        self._first(status_client, key, instance_id, now)
        other = _report_body(
            instance_id,
            seq=1,
            observed_at=now,
            incident_id=str(uuid.uuid4()),
            reason_code="ORIGIN_LEFT",
        )
        resp = post_report(status_client, key, other)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "SEQUENCE_CONFLICT"

    def test_late_low_seq_report_is_archived_without_rolling_back(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        incident = str(uuid.uuid4())
        old = _report_body(instance_id, seq=5, observed_at=now, incident_id=incident)
        assert post_report(status_client, key, old).status_code == 200

        newer = _report_body(
            instance_id,
            seq=9,
            observed_at=now,
            event_type="binding_ready",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_reason="INITIALIZING",
            snapshot_complete=True,
            snapshot_at=now,
            incident_id=incident,
        )
        assert post_report(status_client, key, newer).status_code == 200

        late = _report_body(instance_id, seq=6, observed_at=now, incident_id=incident)
        resp = post_report(status_client, key, late)
        assert resp.status_code == 200
        assert resp.json()["receipt"]["projectionUpdated"] is False

        row = _instance_row(instance_id, session)
        assert row.latest_report_seq == 9
        from tts_erp_v2.db.models.plugin_status import StatusReport

        archived = session.execute(
            select(StatusReport).where(StatusReport.report_seq == 6)
        ).scalar_one()
        assert archived.response_decision is None

    def test_recovery_budget_of_three_then_require_user(
        self, status_client, admin_key, session
    ):
        """Three issued recoveries, then a person — with a controlled clock.

        §15 S09: the budget and the one-minute interval can only be observed with
        a controlled clock, because the interval is measured between *issuances*.
        Issued through the service so ``now`` is explicit; every other rule here
        (scope, dedupe, projection, policy) is the same code the HTTP route uses.
        """
        from tts_erp_v2.plugin.status.contracts import (
            report_payload_hash,
            status_report_from_wire,
        )

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        incident = str(uuid.uuid4())
        actions = []
        for step in range(5):
            now = datetime.now(UTC) + timedelta(seconds=70 * step)
            body = _report_body(
                str(instance_id),
                seq=step + 1,
                observed_at=now - timedelta(seconds=1),
                incident_id=incident,
            )
            report = status_report_from_wire(body)
            response = ingest_report(
                session,
                instance_id=instance_id,
                report=report,
                payload_hash=report_payload_hash(report),
                request_id=str(uuid.uuid4()),
                now=now,
                authenticated=authenticate_report(
                    session, bearer_token=payload["statusApiKey"]["value"]
                ),
            )
            session.commit()
            session.expire_all()
            actions.append(
                response.decision.action.value if response.decision else None
            )
        # 60 s minimum interval, 3 attempts, then a person — never a fourth.
        assert actions == [
            "recover_binding",
            "recover_binding",
            "recover_binding",
            "require_user",
            "require_user",
        ]


# ── §6.2/§11 readiness and healthy work ───────────────────────────────


class TestReadinessAndHealthyWork:
    def test_ready_with_a_fresh_snapshot_is_resumed(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        now = datetime.now(UTC)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=now,
            incident_id=str(uuid.uuid4()),
            event_type="binding_ready",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_reason="INITIALIZING",
            snapshot_complete=True,
            snapshot_at=now,
        )
        resp = post_report(status_client, payload["statusApiKey"]["value"], body)
        assert resp.status_code == 200
        assert resp.json()["decision"]["action"] == "resume_monitoring"

    def test_ready_without_a_fresh_snapshot_stays_held(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
            event_type="binding_ready",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_reason="INITIALIZING",
            snapshot_complete=False,
        )
        resp = post_report(status_client, payload["statusApiKey"]["value"], body)
        assert resp.json()["decision"]["action"] == "hold"

    def test_healthy_running_heartbeat_returns_no_command(
        self, status_client, admin_key
    ):
        payload = register(status_client, admin_key)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            event_type="heartbeat",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_phase="running",
            execution_reason="NONE",
        )
        resp = post_report(status_client, payload["statusApiKey"]["value"], body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["decision"] is None

    def test_disabled_work_is_held_and_never_nagged(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            event_type="heartbeat",
            reason_code="USER_DISABLED",
            binding_phase="ready",
            identity_verified=True,
            execution_phase="disabled",
            execution_reason="USER_DISABLED",
            monitor_enabled=False,
        )
        resp = post_report(status_client, payload["statusApiKey"]["value"], body)
        assert resp.json()["decision"]["action"] == "hold"

    def test_login_problem_demands_a_person_immediately(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
            reason_code="LOGIN_REQUIRED",
            binding_phase="login_required",
            candidate_count=0,
        )
        resp = post_report(status_client, payload["statusApiKey"]["value"], body)
        assert resp.json()["decision"]["action"] == "require_user"

    def test_initial_advertiser_is_locked_once_then_frozen(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key, advertiser=None)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        incident = str(uuid.uuid4())
        first = _report_body(
            instance_id,
            seq=1,
            observed_at=now,
            incident_id=incident,
            event_type="binding_ready",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_reason="INITIALIZING",
            snapshot_complete=True,
            snapshot_at=now,
        )
        assert post_report(status_client, key, first).status_code == 200
        row = _instance_row(instance_id, session)
        assert row.scope["advertiserId"] == ADVERTISER

        swap = _report_body(
            instance_id,
            seq=2,
            observed_at=now,
            incident_id=incident,
            event_type="heartbeat",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_phase="running",
            execution_reason="NONE",
            advertiser="TEST_ADVERTISER_B",
        )
        resp = post_report(status_client, key, swap)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "SCOPE_DENIED"


# ── §7 decision results and episode resolution ────────────────────────


class TestDecisionResults:
    def test_applied_resume_with_running_resolves_the_episode(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        incident = str(uuid.uuid4())
        ready = _report_body(
            instance_id,
            seq=1,
            observed_at=now,
            incident_id=incident,
            event_type="binding_ready",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_reason="INITIALIZING",
            snapshot_complete=True,
            snapshot_at=now,
        )
        issued = post_report(status_client, key, ready).json()["decision"]

        result = _report_body(
            instance_id,
            seq=2,
            observed_at=now + timedelta(seconds=5),
            incident_id=incident,
            event_type="decision_result",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_phase="running",
            execution_reason="NONE",
            decision_results=[
                {
                    "decisionId": issued["decisionId"],
                    "revision": issued["revision"],
                    "outcome": "applied",
                    "reasonCode": "NONE",
                    "completedAt": (now + timedelta(seconds=4))
                    .astimezone(UTC)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            ],
        )
        resp = post_report(status_client, key, result)
        assert resp.status_code == 200, resp.text
        assert resp.json()["decision"] is None
        assert _instance_row(instance_id, session).current_incident_id is None
        # A fresh session proves the audit fact committed, not just the projection.
        with Session(session.get_bind()) as facts:
            recorded = facts.get(StatusDecision, uuid.UUID(issued["decisionId"]))
            assert recorded is not None
            assert recorded.outcome == "applied"
            assert recorded.outcome_reason_code == "NONE"
            assert recorded.delivery_state == "applied"
            assert recorded.completed_at == datetime.fromisoformat(
                result["decisionResults"][0]["completedAt"]
            )

    def test_unknown_decision_result_is_archived_not_rejected(
        self, status_client, admin_key
    ):
        payload = register(status_client, admin_key)
        now = datetime.now(UTC)
        # A ``decision_result`` is a lifecycle event, so §6.2 requires an
        # ``incidentId``; the *decision* it names is the unknown part.
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=now,
            incident_id=str(uuid.uuid4()),
            event_type="decision_result",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_phase="running",
            execution_reason="NONE",
            decision_results=[
                {
                    "decisionId": str(uuid.uuid4()),
                    "revision": "9",
                    "outcome": "unknown",
                    "reasonCode": "INTERRUPTED",
                    "completedAt": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            ],
        )
        resp = post_report(status_client, payload["statusApiKey"]["value"], body)
        assert resp.status_code == 200, resp.text

    def test_a_late_result_never_reopens_a_resolved_episode(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        incident = str(uuid.uuid4())
        # 1. The page is gone -> the server issues a recovery.
        first = _report_body(instance_id, seq=1, observed_at=now, incident_id=incident)
        assert post_report(status_client, key, first).status_code == 200
        # 2. The binding is ready again with a fresh snapshot -> release.
        ready = _report_body(
            instance_id,
            seq=2,
            observed_at=now + timedelta(seconds=5),
            incident_id=incident,
            event_type="binding_ready",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_reason="INITIALIZING",
            snapshot_complete=True,
            snapshot_at=now + timedelta(seconds=5),
        )
        resume = post_report(status_client, key, ready).json()["decision"]
        assert resume["action"] == "resume_monitoring"
        # 3. The plugin reports it applied and is actually running: resolved.
        applied = _report_body(
            instance_id,
            seq=3,
            observed_at=now + timedelta(seconds=10),
            incident_id=incident,
            event_type="decision_result",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_phase="running",
            execution_reason="NONE",
            decision_results=[
                {
                    "decisionId": resume["decisionId"],
                    "revision": resume["revision"],
                    "outcome": "applied",
                    "reasonCode": "NONE",
                    "completedAt": (now + timedelta(seconds=9))
                    .astimezone(UTC)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            ],
        )
        resp = post_report(status_client, key, applied)
        assert resp.status_code == 200, resp.text
        assert _instance_row(instance_id, session).current_incident_id is None
        # 4. A late failure for the *old* recovery cannot reopen it.
        late = _report_body(
            instance_id,
            seq=4,
            observed_at=now + timedelta(seconds=11),
            incident_id=incident,
            event_type="decision_result",
            reason_code="NONE",
            decision_results=[
                {
                    "decisionId": resume["decisionId"],
                    "revision": resume["revision"],
                    "outcome": "failed",
                    "reasonCode": "RECOVERY_FAILED",
                    "completedAt": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            ],
        )
        resp = post_report(status_client, key, late)
        assert resp.status_code == 200, resp.text
        with Session(session.get_bind()) as facts:
            recorded = facts.get(StatusDecision, uuid.UUID(resume["decisionId"]))
            assert recorded is not None
            assert recorded.outcome == "applied"
            assert recorded.outcome_reason_code == "NONE"
            assert recorded.delivery_state == "applied"
            assert recorded.completed_at == datetime.fromisoformat(
                applied["decisionResults"][0]["completedAt"]
            )
        assert _instance_row(instance_id, session).current_incident_id is None


# ── §9.5 manual decisions ─────────────────────────────────────────────


class TestManualDecisions:
    def _open_episode(self, client, key, instance_id, now=None):
        now = now or datetime.now(UTC)
        incident = str(uuid.uuid4())
        body = _report_body(instance_id, seq=1, observed_at=now, incident_id=incident)
        issued = client.post(
            "/v2/plugin/status/report",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
        ).json()["decision"]
        return incident, issued

    def _command(self, issued, incident, action="hold", revision=None, request_id=None):
        return {
            "requestId": request_id or str(uuid.uuid4()),
            "expectedRevision": revision or issued["revision"],
            "action": action,
            "incidentId": incident,
            "expectedBindingEpoch": issued["expectedBindingEpoch"],
        }

    def _decide(self, client, admin_key, instance_id, command):
        return client.post(
            f"/v2/plugin/admin/instances/{instance_id}/decisions",
            headers={"Authorization": f"Bearer {admin_key}"},
            json=command,
        )

    def test_hold_latches_and_survives_healthy_heartbeats(
        self, status_client, admin_key
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        incident, issued = self._open_episode(status_client, key, payload["instanceId"])
        resp = self._decide(
            status_client,
            admin_key,
            payload["instanceId"],
            self._command(issued, incident),
        )
        assert resp.status_code == 200
        held = resp.json()["decision"]
        assert held["action"] == "hold"
        assert held["revision"] == str(int(issued["revision"]) + 1)

        heartbeat = _report_body(
            payload["instanceId"],
            seq=2,
            observed_at=datetime.now(UTC),
            incident_id=incident,
            event_type="heartbeat",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_phase="running",
            execution_reason="NONE",
        )
        out = post_report(status_client, key, heartbeat).json()
        assert out["decision"]["action"] == "hold"

    def test_manual_recover_clears_the_latch(self, status_client, admin_key, session):
        """Releasing a latch needs a recovery slot the budget still allows.

        The automatic recovery already spent one attempt at t0, so an immediate
        operator recover is inside the 60 s interval and is refused — that
        refusal is itself the proof the budget is shared, and it must leave the
        latch intact rather than half-applying.
        """
        from tts_erp_v2.plugin.status import service as service_module
        from tts_erp_v2.plugin.status.contracts import AdminDecisionRequest

        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        incident, issued = self._open_episode(status_client, key, instance_id, now)
        held = self._decide(
            status_client,
            admin_key,
            instance_id,
            self._command(issued, incident),
        ).json()["decision"]

        early = self._decide(
            status_client,
            admin_key,
            instance_id,
            self._command(
                held, incident, action="recover_binding", revision=held["revision"]
            ),
        )
        assert early.status_code == 409, early.text
        assert early.json()["error"]["code"] == "RECOVERY_INTERVAL_NOT_ELAPSED"
        assert _instance_row(instance_id, session).manual_latch_action == "hold"

        command = AdminDecisionRequest.model_validate(
            {
                "requestId": str(uuid.uuid4()),
                "expectedRevision": held["revision"],
                "action": "recover_binding",
                "incidentId": incident,
                "expectedBindingEpoch": held["expectedBindingEpoch"],
            }
        )
        service_module.manual_decision(
            session,
            instance_id=uuid.UUID(instance_id),
            command=command,
            now=now + timedelta(seconds=70),
        )
        session.commit()
        session.expire_all()
        row = _instance_row(instance_id, session)
        assert row.manual_latch_action is None
        assert str(row.current_incident_id) == incident

    def test_replaying_a_request_id_does_not_burn_a_revision(
        self, status_client, admin_key
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        incident, issued = self._open_episode(status_client, key, payload["instanceId"])
        command = self._command(issued, incident, request_id=str(uuid.uuid4()))

        first = self._decide(status_client, admin_key, payload["instanceId"], command)
        second = self._decide(status_client, admin_key, payload["instanceId"], command)
        assert first.status_code == second.status_code == 200
        assert (
            first.json()["decision"]["revision"]
            == second.json()["decision"]["revision"]
        )
        assert (
            first.json()["decision"]["decisionId"]
            == second.json()["decision"]["decisionId"]
        )

    def test_stale_expected_revision_is_refused(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        incident, issued = self._open_episode(status_client, key, payload["instanceId"])
        resp = self._decide(
            status_client,
            admin_key,
            payload["instanceId"],
            self._command(issued, incident, revision="99"),
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "REVISION_CONFLICT"

    def test_mismatched_incident_is_refused(self, status_client, admin_key):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        incident, issued = self._open_episode(status_client, key, payload["instanceId"])
        command = self._command(issued, incident)
        command["incidentId"] = str(uuid.uuid4())
        resp = self._decide(status_client, admin_key, payload["instanceId"], command)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "CONTEXT_MISMATCH"

    def test_an_operator_can_never_grant_resume_monitoring(
        self, status_client, admin_key
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        incident, issued = self._open_episode(status_client, key, payload["instanceId"])
        resp = self._decide(
            status_client,
            admin_key,
            payload["instanceId"],
            self._command(issued, incident, action="resume_monitoring"),
        )
        assert resp.status_code == 422

    def test_manual_decision_without_an_open_episode_is_refused(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            event_type="heartbeat",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_phase="running",
            execution_reason="NONE",
        )
        assert (
            post_report(status_client, payload["statusApiKey"]["value"], body).json()[
                "decision"
            ]
            is None
        )

        resp = self._decide(
            status_client,
            admin_key,
            payload["instanceId"],
            {
                "requestId": str(uuid.uuid4()),
                # ``expectedRevision`` is a *positive* decimal on the wire, so
                # the "nothing issued yet" revision 0 cannot be addressed; the
                # command is refused either way and creates nothing, which is
                # the invariant §9.5 actually protects.
                "expectedRevision": "1",
                "action": "recover_binding",
                "incidentId": str(uuid.uuid4()),
                "expectedBindingEpoch": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 409, resp.text
        assert (
            _instance_row(payload["instanceId"], session).current_decision_revision == 0
        )


# ── §10 atomic commit ─────────────────────────────────────────────────


class TestTransactionBoundary:
    def test_a_failure_before_commit_rolls_everything_back(
        self, status_client, admin_key, monkeypatch, session
    ):
        payload = register(status_client, admin_key)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
        )

        from tts_erp_v2.plugin.status import service as service_module

        def _boom(*_args, **_kwargs):
            raise RuntimeError("simulated store failure")

        monkeypatch.setattr(service_module, "_freeze_response", _boom)
        resp = post_report(status_client, payload["statusApiKey"]["value"], body)
        assert resp.status_code == 500
        assert resp.json()["error"]["code"] == "INTERNAL_ERROR"

        from tts_erp_v2.db.models.plugin_status import StatusReport

        assert session.execute(select(StatusReport)).scalars().all() == []


# ── §11 outbox and lifecycle sweep ────────────────────────────────────


class TestOutboxAndSweep:
    def test_require_user_enqueues_exactly_one_notification(
        self, status_client, admin_key, db_engine
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        incident = str(uuid.uuid4())
        for seq in (1, 2):
            body = _report_body(
                payload["instanceId"],
                seq=seq,
                observed_at=now,
                incident_id=incident,
                reason_code="LOGIN_REQUIRED",
                binding_phase="login_required",
                candidate_count=0,
            )
            assert post_report(status_client, key, body).status_code == 200

        with Session(db_engine) as sess:
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            rows = sess.execute(select(StatusNotificationOutbox)).scalars().all()
            assert len(rows) == 1
            assert rows[0].notification_kind == "episode_requires_user"

    def test_sweep_marks_stale_once_and_never_says_login(
        self, status_client, admin_key, db_engine
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        body = _report_body(
            payload["instanceId"], seq=1, observed_at=now, incident_id=str(uuid.uuid4())
        )
        assert post_report(status_client, key, body).status_code == 200

        sent: list[Any] = []

        def _sender(message: Any) -> str:
            sent.append(message)
            return "delivered"

        later = now + timedelta(seconds=400)
        with Session(db_engine) as sess:
            first = run_scheduled(sess, now=later, sender=_sender)
            second = run_scheduled(
                sess, now=later + timedelta(seconds=60), sender=_sender
            )
        assert first["staleInstances"] == 1
        assert second["staleInstances"] == 0
        kinds = [message.kind for message in sent]
        assert kinds.count("instance_stale") == 1
        assert all("login" not in json_text(message).lower() for message in sent)

        with Session(db_engine) as sess:
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            rows = sess.execute(select(StatusNotificationOutbox)).scalars().all()
            assert all(row.delivered_at is not None for row in rows)

    def test_an_never_seen_instance_stays_unknown_and_silent(
        self, status_client, admin_key, db_engine
    ):
        register(status_client, admin_key)
        sent: list[Any] = []

        with Session(db_engine) as sess:
            result = run_scheduled(
                sess, sender=lambda message: sent.append(message) or "delivered"
            )

        assert result["staleInstances"] == 0
        assert sent == []

    def test_failed_delivery_is_retried_with_bounded_backoff(
        self, status_client, admin_key, db_engine
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        body = _report_body(
            payload["instanceId"], seq=1, observed_at=now, incident_id=str(uuid.uuid4())
        )
        assert post_report(status_client, key, body).status_code == 200

        sweep_now = now + timedelta(seconds=400)
        with Session(db_engine) as sess:
            run_scheduled(sess, now=sweep_now, sender=_retry)
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            row = sess.execute(select(StatusNotificationOutbox)).scalars().one()
            assert row.state == "pending"
            assert row.attempt_count == 1
            # Full-jitter backoff: bounded by the 300 s cap, never immediate.
            retry_at = row.next_attempt_at
            delay = (retry_at - sweep_now).total_seconds()
            assert 0 < delay <= 300

        # Full jitter can hit the 1-second floor: +1 second is already due.
        # Check the persisted boundary, not an invented strictly-longer delay.
        with Session(db_engine) as sess:
            result = run_scheduled(
                sess,
                now=retry_at - timedelta(microseconds=1),
                sender=_retry,
            )
            assert result["attempted"] == 0
        with Session(db_engine) as sess:
            due = run_scheduled(sess, now=retry_at, sender=_retry)
            assert due["attempted"] == 1
            assert due["retrying"] == 1

    def test_configuration_failures_wait_for_a_person(
        self, status_client, admin_key, db_engine
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        body = _report_body(
            payload["instanceId"], seq=1, observed_at=now, incident_id=str(uuid.uuid4())
        )
        assert post_report(status_client, key, body).status_code == 200

        with Session(db_engine) as sess:
            run_scheduled(
                sess,
                now=now + timedelta(seconds=400),
                sender=lambda message: "pending_manual",
            )
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            row = sess.execute(select(StatusNotificationOutbox)).scalars().one()
            assert row.state == "pending_manual"

    def test_outbox_payloads_never_carry_a_credential_or_shop_secret(
        self, status_client, admin_key, db_engine
    ):
        payload = register(status_client, admin_key)
        secret = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        body = _report_body(
            payload["instanceId"], seq=1, observed_at=now, incident_id=str(uuid.uuid4())
        )
        assert post_report(status_client, secret, body).status_code == 200
        with Session(db_engine) as sess:
            run_scheduled(
                sess,
                now=now + timedelta(seconds=400),
                sender=lambda message: "delivered",
            )
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            row = sess.execute(select(StatusNotificationOutbox)).scalars().one()
            dumped = json_text(row.payload)
            assert secret not in dumped
            assert "key_hash" not in dumped
            assert SELLER not in dumped


def _retry(message: Any) -> str:
    return "retry"


# ── Service-level surface ─────────────────────────────────────────────


class TestServiceSurface:
    def test_ingest_report_is_importable_and_typed(self):
        assert callable(ingest_report)
        assert issubclass(StatusServiceError, Exception)

    def test_registration_request_is_validated_before_any_write(self):
        with pytest.raises(ValidationError):
            InstanceRegistrationRequest.model_validate(
                {
                    "pluginId": "ads-monitor",
                    "scope": {"kind": "installation"},
                    "capabilities": ["monitor.resume.v1"],
                }
            )
        assert status_report_from_wire is not None


# ── Review-finding regressions (owner-accepted P1/P2) ────────────────
#
# Each test names the defect it pins. All of them were RED against the
# implementation before the corresponding fix.


def _manual(
    client: TestClient,
    admin_key: str,
    instance_id: str,
    *,
    action: str,
    incident: str,
    revision: str | int,
    epoch: str,
    request_id: str | None = None,
):
    return client.post(
        f"/v2/plugin/admin/instances/{instance_id}/decisions",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={
            "requestId": request_id or str(uuid.uuid4()),
            "expectedRevision": str(revision),
            "action": action,
            "incidentId": incident,
            "expectedBindingEpoch": epoch,
        },
    )


def _resolved_episode(client, key, instance_id, incident, *, seq: int, now):
    """Drive one episode all the way to a confirmed recovery and back."""
    ready = _report_body(
        instance_id,
        seq=seq,
        observed_at=now,
        incident_id=incident,
        event_type="binding_ready",
        reason_code="NONE",
        binding_phase="ready",
        identity_verified=True,
        execution_reason="INITIALIZING",
        snapshot_complete=True,
        snapshot_at=now,
    )
    issued = post_report(client, key, ready)
    assert issued.status_code == 200, issued.text
    resume = issued.json()["decision"]
    assert resume["action"] == "resume_monitoring", resume
    applied = _report_body(
        instance_id,
        seq=seq + 1,
        observed_at=now + timedelta(seconds=5),
        incident_id=incident,
        event_type="decision_result",
        reason_code="NONE",
        binding_phase="ready",
        identity_verified=True,
        execution_phase="running",
        execution_reason="NONE",
        decision_results=[
            {
                "decisionId": resume["decisionId"],
                "revision": resume["revision"],
                "outcome": "applied",
                "reasonCode": "NONE",
                "completedAt": (now + timedelta(seconds=4))
                .astimezone(UTC)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ],
    )
    done = post_report(client, key, applied)
    assert done.status_code == 200, done.text
    return resume, done


class TestArchivedReportDoesNotMutateCurrentState:
    """P1 ordering: _advance_episode ran before the sequence guard."""

    def test_a_late_lower_seq_report_cannot_replace_the_episode_or_latch(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        incident_a = str(uuid.uuid4())

        first = _report_body(
            instance_id, seq=5, observed_at=now, incident_id=incident_a
        )
        assert post_report(status_client, key, first).status_code == 200
        issued = post_report(status_client, key, first).json()["decision"]

        held = _manual(
            status_client,
            admin_key,
            instance_id,
            action="hold",
            incident=incident_a,
            revision=issued["revision"],
            epoch=issued["expectedBindingEpoch"],
        )
        assert held.status_code == 200, held.text
        assert _instance_row(instance_id, session).manual_latch_action == "hold"

        # A *lower* sequence from a *different* incident arrives late.
        late = _report_body(
            instance_id,
            seq=3,
            observed_at=now,
            incident_id=str(uuid.uuid4()),
        )
        resp = post_report(status_client, key, late)
        assert resp.status_code == 200, resp.text
        assert resp.json()["receipt"]["projectionUpdated"] is False

        row = _instance_row(instance_id, session)
        assert str(row.current_incident_id) == incident_a
        assert row.manual_latch_action == "hold"
        assert row.latest_report_seq == 5

    def test_the_latched_episode_still_survives_the_late_report(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        incident_a = str(uuid.uuid4())
        first = _report_body(
            instance_id, seq=5, observed_at=now, incident_id=incident_a
        )
        post_report(status_client, key, first)
        issued = post_report(status_client, key, first).json()["decision"]
        _manual(
            status_client,
            admin_key,
            instance_id,
            action="hold",
            incident=incident_a,
            revision=issued["revision"],
            epoch=issued["expectedBindingEpoch"],
        )
        post_report(
            status_client,
            key,
            _report_body(
                instance_id,
                seq=3,
                observed_at=now,
                incident_id=str(uuid.uuid4()),
            ),
        )

        healthy = _report_body(
            instance_id,
            seq=6,
            observed_at=now + timedelta(seconds=5),
            incident_id=incident_a,
            event_type="heartbeat",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_phase="running",
            execution_reason="NONE",
        )
        out = post_report(status_client, key, healthy)
        assert out.status_code == 200, out.text
        assert out.json()["decision"]["action"] == "hold"


class TestEpisodeResolutionNeedsCurrentConfirmation:
    """P1 confirmation: any matched applied resume resolved any incident."""

    def test_a_late_applied_resume_cannot_resolve_a_newer_episode(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        incident_a = str(uuid.uuid4())

        resume_a, _ = _resolved_episode(
            status_client, key, instance_id, incident_a, seq=1, now=now
        )
        assert _instance_row(instance_id, session).current_incident_id is None

        incident_b = str(uuid.uuid4())
        broken = _report_body(
            instance_id,
            seq=4,
            observed_at=now + timedelta(seconds=20),
            incident_id=incident_b,
        )
        assert post_report(status_client, key, broken).status_code == 200

        # Replay episode A's *applied* resume while episode B is running.
        replay = _report_body(
            instance_id,
            seq=5,
            observed_at=now + timedelta(seconds=25),
            incident_id=incident_b,
            event_type="decision_result",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_phase="running",
            execution_reason="NONE",
            decision_results=[
                {
                    "decisionId": resume_a["decisionId"],
                    "revision": resume_a["revision"],
                    "outcome": "applied",
                    "reasonCode": "NONE",
                    "completedAt": (now + timedelta(seconds=4))
                    .astimezone(UTC)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            ],
        )
        resp = post_report(status_client, key, replay)
        assert resp.status_code == 200, resp.text
        assert (
            str(_instance_row(instance_id, session).current_incident_id) == incident_b
        )


class TestDecisionReuseRequiresApplicability:
    """P1 decision reuse: reuse branches ignored epoch and terminal outcome."""

    def test_a_new_binding_epoch_gets_a_new_revision_not_the_old_resume(
        self, status_client, admin_key
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        incident = str(uuid.uuid4())

        first = _report_body(
            instance_id,
            seq=1,
            observed_at=now,
            incident_id=incident,
            event_type="binding_ready",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_reason="INITIALIZING",
            snapshot_complete=True,
            snapshot_at=now,
            epoch=DEFAULT_BINDING_EPOCH,
        )
        first_out = post_report(status_client, key, first)
        assert first_out.json()["decision"]["revision"] == "1"

        # A *different* epoch on a fresh ready report: the old resume is void.
        second = _report_body(
            instance_id,
            seq=2,
            observed_at=now + timedelta(seconds=5),
            incident_id=incident,
            event_type="binding_ready",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_reason="INITIALIZING",
            snapshot_complete=True,
            snapshot_at=now + timedelta(seconds=5),
            epoch=str(uuid.uuid4()),
        )
        out = post_report(status_client, key, second)
        assert out.status_code == 200, out.text
        decision = out.json()["decision"]
        assert decision["revision"] == "2"
        assert decision["expectedBindingEpoch"] == second["binding"]["epoch"]

    def test_a_terminal_decision_is_never_handed_out_again(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        incident = str(uuid.uuid4())
        first = _report_body(instance_id, seq=1, observed_at=now, incident_id=incident)
        issued = post_report(status_client, key, first).json()["decision"]

        rejected = _report_body(
            instance_id,
            seq=2,
            observed_at=now + timedelta(seconds=1),
            incident_id=incident,
            event_type="decision_result",
            # The page is still invalid, so the report keeps the real reason
            # code: claiming NONE here would itself be a new "nothing is wrong"
            # fact and would make the policy hold.
            reason_code="TAB_CLOSED",
            decision_results=[
                {
                    "decisionId": issued["decisionId"],
                    "revision": issued["revision"],
                    "outcome": "rejected",
                    "reasonCode": "LOCAL_DISABLED",
                    "completedAt": (now + timedelta(seconds=1))
                    .astimezone(UTC)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            ],
        )
        assert post_report(status_client, key, rejected).status_code == 200

        # Inside the 60 s interval the policy says "keep what is outstanding",
        # but a rejected decision must not be re-sent for execution.
        again = _report_body(
            instance_id,
            seq=3,
            observed_at=now + timedelta(seconds=2),
            incident_id=incident,
        )
        out = post_report(status_client, key, again)
        assert out.status_code == 200, out.text
        assert out.json()["decision"] is None


class TestRegisteredCapabilitiesAreEnforced:
    """P1 capability authorization: registered grants were never consulted."""

    def _incident_report(self, instance_id, *, seq, now):
        return _report_body(
            instance_id,
            seq=seq,
            observed_at=now,
            incident_id=str(uuid.uuid4()),
        )

    def test_an_instance_without_recovery_grant_is_never_issued_recover(
        self, status_client, admin_key
    ):
        payload = register(status_client, admin_key, capabilities=())
        key = payload["statusApiKey"]["value"]
        resp = post_report(
            status_client,
            key,
            self._incident_report(payload["instanceId"], seq=1, now=datetime.now(UTC)),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["decision"]["action"] == "require_user"

    def test_an_instance_without_monitor_grant_is_never_issued_resume(
        self, status_client, admin_key
    ):
        payload = register(
            status_client, admin_key, capabilities=("binding.recover.v1",)
        )
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=now,
            incident_id=str(uuid.uuid4()),
            event_type="binding_ready",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_reason="INITIALIZING",
            snapshot_complete=True,
            snapshot_at=now,
        )
        resp = post_report(status_client, key, body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["decision"]["action"] != "resume_monitoring"

    def test_an_installation_report_without_a_binding_is_accepted(
        self, status_client, admin_key
    ):
        payload = status_client.post(
            "/v2/plugin/admin/instances",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={
                "pluginId": "ads-data-sync",
                "scope": {"kind": "installation"},
                "capabilities": [],
            },
        )
        assert payload.status_code == 200, payload.text
        instance_id = payload.json()["instanceId"]
        key = payload.json()["statusApiKey"]["value"]
        now = datetime.now(UTC)
        report = {
            "schemaVersion": 1,
            "pluginId": "ads-data-sync",
            "pluginVersion": "1.0.0",
            "instanceId": instance_id,
            "eventId": str(uuid.uuid4()),
            "reportSeq": "1",
            "observedAt": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "eventType": "heartbeat",
            "reasonCode": "NONE",
            "scope": {"kind": "installation"},
            "execution": {"phase": "running", "reasonCode": "NONE"},
            "capabilities": [],
            "capabilityState": {},
            "decisionResults": [],
        }
        resp = post_report(status_client, key, report)
        assert resp.status_code == 200, resp.text
        assert resp.json()["decision"] is None


class TestFutureObservationIsRejected:
    """P1 future observations: §6.2 rejects observedAt > now + 5 minutes."""

    def test_a_observation_far_in_the_future_is_refused(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=now + timedelta(hours=1),
            incident_id=str(uuid.uuid4()),
        )
        resp = post_report(status_client, key, body)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "SCHEMA_INVALID"

        from tts_erp_v2.db.models.plugin_status import StatusReport

        assert session.execute(select(StatusReport)).scalars().all() == []


class TestManualRecoveryRespectsTheEpisodeBudget:
    """P1 operator budget: manual recover bypassed interval/cap/timeout."""

    def _episode(self, status_client, key, instance_id, now):
        body = _report_body(
            instance_id,
            seq=1,
            observed_at=now,
            incident_id=str(uuid.uuid4()),
        )
        issued = post_report(status_client, key, body).json()["decision"]
        return issued

    def test_a_fourth_recovery_in_one_episode_is_a_bounded_refusal(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        issued = self._episode(status_client, key, instance_id, now)
        incident = issued["incidentId"]
        epoch = issued["expectedBindingEpoch"]

        # Issued #1 is the automatic recovery from the report above.
        from tts_erp_v2.plugin.status import service as service_module
        from tts_erp_v2.plugin.status.contracts import (
            AdminDecisionRequest,
        )

        revision = int(issued["revision"])
        outcomes = []
        for step in range(2, 5):
            command = AdminDecisionRequest.model_validate(
                {
                    "requestId": str(uuid.uuid4()),
                    "expectedRevision": str(revision),
                    "action": "recover_binding",
                    "incidentId": incident,
                    "expectedBindingEpoch": epoch,
                }
            )
            at = now + timedelta(seconds=70 * (step - 1))
            try:
                service_module.manual_decision(
                    session,
                    instance_id=uuid.UUID(instance_id),
                    command=command,
                    now=at,
                )
                session.commit()
                session.expire_all()
                outcomes.append("issued")
            except service_module.StatusServiceError as exc:
                session.rollback()
                outcomes.append(exc.code)
            revision += 1
        assert outcomes[0] == "issued"
        assert outcomes[-1] in {
            "RECOVERY_BUDGET_EXHAUSTED",
            "RECOVERY_INTERVAL_NOT_ELAPSED",
        }

    def test_a_second_recovery_inside_the_interval_is_refused(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        issued = self._episode(status_client, key, instance_id, now)
        resp = _manual(
            status_client,
            admin_key,
            instance_id,
            action="recover_binding",
            incident=issued["incidentId"],
            revision=issued["revision"],
            epoch=issued["expectedBindingEpoch"],
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "RECOVERY_INTERVAL_NOT_ELAPSED"

    def test_releasing_a_latch_does_not_reset_the_budget(
        self, status_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)
        incident = str(uuid.uuid4())
        body = _report_body(instance_id, seq=1, observed_at=now, incident_id=incident)
        issued = post_report(status_client, key, body).json()["decision"]

        held = _manual(
            status_client,
            admin_key,
            instance_id,
            action="hold",
            incident=incident,
            revision=issued["revision"],
            epoch=issued["expectedBindingEpoch"],
        )
        assert held.status_code == 200, held.text
        held_decision = held.json()["decision"]

        # Two more recoveries, well outside the interval.
        from tts_erp_v2.plugin.status import service as service_module
        from tts_erp_v2.plugin.status.contracts import AdminDecisionRequest

        revision = int(held_decision["revision"])
        for step in range(2, 5):
            command = AdminDecisionRequest.model_validate(
                {
                    "requestId": str(uuid.uuid4()),
                    "expectedRevision": str(revision),
                    "action": "recover_binding",
                    "incidentId": incident,
                    "expectedBindingEpoch": issued["expectedBindingEpoch"],
                }
            )
            try:
                service_module.manual_decision(
                    session,
                    instance_id=uuid.UUID(instance_id),
                    command=command,
                    now=now + timedelta(seconds=70 * (step - 1)),
                )
                session.commit()
                session.expire_all()
            except service_module.StatusServiceError as exc:
                session.rollback()
                assert exc.code == "RECOVERY_BUDGET_EXHAUSTED"
                break
            revision += 1
        else:
            raise AssertionError("the recovery budget was never enforced")


class TestNotificationIdentityAndText:
    """P2 notification identity/text."""

    def test_two_recoveries_produce_two_distinct_notifications(
        self, status_client, admin_key, db_engine
    ):
        payload = register(status_client, admin_key)
        key = payload["statusApiKey"]["value"]
        instance_id = payload["instanceId"]
        now = datetime.now(UTC)

        _resolved_episode(
            status_client, key, instance_id, str(uuid.uuid4()), seq=1, now=now
        )
        _resolved_episode(
            status_client, key, instance_id, str(uuid.uuid4()), seq=4, now=now
        )

        with Session(db_engine) as sess:
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            rows = (
                sess.execute(
                    select(StatusNotificationOutbox).where(
                        StatusNotificationOutbox.notification_kind
                        == "episode_recovered"
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 2, [r.dedupe_key for r in rows]

    def test_a_recovery_notification_does_not_ask_for_manual_help(self):
        from tts_erp_v2.plugin.status.notifications import render_message

        message = render_message(
            kind="episode_recovered",
            instance_id="i",
            payload={
                "kind": "episode_recovered",
                "pluginId": "ads-monitor",
                "incidentId": "x",
                "eventType": "decision_result",
                "reasonCode": "NONE",
                "bindingPhase": "ready",
                "executionPhase": "running",
                "observedAt": "2026-10-15T17:00:00Z",
            },
            attempt=1,
        )
        assert "需要人工处理" not in message.text


class TestProlongedNoCandidateNotification:
    """P2 §11: an online instance reporting NO_CANDIDATE for 180 s notifies."""

    def test_no_candidate_for_180_seconds_enqueues_one_notification(
        self, status_client, admin_key, session, db_engine
    ):
        """§11: a *reachable* instance stuck on NO_CANDIDATE notifies once.

        Driven through the service on one controlled clock so "still online" and
        "180 s without a candidate" are simultaneously true — with the HTTP path
        the sweep time and the report times would use different clocks and the
        case could not be expressed.
        """
        from tts_erp_v2.plugin.status.contracts import (
            report_payload_hash,
            status_report_from_wire,
        )

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        incident = str(uuid.uuid4())
        base = datetime.now(UTC)

        for step in range(5):
            at = base + timedelta(seconds=60 * step)
            body = _report_body(
                str(instance_id),
                seq=step + 1,
                observed_at=at,
                incident_id=incident,
                reason_code="NO_CANDIDATE",
                candidate_count=0,
            )
            report = status_report_from_wire(body)
            ingest_report(
                session,
                instance_id=instance_id,
                report=report,
                payload_hash=report_payload_hash(report),
                request_id=str(uuid.uuid4()),
                now=at,
                authenticated=authenticate_report(
                    session, bearer_token=payload["statusApiKey"]["value"]
                ),
            )
            session.commit()
            session.expire_all()

        sweep_at = base + timedelta(seconds=60 * 5)
        with Session(db_engine) as sess:
            run_scheduled(sess, now=sweep_at, sender=_retry_noop)
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            rows = (
                sess.execute(
                    select(StatusNotificationOutbox).where(
                        StatusNotificationOutbox.notification_kind
                        == "episode_no_candidate"
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1

        # A second sweep inside the same episode must not repeat it.
        with Session(db_engine) as sess:
            run_scheduled(
                sess, now=sweep_at + timedelta(seconds=60), sender=_retry_noop
            )
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            rows = (
                sess.execute(
                    select(StatusNotificationOutbox).where(
                        StatusNotificationOutbox.notification_kind
                        == "episode_no_candidate"
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1

    def test_a_candidate_reappearing_clears_the_no_candidate_clock(
        self, status_client, admin_key, session, db_engine
    ):
        from tts_erp_v2.plugin.status.contracts import (
            report_payload_hash,
            status_report_from_wire,
        )

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        incident = str(uuid.uuid4())
        base = datetime.now(UTC)

        for step, reason in enumerate(("NO_CANDIDATE", "NO_CANDIDATE", "TAB_CLOSED")):
            at = base + timedelta(seconds=60 * step)
            body = _report_body(
                str(instance_id),
                seq=step + 1,
                observed_at=at,
                incident_id=incident,
                reason_code=reason,
                candidate_count=0 if reason == "NO_CANDIDATE" else 1,
            )
            report = status_report_from_wire(body)
            ingest_report(
                session,
                instance_id=instance_id,
                report=report,
                payload_hash=report_payload_hash(report),
                request_id=str(uuid.uuid4()),
                now=at,
                authenticated=authenticate_report(
                    session, bearer_token=payload["statusApiKey"]["value"]
                ),
            )
            session.commit()
            session.expire_all()

        with Session(db_engine) as sess:
            run_scheduled(sess, now=base + timedelta(seconds=400), sender=_retry_noop)
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            rows = (
                sess.execute(
                    select(StatusNotificationOutbox).where(
                        StatusNotificationOutbox.notification_kind
                        == "episode_no_candidate"
                    )
                )
                .scalars()
                .all()
            )
            assert rows == []


def _retry_noop(message: Any) -> str:
    return "delivered"


class TestCanonicalPurposeGate:
    """P1 purpose: a bound general key must not pass as a status key.

    The canonical ``security.api_keys.purpose`` contract (access/security owner,
    monitor-config lane @ 03d595fb) is not in this tree yet, so the gate is
    expressed as a pure predicate over the raw column value and pinned directly.
    """

    def test_only_the_status_purpose_is_accepted(self):
        from tts_erp_v2.plugin.status.service import (
            PLUGIN_STATUS_PURPOSE,
            purpose_allows_status,
        )

        assert PLUGIN_STATUS_PURPOSE == "plugin_status"
        assert purpose_allows_status(PLUGIN_STATUS_PURPOSE) is True
        # Binding an instance never makes an ordinary key a recovery key.
        assert purpose_allows_status("general") is False
        assert purpose_allows_status("monitor_config") is False
        assert purpose_allows_status("unknown-purpose") is False


# ── Round-3 regressions (owner-accepted P1/P2, ERP seam only) ────────


class TestPurposeBoundaryIsFailClosed:
    """P1: missing canonical purpose must refuse, not fall back."""

    def test_a_missing_purpose_is_refused(self):
        from tts_erp_v2.plugin.status.service import purpose_allows_status

        # Fail-closed: an unreadable/absent purpose means the canonical contract
        # is unavailable, and unavailable is "no", never "assume general".
        assert purpose_allows_status(None) is False
        assert purpose_allows_status("plugin_status") is True
        assert purpose_allows_status("general") is False
        assert purpose_allows_status("monitor_config") is False

    def test_provisioning_mints_a_purpose_scoped_key(
        self, status_client, admin_key, session
    ):
        from tts_erp_v2.db.models.plugin_status import StatusInstance

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        row = session.get(StatusInstance, instance_id)
        api_row = session.get(ApiKey, row.status_api_key_id)
        assert (
            session.execute(
                text("SELECT purpose FROM security.api_keys WHERE id = :i"),
                {"i": row.status_api_key_id},
            ).scalar_one()
            == "plugin_status"
        )
        assert api_row.role == "readwrite"

    def test_a_general_readwrite_key_bound_to_an_instance_is_refused(
        self, status_client, admin_key, session, db_engine
    ):
        """Binding proves *which* instance, never *what the key may do* (§9.1)."""
        from tts_erp_v2.db.models.plugin_status import StatusInstance

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        general = _api_key(
            db_engine, "readwrite", "test_status_general_bound", "active"
        )
        from tts_erp_v2.access import hash_key

        general_id = session.execute(
            select(ApiKey.id).where(ApiKey.key_hash == hash_key(general))
        ).scalar_one()
        row = session.get(StatusInstance, instance_id)
        previous = row.status_api_key_id
        row.status_api_key_id = general_id
        session.commit()

        body = _report_body(
            str(instance_id),
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
        )
        resp = post_report(status_client, general, body)
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "PURPOSE_DENIED"

        row = session.get(StatusInstance, instance_id)
        row.status_api_key_id = previous
        session.commit()


class TestLifecycleMutationsAreCsrfGuarded:
    """P1: enable/disable are mutations and must carry the same guard."""

    @pytest.fixture()
    def cookie_client(self, db_engine, monkeypatch):
        """A client whose requests look cookie-authed to the CSRF guard."""
        from tts_erp_v2.api.v2 import plugin_status

        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
        app = FastAPI()

        @app.middleware("http")
        async def _mark_cookie(request, call_next):
            # Innermost: runs after AuthMiddleware, immediately before the
            # router, so it models a real browser session reaching the handler.
            request.scope["auth_method"] = "cookie"
            return await call_next(request)

        app.add_middleware(AuthMiddleware)
        app.include_router(plugin_status.router)
        with TestClient(app) as client:
            yield client

    def test_cookie_disable_without_the_header_is_refused_and_changes_nothing(
        self, status_client, cookie_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        instance_id = payload["instanceId"]
        resp = cookie_client.post(f"/v2/plugin/admin/instances/{instance_id}/disable")
        assert resp.status_code == 403, resp.text
        from tts_erp_v2.db.models.plugin_status import StatusInstance

        row = session.get(StatusInstance, uuid.UUID(instance_id))
        session.refresh(row)
        assert row.enabled is True

    def test_cookie_enable_without_the_header_is_refused(
        self, status_client, cookie_client, admin_key
    ):
        payload = register(status_client, admin_key)
        resp = cookie_client.post(
            f"/v2/plugin/admin/instances/{payload['instanceId']}/enable"
        )
        assert resp.status_code == 403, resp.text

    def test_the_same_request_with_the_header_is_accepted(
        self, status_client, cookie_client, admin_key, session
    ):
        payload = register(status_client, admin_key)
        instance_id = payload["instanceId"]
        resp = cookie_client.post(
            f"/v2/plugin/admin/instances/{instance_id}/disable",
            headers={"X-Requested-With": "tts-erp"},
        )
        assert resp.status_code == 200, resp.text
        from tts_erp_v2.db.models.plugin_status import StatusInstance

        row = session.get(StatusInstance, uuid.UUID(instance_id))
        session.refresh(row)
        assert row.enabled is False


class TestConnectivityFilterIsAppliedBeforePagination:
    """P2: the derived filter must shape both the page and the total."""

    def test_a_filtered_page_is_computed_from_the_filtered_set(
        self, status_client, admin_key
    ):
        a = register(status_client, admin_key)
        b = register(status_client, admin_key, advertiser="TEST_ADV_B")
        c = register(status_client, admin_key, advertiser="TEST_ADV_C")

        # Only C reports, so only C is online; A and B stay `unknown`.
        body = _report_body(
            c["instanceId"],
            seq=1,
            observed_at=datetime.now(UTC),
            incident_id=str(uuid.uuid4()),
            advertiser="TEST_ADV_C",
        )
        assert (
            post_report(status_client, c["statusApiKey"]["value"], body).status_code
            == 200
        )

        listed = status_client.get(
            "/v2/plugin/admin/instances",
            headers={"Authorization": f"Bearer {admin_key}"},
            params={"connectivity": "unknown", "limit": 1},
        )
        assert listed.status_code == 200, listed.text
        out = listed.json()
        assert out["total"] == 2, out
        assert len(out["items"]) == 1
        assert out["items"][0]["instanceId"] in {a["instanceId"], b["instanceId"]}

        online = status_client.get(
            "/v2/plugin/admin/instances",
            headers={"Authorization": f"Bearer {admin_key}"},
            params={"connectivity": "online"},
        )
        assert online.json()["total"] == 1
        assert online.json()["items"][0]["instanceId"] == c["instanceId"]


class TestDefaultSenderRunsOutsideATransaction:
    """P2 §11: secret resolution and message building happen before sending."""

    def test_the_default_resolver_sends_with_no_open_transaction(
        self, status_client, admin_key, db_engine, monkeypatch
    ):
        import httpx

        from tts_erp_v2.db.models.config import RuntimeConfigSecret
        from tts_erp_v2.proxy.token_service import encrypt

        payload = register(status_client, admin_key)
        now = datetime.now(UTC)
        body = _report_body(
            payload["instanceId"],
            seq=1,
            observed_at=now,
            incident_id=str(uuid.uuid4()),
        )
        assert (
            post_report(
                status_client, payload["statusApiKey"]["value"], body
            ).status_code
            == 200
        )

        with Session(db_engine) as sess:
            sess.execute(
                delete(RuntimeConfigSecret).where(
                    RuntimeConfigSecret.name == "plugin_status_lifecycle_webhook"
                )
            )
            sess.add(
                RuntimeConfigSecret(
                    name="plugin_status_lifecycle_webhook",
                    encrypted_value=encrypt("https://example.invalid/hook"),
                    fingerprint="0123456789abcdef",
                )
            )
            sess.commit()

        observed: dict[str, Any] = {}

        def _fake_post(self, url, **kwargs):
            observed["in_transaction"] = _holder["session"].in_transaction()
            observed["url"] = url
            return httpx.Response(200)

        monkeypatch.setattr(httpx.Client, "post", _fake_post)

        _holder: dict[str, Any] = {}
        with Session(db_engine) as sess:
            _holder["session"] = sess
            run_scheduled(sess, now=now + timedelta(seconds=400))

        assert observed.get("url"), "the default sender never ran"
        assert observed["in_transaction"] is False, (
            "notification delivery must happen with no open transaction"
        )


# ── Round-4 regressions (owner-accepted P1, ERP seam only) ───────────


class TestManualRecoveryRequiresTheRegisteredGrant:
    """§9.1: an operator command is still a recovery issuance."""

    def test_manual_recover_is_refused_without_the_recovery_grant(
        self, status_client, admin_key, session
    ):
        from tts_erp_v2.db.models.plugin_status import StatusInstance

        payload = register(status_client, admin_key, capabilities=())
        instance_id = payload["instanceId"]
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        issued = post_report(
            status_client,
            key,
            _report_body(
                instance_id, seq=1, observed_at=now, incident_id=str(uuid.uuid4())
            ),
        ).json()["decision"]
        assert issued["action"] == "require_user"

        before = session.get(StatusInstance, uuid.UUID(instance_id))
        revision_before = before.current_decision_revision

        resp = _manual(
            status_client,
            admin_key,
            instance_id,
            action="recover_binding",
            incident=issued["incidentId"],
            revision=issued["revision"],
            epoch=issued["expectedBindingEpoch"],
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "CAPABILITY_DENIED"

        after = session.get(StatusInstance, uuid.UUID(instance_id))
        session.refresh(after)
        # Refused before any decision, latch or budget mutation.
        assert after.current_decision_revision == revision_before
        assert after.manual_latch_action is None

    def test_a_granted_instance_still_recovers_manually(
        self, status_client, admin_key, session
    ):
        from tts_erp_v2.plugin.status import service as service_module
        from tts_erp_v2.plugin.status.contracts import AdminDecisionRequest

        payload = register(status_client, admin_key)
        instance_id = payload["instanceId"]
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        issued = post_report(
            status_client,
            key,
            _report_body(
                instance_id, seq=1, observed_at=now, incident_id=str(uuid.uuid4())
            ),
        ).json()["decision"]
        assert issued["action"] == "recover_binding"

        command = AdminDecisionRequest.model_validate(
            {
                "requestId": str(uuid.uuid4()),
                "expectedRevision": issued["revision"],
                "action": "recover_binding",
                "incidentId": issued["incidentId"],
                "expectedBindingEpoch": issued["expectedBindingEpoch"],
            }
        )
        out = service_module.manual_decision(
            session,
            instance_id=uuid.UUID(instance_id),
            command=command,
            now=now + timedelta(seconds=70),
        )
        session.commit()
        assert out["replayed"] is False
        assert out["decision"]["action"] == "recover_binding"


class TestUnresolvedFailureFactsAreSticky:
    """§11/§6.2: a fresh snapshot must not erase an unresolved failure."""

    def _sticky_none(self, instance_id, *, seq, at):
        """The controller's fresh snapshot: reason NONE, page still not ready."""
        return _report_body(
            instance_id,
            seq=seq,
            observed_at=at,
            incident_id=str(uuid.uuid4()),
            reason_code="NONE",
            binding_phase="invalid",
            candidate_count=0,
            identity_verified=False,
            execution_phase="suspended",
            execution_reason="PAGE_INVALID",
        )

    def test_no_candidate_survives_a_none_snapshot(
        self, status_client, admin_key, session, db_engine
    ):
        from tts_erp_v2.plugin.status.contracts import (
            report_payload_hash,
            status_report_from_wire,
        )

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        incident = str(uuid.uuid4())
        base = datetime.now(UTC)

        # The controller reports NO_CANDIDATE, then keeps emitting fresh
        # snapshots with reason NONE while nothing has changed.
        bodies = [
            _report_body(
                str(instance_id),
                seq=1,
                observed_at=base,
                incident_id=incident,
                reason_code="NO_CANDIDATE",
                candidate_count=0,
            ),
            self._sticky_none(str(instance_id), seq=2, at=base + timedelta(seconds=60)),
            self._sticky_none(
                str(instance_id), seq=3, at=base + timedelta(seconds=120)
            ),
        ]
        for body in bodies:
            report = status_report_from_wire(body)
            ingest_report(
                session,
                instance_id=instance_id,
                report=report,
                payload_hash=report_payload_hash(report),
                request_id=str(uuid.uuid4()),
                now=report.observed_at,
                authenticated=authenticate_report(
                    session, bearer_token=payload["statusApiKey"]["value"]
                ),
            )
            session.commit()
            session.expire_all()

        # Sweep while the instance is still comfortably online: §11's
        # no-candidate notice is for a *reachable* instance, so the sweep must
        # land inside the heartbeat window, not exactly on its edge.
        sweep_at = base + timedelta(seconds=250)
        with Session(db_engine) as sess:
            run_scheduled(sess, now=sweep_at, sender=_retry_noop)
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            rows = (
                sess.execute(
                    select(StatusNotificationOutbox).where(
                        StatusNotificationOutbox.notification_kind
                        == "episode_no_candidate"
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1, "a NONE snapshot erased the unresolved failure"

    def test_sticky_snapshots_do_not_reset_the_transport_failure_count(
        self, status_client, admin_key, session
    ):
        from tts_erp_v2.db.models.plugin_status import StatusInstance
        from tts_erp_v2.plugin.status.contracts import (
            report_payload_hash,
            status_report_from_wire,
        )

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        base = datetime.now(UTC)

        bodies = [
            _report_body(
                str(instance_id),
                seq=1,
                observed_at=base,
                incident_id=str(uuid.uuid4()),
                reason_code="TRANSPORT_TIMEOUT",
            ),
            self._sticky_none(str(instance_id), seq=2, at=base + timedelta(seconds=60)),
            self._sticky_none(
                str(instance_id), seq=3, at=base + timedelta(seconds=120)
            ),
        ]
        for body in bodies:
            report = status_report_from_wire(body)
            response = ingest_report(
                session,
                instance_id=instance_id,
                report=report,
                payload_hash=report_payload_hash(report),
                request_id=str(uuid.uuid4()),
                now=report.observed_at,
                authenticated=authenticate_report(
                    session, bearer_token=payload["statusApiKey"]["value"]
                ),
            )
            session.commit()
            session.expire_all()
            # One observed failure is not enough to authorise a recovery.
            assert response.decision is None or response.decision.action.value != (
                "recover_binding"
            )

        row = session.get(StatusInstance, instance_id)
        session.refresh(row)
        assert int(row.consecutive_transport_failures or 0) == 1, (
            "sticky snapshots must not reset the unresolved transport failure"
        )

    def test_contradictory_ready_evidence_does_clear_the_sticky_failure(
        self, status_client, admin_key, session
    ):
        from tts_erp_v2.db.models.plugin_status import StatusInstance
        from tts_erp_v2.plugin.status.contracts import (
            report_payload_hash,
            status_report_from_wire,
        )

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        base = datetime.now(UTC)
        incident = str(uuid.uuid4())

        first = _report_body(
            str(instance_id),
            seq=1,
            observed_at=base,
            incident_id=incident,
            reason_code="TRANSPORT_TIMEOUT",
        )
        second = _report_body(
            str(instance_id),
            seq=2,
            observed_at=base + timedelta(seconds=60),
            incident_id=incident,
            event_type="binding_ready",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_reason="INITIALIZING",
            snapshot_complete=True,
            snapshot_at=base + timedelta(seconds=60),
        )
        for body in (first, second):
            report = status_report_from_wire(body)
            ingest_report(
                session,
                instance_id=instance_id,
                report=report,
                payload_hash=report_payload_hash(report),
                request_id=str(uuid.uuid4()),
                now=report.observed_at,
                authenticated=authenticate_report(
                    session, bearer_token=payload["statusApiKey"]["value"]
                ),
            )
            session.commit()
            session.expire_all()

        row = session.get(StatusInstance, instance_id)
        session.refresh(row)
        assert int(row.consecutive_transport_failures or 0) == 0
        assert row.no_candidate_since is None


# ── Round-5 regressions (owner-accepted P1/P2, ERP seam only) ─────────
#
# These pin behaviour against the report sequence the **controller actually
# emits**, not a convenient substitute. That sequence is reproduced here as a
# fixture rather than by editing the plugin (which is another component's
# ownership): a controller keeps its *sticky* reason across heartbeats, so a
# heartbeat repeats the last real failure reason rather than reporting NONE.


def _controller_report(
    instance_id: str,
    *,
    seq: int,
    at: datetime,
    incident: str,
    sticky_reason: str,
    new_invalidation: bool,
    candidate_count: int = 1,
    execution_phase: str = "suspended",
    execution_reason: str = "PAGE_INVALID",
    monitor_enabled: bool = True,
) -> dict[str, Any]:
    """One report as ``tab-binding-controller`` emits it.

    * ``new_invalidation`` — a fresh business failure produced this report, so
      the controller emits ``binding_invalidated`` with the real reason.
    * otherwise the controller is re-sending unchanged state, so it emits a
      ``heartbeat`` that **repeats the sticky reason**. This is the real
      controller behaviour; substituting ``NONE`` here previously hid a real
      double-counting defect.
    """
    return _report_body(
        instance_id,
        seq=seq,
        observed_at=at,
        event_type="binding_invalidated" if new_invalidation else "heartbeat",
        reason_code=sticky_reason,
        incident_id=incident,
        binding_phase="invalid",
        candidate_count=candidate_count,
        identity_verified=False,
        execution_phase=execution_phase,
        execution_reason=execution_reason,
        monitor_enabled=monitor_enabled,
    )


class TestStickyReasonsCountOnce:
    """P1: one business failure plus its heartbeat is not two failures."""

    def test_a_repeated_sticky_heartbeat_does_not_count_as_a_new_failure(
        self, status_client, admin_key, session
    ):
        from tts_erp_v2.db.models.plugin_status import StatusInstance
        from tts_erp_v2.plugin.status.contracts import (
            report_payload_hash,
            status_report_from_wire,
        )

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        base = datetime.now(UTC)
        incident = str(uuid.uuid4())

        decisions = []
        for step in range(3):
            body = _controller_report(
                str(instance_id),
                seq=step + 1,
                at=base + timedelta(seconds=30 * step),
                incident=incident,
                sticky_reason="TRANSPORT_TIMEOUT",
                # Only the first report is a new business failure; the rest are
                # heartbeats repeating the same sticky reason.
                new_invalidation=(step == 0),
            )
            report = status_report_from_wire(body)
            response = ingest_report(
                session,
                instance_id=instance_id,
                report=report,
                payload_hash=report_payload_hash(report),
                request_id=str(uuid.uuid4()),
                now=report.observed_at,
                authenticated=authenticate_report(
                    session, bearer_token=payload["statusApiKey"]["value"]
                ),
            )
            session.commit()
            session.expire_all()
            decisions.append(
                response.decision.action.value if response.decision else None
            )

        row = session.get(StatusInstance, instance_id)
        session.refresh(row)
        assert int(row.consecutive_transport_failures or 0) == 1, (
            "a repeated sticky heartbeat was counted as a new business failure"
        )
        assert "recover_binding" not in decisions, decisions

    def test_a_second_distinct_failure_observation_does_authorise_recovery(
        self, status_client, admin_key, session
    ):
        from tts_erp_v2.plugin.status.contracts import (
            report_payload_hash,
            status_report_from_wire,
        )

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        base = datetime.now(UTC)
        incident = str(uuid.uuid4())

        actions = []
        # Two *new* invalidations, with a sticky heartbeat in between.
        plan = [
            (1, True, 0),
            (2, False, 30),
            (3, True, 60),
        ]
        for seq, is_new, offset in plan:
            body = _controller_report(
                str(instance_id),
                seq=seq,
                at=base + timedelta(seconds=offset),
                incident=incident,
                sticky_reason="TRANSPORT_TIMEOUT",
                new_invalidation=is_new,
            )
            report = status_report_from_wire(body)
            response = ingest_report(
                session,
                instance_id=instance_id,
                report=report,
                payload_hash=report_payload_hash(report),
                request_id=str(uuid.uuid4()),
                now=report.observed_at,
                authenticated=authenticate_report(
                    session, bearer_token=payload["statusApiKey"]["value"]
                ),
            )
            session.commit()
            session.expire_all()
            actions.append(
                response.decision.action.value if response.decision else None
            )

        assert actions[0] != "recover_binding"
        assert actions[-1] == "recover_binding", actions


class TestDisabledInstanceRefusesNewRecovery:
    """P1: a disabled instance is off the same disabled-priority branch."""

    def test_a_new_recovery_is_refused_while_disabled_and_mutates_nothing(
        self, status_client, admin_key, session
    ):
        from tts_erp_v2.db.models.plugin_status import StatusDecision, StatusInstance

        payload = register(status_client, admin_key)
        instance_id = payload["instanceId"]
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        issued = post_report(
            status_client,
            key,
            _report_body(
                instance_id, seq=1, observed_at=now, incident_id=str(uuid.uuid4())
            ),
        ).json()["decision"]
        assert issued["action"] == "recover_binding"

        disabled = status_client.post(
            f"/v2/plugin/admin/instances/{instance_id}/disable",
            headers={"Authorization": f"Bearer {admin_key}"},
        )
        assert disabled.status_code == 200, disabled.text

        before = session.get(StatusInstance, uuid.UUID(instance_id))
        revision_before = before.current_decision_revision
        decisions_before = (
            session.execute(
                select(StatusDecision).where(
                    StatusDecision.instance_id == uuid.UUID(instance_id)
                )
            )
            .scalars()
            .all()
        )

        resp = _manual(
            status_client,
            admin_key,
            instance_id,
            action="recover_binding",
            incident=issued["incidentId"],
            revision=issued["revision"],
            epoch=issued["expectedBindingEpoch"],
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "INSTANCE_DENIED"

        after = session.get(StatusInstance, uuid.UUID(instance_id))
        session.refresh(after)
        assert after.current_decision_revision == revision_before
        assert after.manual_latch_action is None
        assert len(
            session.execute(
                select(StatusDecision).where(
                    StatusDecision.instance_id == uuid.UUID(instance_id)
                )
            )
            .scalars()
            .all()
        ) == len(decisions_before)

    def test_a_frozen_historical_command_still_replays_while_disabled(
        self, status_client, admin_key
    ):
        """Replay is an audit read, not a new permission — it must survive."""
        payload = register(status_client, admin_key)
        instance_id = payload["instanceId"]
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        issued = post_report(
            status_client,
            key,
            _report_body(
                instance_id, seq=1, observed_at=now, incident_id=str(uuid.uuid4())
            ),
        ).json()["decision"]

        request_id = str(uuid.uuid4())
        command = {
            "requestId": request_id,
            "expectedRevision": issued["revision"],
            "action": "hold",
            "incidentId": issued["incidentId"],
            "expectedBindingEpoch": issued["expectedBindingEpoch"],
        }
        base = f"/v2/plugin/admin/instances/{instance_id}/decisions"
        headers = {"Authorization": f"Bearer {admin_key}"}
        first = status_client.post(base, headers=headers, json=command)
        assert first.status_code == 200, first.text

        status_client.post(
            f"/v2/plugin/admin/instances/{instance_id}/disable", headers=headers
        )

        replay = status_client.post(base, headers=headers, json=command)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert (
            replay.json()["decision"]["decisionId"]
            == first.json()["decision"]["decisionId"]
        )


class TestDisabledMonitorGetsNoNoCandidateFault:
    """P2: a disabled monitor must not be told it has no candidate page."""

    def test_a_disabled_report_ends_the_continuous_no_candidate_condition(
        self, status_client, admin_key, session, db_engine
    ):
        from tts_erp_v2.plugin.status.contracts import (
            report_payload_hash,
            status_report_from_wire,
        )

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        base = datetime.now(UTC)
        incident = str(uuid.uuid4())

        bodies = [
            _controller_report(
                str(instance_id),
                seq=1,
                at=base,
                incident=incident,
                sticky_reason="NO_CANDIDATE",
                new_invalidation=True,
                candidate_count=0,
            ),
        ]
        bodies[0]["incidentId"] = incident
        # The user switched monitoring off, and the controller keeps reporting
        # the disabled state. Two heartbeats, the last close to the sweep, so the
        # instance is unambiguously *online* when the sweep runs — otherwise the
        # notice would be skipped for the wrong reason (stale, not disabled).
        for step, offset in ((2, 60), (3, 120)):
            bodies.append(
                _report_body(
                    str(instance_id),
                    seq=step,
                    observed_at=base + timedelta(seconds=offset),
                    incident_id=incident,
                    event_type="heartbeat",
                    reason_code="USER_DISABLED",
                    binding_phase="invalid",
                    candidate_count=0,
                    identity_verified=False,
                    execution_phase="disabled",
                    execution_reason="USER_DISABLED",
                    monitor_enabled=False,
                )
            )
        for body in bodies:
            report = status_report_from_wire(body)
            ingest_report(
                session,
                instance_id=instance_id,
                report=report,
                payload_hash=report_payload_hash(report),
                request_id=str(uuid.uuid4()),
                now=report.observed_at,
                authenticated=authenticate_report(
                    session, bearer_token=payload["statusApiKey"]["value"]
                ),
            )
            session.commit()
            session.expire_all()

        sweep_at = base + timedelta(seconds=250)
        with Session(db_engine) as sess:
            run_scheduled(sess, now=sweep_at, sender=_retry_noop)
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            rows = (
                sess.execute(
                    select(StatusNotificationOutbox).where(
                        StatusNotificationOutbox.notification_kind
                        == "episode_no_candidate"
                    )
                )
                .scalars()
                .all()
            )
            assert rows == [], "a disabled monitor received a no-candidate fault notice"


def _locally_disabled_report(
    instance_id: str, *, seq: int, at: datetime, incident: str, reason: str = "NONE"
) -> dict[str, Any]:
    """A report from a monitor the *user* switched off locally.

    Deliberately keeps ``reasonCode`` in the persisting set, so only the local
    signals distinguish it: the monitor switch is off and execution is disabled.
    A server-side ``USER_DISABLED`` reason would have been caught by the
    supersede rule and would not test what this finding is about.
    """
    return _report_body(
        instance_id,
        seq=seq,
        observed_at=at,
        event_type="heartbeat",
        reason_code=reason,
        # Still inside the invalid-binding episode, so §6.2 requires the id.
        incident_id=incident,
        binding_phase="invalid",
        candidate_count=0,
        identity_verified=False,
        execution_phase="disabled",
        execution_reason="USER_DISABLED",
        monitor_enabled=False,
    )


class TestLocallyDisabledEndsTheNoCandidateCondition:
    """P2: §11 disabled priority must not depend on the reported reason code.

    Four *independent* signals mean "the user turned this off": the instance was
    disabled server-side, the report says the local monitor switch is off, or the
    report's execution is disabled / USER_DISABLED. The condition and the notice
    eligibility must agree on all four, not just the reason code.
    """

    def _sweep_notices(self, db_engine, at) -> Sequence[Any]:
        with Session(db_engine) as sess:
            run_scheduled(sess, now=at, sender=_retry_noop)
            from tts_erp_v2.db.models.plugin_status import StatusNotificationOutbox

            return (
                sess.execute(
                    select(StatusNotificationOutbox).where(
                        StatusNotificationOutbox.notification_kind
                        == "episode_no_candidate"
                    )
                )
                .scalars()
                .all()
            )

    def _ingest(self, session, instance_id, body, *, token: str) -> None:
        from tts_erp_v2.plugin.status.contracts import (
            report_payload_hash,
            status_report_from_wire,
        )

        report = status_report_from_wire(body)
        ingest_report(
            session,
            instance_id=instance_id,
            report=report,
            payload_hash=report_payload_hash(report),
            request_id=str(uuid.uuid4()),
            now=report.observed_at,
            authenticated=authenticate_report(session, bearer_token=token),
        )
        session.commit()
        session.expire_all()

    def test_a_locally_disabled_report_ends_an_existing_condition(
        self, status_client, admin_key, session, db_engine
    ):
        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        base = datetime.now(UTC)
        incident = str(uuid.uuid4())

        first = _controller_report(
            str(instance_id),
            seq=1,
            at=base,
            incident=incident,
            sticky_reason="NO_CANDIDATE",
            new_invalidation=True,
            candidate_count=0,
        )
        self._ingest(
            session, instance_id, first, token=payload["statusApiKey"]["value"]
        )
        # Then the user switches monitoring off: reason stays NONE, only the
        # local signals change.
        self._ingest(
            session,
            instance_id,
            _locally_disabled_report(
                str(instance_id),
                seq=2,
                at=base + timedelta(seconds=60),
                incident=incident,
            ),
            token=payload["statusApiKey"]["value"],
        )
        self._ingest(
            session,
            instance_id,
            _locally_disabled_report(
                str(instance_id),
                seq=3,
                at=base + timedelta(seconds=120),
                incident=incident,
            ),
            token=payload["statusApiKey"]["value"],
        )

        rows = self._sweep_notices(db_engine, base + timedelta(seconds=250))
        assert rows == [], (
            "a locally disabled monitor received a no-candidate fault notice"
        )

    def test_a_locally_disabled_report_never_starts_the_condition(
        self, status_client, admin_key, session, db_engine
    ):
        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        base = datetime.now(UTC)
        incident = str(uuid.uuid4())

        for seq, offset in ((1, 0), (2, 60), (3, 120)):
            self._ingest(
                session,
                instance_id,
                _locally_disabled_report(
                    str(instance_id),
                    seq=seq,
                    at=base + timedelta(seconds=offset),
                    incident=incident,
                    reason="NO_CANDIDATE",
                ),
                token=payload["statusApiKey"]["value"],
            )

        rows = self._sweep_notices(db_engine, base + timedelta(seconds=250))
        assert rows == [], (
            "a locally disabled monitor started a no-candidate fault condition"
        )

    def test_an_enabled_monitor_still_receives_the_notice(
        self, status_client, admin_key, session, db_engine
    ):
        """Guard against over-clearing: the notice must still fire when on."""
        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        base = datetime.now(UTC)
        incident = str(uuid.uuid4())

        for seq, offset in ((1, 0), (2, 60), (3, 120)):
            self._ingest(
                session,
                instance_id,
                _controller_report(
                    str(instance_id),
                    seq=seq,
                    at=base + timedelta(seconds=offset),
                    incident=incident,
                    sticky_reason="NO_CANDIDATE",
                    new_invalidation=(seq == 1),
                    candidate_count=0,
                ),
                token=payload["statusApiKey"]["value"],
            )

        rows = self._sweep_notices(db_engine, base + timedelta(seconds=250))
        assert len(rows) == 1, "the enabled no-candidate notice stopped firing"


class TestLockBoundaryIsAuthoritative:
    """P1: taking the row lock must also *read* the authoritative row.

    ``authenticate_report`` loads the instance into the session's identity map
    and hands it back. A later ``SELECT ... FOR UPDATE`` of the same primary key
    acquires the database lock but, without ``populate_existing``, returns the
    object already in the identity map with its previously loaded attributes. A
    concurrent operator command or first-advertiser assignment committed between
    authentication and the lock would then be invisible to the write path.
    """

    @staticmethod
    def _ingest(sess, instance_id, body, *, authenticated: AuthenticatedInstance):
        from tts_erp_v2.plugin.status.contracts import (
            report_payload_hash,
            status_report_from_wire,
        )

        report = status_report_from_wire(body)
        response = ingest_report(
            sess,
            instance_id=instance_id,
            report=report,
            payload_hash=report_payload_hash(report),
            request_id=str(uuid.uuid4()),
            now=report.observed_at,
            authenticated=authenticated,
        )
        sess.commit()
        return response

    def test_a_concurrent_operator_hold_is_visible_to_the_locking_writer(
        self, status_client, admin_key, db_engine
    ):
        from tts_erp_v2.plugin.status import service as service_module
        from tts_erp_v2.plugin.status.contracts import AdminDecisionRequest

        payload = register(status_client, admin_key)
        instance_id = uuid.UUID(payload["instanceId"])
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        incident = str(uuid.uuid4())

        with Session(db_engine) as seed:
            seed_decision = self._ingest(
                seed,
                instance_id,
                _report_body(
                    str(instance_id),
                    seq=1,
                    observed_at=now,
                    incident_id=incident,
                ),
                authenticated=authenticate_report(seed, bearer_token=key),
            ).decision
        assert seed_decision is not None
        assert seed_decision.action.value == "recover_binding"

        # Session A authenticates: this loads the instance into A's identity map.
        with Session(db_engine) as session_a:
            auth = service_module.authenticate_report(session_a, bearer_token=key)
            assert auth.instance.current_decision_revision == 1

            # Session B commits an operator HOLD while A still holds no lock.
            with Session(db_engine) as session_b:
                service_module.manual_decision(
                    session_b,
                    instance_id=instance_id,
                    command=AdminDecisionRequest.model_validate(
                        {
                            "requestId": str(uuid.uuid4()),
                            "expectedRevision": "1",
                            "action": "hold",
                            "incidentId": incident,
                            "expectedBindingEpoch": (
                                seed_decision.expected_binding_epoch
                            ),
                        }
                    ),
                    now=now + timedelta(seconds=1),
                )
                session_b.commit()

            # Session A now ingests. The lock must surface revision 2 and the
            # latch, so the HOLD keeps applying to this very report.
            response = self._ingest(
                session_a,
                instance_id,
                _report_body(
                    str(instance_id),
                    seq=2,
                    observed_at=now + timedelta(seconds=2),
                    incident_id=incident,
                    event_type="binding_ready",
                    reason_code="NONE",
                    binding_phase="ready",
                    identity_verified=True,
                    execution_reason="INITIALIZING",
                    snapshot_complete=True,
                    snapshot_at=now + timedelta(seconds=2),
                ),
                authenticated=auth,
            )
            assert response.decision is not None
            assert response.decision.action.value == "hold", (
                "the report ignored a HOLD committed after it authenticated"
            )
            from tts_erp_v2.db.models.plugin_status import StatusInstance

            locked = session_a.get(StatusInstance, instance_id)
            assert locked is not None
            assert locked.current_decision_revision >= 2

    def test_a_concurrent_first_advertiser_assignment_cannot_be_overwritten(
        self, status_client, admin_key, db_engine
    ):
        from tts_erp_v2.plugin.status import service as service_module

        payload = register(status_client, admin_key, advertiser=None)
        instance_id = uuid.UUID(payload["instanceId"])
        key = payload["statusApiKey"]["value"]
        now = datetime.now(UTC)
        incident = str(uuid.uuid4())

        ready = _report_body(
            str(instance_id),
            seq=1,
            observed_at=now,
            incident_id=incident,
            event_type="binding_ready",
            reason_code="NONE",
            binding_phase="ready",
            identity_verified=True,
            execution_reason="INITIALIZING",
            snapshot_complete=True,
            snapshot_at=now,
            advertiser=ADVERTISER,
        )

        with Session(db_engine) as session_a:
            auth = service_module.authenticate_report(session_a, bearer_token=key)
            assert auth.instance.scope.get("advertiserId") is None

            # Session B performs the one-time advertiser binding and commits.
            with Session(db_engine) as session_b:
                self._ingest(
                    session_b,
                    instance_id,
                    ready,
                    authenticated=authenticate_report(session_b, bearer_token=key),
                )

            # Session A must now see the bound advertiser and refuse a swap,
            # rather than re-running the compare-and-set on stale scope.
            from tts_erp_v2.plugin.status.contracts import (
                report_payload_hash,
                status_report_from_wire,
            )

            swap = _report_body(
                str(instance_id),
                seq=2,
                observed_at=now + timedelta(seconds=5),
                incident_id=incident,
                event_type="heartbeat",
                reason_code="NONE",
                binding_phase="ready",
                identity_verified=True,
                execution_phase="running",
                execution_reason="NONE",
                advertiser="TEST_ADVERTISER_LATE",
            )
            report = status_report_from_wire(swap)
            with pytest.raises(service_module.StatusServiceError) as refusal:
                ingest_report(
                    session_a,
                    instance_id=instance_id,
                    report=report,
                    payload_hash=report_payload_hash(report),
                    request_id=str(uuid.uuid4()),
                    now=report.observed_at,
                    authenticated=auth,
                )
            session_a.rollback()
            assert refusal.value.code == "SCOPE_DENIED"

            from tts_erp_v2.db.models.plugin_status import StatusInstance

            with Session(db_engine) as verify:
                row = verify.get(StatusInstance, instance_id)
                assert row is not None
                assert row.scope["advertiserId"] == ADVERTISER
