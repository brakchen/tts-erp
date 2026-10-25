"""Remaining-console API acceptance against the owned isolated child.

Real authentication, CSRF, application sessions and SQL; only the maintained
fixed collaborator profile prevents real scheduled jobs/storage/device access.
This is not browser/native, worker-completion or deployment acceptance.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.support.frontend_p2_accounts import SeedAccount, seed_accounts
from tests.support.frontend_p2_binding import (
    NONCE_HEADER,
    checked_session,
    current_binding,
    validate_application,
    verify_attestation,
)
from tests.support.frontend_p2_canary import (
    VerifiedDataProbe,
    require_data_probe,
    seed_and_check_canary,
)
from tests.support.frontend_p2_live import VerifiedChild, live_api_server
from tts_erp_v2.db.models.commerce import ChannelAccount, ChannelProduct
from tts_erp_v2.db.models.intercept import InterceptConfig
from tts_erp_v2.db.models.procurement import ManualProductCost

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_integration]
CSRF = {"X-Requested-With": "tts-erp"}


@dataclass(frozen=True)
class OwnedClone:
    child: VerifiedChild = field(repr=False)
    probe: VerifiedDataProbe = field(repr=False)
    accounts: dict[str, SeedAccount] = field(repr=False)
    engine: Engine = field(repr=False)
    factory: sessionmaker[Session] = field(repr=False)
    prefix: str


@pytest.fixture(scope="module", params=["", "/tts"], ids=["root", "tts"])
def clone(request: pytest.FixtureRequest) -> Iterator[OwnedClone]:
    value, binding = current_binding()
    with live_api_server(value, collaborators=True) as child:
        probe = seed_and_check_canary(child, prefix=request.param)
        accounts = {account.role_code: account for account in seed_accounts(probe)}
        require_data_probe(probe)
        engine = create_engine(value)
        try:
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            assert validate_application(binding, engine, factory) == binding.run_db
            child.require_live()
            yield OwnedClone(child, probe, accounts, engine, factory, request.param)
            child.require_live()
        finally:
            engine.dispose()


def owned(clone: OwnedClone, response: httpx.Response, status: int) -> httpx.Response:
    """Reuse the original receipt and retained Popen for every API response."""
    clone.child.require_live()
    verify_attestation(
        clone.child.attestation,
        clone.child.binding,
        nonce=clone.child.nonce,
        child_pid=clone.child.pid,
        child_start=clone.child.start,
        http_nonce=response.headers.get(NONCE_HEADER),
    )
    if response.status_code != status:
        pytest.fail(f"Owned API HTTP status {response.status_code}; expected {status}")
    return response


def login(clone: OwnedClone, client: httpx.Client, role: str) -> None:
    account = clone.accounts[role]
    owned(
        clone,
        client.post(
            clone.prefix + "/v2/auth/login",
            json={"username": account.username, "password": account.password},
        ),
        200,
    )
    me = owned(clone, client.get(clone.prefix + "/v2/auth/me"), 200).json()
    assert me["authenticated"] is True
    assert me["username"] == account.username
    assert (
        me["role"]
        == {"viewer": "readonly", "operator": "readwrite", "admin": "admin"}[role]
    )


def test_cookie_login_me_role_rejection_and_logout(clone: OwnedClone) -> None:
    require_data_probe(clone.probe)
    with httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as client:
        me = owned(clone, client.get(clone.prefix + "/v2/auth/me"), 200).json()
        assert me["authenticated"] is False
        owned(clone, client.get(clone.prefix + "/v2/reporting/manual-costs"), 401)
        for role in ("viewer", "operator", "admin"):
            login(clone, client, role)
            cookie_facts = [
                (
                    cookie.path,
                    cookie.has_nonstandard_attr("HttpOnly"),
                    cookie.get_nonstandard_attr("SameSite"),
                )
                for cookie in client.cookies.jar
                if cookie.name == "tts_erp_session"
            ]
            assert len(cookie_facts) == 1
            cookie_path, http_only, same_site = cookie_facts[0]
            assert cookie_path == (clone.prefix or "/")
            assert http_only
            assert isinstance(same_site, str) and same_site.lower() == "lax"
            if clone.prefix:
                outside = owned(clone, client.get("/v2/auth/me"), 200).json()
                assert outside["authenticated"] is False
            own_user = clone.accounts[role].user_pk
            owned(
                clone,
                client.get(clone.prefix + f"/v2/users/{own_user}"),
                200 if role == "admin" else 403,
            )
            owned(
                clone,
                client.get(
                    clone.prefix + "/v2/reporting/manual-costs",
                    params={"q": "TEST_no_cost_" + uuid4().hex},
                ),
                200,
            )
            if role == "viewer":
                owned(
                    clone,
                    client.post(
                        clone.prefix + "/v2/reporting/manual-costs",
                        json={
                            "spu_id": "TEST_denied",
                            "unit_cost": "1.0001",
                            "currency": "USD",
                        },
                        headers=CSRF,
                    ),
                    403,
                )
            owned(
                clone, client.post(clone.prefix + "/v2/auth/logout", headers=CSRF), 204
            )
            me = owned(clone, client.get(clone.prefix + "/v2/auth/me"), 200).json()
            assert me["authenticated"] is False
            owned(clone, client.get(clone.prefix + "/v2/reporting/manual-costs"), 401)


def cost_history(clone: OwnedClone, spu_pks: tuple[int, ...]) -> list[tuple]:
    """Independent transaction, not handler DTOs or the seeding identity map."""
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        statement = (
            select(
                ManualProductCost.id,
                ManualProductCost.spu_pk,
                ManualProductCost.unit_cost,
                ManualProductCost.currency,
                ManualProductCost.valid_from,
                ManualProductCost.valid_to,
                ManualProductCost.note,
                ManualProductCost.created_by,
                ManualProductCost.created_at,
                ManualProductCost.updated_at,
            )
            .where(ManualProductCost.spu_pk.in_(spu_pks))
            .order_by(ManualProductCost.id)
        )
        return [tuple(row) for row in session.execute(statement)]


@pytest.fixture
def manual_cost_facts(
    clone: OwnedClone,
) -> Iterator[tuple[str, tuple[int, ...], int]]:
    """Own committed HTTP-visible facts only for this test's lifetime."""
    require_data_probe(clone.probe)
    marker = "TEST_remaining_cost_" + uuid4().hex
    shared_external = marker + "_shared"
    other_external = marker + "_other"
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()
        shops = [
            ChannelAccount(
                platform="tiktok",
                shop_id=marker + suffix,
                account_name=marker,
                status="active",
            )
            for suffix in ("_shop_a", "_shop_b")
        ]
        session.add_all(shops)
        session.flush()
        products = [
            ChannelProduct(
                shop_pk=shops[0].id,
                spu_id=shared_external,
                title=marker,
                status="ACTIVATE",
            ),
            ChannelProduct(
                shop_pk=shops[1].id,
                spu_id=shared_external,
                title=marker,
                status="ACTIVATE",
            ),
            ChannelProduct(
                shop_pk=shops[0].id,
                spu_id=other_external,
                title=marker,
                status="ACTIVATE",
            ),
        ]
        session.add_all(products)
        session.flush()
        spu_pks = tuple(product.id for product in products)
        shop_pks = tuple(shop.id for shop in shops)
        shop_pk = shops[0].id
        # Failed setup before commit rolls back when checked_session closes.
        clone.child.require_live()
        session.commit()
    try:
        yield marker, spu_pks, shop_pk
    finally:
        require_data_probe(clone.probe)
        with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
            clone.child.require_live()
            # Verify both ownership sets before deleting any dependent history.
            assert set(
                session.execute(
                    select(
                        ChannelAccount.id,
                        ChannelAccount.platform,
                        ChannelAccount.shop_id,
                        ChannelAccount.account_name,
                    ).where(ChannelAccount.id.in_(shop_pks))
                )
            ) == {
                (shop_pks[0], "tiktok", marker + "_shop_a", marker),
                (shop_pks[1], "tiktok", marker + "_shop_b", marker),
            }
            assert set(
                session.execute(
                    select(
                        ChannelProduct.id,
                        ChannelProduct.shop_pk,
                        ChannelProduct.spu_id,
                        ChannelProduct.title,
                    ).where(ChannelProduct.id.in_(spu_pks))
                )
            ) == {
                (spu_pks[0], shop_pks[0], shared_external, marker),
                (spu_pks[1], shop_pks[1], shared_external, marker),
                (spu_pks[2], shop_pks[0], other_external, marker),
            }
            session.execute(
                delete(ManualProductCost).where(ManualProductCost.spu_pk.in_(spu_pks))
            )
            session.execute(delete(ChannelProduct).where(ChannelProduct.id.in_(spu_pks)))
            session.execute(delete(ChannelAccount).where(ChannelAccount.id.in_(shop_pks)))
            clone.child.require_live()
            session.commit()
        with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
            clone.child.require_live()
            assert session.scalar(
                select(ManualProductCost.id).where(ManualProductCost.spu_pk.in_(spu_pks))
            ) is None
            assert session.scalar(
                select(ChannelProduct.id).where(ChannelProduct.id.in_(spu_pks))
            ) is None
            assert session.scalar(
                select(ChannelAccount.id).where(ChannelAccount.id.in_(shop_pks))
            ) is None


