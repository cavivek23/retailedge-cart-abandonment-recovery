# GitHub Copilot Tool Strategy

## Feature Usage Log

| # | Where used | Feature | Why this feature | Result |
|---|---|---|---|---|
| 1 | Initial Inventory generation | Copilot Chat | Required baseline prompt and untouched output | Exposed why low-context prompts need review. |
| 2 | Inventory review | Chat with `#file` references | Kept the review grounded in exact source files | Identified missing security and architecture boundaries. |
| 3 | Inventory remediation | Agent/Edit diff preview | Generated coordinated changes across several layers | Added DTOs, repository, auth, routes, constraints, and tests. |
| 4 | Specification drafting | Chat with `#file` | Converted the brief into an implementation contract | Produced models, indexes, API contracts, and open questions. |
| 5 | Cart model implementation | Agent/Edit | Created related ORM models consistently | Added constraints, relationships, indexes, and Decimal columns. |
| 6 | Cart services | Agent/Edit with multiple file references | Implemented a decomposed service slice with injected protocols | Added scan, recovery, analytics, cursor, and idempotency behavior. |
| 7 | API routes | Agent/Edit | Coordinated dependency injection and error mapping | Added the three required endpoints and BOLA checks. |
| 8 | Tests | Agent/Edit | Generated a consistent test matrix from explicit acceptance criteria | Produced 19 passing tests after path configuration was corrected. |

## Scenario Responses

1. **Understand a legacy 600-line cart service:** Use Copilot Chat with `#file` references and ask for a responsibility map, call graph, state transitions, and risk list in Ask mode. This preserves the code while giving a reviewable explanation before edits.

2. **Add cursor pagination to 10 endpoints:** Use Agent/Edit with the project context file and two representative endpoints as examples, then require a shared cursor contract and a diff review. This coordinates repetitive changes while keeping the human review focused on ordering, tenant binding, and response compatibility.

3. **Verify Decimal discount calculations:** Use Chat with the calculation file and ask for a review that searches for float construction, rounding points, precision, currency mixing, and boundary tests. Then use `/tests` or an explicit test prompt to generate Decimal boundary cases.

4. **Enforce retail-aware suggestions for everyone:** Use `.github/copilot-instructions.md` as the repository-wide context artifact. This is more reliable than repeating retail rules in individual prompts because the guidance applies automatically to future contributors.

5. **Review JWT middleware:** Use Chat with the middleware file and a security-focused prompt requiring algorithm pinning, `alg: none` rejection, signature verification, and `exp`/`iat`/`nbf` tests. Keep the mode non-editing until a human confirms the findings.

6. **Generate four repositories consistently:** Use Agent/Edit with the context file, model files, and one approved repository as a pattern. Explicitly require DTO boundaries, store scoping, transaction handling, and no raw SQL, then review the combined diff.

## Security Review

- **JWT algorithm confusion:** An AI-generated verifier may trust the token header or permit `none`, allowing forged identity claims. In retail this can expose carts or inventory across stores. Pin RS256 or HS256 in middleware, reject mismatches, and test forged tokens.
- **Mass assignment:** Binding JSON directly to an ORM model may let a caller set `store_id`, role-related fields, version, or ownership fields. This can create unauthorized inventory or voucher changes. Use DTOs with explicit fields and reject unknown fields.
- **Broken Object Level Authorisation:** A valid token may still request another customer's cart by ID. This exposes purchase intent and recovery data. Check authenticated store and customer ownership in routes and services, with a 403 or documented existence-hiding response.
- **Currency precision:** AI may use float arithmetic for discounts, creating reconciliation and margin errors. Use Decimal and database Numeric types with explicit scale.

## Limitations Encountered

1. The low-effort Inventory prompt generated a partial model/service solution without controller, DTO, JWT, BOLA, or test boundaries.
2. The accepted Cart implementation required independent validation of scan state transitions and abandonment timestamps because a service can process already-abandoned records without proving how the timestamp was established.
3. Running pytest from the parent `OTJ` folder initially failed to import `src`; the code was valid, but the project needed explicit `pytest.ini` path configuration. This was found by executing the test command rather than assuming the generated setup was portable. A later transition regression was caught and fixed through the focused test loop, resulting in 20 passing tests.

Human review remained necessary for security ownership semantics, business state transitions, external-provider failure behavior, and environment-specific test execution.
