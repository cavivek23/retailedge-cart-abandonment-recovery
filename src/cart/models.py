from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    Numeric,
    String,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, foreign, mapped_column, relationship


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class ValidatedIdentifier(TypeDecorator[str]):
    """Persist bounded identifiers after enforcing their canonical format."""

    impl = String
    cache_ok = True
    _pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not self._pattern.fullmatch(value):
            raise ValueError("identifier must be 1-128 canonical ASCII characters")
        return value


class Base(DeclarativeBase):
    """Declarative metadata root for cart recovery models."""


class CartStatus(str, Enum):
    """Lifecycle states for an abandoned cart."""

    PENDING_CHECKOUT = "PENDING_CHECKOUT"
    ABANDONED = "ABANDONED"
    RECOVERY_PENDING = "RECOVERY_PENDING"
    RECOVERED = "RECOVERED"
    EXPIRED = "EXPIRED"
    CLOSED = "CLOSED"


class RecoveryStatus(str, Enum):
    """Lifecycle states for a cart recovery workflow."""

    PENDING = "PENDING"
    VOUCHER_CREATED = "VOUCHER_CREATED"
    NOTIFICATION_SENT = "NOTIFICATION_SENT"
    RECOVERED = "RECOVERED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class DiscountType(str, Enum):
    """Supported voucher discount representations."""

    PERCENT = "PERCENT"
    FIXED_AMOUNT = "FIXED_AMOUNT"


class AnalyticsEventType(str, Enum):
    """Allowlisted event types emitted by the recovery workflow."""

    CART_ABANDONED = "CART_ABANDONED"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    VOUCHER_ISSUED = "VOUCHER_ISSUED"
    NOTIFICATION_SENT = "NOTIFICATION_SENT"
    ORDER_RECOVERED = "ORDER_RECOVERED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    RECOVERY_EXPIRED = "RECOVERY_EXPIRED"


