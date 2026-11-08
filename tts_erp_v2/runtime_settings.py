"""Reloadable application configuration with explicit source precedence.

Precedence is deliberately local-first:

1. ``.env`` in the process working directory (or ``TTS_ERP_DOTENV_FILE``),
2. the reloadable file named by ``TTS_ERP_RUNTIME_ENV_FILE``,
3. the process environment inherited at startup.

Files are reparsed only when inode, size, or nanosecond mtime changes.  This
makes downstream credential rotation visible to the next request/job without
restarting the process, while avoiding file I/O beyond a cheap ``stat`` on an
unchanged configuration.  Values are never written to logs.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Any, Mapping

from dotenv import dotenv_values

logger = logging.getLogger("tts_erp_v2.runtime_settings")

DOTENV_FILE_VAR: Final = "TTS_ERP_DOTENV_FILE"
RUNTIME_ENV_FILE_VAR: Final = "TTS_ERP_RUNTIME_ENV_FILE"
_UNCHANGED_METADATA_RECHECK_NS: Final = 250_000_000

STRONG_DEPENDENCIES: Final[tuple[str, ...]] = (
    "TTS_ERP_DB_URL",
    "TTS_ERP_FERNET_KEY",
)

# These integrations are optional for the API as a whole.  Missing settings
# disable only that downstream and are reported at ERROR once per state change.
OPTIONAL_SETTING_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "TikTok global API fallback": (
        "TIKTOK_SERVICE_ID",
        "TIKTOK_APP_KEY",
        "TIKTOK_APP_SECRET",
    ),
    "Miaoshou API": (
        "MIAOSHOU_LICENSE_ID",
        "MIAOSHOU_COMPANY_SECRET",
    ),
    "MinIO object storage": (
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_BUCKET",
        "MINIO_SECURE",
        "MINIO_REGION",
        "MINIO_PRESIGN_EXPIRY_SECONDS",
    ),
    "ExchangeRate API": ("EXCHANGERATE_API_KEY",),
    "Artemis publishing": (
        "ARTEMIS_BASE_URL",
        "ARTEMIS_DEVICE_SERIAL",
    ),
}


class MissingRequiredSettings(RuntimeError):
    """Raised before service startup when a strong dependency is absent."""


@dataclass(frozen=True)
class _FileSignature:
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class _CachedFile:
    signature: _FileSignature | None
    values: dict[str, str]
    checked_ns: int


class RuntimeSettings:
    """Thread-safe, mtime-aware view across dotenv, runtime file, and env."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._files: dict[Path, _CachedFile] = {}
        self._missing_runtime_paths: set[Path] = set()
        self._availability: dict[str, bool] = {}
        self._revision = 0

    @staticmethod
    def _signature(path: Path) -> _FileSignature | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return _FileSignature(stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def _read_file(
        self,
        path: Path | None,
        *,
        report_missing: bool = False,
    ) -> dict[str, str]:
        if path is None:
            return {}
        signature = self._signature(path)
        checked_ns = time.monotonic_ns()
        cached = self._files.get(path)
        if (
            cached is not None
            and cached.signature == signature
            and checked_ns - cached.checked_ns < _UNCHANGED_METADATA_RECHECK_NS
        ):
            return cached.values
        if signature is None:
            if report_missing and path not in self._missing_runtime_paths:
                logger.error(
                    "runtime environment file is missing; live configuration "
                    "updates are unavailable: %s",
                    path,
                )
                self._missing_runtime_paths.add(path)
            values: dict[str, str] = {}
        else:
            try:
                parsed = dotenv_values(path, interpolate=False)
            except (OSError, UnicodeError) as exc:
                logger.error(
                    "cannot read configuration file %s: %s",
                    path,
                    type(exc).__name__,
                )
                values = {}
            else:
                values = {
                    str(key): "" if value is None else str(value)
                    for key, value in parsed.items()
                }
                if path in self._missing_runtime_paths:
                    logger.info("runtime environment file is available again: %s", path)
                    self._missing_runtime_paths.discard(path)
        changed = (
            cached is None or cached.signature != signature or cached.values != values
        )
        self._files[path] = _CachedFile(signature, values, checked_ns)
        if changed:
            self._revision += 1
        return values

    @staticmethod
    def _as_path(raw: str | None, *, default: Path | None = None) -> Path | None:
        if raw is None:
            return default
        raw = raw.strip()
        if not raw:
            return None
        return Path(raw).expanduser().resolve(strict=False)

    def _source_maps(
        self, env: Mapping[str, str] | None = None
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Return (dotenv_map, runtime_map).

        ``env`` 默认 None ⇒ 读 os.environ，**行为与改动前完全一致**；
        传入显式 env ⇒ 用它替代 os.environ 解析来源链
        （2026-11-09：owner 的接口必须能表达"上下文"，否则调用方只能
        临时改全局 os.environ 再恢复 —— 那是 workaround，不是接口）。
        """
        e = os.environ if env is None else env
        dotenv_path = self._as_path(
            e.get(DOTENV_FILE_VAR),
            default=(Path.cwd() / ".env").resolve(strict=False),
        )
        dotenv_map = self._read_file(dotenv_path)
        runtime_raw = dotenv_map.get(RUNTIME_ENV_FILE_VAR)
        if runtime_raw is None:
            runtime_raw = e.get(RUNTIME_ENV_FILE_VAR)
        runtime_path = self._as_path(runtime_raw)
        runtime_map = self._read_file(
            runtime_path,
            report_missing=runtime_path is not None,
        )
        return dotenv_map, runtime_map

    def get(self, name: str, default: str | None = None) -> str | None:
        """Return one setting without mutating ``os.environ``."""
        with self._lock:
            dotenv_map, runtime_map = self._source_maps()
            if name in dotenv_map:
                return dotenv_map[name]
            if name in runtime_map:
                return runtime_map[name]
            return os.environ.get(name, default)

    def get_with_source(
        self, name: str, default: str | None = None
    ) -> tuple[str | None, str]:
        """Return ``(value, source)`` — source is one of
        ``dotenv`` / ``runtime_file`` / ``process_env`` / ``default``.

        2026-11-09：暴露本模块的来源判定，供核对工具**消费事实**而不是
        **复制规则**（通则 22：验证工具不得复制被验证的规则）。

        与 :meth:`get` 共用 :meth:`_source_maps`，**不复制优先级链**；
        行为与 :meth:`get` 完全一致，只是多返回来源。

        ``dotenv`` 与 ``runtime_file`` 的具体路径由 :meth:`_source_maps`
        决定（含 ``TTS_ERP_DOTENV_FILE`` 优先于 cwd 隐式默认、
        ``.env`` 里的 ``TTS_ERP_RUNTIME_ENV_FILE`` 优先于进程环境）。
        """
        with self._lock:
            dotenv_map, runtime_map = self._source_maps()
            if name in dotenv_map:
                return dotenv_map[name], "dotenv"
            if name in runtime_map:
                return runtime_map[name], "runtime_file"
            if name in os.environ:
                return os.environ[name], "process_env"
            return default, "default"

    def get_setting_facts(
        self,
        name: str,
        default: str | None = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Return the full source picture for one setting.

        2026-11-09：为通则 22/Chief 第十轮「owner 暴露事实」而暴露。

        Returns::

            {"layers":   {"dotenv":…, "runtime_file":…, "process_env":…},
             "effective": …,
             "source":    "dotenv"|"runtime_file"|"process_env"|"default",
             "shadowed":  {"dotenv": "old-value", …}}

        ``shadowed`` 是关键一格：**被遮蔽但仍然存在的层及其值** ——
        单值接口表达不了"旧值藏在低优先级层"，轮换验收因此必须靠它。

        ``env`` 显式传参 ⇒ 调用方**不需要**临时改全局 os.environ。
        """
        with self._lock:
            e = os.environ if env is None else env
            dotenv_map, runtime_map = self._source_maps(env)
            layers = {
                "dotenv": dotenv_map.get(name),
                "runtime_file": runtime_map.get(name),
                "process_env": e.get(name),
            }
            if name in dotenv_map:
                effective, source = dotenv_map[name], "dotenv"
            elif name in runtime_map:
                effective, source = runtime_map[name], "runtime_file"
            elif name in e:
                effective, source = e[name], "process_env"
            else:
                effective, source = default, "default"
            shadowed = {
                k: v for k, v in layers.items()
                if k != source and v is not None
            }
            return {
                "layers": layers,
                "effective": effective,
                "source": source,
                "shadowed": shadowed,
            }

    def revision(self) -> int:
        """Refresh file signatures and return the monotonic config revision."""
        with self._lock:
            self._source_maps()
            return self._revision

    def note_availability(
        self,
        key: str,
        *,
        available: bool,
        missing: tuple[str, ...],
    ) -> None:
        """Log only missing/recovered transitions, never secret values."""
        with self._lock:
            previous = self._availability.get(key)
            self._availability[key] = available
        if previous is available:
            return
        if available:
            if previous is False:
                logger.info("optional configuration recovered: %s", key)
            return
        logger.error(
            "optional configuration unavailable: %s; missing: %s; "
            "service remains running and will retry on the next request/job",
            key,
            ", ".join(missing),
        )

    def reset_for_testing(self) -> None:
        with self._lock:
            self._files.clear()
            self._missing_runtime_paths.clear()
            self._availability.clear()
            self._revision = 0


_settings = RuntimeSettings()


def get_setting(name: str, default: str | None = None) -> str | None:
    return _settings.get(name, default)


def get_setting_with_source(
    name: str, default: str | None = None
) -> tuple[str | None, str]:
    """Module-level twin of :meth:`RuntimeSettings.get_with_source`.

    2026-11-09：供核对工具消费 owner 的来源判定，避免工具自建第二套链。
    """
    return _settings.get_with_source(name, default)


def get_setting_facts(
    name: str,
    default: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Module-level twin of :meth:`RuntimeSettings.get_setting_facts`."""
    return _settings.get_setting_facts(name, default, env=env)


def config_revision() -> int:
    return _settings.revision()


def require_setting(name: str) -> str:
    value = get_setting(name)
    if value is None or not value.strip():
        raise MissingRequiredSettings(
            f"required setting {name} is missing; configure local .env or "
            f"{RUNTIME_ENV_FILE_VAR} before service startup"
        )
    return value.strip()


def validate_required_settings(
    component: str,
    names: tuple[str, ...] = STRONG_DEPENDENCIES,
) -> None:
    missing = tuple(name for name in names if not ((get_setting(name) or "").strip()))
    if missing:
        raise MissingRequiredSettings(
            f"{component} cannot start; missing required setting(s): "
            + ", ".join(missing)
            + f". Configure local .env or {RUNTIME_ENV_FILE_VAR}."
        )


def optional_setting(
    name: str,
    *,
    feature: str,
    default: str | None = None,
) -> str | None:
    """Read an optional downstream setting and report state transitions."""
    value = get_setting(name)
    available = value is not None and bool(value.strip())
    _settings.note_availability(
        f"{feature}/{name}",
        available=available,
        missing=() if available else (name,),
    )
    return value if available else default


def log_optional_settings_status() -> None:
    """Report optional integration groups without blocking service startup."""
    for feature, names in OPTIONAL_SETTING_GROUPS.items():
        missing = tuple(
            name for name in names if not ((get_setting(name) or "").strip())
        )
        _settings.note_availability(
            feature,
            available=not missing,
            missing=missing,
        )

    docs_names = ("TTS_ERP_DOCS_USER", "TTS_ERP_DOCS_PASSWORD")
    configured = tuple(
        name for name in docs_names if ((get_setting(name) or "").strip())
    )
    if configured and len(configured) != len(docs_names):
        missing = tuple(name for name in docs_names if name not in configured)
        _settings.note_availability(
            "Docs basic authentication",
            available=False,
            missing=missing,
        )


def reset_for_testing() -> None:
    _settings.reset_for_testing()
