"""tts_erp_v2.linkage — link-compute core and issue detectors.

Public surface:
- compute.process_move_collect_task(...)  : state-mutating; persists ORM rows.
- issues.*                                : pure functions returning dicts.
"""
from tts_erp_v2.linkage import (
    compute,  # noqa: F401
    issues,  # noqa: F401
)
