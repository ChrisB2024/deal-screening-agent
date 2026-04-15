# Module: Input Validation

Cross-cutting module. Parent: `../01_decomposition.md` Build order: step 6 (depends on Observability)

## 1. Purpose

Provide a single, trusted gatekeeper for every untrusted byte entering the system — HTTP request bodies, uploaded files, and webhook payloads — so downstream modules can treat their inputs as well-formed, bounded, and free of obviously malicious content.

## 2. Responsibilities

- Enforce request-level limits: maximum body size, maximum header size, maximum field count, maximum multipart parts
- Validate `Content-Type` against a per-endpoint allowlist before the body is read
- Verify uploaded file magic bytes against the declared MIME type (`%PDF-` for PDFs, etc.)
- Run a sandboxed PDF structural check: parseable header, trailer present, no JavaScript actions, no embedded executables, no external references, no encryption, page count within bounds
- Detect and reject decompression bombs: reject any file whose decompressed size exceeds `MAX_DECOMPRESSED_BYTES` or whose compression ratio exceeds `MAX_COMPRESSION_RATIO`
- Validate JSON request bodies against registered Pydantic schemas before handing them to route handlers
- Normalize and validate webhook payloads per provider adapter (SendGrid, Postmark) after HMAC verification
- Emit a typed `ValidationFailure` with a machine-readable reason code on every rejection — never a raw exception
- Record every validation decision (accepted, rejected, reason) via Observability for audit and tuning

## 3. Non-Responsibilities

- **Does not authenticate requests.** Auth Service middleware runs first; Input Validation sees only authenticated requests for protected endpoints.
- **Does not rate-limit.** Rate Limiter middleware runs before this module.
- **Does not store anything.** Validation is stateless per request; any audit trail is via Observability, not a module-owned table.
- **Does not parse document text for business content.** Structural validity only. Text extraction is Extraction Service's job.
- **Does not perform virus scanning.** ClamAV integration is an Open Question; v1 relies on structural checks and sandboxing.
- **Does not decide what to do with rejected requests.** It returns a typed failure; the calling module maps that to an HTTP status or job failure.
- **Does not own the schemas it validates against.** Schemas are defined in the modules that consume the data (Ingestion, Dashboard API); this module is only the validator runtime.
- **Does not scrub PII.** That's Observability's scrubber for logs and Extraction Service's scrubber for LLM calls.

## 4. Inputs / Outputs

### Inputs

- Incoming HTTP request (headers + body stream), passed via FastAPI middleware hook
- Uploaded file bytes (streamed, not fully buffered until size check passes)
- JSON payloads from route handlers invoking `validate_body(schema, payload)`
- Webhook payloads from route handlers invoking `validate_webhook(provider, headers, body)`

### Outputs

- `ValidatedRequest` object containing normalized headers, parsed body, and a list of validated file handles — or
- `ValidationFailure` typed result with one of the reason codes:
  - `BODY_TOO_LARGE`, `HEADER_TOO_LARGE`, `TOO_MANY_PARTS`
  - `UNSUPPORTED_CONTENT_TYPE`, `MIME_MAGIC_MISMATCH`
  - `MALFORMED_PDF`, `PDF_CONTAINS_JAVASCRIPT`, `PDF_ENCRYPTED`, `PDF_TOO_MANY_PAGES`
  - `DECOMPRESSION_BOMB`, `COMPRESSION_RATIO_EXCEEDED`
  - `SCHEMA_VIOLATION`, `WEBHOOK_MALFORMED`

### Side Effects

- Structured log line per validation decision (accepted or rejected) with reason code, size, content type, `request_id`
- Metrics emitted: `input_validation.accepted`, `input_validation.rejected` (tagged by reason), `input_validation.file_size_bytes` (histogram), `input_validation.duration_ms`
- On sandbox failures: a security-event log line tagged `security.input_validation.sandbox_reject`

## 5. State Machine

Input Validation is stateless per request. Internally each validation follows an ephemeral flow used for tracing:

