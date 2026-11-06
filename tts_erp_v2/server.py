"""Uvicorn entry point backed by the reloadable settings provider.

Bind address changes still require a service restart because the listening
socket is created once. Application/downstream settings remain reloadable in
the running process through :mod:`tts_erp_v2.runtime_settings`.
"""

from __future__ import annotations

from tts_erp_v2.runtime_settings import get_setting, validate_required_settings

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9877


def bind_settings() -> tuple[str, int]:
    """Return a validated host/port without exposing any secret settings."""
    host = (get_setting("TTS_ERP_HOST", DEFAULT_HOST) or "").strip() or DEFAULT_HOST
    raw_port = (get_setting("TTS_ERP_PORT", str(DEFAULT_PORT)) or "").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("TTS_ERP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("TTS_ERP_PORT must be between 1 and 65535")
    return host, port


def main() -> None:
    """Validate strong dependencies, then start the API server."""
    validate_required_settings("tts-erp API")
    host, port = bind_settings()

    import uvicorn

    uvicorn.run(
        "tts_erp_v2.app:app",
        host=host,
        port=port,
        proxy_headers=True,
        access_log=False,
    )


if __name__ == "__main__":
    main()
