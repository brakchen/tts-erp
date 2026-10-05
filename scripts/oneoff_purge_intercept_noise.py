#!/usr/bin/env python3
"""Purge intercept noise already covered by blacklist configs 329–363.

Only touches ``plugin.intercepted_requests``. Does not delete configs,
sessions, IM/ticket history, seller order/ads rows, or api16 business APIs.

Targets
-------
- MCS hosts (sgali-mcs / mcs-sg / mcs-va / maliva-mcs)
- seller ``/api/v1/common/region_domain``
- residual ``/monitor_web/*`` on mon.tiktokv.com / mon-va
- starling i18n, session-replay, CDN/static, SDK hosts
- seller SDK paths: ``/api/v1/bs/rt``, ``/ttwid/*``, ``/passport/*``,
  ``/api/v1/session_replay/*``, ``/oec_ads/shopping/v1/feelgood/*``
- api16 ``/api/v1/seller/feelgood/*``

USAGE
-----
    python3 scripts/oneoff_purge_intercept_noise.py
    ALLOW_PROD_DESTRUCTIVE=1 python3 scripts/oneoff_purge_intercept_noise.py --confirm
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import bindparam, text  # noqa: E402

from tts_erp_v2.api.deps import require_destructive_script_guard  # noqa: E402
from tts_erp_v2.db import get_engine, get_session_factory  # noqa: E402

load_dotenv()

_SCRIPT_NAME = "oneoff_purge_intercept_noise"

_NOISE_HOSTS = (
    "sgali-mcs.byteoversea.com",
    "mcs-sg.tiktokv.com",
    "mcs-va.tiktokv.com",
    "maliva-mcs.byteoversea.com",
    "starling-oversea.byteoversea.com",
    "starling-sg.tiktokv.com",
    "starling-sg.byteoversea.com",
    "starling.zijieapi.com",
    "gec-bytereplay.tiktokv-row.com",
    "tsr16-normal-useast1a.tiktok.com",
    "lf-gs-frontend-cn.fanchenstatic.com",
    "sf-gs-frontend-sg.fanchenstatic.com",
    "sf16-website.neutral.ttwstatic.com",
    "sf16-website-login.neutral.ttwstatic.com",
    "sf16-tcc-tos-sg.byteoversea.com",
    "sf-oec-config-center.tiktokcdn.com",
    "sf-tcc-config.tiktokcdn.com",
    "mssdk-sg.tiktok.com",
    "web-sg.tiktok.com",
    "vmweb-sg.byteoversea.com",
    "libraweb-sg.tiktok.com",
    "libraweb.tiktok.com",
    "vcs-sg.tiktokv.com",
    "vcs-sg.byteoversea.com",
    "tnc16-alisg.byteoversea.com",
    "api-verification.tiktokshop.com",
)
_MON_HOSTS = ("mon.tiktokv.com", "mon-va.byteoversea.com")
_SELLER = "seller.tiktokshopglobalselling.com"
_API16 = "api16-normal-sg.tiktokshopglobalselling.com"

_WHERE = """
(
    endpoint_host IN :noise_hosts
    OR (
        endpoint_host = :seller
        AND (
            endpoint_path = '/api/v1/common/region_domain'
            OR endpoint_path = '/api/v1/bs/rt'
            OR endpoint_path LIKE '/ttwid/%'
            OR endpoint_path LIKE '/passport/%'
            OR endpoint_path LIKE '/api/v1/session_replay/%'
            OR endpoint_path LIKE '/oec_ads/shopping/v1/feelgood/%'
        )
    )
    OR (
        endpoint_host IN :mon_hosts
        AND endpoint_path LIKE '/monitor_web/%'
    )
    OR (
        endpoint_host = :api16
        AND endpoint_path LIKE '/api/v1/seller/feelgood/%'
    )
)
"""

_COUNT_SQL = text(
    f"""
    SELECT endpoint_host, endpoint_path, count(*) AS n
    FROM plugin.intercepted_requests
    WHERE {_WHERE}
    GROUP BY 1, 2
    ORDER BY n DESC
    """
).bindparams(
    bindparam("noise_hosts", expanding=True),
    bindparam("mon_hosts", expanding=True),
)
_TOTAL_SQL = text(
    f"""
    SELECT count(*)
    FROM plugin.intercepted_requests
    WHERE {_WHERE}
    """
).bindparams(
    bindparam("noise_hosts", expanding=True),
    bindparam("mon_hosts", expanding=True),
)
_DELETE_SQL = text(
    f"""
    DELETE FROM plugin.intercepted_requests
    WHERE id IN (
        SELECT id
        FROM plugin.intercepted_requests
        WHERE {_WHERE}
        LIMIT :batch
    )
    """
).bindparams(
    bindparam("noise_hosts", expanding=True),
    bindparam("mon_hosts", expanding=True),
)


def _params(*, batch: int | None = None) -> dict[str, object]:
    params: dict[str, object] = {
        "noise_hosts": list(_NOISE_HOSTS),
        "mon_hosts": list(_MON_HOSTS),
        "seller": _SELLER,
        "api16": _API16,
    }
    if batch is not None:
        params["batch"] = batch
    return params


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Purge intercept MCS/region_domain/SDK/CDN/monitor_web rows"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete (without this flag: dry-run)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10_000,
        help="Rows per DELETE batch (default: 10000)",
    )
    args = parser.parse_args()
    if args.batch_size < 1:
        sys.exit("--batch-size must be >= 1")

    require_destructive_script_guard(
        script_name=_SCRIPT_NAME,
        confirmation=args.confirm,
        dangerous=args.confirm,
    )

    engine = get_engine()
    Session = get_session_factory(engine)
    sess = Session()
    try:
        rows = list(sess.execute(_COUNT_SQL, _params()))
        total = int(sess.execute(_TOTAL_SQL, _params()).scalar() or 0)
        print("table=plugin.intercepted_requests")
        print("breakdown (top 30):")
        for host, path, n in rows[:30]:
            print(f"  {host} {path}  {n:,}")
        if len(rows) > 30:
            print(f"  ... {len(rows) - 30} more path groups")
        print(f"target rows: {total:,}")

        if total == 0:
            print("Nothing to purge.")
            return
        if not args.confirm:
            print("DRY-RUN — pass --confirm to execute.")
            return

        deleted = 0
        while True:
            result = sess.execute(_DELETE_SQL, _params(batch=args.batch_size))
            batch_deleted = int(result.rowcount or 0)
            sess.commit()
            deleted += batch_deleted
            print(f"  deleted {deleted:,} / {total:,} ...")
            if batch_deleted < args.batch_size:
                break

        leftover = int(sess.execute(_TOTAL_SQL, _params()).scalar() or 0)
        print(f"\nDone. Purged {deleted:,} rows; leftover={leftover:,}.")
        if leftover:
            sys.exit(1)
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


if __name__ == "__main__":
    main()
