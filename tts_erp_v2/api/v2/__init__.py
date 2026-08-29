"""tts_erp_v2.api.v2: per-domain routers.

This package re-exports each router so that
``from tts_erp_v2.api.v2 import commerce, linkage, pages, reporting``
resolves each symbol to its submodule. Submodules themselves remain
importable as ``tts_erp_v2.api.v2.commerce`` etc.
"""

from __future__ import annotations

from tts_erp_v2.api.v2 import commerce, linkage, pages, reporting

__all__ = ["commerce", "linkage", "pages", "reporting"]
