"""Copy the canonical result after all existing terminal hooks have finished."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_terminal_summary(terminalreporter, exitstatus):
    yield
    source = os.environ.get("TTS_ERP_TEST_SELECTION_REPORT")
    if source and Path(source).is_file():
        shutil.copyfile(source, Path(os.environ["CI_RESULT_DIR"]) / "selected-report.json")
