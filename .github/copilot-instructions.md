# RetailEdge Engineering Instructions

## Technology and Architecture

- Use Python 3.12, FastAPI, SQLAlchemy 2.x, Pydantic v2, PyJWT, and pytest.
- Use layered architecture: model -> repository -> service -> controller/route.
- Keep business rules in services, persistence in repositories, and HTTP concerns in controllers.
- Use ORM queries and migrations; never use raw SQL or direct database-driver calls in services.

## Retail Domain Rules

- Represent currency, unit prices, discounts, tax, and cart totals with Decimal or a database numeric type. Never use float for money.
- Store currency explicitly and calculate tax using the configured jurisdiction and Decimal arithmetic.
- Inventory stock must never become negative; concurrent stock updates must detect conflicts.
- Treat customer names, email addresses, addresses, and identifiers as personally identifiable information. Do not log them in plain text or include them unnecessarily in events.
- Every customer, cart, order, voucher, and inventory query must be scoped to the authenticated store context.

## Coding Standards

- Use descriptive snake_case names for Python symbols and PascalCase for classes.
- Add type annotations to public functions and document public methods.
- Validate all external input with Pydantic DTOs.
- Raise specific domain exceptions and map them to stable machine-readable API errors.
- Use structured logs with correlation IDs. Never log credentials, tokens, voucher secrets, or raw personally identifiable information.

## Security Rules

- JWT verification must pin the allowed algorithm to RS256 or HS256. Reject alg: none and algorithm confusion at the middleware layer.
- JWT middleware must verify the signature and explicitly validate exp, iat, and nbf. Any missing or invalid claim returns HTTP 401 with a specific error code.
- Middleware must extract and validate store_id, customer_id, role, and scope claims.
- All write endpoints must accept DTOs that explicitly whitelist permitted fields. Never bind request bodies directly to ORM models.
- Do not hardcode passwords, tokens, private keys, or connection secrets.
- Validate and constrain all external data before it reaches the database or an external service.

## Authorisation Rules

- Authentication is not authorisation. Every resource-by-ID endpoint must verify ownership or explicit permission for that resource.
- A customer must not access another customer's carts, orders, vouchers, or inventory records.
- Store-level operations must use the authenticated store_id and must not accept an untrusted store context from the request body.
- Admin-only operations, including abandonment scans and analytics summaries, must return HTTP 403 with a machine-readable error code for non-admin tokens.
- Enforce Broken Object Level Authorisation checks in both controllers and services for defense in depth.

## Performance and Data Access

- Every list endpoint must use cursor-based pagination with cursor and limit request parameters.
- List responses must include nextCursor and hasMore. Do not use page, offset, or skip pagination.
- Abandonment scans must accept configurable batchSize, commit each batch independently, and resume from the last successfully processed cursor.
- Declare at least two ORM database indexes in SPEC.md and in model definitions. Each declaration must identify columns, index type, and the query it supports.
- Use bounded queries and avoid loading an unbounded customer or cart dataset into memory.

## Testing Expectations

- Add unit tests for domain services and endpoint tests for authentication, authorisation, validation, and error responses.
- Test happy paths, boundary times, duplicate/retry behavior, concurrency conflicts, and external-service failures.
- Use dependency injection and mocks or fakes for notification, voucher, analytics, and external identity services.
- Every admin endpoint must have a non-admin 403 test.
- Every resource-by-ID endpoint must have a cross-customer or cross-store 403 test.
- Tests must confirm Decimal behavior and must fail if float arithmetic is introduced.
