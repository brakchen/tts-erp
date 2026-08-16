"""Production HTTP client implementations.

TikTokHttpClient: wraps tts_signing.tiktok_request (HMAC-signed calls to TikTok).
PlainHttpClient: plain urllib wrapper for internal services (oauth-receiver).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

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


class PlainHttpClient:
    """Plain HTTP client for internal services (oauth-receiver).

    No signing, no auth headers. Just GET/POST with JSON body.
    """

    def __init__(self, *, timeout: int = 10):
        self._timeout = timeout

    def request(
        self,
        method: str,
        url: str,
        *,
        body: dict | None = None,
        extra_params: dict[str, str] | None = None,  # unused for plain HTTP
        timeout: int | None = None,
    ) -> dict[str, Any]:
        # Append query string from extra_params
        if extra_params:
            from urllib.parse import urlencode
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urlencode(extra_params)}"

        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            method=method,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"}
            if data else {"Accept": "application/json"},
        )
        actual_timeout = timeout or self._timeout
        try:
            with urllib.request.urlopen(req, timeout=actual_timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {
                "_error": True,
                "_http_status": e.code,
                "_body": e.read().decode("utf-8", errors="replace")[:500],
            }
        except urllib.error.URLError as e:
            return {"_error": True, "_reason": f"URLError: {e.reason}"}
        except Exception as e:  # noqa: BLE001
            return {"_error": True, "_reason": f"{type(e).__name__}: {e}"}
