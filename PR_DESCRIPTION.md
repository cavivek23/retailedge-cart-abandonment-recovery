# Pull Request: Cart Abandonment Recovery

## Summary

This change adds a RetailEdge Cart Abandonment Recovery workflow and remediates the inherited Inventory Sync module. The feature identifies eligible carts, starts one idempotent recovery workflow per customer-priority cart, optionally issues a Decimal-safe voucher, sends a notification, and records analytics events. Recovery tracking handles order conversion within 24 hours and expiry after the recovery window. The API exposes admin-only scan and analytics operations plus an ownership-protected recovery endpoint.

## AI Tool Disclosure

GitHub Copilot Chat, file references, Agent/Edit mode, and proposed diffs were used throughout the assessment. The initial Inventory model and service were generated with the mandated low-effort prompt and preserved for review. Copilot output was accepted for scaffolding only after the proposed diff was inspected. Human review and corrections were applied to authorization, JWT handling, DTO boundaries, database constraints, Decimal arithmetic, idempotency, test behavior, and configuration.

Estimated implementation split: approximately 65% Copilot-generated scaffolding and 35% human-directed design, correction, validation, and documentation. This is an estimate rather than an automated measurement.

The context file improved output quality by making retail-specific constraints explicit. For example, later prompts produced Decimal `Numeric` columns, store-scoped indexes, DTO rejection of unknown fields, and security tests instead of repeating the incomplete Inventory pattern.

## Security Note

- JWT verification pins the configured algorithm to RS256 or HS256 and rejects algorithm mismatch, including `alg: none`.
- `exp`, `iat`, and `nbf` are required and validated; invalid claims produce machine-readable 401 errors.
- Write requests use Pydantic DTOs with explicit fields and `extra="forbid"`; ORM models are not bound directly to request bodies.
- Every resource lookup uses authenticated store context, and customer recovery access verifies ownership.
- Scan and analytics operations require an admin role and the appropriate scope, returning 403 with a stable error code for non-admin callers.
- Voucher plaintext, customer PII, tokens, and provider payloads are excluded from responses and audit logs.

## Testing

The suite contains 20 passing tests. Required coverage includes 30-minute eligibility, pending-checkout abandonment transition, voucher threshold behavior, conversion, expiry, latest-cart priority, customer BOLA denial, and non-admin scan denial. Additional coverage includes algorithm confusion, missing JWT claims, DTO unknown fields, invalid cursors, duplicate order-event idempotency, mixed-currency analytics, negative stock, stale versions, and cross-store access.

Known gaps are integration tests against real notification, voucher, analytics, and order-event providers; production PostgreSQL migration execution; and full clock-skew testing across distributed workers.

## Risk and Trade-off

The scan processes one bounded batch per request and commits each batch independently, which limits rollback scope and supports restartability. The trade-off is that a multi-batch scan is eventually consistent and requires a durable caller or scheduler to continue with `nextCursor`. Provider idempotency is also required to prevent retries from issuing duplicate vouchers or notifications.

## Self-Review Checklist

- [x] No hardcoded secrets or real PII
- [x] External inputs use validation models
- [x] Specific domain errors are mapped to safe API responses
- [x] Code follows `.github/copilot-instructions.md`
- [x] Copilot output was reviewed before acceptance
- [x] Happy paths, edge cases, and security failures are tested
- [x] JWT algorithm is pinned; `alg: none` is rejected
- [x] `exp`, `iat`, and `nbf` are validated
- [x] Write DTOs whitelist fields
- [x] BOLA checks exist at route and service layers
- [x] Decimal is used for money and quantity arithmetic
- [x] Cursor pagination is used for list/analytics surfaces
- [x] Database indexes and constraints are declared

## Peer Review Simulation

| # | Code location | Requested change | Why it matters |
|---|---|---|---|
| 1 | `src/cart/routes.py`, `get_cart_recovery` | Keep the route dependency and service-level ownership checks; add an explicit test for a manager without `recovery:read` attempting another customer's recovery. | Authentication alone does not prove resource ownership; this protects customer recovery data from BOLA. |
| 2 | `src/cart/services.py`, `AbandonmentScanService.scan` | Persist the cart's abandonment timestamp and state transition in the same idempotent batch transaction before dispatching recovery work. | Analytics and customer-support workflows need a reliable abandonment timestamp, and retries must not create inconsistent state. |
| 3 | `src/cart/services.py`, voucher and notification flow | Use an outbox or durable provider handoff for notification dispatch and document compensation when voucher issuance succeeds but notification delivery fails. | Retail retries must not issue duplicate discounts or silently lose a recovery message during provider outages. |

### AI Blind Spot Explanation

AI-generated code often recognizes that a route has authentication but does not reason fully about object ownership and tenant context. In this retail system, a valid customer token must still be rejected when it targets another customer's cart, so a human must inspect the relationship between the token claims, route ID, database query, and service rule.