def test_manual_cost_exact_identity_decimal_history_and_rejections(
    clone: OwnedClone, manual_cost_facts: tuple[str, tuple[int, ...], int],
) -> None:
    require_data_probe(clone.probe)
    marker, spu_pks, shop_pk = manual_cost_facts
    shared_external = marker + "_shared"
    other_external = marker + "_other"
    assert cost_history(clone, spu_pks) == []
    payload = {
        "spu_pk": spu_pks[0],
        "spu_id": shared_external,
        "unit_cost": "1234567890123456.7891",
        "currency": "USD",
        "note": marker,
    }
    with httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as client:
        login(clone, client, "viewer")
        readonly_before = cost_history(clone, spu_pks)
        owned(
            clone,
            client.post(
                clone.prefix + "/v2/reporting/manual-costs", json=payload, headers=CSRF
            ),
            403,
        )
        assert cost_history(clone, spu_pks) == readonly_before
        owned(clone, client.post(clone.prefix + "/v2/auth/logout", headers=CSRF), 204)
        login(clone, client, "operator")
        owned(
            clone,
            client.post(clone.prefix + "/v2/reporting/manual-costs", json=payload),
            403,
        )
        assert cost_history(clone, spu_pks) == []
        first = owned(
            clone,
            client.post(
                clone.prefix + "/v2/reporting/manual-costs", json=payload, headers=CSRF
            ),
            201,
        ).json()
        assert first["spu_pk"] == spu_pks[0]
        assert isinstance(first["unit_cost"], str)
        assert Decimal(first["unit_cost"]) == Decimal(payload["unit_cost"])
        before = cost_history(clone, spu_pks)
        assert len(before) == 1
        assert before[0][0] == first["id"]
        assert before[0][1:4] == (spu_pks[0], Decimal(payload["unit_cost"]), "USD")
        assert before[0][5:8] == (None, marker, "api_key:readwrite")
        assert before[0][4].utcoffset().total_seconds() == 0
        rejected = [
            ({**payload, "spu_id": other_external}, 404),
            ({key: value for key, value in payload.items() if key != "spu_pk"}, 409),
            ({**payload, "unit_cost": "0"}, 422),
            ({**payload, "currency": "usd"}, 422),
        ]
        for invalid, status in rejected:
            owned(
                clone,
                client.post(
                    clone.prefix + "/v2/reporting/manual-costs",
                    json=invalid,
                    headers=CSRF,
                ),
                status,
            )
            assert cost_history(clone, spu_pks) == before
        second_payload = {**payload, "unit_cost": "0.0007", "note": marker + "_updated"}
        second = owned(
            clone,
            client.post(
                clone.prefix + "/v2/reporting/manual-costs",
                json=second_payload,
                headers=CSRF,
            ),
            201,
        ).json()
        after = cost_history(clone, spu_pks)
        assert len(after) == 2
        assert after[0][:5] == before[0][:5]
        assert after[0][5] is not None
        assert after[0][6:9] == before[0][6:9]
        assert after[1][0] == second["id"] != first["id"]
        assert after[1][1:4] == (spu_pks[0], Decimal("0.0007"), "USD")
        assert after[1][5:8] == (None, marker + "_updated", "api_key:readwrite")
        listing = owned(
            clone,
            client.get(
                clone.prefix + "/v2/reporting/manual-costs",
                params={"shop_pk": shop_pk, "q": shared_external},
            ),
            200,
        ).json()
        assert listing["total"] == 2
        assert {row["id"] for row in listing["items"]} == {first["id"], second["id"]}
        assert all(
            row["shop_pk"] == shop_pk and row["spu_pk"] == spu_pks[0]
            for row in listing["items"]
        )
        assert {Decimal(row["unit_cost"]) for row in listing["items"]} == {
            Decimal(payload["unit_cost"]),
            Decimal("0.0007"),
        }
        owned(clone, client.post(clone.prefix + "/v2/auth/logout", headers=CSRF), 204)


