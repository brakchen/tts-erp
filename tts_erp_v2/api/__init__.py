"""tts_erp_v2.api package: v2 routers + pydantic schemas.

Routers:
- commerce.py    — store/SKU/order/order-line reads (readonly)
- linkage.py     — link queries + link_overrides write + link_issues queue
- reporting.py   — cost/profit/coverage reports + manual-costs POST
- pages.py       — server-rendered manual-costs HTML page (no SPA framework)
- spu_images.py  — SPU image upload-url / confirm / list / delete (MinIO)
"""

from __future__ import annotations
