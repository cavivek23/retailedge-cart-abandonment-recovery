# Copilot Prompt Chain

## Prompt Sequence

### Prompt 1: Baseline Inventory Generation

Feature: Copilot Chat. Technique: minimal baseline prompt. Evidence: Screenshots 4-6.

```text
Generate an Inventory model and an Inventory service with create, update-stock, get-by-product, and delete functions. Use a database.
```

### Prompt 2: Inventory Review

Feature: Copilot Chat with `#file` references. Techniques: role-based, constraint-based, and decomposition. Evidence: Screenshots 7-8.

```text
Review the Inventory Sync module for production-readiness. Use #file:src/inventory/models.py, #file:src/inventory/service.py, #file:src/inventory/database.py, and #file:src/inventory/exceptions.py. Do not modify any files. Identify security, authorisation, validation, architecture, database, concurrency, retail-domain, and testing issues. For every issue, provide the file and function, severity, impact, and recommended fix. Specifically check JWT algorithm pinning, exp/iat/nbf validation, DTO whitelisting, Broken Object Level Authorisation, store_id scoping, decimal or float usage, negative stock, and cursor pagination.
```

### Prompt 3: Inventory Remediation

Feature: Copilot Agent/Edit with a proposed diff. Techniques: role-based, constraint-based, and iterative refinement. Evidence: Screenshots 9-12.

```text
Remediate the Inventory Sync module using #file:src/inventory/models.py, #file:src/inventory/service.py, #file:src/inventory/database.py, and #file:src/inventory/exceptions.py.

Rewrite it to production standards using layered architecture: model, repository, DTO/schema, service, controller/route, and authentication middleware.

Requirements:
- Use SQLAlchemy ORM only; no raw SQL or direct database-driver calls.
- Use Pydantic DTOs with explicit field whitelisting for every write endpoint.
- Pin JWT verification to RS256 or HS256 and reject alg: none and algorithm confusion.
- Validate JWT signature and exp, iat, and nbf claims; invalid claims must return HTTP 401 with machine-readable error codes.
- Extract and validate store_id, customer_id, role, and scope from the authenticated principal.
- Enforce store ownership and role-based authorization in both routes and services.
- Prevent Broken Object Level Authorisation on every resource-by-ID operation.
- Prevent negative stock using DTO validation, service validation, and a database check constraint.
- Preserve Decimal-safe quantity handling and define the permitted scale.
- Handle optimistic concurrency conflicts and return a stable conflict error.
- Load database configuration from environment variables.
- Add structured audit logging without logging credentials, tokens, or personally identifiable information.
- Add API error handlers and tests for cross-store access, non-admin access, invalid tokens, negative stock, and stale versions.
- Do not omit migration or configuration notes.

Show the proposed diff before applying changes.
```

### Prompt 4: Technical Specification

Feature: Copilot Chat with `#file`. Techniques: decomposition, specificity, and constraint-based prompting. Evidence: Screenshot 13.

```text
Create SPEC.md for the RetailEdge Cart Abandonment Recovery feature. Use #file:.github/copilot-instructions.md and the assessment requirements as context.

Do not write implementation code. Produce a detailed, implementation-ready specification containing:

1. Feature overview and business objective.
2. Abandoned cart, cart item, recovery, voucher, and analytics event models with fields, types, relationships, and Decimal currency rules.
3. At least two database indexes, including columns, index type, and the query each supports.
4. Service responsibilities with inputs, outputs, and business rules.
5. API specifications for:
	POST /scan/abandoned-carts
	GET /carts/:id/recovery
	GET /analytics/recovery
6. Cursor-based pagination using cursor and limit, with nextCursor and hasMore.
7. Configurable batchSize, independent commits, and restartability from the last successful cursor.
8. Authentication, authorization, store_id/customer_id scoping, DTO whitelisting, and Broken Object Level Authorisation requirements.
9. Edge cases and validation rules.
10. Notification, voucher, order-event, and analytics dependencies.
11. Assumptions and open questions for the product team.

Keep the specification precise enough to implement without inventing missing business rules.
```

### Prompt 5: Cart Models

Feature: Copilot Agent/Edit with `#file` and proposed diff. Techniques: decomposition, specificity, and constraint-based prompting. Evidence: Screenshots 14-16.

