from __future__ import annotations

from datetime import datetime
from typing import Sequence
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    AbandonedCart,
    AnalyticsEvent,
    CartStatus,
    Recovery,
    RecoveryStatus,
)


class RepositoryConflictError(Exception):
    """Raised when an optimistic or idempotency write cannot be applied."""


class SqlAlchemyCartRepository:
    """SQLAlchemy ORM repository for store-scoped cart recovery data."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_eligible_carts(
        self,
        store_id: str,
        cutoff: datetime,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> Sequence[AbandonedCart]:
        """Return the latest eligible abandoned cart for each customer."""
        customer_rank = (
            func.row_number()
            .over(
                partition_by=AbandonedCart.customer_id,
                order_by=(AbandonedCart.last_activity_at.desc(), AbandonedCart.id.desc()),
            )
            .label("customer_rank")
        )
        latest = (
            select(AbandonedCart.id, customer_rank)
            .where(
                AbandonedCart.store_id == store_id,
                AbandonedCart.status.in_((CartStatus.PENDING_CHECKOUT, CartStatus.ABANDONED)),
                AbandonedCart.last_activity_at <= cutoff,
            )
            .subquery()
        )
        filters = [
            AbandonedCart.store_id == store_id,
            latest.c.customer_rank == 1,
        ]
        if cursor is not None:
            cursor_time, cursor_id = cursor
            filters.append(
                or_(
                    AbandonedCart.last_activity_at > cursor_time,
                    and_(
                        AbandonedCart.last_activity_at == cursor_time,
                        AbandonedCart.id > cursor_id,
                    ),
                )
            )
        statement = (
            select(AbandonedCart)
            .join(latest, latest.c.id == AbandonedCart.id)
            .where(*filters)
            .order_by(AbandonedCart.last_activity_at, AbandonedCart.id)
            .limit(limit + 1)
        )
        return list(self.session.scalars(statement))

    def get_recovery(self, store_id: str, cart_id: UUID) -> Recovery | None:
        """Return a store-scoped recovery without exposing another tenant."""
        return self.session.scalar(
            select(Recovery).where(Recovery.store_id == store_id, Recovery.cart_id == cart_id)
        )

    def get_recovery_for_update(self, store_id: str, cart_id: UUID) -> Recovery | None:
        """Return a store-scoped recovery with a database row lock where supported."""
        return self.session.scalar(
            select(Recovery)
            .where(Recovery.store_id == store_id, Recovery.cart_id == cart_id)
            .with_for_update()
        )

    def get_event(self, event_key: str) -> AnalyticsEvent | None:
        """Return an event by its unique idempotency key."""
        return self.session.scalar(select(AnalyticsEvent).where(AnalyticsEvent.event_key == event_key))

    def create_recovery(self, store_id: str, customer_id: str, cart_id: UUID) -> Recovery:
        """Stage a pending recovery and flush its generated identifier."""
        recovery = Recovery(
            cart_id=cart_id,
            store_id=store_id,
            customer_id=customer_id,
            status=RecoveryStatus.PENDING,
        )
        self.session.add(recovery)
        try:
            self.session.flush()
        except IntegrityError as error:
            self.session.rollback()
            raise RepositoryConflictError("Recovery already exists") from error
        return recovery

    def update_recovery(
        self,
        recovery: Recovery,
        expected_version: int,
        status: RecoveryStatus,
        **values: object,
    ) -> Recovery:
        """Stage a version-conditional recovery update."""
        values["status"] = status
        values["version"] = Recovery.version + 1
        values["updated_at"] = func.now()
        result = self.session.execute(
            update(Recovery)
            .where(Recovery.id == recovery.id, Recovery.version == expected_version)
            .values(**values)
        )
        if result.rowcount != 1:
            raise RepositoryConflictError("Recovery version is stale")
        self.session.expire(recovery)
        refreshed = self.session.get(Recovery, recovery.id)
        if refreshed is None:
            raise RepositoryConflictError("Recovery disappeared during update")
        return refreshed

    def add_event(self, event: AnalyticsEvent) -> None:
        """Stage a sanitized analytics event."""
        self.session.add(event)

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
        statement = select(AnalyticsEvent).where(
            AnalyticsEvent.store_id == store_id,
            AnalyticsEvent.occurred_at >= start,
            AnalyticsEvent.occurred_at < end,
        )
        if status is not None:
            statement = statement.join(Recovery, Recovery.id == AnalyticsEvent.recovery_id).where(
                Recovery.status == status
            )
        if cursor is not None:
            cursor_time, cursor_id = cursor
            statement = statement.where(
                or_(
                    AnalyticsEvent.occurred_at > cursor_time,
                    and_(
                        AnalyticsEvent.occurred_at == cursor_time,
                        AnalyticsEvent.id > cursor_id,
                    ),
                )
            )
        return list(
            self.session.scalars(
                statement.order_by(AnalyticsEvent.occurred_at, AnalyticsEvent.id).limit(limit + 1)
            )
        )

    def commit(self) -> None:
        """Commit the current transaction."""
        self.session.commit()

    def rollback(self) -> None:
        """Roll back the current transaction."""
        self.session.rollback()