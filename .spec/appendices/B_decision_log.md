# Appendix B — Decision Log

Parent: `README.md`

## Purpose

A running record of architectural decisions made during spec authoring, with the alternatives considered and the rationale. This log is append-only. When a decision is revisited, the new decision gets a new entry that references the old one, and the old one stays in place as history.

Format: each entry has a date, a one-line title, Context, Decision, Alternatives Considered, Consequences, and a Status (Active / Superseded by #N / Revisited in #N).

---

## ADR-001 — 2026-04-05 — Use PostgreSQL as the job queue

**Context:** Background Jobs module needs a durable async execution substrate for the Ingestion → Extraction boundary.

**Decision:** Implement the queue as a PostgreSQL table (`background_jobs`) using `SELECT ... FOR UPDATE SKIP LOCKED` for worker claims. No separate message broker.

**Alternatives Considered:**
- **Redis Streams:** lower latency, but adds a new stateful dependency and a separate durability model.
- **SQS:** fully managed, but ties us to AWS and makes local dev and testing harder (LocalStack is fine but imperfect).
- **NATS/Kafka:** massive overkill at this scale.

**Rationale:** PostgreSQL gives us transactional enqueue with the producing write. When Ingestion inserts a `deals` row and enqueues an extraction job, both happen in the same transaction — no window where the deal exists but the job doesn't. That property is worth more than the latency we'd gain from a dedicated broker. Revisit if throughput measurements justify the added surface (see `modules/background_jobs.md` Open Question 4).

**Consequences:** Extra load on the primary DB. Claim query performance depends on a partial index. Migration off this approach, if ever needed, is non-trivial.

**Status:** Active.

---

## ADR-002 — 2026-04-06 — Extraction → Scoring is an in-process call, not a queue hop

**Context:** After extraction completes, scoring runs next. This could be either a direct in-process function call or another queue hop.

**Decision:** Direct in-process call within the worker.

**Alternatives Considered:**
- **Second queue hop:** cleaner module isolation, enables independent scaling.

**Rationale:** Both services are stateless, operate on the same `deal_id`, and run on the same worker fleet. Adding a queue hop adds failure modes (dead-lettering, partial completion, ordering) without isolating anything meaningful. A single extraction-plus-scoring unit of work is easier to reason about and to trace. If scoring ever grows heavy enough to need independent scaling, split then — not now.

**Consequences:** A scoring bug can dead-letter an extraction job. Scoring latency is additive to extraction latency under the same claim timeout. Extraction and scoring must ship in the same image (see ADR-004).

**Status:** Active.

---

## ADR-003 — 2026-04-06 — Two ECS services, not ten

**Context:** Ten modules in `01_decomposition.md`. How many ECS services?

**Decision:** Two services — `api` (sync request path) and `worker` (async job handlers). See `05_deployment.md` § 3.

**Alternatives Considered:**
- **Ten microservices:** maximum isolation, maximum operational overhead.
- **One monolith:** simpler, but couples API latency to LLM latency, defeating the async decomposition.
- **Three services:** split Auth off for blast-radius reasons.

**Rationale:** Modules that share a request lifecycle should share a process; modules that cross a time boundary should not. Splitting further buys isolation the module boundaries already give us, at the cost of deployment and observability complexity we'd rather not pay.

**Consequences:** A code-level bug in one "module" can affect others in the same service. Defense-in-depth comes from module-level design (typed failures, trust boundaries) rather than from network-level isolation.

**Status:** Active.

---

## ADR-004 — 2026-04-06 — One image, two entrypoints

**Context:** Given two services, do they ship as one Docker image or two?

**Decision:** One image, two entrypoints. Both services deploy atomically from the same ECR tag. See `05_deployment.md` § 8.

**Alternatives Considered:**
- **Two images:** independent deploy cadence per service.

**Rationale:** Prevents schema drift between producer and consumer. The `SCHEMA_EVOLUTION` dead-letter reason in Background Jobs exists for the case where a Pydantic model changes in a way `api` knows about but `worker` doesn't. This image strategy is the first line of defense against that happening.

**Consequences:** Cannot deploy one service without the other. Emergency hotfixes to one path ship both. Acceptable because deploys are fast and rollback is safe.

**Status:** Active.

---

## ADR-005 — 2026-04-06 — Fly.io for portfolio deployment, ECS as canonical

**Context:** Portfolio phase needs a real running deployment. Production-grade would be AWS ECS, but at $325/month for a portfolio project that's hard to justify.

**Decision:** Deploy the portfolio version to Fly.io ($5–15/month). Keep AWS ECS as the canonical reference architecture. Document the mapping in `appendices/C_flyio_portfolio_deployment.md`.

**Alternatives Considered:**
- **ECS-only:** $325/month just to prove it runs, hard to sustain.
- **Free tier hacks (Render, Railway free tiers):** too constrained for the worker workload and credentials story.
- **Fly.io only, drop ECS spec:** loses the "this is what production would actually look like" portfolio value.

**Rationale:** The spec is the portfolio artifact as much as the running system. Having both lets the portfolio tell two stories: "here's what I would actually build" (ECS) and "here's what's actually running that you can poke at" (Fly.io). The appendix makes the tradeoffs explicit.

**Consequences:** Two deployment targets to maintain. Fly.io limitations (single-region, less mature AZ story) shape the portfolio version in ways that need to be called out explicitly.

**Status:** Active.

---

## ADR-006 — 2026-04-07 — Audit log lives in PostgreSQL, operational logs do not

**Context:** Where does `deal_audit_log` live, and does it share storage with operational logs?

**Decision:** `deal_audit_log` is a Postgres table, written transactionally with the state change it records. Operational logs go to stdout → CloudWatch Logs and never touch Postgres. See `modules/observability.md` § 11.

**Alternatives Considered:**
- **Both in Postgres:** simpler, but operational log volume would blow out DB storage.
- **Both in log sink:** cheaper, but loses transactional atomicity with state changes. Audit log becomes best-effort, which is worthless for audit purposes.

**Rationale:** Audit logs and operational logs have different durability requirements. Audit logs are business records that must be atomic with state changes; operational logs are debugging data that can tolerate loss. Conflating them sacrifices one of the two.

**Consequences:** Two storage systems to manage, two retention policies, two query interfaces. Worth it for the atomicity guarantee.

**Status:** Active.

---

## ADR-007 — 2026-04-08 — PII scrubbing runs in-process, before log emit

**Context:** PII scrubbing could happen at three possible places: in-process before log emit, at the log shipper (sidecar), or at the sink.

**Decision:** In-process, synchronously, before any log record leaves the process. See `modules/observability.md` § 11.

**Alternatives Considered:**
- **Sink-side scrubbing:** centralizes the rules, but exposes untrusted data to every hop in between.
- **Sidecar scrubbing:** better than sink but still exposes data to the sidecar.

**Rationale:** Defense in depth means assuming every hop can be compromised. In-process scrubbing means no untrusted data ever exists outside the process memory. Adds a small per-log-line cost, pays for it many times over in blast-radius containment.

**Consequences:** Every module pays the scrubbing cost. Scrubber bugs affect logging for all modules uniformly — which is also a property of the decision, because it means one fix helps everyone.

**Status:** Active.

---

## ADR-008 — 2026-04-09 — Temperature 0 and pinned model version for extraction

**Context:** LLM outputs are nondeterministic. How much effort to spend on reproducibility?

**Decision:** `temperature=0` on every extraction call, model version pinned to a specific release (e.g., `gpt-4o-2024-08-06`), prompt version string recorded with every extraction. See `modules/extraction_service.md` invariant 3.

**Alternatives Considered:**
- **Temperature > 0 with ensembling:** better quality on some tasks, non-reproducible.
- **Model alias (e.g., `gpt-4o`):** tracks the latest, but provider-side upgrades produce silent behavior changes.

**Rationale:** Reproducibility matters for debugging and audit, not because it's theoretically elegant. When an analyst asks "why did this deal score 72 yesterday," we need to be able to answer. Pinned model + prompt version + temp 0 gets us close enough — not byte-identical (OpenAI doesn't guarantee that even at temp 0), but close enough that the same input yields the same output within a single model version.

