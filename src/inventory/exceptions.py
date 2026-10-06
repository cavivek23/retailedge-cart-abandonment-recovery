class InventoryError(Exception):
    """Base exception for inventory domain errors."""

    code = "inventory_error"
    status_code = 500


class InventoryNotFoundError(InventoryError):
    """Raised when an inventory record is not visible in the store context."""

    code = "inventory_not_found"
    status_code = 404


class DuplicateInventoryError(InventoryError):
    """Raised when a store already has inventory for a product."""

    code = "inventory_duplicate"
    status_code = 409


class InvalidStockError(InventoryError):
    """Raised when stock input is invalid or would become negative."""

    code = "invalid_stock"
    status_code = 422


class InventoryConflictError(InventoryError):
    """Raised when an inventory update uses a stale version."""

    code = "inventory_conflict"
    status_code = 409


class AuthorizationError(InventoryError):
    """Raised when a principal lacks access to a store or operation."""

    code = "forbidden"
    status_code = 403


class AuthenticationError(InventoryError):
    """Raised when a bearer token is missing or invalid."""

    code = "invalid_token"
    status_code = 401

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class InventoryPersistenceError(InventoryError):
    """Raised when a database operation cannot be completed safely."""

    code = "inventory_persistence_error"
    status_code = 500