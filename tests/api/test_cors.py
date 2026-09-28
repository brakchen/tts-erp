"""CORS contract for browser-based extension clients."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tts_erp_v2.app import DEFAULT_CORS_ORIGINS, build_app


EXTENSION_ORIGIN = DEFAULT_CORS_ORIGINS[0]


def test_production_extension_preflight_allows_sync_headers(monkeypatch) -> None:
    monkeypatch.delenv("TTS_ERP_CORS_ALLOW_ORIGINS", raising=False)
    client = TestClient(build_app())

    response = client.options(
        "/v2/analytics/sync/dumps",
        headers={
            "Origin": EXTENSION_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,x-api-key,x-request-id,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    assert "x-request-id" in response.headers["access-control-allow-headers"].lower()


def test_unconfigured_browser_origin_remains_denied(monkeypatch) -> None:
    monkeypatch.delenv("TTS_ERP_CORS_ALLOW_ORIGINS", raising=False)
    client = TestClient(build_app())

    response = client.options(
        "/v2/analytics/sync/dumps",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,x-request-id",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
