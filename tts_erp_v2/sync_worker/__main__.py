"""``python -m tts_erp_v2.sync_worker`` → :func:`main.main`."""

from __future__ import annotations

import sys

from tts_erp_v2.sync_worker.main import main

if __name__ == "__main__":
    sys.exit(main())
