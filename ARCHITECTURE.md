# Architecture

RetailEdge exposes the cart recovery API through a FastAPI application.
Inventory Sync is a store-scoped supporting module with model, repository, service, and route layers.
Cart recovery uses the same layered boundary and shares authenticated principal validation.
Cart models persist carts, items, recoveries, vouchers, and sanitized analytics events through SQLAlchemy.
The scan service queries eligible carts using a composite store/status/activity index.
It processes bounded batches, commits each batch independently, and returns an integrity-protected cursor.
Voucher, notification, order-event, and analytics providers are injected protocols rather than hard dependencies.
Recovery queries enforce both store ownership and customer ownership before returning a DTO.
Analytics uses bounded cursor pagination and Decimal aggregation with explicit currency handling.
The web, mobile, and POS channels can use the same tenant-scoped APIs and event contracts.
Independent commits and idempotency keys tolerate retries and eventual consistency between services.
The trade-off is that a multi-batch scan is eventually consistent and requires scheduler continuation.
An outbox or durable provider handoff is the next production enhancement for cross-service delivery guarantees.
