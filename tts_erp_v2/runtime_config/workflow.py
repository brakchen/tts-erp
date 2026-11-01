"""Shared validation and review for runtime publication candidates.

Callers own the transaction and hold the item's row lock. Validation never
changes drafts or revisions; publication remains owned by repository.publish.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.config import RuntimeConfigItem, RuntimeConfigRevision
from tts_erp_v2.runtime_config.repository import (
    ensure_secret_references_exist,
    published_revision,
)
from tts_erp_v2.runtime_config.validation import (
    ConfigValidationError,
    validate_payload,
    validate_spu_deterioration_alert_runtime_mutation,
)


class ConfigVersionConflict(ValueError):
    """The operator reviewed a stale item version."""


class ConfigRevisionNotFound(LookupError):
    """A historical source does not belong to the selected configuration."""


class CandidateValidationError(ConfigValidationError):
    """A fixed public category plus the original detail for legacy callers."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class CandidateReview:
    has_changes: bool
    published_text: str | None
    candidate_text: str


def check_versions(
    item: RuntimeConfigItem,
    *,
    expected_draft_version: int,
    expected_published_version: int | None,
    check_published_version: bool = True,
) -> None:
    if expected_draft_version != item.draft_version:
        raise ConfigVersionConflict("draft version conflict; reload before publishing")
    if check_published_version and expected_published_version != item.published_version:
        raise ConfigVersionConflict("published version conflict; reload before publishing")


def review_candidate(
    session: Session,
    *,
    item: RuntimeConfigItem,
    payload: dict[str, Any],
    source_revision_version: int | None = None,
) -> CandidateReview:
    """Validate exactly what can be published, without persisting any changes.

    Secret locks are held only for this transaction. A preview grants no later
    publication guarantee; the final command repeats these checks under locks.
    A historical source is provenance, so a candidate may be edited after load.
    """
    try:
        _validate_finite_numbers(payload)
    except ConfigValidationError as exc:
        raise CandidateValidationError("runtime_number_invalid", str(exc)) from exc
    try:
        validate_payload(payload, item.json_schema)
    except ConfigValidationError as exc:
        raise CandidateValidationError("runtime_schema_invalid", str(exc)) from exc
    try:
        validate_spu_deterioration_alert_runtime_mutation(item.config_key, payload=payload)
    except ConfigValidationError as exc:
        raise CandidateValidationError("runtime_business_invalid", str(exc)) from exc
    try:
        ensure_secret_references_exist(session, payload)
    except (ConfigValidationError, KeyError) as exc:
        raise CandidateValidationError("runtime_secret_unavailable", str(exc)) from exc
    if source_revision_version is not None:
        source = session.execute(
            select(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key == item.config_key,
                RuntimeConfigRevision.version == source_revision_version,
            )
        ).scalar_one_or_none()
        if source is None:
            raise ConfigRevisionNotFound("runtime config revision not found")
        # Provenance checks retained sidecars, while business/schema rules apply
        # to the edited candidate. Old invalid content can therefore be repaired.
        try:
            validate_spu_deterioration_alert_runtime_mutation(
                item.config_key, rollout=source.rollout
            )
        except ConfigValidationError as exc:
            raise CandidateValidationError("runtime_source_rollout_invalid", str(exc)) from exc
    current = published_revision(session, item)
    try:
        published_text = canonical_json(current.payload) if current else None
        candidate_text = canonical_json(payload)
    except ConfigValidationError as exc:
        raise CandidateValidationError("runtime_number_invalid", str(exc)) from exc
    return CandidateReview(
        has_changes=current is None or not json_values_equal(current.payload, payload),
        published_text=published_text,
        candidate_text=candidate_text,
    )


def canonical_json(payload: dict[str, Any]) -> str:
    """Stable display text using the existing Python JSON numeric contract."""
    try:
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
    except ValueError as exc:
        raise ConfigValidationError("runtime config payload numbers must be finite") from exc


def _validate_finite_numbers(value: Any) -> None:
    """Reject parser overflow and non-JSON constants even without child schemas.

    Check floats alone: converting arbitrary-size JSON integers to float would
    lose precision or overflow and would change the existing integer contract.
    """
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigValidationError("runtime config payload numbers must be finite")
    if isinstance(value, dict):
        for child in value.values():
            _validate_finite_numbers(child)
    elif isinstance(value, list):
        for child in value:
            _validate_finite_numbers(child)


def json_values_equal(left: Any, right: Any) -> bool:
    """Ignore object order while preserving arrays, JSON types and large ints.

    Numeric int/float equality follows Python's existing parsed JSON contract;
    explicitly separate bool because Python otherwise considers True == 1.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            json_values_equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            json_values_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right
