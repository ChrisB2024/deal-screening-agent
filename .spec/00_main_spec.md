# 00 — Main Spec: Problem Framing & System Model

> Phases 0 and 1 of the System Thinking Framework.
> Parent: `README.md` · Next: `01_decomposition.md`

---

## Phase 0 — Problem Thinking

### Who Hurts and Why

**The Person.** A deal sourcing analyst or solo GP at a small/mid-size investment firm (PE, VC, search fund, or M&A advisory) who reviews 50–200+ inbound deals per week from brokers, CIMs, and deal platforms.

**The Pain.** Roughly 80% of deals are obvious passes within 2 minutes of reading — wrong sector, wrong size, wrong geography, bad margins. But the analyst still has to open every CIM/teaser, mentally run it against the fund's criteria, and log the decision. The volume is numbing and the real cost is attention: by the time they've waded through garbage, they're too fatigued to think sharply about the 3 deals that actually matter.

**The Ugly Workaround.** Manually skimming PDFs/emails, copy-pasting deal data into a spreadsheet, and applying gut-feel filters. Some use Airtable or Notion with manual tagging. The system breaks the moment deal flow spikes. Nothing learns from past pass/pursue decisions.

**The Sentence Test.**
> *A deal sourcing analyst currently manually screens 100+ deals/week against fund criteria using spreadsheets and gut feel because there's no automated filter that understands their thesis, and this product eliminates this by ingesting deal flow, scoring against configurable criteria, and surfacing only the deals worth reading — ranked by fit.*

---

### Product Invariants

1. The user must never miss a real opportunity because the system silently filtered it out. Every pass decision must be auditable with a reason.
2. Core screening must always return a score + rationale, never a bare pass/fail. The user decides; the agent informs.
3. The system must never hallucinate deal data. If a field can't be extracted with confidence, it must be flagged as `MISSING`, not guessed.
4. When data is missing, the system must still attempt a partial score and clearly indicate which criteria couldn't be evaluated.
5. The system must be honest about confidence: every score includes a confidence indicator (`HIGH` / `MEDIUM` / `LOW`) based on extraction completeness.
6. Decisions are immutable. Re-screening creates new records; history is append-only.

### Security Invariants

1. User data (deal documents, criteria, decisions) must never be shared across tenants or used for model training.
2. No API endpoint accepts unauthenticated requests except the auth endpoints themselves and `/health`.
3. Secrets (API keys, DB credentials, JWT signing keys) must never appear in logs, responses, container images, or source control.
4. If the LLM provider is compromised, blast radius is limited to extraction quality — no PII is sent without scrubbing.
5. User data on account deletion will be hard-deleted within 30 days, including raw documents, extracted fields, scores, and decision history.
6. All data in transit uses TLS 1.3. All data at rest (DB, object storage) is encrypted with provider-managed or customer-managed keys.
7. Authentication uses short-lived access tokens (15 min) with refresh token rotation. Refresh token reuse triggers full session revocation.

### Technical Invariants

1. Database writes are atomic — a deal is either fully ingested (document + extracted fields + initial score + audit log) or not at all.
2. API responses always include a `request_id` header and body field for traceability.
3. No state transition (e.g., `PENDING → SCORED`) occurs without a corresponding audit log entry written in the same transaction.
4. All external service calls (LLM, storage, email) have explicit timeouts and retry policies. No unbounded waits.
5. Every background job is idempotent — re-running it produces the same result without side-effect duplication.
6. Database migrations are reversible. Every `up` migration has a corresponding `down` migration tested in CI.

---

### Product Causality Chains

| Change | Technical Impact | UX Impact | Business Impact | Security Impact |
|---|---|---|---|---|
| Add LLM-based extraction (v1) | OpenAI dependency + latency + cost/deal | Auto-scored on upload, saves 10+ hrs/wk | Core value prop — enables paid conversion | Deal text sent to external API — requires PII scrub |
| Add feedback loop (v2) | Decision log + retrain pipeline | Scores improve over time, increases trust | Retention driver — system gets smarter = stickier | Decision history is sensitive — RBAC required |
| Multi-tenant support | Row-level security, tenant-scoped queries | Enables team features (shared queue) | Unlocks team/enterprise tiers | Cross-tenant leak = critical severity |
| Self-serve signup | Public registration endpoint + email verification | Reduces sales friction | Increases top-of-funnel | New attack surface — abuse, fake accounts |

### Threat Model

| Data Touched | Who Should Access | Blast Radius if Compromised | Regulatory |
|---|---|---|---|
| Deal documents (CIMs, teasers) | Uploading user + team | Confidential deal terms leaked to competitors | NDA-bound (contractual) |
| Fund screening criteria | Fund admins only | Reveals investment thesis to market | Trade secret |
| Screening decisions + notes | Analyst + assigned GP | Reveals pass/pursue pipeline (competitive intel) | Internal audit trail |
| Auth tokens / refresh tokens | Auth service only | Account takeover → all deal data | SOC 2 (enterprise tier) |
| JWT signing key | Auth service + secret store | Full account impersonation across tenants | Critical — rotate on exposure |
| OpenAI API key | Extraction service + secret store | Unbounded spend + PII exposure if misused | Critical — rate-limited + monitored |

