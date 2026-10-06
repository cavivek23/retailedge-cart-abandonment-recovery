from decimal import Decimal

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .exceptions import (
    DuplicateInventoryError,
    InventoryConflictError,
    InventoryNotFoundError,
    InventoryPersistenceError,
)
from .models import Inventory, InventoryMutation


class InventoryRepository:
    """Persistence boundary for inventory data."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, store_id: str, product_id: str, stock: Decimal) -> Inventory:
        inventory = Inventory(store_id=store_id, product_id=product_id, stock=stock)
        self.session.add(inventory)
        try:
            self.session.commit()
            self.session.refresh(inventory)
            return inventory
        except IntegrityError as error:
            self.session.rollback()
            if "uq_inventory_store_product" in str(error.orig):
                raise DuplicateInventoryError("Inventory already exists") from None
            raise InventoryPersistenceError("Inventory could not be created") from None
        except SQLAlchemyError:
            self.session.rollback()
            raise InventoryPersistenceError("Inventory could not be created") from None

    def get(self, store_id: str, inventory_id: int) -> Inventory:
        inventory = self.session.scalar(
            select(Inventory).where(Inventory.store_id == store_id, Inventory.id == inventory_id)
        )
        if inventory is None:
            raise InventoryNotFoundError("Inventory not found")
        return inventory

    def get_by_product(self, store_id: str, product_id: str) -> Inventory:
        inventory = self.session.scalar(
            select(Inventory).where(
                Inventory.store_id == store_id,
                Inventory.product_id == product_id,
            )
        )
        if inventory is None:
            raise InventoryNotFoundError("Inventory not found")
        return inventory

    def update_stock(
        self,
        inventory: Inventory,
        quantity_delta: Decimal,
        expected_version: int,
        operation_id: str,
    ) -> Inventory:
        existing = self.session.get(InventoryMutation, operation_id)
        if existing is not None:
            if existing.inventory_id != inventory.id or existing.quantity_delta != quantity_delta:
                raise InventoryConflictError("Operation ID was already used")
            return self.get(inventory.store_id, inventory.id)

        new_stock = inventory.stock + quantity_delta
        result = self.session.execute(
            update(Inventory)
            .where(Inventory.id == inventory.id, Inventory.version == expected_version)
            .values(stock=new_stock, version=Inventory.version + 1)
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise InventoryConflictError("Inventory version is stale")

        mutation = InventoryMutation(
            operation_id=operation_id,
            inventory_id=inventory.id,
            store_id=inventory.store_id,
            product_id=inventory.product_id,
            quantity_delta=quantity_delta,
            resulting_stock=new_stock,
            resulting_version=expected_version + 1,
        )
        self.session.add(mutation)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise InventoryConflictError("Operation was already processed") from None
        except SQLAlchemyError:
            self.session.rollback()
            raise InventoryPersistenceError("Inventory could not be updated") from None
        return self.get(inventory.store_id, inventory.id)

    def delete(self, inventory: Inventory, expected_version: int) -> None:
        result = self.session.execute(
            delete(Inventory).where(
                Inventory.id == inventory.id,
                Inventory.store_id == inventory.store_id,
                Inventory.version == expected_version,
            )
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise InventoryConflictError("Inventory version is stale")
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise InventoryPersistenceError("Inventory could not be deleted") from None

    def list_by_store(self, store_id: str, cursor: int | None, limit: int) -> list[Inventory]:
        statement = select(Inventory).where(Inventory.store_id == store_id)
        if cursor is not None:
            statement = statement.where(Inventory.id > cursor)
        return list(self.session.scalars(statement.order_by(Inventory.id).limit(limit + 1)))