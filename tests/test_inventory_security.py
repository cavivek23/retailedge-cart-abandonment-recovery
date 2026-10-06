from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.inventory.auth import AuthenticatedPrincipal
from src.inventory.database import Base
from src.inventory.exceptions import (
    AuthenticationError,
    AuthorizationError,
    InventoryConflictError,
    InvalidStockError,
)
from src.inventory.repository import InventoryRepository
from src.inventory.routes import get_service, register_exception_handlers, router
from src.inventory.schemas import InventoryCreateRequest, StockUpdateRequest
from src.inventory.service import InventoryService


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as database_session:
        yield database_session


def principal(store_id: str = "store-a", role: str = "admin") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        store_id=store_id,
        customer_id="customer-1",
        role=role,
        scopes=frozenset({"inventory:read", "inventory:write"}),
        subject="user-1",
    )


def test_negative_stock_and_stale_version_are_rejected(session: Session) -> None:
    service = InventoryService(InventoryRepository(session))
    inventory = service.create(principal(), InventoryCreateRequest(product_id="sku-1", initial_stock=Decimal("1.000")))

    with pytest.raises(InvalidStockError):
        service.update_stock(
            principal(),
            inventory.id,
            StockUpdateRequest(quantity_delta=Decimal("-2.000"), expected_version=1, operation_id="op-negative"),
        )

    service.update_stock(
        principal(),
        inventory.id,
        StockUpdateRequest(quantity_delta=Decimal("1.000"), expected_version=1, operation_id="op-1"),
    )
    with pytest.raises(InventoryConflictError):
        service.update_stock(
            principal(),
            inventory.id,
            StockUpdateRequest(quantity_delta=Decimal("1.000"), expected_version=1, operation_id="op-stale"),
        )


def test_cross_store_access_is_denied(session: Session) -> None:
    service = InventoryService(InventoryRepository(session))
    inventory = service.create(principal(), InventoryCreateRequest(product_id="sku-1"))

    with pytest.raises(Exception) as error:
        service.get(principal("store-b"), inventory.id)
    assert error.type.__name__ == "InventoryNotFoundError"


def test_non_admin_cannot_delete(session: Session) -> None:
    service = InventoryService(InventoryRepository(session))
    inventory = service.create(principal(), InventoryCreateRequest(product_id="sku-1"))
    with pytest.raises(AuthorizationError):
        service.delete(
            principal(role="manager"), inventory.id, expected_version=inventory.version
        )


def test_jwt_requires_time_claims_and_pinned_algorithm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "user-1",
        "store_id": "store-a",
        "customer_id": "customer-1",
        "role": "admin",
        "scope": "inventory:read inventory:write",
        "exp": now + timedelta(minutes=5),
        "iat": now,
    }
    token = jwt.encode(claims, "test-secret", algorithm="HS256")
    response = TestClient(app).get(
        "/stores/store-a/inventory/product/sku-1", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] in {"invalid_token", "invalid_nbf"}


def test_authentication_and_authorization_error_contracts() -> None:
    assert AuthenticationError("bad").status_code == 401
    assert AuthorizationError("no").status_code == 403