"""Production HTTP client implementations.

TikTokHttpClient: wraps tts_signing.tiktok_request (HMAC-signed calls to TikTok).
"""
from __future__ import annotations

import urllib.error
from collections.abc import Callable
from typing import Any

from tts_signing import tiktok_request


class TikTokHttpClient:
    """Production HttpClient for TikTok Partner API.

    Every request gets a fresh access_token via get_access_token (the
    provider may rotate tokens between calls). Signing + auth headers
    are handled by tts_signing.tiktok_request.
    """

    def __init__(
        self,
        *,
        api_host: str,
        app_key: str,
        app_secret: str,
        get_access_token: Callable[[], str],
    ):
        self._api_host = api_host
        self._app_key = app_key
        self._app_secret = app_secret
        self._get_access_token = get_access_token

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        extra_params: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        return tiktok_request(
            method=method,
            api_host=self._api_host,
            path=path,
            access_token=self._get_access_token(),
            app_key=self._app_key,
            app_secret=self._app_secret,
            body=body,
            extra_params=extra_params,
            timeout=timeout,
        )