class AbandonedCart(Base):
    """A store-scoped cart eligible for or participating in recovery."""

    __tablename__ = "abandoned_cart"
    __table_args__ = (
        CheckConstraint("length(currency) = 3 AND currency = upper(currency)", name="ck_cart_currency"),
        CheckConstraint("subtotal >= 0", name="ck_cart_subtotal_non_negative"),
        CheckConstraint("discount_total >= 0", name="ck_cart_discount_non_negative"),
        CheckConstraint("tax_total >= 0", name="ck_cart_tax_non_negative"),
        CheckConstraint("total >= 0", name="ck_cart_total_non_negative"),
        CheckConstraint("version > 0", name="ck_cart_version_positive"),
        Index("ix_cart_abandonment_scan", "store_id", "status", "last_activity_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    store_id: Mapped[str] = mapped_column(ValidatedIdentifier(128), nullable=False)
    customer_id: Mapped[str] = mapped_column(ValidatedIdentifier(128), nullable=False)
    status: Mapped[CartStatus] = mapped_column(
        SqlEnum(CartStatus, native_enum=False, create_constraint=True, validate_strings=True),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(19, 4, asdecimal=True), nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(19, 4, asdecimal=True), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(19, 4, asdecimal=True), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(19, 4, asdecimal=True), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now()
    )

    items: Mapped[list["CartItem"]] = relationship(
        back_populates="cart", cascade="all, delete-orphan", passive_deletes=True
    )
    recovery: Mapped["Recovery | None"] = relationship(
        back_populates="cart", uselist=False, cascade="all, delete-orphan", passive_deletes=True
    )
    analytics_events: Mapped[list["AnalyticsEvent"]] = relationship(back_populates="cart")


class CartItem(Base):
    """Immutable pricing snapshot for one product in an abandoned cart."""

    __tablename__ = "cart_item"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_cart_item_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_cart_item_unit_price_non_negative"),
        CheckConstraint("discount_total >= 0", name="ck_cart_item_discount_non_negative"),
        CheckConstraint("tax_total >= 0", name="ck_cart_item_tax_non_negative"),
        CheckConstraint("length(currency) = 3 AND currency = upper(currency)", name="ck_cart_item_currency"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    cart_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("abandoned_cart.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(ValidatedIdentifier(128), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3, asdecimal=True), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(19, 4, asdecimal=True), nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(19, 4, asdecimal=True), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(19, 4, asdecimal=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )

    cart: Mapped[AbandonedCart] = relationship(back_populates="items")


class Recovery(Base):
    """Store- and customer-scoped recovery workflow for one cart."""

    __tablename__ = "recovery"
    __table_args__ = (
        UniqueConstraint("cart_id", name="uq_recovery_cart"),
        CheckConstraint("attempt_count >= 0", name="ck_recovery_attempt_count_non_negative"),
        CheckConstraint("version > 0", name="ck_recovery_version_positive"),
        Index("ix_recovery_store_status", "store_id", "status", "updated_at", "id"),
        Index("ix_recovery_customer_cart", "store_id", "customer_id", "cart_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    cart_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("abandoned_cart.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[str] = mapped_column(ValidatedIdentifier(128), nullable=False)
    customer_id: Mapped[str] = mapped_column(ValidatedIdentifier(128), nullable=False)
    status: Mapped[RecoveryStatus] = mapped_column(
        SqlEnum(RecoveryStatus, native_enum=False, create_constraint=True, validate_strings=True),
        nullable=False,
    )
    voucher_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    notification_id: Mapped[str | None] = mapped_column(String(128))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    recovered_order_id: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now()
    )

    cart: Mapped[AbandonedCart] = relationship(back_populates="recovery")
    voucher: Mapped["Voucher | None"] = relationship(
        primaryjoin=lambda: Recovery.id == foreign(Voucher.recovery_id),
        foreign_keys=lambda: [Voucher.recovery_id],
        back_populates="recovery",
        uselist=False,
    )
    analytics_events: Mapped[list["AnalyticsEvent"]] = relationship(back_populates="recovery")


class Voucher(Base):
    """A recovery voucher persisted without plaintext redemption material."""

    __tablename__ = "voucher"
    __table_args__ = (
        UniqueConstraint("recovery_id", name="uq_voucher_recovery"),
        CheckConstraint("length(code_hash) > 0", name="ck_voucher_code_hash_present"),
        CheckConstraint("code_last4 IS NULL OR length(code_last4) = 4", name="ck_voucher_code_last4"),
        CheckConstraint(
            "(discount_type = 'PERCENT' AND discount_value > 0 AND discount_value <= 100)"
            " OR (discount_type = 'FIXED_AMOUNT' AND discount_value > 0 AND currency IS NOT NULL)",
            name="ck_voucher_discount_range",
        ),
        CheckConstraint("minimum_cart_total IS NULL OR minimum_cart_total >= 0", name="ck_voucher_minimum_non_negative"),
        Index("ix_voucher_store_expiry", "store_id", "expires_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    recovery_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recovery.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[str] = mapped_column(ValidatedIdentifier(128), nullable=False)
    code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    code_last4: Mapped[str | None] = mapped_column(String(4))
    discount_type: Mapped[DiscountType] = mapped_column(
        SqlEnum(DiscountType, native_enum=False, create_constraint=True, validate_strings=True),
        nullable=False,
    )
    discount_value: Mapped[Decimal] = mapped_column(Numeric(19, 4, asdecimal=True), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3))
    minimum_cart_total: Mapped[Decimal | None] = mapped_column(Numeric(19, 4, asdecimal=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )

    recovery: Mapped[Recovery] = relationship(back_populates="voucher")


class AnalyticsEvent(Base):
    """Allowlisted, idempotent event for recovery analytics."""

    __tablename__ = "analytics_event"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_analytics_event_key"),
        CheckConstraint("amount IS NULL OR amount >= 0", name="ck_analytics_amount_non_negative"),
        CheckConstraint("amount IS NULL OR currency IS NOT NULL", name="ck_analytics_amount_currency"),
        CheckConstraint(
            "currency IS NULL OR (length(currency) = 3 AND currency = upper(currency))",
            name="ck_analytics_currency",
        ),
        Index("ix_analytics_store_time", "store_id", "occurred_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    event_key: Mapped[str] = mapped_column(ValidatedIdentifier(128), nullable=False)
    store_id: Mapped[str] = mapped_column(ValidatedIdentifier(128), nullable=False)
    event_type: Mapped[AnalyticsEventType] = mapped_column(
        SqlEnum(AnalyticsEventType, native_enum=False, create_constraint=True, validate_strings=True),
        nullable=False,
    )
    cart_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("abandoned_cart.id", ondelete="SET NULL")
    )
    recovery_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recovery.id", ondelete="SET NULL")
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    currency: Mapped[str | None] = mapped_column(String(3))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4, asdecimal=True))
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)

    cart: Mapped[AbandonedCart | None] = relationship(back_populates="analytics_events")
    recovery: Mapped[Recovery | None] = relationship(back_populates="analytics_events")