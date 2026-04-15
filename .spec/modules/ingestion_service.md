# Module: Ingestion Service

> Core business module. Parent: `../01_decomposition.md`
> Build order: step 8 (depends on Auth, Input Validation, Background Jobs, S3)

---

## 1. Purpose

Accept deal documents from multiple sources (direct upload, email forward, webhook), validate and normalize them, store the raw file, and enqueue an extraction job — returning a `deal_id` the client can track.

## 2. Responsibilities

- Accept `multipart/form-data` uploads via authenticated HTTP endpoint
- Accept email-forwarded deals via webhook from a mail-parsing provider (e.g., SendGrid Inbound Parse, Postmark)
- Compute a content hash (SHA-256) for deduplication
- Reject duplicate uploads within the same tenant (return existing `deal_id`)
- Store raw documents in object storage (S3 in production, Fly.io volumes in portfolio phase — abstracted behind a `StorageClient` interface)
- Write a new `Deal` record in state `UPLOADED` with source metadata
- Enqueue an `ExtractionJob` via the Background Jobs module
- Return `deal_id`, `status`, and a tracking URL to the client
- Emit structured logs and metrics for every ingestion attempt

## 3. Non-Responsibilities

- **Does not parse document content.** That's Extraction Service.
- **Does not score deals.** That's Scoring Engine.
- **Does not validate file contents beyond type/size/magic-byte checks.** Deep validation (malicious PDF scanning, zip bomb detection) is delegated to Input Validation module.
- **Does not authenticate requests.** Auth Service middleware handles that before Ingestion sees the request.
- **Does not rate-limit requests.** Rate Limiter middleware handles that.
- **Does not manage criteria configs.** Dashboard API handles that.
- **Does not transform or preprocess document text.** Raw bytes are stored as-received.

## 4. Inputs / Outputs

### Inputs

**Upload endpoint input:**
- `file`: PDF binary (enforced: MIME `application/pdf`, magic bytes `%PDF-`, max 50 MB)
- `source`: one of `upload`, `email_forward`, `webhook`
- `source_metadata`: optional JSON (sender email, subject line, webhook provider, etc.)
- `tenant_id`: derived from JWT claims (never from client input)
- `user_id`: derived from JWT claims

**Webhook endpoint input (email parse):**
- HMAC-signed payload from mail provider
- Extracted attachment(s) — first PDF attachment becomes the deal; additional attachments logged but not processed in v1
- Sender, subject, received-at timestamp → becomes `source_metadata`

### Outputs

**Success response (HTTP 201):**
```json
{
  "deal_id": "deal_01HW8K...",
  "status": "UPLOADED",
  "content_hash": "sha256:abc123...",
  "tracking_url": "/api/v1/deals/deal_01HW8K...",
  "request_id": "req_01HW8K..."
}
```

**Duplicate response (HTTP 200, idempotent):**
```json
{
  "deal_id": "deal_01HW7J...",
  "status": "EXTRACTED",
  "content_hash": "sha256:abc123...",
  "duplicate_of": "deal_01HW7J...",
  "message": "This document was already ingested 2 hours ago.",
  "request_id": "req_01HW8K..."
}
```

**Error response (HTTP 4xx/5xx):** standard error envelope defined in Dashboard API spec.

### Side Effects

- New row in `deals` table (state `UPLOADED`)
- New row in `deal_audit_log` (action `DEAL_INGESTED`)
- New file in object storage at key `tenants/{tenant_id}/deals/{deal_id}/source.pdf`
- New job in `background_jobs` table of type `extraction`
- Metrics emitted: `ingestion.attempts`, `ingestion.success`, `ingestion.duplicates`, `ingestion.rejected`, `ingestion.duration_ms`

## 5. State Machine

Ingestion writes `Deal` in state `UPLOADED` — it does not manage state transitions beyond the initial creation. The full deal state machine lives in `00_main_spec.md`.

Internally, each ingestion *request* follows a simple flow:

```
RECEIVED → VALIDATED → HASHED → DEDUPED → STORED → QUEUED → RESPONDED
                │          │        │         │
                ▼          ▼        ▼         ▼
             REJECTED  REJECTED  DUPLICATE  FAILED
```

