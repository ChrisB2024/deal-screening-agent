# Module: Dashboard API

> Core business module. Parent: `../01_decomposition.md`
> Build order: step 11 (depends on all prior modules)

---

## 1. Purpose

Expose the analyst-facing HTTP API for browsing the deal queue, viewing scored deals, recording PASS/PURSUE decisions, and managing tenant criteria configurations — and serve the minimal web UI that consumes it.

## 2. Responsibilities

- Provide authenticated HTTP endpoints for listing, filtering, sorting, and paginating deals
- Return a single deal with its latest extraction, score, and decision history
- Record analyst decisions (`PASS` / `PURSUE`) with optional notes, transitioning the deal `SCORED → DECIDED`
- Expose criteria configuration CRUD: list, get, create, activate, deactivate
- Trigger manual re-scoring of a deal against the current active config
- Generate presigned URLs for original document download via the `StorageClient` interface
- Define and enforce the standard error envelope used across all modules
- Serve the minimal UI (static bundle) that consumes these endpoints
- Enforce tenant scoping on every read and write
- Capture the feedback signal (decisions + notes) that the future v2 ML scoring strategy will consume

## 3. Non-Responsibilities

- **Does not ingest documents.** The upload endpoint lives in Ingestion Service; Dashboard API links to it.
- **Does not extract or score.** Those are the respective services; Dashboard reads their outputs from the DB.
- **Does not authenticate.** Auth Service middleware handles JWT verification before requests reach any handler.
- **Does not rate-limit.** Rate Limiter middleware applies per-scope.
- **Does not manage users, tenants, or billing.** Those are handled by Auth Service and (future) a billing module.
- **Does not render documents.** It provides presigned download URLs; the browser handles rendering.
- **Does not push real-time updates.** v1 is request/response only; WebSocket/SSE is flagged as a Medium-risk evolution item in `01_decomposition.md`.

## 4. Inputs / Outputs

### Standard error envelope

Used by every endpoint in every module, not just Dashboard:

```json
{
  "error": {
    "code": "DEAL_NOT_FOUND",
    "message": "No deal with that ID exists in your workspace.",
    "details": { "deal_id": "deal_01HW..." },
    "request_id": "req_01HW..."
  }
}
```

`code` is a stable machine-readable string. `message` is user-safe (no stack traces, no DB details, no internal paths). `details` is an optional object with structured context. `request_id` matches the `X-Request-Id` response header.

### Key request/response shapes

**Deal list item** (summary view for the queue):
```json
{
  "deal_id": "deal_01HW...",
  "state": "SCORED",
  "overall_score": 72,
  "confidence": "MEDIUM",
  "required_criteria_passed": true,
  "sector": "B2B SaaS",
  "revenue": 12500000,
  "geography": "North America",
  "ingested_at": "2026-04-11T13:58:02Z",
  "scored_at": "2026-04-11T14:03:25Z",
  "has_decision": false
}
```

**Deal detail** (full view): includes the latest `ExtractedDeal`, latest `ScoredDeal`, decision history, and a presigned `document_url` valid for 15 minutes.

## 5. State Machine

Dashboard API is the sole writer of the `SCORED → DECIDED` transition and the `DECIDED → ARCHIVED` transition. All other transitions are handled by upstream services. Every decision write goes through `transition_deal_state()` so the audit log is written in the same transaction.

Internally, each request follows a standard FastAPI middleware chain:

```
REQUEST → RequestId → RateLimiter → Auth → TenantScope → Handler → Response
                                                    │
                                                    ▼
                                               403/404/422
```

`TenantScope` is a dependency that extracts `tenant_id` from the verified JWT and injects it into every DB query. There is no code path that queries the `deals` table without a `tenant_id` filter.

## 6. Data Model

Dashboard API does not own new tables beyond the `deal_decisions` table. All other reads go against `deals`, `deal_extractions`, `deal_scores`, `criteria_configs`, and `deal_audit_log`.

### `deal_decisions` table

