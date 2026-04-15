# 01 — Decomposition: Modules, Communication, Build Order

> Phase 2 of the System Thinking Framework.
> Parent: `00_main_spec.md` · Next: `modules/ingestion_service.md`

---

## Module Overview

The system is decomposed into **10 modules**, split into two tiers:

### Core Business Modules

These implement the primary user-visible functionality.

| Module | Purpose | Spec File |
|---|---|---|
| **Ingestion Service** | Accept deal documents from multiple sources, normalize, store, queue for extraction | `modules/ingestion_service.md` |
| **Extraction Service** | Parse documents via LLM, extract structured fields with confidence metadata | `modules/extraction_service.md` |
| **Scoring Engine** | Evaluate extracted deals against tenant-configured criteria, produce score + rationale | `modules/scoring_engine.md` |
| **Dashboard API** | Expose deal queue, scores, and decision actions to analysts; serve UI | `modules/dashboard_api.md` |

### Cross-Cutting Modules

These provide infrastructure and are consumed by the core modules. They are first-class modules — not "middleware" — because each has its own state, failure modes, and security surface that must be designed explicitly.

| Module | Purpose | Spec File |
|---|---|---|
| **Auth Service** | JWT issuance, refresh token rotation, session lifecycle, revocation | `modules/auth_service.md` |
| **Rate Limiter** | Per-user / per-IP / per-tenant request throttling with token bucket strategy | `modules/rate_limiter.md` |
| **Secrets & Config** | Centralized secret loading, rotation, environment-scoped config | `modules/secrets_config.md` |
| **Observability Stack** | Structured logging, metrics, distributed tracing, audit log storage | `modules/observability.md` |
| **Input Validation** | File sandboxing, schema validation, content-type verification, payload limits | `modules/input_validation.md` |
| **Background Jobs** | Async job execution with retries, idempotency, dead-letter handling | `modules/background_jobs.md` |

---

## Module Communication Patterns

| From | To | Pattern | Coupling | Contract |
|---|---|---|---|---|
| Dashboard API | Ingestion Service | Sync HTTP (internal) | Low | Multipart upload + metadata schema |
| Ingestion Service | Background Jobs | Async enqueue | Low | `ExtractionJob` payload (deal_id, file_ref) |
| Background Jobs | Extraction Service | Sync call (in-process) | Medium | `ExtractionRequest` → `ExtractedDeal` schema |
| Extraction Service | Scoring Engine | Sync call (in-process) | Medium | `ExtractedDeal` → `ScoredDeal` schema |
| Scoring Engine | PostgreSQL | Sync DB write | Low | `deals`, `scores`, `audit_log` tables |
| Dashboard API | PostgreSQL | Sync DB read | Low | Tenant-scoped queries via ORM |
| All modules | Auth Service | Sync middleware | Low | JWT verification on every protected request |
| All modules | Rate Limiter | Sync middleware | Low | Token bucket check per scope |
| All modules | Observability Stack | Sync logging + async metrics | Low | Structured log format + OpenTelemetry traces |
| All modules | Secrets & Config | Sync load at startup + periodic refresh | Low | Typed config objects |

**Coupling principles:**

- **Async between tiers**: Ingestion → Extraction is async via job queue. This allows the upload endpoint to return fast and isolates LLM latency from user requests.
- **Sync within a job**: Once an extraction job is running, Extraction → Scoring is a direct in-process call. Both are stateless services operating on the same `deal_id`. No queue needed here; adding one would just add failure modes without benefit.
- **DB as integration point for reads**: Dashboard API reads from the same DB that background jobs write to. This is a simple, reliable pattern that avoids distributed consistency problems.
- **Middleware for cross-cutting**: Auth, rate limiting, observability, and input validation all apply as FastAPI middleware. They never get bypassed; the framework enforces the chain.

---

## Evolution Check

For each core module: if I need to add a feature in 6 months, does the current architecture support it or fight it?

| Module | Likely Future Change | Supports It? | Risk |
|---|---|---|---|
| Ingestion | Add CRM integration (Salesforce, HubSpot) as a new source | Yes — new source adapter, same output contract | Low |
| Ingestion | Support multi-document deals (CIM + financials + teaser as one deal) | Partially — current 1:1 doc:deal assumption would need to become 1:N via a `deal_group` table | Medium |
| Extraction | Multi-model extraction (fall back to Claude if OpenAI fails) | Yes — LLM client is behind an interface | Low |
| Extraction | Fine-tuned extraction model trained on user corrections | Yes — model selection is a config value | Low |
| Scoring | ML-based scoring trained on pass/pursue feedback | Yes — scoring engine is a pluggable interface; rule-based engine is one implementation | Low |
| Scoring | Per-analyst personalized scoring weights | Partially — criteria config is currently per-tenant; would need per-user override layer | Medium |
| Dashboard | Team features (shared queue, assignments, comments) | Partially — needs RBAC expansion beyond tenant-level | Medium |
| Dashboard | Real-time updates (WebSocket) when new deals score | No — would require new delivery mechanism; current design is request/response | Medium |

