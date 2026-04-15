# Appendix A — Lifecycle Maps

Parent: `README.md` · Related: `00_main_spec.md`, `modules/*.md`

## Purpose

Reference diagrams showing how the primary entities move through the system over time. These are a reading aid: every transition shown here is defined authoritatively in a module spec, and this appendix just assembles the full picture in one place. If this diagram and a module spec disagree, the module spec wins.

## A.1 Deal Lifecycle (End-to-End)

The full journey of a deal from an inbound document to a final archived decision, annotated with which module owns each transition and what side effects fire.

```
┌─────────────────────────────────────────────────────────────────┐
│                         INGESTION                                │
│  [ingestion_service.md § 5]                                     │
│                                                                 │
│   upload / email / webhook                                      │
│          │                                                      │
│          ▼                                                      │
│   RECEIVED → VALIDATED → HASHED → DEDUPED → STORED → QUEUED    │
│     (ephemeral request-level states)                            │
│          │                                                      │
│          ▼                                                      │
│   Deal row inserted, state = UPLOADED                           │
│   Audit: DEAL_INGESTED                                          │
│   Job enqueued: extraction                                      │
└──────────┬──────────────────────────────────────────────────────┘
           │
           │ (background_jobs claims, dispatches to extraction handler)
           ▼
┌─────────────────────────────────────────────────────────────────┐
│                         EXTRACTION                               │
│  [extraction_service.md § 5]                                    │
│                                                                 │
│   DEQUEUED → FETCHED → PARSED → SCRUBBED → LLM_CALLED →        │
│               VALIDATED → PERSISTED → DONE                      │
│     (ephemeral job-level states)                                │
│          │                                                      │
│          ├─ ≥3 core fields extracted ──▶ state = EXTRACTED     │
│          │                              Audit: EXTRACTION_COMPLETED│
│          │                              In-process call → SCORING  │
│          │                                                      │
│          └─ <3 fields / unreadable ───▶ state = FAILED         │
│                                        Audit: EXTRACTION_FAILED │
│                                        (manual retry or re-upload)│
└──────────┬──────────────────────────────────────────────────────┘
           │
           │ (in-process, same worker process)
           ▼
┌─────────────────────────────────────────────────────────────────┐
│                         SCORING                                  │
│  [scoring_engine.md — Batch 2]                                  │
│                                                                 │
│   evaluate criteria → compute score + rationale                 │
│          │                                                      │
│          ▼                                                      │
│   state = SCORED                                                │
│   Audit: DEAL_SCORED                                            │
└──────────┬──────────────────────────────────────────────────────┘
           │
           │ (appears in analyst queue, dashboard_api)
           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ANALYST DECISION                             │
│  [dashboard_api.md — Batch 2]                                   │
│                                                                 │
│   analyst clicks PASS or PURSUE                                 │
│          │                                                      │
│          ▼                                                      │
│   state = DECIDED                                               │
│   Audit: DEAL_DECIDED (decision + optional notes)              │
└──────────┬──────────────────────────────────────────────────────┘
           │
           │ (retention policy or manual archive)
           ▼
┌─────────────────────────────────────────────────────────────────┐
│   state = ARCHIVED   (terminal, read-only)                      │
│   Audit: DEAL_ARCHIVED                                          │
└─────────────────────────────────────────────────────────────────┘
```

**Retry loop (not shown inline):** `FAILED → UPLOADED` on manual retry or re-upload per `00_main_spec.md` Phase 1 state machine. This is the only backward transition in the deal lifecycle. All other transitions are forward-only.

**Invalid transitions** (these must never occur, enforced by `transition_deal_state()`):
- `SCORED → UPLOADED` — re-extraction requires a new deal record
- `DECIDED → SCORED` — decisions are immutable
- Any skip of a state (e.g., `UPLOADED → SCORED`)

## A.2 Background Job Lifecycle

Per `modules/background_jobs.md` § 5. Summarized here for cross-reference:

```
   enqueue()
       │
       ▼
   PENDING ◀──────────────── (retry with backoff)
       │                              ▲
       │ worker claim (FOR UPDATE     │
       │  SKIP LOCKED)                │
       ▼                              │
   CLAIMED ─── claim expires ─▶ PENDING (no attempt++)
       │
       │ handler starts
       ▼
   RUNNING
       │
       ├── success ──▶ SUCCEEDED (terminal, retained JOB_RETENTION_DAYS)
       │
       └── failure ──▶ FAILED ──▶ attempts < max? ──yes──▶ PENDING
                                        │
                                        no
                                        ▼
                                 DEAD_LETTERED (terminal, retained indefinitely)
```

