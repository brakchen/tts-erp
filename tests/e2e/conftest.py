"""tests/e2e — 需要运行中服务（默认 :9877）的端到端 live 冒烟。

这些用例**默认不跑**：全部带 `requires_service` 标记，被
`scripts/test.sh fast`（`-m "not slow and not requires_service"`）排除。
显式运行：

    bash scripts/test_isolated.sh e2e        # 或 bash scripts/test.sh e2e

标记由本 conftest 的 `pytest_collection_modifyitems` 统一打给
`tests/e2e/` 下所有用例。注意：`pytestmark` 只作用于它所在的模块，
写在 conftest.py 里**不会**传递给测试文件，会导致 `-m domain_e2e`
零收集、`fast` 反而误含这些 live 用例，因此不用 `pytestmark`。

约定：

- 只读：只打 GET 端点（/healthz、/endpoints、/v2/* 只读接口），不做任何写操作；
- 鉴权：从 `TTS_ERP_SERVICE_KEY` 环境变量或仓库根 `.env` 读取服务 key，
  以 `Bearer` 提交；缺 key 时相关用例跳过；
- 基址可用 `TTS_ERP_E2E_BASE` 覆盖（默认 `http://127.0.0.1:9877`）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

DEFAULT_BASE = "http://127.0.0.1:9877"

E2E_MARKS = [pytest.mark.requires_service, pytest.mark.domain_e2e]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """给本目录下的用例补上 e2e 标记（conftest 的 pytestmark 不传递）."""
    here = Path(__file__).resolve().parent
    for item in items:
        item_path = getattr(item, "path", None)
        if item_path is not None and here in Path(item_path).parents:
            for mark in E2E_MARKS:
                item.add_marker(mark)


def base_url() -> str:
    return os.environ.get("TTS_ERP_E2E_BASE", DEFAULT_BASE).rstrip("/")


def service_key() -> str | None:
    """服务 key：环境变量优先，其次仓库根 .env 的 TTS_ERP_SERVICE_KEY。"""
    key = os.environ.get("TTS_ERP_SERVICE_KEY")
    if key:
        return key
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("TTS_ERP_SERVICE_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def request_json(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    """请求 live 服务并解析 JSON；返回 (http_status, parsed_or_error_text)。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    key = service_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(base_url() + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(text)
            except json.JSONDecodeError:
                return resp.status, text
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body_text)
        except json.JSONDecodeError:
            return exc.code, body_text
