"""Migration scripts for tts_erp v1 → v2 (refactor Wave 2, Lane F).

Reads from the legacy `public.*` mirror tables (read-only) and from the
separate `oauth_receiver.oauth_tokens` table on the same PG instance,
and writes into the new nine-schema target layout built by Lane 0.

All scripts in this package are idempotent (use ``ON CONFLICT DO UPDATE``)
and accept ``--dry-run`` so they can be replayed safely.
"""
