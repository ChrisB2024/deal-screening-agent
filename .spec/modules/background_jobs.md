# Module: Background Jobs

Cross-cutting module. Parent: `../01_decomposition.md` Build order: step 7 (depends on DB Models, Observability)

## 1. Purpose

Provide a durable, idempotent, observable async job execution substrate so any module can enqueue work that must complete eventually without blocking a request, with uniform retry, dead-lettering, and tracing across the whole system.

## 2. Responsibilities

- Accept typed job payloads via `enqueue(job_type, payload, *, idempotency_key, trace_context)` and persist them atomically in the `background_jobs` table
- Run one or more worker processes that claim jobs using `SELECT ... FOR UPDATE SKIP LOCKED` so multiple workers can run in parallel without double-processing
- Dispatch claimed jobs to the handler registered for the given `job_type` (e.g., `extraction` → Extraction Service's `run_extraction`)
- Enforce per-job-type retry policy: max attempts, backoff schedule, jitter, retry-on exception allowlist
- Move permanently-failed jobs to a dead-letter state and emit an alert
- Honor idempotency keys so a retried `enqueue` call never creates a duplicate job
- Propagate `trace_context` from the enqueueing request into the job span so the full lifecycle shows as one trace in Observability
- Emit metrics for queue depth, claim latency, run latency, attempt count, and dead-letter count
- Provide a reconciler hook that other modules (e.g., Ingestion) can use to re-enqueue work that was committed to the DB but never made it onto the queue
- Expose a minimal admin interface to inspect, retry, or drop dead-lettered jobs

## 3. Non-Responsibilities

- **Does not decide what work to do.** It executes registered handlers; the business logic belongs to the module that registered the handler.
- **Does not own job payload schemas.** Each job type has a Pydantic schema owned by the producing/consuming module; Background Jobs only validates that a schema exists and matches on dispatch.
- **Does not run long-lived services or cron jobs.** It runs discrete, bounded units of work. Scheduled jobs are a thin scheduler built on top of `enqueue`, not a separate concept.
- **Does not broker messages between modules at runtime.** Ingestion → Extraction is async via this queue; Extraction → Scoring is a direct in-process call, per `01_decomposition.md`. Background Jobs is for crossing time, not for crossing modules.
- **Does not guarantee ordering.** Jobs of the same type can run in any order. Ordering requirements must be encoded in the payload (e.g., by making downstream work depend on DB state, not on queue order).
- **Does not manage the DB schema for the tables jobs operate on.** It only owns `background_jobs` itself.
- **Does not handle secrets.** Handlers get their secrets from Secrets & Config like any other code path.

## 4. Inputs / Outputs

### Inputs

- `enqueue()` call from any module with: `job_type` (string, must be registered), `payload` (dict matching the type's schema), `idempotency_key` (optional string), `trace_context` (optional dict carrying `trace_id` and `parent_span_id`), `not_before` (optional timestamp for delayed execution)
- Worker startup reads job-type handler registrations from module initialization
- Reconciler sweeps from producing modules that want to re-enqueue orphaned work

### Outputs

- New row in `background_jobs` table on enqueue
- Handler invocation with typed payload when a worker claims a job
- State transitions on the job row: `PENDING → CLAIMED → RUNNING → SUCCEEDED` or `→ FAILED → (retry) PENDING` or `→ DEAD_LETTERED`
- Metrics: `jobs.enqueued`, `jobs.claimed`, `jobs.succeeded`, `jobs.failed`, `jobs.dead_lettered`, `jobs.queue_depth` (gauge), `jobs.claim_latency_ms`, `jobs.run_latency_ms`, `jobs.attempts` (histogram) — all tagged by `job_type`
- Structured log lines at every state transition, carrying `job_id`, `job_type`, `attempt`, `tenant_id` (if in payload), `trace_id`

### Side Effects

- DB row inserts and updates on `background_jobs`
- Spans emitted per claim and per run, linked to the enqueueing request's trace via `trace_context`
- Critical alert emitted when a job is dead-lettered or when queue depth exceeds a configured threshold

## 5. State Machine

```
                      ┌───────────────┐
   enqueue() ────────▶│    PENDING    │◀───────── retry scheduled
                      └───────┬───────┘
                              │ worker claims
                              ▼
                      ┌───────────────┐
                      │    CLAIMED    │ ─── claim expired (crash) ──▶ PENDING
                      └───────┬───────┘
                              │ handler starts
                              ▼
                      ┌───────────────┐
                      │    RUNNING    │
                      └───┬───────┬───┘
                          │       │
             success      │       │     failure
                          ▼       ▼
                  ┌───────────┐  ┌───────────┐
                  │ SUCCEEDED │  │  FAILED   │
                  └───────────┘  └─────┬─────┘
                                       │
                          attempts < max? ──yes──▶ PENDING (with backoff)
                                       │
                                       no
                                       ▼
                               ┌───────────────┐
                               │ DEAD_LETTERED │
                               └───────────────┘
```

**Transitions:**

- `PENDING → CLAIMED`: a worker executes `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1` against rows where `state = 'PENDING' AND not_before <= NOW()`, then updates the row to `CLAIMED` with `claimed_by` and `claim_expires_at` set. Atomic via transaction.
- `CLAIMED → RUNNING`: the worker begins handler execution. This transition exists so a crashed worker can be distinguished from a slow handler.
- `CLAIMED → PENDING`: if `claim_expires_at` passes without progress, the reaper moves the job back to `PENDING`. This is how worker crashes are recovered.
- `RUNNING → SUCCEEDED`: handler returns normally.
- `RUNNING → FAILED`: handler raises a retryable exception or returns a retryable failure result. `attempts` is incremented.
- `FAILED → PENDING`: if `attempts < max_attempts`, the reaper reschedules with `not_before = NOW() + backoff(attempts)`.
- `FAILED → DEAD_LETTERED`: if `attempts >= max_attempts` or the exception is in the non-retryable allowlist, the job moves to dead-letter and a critical alert fires.

**Terminal states:** `SUCCEEDED` and `DEAD_LETTERED`. Dead-lettered jobs can be manually re-enqueued via the admin interface, which creates a new row rather than mutating the terminal state.

## 6. Data Model

```sql
CREATE TABLE background_jobs (
    job_id            TEXT PRIMARY KEY,           -- ULID
    job_type          TEXT NOT NULL,              -- e.g. 'extraction'
    payload           JSONB NOT NULL,
    state             TEXT NOT NULL,              -- PENDING|CLAIMED|RUNNING|SUCCEEDED|FAILED|DEAD_LETTERED
    attempts          INT NOT NULL DEFAULT 0,
    max_attempts      INT NOT NULL,
    not_before        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_by        TEXT,                       -- worker_id
    claim_expires_at  TIMESTAMPTZ,
    idempotency_key   TEXT,
    trace_context     JSONB,                      -- {trace_id, parent_span_id}
    last_error        TEXT,                       -- truncated, scrubbed
    tenant_id         TEXT,                       -- denormalized from payload for filtering
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    succeeded_at      TIMESTAMPTZ,
    dead_lettered_at  TIMESTAMPTZ,
    CONSTRAINT valid_state CHECK (state IN
        ('PENDING','CLAIMED','RUNNING','SUCCEEDED','FAILED','DEAD_LETTERED')),
    CONSTRAINT attempts_bounded CHECK (attempts >= 0 AND attempts <= max_attempts),
    CONSTRAINT uq_idempotency UNIQUE (job_type, idempotency_key)
);

CREATE INDEX idx_jobs_claim ON background_jobs(state, not_before)
    WHERE state = 'PENDING';
CREATE INDEX idx_jobs_type_state ON background_jobs(job_type, state);
CREATE INDEX idx_jobs_tenant ON background_jobs(tenant_id, created_at DESC);
CREATE INDEX idx_jobs_dead_letter ON background_jobs(dead_lettered_at)
    WHERE state = 'DEAD_LETTERED';
```

The partial index on `(state, not_before) WHERE state = 'PENDING'` is what keeps claim queries fast: workers only scan pending, ready-to-run rows, not the full history.

The `UNIQUE (job_type, idempotency_key)` constraint makes idempotent enqueue atomic at the DB level. `idempotency_key` is nullable; `NULL` values do not collide because of standard SQL unique semantics.

Terminal jobs (`SUCCEEDED`) are retained for `JOB_RETENTION_DAYS` (default 7) before being purged by a sweeper. Dead-lettered jobs are retained indefinitely until manually resolved.

## 7. API Contract

No public HTTP API. Exposed as a Python interface plus a worker entry point plus an admin CLI.

```python
# Producer side
job_id = await jobs.enqueue(
    job_type="extraction",
    payload={"deal_id": deal_id, "tenant_id": tenant_id},
    idempotency_key=f"extraction:{deal_id}",
    trace_context=get_current_trace_context(),
)

# Handler registration (at module init)
@jobs.register("extraction", schema=ExtractionJob, max_attempts=5)
async def handle_extraction(job: ExtractionJob, ctx: JobContext) -> None:
    await extraction_service.run_extraction(job)

# Worker entry point
python -m background_jobs.worker --concurrency 4

# Admin CLI
python -m background_jobs.admin list-dead-letter
python -m background_jobs.admin retry <job_id>
python -m background_jobs.admin drop <job_id>
```

Handlers are `async` functions that accept the typed payload and a `JobContext` (carrying `job_id`, `attempt`, `trace_context`, and a cancellation token). Handlers return `None` on success or raise an exception to fail. Returning a value is a programmer error.

## 8. Invariants

1. A job is never executed more than once concurrently. The `SELECT ... FOR UPDATE SKIP LOCKED` claim combined with `claim_expires_at` guarantees exactly one worker owns a job at a time.
2. Every `enqueue` call either inserts exactly one new row or, if an idempotency key collision occurs, returns the existing job's ID without inserting. Duplicate enqueue is silent and safe.
3. A job's `attempts` counter is only incremented on transitions from `RUNNING → FAILED`. A claim that expires due to worker crash moves `CLAIMED → PENDING` without incrementing, because no handler code ran to completion.
4. Handlers are treated as idempotent by the framework. If a handler is not idempotent, that is a bug in the handler, not the framework — documented per job type and enforced by the Validator agent's tests.
5. `trace_context` is propagated into the job span unchanged, so a job's span has the enqueueing request as its parent. A connected trace from Ingestion → Extraction → Scoring is the observable result.
6. Dead-lettering is terminal and loud. A dead-letter is never silent: it always emits a critical-severity log line and increments `jobs.dead_lettered` with a `job_type` tag so alerts can fire.
7. No row in `background_jobs` is ever deleted except by the retention sweeper (for `SUCCEEDED` rows past TTL) or by explicit admin action on `DEAD_LETTERED` rows. Transient state transitions never lose history.
8. A worker claims at most one job per claim query. Batching is deferred (see Open Questions) because it complicates crash recovery.
9. `tenant_id` in the denormalized column always matches `payload->>'tenant_id'` when the payload has one. This is asserted on insert.

## 9. Trust Boundaries

| Boundary | Validation |
|---|---|
| Producer module → `enqueue()` | `job_type` must be registered; payload is Pydantic-validated against the registered schema; `idempotency_key` is length-capped |
| Queue row → Worker | Payload is re-validated against the current schema at dispatch time (schemas can evolve between enqueue and run); mismatch goes straight to dead-letter with reason `SCHEMA_EVOLUTION` |
| Worker → Handler | Handler receives a typed object, not raw JSON; `JobContext` is constructed by the framework, not the payload |
| Handler → DB | Handler uses its own module's DB session; Background Jobs does not share a transaction with the handler |
| Admin CLI → `background_jobs` table | CLI requires a privileged DB role (separate from the application role) to modify dead-lettered rows |

## 10. Failure Modes

| Failure | Detection | System Behavior | User-Facing Behavior |
|---|---|---|---|
| Worker crashes mid-handler | `claim_expires_at` passes with row still in `CLAIMED` or `RUNNING` | Reaper moves row back to `PENDING`, does NOT increment `attempts`; another worker picks it up | Deal processing is briefly delayed; the user sees the deal stay in its pre-job state until the retry succeeds |
| Handler raises retryable exception | Exception class in retryable allowlist | Row → `FAILED`, `attempts++`, scheduled for retry with backoff and jitter | Transparent until max attempts exhausted; user sees no change |
| Handler raises non-retryable exception | Exception class in non-retryable allowlist (e.g., `SchemaViolation`, `TenantMismatch`) | Row → `DEAD_LETTERED` immediately, critical alert fires | Deal transitions to `FAILED` (via the handler's own logic); user sees "Extraction failed. Please retry." |
| Handler exceeds max attempts | `attempts >= max_attempts` after failure | Row → `DEAD_LETTERED`, critical alert fires | User sees the deal in its pre-job state with a "processing failed" indicator; retry is manual |
| DB unreachable on enqueue | SQLAlchemy exception | `enqueue()` raises to the producer; producer is expected to treat this as a request-level failure | HTTP 503 on the producing endpoint, returned by Ingestion |
| DB unreachable during claim | Worker query fails | Worker sleeps with backoff and retries claiming; no job state change | None — queue drains slower, depth metric rises |
| Queue depth exceeds threshold | `jobs.queue_depth` gauge | Alert fires; deployment may scale workers | None directly; eventually latency-visible if unresolved |
| Idempotency collision | Unique constraint violation on insert | `enqueue()` catches, loads existing row, returns its `job_id` without error | Producer gets the existing job ID back; behavior is "enqueue was a no-op" |
| Trace context malformed | Validation at enqueue | Job enqueues with empty `trace_context`, warning metric `jobs.trace_context_invalid` | None |
| Handler for unknown `job_type` at dispatch | Registry lookup miss | Row → `DEAD_LETTERED` with reason `UNKNOWN_JOB_TYPE`, critical alert | None directly; indicates a deployment mismatch (new job type enqueued by new code, worker running old code) |
| Payload fails schema validation at dispatch | Pydantic error on re-validation | Row → `DEAD_LETTERED` with reason `SCHEMA_EVOLUTION` | None directly; indicates a schema change without a migration plan |
| Clock skew between producer and worker | `not_before` comparison against `NOW()` | Worker uses DB clock via `NOW()`, not local clock, so skew is bounded by DB replication | None |
| Reaper itself crashes | No heartbeat from reaper process | Orphaned `CLAIMED` rows accumulate; alert fires on `jobs.claim_age_seconds` gauge exceeding threshold | Jobs eventually delayed; on-call paged |

### Retry policy

- Default: `max_attempts=5`, exponential backoff (1s, 4s, 16s, 64s, 256s) with ±25% jitter
- Per job type override at registration time
- Non-retryable exception allowlist: `SchemaViolation`, `TenantMismatch`, `UnknownJobType`, `PermanentAuthFailure`
- Retryable exception allowlist: `TimeoutError`, `ConnectionError`, `TransientLLMError`, `StorageUnavailable`
- Uncategorized exceptions are treated as retryable by default but logged at warning level — this is deliberate, because failing open on retry is safer than failing open on skip

## 11. Security Considerations

- **Payload PII.** Job payloads may contain `tenant_id`, `user_id`, and `deal_id`, but must never contain PII like names or emails. Enforced by review: schemas are owned by producing modules, and the Validator agent checks them for PII-ish field names.
- **Job replay attacks.** Admin retry of a dead-lettered job creates a new row with a new `job_id`, not a mutation of the dead-letter row. The original dead-letter row stays as an immutable record.
- **Privileged admin interface.** The admin CLI uses a separate DB role with update/delete permissions on `background_jobs`. The application DB role has only INSERT and SELECT plus UPDATE on non-terminal states. This prevents a compromised application process from silently clearing dead-letters.
- **Poisoned queue.** A single bad job that crashes the handler repeatedly is isolated by the retry cap and dead-lettering — it cannot indefinitely tie up a worker slot.
- **`last_error` scrubbing.** Error strings stored in the row pass through Observability's PII scrubber before persistence. Stack traces are truncated to 4 KB and have API keys/tokens redacted.
- **Tenant cross-contamination.** The worker's handler contract requires cross-checking `tenant_id` in the payload against the row it operates on (see Extraction Service invariant 9). Background Jobs does not enforce this itself, but the `tenant_id` denormalized column lets ops audit whether any job operated on a mismatched tenant.
- **Claim expiry tuning.** Too short a claim expiry causes spurious re-runs under load. Too long delays recovery from crashes. Default is `max(300s, handler_p99 * 2)` per job type, measured and tuned per type.

## 12. Dependencies

**Internal modules:**
- Observability (logging, metrics, tracing, alerts)
- Secrets & Config (DB credentials, retention config)
- Every producing and consuming module registers handlers with Background Jobs at startup

**External services:**
- PostgreSQL — the queue is a table, not a separate broker. This is deliberate: it keeps the dependency surface small and gives us transactional enqueue with the producing write.

**Libraries:**
- `sqlalchemy[asyncio]` for DB access
- `pydantic` for payload schemas
- `tenacity` for retry scheduling math (backoff + jitter calculators only; the retry loop itself is in the framework)
- Standard library `asyncio` for worker concurrency

## 13. Testing Requirements

### Unit tests

- `enqueue` with a valid payload inserts one row in `PENDING`
- `enqueue` with a duplicate `idempotency_key` returns the existing `job_id` without inserting
- `enqueue` with an unregistered `job_type` raises immediately (producer bug, not a dead-letter)
- Backoff schedule matches spec: 1s, 4s, 16s, 64s, 256s ± jitter
- Non-retryable exceptions dead-letter on first failure
- Retryable exceptions increment `attempts` and reschedule
- `trace_context` is propagated into the handler's span

### Integration tests

- Full lifecycle: enqueue → claim → run → succeed, with a real DB
- Two workers racing for one job: exactly one wins the claim; the other gets a different job or sleeps
- Worker crash mid-run: kill the worker, reaper moves row back to `PENDING`, another worker picks it up, `attempts` is not incremented
- Job exceeds max attempts: row ends in `DEAD_LETTERED` with `last_error` populated
- Schema evolution: enqueue with v1 schema, change handler to v2 schema, dispatch dead-letters the job with reason `SCHEMA_EVOLUTION`
- Reconciler integration: Ingestion's "stuck UPLOADED" reconciler re-enqueues orphaned deals and the result is idempotent (no duplicates created)
- Delayed job: `not_before` in the future is respected; workers do not claim early

### Security tests

- `last_error` containing an OpenAI API key has the key redacted before persistence
- Application DB role cannot DELETE from `background_jobs` or UPDATE a `DEAD_LETTERED` row
- Admin CLI requires the privileged role and fails clearly without it
- Payload with unexpected extra fields (`additionalProperties` attack) is rejected at enqueue

### Load tests

- 10,000 jobs enqueued in 60s with 8 workers drain within SLA
- Claim query p99 under 20 ms with 1M pending rows (index behavior check)
- Queue depth metric accurately reflects pending count under load
- Worker concurrency of 16 does not cause claim contention collapse

### Chaos tests

- Kill workers randomly during a 1000-job run; all jobs eventually succeed or dead-letter, none are silently lost
- Disconnect the DB for 10s mid-run; workers recover without dead-lettering in-flight jobs spuriously
- Clock skew simulated by advancing DB clock by 60s; `not_before` handling remains correct

## 14. Open Questions

1. **Batching claims.** Workers currently claim one job at a time. Batching would cut claim-query overhead but complicates crash recovery (a worker crash with a batch of 10 claimed jobs means 10 orphaned rows to reap instead of 1). Defer until claim query CPU is actually measured as a bottleneck.
2. **Priority queues.** All jobs of a given type share a single FIFO order. A future "manual retry" action might want to jump the queue ahead of routine work. A `priority` column would add it cheaply, but the retry-scheduling math interacts with priority in non-obvious ways. Defer until a real use case surfaces.
3. **Scheduled / cron jobs.** A thin scheduler that enqueues jobs on a schedule is straightforward to build on top of `enqueue` with `not_before`. Should it live in Background Jobs or in a separate module? Leaning toward "same module, separate submodule" — needs Conductor decision.
4. **Moving off PostgreSQL for the queue.** At very high throughput, a DB-backed queue underperforms a purpose-built broker (Redis streams, SQS, NATS). The `LLMClient`-style interface pattern could be applied here too, with a `JobQueueBackend` interface and a Postgres implementation as the default. Defer until throughput measurements justify the added surface.
5. **Dead-letter auto-resolution policies.** Dead-lettered jobs currently require manual admin action. Some failure classes (e.g., `STORAGE_UNAVAILABLE` after a known outage) could be auto-reset once the root cause clears. Needs a decision on who owns the policy: framework or per-module.
6. **Visibility of `tenant_id` in payloads.** The denormalized `tenant_id` column exists for filtering and auditing, but duplicates the value in the payload. Should the framework enforce that every job type's payload declare a `tenant_id` field (making this a real foreign-key-shaped thing), or keep it optional? Leaning toward mandatory for v2 — needs Conductor decision.
7. **Retention of `SUCCEEDED` rows.** Default is 7 days. Long enough to debug a "why did this deal score weirdly yesterday" question, not so long that the table grows unbounded. Revisit after the first month of real traffic.