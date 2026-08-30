"""TDD tests for api_keys.py — pins the V3 schema contract.

Root cause the tests are written against:
    api_keys.py uses unqualified table names (``INSERT INTO api_keys``,
    ``FROM api_keys``, ``UPDATE api_keys``). With
    ``search_path = "$user", public`` PostgreSQL resolves these to
    ``public.api_keys`` — the V1 legacy table — and NOT to
    ``security.api_keys``, which is what the v2 app reads from for
    login. Every key that api_keys.py "successfully" creates has
    been landing in a dead table since the V2 → V3 schema split.
    The exit code 0 is a lie.

After this commit the CLI must write to ``security.api_keys`` and
read from it. The tests below pin that contract end-to-end:

* create → row in security.api_keys (not public)
* the new key authenticates against POST /v2/auth/login
* list reads from security.api_keys
* revoke flips status='disabled' in security.api_keys
* --scopes / --expires-days are accepted (back-compat) but the
  columns are not stored (V3 dropped them)

All test data uses ``name`` values starting with ``TEST_`` so the
``_isolate_state`` autouse in tests_v2/api/conftest.py wipes them
on teardown without touching the user's real keys.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import psycopg
import pytest

REPO = Path("/home/schan/tts-erp")
sys.path.insert(0, str(REPO))
from scripts._db_url import normalize_db_url  # noqa: E402


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(REPO / ".venv" / "bin" / "python"),
         str(REPO / "api_keys.py"),
         *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _key_from_create_stdout(stdout: str) -> str:
    m = re.search(r"API KEY \(shown ONCE, store it now\):\s+(\S+)", stdout)
    if not m:
        raise AssertionError(
            f"could not parse key from create output:\n{stdout}"
        )
    return m.group(1)


def _db_url() -> str:
    raw = next(
        line.split("=", 1)[1].strip()
        for line in (REPO / ".env").read_text().splitlines()
        if line.startswith("TTS_ERP_DB_URL=")
    )
    return normalize_db_url(raw)


@pytest.fixture()
def test_name() -> str:
    """TEST_-prefixed name so the api_client conftest's wipe cleans up."""
    import time
    return f"TEST_api_keys_py_{int(time.time() * 1000) % 1_000_000_000}"


def test_create_writes_to_security_api_keys_not_public(test_name):
    """The headline fix: api_keys.py must target security.api_keys,
    not the V1 public.api_keys dead table. Run create, then check
    BOTH schemas — exactly one row, in security, with the right hash.
    """
    proc = _run_cli("create", "--name", test_name, "--role", "readwrite")
    assert proc.returncode == 0, f"create failed:\n{proc.stderr}"
    key = _key_from_create_stdout(proc.stdout)
    key_hash = _key_from_db_helper(key)

    with psycopg.connect(_db_url()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, key_prefix, role, status "
            "FROM security.api_keys WHERE key_hash = %s",
            (key_hash,),
        )
        sec_row = cur.fetchone()
        cur.execute(
            "SELECT id FROM public.api_keys WHERE key_hash = %s",
            (key_hash,),
        )
        pub_row = cur.fetchone()

    assert sec_row is not None, (
        f"create printed {key[:16]}... but security.api_keys has no row with that hash. "
        f"The CLI is still writing to the V1 dead table."
    )
    assert pub_row is None, (
        f"create wrote to BOTH schemas (security id={sec_row[0]}, public id={pub_row[0]}). "
        f"expected security.api_keys only."
    )
    assert sec_row[1] == test_name
    assert sec_row[2] == key[:16]
    assert sec_row[3] == "readwrite"
    assert sec_row[4] == "active"  # V3 status, not V1 enabled


