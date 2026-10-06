from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Inventory(Base):
    """Stock for one product within one store."""

    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("store_id", "product_id", name="uq_inventory_store_product"),
        CheckConstraint("stock >= 0", name="ck_inventory_stock_non_negative"),
        CheckConstraint("version > 0", name="ck_inventory_version_positive"),
        Index("ix_inventory_store_product", "store_id", "product_id"),
        Index("ix_inventory_store_updated", "store_id", "updated_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), nullable=False)
    product_id: Mapped[str] = mapped_column(String(128), nullable=False)
    stock: Mapped[Decimal] = mapped_column(
        Numeric(18, 3, asdecimal=True), nullable=False, default=Decimal("0")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class InventoryMutation(Base):
    """Idempotency record for an externally retried stock mutation."""

    __tablename__ = "inventory_mutation"
    operation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    inventory_id: Mapped[int] = mapped_column(Integer, nullable=False)
    store_id: Mapped[str] = mapped_column(String(128), nullable=False)
    product_id: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(18, 3, asdecimal=True), nullable=False)
    resulting_stock: Mapped[Decimal] = mapped_column(Numeric(18, 3, asdecimal=True), nullable=False)
    resulting_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("resulting_stock >= 0", name="ck_mutation_stock_non_negative"),
        Index("ix_mutation_store_product", "store_id", "product_id"),
    )