**Evolution principle:** the cross-cutting modules (auth, rate limiter, observability, etc.) are designed to be stable. The core business modules are designed with pluggable interfaces so implementations can swap without breaking contracts.

---

## Build Order

Dependencies drive the order. A module cannot be built until all its dependencies are in place.

| Order | Module | Depends On | Estimated Sessions |
|---|---|---|---|
| 1 | Secrets & Config | — | 1 |
| 2 | Observability Stack | Secrets & Config | 1–2 |
| 3 | Database Models + Migrations (schema only) | — | 1 |
| 4 | Auth Service | Secrets, Observability, DB Models | 2 |
| 5 | Rate Limiter | Observability, DB (or Redis) | 1 |
| 6 | Input Validation | Observability | 1 |
| 7 | Background Jobs | DB Models, Observability | 1–2 |
| 8 | Ingestion Service | Auth, Input Validation, Background Jobs, S3 | 1–2 |
| 9 | Extraction Service | Background Jobs, Secrets (for LLM key), Observability | 2 |
| 10 | Scoring Engine | DB Models, Observability | 1–2 |
| 11 | Dashboard API | All above | 2 |
| 12 | Minimal UI | Dashboard API | 1–2 |
| 13 | Deployment pipeline (Docker, CI/CD, Fly.io) | All above | 2 |
| 14 | End-to-end tests + runbook validation | All above | 1–2 |

**Total estimate:** 18–24 focused sessions. This is a portfolio-grade build, not an MVP — the extra sessions vs. the original 7–10 estimate reflect the inclusion of full cross-cutting modules, production deployment, and observability.

**Critical path:** Secrets → DB Models → Auth → Background Jobs → Ingestion → Extraction → Scoring → Dashboard. Observability, rate limiting, and input validation can be built in parallel once their dependencies are met.

**DADP notes:**

- Each module in this list corresponds to one `.spec/modules/*.md` file. The Builder agent consumes one module spec at a time.
- The Validator agent generates tests from the same spec and reviews Builder output before the module is considered complete.
- The Conductor (human) reviews the Validator's report and makes the call to merge or request revisions.
- Integration tests that span multiple modules are built after both modules pass individual validation.

---

## Per-Module Security Checklist (Summary)

Full security analysis lives in each module spec. This is the summary view:

| Module | Sensitive Data | Auth | Attack Surface | Mitigation Location |
|---|---|---|---|---|
| Ingestion | Deal documents | JWT required | Malicious PDF, oversized upload, path traversal | `modules/ingestion_service.md` + `modules/input_validation.md` |
| Extraction | Deal text sent to LLM | Internal only | Prompt injection via doc content, LLM key leakage | `modules/extraction_service.md` + `modules/secrets_config.md` |
| Scoring | Criteria config (trade secret) | Internal + tenant-scoped | Malformed criteria config, criteria exfiltration | `modules/scoring_engine.md` |
| Dashboard | All deal data | JWT + tenant scope | XSS, IDOR, auth bypass, CSRF | `modules/dashboard_api.md` + `modules/auth_service.md` |
| Auth Service | Credentials, tokens, signing keys | N/A (it IS the auth) | Token theft, replay, refresh reuse, key exposure | `modules/auth_service.md` |
| Rate Limiter | Request metadata | N/A | DoS via limiter bypass, state poisoning | `modules/rate_limiter.md` |
| Secrets & Config | All secrets | N/A (loads before app starts) | Secret exposure in logs/images/env leaks | `modules/secrets_config.md` |
| Observability | Log data (may contain PII) | N/A | PII leakage via logs, log injection | `modules/observability.md` |
| Input Validation | Uploaded files | N/A | Zip bombs, malformed PDFs, MIME confusion | `modules/input_validation.md` |
| Background Jobs | Job payloads | N/A | Job replay, poisoned queue, idempotency violations | `modules/background_jobs.md` |

---

## What Comes Next

Batch 2 will deliver the four core business modules in full: `ingestion_service.md`, `extraction_service.md`, `scoring_engine.md`, `dashboard_api.md`. Each will follow the 14-section template defined in `README.md`.