def intercept_snapshot(clone: OwnedClone, marker: str) -> list[tuple]:
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        statement = select(
            InterceptConfig.id,
            InterceptConfig.domain,
            InterceptConfig.endpoint,
            InterceptConfig.mode,
            InterceptConfig.capture_headers,
            InterceptConfig.capture_body,
            InterceptConfig.description,
            InterceptConfig.tags,
            InterceptConfig.enabled,
            InterceptConfig.created_at,
            InterceptConfig.updated_at,
        ).where(InterceptConfig.domain == marker)
        return [tuple(row) for row in session.execute(statement)]


def test_intercept_test_config_create_read_update_and_permission_rejection(
    clone: OwnedClone,
) -> None:
    require_data_probe(clone.probe)
    marker = "TEST_remaining_intercept_" + uuid4().hex
    payload = {
        "domain": marker,
        "endpoint": "/TEST_remaining",
        "mode": "whitelist",
        "capture_headers": False,
        "capture_body": False,
        "description": marker,
        "tags": [marker],
        "enabled": False,
    }
    assert intercept_snapshot(clone, marker) == []
    with (
        httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as writer,
        httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as reader,
    ):
        login(clone, reader, "viewer")
        owned(
            clone,
            reader.post(
                clone.prefix + "/v2/intercept/configs", json=payload, headers=CSRF
            ),
            403,
        )
        assert intercept_snapshot(clone, marker) == []
        login(clone, writer, "operator")
        created = owned(
            clone,
            writer.post(
                clone.prefix + "/v2/intercept/configs", json=payload, headers=CSRF
            ),
            201,
        ).json()["config"]
        config_pk = created["id"]
        before = intercept_snapshot(clone, marker)
        assert len(before) == 1
        assert before[0][:9] == (
            config_pk,
            marker,
            "/TEST_remaining",
            "whitelist",
            False,
            False,
            marker,
            [marker],
            False,
        )
        owned(
            clone,
            writer.post(
                clone.prefix + "/v2/intercept/configs", json=payload, headers=CSRF
            ),
            409,
        )
        assert intercept_snapshot(clone, marker) == before
        updated_payload = {
            **payload,
            "mode": "blacklist",
            "description": marker + "_updated",
            "tags": [marker + "_updated"],
        }
        path = clone.prefix + f"/v2/intercept/configs/{config_pk}"
        owned(clone, reader.put(path, json=updated_payload, headers=CSRF), 403)
        assert intercept_snapshot(clone, marker) == before
        owned(clone, writer.put(path, json=updated_payload, headers=CSRF), 200)
        after = intercept_snapshot(clone, marker)
        assert len(after) == 1
        assert after[0][:9] == (
            config_pk,
            marker,
            "/TEST_remaining",
            "blacklist",
            False,
            False,
            marker + "_updated",
            [marker + "_updated"],
            False,
        )
        assert after[0][9] == before[0][9]
        detail = owned(clone, writer.get(path), 200).json()["config"]
        assert detail["id"] == config_pk
        assert all(detail[key] == value for key, value in updated_payload.items())
        listing = owned(
            clone,
            writer.get(
                clone.prefix + "/v2/intercept/configs", params={"domain": marker}
            ),
            200,
        ).json()
        assert listing["total"] == 1
        assert [row["id"] for row in listing["configs"]] == [config_pk]
        for client in (reader, writer):
            owned(
                clone, client.post(clone.prefix + "/v2/auth/logout", headers=CSRF), 204
            )


