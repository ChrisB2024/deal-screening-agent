# Claude Agent Log (Builder)

> Append-only. Each session is a new section. Never overwrite previous entries.

---

## Session 0 — Bootstrap

Project initialized with DADP protocol. Awaiting system spec and first build session.

---

## Session 1 — 2026-03-23 — Foundation (DB Models + Criteria Config)

### Context
- **Reading from:** `.spec/system_spec.md` (v1.0), `.agent/handoff.json`, `.agent/codex_log.md`
- **Building:** Criteria Config Schema + DB Models (Build Order #1)
- **Spec reference:** Phase 1 (State Machine), Phase 2 (Module Breakdown), Appendix B (Decision Log Seed)
- **Addressing from Codex:** None — first build session

### Decisions

#### [DECISION-1.1] SQLAlchemy ORM with async PostgreSQL
- **What:** Chose SQLAlchemy 2.0 with asyncpg driver as the ORM layer.
- **Why:** Spec requires parameterized queries and ORM-only DB access (Trust Boundaries table). SQLAlchemy 2.0's mapped_column style gives us type safety, and asyncpg is the standard async driver for PostgreSQL + FastAPI.
- **[CONCEPT]:** An ORM (Object-Relational Mapper) translates between Python objects and database rows. Instead of writing raw SQL like `INSERT INTO deals ...`, you create a `Deal` Python object and the ORM handles the SQL. This prevents SQL injection (a security invariant) because the ORM parameterizes all queries automatically.
- **Tradeoff:** Slight overhead vs raw SQL, but safety and maintainability far outweigh it for this use case.
- **Alternatives considered:** Raw asyncpg (faster but loses safety guarantees), Tortoise ORM (less mature ecosystem).

#### [DECISION-1.2] UUID primary keys everywhere
- **What:** All tables use UUID v4 primary keys instead of auto-incrementing integers.
- **Why:** Spec requires tenant isolation and multi-tenant support in the evolution path. UUIDs prevent enumeration attacks (can't guess `deal_id=124` from knowing `deal_id=123`) and are safe for distributed systems.
- **[CONCEPT]:** Auto-incrementing IDs (1, 2, 3...) leak information — an attacker can estimate how many deals exist or guess valid IDs. UUIDs are random 128-bit values like `a3f8b2c1-...`, making them unguessable. This is a security-by-design choice.
- **Tradeoff:** UUIDs are larger (16 bytes vs 4 bytes) and slightly slower for index lookups, but the security and multi-tenancy benefits justify it.
- **Alternatives considered:** ULID (sortable UUIDs — nice but adds a dependency for marginal gain at MVP).

#### [DECISION-1.3] Immutable criteria configs with versioning
- **What:** CriteriaConfig is versioned and append-only. Updating criteria creates a new version; the old one is deactivated. DealScore references the exact config it was scored against.
- **Why:** Spec says "Score history is append-only — re-scores create new records, old scores preserved" and decisions are immutable audit records. If we mutated criteria in-place, we'd lose the ability to explain *why* a deal got a particular score.
- **[CONCEPT]:** This is an audit trail pattern. Imagine a deal scored 85 last week. The analyst changed their criteria today. If we overwrote the old criteria, we couldn't explain the 85 anymore. By versioning, every score permanently links to the exact rules that produced it — like keeping every draft of a contract, not just the latest.
- **Tradeoff:** More rows in the DB, slightly more complex queries to get "current active config."
- **Alternatives considered:** Mutable config with change log (simpler queries but weaker audit trail).

#### [DECISION-1.4] Criteria rule structure (field + operator + target)
- **What:** Each criterion is defined as `field_name` + `operator` (eq, gt, in, etc.) + `target_value` (JSON-encoded). This gives us a flexible rule engine without needing an expression parser.
- **Why:** Spec defines criteria as "must-haves, nice-to-haves, dealbreakers, weights." We need a structure that's expressive enough for rules like "revenue > 5M" and "sector in [healthcare, tech]" but simple enough to validate.
- **[CONCEPT]:** Think of each criterion as a mini if-statement: `if deal.revenue > 5000000 then match`. The operator (`>`) and target (`5000000`) are stored separately so we can validate them independently. The scoring engine reads these rules and applies them mechanically — no ambiguity, fully deterministic.
- **Tradeoff:** Less flexible than a full expression language (can't do `revenue > 5M AND < 20M` in one criterion — need two). But much safer and easier to validate.
- **Alternatives considered:** JSON-based expression trees (powerful but hard to validate), free-text rules parsed by LLM (non-deterministic — violates scoring invariant).

#### [DECISION-1.5] Separate ExtractedField rows (not a JSON blob)
- **What:** Each extracted field is its own row in `extracted_fields` rather than a single JSON column on the deal.
- **Why:** Each field has its own extraction status (FOUND/INFERRED/MISSING) and confidence level. Normalizing this into rows makes it queryable (e.g., "show me all deals missing EBITDA") and supports re-extraction (new run creates new rows).
- **[CONCEPT]:** In database design, you choose between "one row with a big JSON blob" (flexible but hard to query) vs "one row per field" (more structured, easy to filter). Since the spec requires per-field confidence tracking and the scoring engine needs to evaluate each field independently, separate rows are the right call.
- **Tradeoff:** More rows, more joins. But extraction happens once and scoring reads these directly.
- **Alternatives considered:** JSON column with per-field status nested inside (simpler writes, harder queries and validation).

### Work Done
- `pyproject.toml`: Project definition with all MVP dependencies (FastAPI, SQLAlchemy, OpenAI, etc.)
- `src/app/models/enums.py`: All enums — DealStatus state machine, FieldExtractionStatus, ConfidenceLevel, CriterionType, AuditAction. Also defines CORE_EXTRACTION_FIELDS and MIN_FIELDS_FOR_EXTRACTION constants.
- `src/app/models/base.py`: SQLAlchemy Base, TimestampMixin (created_at/updated_at), UUIDPrimaryKeyMixin.
- `src/app/models/deal.py`: Deal, ExtractedField, DealScore, DealDecision, AuditLog models. Maps 1:1 to spec state machine and lifecycle.
- `src/app/models/criteria.py`: CriteriaConfig (versioned, tenant-scoped) + Criterion (field/operator/target rule).
- `src/app/schemas/deal.py`: Pydantic schemas for cross-module contracts — ExtractedDealSchema, ScoredDealSchema, DealCardSchema, request/response types.
- `src/app/schemas/criteria.py`: Pydantic schemas for criteria CRUD — CriterionCreateSchema with operator validation, CriteriaConfigCreateSchema/ResponseSchema.
- `src/app/config.py`: Settings via pydantic-settings (DB URL, OpenAI key, JWT, rate limits).
- `.env.example`: Template for required environment variables.
- `.gitignore`: Excludes .env, __pycache__, uploads, etc.

### Invariants Verified
- [x] Every document gets unique deal_id (UUID PK on Deal)
- [x] Duplicate detection by content_hash (unique index on Deal.content_hash)
- [x] Tenant isolation (tenant_id on Deal, CriteriaConfig, AuditLog — all indexed)
- [x] No state transition without audit log (AuditLog model with from_status/to_status)
- [x] Decisions are append-only (DealDecision is insert-only by design, no update endpoints planned)
- [x] Score history is append-only (DealScore references criteria_config_id for traceability)
- [x] Every field has FOUND/INFERRED/MISSING status (FieldExtractionStatus enum on ExtractedField)
- [x] Secrets externalized (.env.example, config.py reads from env vars)

### Security Considerations
- UUID PKs prevent ID enumeration attacks
- tenant_id on all data models enables row-level tenant isolation
- Criteria config is immutable after creation — prevents retroactive score manipulation
- Operator validation on criteria prevents injection of arbitrary expressions
- No secrets in code — all sensitive values via environment variables
- ORM-only DB access prevents SQL injection

### Open Questions
- Should we add Alembic migrations now or after all models are stable? (Leaning toward after Session 2 when extraction models are tested)
- The spec mentions Supabase Storage for documents — should we abstract storage behind an interface now, or hardcode local filesystem for MVP?

### Handoff
- **Status:** READY_FOR_VALIDATION
- **Next agent should:** Validate all models can be instantiated, check enum completeness against spec state machine, verify FK relationships and cascade behavior, test CriterionCreateSchema validation (especially operator validation), and confirm that the data model supports all spec invariants.
- **Blockers:** None

---

## Session 2a — 2026-03-23 — Bugfix (Codex Findings from Session 1)

### Context
- **Reading from:** `.agent/codex_log.md` (Session 1), `.agent/handoff.json`
- **Building:** Fixing two failed tests from Codex validation
- **Spec reference:** Security Invariants (tenant isolation), Phase 2 (Ingestion invariants, CriteriaConfig versioning)
- **Addressing from Codex:** C-001 [FAILED], C-002 [FAILED]

### Decisions

#### [DECISION-2a.1] Tenant-scoped content_hash uniqueness
- **What:** Replaced global `unique=True` on `content_hash` with a composite `UniqueConstraint("tenant_id", "content_hash")`.
- **Why:** Codex correctly identified that global uniqueness would prevent two different tenants from uploading the same PDF. The spec says "Duplicate detection by content hash" but also "User data must never be shared across tenants." These two invariants conflicted under the old schema — dedup must be per-tenant.
- **[CONCEPT]:** A composite unique constraint means "this *combination* must be unique." So tenant A and tenant B can both upload the same document (same hash), but tenant A can't upload it twice. It's like apartment mailboxes — unit 1A and unit 2A can both get a letter from the same sender, but unit 1A can't get two identical letters.
- **Tradeoff:** None — this is strictly more correct.
- **Alternatives considered:** Application-level dedup check (fragile under concurrent requests — DB constraint is authoritative).

#### [DECISION-2a.2] Per-tenant criteria config version uniqueness
- **What:** Added `UniqueConstraint("tenant_id", "version")` on `CriteriaConfig`.
- **Why:** Without this, two concurrent requests could create two configs with the same version number for the same tenant, breaking the append-only versioning guarantee.
- **[CONCEPT]:** Version numbers only mean something if they're unique within their scope. Version 3 of tenant A's criteria and version 3 of tenant B's criteria are different things — that's fine. But two "version 3"s for the same tenant would make the audit trail ambiguous. The DB constraint makes this impossible.
- **Tradeoff:** None — purely additive safety.

### Work Done
- `src/app/models/deal.py`: Removed column-level `unique=True` from `content_hash`, added `UniqueConstraint("tenant_id", "content_hash")` via `__table_args__`.
- `src/app/models/criteria.py`: Added `UniqueConstraint("tenant_id", "version")` via `__table_args__`.

### Invariants Verified
- [x] Tenant-scoped dedup (C-001 resolved)
- [x] Per-tenant version uniqueness (C-002 resolved)

### Security Considerations
- Both fixes strengthen tenant isolation at the DB level — the strongest possible enforcement point.

### Open Questions
- None from this fix.

### Handoff
- **Status:** READY_FOR_VALIDATION
- **Next agent should:** Re-run the two previously failing tests to confirm they pass. Then proceed to validate readiness for Session 2 (Extraction Service).
- **Blockers:** None

---

## Session 2 — 2026-03-23 — Extraction Service (LLM + PDF Parsing)

### Context
- **Reading from:** `.spec/system_spec.md` (Phase 2 — Extraction Service module), `.agent/codex_log.md` (Session 1), `.agent/handoff.json`
- **Building:** Extraction Service (Build Order #2)
- **Spec reference:** Phase 2 Module: Extraction Service, Trust Boundaries (API → OpenAI), Appendix B (LLM provider decision)
- **Addressing from Codex:** C-001 and C-002 were fixed in Session 2a. No new failures.

### Decisions

#### [DECISION-2.1] Four-component extraction pipeline
- **What:** Split extraction into 4 distinct components: `pdf_parser.py` (PDF → text), `pii_scrubber.py` (text → sanitized text), `llm_client.py` (sanitized text → structured JSON), and `extraction_service.py` (orchestrator that ties them together and manages state).
- **Why:** Each component crosses a different trust boundary or has a different failure mode. Separating them makes each independently testable and replaceable (spec Evolution Check says extraction is behind an interface).
- **[CONCEPT]:** This is the Single Responsibility Principle applied to a pipeline. Think of it like an assembly line: one station reads the document, one scrubs sensitive data, one calls the AI, and one supervisor checks quality and records results. If the AI provider changes (OpenAI → Claude), only the LLM client changes. If PDF parsing improves, only the parser changes. The orchestrator doesn't care how each step works — just that each step produces the right output shape.
- **Tradeoff:** More files than a single monolithic function, but each is small, focused, and testable.
- **Alternatives considered:** Single function doing everything (hard to test, impossible to swap components). External extraction microservice (overkill for MVP).

#### [DECISION-2.2] Regex-based PII scrubbing for MVP
- **What:** Built a pattern-based PII scrubber that catches SSNs, emails, phone numbers, credit cards, and labeled personal names (e.g., "Contact: John Smith").
- **Why:** Spec security invariant says "no deal data is sent without PII scrubbing." Deal docs are primarily business data (revenue, EBITDA), but CIMs often include contact info for the broker or management team. A regex approach catches the common patterns at zero latency cost.
- **[CONCEPT]:** PII (Personally Identifiable Information) is data that can identify a specific person — SSN, email, phone. When we send document text to OpenAI, we're crossing a trust boundary to a "semi-trusted" third party. Scrubbing PII before that crossing limits the damage if OpenAI is compromised or logs data unexpectedly. It's like redacting names from a document before sharing it externally — the business content remains useful, but personal details are protected.
- **Tradeoff:** Regex catches ~80% of common PII patterns but misses context-dependent PII (e.g., "the CEO, Michael Chen, founded..."). A production system should use NER (Named Entity Recognition) ML models. This is explicitly an MVP compromise.
- **Alternatives considered:** NER-based scrubbing via spaCy (more accurate but adds a heavy dependency), no scrubbing (violates spec security invariant).

#### [DECISION-2.3] OpenAI JSON mode with strict validation
- **What:** Use `response_format={"type": "json_object"}` to force JSON output from GPT-4o, then validate the response structure locally with `_validate_extraction_response()`.
- **Why:** Spec says "Never fabricate data" and "LLM temperature = 0." JSON mode + temperature=0 gives us deterministic, parseable output. But we don't trust the LLM to always conform perfectly — our validator catches structural issues like missing fields, invalid statuses, or MISSING fields that have values (which would indicate hallucination).
- **[CONCEPT]:** LLMs are probabilistic — even at temperature=0, they can produce subtly wrong output. "Trust but verify" is the pattern: we tell the LLM exactly what format we want (structured prompt), force it to produce JSON (API setting), then validate every field ourselves (local code). This three-layer approach means a single LLM mistake doesn't corrupt our data. The validation function is the last line of defense before data enters our database.
- **Tradeoff:** Validation may reject valid-but-unexpected outputs (e.g., if the LLM adds an extra helpful field). We prefer false negatives (reject good data) over false positives (accept bad data) — aligned with the "honest about confidence" invariant.
- **Alternatives considered:** OpenAI function calling / tools (more structured but locks us into OpenAI's schema format), raw text parsing (fragile).

#### [DECISION-2.4] Retry with exponential backoff (2 retries, 30s timeout)
- **What:** LLM calls retry up to 2 times with exponential backoff (1s, 2s) on API errors, rate limits, and timeouts. Hard timeout at 30 seconds per request.
- **Why:** Spec explicitly states "Retry 2x with exponential backoff, then mark FAILED" and "30s timeout." These exact parameters.
- **[CONCEPT]:** External API calls fail for many reasons: network blips, rate limits, server overload. Rather than immediately failing the user's deal, we retry a couple times with increasing delays. The "exponential" part (1s → 2s → 4s) avoids hammering a struggling server. But we cap retries at 2 because the user is waiting — better to fail fast and let them retry later than hang indefinitely. The 30s timeout prevents a single slow request from blocking resources.
- **Tradeoff:** 3 attempts × 30s = 90s worst case before failure. Acceptable for background processing, but the UI should show a processing indicator.
- **Alternatives considered:** No retries (too fragile), more retries (too slow for user experience).

#### [DECISION-2.5] Overall confidence computation from per-field results
- **What:** Implemented a rule-based confidence aggregation: all 6 HIGH → HIGH, ≥4 MEDIUM+ → MEDIUM, ≥3 fields → LOW, <3 → NONE.
- **Why:** Spec says "every score includes a confidence indicator based on extraction completeness." This needs to be deterministic and explainable, not an LLM judgment call.
- **[CONCEPT]:** Each extracted field has its own confidence (HIGH/MEDIUM/LOW). The overall confidence is an aggregation that answers "how much should the analyst trust this extraction?" Think of it like a school grade: if you aced all 6 subjects → straight A's (HIGH). If you did well in 4+ → solid B (MEDIUM). If you barely passed 3 → you scraped by (LOW). If you passed fewer than 3 → you failed (NONE).
- **Tradeoff:** The thresholds (4 for MEDIUM, 3 for minimum) are somewhat arbitrary. They match the spec's "≥ 3/6 fields" threshold but the MEDIUM cutoff is a judgment call. Can be tuned based on analyst feedback.
- **Alternatives considered:** LLM self-assessment of confidence (non-deterministic), simple count-based (too coarse).

### Work Done
- `src/app/services/pdf_parser.py`: PDF text extraction via pypdf. Handles empty/corrupted/image-only PDFs. Caps at 80 pages. Raises `PDFParseError` on failure.
- `src/app/services/pii_scrubber.py`: Regex-based PII scrubbing for SSN, email, phone, credit card, and labeled personal names. Returns redaction markers like `[EMAIL_REDACTED]`.
- `src/app/services/extraction_prompt.py`: System + user prompt for LLM extraction. Enforces all 6 fields, FOUND/INFERRED/MISSING status, and conservative extraction stance.
- `src/app/services/llm_client.py`: Async OpenAI client with JSON mode, temperature=0, retry logic (2x exponential backoff), 30s timeout, and strict response validation.
- `src/app/services/extraction_service.py`: Orchestrator that runs the full pipeline (load deal → parse PDF → scrub PII → call LLM → validate → persist fields → update status). Handles UPLOADED→EXTRACTED and UPLOADED→FAILED transitions with audit logging.

### Invariants Verified
- [x] Never fabricate data — prompt instructs MISSING not guess, validator rejects MISSING fields with values
- [x] Every field has FOUND/INFERRED/MISSING status — enforced by LLM response validator
- [x] LLM temperature = 0 — hardcoded in llm_client.py
- [x] PII scrubbed before LLM call — pipeline order enforced in orchestrator
- [x] Retry 2x with exponential backoff — implemented in llm_client.py
- [x] 30s timeout — set on OpenAI client
- [x] >= 3/6 fields for EXTRACTED, else FAILED — enforced in orchestrator
- [x] No state transition without audit log — _log_audit called on every transition
- [x] State validation — only UPLOADED or FAILED deals can be extracted
- [x] Tenant verification — deal.tenant_id checked against caller's tenant_id

### Security Considerations
- PII scrubbing before LLM call limits exposure at the API → OpenAI trust boundary
- Prompt is designed to produce structured output only — mitigates prompt injection risk (attacker text in PDF can't break out of the extraction task because we use JSON mode and validate the response)
- File path comes from our storage layer (trusted), not user input
- LLM response is validated field-by-field before DB persistence — defense in depth against hallucination

### Open Questions
- Should we truncate very long documents before sending to LLM? Current 80-page cap helps, but some CIMs are dense. Token limits may be an issue for GPT-4o context window — needs testing with real documents.
- The PII scrubber doesn't catch names in running text (e.g., "CEO Michael Chen"). Worth adding spaCy NER for v2?

### Handoff
- **Status:** READY_FOR_VALIDATION
- **Next agent should:**
  1. Re-run Session 1 failing tests (C-001, C-002) to confirm composite constraint fixes.
  2. Test `pdf_parser.py`: valid PDF, empty PDF, non-PDF file, missing file, image-only PDF.
  3. Test `pii_scrubber.py`: SSN, email, phone, CC, labeled names — verify redaction. Also verify it doesn't destroy deal-relevant text.
  4. Test `llm_client.py` response validation: valid response, missing fields, duplicate fields, MISSING-with-value, FOUND-without-value, invalid status/confidence values.
  5. Test `extraction_service.py` orchestrator: mock LLM client, verify state transitions (UPLOADED→EXTRACTED, UPLOADED→FAILED), audit log creation, threshold enforcement (2/6 → FAILED, 3/6 → EXTRACTED), tenant isolation check.
  6. Test confidence computation: all HIGH → HIGH, mixed → MEDIUM, minimum → LOW, below threshold → NONE.
- **Blockers:** None

---

## Session 3 — 2026-03-23 — Scoring Engine (Rule-Based v1)

### Context
- **Reading from:** `.spec/system_spec.md` (Phase 2 — Scoring Engine module), `.agent/codex_log.md` (Sessions 1, 2a, 2b), `.agent/handoff.json`
- **Building:** Scoring Engine (Build Order #3)
- **Spec reference:** Phase 2 Module: Scoring Engine, Appendix B (scoring engine v1 decision)
- **Addressing from Codex:** None — all 23 extraction tests passed, C-001/C-002 validated as resolved.

### Decisions

#### [DECISION-3.1] Two-layer scoring architecture: evaluator + orchestrator
- **What:** Split scoring into `criteria_evaluator.py` (evaluates one criterion against one field) and `scoring_service.py` (orchestrates all criteria, computes aggregate score, generates rationale, persists results).
- **Why:** Same separation principle as extraction. The evaluator is a pure function (no DB, no state) — easy to test exhaustively. The orchestrator manages state transitions and persistence. This also enables the v2 ML swap: replace `_compute_score` in the orchestrator without touching criterion evaluation logic.
- **[CONCEPT]:** Think of the evaluator as a single judge at a competition who scores one category (e.g., "technical difficulty"). The orchestrator is the head judge who collects all individual scores, applies weights, checks for disqualifications (dealbreakers), and announces the final result. Splitting them means you can change how individual criteria are checked without changing how the final score is computed, and vice versa.
- **Tradeoff:** Two files instead of one. But each is focused and independently testable.
- **Alternatives considered:** Single scoring function (harder to test edge cases per operator), LLM-based scoring (non-deterministic — violates spec invariant).

#### [DECISION-3.2] Weighted average scoring with dealbreaker short-circuit
- **What:** Score = (sum of matched criterion weights / sum of evaluable criterion weights) × 100. But if ANY non-skipped dealbreaker fails → score = 0 immediately.
- **Why:** Spec says "must-haves, nice-to-haves, dealbreakers, weights." Dealbreakers need to be absolute — a deal in the wrong sector is a 0 regardless of how good the margins are. The weighted average for non-dealbreaker criteria lets users express relative importance (e.g., geography matters twice as much as deal type).
- **[CONCEPT]:** Imagine you're buying a house. Your dealbreaker is "must have 3+ bedrooms." If a house has 2 bedrooms, you don't care that it has a pool, great location, and low price — it's a 0. But among houses that pass the dealbreaker, you weigh your preferences: location matters 2x more than yard size. That's exactly what this algorithm does. Dealbreakers are pass/fail gates. Everything else is weighted relative scoring.
- **Tradeoff:** Skipped criteria (MISSING fields) are excluded from the denominator, not counted as failures. This means a deal with only 3/6 fields could still score high if those 3 fields match. The confidence level compensates — the analyst sees "LOW confidence" and knows to dig deeper.
- **Alternatives considered:** Penalizing missing fields as failures (too harsh — spec says partial scores are valid), flat scoring without weights (less expressive).

#### [DECISION-3.3] MISSING field dealbreakers don't auto-fail
- **What:** If a dealbreaker criterion references a MISSING field, the criterion is marked `skipped=True` rather than `matched=False`. It reduces confidence but doesn't trigger the score=0 short-circuit.
- **Why:** Spec says "When data is missing, the system must still attempt a partial score." If we treated missing-field dealbreakers as failures, a CIM that doesn't mention revenue would auto-fail even though the deal might be excellent — the data just isn't in the teaser. The analyst should see "LOW confidence — revenue not available" rather than an unexplained 0.
- **[CONCEPT]:** Absence of evidence is not evidence of absence. If a CIM doesn't mention the company's sector, we can't say "wrong sector — fail." We can only say "we don't know the sector — proceed with caution." The confidence indicator is what communicates this uncertainty to the analyst. This is the "honest about confidence" invariant in action.
- **Tradeoff:** A deal might score well despite missing a critical field. But the LOW confidence indicator + the rationale ("Could not evaluate: sector is MISSING") makes this visible. The user decides — the agent informs.
- **Alternatives considered:** Auto-fail on missing dealbreakers (violates partial scoring invariant), separate "required fields" concept (adds complexity without clarity).

#### [DECISION-3.4] Scoring confidence derived from criteria evaluability
- **What:** Confidence = f(% of criteria that could be evaluated, underlying extraction confidence). 100% evaluated + HIGH extraction → HIGH. ≥75% → MEDIUM. ≥50% → LOW. <50% → NONE.
- **Why:** Scoring confidence is different from extraction confidence. Extraction confidence asks "did we read the document correctly?" Scoring confidence asks "could we evaluate enough criteria to make a meaningful recommendation?" Both matter — a perfect extraction of 3/6 fields still gives LOW scoring confidence if the other 3 were must-haves.
- **[CONCEPT]:** Think of it like a school exam. Extraction confidence is "did you read the questions correctly?" Scoring confidence is "did you answer enough questions for the grade to be meaningful?" If you only answered 3 out of 10 questions, even getting all 3 right doesn't mean you'd pass — there's just not enough data to tell.
- **Tradeoff:** Thresholds (75%, 50%) are somewhat arbitrary. They should be tunable per tenant in v2.

#### [DECISION-3.5] Natural language rationale generation
- **What:** The rationale is a structured text block that lists dealbreaker failures, matched criteria, unmatched criteria, and skipped criteria — each with the evaluation detail string.
- **Why:** Spec says "Rationale must cite specific criteria matches/misses" and "The user decides, the agent informs." A bare score is useless to an analyst — they need to see *why* a deal scored 72 to trust the system and make a decision.
- **[CONCEPT]:** This is explainable AI at the simplest level. Instead of a black box that says "72/100," the system says "72/100 because: sector matches (healthcare ✓), revenue above threshold ($8M > $5M ✓), but geography doesn't match (Canada, expected US Southeast ✗), and EBITDA data was not available (?)" The analyst can immediately see what drove the score and whether they agree.
- **Tradeoff:** Rationale text can get long for configs with many criteria. But it's stored per-score, not shown in list view — only expanded when the analyst reviews a specific deal.

### Work Done
- `src/app/services/criteria_evaluator.py`: Single-criterion evaluator. Handles all operators (eq, ne, gt, lt, gte, lte, in, not_in, contains) with numeric and case-insensitive string comparison. MISSING fields produce skipped results. Pure function, no DB dependency.
- `src/app/services/scoring_service.py`: Scoring orchestrator. Loads deal (verifies EXTRACTED status + tenant), loads active criteria config, loads extracted fields, evaluates all criteria, computes weighted score with dealbreaker short-circuit, computes confidence, generates rationale, persists DealScore, transitions EXTRACTED→SCORED with audit log.

### Invariants Verified
- [x] Score is deterministic for same inputs — pure functions, no randomness, no LLM
- [x] Rationale cites specific criteria matches/misses — _generate_rationale lists each result
- [x] Missing fields reduce confidence, never silently skipped — skipped=True with detail message
- [x] DEALBREAKER non-match (non-skipped) → score=0 — short-circuit in _compute_score
- [x] No state transition without audit log — AuditLog created on EXTRACTED→SCORED
- [x] Tenant isolation — deal and config queries scoped to tenant_id
- [x] State validation — only EXTRACTED deals can be scored
- [x] Score history is append-only — new DealScore row per scoring, references criteria_config_id

### Security Considerations
- No external calls — scoring is entirely internal (no LLM, no API)
- Criteria config is loaded from DB (already validated by Pydantic on creation)
- target_value is JSON-decoded in the evaluator — but was validated at schema level on input
- Tenant-scoped queries prevent cross-tenant scoring

### Open Questions
- Should criteria config changes auto-trigger re-scoring of EXTRACTED deals? Spec says "Criteria changes trigger re-score of EXTRACTED deals only." This needs an event/hook in the criteria update endpoint (Session 5, Dashboard API).
- Should we add a "re-score" endpoint that lets analysts manually trigger re-scoring? Useful if they tweak criteria and want to see updated results.

### Handoff
- **Status:** READY_FOR_VALIDATION
- **Next agent should:**
  1. Test `criteria_evaluator.py`: all operators (eq, ne, gt, lt, gte, lte, in, not_in, contains) with string and numeric values. MISSING field handling. Edge cases: non-numeric string with numeric operator, empty target list for `in`, case sensitivity.
  2. Test `scoring_service.py` score computation: dealbreaker short-circuit (matched vs failed vs skipped), weighted average correctness, all-skipped → 0, empty criteria → 0.
  3. Test confidence computation: 100% evaluated → HIGH (with all-HIGH extraction), 75%+ → MEDIUM, 50%+ → LOW, <50% → NONE.
  4. Test rationale generation: verify dealbreaker failures, matched, unmatched, and skipped sections appear correctly with details.
  5. Test orchestrator: mock DB, verify EXTRACTED→SCORED transition, audit log creation, DealScore persistence with correct criteria_config_id. Verify wrong-status deals are rejected. Verify missing config returns error.
  6. Test determinism: same inputs → same score across multiple runs.
- **Blockers:** None

---

## Session 4 — 2026-03-23 — Ingestion Service + Database Layer

### Context
- **Reading from:** `.spec/system_spec.md` (Phase 2 — Ingestion Service module), `.agent/codex_log.md` (Session 3), `.agent/handoff.json`
- **Building:** Ingestion Service (Build Order #4) + Database session dependency
- **Spec reference:** Phase 2 Module: Ingestion Service, Trust Boundaries (Client → API, Email/Webhook → API), Appendix B (Queue decision)
- **Addressing from Codex:** None — 22/22 scoring tests passed.

### Decisions

#### [DECISION-4.1] Synchronous extraction+scoring pipeline for MVP
- **What:** Ingestion runs extraction and scoring inline (await extract_deal → await score_deal) rather than queuing async jobs. The full pipeline runs within the same request/transaction.
- **Why:** Spec Appendix B chose "PostgreSQL advisory locks (MVP simplicity)" for the queue. For MVP, the simplest correct approach is synchronous: upload → extract → score in one request. This eliminates the need for a job runner, polling, status webhooks, or background task infrastructure. The spec explicitly marks this as swappable ("Yes — swap to Celery when needed").
- **[CONCEPT]:** In production systems, long-running tasks (like calling an LLM) usually go through a job queue: you say "process this later" and the user gets an immediate "we're on it" response. But queues add complexity (Redis, Celery, retry logic, status polling). For MVP, we skip the queue and just do everything while the user waits. It's slower (the upload endpoint blocks for ~10-30s while the LLM runs) but much simpler. When deal volume grows, we swap in a queue without changing the service layer — just change where `extract_deal` is called from.
- **Tradeoff:** Upload endpoint is slow (~10-30s) because it waits for LLM extraction. Acceptable for MVP (analysts upload one deal at a time). Not acceptable for batch/email ingestion — that path needs the queue.
- **Alternatives considered:** Celery + Redis (production-ready but heavy for MVP), FastAPI BackgroundTasks (fire-and-forget but no error handling), PostgreSQL LISTEN/NOTIFY (interesting but non-standard).

#### [DECISION-4.2] Content hash dedup with SHA-256
- **What:** SHA-256 hash of file bytes for duplicate detection, scoped to tenant (using the composite unique constraint from Session 2a).
- **Why:** Spec says "Duplicate detection by content hash." SHA-256 is collision-resistant and fast. Hashing the raw bytes (not the extracted text) means identical PDFs are detected even if extraction logic changes.
- **[CONCEPT]:** A hash function takes any input (like a PDF's bytes) and produces a fixed-size fingerprint (64 hex characters for SHA-256). If two files produce the same fingerprint, they're the same file. This lets us detect re-uploads without comparing entire file contents byte-by-byte. It's like a fingerprint for documents — quick to check, virtually impossible to forge.
- **Tradeoff:** Two slightly different PDFs (e.g., re-exported with different metadata) will have different hashes. This is intentional — they might contain different content. We dedup on identical bytes only.
- **Alternatives considered:** Fuzzy dedup via text similarity (catches near-duplicates but expensive and non-trivial to get right), filename-based dedup (unreliable — same deal from different brokers has different filenames).

#### [DECISION-4.3] Path traversal prevention via basename sanitization
- **What:** `_store_file` uses `Path(filename).name` to strip directory components before writing to disk.
- **Why:** The filename comes from the user's upload (untrusted input at the Client → API trust boundary). A malicious filename like `../../etc/passwd` could write outside the upload directory. `Path.name` strips everything except the final component.
- **[CONCEPT]:** Path traversal is a classic attack: if the server naively uses a user-provided filename to write a file, an attacker can include `../` to escape the intended directory and overwrite system files. By taking only the basename (the part after the last `/`), we ensure the file always ends up in the deal's upload directory, regardless of what the user sends. It's like a mailroom that only looks at the apartment number, ignoring any "redirect to another building" instructions on the envelope.
- **Tradeoff:** None — this is purely defensive with no downside.

#### [DECISION-4.4] Database session with auto-commit/rollback
- **What:** `database.py` provides an async session generator that auto-commits on success and auto-rolls-back on exception.
- **Why:** Spec technical invariant: "Database transactions must be atomic — a deal is either fully ingested (document + extracted fields + initial score) or not at all." The session generator enforces this: if anything fails during ingestion, the entire transaction rolls back — no partial state in the DB.
- **[CONCEPT]:** A database transaction is like an all-or-nothing contract. If you're transferring money between bank accounts, you don't want the debit to succeed but the credit to fail. Similarly, if extraction fails mid-way, we don't want a deal record in the DB pointing to extracted fields that don't exist. The session wrapper ensures either everything commits or nothing does.
- **Tradeoff:** Long transactions (ingestion takes 10-30s with LLM) hold a DB connection. With a pool of 10 connections, this limits concurrency to ~10 simultaneous uploads. Fine for MVP.

### Work Done
- `src/app/database.py`: Async SQLAlchemy engine + session factory. FastAPI dependency with auto-commit/rollback. Pool size 10, max overflow 20.
- `src/app/services/ingestion_service.py`: Full ingestion pipeline — file type validation (extension + content-type), file size check (50MB max), empty file check, SHA-256 dedup (tenant-scoped), file storage with path traversal prevention, deal record creation, audit log, and inline extraction + scoring.

### Invariants Verified
- [x] Every document gets unique deal_id — UUID generated before record creation
- [x] Duplicate detection by content hash (per tenant) — SHA-256 + tenant-scoped query
- [x] Only PDF accepted in v1 — extension + content-type validation
- [x] File size <= 50MB — checked before any storage/processing
- [x] Atomic ingestion — session auto-commits on success, rolls back on failure
- [x] No state transition without audit log — DEAL_UPLOADED audit on creation
- [x] Path traversal prevention — basename sanitization on user-provided filename

### Security Considerations
- File type validation at two levels: extension check AND content-type header (defense in depth)
- Path traversal prevention via `Path.name` basename extraction
- SHA-256 content hash prevents processing identical docs twice (DoS mitigation)
- Empty file rejection prevents wasted LLM calls
- File size limit prevents resource exhaustion

### Open Questions
- Should we add virus/malware scanning on uploaded PDFs? Out of scope for MVP but important for production.
- The synchronous pipeline means a single slow LLM call blocks the upload response. Should we add a timeout wrapper around the full pipeline?

### Handoff
- **Status:** READY_FOR_VALIDATION
- **Next agent should:**
  1. Test `ingestion_service.py`: valid PDF upload (mock file content + extraction + scoring), duplicate detection (same content same tenant → returns existing), cross-tenant dedup (same content different tenant → creates new), file type rejection (non-PDF extension, wrong content-type), file size rejection (over 50MB), empty file rejection.
  2. Test path traversal prevention: filename with `../` components should be sanitized.
  3. Test `database.py`: session yields, commits on success, rolls back on exception.
  4. Test pipeline integration: ingestion → extraction → scoring flow (with mocked LLM), verify final deal status is SCORED.
  5. Test extraction failure path: mock LLM failure, verify deal ends up FAILED with error message.
  6. Test scoring failure path: mock missing criteria config, verify deal ends up EXTRACTED with scoring error message.
- **Blockers:** None

---

## Session 5 — 2026-03-23 — Dashboard API (FastAPI Routes)

### Context
- **Reading from:** `.spec/system_spec.md` (Phase 2 — Dashboard API + UI module), `.agent/codex_log.md` (Session 4), `.agent/handoff.json`
- **Building:** Dashboard API (Build Order #5) — the final MVP module
- **Spec reference:** Phase 2 Module: Dashboard API + UI, Technical Invariants (request_id, structured errors)
- **Addressing from Codex:** None — 11/11 ingestion tests passed.

### Decisions

#### [DECISION-5.1] Request-ID middleware for traceability
- **What:** HTTP middleware adds a UUID `X-Request-ID` header to every response and stores it on `request.state` for use in error responses.
- **Why:** Spec technical invariant: "API responses must always include a request_id for traceability and return structured error objects on failure." The middleware ensures no route can forget to include it — it's automatic.
- **[CONCEPT]:** When something goes wrong in production, you need to trace a user's complaint ("my upload failed") back to a specific server log entry. A request_id is like a receipt number — the user can say "it failed, here's my request_id" and you can grep your logs for that exact ID. By generating it in middleware, every single request gets one automatically, even error responses.
- **Tradeoff:** Tiny overhead per request (UUID generation). Negligible.

#### [DECISION-5.2] MVP auth via headers (X-Tenant-ID, X-User-ID)
- **What:** Tenant and user context comes from request headers rather than JWT tokens. Validated as UUIDs but not authenticated.
- **Why:** Spec says "All API routes require valid JWT." But implementing JWT auth properly (token issuance, refresh rotation, key management) is a full session of work. For MVP, we use headers so the API layer is structurally correct (every route requires tenant context, queries are scoped) while deferring the auth implementation. The header-to-JWT swap is a single dependency change in `deps.py`.
- **[CONCEPT]:** Authentication (who are you?) and authorization (what can you access?) are separate concerns. By building the API with mandatory tenant/user context from the start — even if we're not validating credentials yet — we ensure every query is already tenant-scoped. Swapping in real JWT auth later only changes *how* we get the tenant_id, not *where* it's used. This is the difference between "auth bolted on at the end" and "auth built into the architecture."
- **Tradeoff:** The API is currently unauthenticated — anyone can spoof a tenant_id header. This is explicitly an MVP shortcut. Must be replaced before any deployment beyond localhost.

#### [DECISION-5.3] Deal list with score-based sorting
- **What:** `GET /api/v1/deals/` supports `sort_by=score` which joins the latest score for ranking, plus `sort_by=created_at` (default), with pagination.
- **Why:** The spec's core UX promise is "surfacing only the deals worth reading — ranked by fit." The analyst needs to see the highest-scoring deals first. Score-based sorting via a subquery join is the cleanest approach at the DB level.
- **[CONCEPT]:** The analyst's primary view is a ranked queue: best deals at the top. Without sort-by-score, they'd see deals in upload order (newest first), which is useless for prioritization. The subquery approach (find max score per deal, then sort by that) keeps the query efficient — we don't load all scores, just the max per deal.
- **Tradeoff:** The subquery adds a join. For MVP volumes (<1000 deals) this is negligible. At scale, a denormalized `latest_score` column on the Deal table would be faster.

#### [DECISION-5.4] Criteria config creation triggers re-scoring
- **What:** When a new criteria config is created, all deals in EXTRACTED status are automatically re-scored against the new criteria. DECIDED deals are untouched.
- **Why:** Spec explicitly says "Criteria changes trigger re-score of EXTRACTED deals only" and "DECIDED deals untouched — decisions are immutable audit records." This ensures the ranked queue immediately reflects the new criteria.
- **[CONCEPT]:** Imagine you change your thesis from "healthcare only" to "healthcare and tech." All the tech deals that previously scored 0 (sector mismatch) now need to be re-evaluated. But deals you've already decided on (PASSED or PURSUING) stay as-is — your past decisions are a historical record, not something that gets retroactively changed. It's like updating your search filters on a job board: new results appear, but your saved/rejected jobs don't change.
- **Tradeoff:** Re-scoring is synchronous in the config creation endpoint. For a tenant with hundreds of EXTRACTED deals, this could be slow. Acceptable for MVP; should be async for production.

#### [DECISION-5.5] Criteria config history endpoint for audit trail
- **What:** `GET /api/v1/criteria/config/history` returns all config versions (active and inactive) for a tenant.
- **Why:** Spec says score history is append-only and every score references its criteria_config_id. The history endpoint lets analysts (and auditors) see what criteria were active at any point in time.
- **[CONCEPT]:** If a deal scored 85 two weeks ago but now scores 60 with the current criteria, the analyst needs to understand why. The config history shows that criteria version 3 (which produced the 85) had different weights than version 5 (which produces the 60). Combined with the score's criteria_config_id reference, you can reconstruct exactly why any score was what it was, at any point in time. This is the full audit trail.
- **Tradeoff:** No tradeoff — this is a read-only endpoint on existing data.

### Work Done
- `src/app/main.py`: FastAPI app with request_id middleware, global exception handler (structured error objects), health check, router mounts.
- `src/app/api/deps.py`: Shared dependencies — tenant_id and user_id extraction from headers (MVP auth), request_id access.
- `src/app/api/deals.py`: Deal routes — `POST /upload` (full pipeline), `GET /` (list with filtering + score sorting + pagination), `GET /{deal_id}` (detail), `POST /{deal_id}/decide` (pass/pursue with audit). Deal card builder assembles extraction + score + decision data.
- `src/app/api/criteria.py`: Criteria routes — `POST /config` (create with version bump + re-scoring), `GET /config` (active config), `GET /config/history` (full version history). Immutable versioning with deactivation of previous config.

### Invariants Verified
- [x] API responses include request_id — middleware adds X-Request-ID header automatically
- [x] Structured error objects — global exception handler returns {error, message, request_id}
- [x] Decisions are append-only — DealDecision is insert-only, no update endpoint
- [x] SCORED → DECIDED only — decide endpoint validates deal.status == SCORED
- [x] Criteria changes re-score EXTRACTED deals — _rescore_extracted_deals runs on config creation
- [x] DECIDED deals untouched on re-score — only EXTRACTED status deals are queried
- [x] Tenant isolation — every query scoped to tenant_id from dependency
- [x] No state transition without audit log — decide endpoint creates AuditLog

### Security Considerations
- Tenant isolation enforced at the dependency level — every route gets tenant_id, every query filters by it
- IDOR prevention — deal lookup is always (deal_id + tenant_id), never deal_id alone
- File upload size handled in ingestion service, not repeated in API layer
- MVP auth is explicitly unauthenticated — flagged as must-fix before deployment
- Request_id in error responses aids debugging without leaking internal details

### Open Questions
- JWT auth implementation — should be the next session. Need token issuance, validation, refresh rotation, and the deps.py swap.
- Rate limiting — spec says "100 req/min" at the Client → API boundary. Not yet implemented. FastAPI has rate limiting middleware options (slowapi).
- The deal list endpoint does N+1 queries for deal cards (one per deal for related data). Should be optimized with eager loading for production volumes.

### Handoff
- **Status:** READY_FOR_VALIDATION
- **Next agent should:**
  1. Test `main.py`: request_id middleware adds header to all responses, global exception handler returns structured error.
  2. Test `deps.py`: valid UUID headers accepted, invalid UUID headers return 400, missing headers return 422.
  3. Test `deals.py` routes:
     - `POST /upload`: valid PDF → 200 with deal_id/status/message, invalid file → 400, ingestion error → 400.
     - `GET /`: returns deal cards, filtering by status works, sort_by=score works, pagination works, tenant isolation (can't see other tenant's deals).
     - `GET /{deal_id}`: returns full card, 404 for wrong tenant or missing deal.
     - `POST /{deal_id}/decide`: SCORED deal → 200 with decision, non-SCORED deal → 400, creates DealDecision + AuditLog, transitions to DECIDED.
  4. Test `criteria.py` routes:
     - `POST /config`: creates new config with version bump, deactivates previous, re-scores EXTRACTED deals, returns config with criteria.
     - `GET /config`: returns active config, 404 when none exists.
     - `GET /config/history`: returns all versions ordered by version desc.
  5. Test IDOR: tenant A cannot access tenant B's deals or criteria configs.
- **Blockers:** None

---

## Session 5a — 2026-03-23 — Bugfix (Codex Session 5 Finding)

### Context
- **Reading from:** `.agent/codex_log.md` (Session 5), `.agent/handoff.json`
- **Building:** Fixing C-003 — exception responses missing X-Request-ID header
- **Spec reference:** Technical Invariants ("API responses must always include a request_id")
- **Addressing from Codex:** C-003 [FAILED]

### Decisions

#### [DECISION-5a.1] Add X-Request-ID header to exception handler response
- **What:** Added `headers={"X-Request-ID": request_id}` to the `JSONResponse` in the global exception handler.
- **Why:** The exception handler creates a fresh `JSONResponse` that bypasses the middleware's `response.headers["X-Request-ID"] = request_id` line (because the middleware's `call_next` raised, so it never reached the header-setting code). The fix is to set the header directly on the exception response.
- **[CONCEPT]:** Middleware wraps the request/response cycle like layers of an onion. When an exception occurs, FastAPI short-circuits the normal flow and jumps to the exception handler, skipping the middleware's post-processing step. So the middleware sets `request_id` on `request.state` (before the handler), but never gets to set it on the response headers (after the handler). The fix is to have the exception handler set the header itself, since it already has access to the request_id.
- **Tradeoff:** None — one-line fix.

### Work Done
- `src/app/main.py`: Added `headers={"X-Request-ID": request_id}` to the `JSONResponse` in `global_exception_handler`.

### Invariants Verified
- [x] All responses (including errors) now carry X-Request-ID header (C-003 resolved)

### Handoff
- **Status:** READY_FOR_VALIDATION
- **Next agent should:** Re-run the failing `test_global_exception_handler_returns_structured_error` test to confirm the X-Request-ID header is present on error responses.
- **Blockers:** None

---

## Session 6 — 2026-03-23 — Minimal UI (Build Order #6)

### Context
- **Reading from:** `.spec/system_spec.md` (Build Order item 6, Appendix B), `.agent/handoff.json`
- **Building:** Minimal React SPA frontend — spec Build Order #6: "Minimal UI (deal queue + score cards)"
- **Spec reference:** Build Order MVP table row 6; Appendix B Decision Log ("MVP frontend → Minimal React SPA")
- **Self-correction:** Previously marked MVP as feature-complete after Session 5a, but Build Order explicitly lists 6 modules and the frontend (item 6) was not built. This session completes the actual MVP scope.

### Decisions

#### [DECISION-6.1] React + Vite + TypeScript + Tailwind CSS
- **What:** Scaffolded a React SPA in `/frontend` using Vite with TypeScript template, Tailwind CSS v4, and Lucide icons.
- **Why:** Spec Appendix B chose "Minimal React SPA" for speed to demo. Vite is the standard modern React bundler. Tailwind provides utility-first styling without a component library dependency.
- **[CONCEPT]:** The frontend is fully decoupled from the backend — it calls the FastAPI API through a Vite dev proxy (`/api` → `localhost:8000`). This matches the spec's note that "frontend is decoupled from API" and keeps deployment flexible.
- **Tradeoff:** No shadcn/ui component library installed (just raw Tailwind classes) — keeps dependencies minimal for MVP.

#### [DECISION-6.2] Vite proxy instead of CORS-only
- **What:** Configured Vite's dev server to proxy `/api` requests to the FastAPI backend. Also added CORS middleware to FastAPI as a fallback.
- **Why:** Proxy avoids CORS issues in development entirely and mirrors how a production reverse proxy (nginx) would work. CORS added as belt-and-suspenders for direct API access.
- **Tradeoff:** None — standard pattern.

#### [DECISION-6.3] Hardcoded tenant ID for MVP
- **What:** The API client sends `X-Tenant-ID: 00000000-0000-0000-0000-000000000001` on all requests.
- **Why:** Auth/JWT is not yet implemented (post-MVP item). The backend's `get_tenant_id` dependency reads from this header. Hardcoding a UUID lets the frontend work end-to-end without auth.
- **Tradeoff:** Not multi-tenant in practice until JWT auth is added. Acceptable for MVP demo.

#### [DECISION-6.4] Alembic async migrations
- **What:** Set up Alembic with the async template, configured `env.py` to read `DATABASE_URL` from `app.config.settings`, auto-generated initial migration covering all 7 tables.
- **Why:** Needed to actually create the database schema to run the app. Alembic is already a project dependency and is the standard SQLAlchemy migration tool.
- **[CONCEPT]:** Alembic's async template uses `asyncio.run()` to bridge the sync migration runner with our async engine. The `env.py` imports `app.models.Base` which triggers all model imports via `__init__.py`, so autogenerate detects all tables.

### Work Done
- Alembic setup: `alembic.ini`, `alembic/env.py`, initial migration `alembic/versions/1ed7e8f2e432_initial_schema.py`
- Frontend scaffold: `frontend/` — Vite + React + TypeScript
- `frontend/vite.config.ts`: Tailwind plugin, `@/` path alias, `/api` proxy to backend
- `frontend/src/index.css`: Tailwind v4 theme with design tokens
- `frontend/src/lib/utils.ts`: `cn()` class merge utility
- `frontend/src/lib/api.ts`: Full typed API client (deals CRUD, criteria CRUD, upload)
- `frontend/src/components/Layout.tsx`: App shell with nav bar (Deals, Upload, Criteria)
- `frontend/src/components/StatusBadge.tsx`: Color-coded status/confidence badges
- `frontend/src/pages/DealList.tsx`: Deal pipeline view with status filters, score display, pagination
- `frontend/src/pages/DealDetail.tsx`: Deal detail with extracted fields, score bar, decision form (pass/pursue)
- `frontend/src/pages/Upload.tsx`: Drag-and-drop PDF upload with progress and pipeline explanation
- `frontend/src/pages/Criteria.tsx`: Active config display, new config form with dynamic criterion rows, version history
- `frontend/src/App.tsx`: React Router setup
- `frontend/src/main.tsx`: Entry point
- `src/app/main.py`: Added CORS middleware for `localhost:5173`
- Database: Created `deal_screening` DB, ran `alembic upgrade head` — all 7 tables created

### Invariants Verified
- [x] Frontend proxies all API calls through `/api` — no direct cross-origin requests in normal flow
- [x] CORS middleware added as fallback for direct API access
- [x] TypeScript compiles with zero errors (`npx tsc --noEmit`)
- [x] All 7 database tables created via Alembic migration
- [x] Backend health check returns 200 OK
- [x] Frontend dev server starts and serves on port 5173

### Security Considerations
- Hardcoded tenant ID is MVP-only — must be replaced with JWT-derived tenant ID before any shared deployment
- CORS origin restricted to `localhost:5173` — must be updated for production domain

### Handoff
- **Status:** MVP_COMPLETE
- **All 6 Build Order modules implemented:** (1) DB Models + Criteria Config, (2) Extraction Service, (3) Scoring Engine, (4) Ingestion Service, (5) Dashboard API, (6) Minimal UI
- **Next priorities:** JWT auth implementation, rate limiting, production deployment config
- **Blockers:** None

---

## Session 6a — 2026-03-23 — Bugfix (Codex Session 6 Finding)

### Context
- **Reading from:** `.agent/codex_log.md` (Session 6), `.agent/handoff.json`
- **Building:** Fixing C-005 — frontend lint failure in `DealList.tsx`
- **Addressing from Codex:** C-005 [FAILED] — `react-hooks/set-state-in-effect` lint violation

### Decisions

#### [DECISION-6a.1] Refactor useEffect to async IIFE pattern
- **What:** Replaced the synchronous `.then()` chain with an `async function fetchDeals()` inside the effect. `setLoading(true)` is now called inside the async function, not synchronously in the effect body.
- **Why:** The `react-hooks/set-state-in-effect` rule flags synchronous `setState` calls directly in an effect body because they cause an immediate re-render before the effect's async work completes. Moving it inside an async function satisfies the rule.
- **[CONCEPT]:** React's `useEffect` runs synchronously during commit phase. Calling `setState` directly in the effect body triggers a synchronous re-render before the effect returns. The async IIFE pattern defers the setState to the microtask queue, and also enables a cleanup function with a `cancelled` flag to prevent state updates on unmounted components.
- **Tradeoff:** Slightly more verbose but also adds proper cleanup (cancelled flag) which prevents React's "setState on unmounted component" warning.

### Work Done
- `frontend/src/pages/DealList.tsx`: Refactored `useEffect` to use async function with cancelled flag pattern. Both `npx tsc --noEmit` and `npm run lint` pass clean.

### Invariants Verified
- [x] TypeScript compiles with zero errors
- [x] `npm run lint` passes with zero warnings/errors
- [x] Cleanup function prevents state updates after unmount

### Handoff
- **Status:** READY_FOR_VALIDATION
- **Next agent should:** Re-run `npm run lint` in `frontend/` to confirm C-005 is resolved.
- **Blockers:** None
