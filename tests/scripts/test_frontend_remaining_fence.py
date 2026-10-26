"""Cold, actual-profile/SQL tests of the canonical staged two-page fence."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from tests.support.frontend_p2_binding import checked_session, current_binding
from tests.support.frontend_p2_browser import BrowserFence
from tests.support.frontend_p2_canary import seed_and_check_canary
from tests.support.frontend_p2_live import live_api_server
from tests.support.frontend_remaining_targets import RemainingTargets
from tts_erp_v2.db.models.commerce import ChannelAccount, ChannelProduct

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_integration]


def gate_for(probe, marker, **targets):
    return BrowserFence(
        probe,
        username="test_p2_cold_actor",
        shop_id="9900123456789",
        service_id=marker,
        enum_value=marker,
        remaining=RemainingTargets(marker=marker, **targets),
    )


@pytest.fixture
def manual_owned():
    value, binding = current_binding()
    marker = "TEST_cold_" + uuid4().hex
    with live_api_server(value, collaborators=True) as child:
        probe = seed_and_check_canary(child)
        engine = create_engine(value)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        shop_pks = product_pks = ()
        try:
            with checked_session(binding, engine, factory) as session:
                shops = [
                    ChannelAccount(
                        platform="tiktok",
                        shop_id=marker + suffix,
                        account_name=marker,
                        status="active",
                    )
                    for suffix in ("_a", "_b")
                ]
                session.add_all(shops)
                session.flush()
                products = [
                    ChannelProduct(
                        shop_pk=shop.id,
                        spu_id=marker + "_shared",
                        title=marker,
                        status="ACTIVATE",
                    )
                    for shop in shops
                ]
                session.add_all(products)
                session.flush()
                shop_pks = tuple(row.id for row in shops)
                product_pks = tuple(row.id for row in products)
                session.commit()
            yield probe, marker, shop_pks, product_pks, engine, factory
        finally:
            if shop_pks:
                with checked_session(binding, engine, factory) as session:
                    actual_shops = list(
                        session.execute(
                            select(
                                ChannelAccount.id, ChannelAccount.account_name
                            ).where(ChannelAccount.id.in_(shop_pks))
                        )
                    )
                    actual_products = list(
                        session.execute(
                            select(
                                ChannelProduct.id,
                                ChannelProduct.shop_pk,
                                ChannelProduct.title,
                            ).where(ChannelProduct.id.in_(product_pks))
                        )
                    )
                    assert set(actual_shops) == {(pk, marker) for pk in shop_pks}
                    assert set(actual_products) == {
                        (pk, shop, marker)
                        for pk, shop in zip(product_pks, shop_pks, strict=True)
                    }
                    session.execute(
                        delete(ChannelProduct).where(ChannelProduct.id.in_(product_pks))
                    )
                    session.execute(
                        delete(ChannelAccount).where(ChannelAccount.id.in_(shop_pks))
                    )
                    session.commit()
                with checked_session(binding, engine, factory) as session:
                    assert (
                        session.scalar(
                            select(ChannelProduct.id).where(
                                ChannelProduct.id.in_(product_pks)
                            )
                        )
                        is None
                    )
                    assert (
                        session.scalar(
                            select(ChannelAccount.id).where(
                                ChannelAccount.id.in_(shop_pks)
                            )
                        )
                        is None
                    )
            engine.dispose()


def test_cold_default_child_forged_profile_cannot_grant_remaining():
    value, _binding = current_binding()
    marker = "TEST_forged_" + uuid4().hex
    with live_api_server(value) as child:
        probe = seed_and_check_canary(child)
        original = BrowserFence(
            probe,
            username="test_p2_cold_actor",
            shop_id="9900123456789",
            service_id=marker,
            enum_value=marker,
        )
        assert (
            original.check_request("GET", child.base + "/v2/pages/shops", None)
            == "/v2/pages/shops"
        )
        forged = replace(
            probe, child=replace(child, collaborator_profile="remaining-console-v1")
        )
        gate = gate_for(forged, marker)
        with pytest.raises(ValueError):
            gate.check_request("GET", child.base + "/v2/pages/manual-costs", None)


def test_cold_manual_cost_crossed_owned_tuple_refused(manual_owned):
    probe, marker, shops, products, _engine, _factory = manual_owned
    gate = gate_for(
        probe, marker, shop_pks=frozenset(shops), product_pks=frozenset(products)
    )
    body = {
        "spu_id": marker + "_shared",
        "spu_pk": products[0],
        "shop_pk": shops[1],
        "unit_cost": "1.0001",
        "currency": "USD",
    }
    with pytest.raises(ValueError):
        gate.check_request(
            "POST", probe.child.base + "/v2/reporting/manual-costs", body
        )


def test_cold_fixed_profile_requires_original_nonce():
    import httpx

    from tests.support.frontend_p2_binding import NONCE_HEADER, verify_attestation

    value, binding = current_binding()
    with live_api_server(value, collaborators=True) as child:
        probe = seed_and_check_canary(child)
        gate = gate_for(probe, "TEST_metadata_" + uuid4().hex)
        assert gate.observe_store()["putCount"] == 0
        with httpx.Client(base_url=child.base, trust_env=False) as client:
            for headers in ({}, {NONCE_HEADER: "TEST_wrong_nonce"}):
                response = client.get(
                    "/__test__/remaining-console-v1/store", headers=headers
                )
                verify_attestation(
                    child.attestation,
                    binding,
                    nonce=child.nonce,
                    child_pid=child.pid,
                    child_start=child.start,
                    http_nonce=response.headers.get(NONCE_HEADER),
                )
                assert response.status_code == 403
    # Original retained Popen lifecycle is the dead-child proof, not a fake poll.
    with pytest.raises(ValueError):
        gate.check_request("GET", child.base + "/v2/pages/video-publish", None)


def test_cold_unsupported_six_page_scopes_refused(manual_owned):
    probe, marker, shops, products, _engine, _factory = manual_owned
    gate = gate_for(
        probe, marker, shop_pks=frozenset(shops), product_pks=frozenset(products)
    )
    for name in (
        "ad-daily",
        "spu-profit-deterioration",
        "users",
        "runtime-configs",
        "intercept-configs",
        "sync-jobs",
    ):
        with pytest.raises(ValueError):
            gate.check_request("GET", probe.child.base + "/v2/pages/" + name, None)
    for path in (
        "/v2/reporting/ad-daily/options",
        "/v2/analytics/spu-profit-deterioration",
        "/v2/users",
        "/v2/roles",
        "/v2/config/runtime/items",
        "/v2/config/runtime/secrets",
        "/v2/intercept/configs",
        "/v2/sync/jobs",
    ):
        with pytest.raises(ValueError):
            gate.check_request("GET", probe.child.base + path, None)
    for name in ("manual-costs", "video-publish"):
        path = "/v2/pages/" + name
        assert gate.check_request("GET", probe.child.base + path, None) == path


def test_cold_missing_or_foreign_target_refused(manual_owned):
    probe, marker, shops, products, _engine, _factory = manual_owned
    missing = max(products) + 1000000
    gate = gate_for(
        probe, marker, shop_pks=frozenset(shops), product_pks=frozenset({missing})
    )
    with pytest.raises(ValueError):
        gate.check_request("GET", probe.child.base + "/v2/pages/manual-costs", None)
    foreign = gate_for(
        probe,
        marker + "_foreign",
        shop_pks=frozenset(shops),
        product_pks=frozenset(products),
    )
    with pytest.raises(ValueError):
        foreign.check_request("GET", probe.child.base + "/v2/pages/manual-costs", None)
    valid = gate_for(
        probe, marker, shop_pks=frozenset(shops), product_pks=frozenset(products)
    )
    path = "/v2/reporting/manual-costs"
    assert (
        valid.check_request(
            "POST",
            probe.child.base + path,
            {
                "spu_id": marker + "_shared",
                "spu_pk": products[0],
                "shop_pk": shops[0],
                "unit_cost": "1.0001",
                "currency": "USD",
            },
        )
        == path
    )
    # All matching ambiguous candidates are owned: permit ERP's intentional 409.
    assert (
        valid.check_request(
            "POST",
            probe.child.base + path,
            {"spu_id": marker + "_shared", "unit_cost": "1.0001", "currency": "USD"},
        )
        == path
    )


def test_cold_manual_cost_shared_external_ambiguity_requires_all_candidates_owned(
    manual_owned,
):
    probe, marker, shops, products, engine, factory = manual_owned
    foreign_shop_pk = foreign_product_pk = None
    binding = probe.child.binding
    try:
        with checked_session(binding, engine, factory) as session:
            shop = ChannelAccount(
                platform="tiktok",
                shop_id=marker + "_foreign",
                account_name=marker + "_foreign",
            )
            session.add(shop)
            session.flush()
            product = ChannelProduct(
                shop_pk=shop.id,
                spu_id=marker + "_shared",
                title=marker + "_foreign",
                status="ACTIVATE",
            )
            session.add(product)
            session.flush()
            foreign_shop_pk, foreign_product_pk = shop.id, product.id
            session.commit()
        gate = gate_for(
            probe, marker, shop_pks=frozenset(shops), product_pks=frozenset(products)
        )
        path = "/v2/reporting/manual-costs"
        with pytest.raises(ValueError):
            gate.check_request(
                "POST",
                probe.child.base + path,
                {
                    "spu_id": marker + "_shared",
                    "unit_cost": "1.0001",
                    "currency": "USD",
                },
            )
        assert (
            gate.check_request(
                "POST",
                probe.child.base + path,
                {
                    "spu_id": marker + "_shared",
                    "spu_pk": products[0],
                    "unit_cost": "1.0001",
                    "currency": "USD",
                },
            )
            == path
        )
        foreign = gate_for(
            probe,
            marker,
            shop_pks=frozenset({foreign_shop_pk}),
            product_pks=frozenset({foreign_product_pk}),
        )
        with pytest.raises(ValueError):
            foreign.check_request(
                "GET", probe.child.base + "/v2/pages/manual-costs", None
            )
    finally:
        if foreign_shop_pk is not None:
            with checked_session(binding, engine, factory) as session:
                assert (
                    session.scalar(
                        select(ChannelAccount.account_name).where(
                            ChannelAccount.id == foreign_shop_pk
                        )
                    )
                    == marker + "_foreign"
                )
                assert (
                    session.scalar(
                        select(ChannelProduct.title).where(
                            ChannelProduct.id == foreign_product_pk,
                            ChannelProduct.shop_pk == foreign_shop_pk,
                        )
                    )
                    == marker + "_foreign"
                )
                session.execute(
                    delete(ChannelProduct).where(
                        ChannelProduct.id == foreign_product_pk
                    )
                )
                session.execute(
                    delete(ChannelAccount).where(ChannelAccount.id == foreign_shop_pk)
                )
                session.commit()
            with checked_session(binding, engine, factory) as session:
                assert session.get(ChannelAccount, foreign_shop_pk) is None
                assert session.get(ChannelProduct, foreign_product_pk) is None


def test_cold_upload_url_response_task_correlation():
    import json
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import httpx

    from tests.support.frontend_p2_accounts import seed_accounts
    from tts_erp_v2.db.models.publishing import PublishWorkerHeartbeat, VideoPublishTask

    value, binding = current_binding()
    marker = "TEST_tasks_" + uuid4().hex
    with live_api_server(value, collaborators=True) as child:
        probe = seed_and_check_canary(child)
        accounts = seed_accounts(probe)
        account = next(row for row in accounts if row.role_code == "admin")
        other = next(row for row in accounts if row.role_code == "operator")
        gate = gate_for(probe, marker)
        gate.username = account.username
        engine = create_engine(value)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        created = set()
        versions = {}
        try:
            with checked_session(binding, engine, factory) as session:
                session.add(
                    PublishWorkerHeartbeat(
                        instance_id=marker,
                        hostname="TEST_host",
                        pid=12345,
                        status="ready",
                        device_status="ready",
                        started_at=datetime.now(UTC),
                        heartbeat_at=datetime.now(UTC),
                    )
                )
                session.commit()
            with httpx.Client(
                base_url=child.base, trust_env=False, timeout=5
            ) as client:
                login = client.post(
                    "/v2/auth/login",
                    json={"username": account.username, "password": account.password},
                )
                assert login.status_code == 200

                def dispatch(path, body, *, response_path=None):
                    route = MagicMock()
                    route.request = SimpleNamespace(
                        method="POST",
                        url=child.base + path,
                        post_data_json=body,
                        post_data=json.dumps(body),
                        headers={"x-requested-with": "tts-erp"},
                    )

                    def fetch(**_options):
                        response = client.post(
                            response_path or path,
                            json=body,
                            headers={"X-Requested-With": "tts-erp"},
                        )
                        data = response.json()
                        if "taskId" in data and "rowVersion" in data:
                            versions[data["taskId"]] = data["rowVersion"]
                        if (
                            path == "/v2/video-publish/tasks"
                            and response.status_code in (200, 201)
                        ):
                            created.add(data["taskId"])
                        return SimpleNamespace(
                            status=response.status_code,
                            headers=response.headers,
                            json=lambda: data,
                        )

                    route.fetch.side_effect = fetch
                    gate.handle(route)
                    return route

                for _ in range(2):
                    route = dispatch(
                        "/v2/video-publish/tasks",
                        {
                            "clientRequestId": str(uuid4()),
                            "filename": marker + ".mp4",
                            "contentType": "video/mp4",
                            "sizeBytes": 16,
                            "caption": marker,
                        },
                    )
                    assert bool(route.fulfill.called) and not bool(route.abort.called)
                assert len(created) == 2 and len(gate._tickets) == 2
                gate.username = other.username
                with pytest.raises(ValueError):
                    gate.check_request(
                        "GET",
                        child.base + "/v2/video-publish/tasks/" + next(iter(created)),
                        None,
                    )
                gate.username = account.username
                first, second = sorted(created)
                old_ticket = next(
                    url for url, target in gate._tickets.items() if target[0] == first
                )
                route = dispatch("/v2/video-publish/tasks/" + first + "/upload-url", {})
                assert bool(route.fulfill.called) and not bool(route.abort.called)
                assert bool(old_ticket not in gate._tickets)
                route = dispatch(
                    "/v2/video-publish/tasks/" + first + "/upload-url",
                    {},
                    response_path="/v2/video-publish/tasks/" + second + "/upload-url",
                )
                assert bool(route.abort.called) and not bool(route.fulfill.called)
                route = dispatch(
                    "/v2/video-publish/tasks/" + first + "/cancel",
                    {"rowVersion": versions[first]},
                )
                assert bool(route.fulfill.called) and not bool(route.abort.called)
                assert not any(target[0] == first for target in gate._tickets.values())
        finally:
            with checked_session(binding, engine, factory) as session:
                rows = list(
                    session.scalars(
                        select(VideoPublishTask).where(
                            VideoPublishTask.caption == marker
                        )
                    )
                )
                assert {str(row.public_id) for row in rows} == created
                assert all(
                    row.created_by_user_id == account.user_pk
                    and row.original_filename == marker + ".mp4"
                    for row in rows
                )
                session.execute(
                    delete(VideoPublishTask).where(
                        VideoPublishTask.public_id.in_([UUID(key) for key in created])
                    )
                )
                session.execute(
                    delete(PublishWorkerHeartbeat).where(
                        PublishWorkerHeartbeat.instance_id == marker,
                        PublishWorkerHeartbeat.hostname == "TEST_host",
                    )
                )
                session.commit()
            with checked_session(binding, engine, factory) as session:
                assert (
                    session.scalar(
                        select(VideoPublishTask.id).where(
                            VideoPublishTask.caption == marker
                        )
                    )
                    is None
                )
                assert session.get(PublishWorkerHeartbeat, marker) is None
            engine.dispose()



def test_remaining_request_canonical_wire_pk_string():
    from tests.support.frontend_remaining_targets import remaining_request

    targets = RemainingTargets(marker="TEST_manual_wire_pk")
    path = "/v2/reporting/manual-costs"
    body = {
        "spu_id": "TEST_manual_wire_pk_shared",
        "spu_pk": 47,
        "unit_cost": "1.0001",
        "currency": "USD",
    }
    integer_allowed = remaining_request(targets, "POST", path, {}, body)
    assert integer_allowed
    wire_body = {**body, "spu_pk": str(body["spu_pk"])}
    string_allowed = remaining_request(targets, "POST", path, {}, wire_body)
    assert string_allowed


def test_cold_manual_cost_canonical_wire_pk_strings(manual_owned):
    probe, marker, shops, products, _engine, _factory = manual_owned
    gate = gate_for(
        probe,
        marker,
        shop_pks=frozenset(shops),
        product_pks=frozenset(products),
    )
    path = "/v2/reporting/manual-costs"
    body = {
        "spu_id": marker + "_shared",
        "spu_pk": products[0],
        "unit_cost": "1.0001",
        "currency": "USD",
    }
    integer_path = gate.check_request("POST", probe.child.base + path, body)
    integer_allowed = integer_path == path
    assert integer_allowed
    wire_body = {**body, "spu_pk": str(body["spu_pk"])}
    string_path = gate.check_request("POST", probe.child.base + path, wire_body)
    string_allowed = string_path == path
    assert string_allowed
