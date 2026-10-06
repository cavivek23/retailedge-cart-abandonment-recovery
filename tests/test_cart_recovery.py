from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session

from src.cart.models import (
    AbandonedCart,
    AnalyticsEvent,
    AnalyticsEventType,
    Base,
    CartStatus,
    Recovery,
    RecoveryStatus,
)
from src.cart.repositories import SqlAlchemyCartRepository
from src.cart.routes import get_principal, get_query_service, get_repository, register_exception_handlers, router
from src.cart.schemas import (
    AnalyticsFilters,
    OrderCompletedEvent,
    ScanRequest,
    VoucherDelivery,
)
from src.cart.services import (
    AbandonmentScanService,
    CursorCodec,
    CursorValidationError,
    MixedCurrencyError,
    RecoveryAnalyticsService,
    RecoveryTrackingService,
)
from src.inventory.auth import AuthenticatedPrincipal, JwtAuthenticator
from src.inventory.exceptions import AuthenticationError


class FakeVoucherService:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, Decimal]] = []

    def get_or_create_voucher(
        self,
        store_id: str,
        recovery_id: UUID,
        currency: str,
        cart_total: Decimal,
        idempotency_key: str,
    ) -> VoucherDelivery:
        self.calls.append((recovery_id, cart_total))
        return VoucherDelivery(
            voucher_id=uuid4(),
            discount_type="FIXED_AMOUNT",
            discount_value=Decimal("5.0000"),
            currency=currency,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )


class FakeNotificationService:
    def __init__(self) -> None:
        self.calls = []

    def send_recovery_notification(self, summary, voucher, idempotency_key: str) -> str:
        self.calls.append((summary, voucher, idempotency_key))
        return f"notification-{len(self.calls)}"


class FakeAnalyticsService:
    def __init__(self) -> None:
        self.events: list[AnalyticsEvent] = []

    def record_event(self, event: AnalyticsEvent) -> None:
        self.events.append(event)


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    database_session = Session(engine)
    try:
        yield database_session
    finally:
        database_session.close()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def make_principal(
    customer_id: str = "customer-a",
    role: str = "admin",
    scopes: frozenset[str] | None = None,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        store_id="store-a",
        customer_id=customer_id,
        role=role,
        scopes=scopes or frozenset({"recovery:read", "recovery:write", "analytics:read"}),
        subject=f"subject-{customer_id}",
    )


def add_cart(
    session: Session,
    *,
    customer_id: str,
    total: Decimal,
    last_activity_at: datetime,
    store_id: str = "store-a",
) -> AbandonedCart:
    cart = AbandonedCart(
        store_id=store_id,
        customer_id=customer_id,
        status=CartStatus.ABANDONED,
        currency="USD",
        subtotal=total,
        discount_total=Decimal("0.0000"),
        tax_total=Decimal("0.0000"),
        total=total,
        last_activity_at=last_activity_at,
    )
    session.add(cart)
    session.flush()
    return cart


def build_scan_service(
    session: Session,
    *,
    threshold: Decimal,
    clock: datetime,
) -> tuple[AbandonmentScanService, FakeVoucherService, FakeAnalyticsService]:
    voucher = FakeVoucherService()
    notifications = FakeNotificationService()
    analytics = FakeAnalyticsService()
    service = AbandonmentScanService(
        SqlAlchemyCartRepository(session),
        voucher,
        notifications,
        analytics,
        threshold,
        cursor_codec=CursorCodec(b"cart-test-cursor-key"),
        clock=lambda: clock,
    )
    return service, voucher, analytics


def test_scan_identifies_cart_after_30_minutes(session: Session, now: datetime) -> None:
    cart = add_cart(
        session,
        customer_id="customer-a",
        total=Decimal("25.0000"),
        last_activity_at=now - timedelta(minutes=30),
    )
    service, _, _ = build_scan_service(session, threshold=Decimal("100.0000"), clock=now)

    result = service.scan(make_principal(), batch_size=10)

    assert result.processed_count == 1
    assert session.scalar(select(Recovery).where(Recovery.cart_id == cart.id)) is not None


