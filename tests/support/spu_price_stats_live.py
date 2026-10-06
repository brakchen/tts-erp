"""SPU 价格统计真实全栈 E2E 支撑：冷启 uvicorn + 隔离 PostgreSQL + 真实 Chromium。

与相邻两层的分工（lane `spu-price-e2e`，2026-10-06）
----------------------------------------------------
| 层 | 命令 | 数据来源 | 拦什么 |
| --- | --- | --- | --- |
| `tests/browser/` mock 渲染 | `test_isolated.sh fast` | 本分支 Jinja + 本地静态服务 + mock JSON | 渲染契约、多视口溢出、字体栈 |
| `tests/e2e/` live smoke | `test_isolated.sh e2e`（需 :9877） | 常驻服务 + 真数据 | HTTP 形状 |
| **本层**自包含真实全栈 | `test_isolated.sh fast` | **uvicorn 冷启 + 隔离测试库 + 真实 Chromium** | 价格六指标在浏览器里的真实呈现 |

设计约束（对应 `docs/design/spu-price-statistics.md` §8.1 gate 4）：

- **自包含**：不依赖 :9877，不打 `requires_service`。uvicorn 子进程的
  `TTS_ERP_DB_URL` 指向 `scripts/test_isolated.sh` 克隆出来的库；端口由内核分配，
  启动完成后 `/healthz` 探活。API 不可用即失败（不是 skip）。
- **私有模板**：调用方必须用
  `TTS_ERP_TEST_NO_DOTENV=1` +
  `TTS_ERP_TEST_TEMPLATE_DB=tts_erp_test_template_price_e2e` +
  `--template-db tts_erp_test_template_price_e2e`，因为共享模板会被其它 lane
  stamp 成本 worktree 尚未合并的 revision（克隆库 `alembic_version` 指向不存在的
  脚本时 alembic 报 `Can't locate ...`）。
- **鉴权两条 leg**（§8.1 gate 2）：cookie leg 先 seed 一个 TEST 用户
  （`test_price_e2e_login`，viewer 角色），浏览器在 context 里真实
  `POST /v2/auth/login` 拿会话 cookie；api-key leg 仍用 readonly key。
- **env allowlist**：子进程只拿本模块显式列出的测试键（`api_env`），不继承本机
  `.env` 的生产凭据（TIKTOK_*、外部前缀等）。
- **真实行为**：断言页面 DOM 上的价格文本、状态文案、行序；期望值由本模块用
  `Decimal` **独立算出**（件数加权均值/中位数、原生币种→CNY 换算），不读 API
  响应，所以 API 与页面同时算错也会被抓到。
- **只读**：页面加载只发 GET；`LiveBrowser.close()` 断言整场没有写请求。
- 造数全部 `TEST_PRICE_E2E_` 前缀，module teardown 按子表→父表清干净。
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    FocusedSpu,
    ManualProductCost,
    RawRecord,
    SalesOrder,
    SalesOrderLine,
)
from tts_erp_v2.db.models.commerce import SalesOrderLinePriceObservation

# 会话 cookie 名以 app 常量为唯一来源（生产与测试同为 tts_erp_session）。
from tts_erp_v2.middleware.session_auth import SESSION_COOKIE_NAME

REPO_ROOT = Path(__file__).resolve().parents[2]

# ─── 口径常量（与 tts_erp_v2/analytics/spu_profitability 同源）─────────

_Q4 = Decimal("0.0001")
USD_CNY = Decimal("6.5")
USD_VND = Decimal(26000)
VND_RATE = USD_CNY / USD_VND  # 0.00025，与 native_to_cny_rates 同式
NATIVE_TO_CNY = {"CNY": Decimal(1), "USD": USD_CNY, "VND": VND_RATE}
DEFAULT_K1_CNY = Decimal(40)  # _implementation.K1_DEFAULT_CNY
PAID_STATUS = "DELIVERED"  # PAID_SALES_ORDER_STATUSES 成员

# 远未来：确保是本库最新汇率快照（同 `tests/api/test_spu_price_stats.py` 口径）
FX_SEED_TS = "2099-12-31T00:00:00+00:00"
FX_NEXT_UPDATE_TS = "2100-01-01T00:00:00+00:00"

API_KEY_PLAINTEXT = "ttserp_readonly_price_e2e"
API_KEY_NAME = "TEST_PRICE_E2E_READONLY"

# 造数前缀：所有 DELETE 都以它为准，绝不碰非本 lane 的行。
PREFIX_TEST = "TEST_PRICE_E2E"

# 登录 leg 的 TEST 用户：`USERNAME_RE` 只接受小写，所以库里存的是小写形态。
LOGIN_USERNAME = f"{PREFIX_TEST}_LOGIN".lower()  # test_price_e2e_login
LOGIN_CREDENTIAL = "Pricee2e1"  # 满足密码策略（≥6 位 + 大写 + 小写 + 数字）
LOGIN_ROLE = "viewer"  # readonly tier + page:spu-roi / page:focused-spus 权限

# 确定性测试 Fernet key（与 tests/conftest.py 的测试默认值同源，非生产凭据）。
TEST_FERNET_KEY = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="

AuthMode = Literal["api_key", "session"]

DAY_D1 = "2026-09-01"
DAY_D2 = "2026-09-10"
DAY_EMPTY_START = "2026-11-01"
DAY_EMPTY_END = "2026-11-02"

SPU_A1 = f"{PREFIX_TEST}_SPU_A1"
SPU_A2 = f"{PREFIX_TEST}_SPU_A2"
SPU_B1 = f"{PREFIX_TEST}_SPU_B1"
SPU_B2 = f"{PREFIX_TEST}_SPU_B2"
SPU_B3 = f"{PREFIX_TEST}_SPU_B3"

SHOP_A_SELLER = f"{PREFIX_TEST}_SELLER_A"
SHOP_B_SELLER = f"{PREFIX_TEST}_SELLER_B"

# 页面上价格六指标的 DOM 契约（grouped columns 的 tabulator-field）。
PRICE_FIELDS = (
    "priceStats.purchase.mean",
    "priceStats.purchase.median",
    "priceStats.originalSale.mean",
    "priceStats.originalSale.median",
    "priceStats.paid.mean",
    "priceStats.paid.median",
)

METRIC_LABELS = {"purchase": "采购价", "originalSale": "销售价", "paid": "实付价"}
STATUS_LABELS = {"complete": "完整", "partial": "部分覆盖", "no_samples": "无样本"}
_METRIC_ATTRS = {"purchase": "purchase", "originalSale": "original_sale", "paid": "paid"}
_METRICS = ("purchase", "originalSale", "paid")


# ─── 期望值 oracle（独立算，不读 API）────────────────────────────────


def q4(value: Decimal) -> str:
    """四位小数字符串（与 wire `_fmt(..., _MONEY_Q)` 同口径）。"""
    return format(value.quantize(_Q4, rounding=ROUND_HALF_UP), "f")


@dataclass(frozen=True)
class MetricStat:
    """一个价格指标在某个 scope/window 上的件数加权统计。"""

    mean: str | None
    median: str | None
    observed_quantity: int
    eligible_quantity: int
    estimated: bool = False

    @property
    def status(self) -> str:
        if self.observed_quantity <= 0:
            return "no_samples"
        if self.observed_quantity < self.eligible_quantity:
            return "partial"
        return "complete"

    def display(self, metric: str, kind: str) -> str:
        """页面呈现：空样本 → `—`；采购价估算样本加 `≈`；否则四位小数。"""
        value = self.mean if kind == "mean" else self.median
        if value is None:
            return "—"
        prefix = "≈" if metric == "purchase" and self.estimated else ""
        return prefix + value


@dataclass(frozen=True)
class ScopeStats:
    """purchase / originalSale / paid 三指标 = 页面上六个价格格。"""

    purchase: MetricStat
    original_sale: MetricStat
    paid: MetricStat

    def metric(self, name: str) -> MetricStat:
        return getattr(self, _METRIC_ATTRS[name])

    def cells(self) -> dict[str, str]:
        return {
            f"{metric}.{kind}": self.metric(metric).display(metric, kind)
            for metric in _METRICS
            for kind in ("mean", "median")
        }

    def summary_status(self) -> str:
        """`#price-summary-status` 的完整文案（与 page kernel 同式）。"""
        parts = "；".join(
            f"{METRIC_LABELS[metric]}：{STATUS_LABELS[self.metric(metric).status]}"
            for metric in _METRICS
        )
        return f"已加载 · {parts}"


def _price_at_rank(buckets: dict[Decimal, int], rank: int) -> Decimal:
    cumulative = 0
    for price in sorted(buckets):
        cumulative += buckets[price]
        if rank <= cumulative:
            return price
    raise AssertionError("median rank exceeded observed quantity")


def _mean_and_median(buckets: dict[Decimal, int]) -> tuple[str | None, str | None]:
    """件数加权均值/中位数（与 `_price_math.summarize_weighted_prices` 同定义）。

    `localcontext(prec=80)` 与 `_price_math` 对齐：长小数价（原生币种换算）在
    默认 28 位精度下会先被截断再取整，oracle 会与 API 分歧。
    """
    total = sum(buckets.values())
    if total <= 0:
        return None, None
    with localcontext() as context:
        context.prec = 80
        weighted = sum((price * qty for price, qty in buckets.items()), Decimal(0))
        median = _price_at_rank(buckets, total // 2 + 1)
        if total % 2 == 0:
            median = (_price_at_rank(buckets, total // 2) + median) / 2
        return q4(weighted / Decimal(total)), q4(median)


# ─── 造数规格 ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class SeedLine:
    """一行商品的价格事实；`observed=False` 表示只有订单行、没有价格观察。"""

    spu_id: str
    day: str
    quantity: int
    currency: str
    unit_cost_cny: Decimal | None  # None → 无人工成本 → K1(40 CNY) 估算
    original_native: Decimal | None
    paid_native: Decimal | None
    observed: bool = True


@dataclass(frozen=True)
class SeedShop:
    name: str
    seller: str
    account_name: str
    region: str
    lines: tuple[SeedLine, ...]
    focused: bool = False


SHOP_A = SeedShop(
    name="A",
    seller=SHOP_A_SELLER,
    account_name=f"{PREFIX_TEST} 价格店 A",
    region="VN",
    # 设计文档 §7.1 加权 oracle：10×1 + 40×9 → 大盘均值 37、中位数 40（行均值=25）。
    # A2 用 VND 原生价 → 证明价格确实经 FX 快照换算（200000 VND × 0.00025 = 50 CNY）。
    # A2 的行排在前：两行 spend 均为 0 → roi_real 无效 → 服务端默认序按 spu_pk 升序
    # [A2, A1]，与价格升序 [A1, A2]（10 < K1 40）相反，asc 断言才证明行序真的反了。
    lines=(
        SeedLine(SPU_A2, DAY_D1, 9, "VND", None, Decimal(200000), Decimal(180000)),
        SeedLine(SPU_A1, DAY_D1, 1, "CNY", Decimal(10), Decimal(20), Decimal(18)),
        SeedLine(SPU_A1, DAY_D2, 1, "CNY", Decimal(10), Decimal(880), Decimal(800)),
    ),
    focused=True,
)

SHOP_B = SeedShop(
    name="B",
    seller=SHOP_B_SELLER,
    account_name=f"{PREFIX_TEST} 价格店 B",
    region="VN",
    # B1 无观察 → 六格全 `—`（绝不 0）；B3 只有 paid 有价 → originalSale 两格 `—`。
    lines=(
        SeedLine(SPU_B1, DAY_D1, 2, "CNY", Decimal(20), None, None, observed=False),
        SeedLine(SPU_B2, DAY_D1, 1, "CNY", Decimal(30), Decimal(100), Decimal(90)),
        SeedLine(SPU_B2, DAY_D1, 1, "CNY", Decimal(30), None, Decimal(200)),
        SeedLine(SPU_B3, DAY_D1, 1, "CNY", Decimal(30), None, Decimal(70)),
    ),
)


@dataclass(frozen=True)
class SeededShop:
    """已落库的店铺 + 造数规格，可按窗口/SPU 独立推出期望值。"""

    pk: int
    spec: SeedShop

    @property
    def name(self) -> str:
        return self.spec.name

    def lines(self, days: Iterable[str], spu_id: str | None = None) -> list[SeedLine]:
        wanted = set(days)
        return [
            line
            for line in self.spec.lines
            if line.day in wanted and (spu_id is None or line.spu_id == spu_id)
        ]

    def stats(self, days: Iterable[str], spu_id: str | None = None) -> ScopeStats:
        eligible = [line for line in self.lines(days, spu_id) if line.observed]
        eligible_quantity = sum(line.quantity for line in eligible)

        cost_buckets: dict[Decimal, int] = {}
        cost_estimated = False
        for line in eligible:
            cost = line.unit_cost_cny
            if cost is None:
                cost = DEFAULT_K1_CNY
                cost_estimated = True
            cost_buckets[cost] = cost_buckets.get(cost, 0) + line.quantity
        purchase_mean, purchase_median = _mean_and_median(cost_buckets)

        def field_stat(attr: str) -> MetricStat:
            buckets: dict[Decimal, int] = {}
            observed = 0
            for line in eligible:
                native = getattr(line, attr)
                if native is None:
                    continue
                value = native * NATIVE_TO_CNY[line.currency]
                buckets[value] = buckets.get(value, 0) + line.quantity
                observed += line.quantity
            mean, median = _mean_and_median(buckets)
            return MetricStat(mean, median, observed, eligible_quantity)

        return ScopeStats(
            purchase=MetricStat(
                purchase_mean,
                purchase_median,
                eligible_quantity,
                eligible_quantity,
                estimated=cost_estimated,
            ),
            original_sale=field_stat("original_native"),
            paid=field_stat("paid_native"),
        )


@dataclass(frozen=True)
class PriceScene:
    """真实库里的价格场景（两家店 + 汇率快照 + 只读 API key）。"""

    shop_a: SeededShop
    shop_b: SeededShop
    fx_snapshot_id: int
    api_key: str
    login_username: str
    login_credential: str

    def shop(self, name: str) -> SeededShop:
        return {"A": self.shop_a, "B": self.shop_b}[name]


# ─── 数据库造数 / 清理 ───────────────────────────────────────────────


def make_engine(db_url: str) -> Engine:
    """本 lane 自用的 engine（不占用 `tests/conftest.db_engine` 的单例）。"""
    return create_engine(db_url, future=True)


# 本 lane 的清理语句：只按 `TEST_PRICE_E2E_` 前缀删自己的行，子表→父表排序。
_WIPE_STATEMENTS = (
    (
        "DELETE FROM commerce.sales_order_line_price_observations WHERE shop_pk IN "
        "(SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_PRICE_E2E_%')"
    ),
    (
        "DELETE FROM commerce.sales_order_lines WHERE order_pk IN ("
        "SELECT id FROM commerce.sales_orders WHERE shop_pk IN ("
        "SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_PRICE_E2E_%'))"
    ),
    (
        "DELETE FROM commerce.sales_orders WHERE shop_pk IN "
        "(SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_PRICE_E2E_%')"
    ),
    (
        "DELETE FROM reporting.focused_spus WHERE shop_pk IN "
        "(SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_PRICE_E2E_%')"
    ),
    (
        "DELETE FROM procurement.manual_product_costs WHERE spu_pk IN "
        "(SELECT id FROM commerce.products_spu WHERE spu_id LIKE 'TEST_PRICE_E2E_%')"
    ),
    "DELETE FROM integration.raw_records WHERE external_id LIKE 'TEST_PRICE_E2E_%'",
    "DELETE FROM commerce.products_spu WHERE spu_id LIKE 'TEST_PRICE_E2E_%'",
    "DELETE FROM commerce.shops WHERE shop_id LIKE 'TEST_PRICE_E2E_%'",
    (
        "DELETE FROM fx.exchange_rate_snapshots WHERE base_code = 'USD' "
        "AND upstream_last_update = :ts"
    ),
    "DELETE FROM security.api_keys WHERE name = :name",
    # 登录用户：子表 user_sessions / user_roles 在库里是 ON DELETE CASCADE。
    "DELETE FROM security.users WHERE username LIKE 'test_price_e2e_%'",
)


def _wipe(engine: Engine) -> None:
    """按子表→父表清掉本 lane 的 `TEST_PRICE_E2E_` 行。"""
    with engine.begin() as conn:
        for statement in _WIPE_STATEMENTS:
            # pi-lens-ignore: python-sql-injection
            conn.execute(text(statement), {"ts": FX_SEED_TS, "name": API_KEY_NAME})


def _seed_fx(sess: Session) -> int:
    # pi-lens-ignore: python-sql-injection
    snapshot_id = sess.execute(
        text(
            "INSERT INTO fx.exchange_rate_snapshots "
            "(base_code, upstream_last_update, next_update_at, fetched_at, rates_count) "
            "VALUES ('USD', :ts, :next_ts, now(), 3) RETURNING id"
        ),
        {"ts": FX_SEED_TS, "next_ts": FX_NEXT_UPDATE_TS},
    ).scalar_one()
    for code, rate in (("USD", "1"), ("CNY", str(USD_CNY)), ("VND", str(USD_VND))):
        # pi-lens-ignore: python-sql-injection
        sess.execute(
            text(
                "INSERT INTO fx.exchange_rates (snapshot_id, base_code, target_code, rate) "
                "VALUES (:sid, 'USD', :code, :rate)"
            ),
            {"sid": snapshot_id, "code": code, "rate": rate},
        )
    return int(snapshot_id)


def _seed_api_key(sess: Session) -> str:
    """只读 API key（页面与 API 都由它鉴权）；真实提交，app 才查得到。"""
    key_hash = hashlib.sha256(API_KEY_PLAINTEXT.encode()).hexdigest()
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(
            "INSERT INTO security.api_keys (key_hash, key_prefix, name, role, status) "
            "VALUES (:hash, :prefix, :name, 'readonly', 'active') "
            "ON CONFLICT (key_hash) DO UPDATE SET "
            "key_prefix = EXCLUDED.key_prefix, name = EXCLUDED.name, "
            "role = EXCLUDED.role, status = EXCLUDED.status"
        ),
        {
            "hash": key_hash,
            "prefix": API_KEY_PLAINTEXT[:16],
            "name": API_KEY_NAME,
        },
    )
    return API_KEY_PLAINTEXT


def _seed_login_user(sess: Session) -> None:
    """真实 `security.users` TEST 用户（viewer 角色，可登录 spu-roi 页）。

    用 app 自己的 `accounts.service.create_user`（同一套 argon2 哈希与密码策略），
    浏览器再走真实 `POST /v2/auth/login` 拿 cookie；不硬编码生产口令、不绕过登录。
    """
    from tts_erp_v2.accounts import service  # 局部导入：仅供本 leg 使用

    service.create_user(
        sess,
        username=LOGIN_USERNAME,
        display_name=f"{PREFIX_TEST} 登录用户",
        password=LOGIN_CREDENTIAL,
        roles=[LOGIN_ROLE],
        actor="spu-price-e2e",
    )


def _day_timestamps(day: str) -> tuple[datetime, datetime]:
    """订单时间取当日 08:00Z（店铺本地 UTC+7 → 15:00，绝不跨日）。"""
    moment = datetime.fromisoformat(f"{day}T08:00:00+00:00")
    return moment, moment


def _seed_shop(sess: Session, spec: SeedShop) -> SeededShop:
    account = ChannelAccount(
        platform="tiktok",
        shop_id=spec.seller,
        account_name=spec.account_name,
        region=spec.region,
        status="active",
    )
    sess.add(account)
    sess.flush()

    products: dict[str, ChannelProduct] = {}
    costs_seeded: set[str] = set()
    for ordinal, line in enumerate(spec.lines):
        product = products.get(line.spu_id)
        if product is None:
            product = ChannelProduct(
                shop_pk=account.id,
                spu_id=line.spu_id,
                title=f"{line.spu_id} 标题",
                status="ACTIVATE",
                main_image_url="https://img.test/price-e2e.png",
            )
            sess.add(product)
            sess.flush()
            products[line.spu_id] = product
        if line.unit_cost_cny is not None and line.spu_id not in costs_seeded:
            sess.add(
                ManualProductCost(
                    spu_pk=product.id,
                    unit_cost=line.unit_cost_cny,
                    currency="CNY",
                    note=f"{PREFIX_TEST} 人工成本",
                    created_by="spu-price-e2e",
                )
            )
            costs_seeded.add(line.spu_id)

        order_time, paid_at = _day_timestamps(line.day)
        key = f"{line.spu_id}_{line.day}_{ordinal}"
        order = SalesOrder(
            shop_pk=account.id,
            order_id=f"{PREFIX_TEST}_ORDER_{key}",
            status=PAID_STATUS,
            currency=line.currency,
            paid_at=paid_at,
            order_time=order_time,
            payment_amount=Decimal(0),
            total_amount=Decimal(0),
        )
        sess.add(order)
        sess.flush()
        sales_line = SalesOrderLine(
            order_pk=order.id,
            external_line_id=f"{PREFIX_TEST}_LINE_{key}",
            spu_pk=product.id,
            quantity=Decimal(line.quantity),
            unit_price=Decimal(0),
            currency=line.currency,
        )
        sess.add(sales_line)
        sess.flush()
        if not line.observed:
            continue

        raw = RawRecord(
            endpoint="/order/202309/orders/search",
            external_id=f"{PREFIX_TEST}_RAW_{key}",
            payload={"order_id": order.order_id},
            payload_hash=hashlib.sha256(key.encode()).hexdigest(),
        )
        sess.add(raw)
        sess.flush()
        sess.add(
            SalesOrderLinePriceObservation(
                shop_pk=account.id,
                order_pk=order.id,
                external_line_id=sales_line.external_line_id,
                raw_record_id=raw.id,
                source_endpoint="ORDER_SEARCH",
                source_payload_hash=f"{PREFIX_TEST}_PAYLOAD_{key}",
                semantic_observation_hash=f"{PREFIX_TEST}_SEM_{key}",
                source_order_version_at=order_time,
                source_captured_at=order_time,
                spu_pk=product.id,
                raw_quantity=Decimal(line.quantity),
                effective_quantity=Decimal(line.quantity),
                quantity_status="OBSERVED",
                line_status_raw=PAID_STATUS,
                parent_payment_status=PAID_STATUS,
                gift_status="NOT_GIFT",
                original_price_native=line.original_native,
                paid_price_native=line.paid_native,
                currency=line.currency,
                original_price_status=(
                    "OBSERVED" if line.original_native is not None else "MISSING"
                ),
                paid_price_status=(
                    "OBSERVED" if line.paid_native is not None else "MISSING"
                ),
            )
        )
        sess.flush()

    if spec.focused:
        for spu_id in sorted(products):
            sess.add(FocusedSpu(shop_pk=account.id, spu_id=spu_id, active=True))
        sess.flush()
    return SeededShop(pk=int(account.id), spec=spec)


@contextmanager
def priced_test_database(db_url: str) -> Iterator[PriceScene]:
    """在隔离测试库里落一套价格场景，退出时清干净并断开 engine。"""
    engine = make_engine(db_url)
    try:
        _wipe(engine)
        with Session(engine) as sess:
            snapshot_id = _seed_fx(sess)
            api_key = _seed_api_key(sess)
            _seed_login_user(sess)
            shop_a = _seed_shop(sess, SHOP_A)
            shop_b = _seed_shop(sess, SHOP_B)
            sess.commit()
        yield PriceScene(
            shop_a=shop_a,
            shop_b=shop_b,
            fx_snapshot_id=snapshot_id,
            api_key=api_key,
            login_username=LOGIN_USERNAME,
            login_credential=LOGIN_CREDENTIAL,
        )
    finally:
        _wipe(engine)
        engine.dispose()


def is_test_shaped(db_url: str) -> bool:
    """DB 名必须含 `test`——防止把价格造数打进生产库。"""
    name = db_url.rsplit("/", 1)[-1].split("?", 1)[0]
    return "test" in name


# ─── 冷启临时 API（uvicorn 子进程）────────────────────────────────────


# 子进程只拿这份 allowlist 里的进程级变量；其余键一个都不继承。
_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")


def api_env(db_url: str) -> dict[str, str]:
    """临时 API 的环境：**显式 allowlist** + 只读测试库 + enforce 鉴权。

    `dict(os.environ)` 会把本机 `.env` 的生产凭据（TIKTOK_*、外部前缀…）带进
    子进程（design §8.1 gate 5 第 1 条），所以这里只放必需键：

    - `TTS_ERP_DB_URL` / `TTS_ERP_DB_URL_TEST` 都指向同一个克隆测试库；
    - `TTS_ERP_AUTH_MODE=enforce` + 确定性测试 Fernet key：与生产同形、无真凭据；
    - `TTS_ERP_RATE_LIMIT_PER_MIN` 抬高：一个 module 内要驱动十几次真实页面请求，
      默认 100/min 会把 E2E 变成限流测试（限流本身另有单测）。
    """
    env = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    env.update(
        {
            "TTS_ERP_DB_URL": db_url,
            "TTS_ERP_DB_URL_TEST": db_url,
            "TTS_ERP_AUTH_MODE": "enforce",
            "TTS_ERP_ACCESS_LOG": "0",
            "TTS_ERP_RATE_LIMIT_PER_MIN": "100000",
            "TTS_ERP_SESSION_SECURE": "0",
            "TTS_ERP_FERNET_KEY": TEST_FERNET_KEY,
            "TTS_ERP_TEST_NO_DOTENV": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def log_tail(path: Path, lines: int = 40) -> str:
    """冷启 API 日志的最后 `lines` 行（失败留证/探活报错共用）。"""
    if not path.exists():
        return "<no log>"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def _wait_healthy(port: int, proc: subprocess.Popen[bytes], log_path: Path) -> None:
    """轮询 /healthz（用 http.client：本机回环，不涉及 URL scheme 审计）。"""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"临时 API 提前退出 rc={proc.returncode}\n{log_tail(log_path)}"
            )
        with suppress(OSError):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                conn.request("GET", "/healthz")
                if conn.getresponse().status == 200:
                    return
            finally:
                conn.close()
        # 启动轮询间隔（不是用例同步 sleep）
        time.sleep(0.2)
    raise AssertionError(f"临时 API 60s 内未就绪\n{log_tail(log_path)}")


@dataclass(frozen=True)
class LiveApi:
    """冷启的临时 API：基址 + 日志/证据目录（失败时往这里写留证）。"""

    base: str
    log_path: Path
    evidence_dir: Path


def _stop_process_group(proc: subprocess.Popen[bytes]) -> None:
    """terminate → kill 升级，收掉整个进程组。

    `start_new_session=True` 使 pgid == pid，子进程再 fork 的成员也一并收掉；
    只调 `proc.terminate()` 会漏掉它们。
    """
    if proc.poll() is None:
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - 收尾尽力而为
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)


@contextmanager
def live_api_server(db_url: str) -> Iterator[LiveApi]:
    """冷启真实 FastAPI（uvicorn 子进程）并返回基址；退出时收掉整个进程组。"""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    evidence_dir = Path(tempfile.mkdtemp(prefix="tts-erp-price-e2e-"))
    log_path = evidence_dir / "uvicorn.log"
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "tts_erp_v2.app:build_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=api_env(db_url),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            _wait_healthy(port, proc, log_path)
            yield LiveApi(base=base, log_path=log_path, evidence_dir=evidence_dir)
        finally:
            _stop_process_group(proc)


# ─── 真实浏览器 ──────────────────────────────────────────────────────


_PRICE_STATE_JS = """(expected) => {
  for (const key of Object.keys(expected.cells)) {
    const el = document.getElementById('price-' + key.replace('.', '-'));
    if (!el || el.textContent.trim() !== expected.cells[key]) return false;
  }
  const status = document.getElementById('price-summary-status');
  return Boolean(status) && status.textContent.includes(expected.summary);
}"""

_SUMMARY_STATUS_JS = """(fragment) => {
  const el = document.getElementById('price-summary-status');
  return Boolean(el) && el.textContent.includes(fragment);
}"""

_SORT_NOTE_JS = """(fragment) => {
  const el = document.getElementById('sort-note');
  return Boolean(el) && el.textContent.includes(fragment);
}"""

_PRICE_CELLS_JS = """() => {
  const out = {};
  for (const metric of ['purchase', 'originalSale', 'paid']) {
    for (const kind of ['mean', 'median']) {
      const el = document.getElementById('price-' + metric + '-' + kind);
      out[metric + '.' + kind] = el ? el.textContent.trim() : null;
    }
  }
  return out;
}"""

# 商品格的 textContent 含 SPU id + 标题 + ≈ 标记，因此行身份用「包含且只命中一个
# 期望 id」判定：既能认出行，也能抓出多余的未知行（长度/命中数都不符即失败）。
_ROW_MATCH_JS = """(expected) => Array.from(document.querySelectorAll('.tabulator-row'))
  .map((row) => {
    const cell = row.querySelector(".tabulator-cell[tabulator-field='spu_id']");
    const text = cell ? cell.textContent : '';
    const matches = expected.filter((id) => text.includes(id));
    return matches.length === 1 ? matches[0] : null;
  })"""

_ROW_ORDER_WAIT_JS = """(expected) => {
  const got = Array.from(document.querySelectorAll('.tabulator-row')).map((row) => {
    const cell = row.querySelector(".tabulator-cell[tabulator-field='spu_id']");
    const text = cell ? cell.textContent : '';
    const hits = expected.filter((id) => text.includes(id));
    return hits.length === 1 ? hits[0] : null;
  });
  if (got.length !== expected.length) return false;
  return got.every((value, index) => value === expected[index]);
}"""

_ROWS_SET_WAIT_JS = """(expected) => {
  const got = Array.from(document.querySelectorAll('.tabulator-row')).map((row) => {
    const cell = row.querySelector(".tabulator-cell[tabulator-field='spu_id']");
    const text = cell ? cell.textContent : '';
    const hits = expected.filter((id) => text.includes(id));
    return hits.length === 1 ? hits[0] : null;
  });
  if (got.length !== expected.length) return false;
  if (got.some((value) => value === null)) return false;
  return new Set(got).size === expected.length;
}"""

_ROW_CELL_JS = """([spuId, field]) => {
  const rows = Array.from(document.querySelectorAll('.tabulator-row')).filter((row) => {
    const idCell = row.querySelector(".tabulator-cell[tabulator-field='spu_id']");
    return Boolean(idCell) && idCell.textContent.includes(spuId);
  });
  if (rows.length !== 1) return null;
  const cell = rows[0].querySelector('.tabulator-cell[tabulator-field="' + field + '"]');
  return cell ? cell.textContent.trim() : null;
}"""

_SET_WINDOW_VALUES_JS = """(values) => {
  document.querySelector('#filter-w-start').value = values.start;
  document.querySelector('#filter-w-end').value = values.end;
}"""

_DISPATCH_CHANGE_JS = """(which) => {
  const el = document.querySelector(which === 'start' ? '#filter-w-start' : '#filter-w-end');
  el.dispatchEvent(new Event('change', {bubbles: true}));
}"""

_MOBILE_LAYOUT_JS = """() => ({
  documentOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  tableOverflow: document.querySelector('.op-table-wrap').scrollWidth
    > document.querySelector('.op-table-wrap').clientWidth,
  productFrozen: Boolean(document.querySelector('.tabulator-cell.tabulator-frozen')),
})"""

SPU_ROI_API_PATTERN = re.compile(r"/v2/analytics/spu-roi(\?.*)?$")


class LivePage:
    """一个真实页面 + 请求记录（断言服务端排序参数与只读约束）。"""

    def __init__(self, page: Any, base: str, context: Any) -> None:
        self.page = page
        self.base = base
        self.context = context
        self.requests: list[str] = []
        self.write_requests: list[str] = []
        self._route_patterns: list[Any] = []
        page.on("request", self._record)

    def _record(self, request: Any) -> None:
        self.requests.append(request.url)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            self.write_requests.append(f"{request.method} {request.url}")

    # --- 导航 ---

    def goto(self, path: str) -> LivePage:
        response = self.page.goto(
            self.base + path, wait_until="domcontentloaded", timeout=30_000
        )
        # 有轮询的页面（sync-jobs 家族）不会 idle，等不到就算了，不因此失败
        with suppress(Exception):
            self.page.wait_for_load_state("networkidle", timeout=10_000)
        status = response.status if response is not None else 0
        assert status == 200, f"{path} 未渲染成功：HTTP {status}"
        return self

    # --- 筛选动作（真实 DOM 事件）---

    def set_window(self, start: str, end: str) -> None:
        """填写起止日期并派发 change。

        两个输入各自触发一次重取。派发顺序要避免中途出现 `start > end`：页面
        对 start 晚于 end 的中间态会走校验错误分支，所以先派发不会造成非法区间
        的那个字段（两个方向里至多一个非法）。
        """
        previous_end = self.page.locator("#filter-w-end").input_value()
        self.page.evaluate(_SET_WINDOW_VALUES_JS, {"start": start, "end": end})
        illegal_if_start_first = bool(start and previous_end and start > previous_end)
        order = ("end", "start") if illegal_if_start_first else ("start", "end")
        for which in order:
            self.page.evaluate(_DISPATCH_CHANGE_JS, which)

    def apply_all_history_preset(self) -> None:
        self.page.locator('[data-date-preset="all"]').click()

    def select_shop(self, shop_pk: int) -> None:
        self.page.select_option("#shop-switcher", str(shop_pk))

    # --- 价格断言帮手 ---

    def wait_price_state(
        self, cells: dict[str, str], summary: str, *, timeout: float = 30_000
    ) -> None:
        self.page.wait_for_function(
            _PRICE_STATE_JS, arg={"cells": cells, "summary": summary}, timeout=timeout
        )

    def wait_summary_status(self, fragment: str, *, timeout: float = 30_000) -> None:
        self.page.wait_for_function(_SUMMARY_STATUS_JS, arg=fragment, timeout=timeout)

    def wait_sort_note(self, fragment: str, *, timeout: float = 30_000) -> None:
        """等 `#sort-note` 反映新的服务端排序（证明重取后的渲染已完成）。"""
        self.page.wait_for_function(_SORT_NOTE_JS, arg=fragment, timeout=timeout)

    def wait_request(
        self, predicate: Callable[[str], bool], *, timeout: float = 20_000
    ) -> str:
        """等某个已发出的请求满足条件；用 Playwright 的等待驱动事件循环。"""
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            for url in self.requests:
                if predicate(url):
                    return url
            self.page.wait_for_timeout(50)
        raise AssertionError(f"未观察到满足条件的请求；已发出 {self.requests}")

    def price_cells(self) -> dict[str, str]:
        return self.page.evaluate(_PRICE_CELLS_JS)

    def summary_status(self) -> str:
        return self.page.locator("#price-summary-status").inner_text()

    def auth_identity(self) -> str | None:
        """页面自己的 `GET /v2/auth/me`：已登录返回用户名，否则 None。

        用于区分「cookie 会话真的成立了」与「页面照了某个 key 的便车」。
        """
        payload = self.page.evaluate("() => fetch('/v2/auth/me').then((r) => r.json())")
        return payload.get("username") if payload.get("authenticated") else None

    def row_order(self, expected: Sequence[str]) -> list[str | None]:
        """DOM 行序（按期望 id 归一；未命中的行返回 None）。"""
        return self.page.evaluate(_ROW_MATCH_JS, list(expected))

    def wait_row_order(self, expected: Sequence[str], *, timeout: float = 30_000) -> None:
        self.page.wait_for_function(_ROW_ORDER_WAIT_JS, arg=list(expected), timeout=timeout)

    def wait_rows(self, expected: Sequence[str], *, timeout: float = 30_000) -> None:
        self.page.wait_for_function(_ROWS_SET_WAIT_JS, arg=list(expected), timeout=timeout)

    def row_cell(self, spu_id: str, field: str) -> str:
        value = self.page.evaluate(_ROW_CELL_JS, [spu_id, field])
        assert value is not None, f"无法唯一定位第 {spu_id} 行的 {field} 格"
        return str(value)

    def price_header(self, field: str) -> Any:
        return self.page.locator(f'.tabulator-header [tabulator-field="{field}"]')

    def price_field_count(self, field: str) -> int:
        return int(self.price_header(field).count())

    def header_text(self) -> str:
        return self.page.locator(".tabulator-header").inner_text()

    def price_tip(self, metric: str) -> Any:
        return self.page.locator(f'[data-price-tip="{metric}"]')

    def retry_link(self) -> Any:
        return self.page.locator("#retry-link")

    def mobile_layout(self) -> dict[str, Any]:
        """移动端布局体检：文档溢出 / 表格自身横滚 / 商品列冻结。"""
        return self.page.evaluate(_MOBILE_LAYOUT_JS)

    def spu_roi_requests(self) -> list[str]:
        return [url for url in self.requests if "/v2/analytics/spu-roi?" in url]

    # --- 故障注入（只拦请求，不改数据）---

    def fail_spu_roi(self, detail: str = "E2E 注入故障") -> None:
        """让价格请求持续返回 500，直到 `clear_routes()`。真错误态 + 真重试。"""

        def handler(route: Any, request: Any) -> None:
            route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"detail": detail}),
            )

        self.page.route(SPU_ROI_API_PATTERN, handler)
        self._route_patterns.append(SPU_ROI_API_PATTERN)

    def clear_routes(self) -> None:
        for pattern in self._route_patterns:
            self.page.unroute(pattern)
        self._route_patterns.clear()

    # --- 收尾 ---

    def assert_read_only(self) -> None:
        assert not self.write_requests, f"价格页必须只读，却发出写请求: {self.write_requests}"

    def close(self) -> None:
        self.assert_read_only()
        with suppress(Exception):  # 收尾尽力而为
            self.context.close()


class LiveBrowser:
    """真实 Chromium 会话；每个页面一个 context（视口独立，鉴权两选一）。

    - `auth="api_key"`：context 带 readonly key 的 Bearer 头（原 leg）；
    - `auth="session"`：先在 context 里真实 `POST /v2/auth/login`（design §8.1
      gate 2）拿会话 cookie，**不**发 Authorization 头。
    """

    def __init__(self, browser: Any, base: str, scene: PriceScene) -> None:
        self._browser = browser
        self._base = base.rstrip("/")
        self._scene = scene
        self._pages: list[LivePage] = []
        # 最近一次真实登录响应体（用户名/角色/页面权限）；断言用它证明登录真发生了。
        self.last_login: dict[str, Any] | None = None

    def _login(self, context: Any) -> None:
        """在 context 上真实 POST /v2/auth/login；断言 200 + 会话 cookie 进了 context。"""
        response = context.request.post(
            f"{self._base}/v2/auth/login",
            data={
                "username": self._scene.login_username,
                "password": self._scene.login_credential,
            },
        )
        assert response.status == 200, (
            f"真实登录失败：HTTP {response.status} {response.text()}"
        )
        cookie_names = {cookie["name"] for cookie in context.cookies(self._base)}
        assert SESSION_COOKIE_NAME in cookie_names, (
            f"登录未下发会话 cookie：{sorted(cookie_names)}"
        )
        self.last_login = response.json()

    def new_page(
        self, *, auth: AuthMode = "api_key", width: int = 1440, height: int = 1200
    ) -> LivePage:
        context = self._browser.new_context(
            viewport={"width": width, "height": height}
        )
        if auth == "api_key":
            context.set_extra_http_headers(
                {"Authorization": f"Bearer {self._scene.api_key}"}
            )
        else:
            self._login(context)
        live_page = LivePage(context.new_page(), self._base, context)
        self._pages.append(live_page)
        return live_page

    def open(
        self,
        path: str,
        *,
        auth: AuthMode = "api_key",
        width: int = 1440,
        height: int = 1200,
    ) -> LivePage:
        return self.new_page(auth=auth, width=width, height=height).goto(path)

    def screenshot_all(self, directory: Path) -> list[Path]:
        """把已打开页面逐个截图写入 `directory`（失败留证，尽力而为）。"""
        shots: list[Path] = []
        for index, live_page in enumerate(self._pages, start=1):
            target = directory / f"failure-page-{index}.png"
            with suppress(Exception):
                live_page.page.screenshot(path=str(target), full_page=True)
            if target.exists():
                shots.append(target)
        return shots

    def close(self) -> None:
        for live_page in self._pages:
            live_page.close()
        self._pages.clear()


def spu_roi_path(shop_pk: int) -> str:
    return f"/v2/pages/spu-roi?shop_pk={shop_pk}"


def focused_spus_path(shop_pk: int) -> str:
    return f"/v2/pages/focused-spus?shop_pk={shop_pk}"
