from decimal import Decimal, InvalidOperation

from .audit import audit_inventory_event
from .auth import AuthenticatedPrincipal, require_admin, require_scope
from .exceptions import InvalidStockError
from .models import Inventory
from .repository import InventoryRepository
from .schemas import InventoryCreateRequest, InventoryPage, InventoryResponse, StockUpdateRequest


SCALE = Decimal("0.001")
MAX_QUANTITY = Decimal("999999999999999.999")


class InventoryService:
    """Business operations with defense-in-depth tenant and role authorization."""

    def __init__(self, repository: InventoryRepository) -> None:
        self.repository = repository

    def create(self, principal: AuthenticatedPrincipal, command: InventoryCreateRequest) -> Inventory:
        require_scope(principal, "inventory:write")
        inventory = self.repository.create(
            principal.store_id, command.product_id, self._stock_value(command.initial_stock)
        )
        audit_inventory_event(
            "inventory_created", role=principal.role, operation_id=f"create:{inventory.id}"
        )
        return inventory

    def update_stock(
        self, principal: AuthenticatedPrincipal, inventory_id: int, command: StockUpdateRequest
    ) -> Inventory:
        require_scope(principal, "inventory:write")
        inventory = self.repository.get(principal.store_id, inventory_id)
        delta = self._stock_value(command.quantity_delta, allow_negative=True)
        if inventory.stock + delta < 0:
            raise InvalidStockError("Stock cannot become negative")
        result = self.repository.update_stock(
            inventory, delta, command.expected_version, command.operation_id
        )
        audit_inventory_event(
            "stock_updated",
            role=principal.role,
            operation_id=command.operation_id,
            old_version=command.expected_version,
            new_version=result.version,
        )
        return result

    def get_by_product(self, principal: AuthenticatedPrincipal, product_id: str) -> Inventory:
        require_scope(principal, "inventory:read")
        return self.repository.get_by_product(principal.store_id, product_id)

    def get(self, principal: AuthenticatedPrincipal, inventory_id: int) -> Inventory:
        require_scope(principal, "inventory:read")
        return self.repository.get(principal.store_id, inventory_id)

    def delete(self, principal: AuthenticatedPrincipal, inventory_id: int, expected_version: int) -> None:
        require_admin(principal)
        inventory = self.repository.get(principal.store_id, inventory_id)
        self.repository.delete(inventory, expected_version)
        audit_inventory_event(
            "inventory_deleted",
            role=principal.role,
            operation_id=f"delete:{inventory_id}:{expected_version}",
            old_version=expected_version,
        )

    def list(self, principal: AuthenticatedPrincipal, cursor: int | None, limit: int) -> InventoryPage:
        require_scope(principal, "inventory:read")
        records = self.repository.list_by_store(principal.store_id, cursor, limit)
        has_more = len(records) > limit
        items = records[:limit]
        return InventoryPage(
            items=[InventoryResponse.model_validate(item) for item in items],
            next_cursor=str(items[-1].id) if has_more and items else None,
            has_more=has_more,
        )

    @staticmethod
    def _stock_value(value: Decimal, allow_negative: bool = False) -> Decimal:
        if isinstance(value, float):
            raise InvalidStockError("Stock values must use Decimal, not float")
        try:
            normalized = Decimal(value)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise InvalidStockError("Stock must be a valid decimal value") from error
        if not normalized.is_finite() or abs(normalized) > MAX_QUANTITY:
            raise InvalidStockError("Stock exceeds the permitted range")
        if normalized.quantize(SCALE) != normalized:
            raise InvalidStockError("Stock may have at most three decimal places")
        if not allow_negative and normalized < 0:
            raise InvalidStockError("Stock cannot be negative")
        return normalized
