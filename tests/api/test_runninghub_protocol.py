"""Official protocol, redaction, workflow and permission contracts."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.support.runninghub_workflow import graph
from tts_erp_v2.runninghub.client import RemoteError, RunningHubClient, sanitize
from tts_erp_v2.runninghub.workflow import (
    MAX_SEED,
    WorkflowError,
    prepare,
    validate_graph,
)

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]


def test_complete_workflow_mapping_and_safe_seeds():
    original = graph()
    modified, nodes = prepare(
        original, "openapi/TEST.png", "openapi/TEST.mp4", MAX_SEED, {}
    )
    assert modified.keys() == original.keys()
    assert modified["136"]["inputs"]["prompt"] == ["173", 0]
    assert modified["136"]["inputs"]["height"] == ["167", 2]
    assert modified["132"]["inputs"]["value"] == 5.0
    assert type(nodes[2]["fieldValue"]) is int
    assert nodes[3]["fieldValue"] <= 2**53 - 1
    assert original["137"]["inputs"]["image"] == "TEST_image.png"
    broken = graph()
    broken["136"]["inputs"]["width"] = ["167", 0]
    with pytest.raises(WorkflowError, match="136.width"):
        validate_graph(broken)
    with pytest.raises(WorkflowError):
        prepare(original, "x", "y", MAX_SEED + 1, {})
    with pytest.raises(WorkflowError):
        validate_graph({"nodes": [], "links": []})


def test_official_wrappers_ids_multipart_and_no_create_retry(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer TEST_SECRET"
        if request.url.path.endswith("accountStatus"):
            assert json.loads(request.content) == {"apikey": "TEST_SECRET"}
            return httpx.Response(
                200, json={"code": 0, "data": {"remainCoins": "3.50"}}
            )
        if request.url.path.endswith("getJsonApiFormat"):
            assert json.loads(request.content)["workflowId"] == "2111762608670990337"
            return httpx.Response(
                200, json={"code": 0, "data": {"prompt": json.dumps(graph())}}
            )
        if request.url.path.endswith("binary"):
            assert b'name="file"' in request.content
            assert request.headers["content-type"].startswith(
                "multipart/form-data; boundary="
            )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "fileName": "openapi/TEST.png",
                        "download_url": "https://invalid/",
                    },
                },
            )
        if request.url.path.endswith("query"):
            assert json.loads(request.content) == {"taskId": "90071992547409931234567"}
            return httpx.Response(
                200,
                json={
                    "taskId": "90071992547409931234567",
                    "status": "RUNNING",
                    "results": None,
                },
            )
        raise httpx.ReadTimeout("TEST timeout")

    client = RunningHubClient("TEST_SECRET", transport=httpx.MockTransport(handler))
    assert client.account()["remainCoins"] == "3.50"
    assert validate_graph(client.workflow("2111762608670990337")) == graph()
    source = tmp_path / "TEST.png"
    source.write_bytes(b"TEST_bytes")
    assert client.upload(source) == "openapi/TEST.png"
    assert client.query("90071992547409931234567")["status"] == "RUNNING"
    with pytest.raises(RemoteError) as error:
        client.create({"workflowId": "2111762608670990337"})
    assert error.value.uncertain
    assert sum(r.url.path.endswith("create") for r in calls) == 1
    client.close()


@pytest.mark.parametrize("code", [0, 807, False, "0"])
def test_cancel_uses_official_contract_and_requires_integer_zero(code):
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/task/openapi/cancel"
        assert request.headers["Authorization"] == "Bearer TEST_SECRET"
        assert json.loads(request.content) == {
            "apiKey": "TEST_SECRET",
            "taskId": "90071992547409931234567",
        }
        return httpx.Response(
            200, json={"code": code, "data": None, "msg": "TEST_SECRET"}
        )

    client = RunningHubClient("TEST_SECRET", transport=httpx.MockTransport(handler))
    try:
        if type(code) is int and code == 0:
            assert client.cancel("90071992547409931234567") == {
                "code": 0,
                "data": None,
                "msg": "[redacted]",
            }
        else:
            with pytest.raises(RemoteError, match="UPSTREAM_CANCEL_REJECTED"):
                client.cancel("90071992547409931234567")
    finally:
        client.close()


def test_redaction_nested_diagnostics():
    raw = {
        "apiKey": "TEST_SECRET",
        "Authorization": "Bearer TEST_SECRET",
        "netWssUrl": "wss://secret",
        "msg": "echo TEST_SECRET",
        "url": "https://result/?signature=x",
        "nested": '{"accessPassword":"secret","node_errors":{"1":"TEST_problem"}}',
    }
    clean = sanitize(raw, ("TEST_SECRET",))
    assert "TEST_SECRET" not in json.dumps(clean)
    assert "signature" not in json.dumps(clean)
    assert clean["nested"]["node_errors"] == {"1": "TEST_problem"}


@pytest.mark.parametrize(
    "path,method,role",
    [
        ("/v2/runninghub/config", "GET", "readonly"),
        ("/v2/runninghub/config", "PUT", "admin"),
        ("/v2/runninghub/workflow/check", "POST", "admin"),
        ("/v2/runninghub/batches", "POST", "readwrite"),
        ("/v2/runninghub/jobs/TEST/resolve", "POST", "admin"),
        ("/v2/runninghub/jobs/TEST/recover", "POST", "readwrite"),
    ],
)
def test_explicit_route_roles(path, method, role):
    from tts_erp_v2.access._policy import required_role

    assert required_role(method, path).value == role
