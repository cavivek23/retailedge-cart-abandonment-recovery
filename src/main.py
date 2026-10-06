from fastapi import FastAPI

from src.cart.routes import CorrelationIdMiddleware, register_exception_handlers, router


def create_app() -> FastAPI:
    """Create the RetailEdge API application without creating database tables."""
    app = FastAPI(title="RetailEdge Cart Abandonment Recovery")
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(router)
    register_exception_handlers(app)
    return app


app = create_app()