```sql
CREATE TABLE deal_decisions (
    decision_id  TEXT PRIMARY KEY,          -- ULID
    deal_id      TEXT NOT NULL REFERENCES deals(deal_id),
    tenant_id    TEXT NOT NULL,
    score_id     TEXT NOT NULL REFERENCES deal_scores(score_id),
    decided_by   TEXT NOT NULL,             -- user_id
    decision     TEXT NOT NULL,             -- PASS | PURSUE
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_decision CHECK (decision IN ('PASS','PURSUE'))
);

CREATE INDEX idx_decisions_deal ON deal_decisions(deal_id, created_at DESC);
CREATE INDEX idx_decisions_tenant_created ON deal_decisions(tenant_id, created_at DESC);
```

A decision references the exact `score_id` it was made against. This matters for the future feedback loop: when training the v2 ML scoring strategy, you need to know which extracted features and which score the analyst saw at the moment of decision — not the latest score, which may have been re-computed.

A deal can have multiple decision rows over time (decisions are immutable, but re-decisions after re-scoring are allowed). The "current" decision is `ORDER BY created_at DESC LIMIT 1`. Dashboard UI surfaces only the most recent.

## 7. API Contract

All endpoints are prefixed `/api/v1`. All require a valid JWT unless marked public. All responses include `X-Request-Id`.

### Deal endpoints

**`GET /deals`** — list the tenant's deals

Query parameters:
- `state`: filter by deal state (repeatable)
- `min_score`, `max_score`: score range
- `confidence`: `HIGH` / `MEDIUM` / `LOW` (repeatable)
- `required_passed`: `true` / `false`
- `has_decision`: `true` / `false`
- `sort`: `score_desc` (default), `score_asc`, `ingested_desc`, `ingested_asc`
- `cursor`: opaque pagination cursor
- `limit`: 1–100, default 25

Response: `{ "items": [...], "next_cursor": "...", "total_estimate": 142 }`. Pagination is cursor-based (not offset) to keep performance stable as tenants grow.

**`GET /deals/{deal_id}`** — deal detail. 404 if the deal does not belong to the caller's tenant (never 403 — we don't leak existence across tenants).

**`POST /deals/{deal_id}/decisions`** — record a PASS/PURSUE decision

Body: `{ "decision": "PASS" | "PURSUE", "notes": "optional string, max 2000 chars" }`

Preconditions: deal state must be `SCORED`. If already `DECIDED`, returns `409 DECISION_ALREADY_RECORDED` with the existing decision. If `EXTRACTED` or earlier, returns `409 DEAL_NOT_READY`.

**`POST /deals/{deal_id}/rescore`** — manually trigger re-scoring against the current active config. Returns the new `ScoredDeal` synchronously (scoring is pure computation, so no job queue needed). The deal must be in `EXTRACTED`, `SCORED`, or `DECIDED` state. Re-scoring a `DECIDED` deal creates a new score row but does not affect the existing decision — the analyst sees both.

**`GET /deals/{deal_id}/document`** — returns a presigned URL (JSON body, not a redirect) valid for 15 minutes. Emits an audit log entry for every issuance.

### Criteria endpoints

**`GET /criteria`** — list all configs for the tenant (active flag included)

**`GET /criteria/{config_id}`** — get a specific config

**`POST /criteria`** — create a new config. Body is the `CriteriaConfig` shape from `scoring_engine.md`, minus `config_id`, `tenant_id`, and `version` (server-assigned). Schema validation is strict: unknown fields rejected, operator/field combinations checked. New configs are created as inactive by default.

**`POST /criteria/{config_id}/activate`** — atomically deactivate the current active config and activate the specified one. Wrapped in a single DB transaction so the "at most one active" invariant holds.

**`DELETE /criteria/{config_id}`** — only allowed if the config has never been used to score a deal. Otherwise returns `409 CONFIG_IN_USE` and the client must deactivate instead.

### Health endpoint

**`GET /health`** — public, unauthenticated. Returns `{"status": "ok"}` plus liveness checks for DB and storage. Used by the load balancer and monitoring.