def test_sync_trigger_only_accepts_via_fixed_noop_collaborator(
    clone: OwnedClone,
) -> None:
    from tts_erp_v2.sync_worker.scheduler import JOBS

    require_data_probe(clone.probe)
    job = next(name for name, spec in JOBS.items() if not spec.is_tiktok)
    path = clone.prefix + f"/v2/admin/sync-jobs/{job}/trigger"
    with httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as client:
        login(clone, client, "viewer")
        owned(clone, client.post(path, json={}, headers=CSRF), 403)
        owned(clone, client.post(clone.prefix + "/v2/auth/logout", headers=CSRF), 204)
        login(clone, client, "operator")
        owned(clone, client.post(path, json={}), 403)
        owned(
            clone,
            client.post(
                path, json={"shop_id": "TEST_invalid_system_scope"}, headers=CSRF
            ),
            422,
        )
        owned(
            clone,
            client.post(
                clone.prefix + "/v2/admin/sync-jobs/TEST_unknown_job/trigger",
                json={},
                headers=CSRF,
            ),
            404,
        )
        accepted = owned(clone, client.post(path, json={}, headers=CSRF), 200).json()
        assert accepted["accepted"] is True
        assert accepted["job_name"] == job
        assert accepted["shop_id"] is None
        # Only HTTP admission is proved: the child records instead of running it.
        owned(clone, client.post(clone.prefix + "/v2/auth/logout", headers=CSRF), 204)
