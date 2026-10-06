from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Iterable
from uuid import UUID, uuid4

from .models import AnalyticsEvent, AnalyticsEventType, CartStatus, Recovery, RecoveryStatus
from .protocols import (
    AnalyticsServiceProtocol,
    AuthenticatedPrincipalProtocol,
    CartRepositoryProtocol,
    NotificationServiceProtocol,
    VoucherServiceProtocol,
)
from .schemas import (
    AnalyticsFilters,
    AnalyticsMetrics,
    AnalyticsResponse,
    CartSummary,
    OrderCompletedEvent,
    RecoveryResponse,
    ScanResponse,
    VoucherDelivery,
)
from .repositories import RepositoryConflictError


ABANDONMENT_WINDOW = timedelta(minutes=30)
RECOVERY_WINDOW = timedelta(hours=24)
MONEY_QUANTUM = Decimal("0.0001")


class CartServiceError(Exception):
    """Base error with a stable machine-readable service code."""

    code = "cart_service_error"


class CartAuthorizationError(CartServiceError):
    """Raised when a principal lacks store, customer, or role access."""

    code = "cart_forbidden"


class CartNotFoundError(CartServiceError):
    """Raised when a recovery is not visible in the principal's store."""

    code = "cart_recovery_not_found"


class CursorValidationError(CartServiceError):
    """Raised when a cursor is invalid or belongs to another store."""

    code = "invalid_cursor"


class MixedCurrencyError(CartServiceError):
    """Raised when analytics would aggregate incompatible currencies."""

    code = "mixed_currency_aggregation"


class RecoveryConflictError(CartServiceError):
    """Raised when an optimistic recovery update loses a race."""

    code = "recovery_conflict"


@dataclass(frozen=True)
class CursorCodec:
    """Encode integrity-protected, store-bound pagination cursors."""

    secret: bytes

    @classmethod
    def from_environment(cls) -> "CursorCodec":
        value = os.environ.get("CURSOR_SIGNING_KEY")
        if not value:
            raise RuntimeError("CURSOR_SIGNING_KEY is required for cursor generation")
        return cls(value.encode("utf-8"))

    def encode(self, store_id: str, position: tuple[datetime, UUID]) -> str:
        timestamp, record_id = position
        payload = json.dumps(
            {"s": store_id, "t": timestamp.astimezone(timezone.utc).isoformat(), "i": str(record_id)},
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self.secret, encoded, hashlib.sha256).digest()
        return f"{encoded.decode("ascii")}.{base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")}"

    def decode(self, cursor: str, store_id: str) -> tuple[datetime, UUID]:
        try:
            encoded, encoded_signature = cursor.split(".", 1)
            expected = hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
            supplied = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
            if not hmac.compare_digest(expected, supplied):
                raise ValueError("signature mismatch")
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if payload["s"] != store_id:
                raise ValueError("store mismatch")
            timestamp = datetime.fromisoformat(payload["t"]).astimezone(timezone.utc)
            return timestamp, UUID(payload["i"])
        except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise CursorValidationError("Cursor is invalid") from error


def _require_scope(principal: AuthenticatedPrincipalProtocol, scope: str) -> None:
    if scope not in principal.scopes:
        raise CartAuthorizationError("Required scope is missing")


def _require_admin(principal: AuthenticatedPrincipalProtocol, scope: str) -> None:
    if principal.role != "admin" or scope not in principal.scopes:
        raise CartAuthorizationError("Administrator permission is required")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _event(
    event_type: AnalyticsEventType,
    event_key: str,
    store_id: str,
    cart_id: UUID | None = None,
    recovery_id: UUID | None = None,
    occurred_at: datetime | None = None,
    currency: str | None = None,
    amount: Decimal | None = None,
) -> AnalyticsEvent:
    return AnalyticsEvent(
        event_key=event_key,
        store_id=store_id,
        event_type=event_type,
        cart_id=cart_id,
        recovery_id=recovery_id,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        currency=currency,
        amount=amount,
        event_metadata=None,
    )