```
RECEIVED → SIZE_CHECKED → TYPE_CHECKED → MAGIC_CHECKED → SANDBOX_CHECKED → SCHEMA_CHECKED → ACCEPTED
    │          │              │              │                │                 │
    ▼          ▼              ▼              ▼                ▼                 ▼
 REJECTED   REJECTED       REJECTED       REJECTED         REJECTED          REJECTED
```

Each transition emits a span. The order is fixed and short-circuits on first failure — a `BODY_TOO_LARGE` rejection never reaches the sandbox, which is the point: cheap checks first, expensive checks last.

## 6. Data Model

Input Validation owns no tables. It produces no persisted rows. All observability goes through the Observability module's log and metric sinks.

## 7. API Contract

No public HTTP API. Exposed as a Python interface consumed by FastAPI middleware and route handlers.

```python
# Middleware hook (registered once at app startup)
app.add_middleware(InputValidationMiddleware, config=validation_config)

# Per-route file validation
result = await validator.validate_upload(
    file_stream=request.stream(),
    declared_mime="application/pdf",
    max_bytes=50 * 1024 * 1024,
    endpoint="ingestion.upload",
)
if isinstance(result, ValidationFailure):
    raise HTTPException(status_code=result.http_status, detail=result.reason)

# Per-route JSON body validation
payload = await validator.validate_body(
    schema=CreateCriteriaRequest,
    raw_bytes=await request.body(),
)

# Webhook validation
webhook = await validator.validate_webhook(
    provider="sendgrid",
    headers=request.headers,
    raw_body=await request.body(),
)
```

Every entry point returns either a typed success value or a `ValidationFailure`. Exceptions are reserved for programmer errors (unregistered endpoint, missing schema), never for untrusted-input rejections.

## 8. Invariants

1. No route handler ever receives a request body that has not passed size, content-type, and (for files) magic-byte validation. This is enforced by the middleware running before any handler.
2. File size is checked by streaming and counting, never by trusting the `Content-Length` header. A request claiming 1 KB but streaming 100 MB is cut off at the configured limit.
3. Magic bytes are checked against the declared MIME type before any structural parsing. A file declared `application/pdf` but starting with `PK\x03\x04` is rejected as `MIME_MAGIC_MISMATCH` before the PDF sandbox ever runs.
4. PDF structural checks run in a subprocess with `MAX_SANDBOX_CPU_S` and `MAX_SANDBOX_MEMORY_MB` limits. The main process never parses untrusted PDF bytes directly.
5. Validation decisions are deterministic for a given input and config. The same bytes under the same config version always produce the same `ValidationFailure` or `ValidatedRequest`.
6. Rejected requests never cause partial writes anywhere in the system. Input Validation runs before Ingestion touches storage or the DB.
7. The `ValidationFailure` reason code is drawn from a closed enum. New reason codes require a code change and a migration of downstream consumers.

## 9. Trust Boundaries

| Boundary | Validation |
|---|---|
| Client → Middleware | Everything: size, headers, content type, magic bytes, structural validity. This is where untrusted becomes trusted. |
| Middleware → Route handler | Handler receives `ValidatedRequest` only; raw untrusted bytes are no longer in scope |
| Validator → PDF sandbox subprocess | IPC via pipes with size-bounded messages; subprocess has no network, no filesystem write access, dropped privileges |
| PDF sandbox → Validator | Subprocess returns a typed result (struct-valid or failure reason); any crash is treated as `MALFORMED_PDF` |
| Webhook provider → Webhook validator | HMAC already verified upstream; Input Validation only shape-validates the decoded payload |

## 10. Failure Modes

