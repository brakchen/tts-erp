"""给 tech-doc/enums/ 下每个枚举文件的"取值"表加"等级"列。

知识库：本文件顶部的 PER_FILE_STATUS dict 定义每个文件的每个值的等级。
运行：python scripts/annotate_enums.py [--dry-run]  [--files <globs>]

约定：
  ✅ = 已固化（在 db/constants.py / CheckConstraint / 上游官方枚举）
  🟡 = 实测（生产数据有样本，但代码层未固化）
  🔴 = 未观测（理论上存在但本项目无样本）

【CONTRACT】这个脚本不"猜"——遇到 PER_FILE_STATUS 没列出的具体值报错，让作者手填。
占位/注释行（以"（"开头 或 裸中文短词）自动标 🔴。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ENUM_DIR = Path(__file__).resolve().parent.parent / "tech-doc" / "enums"

# ─────────────────────────────────────────────────────────────────────
# 知识库：每个文件 → 列表 of (值字符串, 等级)
# "值字符串" 必须和 markdown 表里"取值"那行的"值/码/文本"列精确匹配
# ─────────────────────────────────────────────────────────────────────

PER_FILE_STATUS: dict[str, list[tuple[str, str]]] = {
    # ─── 完全固化（✅ 全部）───
    "order-status.md": [
        ("`UNPAID`", "✅"), ("`ON_HOLD`", "✅"), ("`AWAITING_SHIPMENT`", "✅"),
        ("`PARTIAL_SHIPPING`", "✅"), ("`AWAITING_COLLECTION`", "✅"),
        ("`IN_TRANSIT`", "✅"), ("`DELIVERED`", "✅"),
        ("`COMPLETED`", "✅"), ("`CANCELLED`", "✅"),
    ],
    "product-status.md": [
        ("`ACTIVATE`", "✅"), ("`DEACTIVATE`", "✅"), ("`DELETED`", "✅"),
        ("`SUSPENDED`", "✅"), ("`ARCHIVED`", "✅"),
    ],
    "case-type.md": [
        ("`CANCELLATION`", "✅"), ("`REFUND_ONLY`", "✅"),
        ("`RETURN_AND_REFUND`", "✅"),
    ],
    "product-link-relation-type.md": [
        ("`MIAOSHOU_PUBLISHED_TO_TIKTOK`", "✅"),
        ("`MIAOSHOU_BOUND_TO_TIKTOK`", "✅"),
        ("`MIAOSHOU_PROCUREMENT_SOURCE`", "✅"),
    ],
    "link-override-decision.md": [
        ("`ALLOW`", "✅"), ("`DENY`", "✅"), ("`PRIMARY`", "✅"),
    ],
    "link-issue-type.md": [
        ("`PRODUCT_LINK_MISSING`", "✅"),
        ("`MULTIPLE_PRIMARY_LINKS`", "✅"),
        ("`SOURCE_LINK_CONFLICT`", "✅"),
        ("`ACCOUNT_LINK_MISSING`", "✅"),
        ("`VARIANT_LINK_MISSING`", "✅"),
        ("`AMBIGUOUS_SOURCE`", "✅"),
    ],
    "cost-method.md": [
        ("`MANUAL_ENTRY`", "✅"), ("`LATEST_PURCHASE_COST`", "✅"),
        ("`PERIOD_AVERAGE_COST`", "✅"), ("`WEIGHTED_AVERAGE_COST`", "✅"),
        ("`SOURCE_PRICE`", "✅"),
    ],
    "procurement-product-type.md": [
        ("`COLLECTED_PRODUCT`", "✅"), ("`PROCUREMENT_PRODUCT`", "✅"),
        ("`SPU`", "✅"),
    ],
    "intercept-mode.md": [
        ("`whitelist`", "✅"), ("`blacklist`", "✅"),
    ],
    "ad-raw-log-kind.md": [
        ("`daily`", "✅"), ("`today`", "✅"), ("`monthly`", "✅"),
    ],
    "plugin-log-level.md": [
        ("`info`", "✅"), ("`warn`", "✅"), ("`error`", "✅"),
    ],
    "sync-job-status.md": [
        ("`running`", "✅"), ("`succeeded`", "✅"), ("`failed`", "✅"),
    ],
    "api-key-role.md": [
        ("`readonly`", "✅"), ("`readwrite`", "✅"), ("`admin`", "✅"),
    ],
    "provider.md": [
        ("`tiktok`", "✅"), ("`miaoshou`", "✅"),
    ],
    "platform.md": [
        ("`tiktok`", "✅"),
    ],
    "logistics-terminal-codes.md": [
        ("`50101`", "✅"), ("`80101`", "✅"), ("`110101`", "✅"),
    ],
    "cancel-type.md": [
        ("`BUYER_CANCEL`", "✅"), ("`CANCEL`", "✅"),
    ],

    # ─── 部分未固化：seller_center int (🟡 全部)───
    "main-order-status.md": [
        ("`100`", "🟡"), ("`101`", "🟡"), ("`102`", "🟡"),
        ("`103`", "🟡"), ("`104`", "🟡"),
    ],
    "sku-display-status.md": [
        ("`100`", "🟡"), ("`111`", "🟡"), ("`112`", "🟡"),
        ("`121`", "🟡"), ("`122`", "🟡"),
        ("`130`", "🟡"), ("`140`", "🟡"),
    ],
    "fulfillment-type.md": [
        ("`FULFILLMENT_BY_SELLER`", "✅"),
        ("`FULFILLMENT_BY_TIKTOK`", "🔴"),
        ("`0`", "🟡"),
    ],
    "reverse-type.md": [
        ("`1`", "🟡"), ("`3`", "🟡"), ("`4`", "🟡"),
        ("`2`", "🔴"), ("`0`", "🔴"), ("`5+`", "🔴"),
    ],
    "reverse-status.md": [
        ("`4`", "🟡"), ("`100`", "🟡"),
        ("`0`", "🔴"), ("`1`", "🔴"),
        ("`2`", "🔴"), ("`3`", "🔴"), ("`5+`", "🔴"),
    ],

    # ─── 部分未固化：观察 + 推断混合───
    "action-code.md": [
        ("`10101`", "🟡"), ("`20101`", "🟡"),
        ("`30201`", "🟡"), ("`30301`", "🟡"), ("`30401`", "🟡"),
        ("`30501`", "🟡"), ("`31701`", "🟡"), ("`38701`", "🟡"),
        ("`34301`", "🟡"),
        ("`38301`", "🟡"),
        ("`34701`", "🟡"), ("`30801`", "🟡"),
        ("`31201`", "🟡"), ("`31301`", "🟡"), ("`31401`", "🟡"),
        ("`32401`", "🟡"), ("`32601`", "🟡"),
        ("`40101`", "🟡"), ("`40501`", "🟡"),
        ("`40601`", "🟡"),
        ("`70201`", "🟡"),
        ("`80101`", "🟡"), ("`110101`", "🟡"),
        ("`50101`", "🟡"),
    ],
    "cancel-status.md": [
        ("`CANCELLATION_REQUEST_COMPLETE`", "🟡"),
        ("`CANCELLATION_REQUEST_PENDING`", "🔴"),
        ("`CANCELLATION_REQUEST_REJECTED`", "🔴"),
    ],
    "cancel-reason.md": [
        ("`'returned_to_shipper_other'`", "🟡"),
        ("`'客户超时支付'`", "🟡"),
        ("`'不想要了'`", "🟡"),
        ("`'发现更优惠的价格'`", "🟡"),
        ("`'需要更改收货地址'`", "🟡"),
        ("`'需要更改付款方式'`", "🟡"),
        ("`'预计送达时间过晚'`", "🟡"),
        ("`'商品与描述不符'`", "🟡"),
        ("`'改变主意'`", "🟡"),
        ("`'商品太大或太小'`", "🟡"),
    ],
    "settlement-status.md": [
        ("`2`", "🟡"),
    ],
    "payment-status.md": [
        ("`1`", "🟡"),
    ],
    "api-key-status.md": [
        ("`active`", "🟡"),
        ("`revoked`", "🔴"), ("`suspended`", "🔴"), ("`expired`", "🔴"),
    ],
    "pay-method.md": [
        ("`\"Cash on delivery\"`", "🟡"),
        ("`\"Zalopay\"`", "🟡"),
    ],
    "procurement-products-status.md": [
        ("`success`", "🟡"), ("`fail`", "🟡"), ("`skip`", "🟡"),
        ("`active`", "🟡"),
        ("`normal`", "🟡"),
        ("`NULL`", "🟡"),
    ],
    "sale-region.md": [
        ("`VN`", "✅"),
        ("`TH`", "🔴"), ("`PH`", "🔴"), ("`MY`", "🔴"),
        ("`SG`", "🔴"), ("`ID`", "🔴"),
    ],
    "region.md": [
        ("`VN`", "🟡"),
    ],
    "currency.md": [
        ("`VND`", "🟡"), ("`USD`", "🟡"),
        ("`THB`", "🟡"), ("`PHP`", "🟡"), ("`MYR`", "🟡"),
        ("`SGD`", "🟡"), ("`IDR`", "🟡"),
        ("`CNY`", "🔴"), ("`EUR`", "🔴"),
    ],
    "track-status.md": [
        ("`Package picked up`", "🟡"),
        ("`Delivered`", "🟡"),
    ],

    # ─── 完全未观测（🔴 全部）───
    "statement-type.md": [],
    "payment-pending-reason.md": [],
    "line-status.md": [],

    # ─── 53 EAV 全部固化───
    "settlement-component-code.md": [
        ("`ACTUAL_RETURN_SHIPPING_FEE`", "✅"),
        ("`ACTUAL_SHIPPING_FEE`", "✅"),
        ("`ADJUSTMENT`", "✅"),
        ("`AFFILIATE_ADS_COMMISSION`", "✅"),
        ("`AFFILIATE_COMMISSION`", "✅"),
        ("`AFFILIATE_COMMISSION_BEFORE_PIT`", "✅"),
        ("`AFFILIATE_PARTNER_COMMISSION`", "✅"),
        ("`AFTER_SELLER_DISCOUNTS_SUBTOTAL`", "✅"),
        ("`CUSTOMER_ORDER_REFUND`", "✅"),
        ("`CUSTOMER_PAID_SHIPPING_FEE`", "✅"),
        ("`CUSTOMER_PAID_SHIPPING_FEE_REFUND`", "✅"),
        ("`CUSTOMER_PAYMENT`", "✅"),
        ("`CUSTOMER_REFUND`", "✅"),
        ("`CUSTOMER_SHIPPING_FEE`", "✅"),
        ("`CUSTOMER_SHIPPING_FEE_OFFSET`", "✅"),
        ("`FBM_SHIPPING_COST`", "✅"),
        ("`FBT_FULFILLMENT_FEE`", "✅"),
        ("`FBT_FULFILLMENT_FEE_REIMBURSEMENT`", "✅"),
        ("`FBT_SHIPPING_COST`", "✅"),
        ("`FEE`", "✅"),
        ("`GROSS_SALES`", "✅"),
        ("`GROSS_SALES_REFUND`", "✅"),
        ("`ISR_INCOME_TAX`", "✅"),
        ("`IVA_VAT`", "✅"),
        ("`NET_SALES`", "✅"),
        ("`PIT`", "✅"),
        ("`PLATFORM_COMMISSION`", "✅"),
        ("`PLATFORM_DISCOUNT`", "✅"),
        ("`PLATFORM_DISCOUNT_REFUND`", "✅"),
        ("`PLATFORM_REFUND_SUBSIDY`", "✅"),
        ("`PLATFORM_SHIPPING_FEE_DISCOUNT`", "✅"),
        ("`PROMO_SHIPPING_INCENTIVE`", "✅"),
        ("`REFERRAL_FEE`", "✅"),
        ("`REFUND_ADMINISTRATION_FEE`", "✅"),
        ("`REFUND_SHIPPING_COST_DISCOUNT`", "✅"),
        ("`RETAIL_DELIVERY_FEE`", "✅"),
        ("`RETAIL_DELIVERY_FEE_PAYMENT`", "✅"),
        ("`RETAIL_DELIVERY_FEE_REFUND`", "✅"),
        ("`RETURN_SHIPPING_FEE`", "✅"),
        ("`REVENUE`", "✅"),
        ("`SALES_TAX`", "✅"),
        ("`SALES_TAX_PAYMENT`", "✅"),
        ("`SALES_TAX_REFUND`", "✅"),
        ("`SELLER_DISCOUNT`", "✅"),
        ("`SELLER_DISCOUNT_REFUND`", "✅"),
        ("`SETTLEMENT`", "✅"),
        ("`SHIPPING_COST`", "✅"),
        ("`SHIPPING_COST_DISCOUNT`", "✅"),
        ("`SHIPPING_FEE`", "✅"),
        ("`SHIPPING_FEE_SUBSIDY`", "✅"),
        ("`SHIPPING_INSURANCE_FEE`", "✅"),
        ("`SIGNATURE_CONFIRMATION_FEE`", "✅"),
        ("`TRANSACTION_FEE`", "✅"),
    ],
}


# ─────────────────────────────────────────────────────────────────────
# 处理：扫描所有"##/### 取值"段，给每张表加"等级"首列
# ─────────────────────────────────────────────────────────────────────

TAKE_VALUES_HEADING_RE = re.compile(r"^(#{1,3})\s*(.*?取值|实测样本|已知值|实测)")


def is_placeholder(value: str) -> bool:
    """占位/注释行：值以"（"开头（如"（其他）"），或裸中文短词（"其他" "待补" "待发现"）。"""
    if value.startswith("（"):
        return True
    if value.startswith("`") or value.startswith('"'):
        return False
    if 0 < len(value) <= 6 and any('\u4e00' <= c <= '\u9fff' for c in value):
        return True
    return False


def process_file(path: Path, status_pairs: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """返回 (新内容, 警告列表)。失败抛 ValueError 让作者手填。

    逻辑：扫描全文，对所有 "##/### 取值/实测样本/已知值/实测" 段下的子表都加上
    "等级"首列。遇同级或更高 ## 段退出当前子扫描。
    """
    text = path.read_text()
    warnings: list[str] = []
    expected = dict(status_pairs)
    used: set[str] = set()
    lines = text.split("\n")
    new_lines = list(lines)
    edits_made = False

    i = 0
    while i < len(new_lines):
        line = new_lines[i]
        m = TAKE_VALUES_HEADING_RE.match(line)
        if not m:
            i += 1
            continue
        take_level = len(m.group(1))
        # 从 i+1 开始扫描，遇 >= take_level 的 heading 退出
        j = i + 1
        while j < len(new_lines):
            sub = new_lines[j]
            sub_m = re.match(r"^(#{1,3})\s", sub)
            if sub_m and len(sub_m.group(1)) <= take_level:
                # 同级或更高级 heading，退出该段的处理
                break
            # 找 sub 段下的表
            if sub.startswith("|"):
                # 当前是表行，找表头
                # 往上找最近的表头
                header_idx = j
                while header_idx > i and not (
                    new_lines[header_idx].startswith("|")
                    and not new_lines[header_idx].startswith("|---")
                ):
                    header_idx -= 1
                if header_idx <= i:
                    j += 1
                    continue
                header = new_lines[header_idx]
                if "等级" in header:
                    # 已有等级列，跳过整张表
                    while j < len(new_lines) and new_lines[j].startswith("|"):
                        j += 1
                    continue
                # 注入等级首列
                new_lines[header_idx] = "| 等级 | " + header[1:]
                # 找 separator 行
                sep_idx = header_idx + 1
                if sep_idx < len(new_lines) and new_lines[sep_idx].startswith("|---"):
                    new_lines[sep_idx] = "| :---: |" + new_lines[sep_idx][1:]
                # 处理数据行
                k = sep_idx + 1
                while k < len(new_lines) and new_lines[k].startswith("|"):
                    line_text = new_lines[k]
                    stripped = line_text.strip()
                    if not stripped or stripped.startswith("|--"):
                        k += 1
                        continue
                    cols = [c.strip() for c in stripped.split("|") if c.strip() != ""]
                    if not cols:
                        k += 1
                        continue
                    value = cols[0]
                    if is_placeholder(value):
                        new_lines[k] = "| 🔴 | " + line_text[1:]
                        edits_made = True
                        k += 1
                        continue
                    if value not in expected:
                        raise ValueError(
                            f"{path.name}: 行 {k + 1} 的值 {value!r} 不在 PER_FILE_STATUS 知识库中。"
                            f"请在 scripts/annotate_enums.py 顶部 dict 里补登记（不能猜）。"
                        )
                    if value in used:
                        raise ValueError(f"{path.name}: 值 {value!r} 重复出现")
                    used.add(value)
                    status = expected[value]
                    new_lines[k] = f"| {status} | " + line_text[1:]
                    edits_made = True
                    k += 1
                j = k
                continue
            j += 1
        i = j
        continue

    if not edits_made:
        return text, warnings
    new_text = "\n".join(new_lines)
    missing = set(expected.keys()) - used
    if missing:
        warnings.append(
            f"{path.name}: 知识库登记了但 markdown 表里没出现: {sorted(missing)}"
        )
    return new_text, warnings


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    only = []
    if "--files" in sys.argv:
        idx = sys.argv.index("--files")
        only = sys.argv[idx + 1:]
    files_processed = 0
    files_with_warnings = 0
    total_warnings = 0
    for filename, pairs in sorted(PER_FILE_STATUS.items()):
        if only and not any(filename.startswith(p) for p in only):
            continue
        path = ENUM_DIR / filename
        if not path.exists():
            print(f"⚠️  {filename}: 文件不存在，跳过")
            continue
        try:
            new_text, warnings = process_file(path, pairs)
        except ValueError as e:
            print(f"❌ {e}")
            return 1
        if warnings:
            files_with_warnings += 1
            total_warnings += len(warnings)
            for w in warnings:
                print(f"⚠️  {w}")
        if dry_run:
            print(f"🔍 [dry-run] {filename}: 即将写入 {len(pairs)} 行等级标注")
        else:
            path.write_text(new_text)
            print(f"✓  {filename}: 写入 {len(pairs)} 行等级")
        files_processed += 1
    print(f"\n=== 总结 ===")
    print(f"处理文件: {files_processed}")
    print(f"含警告文件: {files_with_warnings}")
    print(f"警告总数: {total_warnings}")
    if dry_run:
        print("(dry-run 模式，未实际写文件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
