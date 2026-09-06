"""mirror_object_key — add products_spu.mirror_object_key (migration 0009)

Revision ID: 0009_add_mirror_object_key
Revises: 0007_analytics_reorg
Create Date: 2026-09-05

2026-09-05 页面改造（spu-image-mirror lane）：manual-costs 页不再人工上传
图片（供应商参考图流程废弃），改展示 get product 同步的 TikTok 主图
（``products_spu.main_image_url``）的**本地 MinIO 镜像**——前端渲染走
自建 MinIO，避免每次页面打开都直连 TikTok CDN。

存储设计（用户拍板：不做状态机 / 不做多行历史 / 不做源哈希列）：

- 只在 ``commerce.products_spu`` 加一列 ``mirror_object_key text NULL``。
- object key 布局 ``mirror/<spu_pk>/<sha1(main_image_url)[:16]>.jpg``：
  key 内嵌源 URL 哈希 → **key 本身就是判重依据**（URL 变了 → 哈希变 →
  key 变 → job 重新镜像；URL 没变 → key 相同 → 跳过）。不需要单独的
  ``status`` 状态列（镜像 job 是系统原子操作：下载→上传→update 一列，
  不存在浏览器上传的 awaiting_upload→confirm 状态机）。
- 失败不落状态：下载失败写 SyncIssue，``mirror_object_key`` 保持旧值/
  NULL，下一轮 job 自动重试。
- 只存 object key 不存 URL：读取端（missing-cost-products / spu-images
  系）在响应时把 key 解析成 presigned / public URL，MinIO 访问配置可
  独立演进。

downgrade 仅删列（镜像对象留在 MinIO，数据不删——与 0007 同注释约定：
down 只保证 schema 可回滚，不保证数据）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Column, Text

revision: str = "0009_add_mirror_object_key"
down_revision: str | None = "0007_analytics_reorg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products_spu",
        Column("mirror_object_key", Text, nullable=True),
        schema="commerce",
    )


def downgrade() -> None:
    op.drop_column(
        "products_spu",
        "mirror_object_key",
        schema="commerce",
    )
