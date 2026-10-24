"""One durable current health record per Seller Center shop identity."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class MonitorHealth(Base):
    __tablename__ = "monitor_health"
    __table_args__ = ({"schema": "plugin"},)

    # Seller Center oec_seller_id / seller_id; deliberately not commerce.shops.id.
    shop_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    shop_name: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False)