```text
Implement only the Cart Abandonment Recovery ORM models described in #file:SPEC.md, following #file:.github/copilot-instructions.md.

Create the model layer under src/cart/ for:
- AbandonedCart
- CartItem
- Recovery
- Voucher
- AnalyticsEvent

Requirements:
- Use SQLAlchemy 2.x typed ORM models.
- Use UUID or validated immutable identifiers.
- Use Decimal-safe Numeric columns: NUMERIC(19,4) for money and NUMERIC(18,3) for quantities.
- Never use float for currency or quantities.
- Add UTC timestamps and optimistic-concurrency version fields.
- Add enums and database check constraints for valid statuses, non-negative money, positive quantities, valid versions, and valid discount ranges.
- Add all indexes declared in SPEC.md, including the abandonment scan composite index, recovery indexes, analytics index, and unique idempotency constraints.
- Store only voucher code_hash and optional code_last4; never store plaintext voucher codes.
- Include store_id and customer_id for tenant and ownership checks.
- Use explicit relationships and foreign keys.
- Do not create services, routes, or tests yet.
- Add concise public class and method documentation.
- Show the proposed diff before applying changes.
```

### Prompt 6: Cart Services

Feature: Copilot Agent/Edit with multiple `#file` references. Techniques: decomposition, role-based, and constraint-based prompting. Evidence: Screenshots 17-19.

```text
Implement the repository, DTO/schema, and service layers for Cart Abandonment Recovery. Use #file:SPEC.md, #file:.github/copilot-instructions.md, and #file:src/cart/models.py.

Create only these layers under src/cart:
- repositories.py
- schemas.py
- services.py
- dependency interfaces or protocols for notification, voucher, order events, and analytics

Implement:

1. AbandonmentScanService
- Accept an authenticated admin principal, configurable batchSize, and an opaque cursor.
- Query only the authenticated store.
- Find carts inactive for at least 30 minutes using the composite abandonment index.
- Use stable cursor ordering by last_activity_at and cart ID.
- Process one batch at a time.
- Commit each batch independently.
- Advance the cursor only after successful commit.
- Resume safely from the last successful cursor.
- Create or reuse recovery records idempotently.
- Select the most recent cart when a customer has multiple eligible abandoned carts.
- Evaluate the Decimal cart total against a configurable voucher threshold.
- Generate or reuse a one-time voucher for qualifying carts.
- Trigger a notification with a redacted cart summary and optional voucher delivery data.
- Record sanitized analytics events.
- Never log customer PII, voucher plaintext, tokens, or provider payloads.

2. RecoveryTrackingService
- Consume authenticated order-completion events.
- Match cart, store, and customer ownership before updating recovery.
- Reject cross-customer or cross-store events.
- Mark recovery as converted only within 24 hours.
- Mark eligible recoveries as expired after 24 hours.
- Use optimistic concurrency and idempotent event keys.
- Record every state transition.

3. RecoveryQueryService
- Fetch one cart recovery record.
- Enforce store ownership and customer ownership or explicit staff permission at the service layer.
- Return DTOs that never expose voucher plaintext, customer contact data, ORM internals, or provider payloads.

4. RecoveryAnalyticsService
- Query store-scoped recovery events.
- Require admin role and analytics:read scope.
- Use cursor and limit pagination and return nextCursor and hasMore.
- Use Decimal aggregation and reject mixed currencies unless explicitly grouped.

Use injected protocols for repositories and external services so tests can mock them. Keep routes and tests out of this change. Show the proposed diff before applying it.
```

### Prompt 7: Cart API Routes

Feature: Copilot Agent/Edit with `#file` references. Techniques: decomposition, specificity, and explicit security constraints. Evidence: Screenshots 20-22.

```text
Implement only the FastAPI route and application wiring layer for Cart Abandonment Recovery. Use #file:SPEC.md, #file:.github/copilot-instructions.md, #file:src/cart/models.py, #file:src/cart/schemas.py, #file:src/cart/services.py, and #file:src/inventory/auth.py.

Create:
- src/cart/routes.py
- src/main.py or the smallest application entry point needed

Implement these exact endpoints:

1. POST /scan/abandoned-carts
- Admin role only.
- Require recovery:write scope.
- Use ScanRequest with extra fields forbidden.
- Accept batchSize and an opaque cursor.
- Never accept store_id from the request body.
- Return scan results with processedCount, eligibleCount, startedCount, failedCount, nextCursor, and hasMore.
- Return HTTP 403 with a machine-readable error code for non-admin tokens.

2. GET /carts/{cart_id}/recovery
- Require authenticated customer ownership or authorized staff access.
- Enforce store_id and customer_id ownership before calling the service.
- Return HTTP 403 for an authenticated user attempting to access another customer’s recovery.
- Never return voucher plaintext, customer PII, ORM internals, or provider payloads.

3. GET /analytics/recovery
- Admin role and analytics:read scope only.
- Use AnalyticsFilters with from, to, status, cursor, limit, and groupByCurrency.
- Return nextCursor and hasMore.
- Return HTTP 403 with a machine-readable error code for non-admin tokens.

Implementation rules:
- Use dependency injection for the database session, authenticated principal, services, and external provider protocols.
- Map authentication failures to HTTP 401 and authorization failures to HTTP 403.
- Map not-found, validation, conflict, and domain errors to stable safe JSON error objects.
- Do not expose stack traces, SQL details, tokens, voucher codes, or customer PII.
- Add correlation ID handling without logging sensitive values.
- Do not implement tests in this change.
- Show the proposed diff before applying changes.
```

