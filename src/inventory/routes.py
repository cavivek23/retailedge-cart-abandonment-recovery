from fastapi import APIRouter, Depends, FastAPI, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .auth import AuthenticatedPrincipal, get_principal, require_store
from .database import get_session
from .exceptions import InventoryError
from .models import Inventory
from .repository import InventoryRepository
from .schemas import InventoryCreateRequest, InventoryPage, InventoryResponse, StockUpdateRequest
from .service import InventoryService


def get_service(session: Session = Depends(get_session)) -> InventoryService:
    return InventoryService(InventoryRepository(session))


router = APIRouter(prefix="/stores/{store_id}/inventory", tags=["inventory"])


@router.post("", response_model=InventoryResponse, status_code=status.HTTP_201_CREATED)
def create_inventory(
    store_id: str,
    command: InventoryCreateRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    service: InventoryService = Depends(get_service),
) -> Inventory:
    _require_path_store(principal, store_id)
    return service.create(principal, command)


@router.patch("/{inventory_id}/stock", response_model=InventoryResponse)
def update_stock(
    store_id: str,
    inventory_id: int,
    command: StockUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    service: InventoryService = Depends(get_service),
) -> Inventory:
    _require_path_store(principal, store_id)
    return service.update_stock(principal, inventory_id, command)


@router.get("/product/{product_id}", response_model=InventoryResponse)
def get_by_product(
    store_id: str,
    product_id: str,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    service: InventoryService = Depends(get_service),
) -> Inventory:
    _require_path_store(principal, store_id)
    return service.get_by_product(principal, product_id)


@router.get("/{inventory_id}", response_model=InventoryResponse)
def get_inventory(
    store_id: str,
    inventory_id: int,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    service: InventoryService = Depends(get_service),
) -> Inventory:
    _require_path_store(principal, store_id)
    return service.get(principal, inventory_id)


@router.delete("/{inventory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_inventory(
    store_id: str,
    inventory_id: int,
    expected_version: int = Query(gt=0),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    service: InventoryService = Depends(get_service),
) -> Response:
    _require_path_store(principal, store_id)
    service.delete(principal, inventory_id, expected_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("", response_model=InventoryPage)
def list_inventory(
    store_id: str,
    cursor: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    service: InventoryService = Depends(get_service),
) -> InventoryPage:
    _require_path_store(principal, store_id)
    return service.list(principal, cursor, limit)


def _require_path_store(principal: AuthenticatedPrincipal, store_id: str) -> None:
    require_store(principal, store_id)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(InventoryError)
    async def inventory_error_handler(_, error: InventoryError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": str(error)}},
        )