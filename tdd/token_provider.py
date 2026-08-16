"""Production TokenProvider implementations.

OAuthReceiverTokenProvider: HTTP-calls oauth-receiver /token/<id>?reveal=1
to get a fresh access_token + shop_cipher for a given shop.
"""
from __future__ import annotations

import urllib.parse

from domain import Creds, TokenError


class OAuthReceiverTokenProvider:
    """Fetches per-shop creds from oauth-receiver on every call.

    Why not cache? Tokens are short-lived and may rotate. We always
    fetch fresh. The HTTP call is cheap (oauth-receiver is local).
    """

    def __init__(self, *, base_url: str, http):
        self._base_url = base_url.rstrip("/")
        self._http = http  # PlainHttpClient instance

    def get(self, shop_id: str) -> Creds:
        url = f"{self._base_url}/token/{urllib.parse.quote(shop_id, safe='')}?reveal=1"
        resp = self._http.request("GET", url)

        if resp.get("_error"):
            raise TokenError(
                f"oauth-receiver call failed: {resp.get('_body') or resp.get('_reason')}",
                status=502,
            )

        access_token = resp.get("access_token")
        shop_cipher = resp.get("shop_cipher")
        if not access_token:
            raise TokenError("token response missing access_token", status=502)
        if not shop_cipher:
            raise TokenError("token response missing shop_cipher", status=502)

        return Creds(
            access_token=access_token,
            shop_cipher=shop_cipher,
            region=resp.get("shop_region") or "",
            shop_id=shop_id,
        )