class AbandonmentScanService:
    """Scan abandoned carts and start idempotent recovery workflows."""

    def __init__(
        self,
        repository: CartRepositoryProtocol,
        voucher_service: VoucherServiceProtocol,
        notification_service: NotificationServiceProtocol,
        analytics_service: AnalyticsServiceProtocol,
        voucher_threshold: Decimal,
        cursor_codec: CursorCodec | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.voucher_service = voucher_service
        self.notification_service = notification_service
        self.analytics_service = analytics_service
        self.voucher_threshold = voucher_threshold
        self.cursor_codec = cursor_codec
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def scan(
        self,
        principal: AuthenticatedPrincipalProtocol,
        batch_size: int,
        cursor: str | None = None,
    ) -> ScanResponse:
        """Process one store-scoped batch and return its restart cursor."""
        _require_admin(principal, "recovery:write")
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        position = None
        if cursor is not None:
            position = self._codec().decode(cursor, principal.store_id)
        now = _utc(self.clock())
        carts = list(
            self.repository.get_eligible_carts(
                principal.store_id, now - ABANDONMENT_WINDOW, position, batch_size
            )
        )
        has_more = len(carts) > batch_size
        batch = carts[:batch_size]
        started_count = 0
        failed_count = 0
        try:
            for cart in batch:
                if cart.status is CartStatus.PENDING_CHECKOUT:
                    cart.status = CartStatus.ABANDONED
                if cart.abandoned_at is None:
                    cart.abandoned_at = now
                recovery = self.repository.get_recovery(principal.store_id, cart.id)
                if recovery is None:
                    try:
                        recovery = self.repository.create_recovery(
                            principal.store_id, cart.customer_id, cart.id
                        )
                    except RepositoryConflictError:
                        recovery = self.repository.get_recovery(principal.store_id, cart.id)
                        if recovery is None:
                            raise
                if recovery.status in {
                    RecoveryStatus.RECOVERED,
                    RecoveryStatus.EXPIRED,
                }:
                    continue
                self._record_event(
                    _event(
                        AnalyticsEventType.RECOVERY_STARTED,
                        f"recovery-started:{recovery.id}",
                        principal.store_id,
                        cart.id,
                        recovery.id,
                    )
                )
                voucher = None
                if cart.total >= self.voucher_threshold:
                    voucher = self.voucher_service.get_or_create_voucher(
                        principal.store_id,
                        recovery.id,
                        cart.currency,
                        cart.total,
                        f"voucher:{recovery.id}",
                    )
                    if recovery.voucher_id is None:
                        recovery = self.repository.update_recovery(
                            recovery,
                            recovery.version,
                            RecoveryStatus.VOUCHER_CREATED,
                            voucher_id=voucher.voucher_id,
                        )
                    self._record_event(
                        _event(
                            AnalyticsEventType.VOUCHER_ISSUED,
                            f"voucher-issued:{recovery.id}",
                            principal.store_id,
                            cart.id,
                            recovery.id,
                            currency=cart.currency,
                            amount=voucher.discount_value,
                        )
                    )
                if recovery.notification_id is None:
                    summary = CartSummary(
                        cart_id=cart.id,
                        item_count=len(cart.items),
                        total=cart.total,
                        currency=cart.currency,
                    )
                    notification_id = self.notification_service.send_recovery_notification(
                        summary, voucher, f"notification:{recovery.id}"
                    )
                    recovery = self.repository.update_recovery(
                        recovery,
                        recovery.version,
                        RecoveryStatus.NOTIFICATION_SENT,
                        notification_id=notification_id,
                    )
                    self._record_event(
                        _event(
                            AnalyticsEventType.NOTIFICATION_SENT,
                            f"notification-sent:{recovery.id}",
                            principal.store_id,
                            cart.id,
                            recovery.id,
                        )
                    )
                started_count += 1
            self.repository.commit()
        except Exception:
            self.repository.rollback()
            raise
        next_cursor = None
        if has_more and batch:
            if self.cursor_codec is None:
                raise RuntimeError("cursor_codec is required when more scan results remain")
            last = batch[-1]
            next_cursor = self._codec().encode(
                principal.store_id, (_utc(last.last_activity_at), last.id)
            )
        return ScanResponse(
            processed_count=len(batch),
            eligible_count=len(carts),
            started_count=started_count,
            failed_count=failed_count,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def _record_event(self, event: AnalyticsEvent) -> None:
        if self.repository.get_event(event.event_key) is None:
            self.repository.add_event(event)
            self.analytics_service.record_event(event)

    def _codec(self) -> CursorCodec:
        return self.cursor_codec or CursorCodec.from_environment()


class RecoveryTrackingService:
    """Track authenticated order completion and recovery expiry."""

    def __init__(
        self,
        repository: CartRepositoryProtocol,
        analytics_service: AnalyticsServiceProtocol,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.analytics_service = analytics_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def consume_order_completed(
        self,
        principal: AuthenticatedPrincipalProtocol,
        event: OrderCompletedEvent,
    ) -> RecoveryResponse | None:
        """Convert a matching recovery once, rejecting cross-tenant events."""
        _require_scope(principal, "recovery:write")
        if event.store_id != principal.store_id or event.customer_id != principal.customer_id:
            raise CartAuthorizationError("Order event ownership is invalid")
        recovery = self.repository.get_recovery_for_update(principal.store_id, event.cart_id)
        if recovery is None or recovery.customer_id != event.customer_id:
            raise CartNotFoundError("Recovery not found")
        if self.repository.get_event(f"order:{event.event_id}") is not None:
            return _recovery_response(recovery)
        now = _utc(self.clock())
        if _utc(recovery.created_at) + RECOVERY_WINDOW <= now:
            expired = self.expire_recovery(principal, event.cart_id)
            return expired
        if recovery.status in {RecoveryStatus.RECOVERED, RecoveryStatus.EXPIRED}:
            return _recovery_response(recovery)
        if recovery.cart.currency != event.currency:
            raise ValueError("Order currency does not match cart currency")
        try:
            updated = self.repository.update_recovery(
                recovery,
                recovery.version,
                RecoveryStatus.RECOVERED,
                recovered_order_id=event.order_id,
            )
            recovery.cart.recovered_at = event.completed_at
            order_event = _event(
                AnalyticsEventType.ORDER_RECOVERED,
                f"order:{event.event_id}",
                principal.store_id,
                event.cart_id,
                recovery.id,
                event.completed_at,
                event.currency,
                event.amount,
            )
            self.repository.add_event(order_event)
            self.analytics_service.record_event(order_event)
            self.repository.commit()
        except RepositoryConflictError as error:
            self.repository.rollback()
            raise RecoveryConflictError("Recovery version is stale") from error
        except Exception:
            self.repository.rollback()
            raise
        return _recovery_response(updated)

    def expire_recovery(
        self, principal: AuthenticatedPrincipalProtocol, cart_id: UUID
    ) -> RecoveryResponse:
        """Mark a pending recovery expired after the 24-hour window."""
        _require_scope(principal, "recovery:write")
        recovery = self.repository.get_recovery_for_update(principal.store_id, cart_id)
        if recovery is None or recovery.customer_id != principal.customer_id:
            raise CartNotFoundError("Recovery not found")
        if recovery.status in {RecoveryStatus.RECOVERED, RecoveryStatus.EXPIRED}:
            return _recovery_response(recovery)
        try:
            updated = self.repository.update_recovery(
                recovery,
                recovery.version,
                RecoveryStatus.EXPIRED,
            )
            transition = _event(
                AnalyticsEventType.RECOVERY_EXPIRED,
                f"recovery-expired:{recovery.id}",
                principal.store_id,
                cart_id,
                recovery.id,
            )
            self.repository.add_event(transition)
            self.analytics_service.record_event(transition)
            self.repository.commit()
        except RepositoryConflictError as error:
            self.repository.rollback()
            raise RecoveryConflictError("Recovery version is stale") from error
        except Exception:
            self.repository.rollback()
            raise
        return _recovery_response(updated)


class RecoveryQueryService:
    """Read one recovery after enforcing tenant and ownership authorization."""

    def __init__(self, repository: CartRepositoryProtocol) -> None:
        self.repository = repository

    def get_recovery(
        self, principal: AuthenticatedPrincipalProtocol, cart_id: UUID
    ) -> RecoveryResponse:
        """Return a secret-free recovery DTO for an authorized principal."""
        recovery = self.repository.get_recovery(principal.store_id, cart_id)
        if recovery is None:
            raise CartNotFoundError("Recovery not found")
        staff_access = principal.role in {"admin", "manager", "operator"} and "recovery:read" in principal.scopes
        if not staff_access and recovery.customer_id != principal.customer_id:
            raise CartAuthorizationError("Customer does not own this recovery")
        return _recovery_response(recovery)


class RecoveryAnalyticsService:
    """Aggregate store-scoped recovery events with Decimal arithmetic."""

    def __init__(
        self,
        repository: CartRepositoryProtocol,
        cursor_codec: CursorCodec | None = None,
    ) -> None:
        self.repository = repository
        self.cursor_codec = cursor_codec

    def query(
        self,
        principal: AuthenticatedPrincipalProtocol,
        filters: AnalyticsFilters,
    ) -> AnalyticsResponse:
        """Return bounded analytics and a store-bound next cursor."""
        _require_admin(principal, "analytics:read")
        cursor = None
        if filters.cursor is not None:
            cursor = self._codec().decode(filters.cursor, principal.store_id)
        events = list(
            self.repository.list_events(
                principal.store_id,
                _utc(filters.start),
                _utc(filters.end),
                filters.status,
                cursor,
                filters.limit,
            )
        )
        has_more = len(events) > filters.limit
        page = events[: filters.limit]
        currencies = {event.currency for event in page if event.amount is not None}
        if len(currencies) > 1 and not filters.group_by_currency:
            raise MixedCurrencyError("Group analytics by currency before aggregating")
        if filters.group_by_currency:
            grouped = {
                currency: self._metrics([event for event in page if event.currency == currency], currency)
                for currency in sorted(currency for currency in currencies if currency is not None)
            }
            metrics: AnalyticsMetrics | dict[str, AnalyticsMetrics] = grouped
        else:
            currency = next(iter(currencies), None)
            metrics = self._metrics(page, currency)
        next_cursor = None
        if has_more and page:
            if self.cursor_codec is None:
                raise RuntimeError("cursor_codec is required when more analytics results remain")
            last = page[-1]
            next_cursor = self._codec().encode(
                principal.store_id, (_utc(last.occurred_at), last.id)
            )
        return AnalyticsResponse(metrics=metrics, next_cursor=next_cursor, has_more=has_more)

    @staticmethod
    def _metrics(events: Iterable[AnalyticsEvent], currency: str | None) -> AnalyticsMetrics:
        events = list(events)
        abandoned = sum(event.event_type == AnalyticsEventType.CART_ABANDONED for event in events)
        started = sum(event.event_type == AnalyticsEventType.RECOVERY_STARTED for event in events)
        recovered = sum(event.event_type == AnalyticsEventType.ORDER_RECOVERED for event in events)
        amount = sum(
            (event.amount for event in events if event.event_type == AnalyticsEventType.ORDER_RECOVERED),
            Decimal("0"),
        )
        rate = (Decimal(recovered) / Decimal(abandoned) if abandoned else Decimal("0")).quantize(
            MONEY_QUANTUM, rounding=ROUND_HALF_UP
        )
        return AnalyticsMetrics(
            abandoned_count=abandoned,
            recovery_started_count=started,
            recovered_count=recovered,
            conversion_rate=rate,
            recovered_amount=amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP),
            currency=currency,
        )

    def _codec(self) -> CursorCodec:
        return self.cursor_codec or CursorCodec.from_environment()


def _recovery_response(recovery: Recovery) -> RecoveryResponse:
    """Map an ORM recovery to a secret-free public DTO."""
    voucher = None
    if recovery.voucher is not None:
        voucher = VoucherDelivery(
            voucher_id=recovery.voucher.id,
            discount_type=recovery.voucher.discount_type,
            discount_value=recovery.voucher.discount_value,
            currency=recovery.voucher.currency,
            expires_at=recovery.voucher.expires_at,
        )
    return RecoveryResponse(
        recovery_id=recovery.id,
        cart_id=recovery.cart_id,
        status=recovery.status,
        voucher=voucher,
        notification_id=recovery.notification_id,
        recovered_at=recovery.cart.recovered_at if recovery.cart is not None else None,
        updated_at=recovery.updated_at,
    )