### Error codes (stable)

`DEAL_NOT_FOUND`, `DEAL_NOT_READY`, `DECISION_ALREADY_RECORDED`, `CONFIG_NOT_FOUND`, `CONFIG_IN_USE`, `CONFIG_INVALID`, `VALIDATION_ERROR`, `RATE_LIMITED`, `UNAUTHORIZED`, `FORBIDDEN`, `INTERNAL_ERROR`.

## 8. Invariants

1. No query against `deals`, `deal_extractions`, `deal_scores`, `criteria_configs`, or `deal_decisions` runs without a `tenant_id` filter sourced from the verified JWT. This is enforced by a repository layer that refuses to build a query without a tenant scope.
2. Decisions are immutable once written. No `UPDATE` or `DELETE` on `deal_decisions` rows. Re-decisions append a new row.
3. A decision write and the `SCORED → DECIDED` state transition happen in a single DB transaction with the audit log entry. All three or none.
4. Cross-tenant access attempts return `404 DEAL_NOT_FOUND`, never `403`. We do not confirm existence of resources in other tenants.
5. Presigned document URLs are never issued for deals in other tenants, and every issuance is audit-logged with `user_id`, `deal_id`, and `expires_at`.
6. All list endpoints use cursor-based pagination. Offset-based pagination is forbidden because it degrades with tenant size and leaks total counts precisely.
7. Every write endpoint is idempotent on the client's supplied `Idempotency-Key` header if provided — replayed requests return the original response without side effects.
8. `notes` text on decisions is stored as-is but escaped on every render path. The DB is not the XSS defense; the renderer is.
9. `criteria_configs` writes go through a validating loader that matches the Scoring Engine's validator exactly — the two use the same schema source of truth.

## 9. Trust Boundaries

| Boundary | Validation |
|---|---|
| Browser → Dashboard API | TLS, JWT verified, CORS allowlist, rate limited per user, request body schema-validated |
| Dashboard API → DB | ORM parameterized queries only, tenant_id injected from auth context, repository layer enforces tenant scope |
| Dashboard API → StorageClient (presigned URLs) | Tenant-scoped key prefix, 15-minute expiry, audit log on every issuance |
| Dashboard API → Scoring Engine (for manual rescore) | In-process typed call, same contract as Extraction → Scoring |
| UI static bundle → Dashboard API | Same-origin deployment in production; CSRF not applicable because JWT is sent in `Authorization` header, not cookies |
| Criteria config JSON → DB | Strict Pydantic validation; unknown fields rejected; operator/field combinations semantically checked |

## 10. Failure Modes

| Failure | Detection | System Behavior | User-Facing Behavior |
|---|---|---|---|
| JWT expired | Auth middleware | `401 UNAUTHORIZED` with `token_expired` hint | UI triggers refresh token flow silently |
| JWT invalid / tampered | Auth middleware | `401 UNAUTHORIZED`, security event emitted | UI forces re-login |
| Cross-tenant deal access | Repository tenant filter | `404 DEAL_NOT_FOUND` (same as "doesn't exist") | "Deal not found." |
| Decision on non-SCORED deal | Handler precondition | `409 DEAL_NOT_READY` | "This deal isn't ready for a decision yet." |
| Duplicate decision submission | Idempotency key or state check | Returns original response, no new row | Transparent |
| Re-score with no active config | Scoring Engine returns `NO_ACTIVE_CONFIG` | `422 CONFIG_INVALID` with details | "No active criteria — please set up criteria first." |
| Invalid criteria config on create | Validating loader | `422 VALIDATION_ERROR` with field path (not value) | Field-specific error in the criteria editor |
| Delete in-use config | Reference check | `409 CONFIG_IN_USE` | "This config has been used to score deals. Deactivate it instead." |
| Storage unavailable for document URL | StorageClient error | `503 INTERNAL_ERROR` with retry hint | "Couldn't generate download link. Try again." |
| DB connection pool exhausted | SQLAlchemy timeout | `503 INTERNAL_ERROR`, on-call alert | Generic "service unavailable" message |
| Malformed request body | Pydantic validation | `422 VALIDATION_ERROR` with field paths | Field-specific errors in the form |
| Rate limit exceeded | Rate Limiter middleware | `429 RATE_LIMITED` with `Retry-After` | UI backs off with exponential delay |

