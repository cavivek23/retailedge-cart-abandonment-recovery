from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
Quantity = Annotated[Decimal, Field(max_digits=18, decimal_places=3)]


class InventoryCreateRequest(BaseModel):
    """Whitelisted fields for inventory creation."""

    model_config = ConfigDict(extra="forbid")
    product_id: Identifier
    initial_stock: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=3)] = Decimal("0")

    @field_validator("initial_stock", mode="before")
    @classmethod
    def reject_float(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("stock must be a decimal value")
        return value


class StockUpdateRequest(BaseModel):
    """Whitelisted fields for an optimistic stock update."""

    model_config = ConfigDict(extra="forbid")
    quantity_delta: Quantity
    expected_version: Annotated[int, Field(gt=0)]
    operation_id: Identifier

    @field_validator("quantity_delta", mode="before")
    @classmethod
    def reject_float(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("quantity_delta must be a decimal value")
        return value


class InventoryResponse(BaseModel):
    """Public inventory representation."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: str
    stock: Decimal
    version: int


class InventoryPage(BaseModel):
    items: list[InventoryResponse]
    next_cursor: str | None
    has_more: bool