The `CLAIMED → PENDING` transition without incrementing `attempts` is the crash-recovery path — invariant 3 in `modules/background_jobs.md`.

## A.3 Extraction Job Inner Lifecycle

Per `modules/extraction_service.md` § 5. These are ephemeral request-level states that exist for tracing, not persistence:

```
DEQUEUED ─▶ FETCHED ─▶ PARSED ─▶ SCRUBBED ─▶ LLM_CALLED ─▶ VALIDATED ─▶ PERSISTED ─▶ DONE
   │          │          │           │             │
   ▼          ▼          ▼           ▼             ▼
 FAILED    FAILED     FAILED     FAILED       RETRY/FAIL
(storage) (parse)   (empty)    (scrub bug)  (LLM error)
```

Only `LLM_CALLED` has a retry loop; all other failures go straight to `FAILED` per Extraction's retry policy.

## A.4 Input Validation Inner Lifecycle

Per `modules/input_validation.md` § 5. Cheap checks first, expensive checks last:

```
RECEIVED → SIZE_CHECKED → TYPE_CHECKED → MAGIC_CHECKED → SANDBOX_CHECKED → SCHEMA_CHECKED → ACCEPTED
    │           │              │              │                │                 │
    ▼           ▼              ▼              ▼                ▼                 ▼
 REJECTED    REJECTED       REJECTED       REJECTED         REJECTED          REJECTED
(headers)  (body size)    (content-type) (magic bytes)   (pdf sandbox)    (pydantic)
```

Short-circuits on first failure. A `BODY_TOO_LARGE` rejection never reaches the sandbox.

## A.5 Observability State Lifecycle

Per `modules/observability.md` § 5. Process-level, not per-request:

```
UNINITIALIZED → INITIALIZING → READY → DRAINING → STOPPED
```

The `/health/readiness` probe returns 200 only in `READY`. `DRAINING` is entered on SIGTERM and persists for `OBS_DRAIN_TIMEOUT_S` (default 10s) to flush pending telemetry before the process exits.

## A.6 Request-to-Trace Propagation

How a single inbound HTTP request becomes one connected trace across modules, per `modules/observability.md` invariant 3:

```
HTTP request arrives at ALB
    │
    ▼
api task receives request
    │  (middleware generates request_id, trace_id, root span)
    ▼
Ingestion handler (span: ingestion.upload)
    │
    ├── stores file to S3 (span: storage.put)
    ├── inserts deal row (span: db.insert.deal)
    └── enqueues extraction job (span: jobs.enqueue)
            │  (trace_context embedded in job row)
            ▼
        [time passes — job waits in queue]
            │
            ▼
worker task claims job
    │  (reads trace_context from job row, resumes trace)
    ▼
Extraction handler (span: extraction.run, parent = jobs.enqueue)
    │
    ├── fetches file (span: storage.get)
    ├── parses PDF (span: extraction.parse)
    ├── scrubs PII (span: extraction.scrub)
    ├── calls LLM (span: extraction.llm_call)
    └── writes extraction row (span: db.insert.extraction)
            │
            ▼
Scoring handler (span: scoring.evaluate, in-process sibling of extraction.run)
    │
    └── writes score row (span: db.insert.score)
```

Result: a single trace tree in X-Ray spanning ingestion through scoring, across two ECS services and a queue boundary, rooted at the original HTTP request.

## A.7 Audit Log Write Pattern

Every state-changing action writes to `deal_audit_log` in the same DB transaction as the state change, per `modules/observability.md` invariant 4. This is not a lifecycle per se but a cross-cutting pattern worth showing:

```
Handler begins DB transaction
    │
    ├── UPDATE deals SET state = '...' WHERE deal_id = ...
    │
    ├── INSERT INTO deal_audit_log (..., before_state, after_state, ...)
    │
    └── COMMIT  ◀── both writes succeed or both roll back
```

If the audit insert fails, the state update rolls back with it. There is no fire-and-forget audit path. This is what makes the audit log a trustworthy record rather than a best-effort log.