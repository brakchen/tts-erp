"""Regression tests for the pre-collection dotenv safety switch."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.layer_unit

REAL_CONFTST = Path(__file__).with_name("conftest.py")


_RUN_CONFTST = """
import os
import runpy

runpy.run_path("tests/conftest.py", run_name="__main__")
print(os.environ.get("BOOTSTRAP_SENTINEL", "<missing>"))
"""


def _run_conftest(
    tmp_path: Path,
    *,
    flag: str | None,
    dotenv: str | None = "from-dotenv",
    exported: str | None = None,
    dotenv_directory: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Execute the real conftest top level in a synthetic checkout.

    This deliberately uses Python module execution rather than source inspection
    or a replacement loader, so the assertion covers imports and pre-collection
    execution of the actual bootstrap module.
    """
    checkout = tmp_path / "checkout"
    tests_dir = checkout / "tests"
    tests_dir.mkdir(parents=True)
    shutil.copy2(REAL_CONFTST, tests_dir / "conftest.py")
    if dotenv_directory:
        (checkout / ".env").mkdir()
    elif dotenv is not None:
        (checkout / ".env").write_text(
            f"BOOTSTRAP_SENTINEL={dotenv}\n", encoding="utf-8"
        )

    allowlisted_names = {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PYTHONPATH",
        "TTS_ERP_DB_URL_TEST",
        "VIRTUAL_ENV",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in allowlisted_names
    }
    project_root = str(REAL_CONFTST.parent.parent)
    pythonpath = [project_root]
    if environment.get("PYTHONPATH"):
        pythonpath.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    if flag is not None:
        environment["TTS_ERP_TEST_NO_DOTENV"] = flag
    if exported is not None:
        environment["BOOTSTRAP_SENTINEL"] = exported

    return subprocess.run(
        [sys.executable, "-c", _RUN_CONFTST],
        cwd=checkout,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _stdout(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_no_dotenv_flag_skips_real_env_file_before_collection(tmp_path: Path) -> None:
    result = _run_conftest(tmp_path, flag="1")

    assert _stdout(result) == "<missing>"


def test_unset_no_dotenv_flag_preserves_dotenv_loading(tmp_path: Path) -> None:
    result = _run_conftest(tmp_path, flag=None)

    assert _stdout(result) == "from-dotenv"


@pytest.mark.parametrize("flag", [None, "0", "false"])
def test_non_one_flag_preserves_existing_export_precedence(
    tmp_path: Path, flag: str | None
) -> None:
    result = _run_conftest(
        tmp_path,
        flag=flag,
        dotenv="from-dotenv",
        exported="from-export",
    )

    assert _stdout(result) == "from-export"


def test_no_dotenv_flag_does_not_read_env_file(tmp_path: Path) -> None:
    result = _run_conftest(tmp_path, flag="1", dotenv_directory=True)

    assert _stdout(result) == "<missing>"
