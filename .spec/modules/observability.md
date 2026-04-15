# Module: Observability Stack

> Cross-cutting module. Parent: `../01_decomposition.md`
> Build order: step 2 (depends on Secrets & Config)

---

## 1. Purpose

Provide a single, opinionated interface for structured logging, metrics, distributed tracing, and audit-log persistence so every other module produces operationally consistent telemetry without making per-module decisions about format, transport, or PII handling.

## 2. Responsibilities

- Expose a `Logger` factory that returns a context-bound structured logger per request/job
- Enforce a single canonical log line schema (JSON) with required fields populated by middleware, not by callers
- Inject `request_id`, `tenant_id`, `user_id`, `module`, and `trace_id` into the logging context at request entry and propagate them to background jobs
- Expose a `Metrics` client with three primitives only: `counter`, `gauge`, `histogram`
- Maintain the metrics taxonomy and naming convention (`<module>.<subject>.<unit>`) as a documented allowlist
- Initialize the OpenTelemetry tracer, configure exporters, and provide context-propagation helpers for the Ingestion → Background Jobs → Extraction → Scoring flow
- Run the PII scrubber over every log record before it leaves the process
- Persist `deal_audit_log` rows on behalf of any caller invoking `audit.record(...)`, in the same DB transaction as the underlying state change
- Enforce log retention by emitting logs to a sink (stdout in dev, CloudWatch/Loki in prod) with configured TTLs
- Provide a `/metrics` Prometheus scrape endpoint and a `/health` liveness/readiness pair

## 3. Non-Responsibilities

- **Does not decide what to log.** Callers decide events; this module decides format.
- **Does not own business semantics of audit events.** It persists `deal_audit_log` rows; the meaning of `DEAL_INGESTED` lives in the relevant business module.
- **Does not implement alerting.** Alert rules live in the deployment/operations spec, evaluated by the metrics backend (Prometheus/CloudWatch), not by application code.
- **Does not ship logs.** A sidecar / platform agent (Fly.io log shipper, ECS awslogs driver) handles transport. This module writes to stdout in a structured format and stops there.
- **Does not perform sampling decisions for traces beyond the configured sampler.** No per-request "interesting" heuristics — keeps trace data uniform.
- **Does not store operational logs in PostgreSQL.** Operational logs go to the log sink. Only audit-log entries — which are business records, not debug data — live in the database.
- **Does not redact at the sink.** PII scrubbing happens in-process before emit, so no untrusted data ever leaves the process.

## 4. Inputs / Outputs

### Inputs

- Log calls: `logger.info(event, **fields)` where `event` is a short snake_case string and `fields` are structured key/values
- Metric calls: `metrics.increment("ingestion.attempts", tags={"source": "upload"})`, `metrics.observe("extraction.latency_ms", 1832)`
- Span calls: `with tracer.span("scoring.evaluate_criterion", attributes={...}):`
- Audit calls: `audit.record(deal_id, action, actor_type, actor_id, before_state, after_state, metadata, db_session)`
- HTTP request middleware hook (called by FastAPI on every request)
- Background job middleware hook (called by Background Jobs module on every job claim)

### Outputs

