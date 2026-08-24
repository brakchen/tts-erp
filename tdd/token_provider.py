"""Production TokenProvider.

``LocalTokenProvider`` (Wave 3+) — calls ``oauth_receiver_core.db_load_token``
in-process. After the oauth-receiver merge, this is the only provider
the FastAPI app uses. No HTTP, no legacy env-var config, no network
round-trip.
"""
from __future__ import annotations

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
