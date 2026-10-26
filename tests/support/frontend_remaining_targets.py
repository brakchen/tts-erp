"""Pure operands of BrowserFence's unpublished manual-cost/video-only slice.

Six other remaining-console domains are deliberately unsupported until N2.
No database, HTTP, provenance or independently usable permit authority lives here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

REMAINING_PAGES = frozenset({"/v2/pages/manual-costs", "/v2/pages/video-publish"})
STORE_PATH = "/__test__/remaining-console-v1/store"


@dataclass(frozen=True)
class RemainingTargets:
    marker: str
    shop_pks: frozenset[int] = frozenset()
    product_pks: frozenset[int] = frozenset()
    task_ids: frozenset[str] = frozenset()
    dates: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"TEST_[A-Za-z0-9_]{1,80}", self.marker):
            raise ValueError("P2 browser request refused")
        for values in (self.shop_pks, self.product_pks):
            if not isinstance(values, frozenset) or any(
                type(pk) is not int or pk <= 0 for pk in values
            ):
                raise ValueError("P2 browser request refused")
        if not isinstance(self.task_ids, frozenset) or any(
            str(UUID(value)) != value for value in self.task_ids
        ):
            raise ValueError("P2 browser request refused")
        if not isinstance(self.dates, frozenset) or any(
            date.fromisoformat(value).isoformat() != value for value in self.dates
        ):
            raise ValueError("P2 browser request refused")


@dataclass(frozen=True)
class ManualTarget:
    product_pk: int
    shop_pk: int
    external_id: str


@dataclass(frozen=True)
class TaskTarget:
    task_id: str
    client_id: str
    size: int
    filename: str
    caption: str
    stage: str



def manual_pk_operand(value: object) -> int | None:
    if type(value) is int:
        return value if 0 < value <= 2**63 - 1 else None
    if type(value) is not str:
        return None
    if not 1 <= len(value) <= 19 or not value.isascii():
        return None
    if re.fullmatch(r"[1-9][0-9]*", value) is None:
        return None
    result = int(value)
    return result if result <= 2**63 - 1 else None

def remaining_request(
    t: RemainingTargets,
    method: str,
    path: str,
    query: dict[str, list[str]],
    body: object,
) -> bool:
    """Schema branch only; canonical gate verifies current profile/SQL relations."""
    if method == "GET" and path in REMAINING_PAGES and not query:
        return True
    if path == STORE_PATH and method == "GET" and not query:
        return True
    if path in {STORE_PATH + "/pause", STORE_PATH + "/release"}:
        return method == "POST" and not query and body == {}
    if method == "GET":
        if not query and path in {
            "/v2/video-publish/config",
            "/v2/video-publish/devices",
            "/v2/video-publish/tasks/current",
            "/v2/video-publish/tasks",
        }:
            return True
        if path in {
            "/v2/commerce/channel-products",
            "/v2/reporting/missing-cost-products",
            "/v2/reporting/manual-costs",
        }:
            choices = {
                "shop_pk": {str(pk) for pk in t.shop_pks},
                "limit": {"25", "50", "100", "200"},
                "offset": {"0", "25", "50", "100", "200"},
                "q": {t.marker},
            }
            if path == "/v2/commerce/channel-products":
                choices.update(
                    {
                        "sort": {"status", "unit_cost", "created_at", "updated_at"},
                        "order": {"asc", "desc"},
                        "status": {"ACTIVATE", "DEACTIVATE", "DRAFT", "DELETED"},
                        "has_orders": {"true"},
                        "missing_manual_cost": {"true"},
                    }
                )
            return {
                "shop_pk",
                "limit",
                "offset",
            } <= query.keys() <= choices.keys() and all(
                len(values) == 1 and values[0] in choices[key]
                for key, values in query.items()
            )
        if not query and path in {
            f"/v2/commerce/channel-products/{pk}" for pk in t.product_pks
        }:
            return True
        if path in {"/v2/video-publish/tasks/" + task for task in t.task_ids}:
            return not query or query == {"includeDiagnostics": ["true"]}
    if query or not isinstance(body, dict):
        return False
    if method == "POST" and path == "/v2/reporting/manual-costs":
        if (
            not {"spu_id", "unit_cost", "currency"}
            <= body.keys()
            <= {
                "spu_id",
                "spu_pk",
                "shop_pk",
                "unit_cost",
                "currency",
                "valid_from",
                "note",
            }
        ):
            return False
        try:
            cost = Decimal(str(body["unit_cost"]))
        except (InvalidOperation, ValueError):
            return False
        return (
            isinstance(body["spu_id"], str)
            and cost.is_finite()
            and cost >= 0
            and body["currency"] in ("USD", "VND", "CNY")
            and all(
                manual_pk_operand(body[key]) is not None
                for key in ("spu_pk", "shop_pk")
                if key in body
            )
            and ("valid_from" not in body or body["valid_from"] in t.dates)
            and (
                "note" not in body
                or body["note"] is None
                or isinstance(body["note"], str)
                and body["note"] in {t.marker, t.marker + "_updated"}
            )
        )
    if method == "POST" and path == "/v2/video-publish/tasks":
        try:
            valid_client = (
                isinstance(body.get("clientRequestId"), str)
                and str(UUID(body["clientRequestId"])) == body["clientRequestId"]
            )
        except ValueError:
            return False
        return (
            {"clientRequestId", "filename", "contentType", "sizeBytes", "caption"}
            <= body.keys()
            <= {
                "clientRequestId",
                "filename",
                "contentType",
                "sizeBytes",
                "caption",
                "deviceSerial",
            }
            and valid_client
            and body["filename"] == t.marker + ".mp4"
            and body["caption"] == t.marker
            and body["contentType"] == "video/mp4"
            and type(body["sizeBytes"]) is int
            and 0 < body["sizeBytes"] <= 32 * 1024 * 1024
            and ("deviceSerial" not in body or body["deviceSerial"] == "TEST_device")
        )
    for task in t.task_ids:
        base = "/v2/video-publish/tasks/" + task
        if method == "POST" and path == base + "/upload-url":
            return body == {}
        if method == "POST" and path in {base + "/confirm-upload", base + "/cancel"}:
            return (
                set(body) == {"rowVersion"}
                and type(body["rowVersion"]) is int
                and body["rowVersion"] >= 0
            )
    return False
