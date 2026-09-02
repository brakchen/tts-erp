"""API demo for the Chrome extension ↔ tts-erp ``analytics_sync`` protocol.

This script is a runnable, copy-pasteable walkthrough of the upload cycle
that the ``tk-adv-cost-monitor`` Chrome extension uses against
``POST /v2/analytics/sync/{cursor,batches}``.  It is written for the
upstream maintainers as a "show me exactly how to call this and what the
server replies with" reference — open it, read top-to-bottom, and you
have everything you need to wire the production client.

How to read this script
-----------------------

The module is split into three numbered steps that match the daily-job
flow in ``tech-doc/analytics/plugin-integration.md`` §4:

  Step 1.  GET /v2/analytics/sync/cursor          — discover what to sync
  Step 2.  POST /v2/analytics/sync/batches        — upload one record (happy)
  Step 3.  POST /v2/analytics/sync/batches        — upload a BAD record

Each step prints:

  - The exact ``curl`` command (so you can paste it into a terminal)
  - The exact Python code (so you can copy the snippet into the plugin)
  - The exact JSON the server replies with (with placeholders for fields
    that vary per request: idempotencyKey, requestId, sellerId, etc.)
  - Field-by-field notes where the protocol has gotchas
    (``capturedAt`` needs timezone, ``idempotencyKey`` is recomputed by
    the server, ``page_count`` / ``pageCount`` parsing, etc.)

Running it
----------

Default mode is ``--dry-run`` — the script prints what it WOULD send
and what the server WOULD reply with, based on a frozen snapshot of
expected response shapes.  Use this when you want to read the protocol
without a running tts-erp instance:

    .venv/bin/python scripts/demo_analytics_sync_client.py

Use ``--live`` to actually call the server (must be running on
``$TTS_ERP_BASE_URL``, default ``http://127.0.0.1:9877``):

    TTS_ERP_BASE_URL=http://127.0.0.1:9877 \\
    TTS_ERP_SERVICE_KEY=ttserp_rw_demo_key \\
    .venv/bin/python scripts/demo_analytics_sync_client.py --live

The ``--live`` mode is what you reach for after a code change to verify
the wire shape; ``--dry-run`` is what you reach for to onboard a new
upstream maintainer.

What changed for v2 (2026-08-31)
---------------------------------

The 400 / ``SCHEMA_INVALID`` response now carries a structured
``errors[]`` array in addition to the free-form ``message`` string.  The
plugin should read ``errors`` first and only fall back to ``message``
for human display:

.. code-block:: json

    {
      "code": "SCHEMA_INVALID",
      "message": "1 validation error for BatchRequest\\nrecords.0.capturedAt\\n  Value error, ...",
      "requestId": "req-prod-...",
      "retryable": false,
      "errors": [
        {"loc": ["records", 0, "capturedAt"],
         "msg":  "Value error, capturedAt must include a timezone ...",
         "type": "value_error"}
      ]
    }

The shape is exactly Pydantic's ``ValidationError.errors()`` with
``input`` / ``ctx`` / ``url`` stripped (those can echo the offending
field value or internal constraint details).  ``loc`` keeps its native
Python types — int for array indices, str for field names — so the
plugin can branch on ``loc[1] === 0`` to mean "first record" without
parsing strings.  ``MALFORMED_JSON`` and ``UNSUPPORTED_PROTOCOL_VERSION``
do NOT carry ``errors[]`` — the field is reserved for ``SCHEMA_INVALID``.

The audit table (``analytics_audit_log``) gained an ``error_message``
column at the same time.  The same 500-char sanitized Pydantic/JSON
detail that goes to stderr is now persisted there, so ops can run
``SELECT ... WHERE error_message LIKE '%capturedAt%'`` after the stderr
log rotates.  See ``analytics_sync/tech-doc/plugin-integration.md`` §6
for the HTTP error table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# ─── Configuration ──────────────────────────────────────────────────────

BASE_URL = os.environ.get("TTS_ERP_BASE_URL", "http://127.0.0.1:9877")
# Read the service key directly from .env so the demo can be run against
# a local dev box without exporting env vars by hand.  Replace with the
# real key via ``export TTS_ERP_SERVICE_KEY=...`` for production demos.
SERVICE_KEY = os.environ.get(
    "TTS_ERP_SERVICE_KEY", "ttserp_rw_DEMO_PLACEHOLDER_REPLACE_ME"
)
SELLER_ID = os.environ.get("TTS_ERP_DEMO_SELLER", "TEST_seller-1")
ADVERTISER_ID = os.environ.get("TTS_ERP_DEMO_ADV", "TEST_adv-1")
SHOP_NAME = os.environ.get("TTS_ERP_DEMO_SHOP", "demo-shop")
CAMPAIGN_ID = os.environ.get("TTS_ERP_DEMO_CAMPAIGN", "TEST_campaign-1")
DEMO_DAY = os.environ.get("TTS_ERP_DEMO_DAY", "2026-08-30")
PROTOCOL_VERSION = 2

STORAGE_KEYS = ("productAnalyses", "sessionAnalyses", "campaignChangeLogs")


# ─── Helpers ────────────────────────────────────────────────────────────


def section(title: str, body: str) -> None:
    """Print a labelled section with a fixed-width rule for readability."""
    print()
    print("─" * 78)
    print(f"  {title}")
    print("─" * 78)
    print(textwrap.indent(body, "  "))


def print_curl(method: str, url: str, body: dict[str, Any] | None = None) -> None:
    """Print a curl command the upstream maintainer can paste verbatim.

    Tokens / IDs are REDACTED in the printed form (``ttserp_rw_XXXX…``).
    No real credentials are written to the demo's stdout.
    """
    safe_key = SERVICE_KEY[:12] + "…" if len(SERVICE_KEY) > 12 else SERVICE_KEY
    pieces = [
        "curl",
        "-sS",  # silent + show errors
        "-X",
        method,
        f'  -H "Authorization: Bearer {safe_key}"',
        '  -H "Content-Type: application/json"',
        f'  -H "X-Protocol-Version: {PROTOCOL_VERSION}"',
    ]
    if body is not None:
        # Extract first so the f-string substitution doesn't have to
        # embed escaped inner double-quotes (which the parser would
        # treat as the outer-fstring terminator).
        request_id = str(body.get("requestId", "req-DEMO-…"))
        pieces.append(f"  -H 'X-Request-Id: {request_id}'")
    pieces.append(f'  "{url}"')
    if body is not None:
        # Extract JSON body to a local first so the f-string substitution
        # doesn't have to inline ``json.dumps(...)`` (ruff B905 style +
        # avoids embedding long payloads in the format spec).
        body_json = json.dumps(body, separators=(",", ":"))
        pieces.append(f"  -d '{body_json}'")
    section("curl", "\n".join(pieces))


def _safe_urlopen(req: urllib.request.Request, *, timeout: float = 10) -> str:
    """Validate scheme + call ``urlopen`` with a tightened exception scope.

    ``BASE_URL`` comes from the ``TTS_ERP_BASE_URL`` env var (operator-
    controlled), but we still validate the scheme is http/https before
    constructing a Request so a misconfigured env var can't trigger
    ``file://`` or ``ftp://`` against the host.  Returns the decoded
    response body.  Raises ``HTTPError`` / ``URLError`` untouched for
    the caller to handle — we don't swallow exceptions here.
    """
    parsed = urllib.parse.urlparse(req.full_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"TTS_ERP_BASE_URL must be http(s); got scheme {parsed.scheme!r}"
        )
    # noqa: S310 — scheme is allowlisted to http/https three lines above.
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8")


# ─── Step 1 · Idempotency key ──────────────────────────────────────────


def compute_idempotency_key(
    *,
    seller_id: str,
    advertiser_id: str,
    storage_key: str,
    campaign_id: str,
    day: str,
    page: int,
) -> str:
    """sha256 hex of the canonical 6-field record identity.

    Must match ``tts_erp_v2.analytics.domain.compute_idempotency_key`` byte-for-
    byte; the server recomputes this on every received record and rejects
    any client-sent ``idempotencyKey`` that doesn't match.  See
    ``tech-doc/analytics/plugin-integration.md`` §3 for the rules.

    Locked reference vector (computed against the production server):

        >>> compute_idempotency_key(
        ...     seller_id="seller-1", advertiser_id="adv-1",
        ...     storage_key="productAnalyses", campaign_id="campaign-1",
        ...     day="2026-08-23", page=1,
        ... )
        '73b716cce7f8b2c4220b1be3e5ab6327c3a963eaf424af84412402ef8607dae3'
    """
    try:
        page_int = int(page)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"page must be coercible to int (got {page!r}); see protocol §2"
        ) from exc
    payload = json.dumps(
        {
            "sellerId": seller_id.strip(),
            "advertiserId": advertiser_id.strip(),
            "storageKey": storage_key,
            "campaignId": campaign_id.strip(),
            "day": day,
            "page": page_int,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def demo_idempotency_key() -> str:
    section(
        "Step 1 · Compute the idempotency key",
        textwrap.dedent(
            f"""\
            The server recomputes this on every record.  If the client's
            value doesn't match, the record is rejected with the per-record
            code ``SCHEMA_INVALID`` and ``retryable: false`` — treat as a
            programming bug, not a transient error.

            For demo record: sellerId={SELLER_ID}, advertiserId={ADVERTISER_ID},
            storageKey=productAnalyses, campaignId={CAMPAIGN_ID}, day={DEMO_DAY}, page=1
            """
        ),
    )
    key = compute_idempotency_key(
        seller_id=SELLER_ID,
        advertiser_id=ADVERTISER_ID,
        storage_key="productAnalyses",
        campaign_id=CAMPAIGN_ID,
        day=DEMO_DAY,
        page=1,
    )
    print(f"  idempotencyKey = {key}")
    print(f"  length         = {len(key)} chars  (must be exactly 64 lowercase hex)")
    return key


# ─── Step 2 · GET cursor ───────────────────────────────────────────────


def step_cursor_dry_run() -> dict[str, Any]:
    """Print what step 2 would send + receive, no live HTTP."""
    url = (
        f"{BASE_URL}/v2/analytics/sync/cursor"
        f"?sellerId={SELLER_ID}&advertiserId={ADVERTISER_ID}"
        f"&storageKey=productAnalyses&campaignId={CAMPAIGN_ID}"
    )
    print_curl("GET", url)

    expected = {
        "code": 0,
        "requestId": "req-DEMO-…",
        "data": {
            "timezone": "Asia/Shanghai",
            "items": [
                {
                    "sellerId": SELLER_ID,
                    "advertiserId": ADVERTISER_ID,
                    "storageKey": "productAnalyses",
                    "campaignId": CAMPAIGN_ID,
                    "latestCompletedDay": None,
                    "nextRequiredDay": DEMO_DAY,
                },
            ],
            "nextCursor": None,
        },
    }
    section("expected 200 response", json.dumps(expected, indent=2, ensure_ascii=False))
    section(
        "field notes",
        textwrap.dedent(
            """\
            • ``nextRequiredDay`` is server-authoritative — never substitute
              your own "query recent N days" heuristic.
            • ``latestCompletedDay`` is diagnostic only; the plugin acts on
              ``nextRequiredDay``.
            • For a fresh account ``latestCompletedDay`` is null and
              ``nextRequiredDay`` is ``today_in_shop_tz - 30 days`` (configurable
              via ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS).
            • On 403 SCOPE_DENIED the token doesn't cover this scope; rotate
              via ``python3 api_keys.py rotate --prefix <prefix>``.
            """
        ),
    )
    return expected


def step_cursor_live() -> dict[str, Any]:
    """Make the actual GET cursor call and return the parsed JSON."""
    url = (
        f"{BASE_URL}/v2/analytics/sync/cursor"
        f"?sellerId={SELLER_ID}&advertiserId={ADVERTISER_ID}"
        f"&storageKey=productAnalyses&campaignId={CAMPAIGN_ID}"
    )
    req = urllib.request.Request(url, method="GET")  # noqa: S310  (scheme allowlisted in _safe_urlopen)
    req.add_header("Authorization", f"Bearer {SERVICE_KEY}")
    req.add_header("X-Protocol-Version", str(PROTOCOL_VERSION))
    try:
        raw = _safe_urlopen(req, timeout=10)
        body = json.loads(raw)
    except urllib.error.URLError as exc:
        raise SystemExit(f"live cursor call failed: {exc.reason}") from exc
    section("200 response (live)", json.dumps(body, indent=2, ensure_ascii=False))
    return body


# ─── Step 3 · POST batches (happy path) ─────────────────────────────────


def build_demo_record(
    *, idempotency_key: str, expected_page_count: int = 3
) -> dict[str, Any]:
    """One canonical ``RecordIn`` payload for a 3-page product analysis day.

    ``response`` is the literal JSON TikTok returned for this page; the
    demo shortens it to ``{"data": []}`` for readability but the field
    is opaque to the server (it stores it as-is into ``analytics_records
    .response_data`` JSONB).  The plugin should send the full body.
    """
    return {
        "idempotencyKey": idempotency_key,
        "sourceRecordId": "11111111-1111-1111-1111-111111111111",
        "storageKey": "productAnalyses",
        "campaignId": CAMPAIGN_ID,
        "day": DEMO_DAY,
        "page": 1,
        "expectedPageCount": expected_page_count,
        "endpoint": "/oec_ads/shopping/v1/oec/stat/post_product_list",
        "method": "POST",
        "requestBody": {
            "campaign_id": CAMPAIGN_ID,
            "page": 1,
            "start_time": DEMO_DAY,
            "end_time": DEMO_DAY,
        },
        "response": {"data": [{"product_id": "TEST_product-a"}]},
        "source": "background_poll",
        "capturedAt": "2026-08-30T18:43:00.000Z",
        "schemaVersion": 2,
    }


def step_batch_happy_dry_run(idempotency_key: str) -> dict[str, Any]:
    """Print what step 3 would send + receive, no live HTTP."""
    record = build_demo_record(idempotency_key=idempotency_key)
    body = {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": "req-DEMO-batch-001",
        "scope": {
            "sellerId": SELLER_ID,
            "advertiserId": ADVERTISER_ID,
            "shopName": SHOP_NAME,
        },
        "records": [record],
    }
    print_curl("POST", f"{BASE_URL}/v2/analytics/sync/batches", body=body)

    expected = {
        "code": 0,
        "requestId": "req-DEMO-batch-001",
        "data": {
            "accepted": [
                {"idempotencyKey": idempotency_key, "status": "inserted"},
            ],
            "rejected": [],
        },
    }
    section("expected 200 response", json.dumps(expected, indent=2, ensure_ascii=False))
    section(
        "field notes",
        textwrap.dedent(
            """\
            • Both ``inserted`` and ``duplicate`` are successes — mark the
              local record ``remoteSyncStatus = "synced"`` on either.
            • ``rejected[*].retryable = false`` → DO NOT retry unchanged;
              surface the code (PAGE_COUNT_CONFLICT, RESPONSE_TOO_LARGE,
              LOCAL_RECORD_INVALID, …) in the operator's diagnostic UI.
            • Max 100 records per request; max 2 MB body (413 if exceeded).
              Plugin splits on the upstream side.
            • Every record's ``capturedAt`` MUST include timezone (``Z`` /
              ``+HH:MM``); the Pydantic model rejects naive datetimes.
            """
        ),
    )
    return expected


# ─── Step 4 · POST batches (BAD record → new structured errors[]) ──────


def step_batch_bad_dry_run() -> dict[str, Any]:
    """Show how a malformed batch surfaces in the new ``errors[]`` shape.

    Three deliberate problems in one batch:
      • record 0: ``capturedAt`` missing timezone       → value_error
      • record 1: ``storageKey`` is not in the allowlist → enum
      • record 2: ``page`` is 0 (must be >= 1)           → greater_than_equal
    """
    bad_records = [
        # record 0: naive datetime
        {
            **build_demo_record(idempotency_key="a" * 64, expected_page_count=1),
            "capturedAt": "2026-08-30T18:43:00",  # no Z / +00:00
        },
        # record 1: bad enum
        {
            **build_demo_record(idempotency_key="b" * 64, expected_page_count=1),
            "storageKey": "WRONG",
        },
        # record 2: negative page
        {
            **build_demo_record(idempotency_key="c" * 64, expected_page_count=1),
            "page": 0,
        },
    ]
    body = {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": "req-DEMO-batch-bad",
        "scope": {
            "sellerId": SELLER_ID,
            "advertiserId": ADVERTISER_ID,
            "shopName": SHOP_NAME,
        },
        "records": bad_records,
    }
    print_curl("POST", f"{BASE_URL}/v2/analytics/sync/batches", body=body)

    expected = {
        "code": "SCHEMA_INVALID",
        "message": "3 validation errors for BatchRequest\n"
        "records.0.capturedAt\n"
        "  Value error, capturedAt must include a timezone ...\n"
        "records.1.storageKey\n"
        "  Input should be 'productAnalyses', 'sessionAnalyses' "
        "or 'campaignChangeLogs' ...\n"
        "records.2.page\n"
        "  Input should be greater than or equal to 1 ...",
        "requestId": "req-DEMO-batch-bad",
        "retryable": False,
        "errors": [
            {
                "loc": ["records", 0, "capturedAt"],
                "msg": "Value error, capturedAt must include a timezone "
                "(use ISO-8601 with 'Z' or '+00:00')",
                "type": "value_error",
            },
            {
                "loc": ["records", 1, "storageKey"],
                "msg": "Input should be 'productAnalyses', 'sessionAnalyses' "
                "or 'campaignChangeLogs'",
                "type": "enum",
            },
            {
                "loc": ["records", 2, "page"],
                "msg": "Input should be greater than or equal to 1",
                "type": "greater_than_equal",
            },
        ],
    }
    section("expected 400 response", json.dumps(expected, indent=2, ensure_ascii=False))
    section(
        "field notes (added 2026-08-31)",
        textwrap.dedent(
            """\
            • Read ``errors[]`` first — it gives you the failing field path
              as a typed list (``loc[1]`` is the record index as ``int``,
              not the string ``"0"``).  Plugin should branch on it.
            • The free-form ``message`` is still there for human display
              and for backwards compatibility with older plugin builds.
            • ``input`` / ``ctx`` / ``url`` are intentionally stripped from
              ``errors[]`` — they can echo the offending field value or
              leak internal constraint details.  The redaction policy
              matches ``tts_erp_v2/extension/storage.py::SENSITIVE_*``.
            • ``MALFORMED_JSON`` and ``UNSUPPORTED_PROTOCOL_VERSION`` do
              NOT carry ``errors[]`` — that field is reserved for
              ``SCHEMA_INVALID``.  Don't assume it's present.
            • The same sanitized Pydantic detail is persisted in
              ``analytics_audit_log.error_message`` (added in the same
              commit), so ops can ``SELECT ... WHERE error_message LIKE
              '%capturedAt%'`` after the stderr log rotates.
            """
        ),
    )
    return expected


# ─── Optional: live HTTP ───────────────────────────────────────────────


def step_batch_live(idempotency_key: str) -> dict[str, Any]:
    """POST a real happy-path batch to the running tts-erp."""
    body = {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": f"req-demo-{hashlib.sha256(os.urandom(8)).hexdigest()[:12]}",
        "scope": {
            "sellerId": SELLER_ID,
            "advertiserId": ADVERTISER_ID,
            "shopName": SHOP_NAME,
        },
        "records": [build_demo_record(idempotency_key=idempotency_key)],
    }
    req = urllib.request.Request(  # noqa: S310  (scheme allowlisted in _safe_urlopen)
        f"{BASE_URL}/v2/analytics/sync/batches",
        method="POST",
        data=json.dumps(body).encode("utf-8"),
    )
    req.add_header("Authorization", f"Bearer {SERVICE_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Protocol-Version", str(PROTOCOL_VERSION))
    req.add_header("X-Request-Id", body["requestId"])
    try:
        raw = _safe_urlopen(req, timeout=10)
        section("live HTTP 200", raw)
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        section(f"live HTTP {exc.code}", text)
        return json.loads(text) if text else {"_http_error": exc.code}


# ─── CLI ───────────────────────────────────────────────────────────────


@dataclass
class CliArgs:
    live: bool = False
    only: list[str] = field(default_factory=list)


def parse_args(argv: list[str]) -> CliArgs:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Make actual HTTP calls to $TTS_ERP_BASE_URL "
        "(requires a running tts-erp + a real SERVICE_KEY).",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=("key", "cursor", "happy", "bad"),
        help="Restrict the demo to a subset of steps.",
    )
    parsed = parser.parse_args(argv)
    return CliArgs(live=parsed.live, only=parsed.only or [])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    mode = "LIVE" if args.live else "DRY-RUN"
    section(
        f"tts-erp analytics ingest API demo · mode={mode} · base={BASE_URL}",
        textwrap.dedent(
            f"""\
            SELLER_ID     = {SELLER_ID}        (TEST_ prefix → auto-cleaned by
                                              the test suite conftest)
            ADVERTISER_ID = {ADVERTISER_ID}
            CAMPAIGN_ID   = {CAMPAIGN_ID}
            DEMO_DAY      = {DEMO_DAY}
            PROTOCOL_VER  = {PROTOCOL_VERSION}

            Dry-run mode prints requests + the canonical expected response
            shape (placeholders where per-request fields vary).  Use
            ``--live`` to actually call {BASE_URL}.
            """
        ),
    )

    show_all = not args.only

    # Step 1: idempotency key
    if show_all or "key" in args.only:
        key = demo_idempotency_key()
    else:
        # Even when only running one step, the key is needed downstream.
        key = compute_idempotency_key(
            seller_id=SELLER_ID,
            advertiser_id=ADVERTISER_ID,
            storage_key="productAnalyses",
            campaign_id=CAMPAIGN_ID,
            day=DEMO_DAY,
            page=1,
        )

    # Step 2: cursor
    if show_all or "cursor" in args.only:
        section(
            "Step 2 · GET /v2/analytics/sync/cursor",
            "Discover what days still need collecting for this campaign.",
        )
        if args.live:
            step_cursor_live()
        else:
            step_cursor_dry_run()

    # Step 3: happy-path batch
    if show_all or "happy" in args.only:
        section(
            "Step 3 · POST /v2/analytics/sync/batches — happy path",
            "Upload one canonical record.  Server validates, recomputes the "
            "idempotency key, inserts if new (returns ``inserted``) or skips "
            "if seen (returns ``duplicate``).",
        )
        if args.live:
            step_batch_live(key)
        else:
            step_batch_happy_dry_run(key)

    # Step 4: bad batch → showcases new structured errors[]
    if show_all or "bad" in args.only:
        section(
            "Step 4 · POST /v2/analytics/sync/batches — bad record",
            "Three deliberate problems in one batch so you can see how each "
            "failing field shows up in the structured ``errors[]`` array.",
        )
        step_batch_bad_dry_run()

    section(
        "where to go next",
        textwrap.dedent(
            """\
• tech-doc/analytics/plugin-integration.md — full protocol spec
            • tech-doc/analytics/analytics-sync.md   — endpoint reference + curl examples
            • analytics_sync/tech-doc/compatibility.md      — v1 ↔ v2 policy + rollout checklist
            • analytics_sync/tech-doc/openapi.yaml         — machine-readable schema
            • chrome-plugins/ads-data-sync/src/core/analytics-sync-v2.ts  — the production TS client
            • tech-doc/external-api.md  (in this repo)      — v2 stable API contract for dashboards / BI

            Re-run with ``--live`` against a real server, or ``--only key cursor``
            to read a specific subsection.
            """
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
