# System Thinking Spec — Deal Screening Agent

> **Version:** 1.0  
> **Date:** 2026-03-23  
> **Author:** Chris  
> **Status:** Draft

---

## Phase 0 — Problem Thinking

### Who Hurts and Why

**The Person:**  
A deal sourcing analyst or solo GP at a small/mid-size investment firm (PE, VC, search fund, or M&A advisory) who reviews 50–200+ inbound deals per week from brokers, CIMs, and deal platforms.

**The Pain:**  
80% of deals are obvious passes within 2 minutes of reading — wrong sector, wrong size, wrong geography, bad margins. But the analyst still has to open every CIM/teaser, mentally run it against the fund's criteria, and log the decision. The volume is numbing and the real cost is attention: by the time they've waded through garbage, they're too fatigued to think sharply about the 3 deals that actually matter.

**The Ugly Workaround:**  
Manually skimming PDFs/emails, copy-pasting deal data into a spreadsheet, and applying gut-feel filters. Some use Airtable or Notion with manual tagging. The system breaks the moment deal flow spikes. Nothing learns from past pass/pursue decisions.

**The Sentence Test:**  
> "A deal sourcing analyst currently manually screens 100+ deals/week against fund criteria using spreadsheets and gut feel because there's no automated filter that understands their thesis, and this product eliminates this by ingesting deal flow, scoring against configurable criteria, and surfacing only the deals worth reading — ranked by fit."

---

### Product Invariants

*What must ALWAYS be true from the user's perspective:*

1. ☐ The user must never miss a real opportunity because the system silently filtered it out. Every pass decision must be auditable with a reason.
2. ☐ Core screening must always return a score + rationale, never a bare pass/fail. The user decides, the agent informs.
3. ☐ The system must never hallucinate deal data. If a field can't be extracted with confidence, it must be flagged as `MISSING`, not guessed.
4. ☐ When data is missing, the system must still attempt a partial score and clearly indicate which criteria couldn't be evaluated.
5. ☐ The system must be honest about confidence: every score includes a confidence indicator (`HIGH` / `MEDIUM` / `LOW`) based on extraction completeness.

### Security Invariants

1. ☐ User data (deal documents, criteria, decisions) must never be shared across tenants or used for model training.
2. ☐ No endpoint should accept unauthenticated requests. All API routes require valid JWT.
3. ☐ Secrets (API keys, DB credentials) must never appear in logs, responses, or client-side code.
4. ☐ If the LLM provider is compromised, blast radius is limited to extraction quality — no deal data is sent without PII scrubbing.
5. ☐ User data on account deletion will be hard-deleted within 30 days, including all stored documents and screening history.

### Technical Invariants

1. ☐ Database transactions must be atomic — a deal is either fully ingested (document + extracted fields + initial score) or not at all.
2. ☐ API responses must always include a `request_id` for traceability and return structured error objects on failure.
3. ☐ No state transition (e.g., `PENDING → SCORED`) should occur without a corresponding audit log entry.

---

### Product Causality Chains

*If you change X, trace the chain: X → user behavior → retention → revenue → survival*

| Change | Technical Impact | UX Impact | Business Impact | Security Impact |
|--------|-----------------|-----------|-----------------|-----------------|
| Add LLM-based extraction (v1) | Adds OpenAI dependency + latency | Deals auto-scored on upload — analyst saves 10+ hrs/wk | Core value prop delivered — enables paid conversion | Deal text sent to external API — requires PII scrub |
| Add feedback loop (v2) | Needs decision log + scoring model retrain pipeline | Scores improve over time — increases trust | Retention driver — system gets smarter = stickier | Decision history is sensitive — access control required |
| Multi-tenant support | Row-level security, tenant-scoped queries | Enables team features (shared deal queue) | Unlocks team/enterprise pricing | Cross-tenant data leak = critical vulnerability |

### Threat Model

| Data Touched | Who Should Access It | Blast Radius if Compromised | Regulatory Req. |
|-------------|---------------------|----------------------------|-----------------|
| Deal documents (CIMs, teasers) | Uploading user + team members | Confidential deal terms leaked to competitors | NDA-bound — contractual, not regulatory |
| Fund screening criteria | Fund admins only | Reveals investment thesis to market | Proprietary — trade secret level |
| Screening decisions + notes | Analyst + GP who assigned | Reveals pass/pursue pipeline — competitive intel | Internal audit trail |
| User auth tokens / credentials | Auth service only | Account takeover → access to all deal data | SOC 2 if enterprise tier |