def test_create_then_v2_login_round_trip(test_name):
    """The bug's user-visible symptom: key exists, login still 401.
    This test runs the full create → POST /v2/auth/login → 200 path
    in one go, so a regression here means the CLI is back to
    writing somewhere the v2 app can't see.
    """
    proc = _run_cli("create", "--name", test_name, "--role", "readwrite")
    assert proc.returncode == 0
    key = _key_from_create_stdout(proc.stdout)

    cj = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(
        "http://127.0.0.1:9877/v2/auth/login",
        method="POST",
        data=json.dumps({"key": key}).encode(),
        headers={"Content-Type": "application/json", "X-Requested-With": "tts-erp"},
    )
    try:
        r = opener.open(req, timeout=5)
    except urllib.error.HTTPError as e:
        pytest.fail(
            f"login returned {e.code} for a key api_keys.py just created. "
            f"Body: {e.read()[:200]!r}. The CLI is writing to a table the "
            f"v2 app doesn't read from."
        )
    body = r.read()
    assert r.status == 200, f"login status {r.status}: {body[:200]!r}"
    assert b'"ok":true' in body


def test_list_reads_from_security_api_keys(test_name):
    """list must read security.api_keys. After create, the new
    key's name + prefix must appear in list output. The V1
    table must not be queried (we never write there)."""
    proc_create = _run_cli("create", "--name", test_name, "--role", "readwrite")
    key = _key_from_create_stdout(proc_create.stdout)

    proc_list = _run_cli("list")
    assert proc_list.returncode == 0, proc_list.stderr
    out = proc_list.stdout
    assert test_name in out, (
        f"newly-created key {test_name!r} missing from list output:\n{out}"
    )
    assert key[:16] in out, f"key prefix {key[:16]!r} missing from list:\n{out}"


def test_revoke_disables_in_security_api_keys(test_name):
    """revoke must update security.api_keys.status='disabled' (V3
    column name), not public.api_keys.enabled. After revoke the
    v2 login for that key must 401 within cache TTL."""
    proc = _run_cli("create", "--name", test_name, "--role", "readwrite")
    key = _key_from_create_stdout(proc.stdout)
    key_hash = _key_from_db_helper(key)

    proc_revoke = _run_cli("revoke", "--prefix", key[:16])
    assert proc_revoke.returncode == 0
    assert "revoked" in proc_revoke.stdout.lower()

    with psycopg.connect(_db_url()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM security.api_keys WHERE key_hash = %s",
            (key_hash,),
        )
        row = cur.fetchone()
    assert row is not None and row[0] == "disabled", (
        f"after revoke, security.api_keys row has status={row[0]!r}, expected 'disabled'"
    )


def test_v1_columns_scopes_and_expires_at_are_not_written(test_name, capsys):
    """Back-compat surface: the CLI still accepts --scopes and
    --expires-days (so existing scripts don't break) but those
    values are NOT stored in security.api_keys (V3 dropped those
    columns). The CLI prints a deprecation line so the operator
    knows.
    """
    proc = _run_cli(
        "create",
        "--name", test_name,
        "--role", "readwrite",
        "--scopes", "seller:1", "advertiser:2",
        "--expires-days", "30",
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # The legacy "scopes : seller:1,advertiser:2" line must NOT be
    # printed (we're not storing them, so claiming we are is a lie).
    assert "scopes  : seller:1" not in out, (
        f"CLI still claims scopes were stored; security.api_keys has no "
        f"scopes column. Output:\n{out}"
    )
    assert "expires : in 30 days" not in out, (
        f"CLI still claims expiry was stored; security.api_keys has no "
        f"expires_at column. Output:\n{out}"
    )
    # The key was actually created (row in DB with the right hash):
    key = _key_from_create_stdout(out)
    key_hash = _key_from_db_helper(key)
    with psycopg.connect(_db_url()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM security.api_keys WHERE key_hash = %s",
            (key_hash,),
        )
        assert cur.fetchone() is not None, "row didn't land in security.api_keys"


# ─── helpers ───────────────────────────────────────────────────────


def _key_from_db_helper(plaintext_key: str) -> str:
    import hashlib
    return hashlib.sha256(plaintext_key.encode()).hexdigest()