- JSON log lines on stdout (one per line, strictly newline-delimited)
- Prometheus exposition format on `GET /metrics`
- OTLP-formatted trace spans exported to the configured collector
- New rows in `deal_audit_log` (committed by the caller's DB session)

### Side Effects

- Internal in-memory metric registry maintains counters and histograms between scrapes
- Trace exporter holds a bounded buffer of pending spans; full buffer triggers a synchronous flush

### Canonical log line schema

Every log line — without exception — contains:

```json
{
  "ts":          "2026-04-11T14:32:08.142Z",
  "level":       "INFO",
  "event":       "ingestion.upload.received",
  "request_id":  "req_01HW8K...",
  "trace_id":    "0af7651916cd43dd8448eb211c80319c",
  "span_id":     "b7ad6b7169203331",
  "tenant_id":   "tnt_01HW...",
  "user_id":     "usr_01HW...",
  "module":      "ingestion_service",
  "duration_ms": 142,
  "fields":      { ... caller-supplied structured fields ... }
}
```

`request_id`, `tenant_id`, `user_id`, `module`, and `duration_ms` are populated by middleware, not by callers. If a caller is invoked outside any request context (e.g., a startup hook), those fields are populated with the literal string `"none"` so the schema is never violated.

## 5. State Machine

Observability has no business state. Internally, the tracer and metrics registry have a simple lifecycle:

```
UNINITIALIZED → INITIALIZING → READY → DRAINING → STOPPED
```

- `UNINITIALIZED` — process started, no exporter configured yet
- `INITIALIZING` — exporters connecting, secrets being loaded from Secrets & Config
- `READY` — accepting telemetry from all callers
- `DRAINING` — SIGTERM received, flushing pending spans/metrics for up to `OBS_DRAIN_TIMEOUT_S` (default 10s)
- `STOPPED` — exporters closed, process can exit

The HTTP `/health` readiness probe returns 200 only when state is `READY`. Liveness returns 200 in all states except `STOPPED`.

## 6. Data Model

Observability owns one table: `deal_audit_log`. Operational logs and metrics are not persisted in the application DB.

```sql
CREATE TABLE deal_audit_log (
    audit_id      TEXT PRIMARY KEY,            -- ULID
    deal_id       TEXT NOT NULL REFERENCES deals(deal_id),
    tenant_id     TEXT NOT NULL,
    actor_type    TEXT NOT NULL,               -- user | system | worker
    actor_id      TEXT,                        -- user_id, worker_id, or NULL for system
    action        TEXT NOT NULL,               -- DEAL_INGESTED, STATE_TRANSITION, etc.
    before_state  TEXT,
    after_state   TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}',
    request_id    TEXT,
    trace_id      TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_actor CHECK (actor_type IN ('user','system','worker')),
    CONSTRAINT actor_id_required_for_user CHECK (actor_type <> 'user' OR actor_id IS NOT NULL)
);

CREATE INDEX idx_audit_deal ON deal_audit_log(deal_id, created_at);
CREATE INDEX idx_audit_tenant_created ON deal_audit_log(tenant_id, created_at DESC);
```

The `actor_id_required_for_user` CHECK constraint enforces that user-attributable actions must have a user identity — invariant 4 below is held by the database, not by the application.

## 7. API Contract

This module exposes a Python interface and two HTTP endpoints. There is no public REST API.

### Python interface

```python
# logging
logger = get_logger("ingestion_service")
logger.info("upload.received", file_size_bytes=12345, source="upload")

# metrics
metrics.increment("ingestion.attempts", tags={"source": "upload"})
metrics.observe("extraction.latency_ms", duration_ms)
metrics.gauge("queue.depth", current_depth, tags={"job_type": "extraction"})

# tracing
with tracer.span("scoring.evaluate", attributes={"deal_id": deal_id}) as span:
    ...
    span.set_attribute("criteria_count", n)

# audit
audit.record(
    deal_id=deal_id,
    action="STATE_TRANSITION",
    actor_type="worker",
    actor_id=worker_id,
    before_state="UPLOADED",
    after_state="EXTRACTED",
    metadata={"fields_extracted": 5},
    db_session=session,    # caller's transaction; audit row commits with it
)
```

### HTTP endpoints

- `GET /health/liveness` → 200 unless STOPPED
- `GET /health/readiness` → 200 only when READY
- `GET /metrics` → Prometheus exposition format; unauthenticated but bound to internal interface only

## 8. Invariants

1. Every log line emitted from any module conforms to the canonical schema. Violation is a unit-test-enforced failure: the logger factory is the only way to produce log output, and it strips/normalizes any non-conforming caller input.
2. `request_id` is generated exactly once per inbound HTTP request and propagated to every downstream span, log line, and enqueued job for that request.
3. `trace_id` propagates across the Ingestion → Background Jobs → Extraction → Scoring boundary via the job payload's `trace_context` field, so a single deal's full lifecycle is one connected trace.
4. Every `deal_audit_log` row is written in the same DB transaction as the state change it records. This is enforced by requiring callers to pass their `db_session` into `audit.record()` — there is no async/fire-and-forget audit path.
5. No log line containing un-scrubbed PII ever leaves the process. The scrubber runs synchronously between log construction and emit; there is no bypass.
6. Metric names are constrained to a documented allowlist registered at startup. Emitting an unregistered metric raises in development and is dropped with a warning counter in production — preventing metric-name explosion.
7. The `/metrics` endpoint is never exposed on a public listener. Bound to localhost or the internal mesh interface only, enforced at the deployment layer and asserted at startup.

## 9. Trust Boundaries

| Boundary | Validation |
|---|---|
| Application code → Observability | Caller-supplied fields are scrubbed before emit; no caller can override `request_id`, `tenant_id`, `user_id`, or `trace_id` |
| Observability → stdout sink | Process-local pipe; trusted, but PII scrubber runs before write as defense in depth |
| Observability → metrics backend | Prometheus scrape pulls from `/metrics`; backend authenticates via network ACL, not application-level auth |
| Observability → trace collector | OTLP over TLS to configured endpoint; collector credentials loaded via Secrets & Config |
| Observability → Database (audit log) | Uses the caller's DB session — no separate connection, no separate trust context |

## 10. Failure Modes

| Failure | Detection | System Behavior | User-Facing Behavior |
|---|---|---|---|
| Trace exporter unreachable | OTLP send error | Buffer spans up to bounded size, then drop oldest; emit `tracing.export_failures` counter | None — request still succeeds |
| Trace buffer full during burst | Buffer size check on enqueue | Drop oldest spans, increment `tracing.dropped_spans` | None |
| Metrics registry contention | Lock timeout | Drop the metric write, increment `metrics.write_failures`, log warning | None |
| PII scrubber pattern matches a non-PII string (false positive) | Detected during dev via test corpus | Field is replaced with `[REDACTED]` in log; original is lost | None — degraded log fidelity, not user-facing |
| Audit row insert fails | DB exception inside caller's transaction | Caller's transaction rolls back — the state change does NOT commit; this is the desired behavior because audit and state are atomic | The triggering action returns the same error the caller raises (typically 5xx) |
| `/metrics` endpoint accidentally bound to public interface | Startup assertion fails | Process refuses to start, exits non-zero | Deployment fails loudly — preferred over silent exposure |
| Log sink (stdout pipe) blocks because shipper is stuck | Write blocks | Application threads block on write; circuit breaker after 1s switches to in-memory ring buffer and increments `logging.dropped_lines` | Request latency may rise briefly; no data loss for application behavior |
| Trace context missing on a job claim | Middleware check | Generate a new `trace_id`, log `tracing.missing_context_on_job` warning with `job_id` | None |
| Caller emits unregistered metric name | Allowlist check | Dev: raise. Prod: drop, increment `metrics.unregistered_emits` | None |
| Caller passes plaintext credential into log fields | Scrubber pattern match (`Bearer\s+\S+`, `sk-\S+`, etc.) | Replace with `[REDACTED]`, increment `logging.scrub_hits`, emit security-event log | None directly; security team alerted via metric |

## 11. Security Considerations

- **PII scrubbing.** The scrubber runs against every log record's `fields` and `event` before emit. Patterns include: email addresses, phone numbers, SSN-like sequences, JWTs, OpenAI keys (`sk-...`), AWS keys (`AKIA...`), and any field whose key matches a denylist (`password`, `token`, `secret`, `authorization`, `cookie`, `api_key`). Scrubbing replaces values with `[REDACTED]` rather than dropping the field, so the structure of log records is preserved.
- **Deal text never logged.** Extraction Service may log *that* extraction happened, latency, and field-level confidence — but never raw extracted text or document contents. This is enforced by a per-module field allowlist consulted by the logger.
- **Audit log immutability.** `deal_audit_log` has no UPDATE or DELETE permission granted to the application DB role. Only INSERT and SELECT. Migrations or compliance erasure use a separate role with DELETE granted, used only by explicit ops procedures.
- **Operational vs audit separation.** Operational logs are debug data and have a 30-day retention. Audit logs are business records, retained per the data retention policy in `00_main_spec.md` (currently: until tenant deletion + 30 days). The two never share storage.
- **Trace data PII.** Span attributes are scrubbed by the same scrubber pipeline before export. Span names are restricted to a static set — callers cannot put dynamic values into span names.
- **Log injection.** Because logs are JSON, newline injection from caller-supplied fields is impossible: the JSON encoder escapes newlines in string values. The unstructured human-readable formatter is disabled in production.
- **`/metrics` endpoint exposure.** Prometheus exposition can leak cardinality patterns (tenant IDs as label values would be a leak). Tenant IDs are never used as metric labels — only as log fields. Label values are restricted to a low-cardinality allowlist per metric.
- **Metric cardinality DoS.** A bug emitting `tags={"deal_id": deal_id}` would create one time series per deal and exhaust the metrics backend. The allowlist enforcement (invariant 6) is the primary defense; a secondary cardinality limiter caps total active series per metric.

## 12. Dependencies

**Internal modules:**
- Secrets & Config (OTLP collector endpoint, exporter credentials, log level)

**External services:**
- OpenTelemetry collector (OTLP receiver, e.g., Grafana Alloy or AWS Distro for OpenTelemetry)
- Metrics backend (Prometheus in portfolio phase, CloudWatch in ECS reference)
- Log sink (stdout → platform shipper)
- PostgreSQL (audit log only)

**Libraries:**
- `structlog` for structured logging
- `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp` for tracing
- `prometheus-client` for metrics exposition
- `sqlalchemy[asyncio]` for audit log writes (uses caller's session)

## 13. Testing Requirements

### Unit tests
- Logger output is valid JSON, one record per line, with all required fields populated
- Caller cannot override `request_id`, `tenant_id`, `user_id` via `**fields`
- PII scrubber matches all documented patterns and leaves structure intact
- Email, JWT, and OpenAI key patterns are redacted from log fields and span attributes
- Unregistered metric name raises in dev mode
- High-cardinality label value is rejected at registration time
- Audit `record()` requires a `db_session` argument and writes via that session

### Integration tests
- A request hitting Ingestion → Background Jobs → Extraction → Scoring produces a single connected trace with shared `trace_id` and parent/child span relationships intact
- `request_id` from inbound HTTP header (or generated if absent) appears in every downstream log line for that request and in every job enqueued by that request
- Audit row insertion failure rolls back the calling transaction (no orphan state change committed without an audit row)
- Concurrent log emission from 100 threads produces 100 well-formed JSON lines with no interleaving or corruption
- `/health/readiness` returns 503 until exporters are connected, then 200
- SIGTERM triggers DRAINING and pending spans/metrics flush within `OBS_DRAIN_TIMEOUT_S`

### Security tests
- Plaintext OpenAI key (`sk-...`) passed as a log field is redacted before emit
- JWT in an `Authorization` header field is redacted
- Field key `password` always redacts its value regardless of content
- Span attribute containing an email is scrubbed before export
- `/metrics` bound to a public interface causes startup to fail

### Load tests
- 10,000 log lines/sec sustained for 60s with no dropped lines and bounded memory
- 1,000 spans/sec sustained for 60s with bounded exporter buffer
- `/metrics` scrape under load completes within 1s

## 14. Open Questions

1. **Trace sampler.** Currently parent-based always-on (100%) for portfolio phase to make demos legible. At what production volume should this switch to a probabilistic sampler, and what rate? Defer until first real load profile is captured.
2. **Audit log archival.** Audit logs grow unbounded. Should rows older than 1 year move to a cold storage table (`deal_audit_log_archive`) or to S3 as compressed JSONL? Decision deferred until the first tenant exceeds 1M audit rows.
3. **Per-tenant log isolation.** For enterprise tier, tenants may demand their logs be stored in a tenant-scoped sink. This is a deployment-layer concern but would require the logger to tag every line with a routing label. Defer until enterprise tier is real.
4. **Log sink choice in portfolio phase.** Fly.io's built-in log shipping vs. shipping to Grafana Cloud Loki via a sidecar. Cost vs. queryability tradeoff. Decide as part of `appendices/C_flyio_portfolio_deployment.md`.
5. **OpenTelemetry logs API.** OTel logs is GA but ecosystem support varies. Currently using stdout-JSON + a shipper. Worth migrating to OTel logs once the trace and metrics pipelines are stable? Revisit after 6 months of operational data.
6. **Audit event taxonomy.** The `action` column is currently free-form TEXT. Should it become an enum (CHECK constraint) to prevent typos, or stay flexible for future event types? Leaning toward enum with a migration policy for additions — needs Conductor decision.