---

## Phase 1 — System Model

### State Machine — Deal Lifecycle

```
[UPLOADED] --extraction_complete--> [EXTRACTED]
    |                                    |
    |--extraction_failed--> [FAILED]     |--scoring_complete--> [SCORED]
                               |                                   |
                               |--retry--> [UPLOADED]              |--user_decision--> [DECIDED]
                                                                        |
                                                           [PASSED]  or  [PURSUING]
```

**States:**
- `UPLOADED`: Raw document received, queued for extraction.
- `EXTRACTED`: Key fields pulled from document (sector, revenue, EBITDA, geography, ask price). May be partial.
- `FAILED`: Extraction could not produce minimum viable fields. Queued for manual review or retry.
- `SCORED`: Criteria engine has produced a fit score + rationale. Awaiting analyst review.
- `DECIDED`: Analyst has marked PASS or PURSUE with optional notes. Feeds the feedback loop.

**Transitions:**
- `UPLOADED → EXTRACTED`: Triggered by extraction job completion. Requires ≥ 3 of 6 core fields extracted.
- `UPLOADED → FAILED`: Extraction returned < 3 core fields or document unreadable.
- `EXTRACTED → SCORED`: Scoring engine evaluates extracted fields against user's criteria config.
- `SCORED → DECIDED`: User action (pass/pursue button click). Requires score to be visible.

**Invalid Transitions:**
- `SCORED → UPLOADED` must never happen — once scored, re-extraction requires explicit re-upload.
- `DECIDED → SCORED` must never happen — decisions are immutable audit records. User can re-screen as a new entry.

---

### Trust Boundaries

| Boundary | From (Trust Level) | To (Trust Level) | Validation Required |
|----------|-------------------|-------------------|---------------------|
| Client → API | Untrusted | Trusted | JWT auth, input validation, rate limiting (100 req/min) |
| API → Database | Trusted | Trusted | Parameterized queries, tenant-scoped access, ORM-only |
| API → OpenAI | Trusted | Semi-trusted | PII scrub before send, response validation, 30s timeout, fallback to manual |
| Email/Webhook → API | Untrusted | Trusted | HMAC signature verification, file type whitelist (PDF only for v1) |

### Data Flow

```
[Deal Source]                                                      [Dashboard]
  Email / Upload / Webhook                                        Ranked deal queue
        |                                                               ^
        v                                                               |
[Ingestion Service]  -->  [Extraction Service]  -->  [Scoring Engine]  -->  [Deal Store]
   File validation         LLM-powered parsing       Criteria matching      PostgreSQL
   Dedup check             Field extraction           Score + rationale      + audit log
   Store raw doc           Confidence tagging         Confidence level
```

---

## Phase 2 — Decomposition

### Module Breakdown

#### Module: Ingestion Service
- **Purpose:** Accept deal documents from multiple sources (upload, email forward, webhook) and normalize for processing.
- **Inputs:** PDF/document file + source metadata (sender, timestamp, source channel).
- **Outputs:** Stored document record with status `UPLOADED`, file stored in S3/local, job queued for extraction.
- **Invariants:** Every ingested document gets a unique `deal_id`. Duplicate detection by content hash. Only PDF accepted in v1.
- **Security surface:** File upload endpoint — malicious files, oversized uploads, path traversal.
- **Failure modes:**
  - Invalid file type → Reject with clear error, no state change.
  - Storage unavailable → Queue for retry, return 503 to client.
  - Duplicate detected → Return existing `deal_id`, skip re-processing.
- **What the user sees on failure:** "We couldn't process this file. Please upload a PDF under 50MB."

#### Module: Extraction Service
- **Purpose:** Parse deal documents using LLM to extract structured fields (sector, revenue, EBITDA, geography, ask price, deal type).
- **Inputs:** Raw document text (post-PDF parsing), extraction prompt template.
- **Outputs:** Structured JSON with extracted fields, confidence per field, overall extraction confidence.
- **Invariants:** Never fabricate data. Every field has an explicit `FOUND` / `INFERRED` / `MISSING` status. LLM temperature = 0.
- **Security surface:** Prompt injection via malicious document content. PII in deal docs sent to external LLM.
- **Failure modes:**
  - LLM timeout/error → Retry 2x with exponential backoff, then mark `FAILED`.
  - Unparseable document → Mark `FAILED`, surface for manual entry.
  - Partial extraction → Proceed if ≥ 3/6 fields, flag as `LOW` confidence.