### Error message principle

User-facing messages never include internal identifiers (table names, query fragments, internal service names), stack traces, or other tenants' data. The `request_id` is always present so support can correlate without exposing internals.

## 11. Security Considerations

- **IDOR defense by repository layer.** The strongest defense against cross-tenant access is that the data layer refuses to build a query without a `tenant_id` filter. Handlers that forget to pass it fail at static analysis (a linter rule) or at test time (a repository test fixture asserts this for every repository method). This is defense in depth on top of the per-handler tenant scoping.
- **Existence oracle prevention.** Cross-tenant lookups return `404`, not `403`. A `403` would confirm that a resource exists in another tenant, which is an information leak useful for targeted attacks.
- **XSS in notes and rationales.** Decision notes, criteria names, and extracted sector strings are user- or document-derived and render into the UI. All UI rendering uses a library (React) that escapes by default; there is no `dangerouslySetInnerHTML` path. The rationale string is composed from fixed templates in Scoring Engine, but Dashboard still escapes on render — defense in depth.
- **CSRF.** JWT is sent in `Authorization: Bearer`, not in cookies. CSRF attacks are not applicable because the browser does not auto-attach the token. If the UI ever moves to cookie-based sessions, CSRF tokens must be added at that time.
- **Presigned URL leakage.** Presigned URLs have 15-minute expiry and are never logged in full (the signature portion is redacted). Every issuance is audit-logged with `user_id`, `deal_id`, and `expires_at`, so unusual access patterns are detectable after the fact.
- **Enumeration attacks via deal IDs.** Deal IDs are ULIDs — time-ordered but not sequential, and random enough to resist enumeration. Even so, the tenant scope filter ensures enumerating IDs across tenants returns 404 uniformly.
- **Pagination cursor tampering.** Cursors are opaque base64-encoded payloads signed with an HMAC key from Secrets & Config. A tampered cursor returns `422 VALIDATION_ERROR`, not a different result set.
- **Criteria content in errors.** When a criteria config validation fails, the error returns the field *path* (e.g., `criteria[3].value`) but not the field *value*. This matches Scoring Engine's rule and prevents accidental leakage of the fund's thesis through error logs.
- **Admin actions on decisions.** There is no admin override to modify or delete decisions, by design. The append-only decision log is the audit trail. If a correction is ever needed, it is made by recording a new decision, not by mutating the old one.

## 12. Dependencies

**Internal modules:**
- Auth Service (JWT verification middleware)
- Rate Limiter (middleware)
- Observability (logging, metrics, tracing, audit)
- Input Validation (body schema validation middleware)
- Secrets & Config (DB creds, HMAC cursor key)
- Scoring Engine (in-process for manual re-score)
- Background Jobs (indirectly: decisions are a future feedback signal, but no direct dependency in v1)
- Ingestion Service (linked to from the UI for uploads, but no direct call)

**External services:**
- PostgreSQL (all reads and decision/config writes)
- Object storage (presigned URL generation via `StorageClient`)

**Libraries:**
- `fastapi` for HTTP layer
- `pydantic` for request/response schemas
- `sqlalchemy[asyncio]` for DB access
- A repository abstraction layer (project-internal) that enforces tenant scoping
- React + Tailwind for the minimal UI (served as a static bundle from the same origin)

## 13. Testing Requirements

### Unit tests
- Repository layer refuses to build any query without a `tenant_id` filter (assertion in fixture, run for every repository method)
- Cursor encoding/decoding round-trips cleanly; tampered cursors fail HMAC verification
- Error envelope shape is consistent across all error types
- Pagination limit clamps to `[1, 100]`
- Filter parameters compose correctly (e.g., `state=SCORED&min_score=70&confidence=HIGH`)