def test_scan_marks_pending_checkout_cart_abandoned(session: Session, now: datetime) -> None:
    cart = add_cart(
        session,
        customer_id="customer-a",
        total=Decimal("25.0000"),
        last_activity_at=now - timedelta(minutes=30),
    )
    cart.status = CartStatus.PENDING_CHECKOUT
    session.commit()
    service, _, _ = build_scan_service(session, threshold=Decimal("100.0000"), clock=now)

    service.scan(make_principal(), batch_size=10)
    session.refresh(cart)

    assert cart.status is CartStatus.ABANDONED
    assert cart.abandoned_at is not None
    assert cart.abandoned_at.replace(tzinfo=timezone.utc) == now


def test_scan_generates_voucher_above_decimal_threshold(session: Session, now: datetime) -> None:
    cart = add_cart(
        session,
        customer_id="customer-a",
        total=Decimal("100.0001"),
        last_activity_at=now - timedelta(minutes=31),
    )
    service, voucher, _ = build_scan_service(session, threshold=Decimal("100.0000"), clock=now)

    service.scan(make_principal(), batch_size=10)

    recovery = session.scalar(select(Recovery).where(Recovery.cart_id == cart.id))
    assert recovery is not None
    assert voucher.calls == [(recovery.id, Decimal("100.0001"))]


def test_scan_does_not_generate_voucher_below_decimal_threshold(session: Session, now: datetime) -> None:
    add_cart(
        session,
        customer_id="customer-a",
        total=Decimal("99.9999"),
        last_activity_at=now - timedelta(minutes=31),
    )
    service, voucher, _ = build_scan_service(session, threshold=Decimal("100.0000"), clock=now)

    service.scan(make_principal(), batch_size=10)

    assert voucher.calls == []


def test_matching_order_converts_recovery_within_24_hours(session: Session, now: datetime) -> None:
    cart = add_cart(
        session,
        customer_id="customer-a",
        total=Decimal("25.0000"),
        last_activity_at=now - timedelta(hours=1),
    )
    scan, _, analytics = build_scan_service(session, threshold=Decimal("100.0000"), clock=now)
    scan.scan(make_principal(), batch_size=10)
    tracking = RecoveryTrackingService(
        SqlAlchemyCartRepository(session), analytics, clock=lambda: now + timedelta(hours=1)
    )

    response = tracking.consume_order_completed(
        make_principal(),
        OrderCompletedEvent(
            event_id="order-event-1",
            order_id="order-1",
            store_id="store-a",
            customer_id="customer-a",
            cart_id=cart.id,
            completed_at=now + timedelta(hours=1),
            amount=Decimal("25.0000"),
            currency="USD",
        ),
    )

    assert response is not None
    assert response.status is RecoveryStatus.RECOVERED


def test_recovery_expires_when_24_hour_window_lapses(session: Session, now: datetime) -> None:
    cart = add_cart(
        session,
        customer_id="customer-a",
        total=Decimal("25.0000"),
        last_activity_at=now - timedelta(hours=2),
    )
    scan, _, analytics = build_scan_service(session, threshold=Decimal("100.0000"), clock=now)
    scan.scan(make_principal(), batch_size=10)
    recovery = session.scalar(select(Recovery).where(Recovery.cart_id == cart.id))
    assert recovery is not None
    recovery.created_at = now - timedelta(hours=25)
    session.commit()
    tracking = RecoveryTrackingService(
        SqlAlchemyCartRepository(session), analytics, clock=lambda: now
    )

    response = tracking.expire_recovery(make_principal(), cart.id)

    assert response.status is RecoveryStatus.EXPIRED


def test_only_most_recent_customer_cart_triggers_recovery(session: Session, now: datetime) -> None:
    older = add_cart(
        session,
        customer_id="customer-a",
        total=Decimal("10.0000"),
        last_activity_at=now - timedelta(hours=2),
    )
    newest = add_cart(
        session,
        customer_id="customer-a",
        total=Decimal("20.0000"),
        last_activity_at=now - timedelta(hours=1),
    )
    service, _, _ = build_scan_service(session, threshold=Decimal("100.0000"), clock=now)

    result = service.scan(make_principal(), batch_size=10)

    assert result.processed_count == 1
    assert session.scalar(select(Recovery).where(Recovery.cart_id == newest.id)) is not None
    assert session.scalar(select(Recovery).where(Recovery.cart_id == older.id)) is None