| Failure | Detection | System Behavior | User-Facing Behavior |
|---|---|---|---|
| Body exceeds size limit | Streaming byte counter | Stream cut off at limit, `BODY_TOO_LARGE` returned, no buffering of excess bytes | HTTP 413 "File is too large. Maximum is 50 MB." |
| Content-Type not allowlisted | Header check before body read | `UNSUPPORTED_CONTENT_TYPE` returned, body never read | HTTP 415 "We only accept PDF files right now." |
| Declared PDF, magic bytes wrong | First-5-byte check after size check | `MIME_MAGIC_MISMATCH` returned, sandbox not invoked | HTTP 415 "This doesn't appear to be a valid PDF." |
| PDF contains `/JavaScript` action | Sandbox subprocess inspects catalog | `PDF_CONTAINS_JAVASCRIPT`, security event emitted | HTTP 422 "This PDF contains content we can't process safely." |
| PDF is encrypted | Sandbox detects `/Encrypt` in trailer | `PDF_ENCRYPTED` returned | HTTP 422 "Encrypted PDFs aren't supported. Please upload an unlocked copy." |
| PDF page count > `MAX_PDF_PAGES` | Sandbox page scan | `PDF_TOO_MANY_PAGES` returned | HTTP 422 "This PDF has too many pages. Maximum is 500." |
| Zip bomb (compressed stream expands > limit) | Streaming decompression with running byte cap | `DECOMPRESSION_BOMB` returned at the moment the cap is hit; decompression aborted | HTTP 422 "This file couldn't be safely processed." |
| Sandbox subprocess OOM or CPU timeout | Resource limit hit | Subprocess killed, `MALFORMED_PDF` returned, `sandbox.timeouts` metric incremented | HTTP 422 "We couldn't read this PDF." |
| Sandbox subprocess crashes unexpectedly | Parent sees non-zero exit without typed result | `MALFORMED_PDF` returned, critical alert emitted for repeated crashes | HTTP 422 "We couldn't read this PDF." |
| JSON body doesn't match schema | Pydantic validation | `SCHEMA_VIOLATION` with field-level error list | HTTP 400 with field errors in standard envelope |
| Webhook payload malformed after HMAC pass | Provider adapter parse fails | `WEBHOOK_MALFORMED` returned, security metric incremented (HMAC valid + payload invalid is suspicious) | HTTP 400 to provider (which will retry or dead-letter per their policy) |
| Malicious header causing parser DoS | Header size check | `HEADER_TOO_LARGE` returned before body read | HTTP 431 |

### Sandbox crash policy

A single sandbox crash is treated as `MALFORMED_PDF` and surfaced normally. A crash rate above `SANDBOX_CRASH_ALERT_THRESHOLD` (default: 5 crashes in 60 seconds) triggers a critical alert — it likely indicates either a new exploit or a bug in the sandbox runtime itself.

## 11. Security Considerations

- **Defense in depth with Ingestion.** Ingestion also checks size and MIME for its own reasons (early rejection, user feedback). Input Validation is the authoritative check; Ingestion's checks are convenience, not security.
- **Sandbox isolation.** PDF structural parsing runs in a subprocess because PDF parsers are historically a rich source of RCE bugs. The subprocess has: no network, read-only filesystem except a tmpfs scratch dir, `setrlimit` CPU and memory caps, and `seccomp` syscall filtering where available.
- **Decompression bombs.** Both PDF object streams and multipart form data can hide bombs. The streaming decompressor enforces a running decompressed-byte cap and a compression-ratio cap. Either trip aborts.
- **Path traversal in filenames.** Filenames are extracted from multipart parts for logging only. They are never used to construct filesystem or storage paths — Ingestion generates storage keys server-side from `tenant_id + deal_id`.
- **Content-type confusion.** The client's declared MIME type is treated as a hint only; magic bytes are authoritative. A file declared `image/png` with `%PDF-` bytes is rejected, because it means either a bug or an attack.
- **PDF JavaScript and embedded files.** PDFs with `/JavaScript`, `/JS`, `/Launch`, `/EmbeddedFile`, or `/OpenAction` entries pointing at scripts are rejected. Legitimate CIMs do not need these features.
- **Sandbox output trust.** The sandbox returns a typed result over a length-prefixed pipe. The parent never `eval`s or deserializes arbitrary data from the sandbox — only reads a fixed struct.
- **Error message leakage.** `ValidationFailure` reason codes are a closed enum shared with clients. Internal parser error strings are logged but never returned to clients, so a malformed PDF doesn't leak library stack traces that could aid an attacker fingerprinting the parser version.
- **Resource exhaustion via concurrency.** A global semaphore caps concurrent sandbox invocations at `MAX_CONCURRENT_SANDBOXES`. Above that limit, new validations queue briefly then return `503` to the caller rather than spawning unbounded subprocesses.