---

## Phase 1 — System Model

### Core Entity: Deal

A `Deal` is the primary entity. It moves through a well-defined lifecycle from ingestion to final decision. Every transition is logged.

### Deal State Machine

```
        ┌─────────────┐
        │  UPLOADED   │
        └──────┬──────┘
               │ extraction job runs
      ┌────────┴────────┐
      ▼                 ▼
┌──────────┐      ┌──────────┐
│ EXTRACTED│      │  FAILED  │
└─────┬────┘      └─────┬────┘
      │ scoring runs    │ retry or manual
      ▼                 └──────→ UPLOADED
┌──────────┐
│  SCORED  │
└─────┬────┘
      │ user decision
      ▼
┌──────────┐       ┌──────────┐
│ DECIDED  │──────▶│ ARCHIVED │
└──────────┘       └──────────┘
```

**States:**

- `UPLOADED` — raw document received, queued for extraction. Entry state.
- `EXTRACTED` — LLM extraction complete. At least 3 of 6 core fields recovered with confidence metadata.
- `FAILED` — extraction could not produce minimum viable fields (< 3 core fields) or document was unreadable. Requires manual intervention or retry.
- `SCORED` — scoring engine has produced a fit score (0–100), per-criterion results, rationale, and confidence level.
- `DECIDED` — analyst has marked `PASS` or `PURSUE` with optional notes. Feeds the future feedback loop.
- `ARCHIVED` — deal is no longer in the active queue. Retained for audit per retention policy.

**Transitions:**

| From | To | Trigger | Precondition |
|---|---|---|---|
| `UPLOADED` | `EXTRACTED` | Extraction job completes | ≥ 3 of 6 core fields extracted with FOUND or INFERRED status |
| `UPLOADED` | `FAILED` | Extraction job completes | < 3 core fields extracted OR document unreadable OR LLM unrecoverable error after retries |
| `FAILED` | `UPLOADED` | Manual retry or re-upload | Admin action or new file hash |
| `EXTRACTED` | `SCORED` | Scoring engine runs | Criteria config exists for tenant |
| `SCORED` | `DECIDED` | User clicks PASS or PURSUE | Score visible in UI |
| `DECIDED` | `ARCHIVED` | Time-based or manual | Retention period expired OR manual archive action |

**Invalid Transitions (must never occur):**

- `SCORED → UPLOADED`: once scored, re-extraction requires explicit re-upload as a new deal record.
- `DECIDED → SCORED`: decisions are immutable. Changing criteria does not re-score decided deals; re-screening creates a new deal record linked to the original.
- `ARCHIVED → *`: archived deals are read-only.
- Any transition that skips states (e.g., `UPLOADED → SCORED`): every state must be reached through its defined predecessor.

Enforcement: the `deal_state_transitions` table defines allowed transitions, and all writes go through a single `transition_deal_state()` function that checks the transition table and writes the audit log atomically.

### Trust Boundaries

| Boundary | From | To | Validation Required |
|---|---|---|---|
| Browser / Client → API | Untrusted | Trusted | TLS, JWT auth, schema validation, rate limiting, CORS allowlist |
| API → Database | Trusted | Trusted | Parameterized queries (ORM only), tenant-scoped row access, connection pool limits |
| API → OpenAI | Trusted | Semi-trusted | PII scrub, response schema validation, 30s timeout, circuit breaker |
| API → S3 / Object Store | Trusted | Trusted | IAM role (no keys in code), presigned URLs for client access, tenant-scoped key prefixes |
| Email / Webhook → Ingestion | Untrusted | Trusted | HMAC signature verification, sender allowlist, file type + size checks, rate limiting |
| Background worker → Database | Trusted | Trusted | Same as API → DB, plus job-level idempotency keys |
| Background worker → OpenAI | Trusted | Semi-trusted | Same as API → OpenAI |

### Data Flow

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ Deal Source  │───▶│  Ingestion   │───▶│  Extraction  │───▶│   Scoring    │
│ Upload/Email │    │   Service    │    │   Service    │    │    Engine    │
└──────────────┘    └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
                           │                   │                   │
                           ▼                   ▼                   ▼
                    ┌───────────────────────────────────────────────┐
                    │         PostgreSQL (deals, audit, auth)       │
                    │         + S3 (raw documents, encrypted)       │
                    └───────────────────────────────────────────────┘
                           ▲                   ▲
                           │                   │
                    ┌──────┴───────┐    ┌──────┴───────┐
                    │  Dashboard   │◀───│   Auth       │
                    │  API + UI    │    │   Service    │
                    └──────────────┘    └──────────────┘
```

Every arrow in this diagram crosses a trust boundary or a module boundary. Each boundary has a defined contract, validation rules, and failure handling (specified in the relevant module files).