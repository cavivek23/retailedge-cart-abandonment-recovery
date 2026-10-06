from __future__ import annotations

import re
from collections.abc import Callable
from uuid import UUID, uuid4

from datetime import datetime

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from src.inventory.auth import AuthenticatedPrincipal, get_principal
from src.inventory.database import get_session
from src.inventory.exceptions import AuthenticationError, AuthorizationError

from .protocols import (
    AnalyticsServiceProtocol,
    NotificationServiceProtocol,
    VoucherServiceProtocol,
)
from .repositories import (
    RepositoryConflictError,
    SqlAlchemyCartRepository,
)
from .schemas import (
    AnalyticsFilters,
    AnalyticsResponse,
    RecoveryResponse,
    ScanRequest,
    ScanResponse,
)
from .models import RecoveryStatus
from .services import (
    AbandonmentScanService,
    CartAuthorizationError,
    CartNotFoundError,
    CartServiceError,
    CursorValidationError,
    MixedCurrencyError,
    RecoveryAnalyticsService,
    RecoveryConflictError,
    RecoveryQueryService,
)


class ProviderUnavailableError(CartServiceError):
    """Raised when an application has not registered an external provider."""

    code = "provider_unavailable"


router = APIRouter()
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def get_repository(session: Session = Depends(get_session)) -> SqlAlchemyCartRepository:
    """Build a repository over the request-scoped database session."""
    return SqlAlchemyCartRepository(session)


def get_voucher_provider() -> VoucherServiceProtocol:
    """Require the deployment to register a real voucher provider."""
    raise ProviderUnavailableError("Voucher provider is not configured")


def get_notification_provider() -> NotificationServiceProtocol:
    """Require the deployment to register a real notification provider."""
    raise ProviderUnavailableError("Notification provider is not configured")


def get_analytics_provider() -> AnalyticsServiceProtocol:
    """Require the deployment to register a real analytics provider."""
    raise ProviderUnavailableError("Analytics provider is not configured")


def require_admin_scope(principal: AuthenticatedPrincipal, scope: str) -> AuthenticatedPrincipal:
    """Enforce route-level role and scope before entering a service."""
    if principal.role != "admin" or scope not in principal.scopes:
        raise AuthorizationError("Required administrator permission is missing")
    return principal


def require_scan_admin(principal: AuthenticatedPrincipal = Depends(get_principal)) -> AuthenticatedPrincipal:
    """Authenticate and authorize scan callers before provider dependencies resolve."""
    return require_admin_scope(principal, "recovery:write")


def get_scan_service(
    _: AuthenticatedPrincipal = Depends(require_scan_admin),
    repository: SqlAlchemyCartRepository = Depends(get_repository),
    voucher_provider: VoucherServiceProtocol = Depends(get_voucher_provider),
    notification_provider: NotificationServiceProtocol = Depends(get_notification_provider),
    analytics_provider: AnalyticsServiceProtocol = Depends(get_analytics_provider),
) -> AbandonmentScanService:
    """Build the scan service from injected persistence and provider boundaries."""
    from decimal import Decimal
    import os

    threshold = Decimal(os.environ.get("RECOVERY_VOUCHER_THRESHOLD", "0"))
    return AbandonmentScanService(
        repository,
        voucher_provider,
        notification_provider,
        analytics_provider,
        threshold,
    )


def get_query_service(
    repository: SqlAlchemyCartRepository = Depends(get_repository),
) -> RecoveryQueryService:
    """Build the recovery query service."""
    return RecoveryQueryService(repository)


def get_analytics_service(
    repository: SqlAlchemyCartRepository = Depends(get_repository),
) -> RecoveryAnalyticsService:
    """Build the analytics service."""
    return RecoveryAnalyticsService(repository)


def require_recovery_access(
    cart_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    repository: SqlAlchemyCartRepository = Depends(get_repository),
) -> AuthenticatedPrincipal:
    """Perform route-level store and customer BOLA checks before the service call."""
    recovery = repository.get_recovery(principal.store_id, cart_id)
    if recovery is None:
        raise CartNotFoundError("Recovery not found")
    staff_access = (
        principal.role in {"admin", "manager", "operator"}
        and "recovery:read" in principal.scopes
    )
    if not staff_access and recovery.customer_id != principal.customer_id:
        raise AuthorizationError("Customer does not own this recovery")
    return principal


@router.post(
    "/scan/abandoned-carts",
    response_model=ScanResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
)
def scan_abandoned_carts(
    command: ScanRequest,
    principal: AuthenticatedPrincipal = Depends(require_scan_admin),
    service: AbandonmentScanService = Depends(get_scan_service),
) -> ScanResponse:
    """Run one authenticated, admin-only abandonment batch."""
    require_admin_scope(principal, "recovery:write")
    return service.scan(principal, command.batch_size, command.cursor)


@router.get(
    "/carts/{cart_id}/recovery",
    response_model=RecoveryResponse,
    response_model_by_alias=True,
)
def get_cart_recovery(
    cart_id: UUID,
    _: AuthenticatedPrincipal = Depends(require_recovery_access),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    service: RecoveryQueryService = Depends(get_query_service),
) -> RecoveryResponse:
    """Return a secret-free recovery DTO after route-level BOLA checks."""
    return service.get_recovery(principal, cart_id)


@router.get(
    "/analytics/recovery",
    response_model=AnalyticsResponse,
    response_model_by_alias=True,
)
def get_recovery_analytics(
    start: datetime = Query(alias="from"),
    end: datetime = Query(alias="to"),
    status_filter: RecoveryStatus | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    group_by_currency: bool = Query(default=False, alias="groupByCurrency"),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    service: RecoveryAnalyticsService = Depends(get_analytics_service),
) -> AnalyticsResponse:
    """Return admin-only, store-scoped recovery analytics."""
    require_admin_scope(principal, "analytics:read")
    filters = AnalyticsFilters(
        **{
            "from": start,
            "to": end,
            "status": status_filter,
            "cursor": cursor,
            "limit": limit,
            "groupByCurrency": group_by_currency,
        }
    )
    return service.query(principal, filters)


def _safe_error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register stable, non-sensitive API error responses."""

    @app.exception_handler(AuthenticationError)
    async def authentication_error_handler(_: Request, error: AuthenticationError) -> JSONResponse:
        return _safe_error(error.code, str(error), 401)

    @app.exception_handler(AuthorizationError)
    async def authorization_error_handler(_: Request, error: AuthorizationError) -> JSONResponse:
        return _safe_error(error.code, str(error), 403)

    @app.exception_handler(CartServiceError)
    async def cart_error_handler(_: Request, error: CartServiceError) -> JSONResponse:
        status_code = {
            CartAuthorizationError: 403,
            CartNotFoundError: 404,
            CursorValidationError: 422,
            MixedCurrencyError: 422,
            RecoveryConflictError: 409,
            ProviderUnavailableError: 503,
        }.get(type(error), 500)
        return _safe_error(error.code, str(error), status_code)

    @app.exception_handler(RepositoryConflictError)
    async def repository_conflict_handler(_: Request, error: RepositoryConflictError) -> JSONResponse:
        return _safe_error("repository_conflict", str(error), 409)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, __: RequestValidationError) -> JSONResponse:
        return _safe_error("validation_error", "Request validation failed", 422)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, __: Exception) -> JSONResponse:
        return _safe_error("internal_error", "Internal server error", 500)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a safe correlation ID without logging sensitive request data."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        supplied = request.headers.get("X-Correlation-ID", "")
        correlation_id = supplied if _CORRELATION_ID.fullmatch(supplied) else str(uuid4())
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response