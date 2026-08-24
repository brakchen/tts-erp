"""Production TokenProvider implementations.

Two implementations live here:

* ``LocalTokenProvider`` (production, Wave 3+) — calls
  ``oauth_receiver_core.db_load_token`` in-process. After the oauth-
  receiver merge, this is the only provider the FastAPI app uses.
  No HTTP, no ``OAUTH_RECEIVER_URL`` env var.

* ``OAuthReceiverTokenProvider`` (legacy, Wave 2-3 transition) — HTTP-
  calls ``oauth-receiver /token/<id>?reveal=1`` to get a fresh
  ``access_token`` + ``shop_cipher``. Kept temporarily during the
  Wave 3 migration for any external scripts that import it; scheduled
  for removal in Slice 5.
"""
from __future__ import annotations

import urllib.parse

import oauth_receiver_core
from domain import Creds, TokenError


class LocalTokenProvider:
    """In-process token fetch from ``oauth_receiver_core``.

    Why in-process? After Wave 3 the oauth-receiver routes are mounted
    on the same FastAPI app as tts-erp. Going through HTTP would mean
    a self-loop socket (uvicorn accepting its own outbound request)
    and serialize us to whatever the HTTP layer is doing. Direct
    function calls are ~100x faster and let us propagate exceptions
    cleanly.

    Construction takes no arguments. There is no ``base_url``, no
    ``http`` client, no config — the contract is "you have the
    ``oauth_receiver_core`` module, you can use me".
    """

    def __init__(self) -> None:
        """Zero-arg constructor. Declared explicitly to override the
        inherited ``object.__init__(self, *args, **kwargs)`` signature
        so ``inspect.signature`` reports ``(self)`` — not ``(self, *args,
        **kwargs)``. The contract is tested in
        ``test_local_provider_constructor_takes_no_args``.
        """
        # no state — everything is fetched live from oauth_receiver_core
        return

    def get(self, shop_id: str) -> Creds:
        """Return ``Creds`` for ``shop_id`` or raise ``TokenError`` (404).

        The provider is hardcoded to ``"tiktok"`` because that's the
        only provider oauth_receiver_core currently supports. When
        Wave N adds a second provider (Google/Facebook), the
        ``provider`` arg can be threaded through here.
        """
        row = oauth_receiver_core.db_load_token(shop_id, provider="tiktok")
        if not row:
            raise TokenError(
                f"no token for shop_id={shop_id} provider=tiktok",
                status=404,
            )
        return Creds(
            access_token=row["access_token"],
            shop_cipher=row["shop_cipher"],
            region=row.get("shop_region") or "",
            shop_id=shop_id,
        )


class OAuthReceiverTokenProvider:
    """Fetches per-shop creds from oauth-receiver on every call.

    Why not cache? Tokens are short-lived and may rotate. We always
    fetch fresh. The HTTP call is cheap (oauth-receiver is local).

    DEPRECATED: use ``LocalTokenProvider`` instead. This class is kept
    only until Wave 3 Slice 5 deletes it.
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