Request-level states are ephemeral (not persisted) and exist only for logging/tracing. Each transition emits a span in the distributed trace.

## 6. Data Model

### `deals` table (relevant columns for this module)

```sql
CREATE TABLE deals (
    deal_id           TEXT PRIMARY KEY,            -- ULID format
    tenant_id         TEXT NOT NULL,
    uploaded_by       TEXT NOT NULL,               -- user_id
    state             TEXT NOT NULL,               -- enum enforced by CHECK
    content_hash      TEXT NOT NULL,               -- sha256 hex
    source            TEXT NOT NULL,               -- upload | email_forward | webhook
    source_metadata   JSONB,
    storage_key       TEXT NOT NULL,               -- S3/volume key
    file_size_bytes   BIGINT NOT NULL,
    mime_type         TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_tenant_content_hash UNIQUE (tenant_id, content_hash),
    CONSTRAINT valid_state CHECK (state IN ('UPLOADED','EXTRACTED','FAILED','SCORED','DECIDED','ARCHIVED'))
);

CREATE INDEX idx_deals_tenant_state ON deals(tenant_id, state);
CREATE INDEX idx_deals_tenant_created ON deals(tenant_id, created_at DESC);
```

The `UNIQUE (tenant_id, content_hash)` constraint is how deduplication is enforced atomically at the database level, not in application code. An `INSERT` attempt on a duplicate fails cleanly and the service catches the unique violation to return the existing deal.

### `deal_audit_log` table (relevant columns)

```sql
CREATE TABLE deal_audit_log (
    audit_id       TEXT PRIMARY KEY,
    deal_id        TEXT NOT NULL REFERENCES deals(deal_id),
    tenant_id      TEXT NOT NULL,
    actor_type     TEXT NOT NULL,    -- user | system | worker
    actor_id       TEXT,             -- user_id or worker_id
    action         TEXT NOT NULL,    -- DEAL_INGESTED, STATE_TRANSITION, etc.
    from_state     TEXT,
    to_state       TEXT,
    metadata       JSONB,
    request_id     TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_deal ON deal_audit_log(deal_id, created_at);
```

## 7. API Contract

### `POST /api/v1/deals`

Authenticated deal upload.

**Headers:**
- `Authorization: Bearer <jwt>` (required)
- `Content-Type: multipart/form-data` (required)
- `Idempotency-Key: <uuid>` (optional — if provided, repeated requests with the same key return the same response)

**Body:** multipart form with `file` field + optional `source_metadata` JSON field

**Responses:**
- `201 Created` — new deal ingested
- `200 OK` — duplicate detected, returning existing deal
- `400 Bad Request` — invalid file type, missing required fields
- `401 Unauthorized` — missing or invalid JWT
- `413 Payload Too Large` — file exceeds 50 MB
- `415 Unsupported Media Type` — not a PDF
- `422 Unprocessable Entity` — file failed deep validation (passed to Input Validation module)
- `429 Too Many Requests` — rate limit exceeded
- `503 Service Unavailable` — storage or queue unavailable

### `POST /api/v1/webhooks/email-inbound`

Unauthenticated but HMAC-verified webhook endpoint for email-forwarded deals.

**Headers:**
- `X-Webhook-Signature: <hmac>` (required)

**Body:** provider-specific (SendGrid, Postmark, etc.) — normalized by a provider-specific adapter inside the module

**Responses:**
- `200 OK` — accepted (always returned if signature valid, even on duplicates — webhook providers retry on non-2xx)
- `401 Unauthorized` — invalid HMAC
- `400 Bad Request` — malformed payload

## 8. Invariants

1. Every successfully ingested deal has exactly one row in `deals` and at least one row in `deal_audit_log` written in the same transaction.
2. No two deals in the same tenant can share the same `content_hash`. This is enforced at the DB level via unique constraint, not application code.
3. The storage key follows the pattern `tenants/{tenant_id}/deals/{deal_id}/source.pdf` with no exceptions. Tenant isolation at the storage layer depends on this.
4. `tenant_id` and `uploaded_by` are never read from request body — only from verified JWT claims.
5. A deal record is only written *after* the file is successfully stored in object storage. Order: validate → store → DB insert → enqueue. If any step fails, prior steps are rolled back or compensated.
6. The extraction job is only enqueued *after* the DB insert commits. No job is ever enqueued referencing a `deal_id` that doesn't exist.
7. Every ingestion attempt (success or failure) emits at least one log line with `request_id`, `tenant_id`, `user_id`, and outcome.