**Consequences:** Model upgrades are deliberate config changes, not automatic. Each upgrade gets a decision log entry. Slower to benefit from provider improvements; worth it.

**Status:** Active.

---

## ADR-009 — 2026-04-09 — Defer OCR for scanned PDFs

**Context:** Image-only PDFs currently fail with `EMPTY_TEXT`. OCR would fix this.

**Decision:** Defer OCR. Track `EMPTY_TEXT` failure rate as the signal for when to revisit. See `modules/extraction_service.md` Open Question 1.

**Alternatives Considered:**
- **Tesseract in-process:** free but adds a heavy dependency.
- **AWS Textract / Google Cloud Vision:** works well but adds cost, latency, and a new external dependency.

**Rationale:** Unknown if OCR demand is real. Adding it now increases complexity and cost for a problem that may not matter. The `EMPTY_TEXT` failure rate from observability tells us when users are actually hitting this, at which point the decision becomes data-driven rather than speculative.

**Consequences:** Users uploading scanned PDFs get a clear failure message, not a workaround. If demand exists, we'll see it in the metrics and can revisit with real data.

**Status:** Active.

---

## ADR-010 — 2026-04-10 — Blameless postmortems are policy, not suggestion

**Context:** Incident response culture. Many teams nominally say postmortems are blameless but soften under pressure.

**Decision:** Blameless postmortems are enforced as policy. Any SEV-1 or SEV-2 gets one. See `06_operations.md` § 8.3.

**Alternatives Considered:**
- **Soft commitment:** "we try to be blameless." Collapses the first time a high-visibility incident happens.

**Rationale:** Half-hearted blamelessness is worse than none — if people suspect they'll be blamed, they hide information, and postmortems stop producing useful output. Either commit fully or don't bother.

**Consequences:** Requires cultural enforcement, not just documentation. If the org isn't prepared to enforce it, the policy should be softened now rather than in the middle of an incident.

**Status:** Active.

---

## Template for new entries

```
## ADR-NNN — YYYY-MM-DD — One-line title

**Context:** What situation prompted this decision.

**Decision:** What was decided.

**Alternatives Considered:**
- **Option A:** brief description and why rejected
- **Option B:** ...

**Rationale:** Why the chosen option over the alternatives.

**Consequences:** What we now have to live with, good and bad.

**Status:** Active / Superseded by ADR-NNN / Revisited in ADR-NNN.
```