### Prompt 8: Cart Tests

Feature: Copilot Agent/Edit with `#file` references. Techniques: decomposition, few-shot acceptance criteria, and constraint-based prompting. Evidence: Screenshots 23-27.

```text
Generate the Cart Abandonment Recovery test suite under tests/test_cart_recovery.py. Use #file:SPEC.md, #file:.github/copilot-instructions.md, #file:src/cart/models.py, #file:src/cart/schemas.py, #file:src/cart/repositories.py, #file:src/cart/services.py, #file:src/cart/routes.py, and #file:src/main.py.

Create at least 8 focused tests using pytest and FastAPI TestClient or service-level fakes. Do not weaken production authorization or validation to make tests pass.

The tests must cover:

1. A cart is correctly identified for recovery after at least 30 minutes of inactivity.
2. A voucher is generated when the Decimal cart total exceeds the configured threshold.
3. No voucher is generated below the configured threshold.
4. Recovery is marked converted when a matching order completes within 24 hours.
5. Recovery is marked expired when the 24-hour window lapses.
6. When a customer has multiple abandoned carts, only the most recent cart triggers recovery.
7. Broken Object Level Authorisation: customer A attempting to access customer B’s recovery returns HTTP 403.
8. Admin-only scan endpoint: a non-admin token returns HTTP 403 with a machine-readable error code.

Also add tests for at least two of:
- alg: none rejection
- missing exp, iat, or nbf claim
- cross-store access
- invalid cursor
- DTO unknown-field rejection
- negative stock or invalid Decimal precision
- stale optimistic-concurrency update
- duplicate order-event idempotency
- mixed-currency analytics rejection

Use Decimal values instead of floats. Use fake notification, voucher, analytics, and clock dependencies where needed. Keep test data free of real customer PII and secrets. Show the proposed diff before applying changes.
```

The prompts were executed in the order shown. The chain uses Chat, Agent/Edit mode, file references, explicit decomposition, role framing, iterative refinement, few-shot acceptance criteria, and security constraints.

## Refined Prompt Example

**Before:**

```text
Generate an Inventory model and an Inventory service with create, update-stock, get-by-product, and delete functions. Use a database.
```

**Problem found:** The output had no controller, DTO allow-list, authentication, tenant authorization, JWT validation, database configuration boundary, or tests. It also required human review for database constraints and concurrency semantics.

**Improved prompt:** The remediation prompt explicitly required model/repository/DTO/service/controller layers, ORM-only access, DTO whitelisting, RS256 or HS256 pinning, `exp`/`iat`/`nbf` validation, store scoping, BOLA checks, non-negative stock constraints, environment configuration, structured logging, and security tests.

## Post-Generation Corrections

- Added the missing repository and controller boundaries around the low-effort Inventory output.
- Replaced untrusted service-level store arguments with authenticated principal context and repeated store authorization in the service.
- Added Pydantic DTOs with `extra="forbid"` to prevent mass assignment.
- Added JWT algorithm pinning and required `exp`, `iat`, and `nbf` claims.
- Added database check constraints for non-negative stock and positive versions.
- Added optimistic concurrency and idempotent operation handling.
- Added environment-based database configuration and redacted structured audit logging.
- Added Cart model constraints, tenant indexes, cursor services, BOLA checks, and Decimal-only monetary handling.
- Added a root `pytest.ini` after the first test run exposed a working-directory import-path issue. This made `python -m pytest -q` work from the permitted `OTJ` folder.
- Independently verified that the Cart application routes appeared in OpenAPI and returned a machine-readable 401 without a token.
- Independently verified the final suite: 19 passed, 0 failed.

Copilot output was reviewed before acceptance. Human judgment was required for tenant boundaries, database invariants, transaction behavior, test environment behavior, and the distinction between an apparently complete service and a complete production feature.