## 12. Dependencies

**Internal modules:**
- Observability (logging, metrics, security events)
- Secrets & Config (sandbox limits, size limits, reason-code enum version)

**External services:**
- None. Input Validation runs entirely in-process plus its subprocess sandbox.

**Libraries:**
- `pypdf` or `pdfminer.six` for PDF structural inspection (inside sandbox only)
- `python-multipart` for multipart streaming (wrapped to enforce size cap)
- `pydantic` for JSON schema validation
- `python-magic` or a small in-house magic-byte matcher for MIME sniffing
- Standard library `subprocess`, `resource`, `signal` for sandbox management

## 13. Testing Requirements

### Unit tests

- Body size cap triggers at the exact configured byte, not after full buffering
- `Content-Length` lying about size is ignored; streaming counter is authoritative
- Magic-byte check rejects PDFs with wrong header and accepts PDFs with correct header
- Every closed-enum reason code has a test that produces it
- Pydantic schema validation produces structured field errors, not raw exceptions
- Webhook adapters normalize SendGrid and Postmark payloads to the same internal shape

### Integration tests

- Full upload flow: 50 MB valid PDF passes; 51 MB PDF rejects with `BODY_TOO_LARGE`
- Sandbox happy path: benign PDF returns `ValidatedRequest` with structural metadata
- Sandbox reject: PDF with `/JavaScript` returns `PDF_CONTAINS_JAVASCRIPT`
- Sandbox crash: deliberately malformed PDF that crashes the parser returns `MALFORMED_PDF`, not a 500
- Sandbox timeout: PDF crafted to hang the parser is killed at `MAX_SANDBOX_CPU_S` and returns `MALFORMED_PDF`
- Concurrent load: 50 concurrent validations do not exceed `MAX_CONCURRENT_SANDBOXES` subprocesses

### Security tests

- Known-bad PDF corpus (from public fuzzing datasets) runs without RCE, without parent crash, producing only typed failures
- Zip bomb corpus: 42.zip and equivalents are rejected as `DECOMPRESSION_BOMB` without exhausting memory
- Content-type confusion: PE executable renamed to `.pdf` with `application/pdf` MIME is rejected as `MIME_MAGIC_MISMATCH`
- Header-size attack: 1 MB of headers is rejected as `HEADER_TOO_LARGE` before body read
- Sandbox escape attempt: PDF with external references to `file:///etc/passwd` cannot read the file (sandbox has no FS read access outside scratch)
- Parser stack traces never appear in HTTP response bodies

### Load tests

- 100 concurrent validations of 10 MB PDFs complete within SLA
- Sustained 50 validations/sec for 60s with bounded memory
- Sandbox crash rate above threshold triggers alert within one metric scrape interval

## 14. Open Questions

1. **Virus scanning.** Should we integrate ClamAV (or a cloud equivalent) as a post-structural check? It adds a dependency and ~200 ms of latency per upload. Defer until a real compliance or enterprise customer asks. Track `sandbox.reject` reasons to see whether structural checks are catching what matters.
2. **Non-PDF document types.** DOCX and XLSX CIMs exist in the wild. Adding them expands the sandbox surface significantly (Office formats are zip containers with XML, macros, external references). Defer until upload telemetry shows meaningful rejection of non-PDF uploads.
3. **Sandbox runtime.** Current design uses a subprocess with `rlimit` + `seccomp`. A gVisor or Firecracker microVM would be stronger but much heavier. Revisit if we see any sandbox escape in the wild or if a compliance regime requires hardware isolation.
4. **Config hot-reload.** Size limits and reason-code enum are loaded at startup. Should limit changes hot-reload via Secrets & Config, or require a deploy? Leaning toward deploy-only for auditability — needs Conductor decision.
5. **Per-tenant validation policies.** Enterprise tenants may want stricter (or looser) page counts and size caps. The config layer supports per-tenant overrides in principle, but the Conductor hasn't decided whether that's a v1 feature. Defer until enterprise tier is real.
6. **PDF/A and other profiles.** Some firms standardize on PDF/A for archival. Should we detect and flag non-PDF/A uploads? Purely informational, not a rejection reason. Defer until a user asks.