## 9. Trust Boundaries

| Boundary | Validation |
|---|---|
| Client → Upload endpoint | JWT required, rate-limited per user, file size + MIME + magic-byte checked |
| Email provider → Webhook endpoint | HMAC signature verification with per-provider secret, sender allowlist, rate-limited per source IP |
| Ingestion → Object storage | IAM role (no keys in code), tenant-scoped key prefix enforced, server-side encryption |
| Ingestion → Database | Parameterized queries via ORM, tenant_id injected from auth context |
| Ingestion → Background Jobs | Job payload contains only `deal_id` and `tenant_id`; worker re-fetches from DB (no trust in payload) |

## 10. Failure Modes

| Failure | Detection | System Behavior | User-Facing Behavior |
|---|---|---|---|
| Invalid MIME / not a PDF | Magic-byte check before storage | Reject with 415, no DB write, no storage write | "We only accept PDF files right now." |
| File too large | Size check at upload middleware | Reject with 413 before reading full body | "File is too large. Maximum is 50 MB." |
| Malformed / corrupted PDF | Input Validation module rejects | Reject with 422, log full error | "We couldn't read this PDF. It may be corrupted." |
| Duplicate content hash | Unique constraint violation on insert | Catch, return existing deal_id with 200 | "This document was already ingested. Viewing existing record." |
| Object storage unavailable | S3 client error or timeout | Return 503, do NOT write DB row, emit alert | "Something went wrong on our end. Please try again in a moment." |
| DB write fails after storage succeeds | Exception after `put_object` | Compensating delete of stored file, return 503 | Same as above |
| Job enqueue fails after DB commit | Queue write error | Log critical, emit alert, but return 201 (deal exists); reconciler sweeps stuck `UPLOADED` deals every 5 min and re-enqueues | "Your deal is being processed." (recovered transparently) |
| Webhook HMAC mismatch | Signature verification fails | Return 401, do not process payload, emit security metric | N/A (provider sees 401) |
| Webhook provider retries on 500 | Idempotency key handling | Content hash dedup catches it; return 200 with duplicate indicator | N/A |
| Malicious PDF (embedded JS, macros) | Input Validation sandbox | Reject with 422, log as security event | "This PDF contains content we can't process safely." |
| Zip bomb / decompression attack | Input Validation size-after-decompress check | Reject with 422 | Same |

### Recovery: the reconciler

Because the ordering is "store → DB → enqueue," there's a failure window where a deal exists in the DB but no extraction job was enqueued. A scheduled job runs every 5 minutes:

```
SELECT deal_id FROM deals
WHERE state = 'UPLOADED'
  AND created_at < NOW() - INTERVAL '2 minutes'
  AND deal_id NOT IN (SELECT deal_id FROM background_jobs WHERE job_type = 'extraction')
```

Any matching deals have their extraction job re-enqueued. This is the idempotency guarantee that keeps the system self-healing.

## 11. Security Considerations

- **Path traversal.** Filenames from clients are never used in storage keys. Keys are generated server-side from `tenant_id` + `deal_id`, both of which are controlled values.
- **Tenant isolation at storage layer.** IAM policies on the storage role restrict read/write to `tenants/${aws:PrincipalTag/tenant_id}/*` — even if application code has a bug, the storage layer enforces isolation.
- **Content-type sniffing attacks.** Do not trust the client's declared MIME type. Check magic bytes (`%PDF-`) on the first 5 bytes of the uploaded file.
- **PII in source metadata.** Email subjects and sender addresses may contain PII. These are stored in `source_metadata` JSONB, subject to the same encryption-at-rest as the rest of the DB, and scrubbed from log output.
- **Webhook secret rotation.** HMAC secrets for email webhooks rotate every 90 days. Both old and new secrets are valid during a 7-day overlap window to avoid dropped deliveries.
- **Upload endpoint DoS.** Rate limiting per user (10 uploads/minute), per tenant (100 uploads/minute), per IP (20 uploads/minute). Enforced by Rate Limiter module.
- **Storage cost attacks.** A malicious user could upload many distinct 50MB files to drive up storage costs. Tenant-level storage quotas are enforced at upload time — quota check against current usage before accepting the file.

