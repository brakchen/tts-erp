"""Shared parsing rules for bounded API query parameters."""

from __future__ import annotations

SPU_IDS_MAX_ITEMS = 100
SPU_ID_MAX_LENGTH = 128


def parse_spu_ids(raw: str | None) -> tuple[str, ...] | None:
    """Parse an English/Chinese-comma SPU list into ordered unique ids.

    ``None`` and an empty string mean that no SPU scope is active. A non-empty
    value containing only separators is rejected so a malformed filter can
    never silently widen into an all-SPU query.
    """

    if raw is None or not raw.strip():
        return None

    values: list[str] = []
    seen: set[str] = set()
    for part in raw.replace("，", ",").split(","):
        spu_id = part.strip()
        if not spu_id or spu_id in seen:
            continue
        if len(spu_id) > SPU_ID_MAX_LENGTH:
            raise ValueError(
                f"each spu_id must contain at most {SPU_ID_MAX_LENGTH} characters"
            )
        seen.add(spu_id)
        values.append(spu_id)

    if not values:
        raise ValueError("spu_ids must contain at least one SPU id")
    if len(values) > SPU_IDS_MAX_ITEMS:
        raise ValueError(f"spu_ids must contain at most {SPU_IDS_MAX_ITEMS} unique ids")
    return tuple(values)