### Integration tests
- Happy path: list deals → get deal detail → record PURSUE decision → deal transitions to `DECIDED`, audit log written, next list reflects `has_decision=true`
- Cross-tenant read: user in tenant A tries to GET a deal in tenant B → `404`, not `403`
- Cross-tenant write: user in tenant A tries to POST a decision on a deal in tenant B → `404`
- Decision on non-SCORED deal: POST on `EXTRACTED` deal → `409 DEAL_NOT_READY`
- Duplicate decision via `Idempotency-Key`: second request returns first response, no new row
- Re-score: `POST /deals/{id}/rescore` against `DECIDED` deal creates new score row, existing decision unchanged, both visible in detail view
- Criteria activation: activating a new config atomically deactivates the old one; scoring a deal immediately after uses the new version
- Criteria delete blocked: cannot delete a config that has any rows in `deal_scores` referencing it
- Presigned URL: issuance audit-logged, URL expires at correct time, cross-tenant deal returns 404

### Security tests
- IDOR: every read endpoint is hit with a crafted ID from another tenant; all return 404
- XSS: decision notes containing `<script>`, `"><img onerror=...>` etc. render as escaped text, never execute
- SQL injection: filter parameters containing SQL fragments are parameterized safely
- JWT forgery: request with a JWT signed by a wrong key → 401
- JWT with another tenant's claim: request with a valid JWT for tenant B, accessing tenant A's deals → 404 (can't even see them exist)
- Cursor tampering: modify the cursor payload → 422
- Pagination leak: `total_estimate` is a rough count, not an exact count — does not confirm existence of specific resources

### Contract tests
- Error envelope matches the documented shape for every error code
- OpenAPI schema generated by FastAPI matches the documented endpoints
- UI client (generated or hand-written) compiles against the OpenAPI schema

### Load tests
- 1000 concurrent `GET /deals` requests with realistic filters complete within p95 < 300ms
- Pagination with 10,000 deals per tenant does not degrade linearly with page depth (cursor vs offset test)
- DB connection pool does not exhaust under sustained load

## 14. Open Questions

1. **Real-time updates.** Analysts want to know when new deals finish scoring without refreshing. v1 is request/response only; the Evolution Check in `01_decomposition.md` flags this as Medium risk because adding WebSocket/SSE requires new infrastructure. Proposed stopgap: client-side polling every 30 seconds on the queue view. Revisit if polling becomes a bottleneck.
2. **Bulk decisions.** Analysts reviewing a queue often want to PASS many deals at once. v1 requires one request per decision. A bulk endpoint would be easy to add but needs idempotency semantics worked out (partial failures, etc.). Defer until usage data shows this is a pain point.
3. **Team features.** Evolution Check flags assignments, comments, shared queues as Medium risk — they require RBAC expansion beyond tenant level. Deferring all of these to a future team-tier milestone. v1 assumes all users within a tenant have equal access to all deals.
4. **UI bundle hosting.** For the portfolio phase, the React bundle is served from the same FastAPI origin (same domain, no CORS, no CSRF complexity). For production, a CDN split would be cheaper and faster but introduces origin and cache-invalidation questions. Decision logged in `appendices/B_decision_log.md` when made.
5. **Export endpoints.** CSV export of the deal queue is an obvious analyst ask. It's not in v1 because it needs to stream (large tenants), handle column selection, and decide whether to include PII. Defer.
6. **Criteria versioning UX.** The data model supports many versions per tenant, but the UI in v1 only shows the active one. Should analysts see version history, diff between versions, or rollback? Defer to a criteria-management milestone.
7. **Feedback signal shape for v2 ML.** The `deal_decisions` table captures the minimum needed (which score the decision was against, what the decision was, optional notes). The ML training pipeline will need to join this with `deal_extractions` and `deal_scores` historically. Is that join cheap enough, or do we need a denormalized "training example" table? Revisit when the ML milestone is scoped.