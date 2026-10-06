from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import AnalyticsEventType, DiscountType, RecoveryStatus


Identifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]
Money = Annotated[Decimal, Field(ge=0, max_digits=19, decimal_places=4)]


class ScanRequest(BaseModel):
    """Validated input for one bounded abandonment scan batch."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    batch_size: Annotated[int, Field(alias="batchSize", ge=1, le=1000)] = 100
    cursor: str | None = None


class CartSummary(BaseModel):
    """Redacted cart data safe for notification providers."""

    model_config = ConfigDict(extra="forbid")
    cart_id: UUID
    item_count: Annotated[int, Field(ge=0)]
    total: Money
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class VoucherDelivery(BaseModel):
    """Non-secret voucher metadata for notification delivery."""

    model_config = ConfigDict(extra="forbid")
    voucher_id: UUID
    discount_type: DiscountType
    discount_value: Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=4)]
    currency: str | None
    expires_at: datetime
    delivery_data: dict[str, str] | None = None

    @field_validator("discount_value", mode="before")
    @classmethod
    def reject_float_discount(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("discount_value must be a Decimal")
        return value


class ScanResponse(BaseModel):
    """Result of a single independently committed scan batch."""

    processed_count: int = Field(alias="processedCount", ge=0)
    eligible_count: int = Field(alias="eligibleCount", ge=0)
    started_count: int = Field(alias="startedCount", ge=0)
    failed_count: int = Field(alias="failedCount", ge=0)
    next_cursor: str | None = Field(alias="nextCursor")
    has_more: bool = Field(alias="hasMore")

    model_config = ConfigDict(populate_by_name=True)


class OrderCompletedEvent(BaseModel):
    """Authenticated order completion event consumed by recovery tracking."""

    model_config = ConfigDict(extra="forbid")
    event_id: Identifier
    order_id: Identifier
    store_id: Identifier
    customer_id: Identifier
    cart_id: UUID
    completed_at: datetime
    amount: Money
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]

    @field_validator("amount", mode="before")
    @classmethod
    def reject_float_amount(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("amount must be a Decimal")
        return value


class RecoveryResponse(BaseModel):
    """Public recovery response with no ORM or secret fields."""

    recovery_id: UUID
    cart_id: UUID
    status: RecoveryStatus
    voucher: VoucherDelivery | None
    notification_id: str | None
    recovered_at: datetime | None
    updated_at: datetime


class AnalyticsFilters(BaseModel):
    """Validated, bounded recovery analytics query."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    start: datetime = Field(alias="from")
    end: datetime = Field(alias="to")
    status: RecoveryStatus | None = None
    cursor: str | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    group_by_currency: bool = Field(default=False, alias="groupByCurrency")

    @field_validator("end")
    @classmethod
    def validate_range(cls, value: datetime, info: Any) -> datetime:
        start = info.data.get("start")
        if start is not None and value <= start:
            raise ValueError("to must be after from")
        return value


class AnalyticsMetrics(BaseModel):
    """Decimal-safe aggregate recovery metrics."""

    abandoned_count: int = Field(alias="abandonedCount", ge=0)
    recovery_started_count: int = Field(alias="recoveryStartedCount", ge=0)
    recovered_count: int = Field(alias="recoveredCount", ge=0)
    conversion_rate: Decimal = Field(alias="conversionRate")
    recovered_amount: Decimal = Field(alias="recoveredAmount")
    currency: str | None

    model_config = ConfigDict(populate_by_name=True)


class AnalyticsResponse(BaseModel):
    """Paginated store-scoped recovery analytics response."""

    metrics: AnalyticsMetrics | dict[str, AnalyticsMetrics]
    next_cursor: str | None = Field(alias="nextCursor")
    has_more: bool = Field(alias="hasMore")

    model_config = ConfigDict(populate_by_name=True)