- **What the user sees on failure:** "We extracted partial data from this deal. Please review and fill in missing fields."

#### Module: Scoring Engine
- **Purpose:** Evaluate extracted deal data against user-configured fund criteria and produce a fit score (0–100) with rationale.
- **Inputs:** Extracted deal fields + user's criteria config (must-haves, nice-to-haves, dealbreakers, weights).
- **Outputs:** Numeric score (0–100), pass/fail per criterion, natural language rationale, overall confidence.
- **Invariants:** Score is deterministic for same inputs. Rationale must cite specific criteria matches/misses. Missing fields reduce confidence, never silently skipped.
- **Security surface:** Minimal — internal service, no external calls. Criteria config is user-controlled input (validate structure).
- **Failure modes:**
  - Invalid criteria config → Reject with schema validation error.
  - All fields `MISSING` → Return score=0, confidence=`NONE`, rationale="Insufficient data to evaluate."
- **What the user sees on failure:** Score card with clear indicators of what couldn't be evaluated and why.

#### Module: Dashboard API + UI
- **Purpose:** Surface the ranked deal queue, allow analyst to review scores, make pass/pursue decisions, and configure criteria.
- **Inputs:** User interactions (filter, sort, decide, configure criteria).
- **Outputs:** Rendered deal cards with score, rationale, confidence. Decision audit log entries.
- **Invariants:** Decisions are append-only (never edited, only superseded). Criteria changes trigger re-score of `EXTRACTED` deals only.
- **Security surface:** Auth bypass, XSS via deal content rendered in UI, IDOR on deal records.
- **Failure modes:**
  - API unavailable → UI shows cached last-known state with stale indicator.
  - Slow query → Paginate, lazy-load deal details.
- **What the user sees on failure:** "Deal data may be out of date. Refreshing..."

---

### Module Communication

| Module A | Module B | Pattern | Coupling | Contract |
|----------|----------|---------|----------|----------|
| Ingestion | Extraction | Async (job queue) | Low | `deal_id` + `file_path` on queue |
| Extraction | Scoring | Sync (internal call) | Medium | `ExtractedDeal` JSON schema |
| Scoring | Dashboard API | Sync (DB read) | Low | `ScoredDeal` DB record |
| Dashboard API | Ingestion | Sync (HTTP POST) | Low | Multipart file upload |

### Evolution Check

*For each module: if I need to add a feature in 6 months, does this support it or fight it?*

| Module | Likely Future Change | Supports It? | Risk |
|--------|---------------------|--------------|------|
| Ingestion | Add CRM integration (Salesforce, HubSpot) | Yes — new source adapter, same output contract | Low |
| Extraction | Multi-document deals (CIM + financials + teaser) | Partially — current 1:1 doc:deal assumption | Medium — refactor to `deal_group` model |
| Scoring | ML-based scoring from feedback loop | Yes — scoring engine is a pluggable interface | Low — swap rule engine for model |
| Dashboard | Team features (shared queue, assignments) | Partially — needs RBAC layer | Medium — auth model expansion |

---

## Phase 3 — Implementation Plan

### Build Order (MVP)

| Order | Module | Depends On | Est. Sessions |
|-------|--------|------------|---------------|
| 1 | Criteria Config Schema + DB Models | — | 1 |
| 2 | Extraction Service (LLM + PDF parsing) | DB Models | 2 |
| 3 | Scoring Engine (rule-based v1) | Extraction output schema | 1 |
| 4 | Ingestion Service (upload endpoint) | Extraction + Scoring | 1 |
| 5 | Dashboard API (FastAPI routes) | All above | 1–2 |
| 6 | Minimal UI (deal queue + score cards) | Dashboard API | 1–2 |

**Total MVP estimate:** 7–10 focused sessions. Enough to demonstrate working end-to-end flow for the application.

### Per-Module Security Checklist

