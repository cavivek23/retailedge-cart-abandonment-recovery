# Impact Analysis: Second Recovery Touchpoint and Recommendations

## Scope Change

The requested change adds a follow-up notification six hours after the first notification, with a higher discount, and sends abandoned-cart data to the recommendation engine so suggested products can appear in recovery email.

## Required Changes

### Data model

- Add `touchpoint_number`, `first_notified_at`, `follow_up_due_at`, `follow_up_sent_at`, and `follow_up_discount` to `Recovery`.
- Add notification-attempt records or an outbox keyed by `(recovery_id, touchpoint_number)` for idempotency.
- Add recommendation snapshot metadata such as `recommendation_request_id`, model/version, generated time, and selected product IDs. Do not store unnecessary customer PII.
- Add state-transition and retry constraints so a follow-up cannot be sent before six hours or more than once.

### Services and API

- Extend the scan/scheduler workflow to select `NOTIFICATION_SENT` recoveries whose first notification is at least six hours old and whose recovery window remains open.
- Add a follow-up voucher policy with an explicit maximum discount and margin guardrail.
- Add a recommendation-provider interface with timeout, consent, tenant, and idempotency controls.
- Extend analytics with touchpoint number, redemption, opt-out, recommendation request, and provider failure events.
- Keep public recovery responses free of voucher plaintext and recommendation-provider payloads.

### Tests requiring updates

- Voucher threshold and discount policy tests
- Notification idempotency tests
- Recovery conversion and expiry tests
- Multiple-cart priority tests
- Analytics aggregation and pagination tests
- Opt-out and consent tests
- Cross-store and cross-customer authorization tests
- Provider timeout and retry tests
- Migration compatibility tests for existing recovery rows

## Effort and Risk

Estimated effort is 5-8 engineering days for schema migration, service changes, provider integration, tests, and operational dashboards, excluding external recommendation-engine contract approval. Risk is medium-high because the change affects customer communications, discount margin, privacy, and two external systems.

The higher discount can improve redemption but increases margin exposure and may train customers to wait for the second message. Sending follow-up mail must respect unsubscribe, consent, frequency-cap, regional marketing, and transactional-versus-promotional rules. A suppression check must happen immediately before each send.

## Recommendation

Phase the change. First deliver the follow-up state machine, consent enforcement, discount caps, idempotency, and analytics behind a feature flag. Integrate recommendation content in a second phase after the data contract, privacy review, relevance evaluation, and provider SLA are approved. This keeps the recovery reliability work independently releasable and limits simultaneous operational risk.

## Recommendation Engine Data Contract

Share only the minimum data needed: store-scoped product IDs, quantities, cart currency, bounded price bands or totals where justified, category identifiers, and an opaque recovery or customer reference. Do not share customer name, email, address, raw JWT claims, voucher codes, or full event payloads. The recommendation engine should return product IDs, ranking scores, model version, and expiry time; the email service resolves contact information separately.

A new data-sharing agreement, retention decision, access-control review, and privacy/security assessment are required unless an existing approved contract explicitly covers abandoned-cart behavioral data. Customer opt-out and deletion requests must propagate to recommendation snapshots and derived caches.

## Migration Plan

Use an additive, backward-compatible migration: add nullable touchpoint and recommendation columns, deploy code that reads old rows with defaults, backfill only safe timestamps from existing notification metadata, then enforce non-null constraints after observation. Deploy new writers after the schema is available. Use an outbox table with unique idempotency keys for follow-up work. Avoid downtime with online index creation where supported, bounded backfill batches, and rollback-compatible code. Existing recoveries should not automatically receive a second message until consent, eligibility, and timing have been evaluated.

## How Copilot Assisted This Analysis

Copilot was prompted with the existing `SPEC.md`, model fields, service flow, and the scope-change request to identify data-model, service, API, migration, dependency, privacy, and testing impacts. Its output was used to expand the checklist, but human judgment was required for discount-margin exposure, marketing opt-out obligations, minimum-necessary data sharing, migration ordering, and the recommendation to phase the work behind a feature flag.
