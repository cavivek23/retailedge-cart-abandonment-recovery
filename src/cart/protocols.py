from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol, Sequence
from uuid import UUID

from .models import AnalyticsEvent, AnalyticsEventType, Recovery, RecoveryStatus
from .schemas import CartSummary, VoucherDelivery


class AuthenticatedPrincipalProtocol(Protocol):
    """Minimum authenticated context required by cart services."""

    store_id: str
    customer_id: str
    role: str
    scopes: frozenset[str]


class NotificationServiceProtocol(Protocol):
    """Provider boundary for redacted recovery notifications."""

    def send_recovery_notification(
        self,
        summary: CartSummary,
        voucher: VoucherDelivery | None,
        idempotency_key: str,
    ) -> str:
        """Send or reuse a notification and return its provider reference."""


class VoucherServiceProtocol(Protocol):
    """Provider boundary for one-time voucher issuance."""

    def get_or_create_voucher(
        self,
        store_id: str,
        recovery_id: UUID,
        currency: str,
        cart_total: Decimal,
        idempotency_key: str,
    ) -> VoucherDelivery:
        """Return an existing voucher or issue one idempotently."""


class AnalyticsServiceProtocol(Protocol):
    """Provider boundary for sanitized analytics events."""

    def record_event(self, event: AnalyticsEvent) -> None:
        """Record an allowlisted event without sensitive payloads."""


class CartRepositoryProtocol(Protocol):
    """Persistence contract consumed by the cart services."""

    def get_eligible_carts(
        self,
        store_id: str,
        cutoff: datetime,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> Sequence[object]:
        """Return one latest eligible cart per customer in stable order."""

    def get_recovery(self, store_id: str, cart_id: UUID) -> Recovery | None:
        """Return a store-scoped recovery by cart."""

    def get_recovery_for_update(self, store_id: str, cart_id: UUID) -> Recovery | None:
        """Return a store-scoped recovery for an optimistic update."""

    def get_event(self, event_key: str) -> AnalyticsEvent | None:
        """Return an idempotency event by key."""

    def create_recovery(self, store_id: str, customer_id: str, cart_id: UUID) -> Recovery:
        """Create a pending recovery and flush its generated identifier."""

    def update_recovery(
        self,
        recovery: Recovery,
        expected_version: int,
        status: RecoveryStatus,
        **values: object,
    ) -> Recovery:
        """Apply a version-conditional recovery mutation."""

    def add_event(self, event: AnalyticsEvent) -> None:
        """Stage an analytics event for the current transaction."""

    def list_events(
        self,
        store_id: str,
        start: datetime,
        end: datetime,
        status: RecoveryStatus | None,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> Sequence[AnalyticsEvent]:
        """Return a bounded, store-scoped analytics page."""

    def commit(self) -> None:
        """Commit the current batch transaction."""

    def rollback(self) -> None:
        """Roll back the current batch transaction."""