| Module | Sensitive Data? | Auth? | Input Validation | Attacker Controls Input? | Storage Breach? |
|--------|----------------|-------|------------------|--------------------------|-----------------|
| Ingestion | Yes — deal docs | JWT required | File type + size + content-type check | Malicious PDF → sandbox extraction | Docs encrypted at rest |
| Extraction | Yes — deal text to LLM | Internal only | PII scrub before LLM call | Prompt injection via doc → structured output only | N/A — stateless |
| Scoring | Yes — criteria = trade secret | Internal + tenant-scoped | Schema validation on criteria config | Malformed criteria → schema rejection | Criteria encrypted at rest |
| Dashboard | Yes — all deal data | JWT + tenant scope | Query param sanitization, pagination limits | XSS via deal content → sanitize on render | Full DB breach → all tenant data |

---

## Phase 4 — Validation Plan

### Technical Validation
- ☐ All extraction tests pass against 10+ sample CIMs/teasers with known fields.
- ☐ Edge cases: empty PDF, image-only PDF, 100+ page CIM, non-English document, corrupted file.
- ☐ Malicious input: prompt injection in PDF text, oversized upload, duplicate rapid submissions.
- ☐ Scoring determinism: same inputs → same score across 100 runs.

### Security Validation
- ☐ Inputs sanitized at every trust boundary (upload, criteria config, API params).
- ☐ Auth enforced at API level — no route accessible without valid JWT.
- ☐ Secrets externalized via environment variables, never in code or logs.
- ☐ Dependencies audited and pinned (`pip freeze`, `npm shrinkwrap`).
- ☐ HTTPS enforced on all network boundaries.

### Product Validation
- ☐ Upload 20 real deal teasers and verify scoring matches manual analyst judgment on ≥ 80%.
- ☐ Time-to-screen comparison: manual (avg ~3 min/deal) vs agent (target < 15 sec/deal).
- ☐ Does it solve the pain from Phase 0? Analyst should feel confident skipping PASS-scored deals.
- ☐ What surprised you? *[capture after testing]*

---

## Appendix A: Lifecycle Maps

### Entity: Deal Record
```
Uploaded → Extracted → Scored → Decided → Archived
                                    |
                           What happens to the raw document?
                           What happens to the score history?
                           What if criteria change after decision?

Answers:
  - Raw document retained for audit (encrypted at rest, TTL = account retention policy)
  - Score history is append-only — re-scores create new records, old scores preserved
  - Criteria change → EXTRACTED deals re-scored automatically, DECIDED deals untouched
```

### Entity: User Session
```
Login → Active → Idle (15min) → Expired → Cleaned Up
           |
   What if token is stolen? → Short-lived JWT (15min) + refresh token rotation
   What if session hijacked? → Bind session to IP + user-agent fingerprint
```

---

## Appendix B: Decision Log Seed

| Decision | Options Considered | Chosen | Tradeoff | Reversible? |
|----------|--------------------|--------|----------|-------------|
| LLM provider for extraction | OpenAI GPT-4o, Claude, local model | OpenAI GPT-4o (per job post) | Vendor lock-in + cost vs accuracy | Yes — extraction is behind interface |
| Scoring engine v1 approach | Rule-based weighted scoring vs LLM-based scoring | Rule-based (deterministic) | Less flexible but auditable + no LLM cost on scoring | Yes — pluggable scoring interface |
| Document storage | S3, local filesystem, Supabase storage | Supabase Storage (matches existing stack) | Platform dependency vs simplicity | Yes — abstracted behind storage interface |
| Queue for async extraction | Redis/Celery, PostgreSQL-backed queue, in-process | PostgreSQL advisory locks (MVP simplicity) | Less throughput but zero new infra | Yes — swap to Celery when needed |
| MVP frontend | React SPA, Next.js, server-rendered HTML | Minimal React SPA (or just API + Postman for demo) | Speed to demo vs polish | Yes — frontend is decoupled from API |

---

## Internal Note: Hiclone Architecture Alignment

*This deal screening agent maps directly onto Hiclone's operational twin architecture:*

- **Ingestion** → Hiclone's data connector layer (operational data intake)
- **Extraction** → Hiclone's perception module (structured understanding of unstructured business data)
- **Scoring Engine** → Hiclone's metric-tracking core (scalar evaluation against configurable targets)
- **Feedback Loop (v2)** → Hiclone's commit/revert architecture (learn from outcomes, improve over time)

*This client engagement validates the core Hiclone loop: ingest → evaluate → recommend → learn. Build reusable interfaces at each boundary.*