## 12. Dependencies

**Internal modules:**
- Auth Service (JWT verification, tenant/user extraction)
- Input Validation (file content validation, sandboxing)
- Background Jobs (extraction job enqueue)
- Rate Limiter (throttling middleware)
- Observability (logging, metrics, tracing)
- Secrets & Config (storage credentials, webhook HMAC secrets)

**External services:**
- PostgreSQL (deals + audit log)
- Object storage (S3 in production, Fly.io volume in portfolio)
- Mail parsing provider (webhook source for email-forwarded deals — optional in v1)

**Libraries:**
- `fastapi` for HTTP layer
- `python-multipart` for multipart parsing
- `boto3` for S3 client (abstracted behind `StorageClient` interface)
- `sqlalchemy[asyncio]` for DB access
- `pydantic` for request/response schemas

## 13. Testing Requirements

### Unit tests
- Hash computation is deterministic for identical bytes
- Storage key generation follows the documented pattern
- Invalid MIME type returns 415 without touching storage or DB
- Oversized file returns 413 without reading full body into memory
- JWT claims are correctly extracted and used (tenant_id, user_id never from request body)

### Integration tests
- Successful upload: file lands in storage, deal row exists in DB, audit row exists, extraction job is enqueued, response contains correct `deal_id`
- Duplicate upload: second upload with same bytes returns 200 with same `deal_id` as first, no new storage write, no new DB row, no new job
- Storage failure mid-upload: no DB row is created, storage has no partial write (or compensating delete runs)
- DB failure after successful storage: compensating delete runs, storage has no orphan file
- Queue failure after DB commit: deal remains in `UPLOADED`, reconciler re-enqueues within 5 minutes

### Security tests
- Magic-byte check rejects `application/pdf` with non-PDF bytes
- Path traversal attempts in filename are ignored (key is server-generated)
- Request with forged `tenant_id` in body uses JWT claim, not body
- Webhook with invalid HMAC is rejected without processing
- Cross-tenant access attempt (user A tries to upload to tenant B via manipulated JWT) fails at auth layer before reaching Ingestion

### Load tests
- 100 concurrent uploads of 10MB files complete without data loss
- 1000 sequential uploads over 10 minutes do not exhaust DB connections or memory
- Duplicate storm: 100 concurrent uploads of the same file result in exactly 1 stored file, 1 DB row, 99 duplicate responses

### Reconciler tests
- Deal stuck in `UPLOADED` without a job is picked up and re-enqueued within 5 minutes
- Deal in `UPLOADED` with a pending job is NOT re-enqueued (no double-processing)
- Reconciler is itself idempotent — running it twice in a row does not enqueue duplicate jobs

## 14. Open Questions

1. **Multi-attachment emails.** When a forwarded email contains multiple PDFs, v1 processes only the first. Should future versions create one deal per attachment, or group them into a `deal_group`? Decision deferred until `deal_group` abstraction is needed elsewhere (see Evolution Check in `01_decomposition.md`).
2. **Filename preservation.** Original filename is stored in `source_metadata` but not used in storage. Do analysts want to see the original filename in the UI for context? Defer to Dashboard UI discussions.
3. **Content hash algorithm.** SHA-256 chosen for collision resistance. Is BLAKE3 worth the speedup for large files? Benchmark after Extraction Service is built and measure if hashing is on the critical path.
4. **Storage abstraction depth.** Should `StorageClient` abstract only put/get/delete, or also presigned URL generation? Presigned URLs are needed by Dashboard API for document preview. Proposed: include presigned URL generation in the interface so Dashboard API doesn't reach into S3 directly.
5. **Ingestion source extensibility.** v1 supports `upload`, `email_forward`, `webhook`. A future CRM integration (Salesforce inbound) would add `crm_sync`. The source enum is extensible, but the adapter layer needs to stabilize. Revisit after first CRM integration is scoped.