def test_customer_cannot_access_another_customers_recovery(session: Session) -> None:
    cart = add_cart(
        session,
        customer_id="customer-b",
        total=Decimal("25.0000"),
        last_activity_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    recovery = Recovery(
        cart_id=cart.id,
        store_id="store-a",
        customer_id="customer-b",
        status=RecoveryStatus.PENDING,
    )
    session.add(recovery)
    session.commit()

    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    app.dependency_overrides[get_principal] = lambda: make_principal("customer-a", "customer")
    app.dependency_overrides[get_repository] = lambda: SqlAlchemyCartRepository(session)

    response = TestClient(app).get(f"/carts/{cart.id}/recovery")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_non_admin_scan_returns_403_machine_readable_error() -> None:
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    app.dependency_overrides[get_principal] = lambda: make_principal(
        role="operator", scopes=frozenset({"recovery:read"})
    )

    response = TestClient(app).post("/scan/abandoned-carts", json={"batchSize": 10})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_alg_none_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_SECRET", "unit-test-key")
    token = jwt.encode({"sub": "subject"}, "", algorithm="none")

    with pytest.raises(AuthenticationError):
        JwtAuthenticator().authenticate(token)


def test_missing_exp_claim_is_rejected(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_SECRET", "unit-test-key")
    token = jwt.encode(
        {
            "sub": "subject",
            "store_id": "store-a",
            "customer_id": "customer-a",
            "role": "admin",
            "scope": "recovery:write",
            "iat": now,
            "nbf": now,
        },
        "unit-test-key",
        algorithm="HS256",
    )

    with pytest.raises(AuthenticationError) as error:
        JwtAuthenticator().authenticate(token)
    assert error.value.code == "invalid_exp"


def test_unknown_scan_dto_field_is_rejected() -> None:
    with pytest.raises(ValueError):
        ScanRequest(batchSize=10, unexpected=True)


def test_invalid_cursor_is_rejected(session: Session, now: datetime) -> None:
    service, _, _ = build_scan_service(session, threshold=Decimal("100.0000"), clock=now)

    with pytest.raises(CursorValidationError):
        service.scan(make_principal(), batch_size=10, cursor="invalid")


def test_duplicate_order_event_is_idempotent(session: Session, now: datetime) -> None:
    cart = add_cart(
        session,
        customer_id="customer-a",
        total=Decimal("25.0000"),
        last_activity_at=now - timedelta(hours=1),
    )
    scan, _, analytics = build_scan_service(session, threshold=Decimal("100.0000"), clock=now)
    scan.scan(make_principal(), batch_size=10)
    event = OrderCompletedEvent(
        event_id="order-event-duplicate",
        order_id="order-duplicate",
        store_id="store-a",
        customer_id="customer-a",
        cart_id=cart.id,
        completed_at=now + timedelta(hours=1),
        amount=Decimal("25.0000"),
        currency="USD",
    )
    tracking = RecoveryTrackingService(
        SqlAlchemyCartRepository(session), analytics, clock=lambda: now + timedelta(hours=1)
    )

    tracking.consume_order_completed(make_principal(), event)
    tracking.consume_order_completed(make_principal(), event)

    recovered_events = [
        item for item in analytics.events if item.event_type is AnalyticsEventType.ORDER_RECOVERED
    ]
    assert len(recovered_events) == 1


def test_mixed_currency_analytics_requires_grouping() -> None:
    class EventRepository:
        def list_events(self, *args, **kwargs):
            return [
                AnalyticsEvent(
                    event_key="event-usd",
                    store_id="store-a",
                    event_type=AnalyticsEventType.ORDER_RECOVERED,
                    occurred_at=datetime.now(timezone.utc),
                    currency="USD",
                    amount=Decimal("10.0000"),
                ),
                AnalyticsEvent(
                    event_key="event-eur",
                    store_id="store-a",
                    event_type=AnalyticsEventType.ORDER_RECOVERED,
                    occurred_at=datetime.now(timezone.utc),
                    currency="EUR",
                    amount=Decimal("10.0000"),
                ),
            ]

    filters = AnalyticsFilters(
        **{
            "from": datetime(2026, 10, 1, tzinfo=timezone.utc),
            "to": datetime(2026, 10, 2, tzinfo=timezone.utc),
            "limit": 10,
        }
    )

    with pytest.raises(MixedCurrencyError):
        RecoveryAnalyticsService(EventRepository()).query(make_principal(), filters)