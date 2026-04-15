# Module: Scoring Engine

> Core business module. Parent: `../01_decomposition.md`
> Build order: step 10 (depends on DB Models, Observability)

---

## 1. Purpose

Evaluate an `ExtractedDeal` against the active criteria configuration for its tenant, producing a `ScoredDeal` with an overall fit score (0–100), per-criterion results, a natural-language rationale, and a confidence level that reflects extraction completeness.

## 2. Responsibilities

- Load the active `CriteriaConfig` for the deal's tenant
- Evaluate each criterion against the extracted fields using a pluggable `ScoringStrategy` interface (default: `RuleBasedStrategy`)
- Produce a per-criterion result: `PASS` / `FAIL` / `SKIPPED` with numeric contribution and human-readable explanation
- Compute a weighted overall score (0–100) from per-criterion results
- Generate a short rationale string summarizing why the deal scored as it did
- Inherit extraction confidence and propagate it to the score (`HIGH` / `MEDIUM` / `LOW`)
- Persist the `ScoredDeal` and transition the deal state `EXTRACTED → SCORED`
- Emit metrics and audit log entries for every scoring event
- Handle missing field values gracefully: criteria referencing missing fields are `SKIPPED`, not `FAIL`

## 3. Non-Responsibilities

- **Does not extract fields.** That's Extraction Service.
- **Does not decide PASS/PURSUE.** That's the analyst via Dashboard API.
- **Does not manage criteria CRUD.** Criteria are authored via Dashboard API and read by this module.
- **Does not re-score historical deals when criteria change.** Per Phase 1 invariant, decisions are immutable and criteria changes apply only to future extractions.
- **Does not learn from feedback.** v1 is rule-based; the v2 ML strategy is an Open Question, not a current responsibility.
- **Does not call external services.** Scoring is pure computation over already-extracted data.

## 4. Inputs / Outputs

### Inputs

**`ExtractedDeal`** — passed in-process from Extraction Service (schema defined in `extraction_service.md`).

**`CriteriaConfig`** — loaded from DB for the deal's tenant:
```json
{
  "config_id": "cfg_01HW...",
  "tenant_id": "tnt_01HW...",
  "version": 7,
  "is_active": true,
  "criteria": [
    {
      "id": "c_sector",
      "name": "Sector fit",
      "field": "sector",
      "op": "in",
      "value": ["B2B SaaS", "Vertical SaaS", "DevTools"],
      "weight": 25,
      "required": true
    },
    {
      "id": "c_revenue",
      "name": "Revenue range",
      "field": "revenue",
      "op": "between",
      "value": [5000000, 50000000],
      "weight": 20,
      "required": true
    },
    {
      "id": "c_ebitda_margin",
      "name": "EBITDA margin >= 15%",
      "field": "derived.ebitda_margin",
      "op": "gte",
      "value": 0.15,
      "weight": 20,
      "required": false
    }
  ]
}
```

Supported operators: `eq`, `neq`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `between`, `contains`. Derived fields (e.g., `ebitda_margin = ebitda / revenue`) are computed by the engine before evaluation.

### Outputs

**`ScoredDeal`:**
```json
{
  "deal_id": "deal_01HW8K...",
  "tenant_id": "tnt_01HW...",
  "score_id": "scr_01HW...",
  "config_version": 7,
  "strategy": "rule_based_v1",
  "overall_score": 72,
  "confidence": "MEDIUM",
  "rationale": "Strong sector fit and revenue in range. EBITDA margin could not be evaluated (EBITDA inferred, not found).",
  "criterion_results": [
    {"id": "c_sector",        "result": "PASS",    "contribution": 25, "explanation": "Sector 'B2B SaaS' is in target list."},
    {"id": "c_revenue",       "result": "PASS",    "contribution": 20, "explanation": "Revenue $12.5M is within $5M–$50M range."},
    {"id": "c_ebitda_margin", "result": "SKIPPED", "contribution": 0,  "explanation": "EBITDA was inferred; margin cannot be computed with confidence."}
  ],
  "required_criteria_passed": true,
  "scored_at": "2026-04-11T14:03:25Z"
}
```

### Side Effects

- New row in `deal_scores` table
- State transition `EXTRACTED → SCORED` via `transition_deal_state()`
- New row in `deal_audit_log` (action `DEAL_SCORED`)
- Metrics emitted: `scoring.attempts`, `scoring.success`, `scoring.skipped_criteria`, `scoring.required_failed`, `scoring.duration_ms`
- Score histogram per tenant for drift detection

## 5. State Machine

Scoring does not define its own persisted state machine. It transitions the deal from `EXTRACTED` to `SCORED` as a terminal action. Internally, each scoring request follows:

```
RECEIVED → CONFIG_LOADED → DERIVED_COMPUTED → EVALUATED → AGGREGATED → PERSISTED → DONE
                │                │                 │            │
                ▼                ▼                 ▼            ▼
             FAILED           FAILED           FAILED        FAILED
```

A scoring failure (e.g., no active criteria config) leaves the deal in `EXTRACTED`, not `FAILED` — the deal is still valid, it just has no score yet. This is surfaced via a distinct failure reason code.

## 6. Data Model

### `criteria_configs` table

```sql
CREATE TABLE criteria_configs (
    config_id    TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    version      INT NOT NULL,
    is_active    BOOLEAN NOT NULL DEFAULT FALSE,
    criteria     JSONB NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_tenant_version UNIQUE (tenant_id, version)
);

CREATE UNIQUE INDEX idx_one_active_per_tenant
    ON criteria_configs(tenant_id) WHERE is_active = TRUE;
```

The partial unique index enforces that at most one config is active per tenant at any time. Activating a new config is a two-step transaction (deactivate old, activate new) wrapped in a single DB transaction.

### `deal_scores` table

```sql
CREATE TABLE deal_scores (
    score_id                 TEXT PRIMARY KEY,
    deal_id                  TEXT NOT NULL REFERENCES deals(deal_id),
    tenant_id                TEXT NOT NULL,
    extraction_id            TEXT NOT NULL REFERENCES deal_extractions(extraction_id),
    config_id                TEXT NOT NULL REFERENCES criteria_configs(config_id),
    config_version           INT NOT NULL,
    strategy                 TEXT NOT NULL,      -- e.g. rule_based_v1
    overall_score            INT NOT NULL,       -- 0-100
    confidence               TEXT NOT NULL,      -- HIGH|MEDIUM|LOW
    rationale                TEXT NOT NULL,
    criterion_results        JSONB NOT NULL,
    required_criteria_passed BOOLEAN NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_score CHECK (overall_score BETWEEN 0 AND 100),
    CONSTRAINT valid_confidence CHECK (confidence IN ('HIGH','MEDIUM','LOW'))
);

CREATE INDEX idx_scores_deal ON deal_scores(deal_id, created_at DESC);
CREATE INDEX idx_scores_tenant_score ON deal_scores(tenant_id, overall_score DESC, created_at DESC);
```

Multiple score rows per deal are allowed (re-scoring with a new config creates a new row). The `extraction_id` reference ties each score to the exact extraction it was based on — critical for audit and for understanding why a score changed.

### Score aggregation formula

```
overall_score = round(
    100 * sum(contribution_i for i in passed_criteria) /
    sum(weight_i for i in evaluated_criteria)
)
```

Where `evaluated_criteria` excludes `SKIPPED`. This means skipped criteria do not penalize the score — they shrink the denominator. A deal with only 2 of 5 criteria evaluable but both passing scores higher than a deal with 5 of 5 evaluable and 2 passing. The `confidence` field tells the analyst whether that score is trustworthy.

If any `required: true` criterion is `FAIL`, `required_criteria_passed = false` and the UI surfaces this prominently regardless of the numeric score.

## 7. API Contract

Scoring Engine has **no public HTTP API**. It is invoked in-process from the Extraction Service at the end of a successful extraction:

```python
async def score_deal(extracted: ExtractedDeal) -> ScoredDeal | ScoringFailure
```

Failures are returned as a typed `ScoringFailure` with a reason code (`NO_ACTIVE_CONFIG`, `INVALID_CONFIG`, `EVALUATION_ERROR`) — never raised as uncaught exceptions.

A future HTTP endpoint for manual re-scoring is expected and will be added via Dashboard API, which will call `score_deal()` internally.

## 8. Invariants

1. Every persisted score has a corresponding audit log row written in the same transaction.
2. `overall_score` is always an integer in `[0, 100]`. Fractional scores are rounded at the boundary.
3. A score is always tied to a specific `extraction_id` and `config_version`. Changing either creates a new score, never mutates an existing one.
4. A criterion referencing a `MISSING` field always produces `SKIPPED`, never `FAIL`. Missing data is not evidence of a bad deal.
5. `required_criteria_passed` is `false` if any required criterion is `FAIL`. A required criterion that is `SKIPPED` does not fail the gate — but the confidence level reflects the gap.
6. Confidence is propagated from the extraction, not re-derived: if extraction confidence was `LOW`, scoring confidence is `LOW`.
7. Scoring is pure: given the same `ExtractedDeal` + same `CriteriaConfig`, the output is byte-identical. No randomness, no timestamps in the rationale, no external calls.
8. `tenant_id` on the score is always taken from the `ExtractedDeal`, cross-checked against the config's `tenant_id`. A mismatch is a security event, not a silent failure.
9. The active criteria config for a tenant is unique (enforced by partial index). Scoring reads the active config at evaluation time; it does not cache across jobs.

## 9. Trust Boundaries

| Boundary | Validation |
|---|---|
| Extraction Service → Scoring Engine | In-process typed call; `tenant_id` cross-checked against DB deal row |
| Scoring → criteria_configs table | Tenant-scoped query; active config resolved via partial unique index |
| Criteria config content (authored via Dashboard) | JSON schema validation at write time, not at read time — but the engine still defends against malformed configs with a validating loader |
| Scoring → deal_scores write | Parameterized via ORM; tenant_id written from the verified input, never from config JSON |

The criteria config is tenant-authored data. The engine treats it as semi-trusted: it has been schema-validated at write time by Dashboard API, but the scoring loader re-validates before use. A config that fails re-validation produces `INVALID_CONFIG` failure, not a crash.

## 10. Failure Modes

| Failure | Detection | System Behavior | User-Facing Behavior |
|---|---|---|---|
| No active criteria config for tenant | Query returns zero rows | Return `NO_ACTIVE_CONFIG` failure; deal stays in `EXTRACTED` | "No screening criteria configured. Set up criteria to score deals." |
| Malformed criteria config | Re-validation fails at load | Return `INVALID_CONFIG`; alert emitted; deal stays in `EXTRACTED` | "Your criteria configuration is invalid. Please review." (plus the specific error) |
| Criterion references unknown field | Validator at load time | Same as `INVALID_CONFIG` — caught before any deal evaluation | Same |
| All criteria `SKIPPED` (every field missing) | Post-evaluation check | Score is 0 with confidence `LOW`, rationale explains nothing could be evaluated | "We couldn't evaluate this deal against your criteria — too many fields missing." |
| Divide-by-zero in derived field (e.g. revenue = 0 for ebitda_margin) | Derived field computation | Criterion evaluates to `SKIPPED` with explanation `"Cannot compute: revenue is zero"` | Shown inline on the criterion |
| Evaluator exception on a single criterion | try/except per criterion | That criterion is `SKIPPED` with explanation `"Evaluation error"`, others continue | Shown inline; alert emitted for engineering |
| Tenant mismatch (extracted vs config) | Cross-check before evaluation | Abort, log security event, do not write any score | N/A — never surfaced to users |
| DB write fails after evaluation | Transaction aborts | No state transition; extraction stays `EXTRACTED`; job retries via Background Jobs | Transparent |

### Failure isolation principle

A single bad criterion never fails the whole score. Each criterion is evaluated in its own try/except with a typed result; exceptions become `SKIPPED` with an explanation. This means a buggy evaluator can degrade score quality but cannot block the pipeline.

## 11. Security Considerations

- **Criteria as trade secret.** The criteria config reveals the fund's investment thesis. It must never leak across tenants. Defenses: tenant-scoped queries, `tenant_id` cross-check at every read, no criteria content in logs (only `config_id` and `version`), no criteria content in metrics labels.
- **Criteria exfiltration via error messages.** A malformed config must not echo the full config in an error message that could end up in shared logs. The validator returns the *path* to the bad field (e.g., `criteria[2].value`), never the value.
- **Rationale text as an injection vector.** The rationale is displayed in the UI. It is composed from fixed templates + sanitized field values — never from LLM output and never from raw config strings without escaping. This prevents stored XSS via criteria names or extracted sector strings.
- **Score manipulation via criteria replay.** Could an attacker with tenant access create a config, score a deal, then roll back the config to make the deal look different? No — the `config_version` is pinned on the score row, and configs are versioned append-only. Historical scores always reference the exact config version that produced them.
- **Denial via pathological configs.** A config with thousands of criteria or deeply nested derived fields could slow scoring. Limits enforced at config write time: max 50 criteria per config, max 1 derived field per criterion, max 3 levels of derived dependency.
- **Audit completeness.** Every score write produces an audit row containing `score_id`, `deal_id`, `config_version`, `extraction_id`, and the `required_criteria_passed` flag — enough to reconstruct why a deal passed or failed the gate months later.

## 12. Dependencies

**Internal modules:**
- Extraction Service (upstream, calls this module in-process)
- Dashboard API (authors criteria configs consumed by this module; also the site of future manual re-scoring triggers)
- Observability (logging, metrics, audit)
- DB Models (`criteria_configs`, `deal_scores`, state transition function)

**External services:**
- PostgreSQL only

**Libraries:**
- `pydantic` for `CriteriaConfig` and `ScoredDeal` schemas + validation
- `sqlalchemy[asyncio]` for DB access
- No LLM, HTTP, or filesystem dependencies — scoring is pure computation

## 13. Testing Requirements

### Unit tests
- Each operator (`eq`, `in`, `between`, `gte`, etc.) returns correct result for boundary values
- `between` is inclusive on both ends (documented behavior)
- Derived field computation: `ebitda_margin = ebitda / revenue` handles zero, null, and negative values
- `MISSING` field status always produces `SKIPPED`, never `FAIL`, for every operator
- `INFERRED` field status is treated as usable (contributes to score) but lowers confidence
- Aggregation formula: skipped criteria shrink denominator, do not count as fail
- `required_criteria_passed` is `false` iff at least one required criterion is `FAIL` (not `SKIPPED`)
- Rationale generator produces deterministic output for deterministic input
- Score is always `round()`-ed to an integer and clamped to `[0, 100]`

### Integration tests
- Happy path: extracted deal + active config → scored deal with correct score, rationale, audit log, state transition
- No active config: returns `NO_ACTIVE_CONFIG`, deal stays `EXTRACTED`, no score row written
- Invalid config: returns `INVALID_CONFIG` with the path to the bad field, alert emitted
- Config activation during scoring: concurrent activation of a new config does not cause mid-flight scoring to use a mix — the config is loaded once per scoring call
- Re-scoring: manually triggering a second score for the same deal creates a new row referencing the new config version, does not mutate the old row
- Tenant mismatch: extraction `tenant_id` differs from config `tenant_id` → security event, no write

### Security tests
- Cross-tenant read attempt (force-load another tenant's config via crafted input) fails at the query layer
- Malformed config error message contains only the field path, never the field value
- XSS payload in a criterion name is escaped in the rendered rationale
- Pathological config (51 criteria) is rejected at write time, not discovered at scoring time

### Determinism tests
- Same `ExtractedDeal` + same `CriteriaConfig` → byte-identical `ScoredDeal` across 100 runs (excluding `score_id` and `scored_at`)
- Reordering criteria in the config produces the same overall score (commutativity) and the same per-criterion results

### Regression fixtures
- A growing library of labeled `(ExtractedDeal, CriteriaConfig, expected_score)` fixtures. Any change to the scoring strategy must re-run these and explain diffs.

## 14. Open Questions

1. **ML-based scoring (v2).** The Evolution Check in `01_decomposition.md` flags an ML strategy trained on pass/pursue feedback. The `ScoringStrategy` interface is designed to accept this, but the interface boundary needs refinement: does the ML strategy consume the same `CriteriaConfig`, or does it replace it entirely with a learned model? Proposal: hybrid — criteria define hard gates (required), ML adjusts the soft score. Defer until feedback data exists.
2. **Per-analyst personalization.** The Evolution Check also flags per-user scoring weights. Current design is per-tenant. A per-user override layer would slot in at config load time — but should overrides compose with tenant config or replace it? Defer until user demand is clear.
3. **Rationale generation.** v1 uses fixed templates for determinism and security. A future version could use an LLM to generate more nuanced rationales, but that reintroduces non-determinism, cost, and a prompt-injection surface. If added, it must run *after* the deterministic score is fixed — never in the scoring loop itself.
4. **Score calibration across tenants.** Different tenants will use different numbers of criteria and different weights, so a "72" for one tenant isn't comparable to a "72" for another. Is this a problem? For a single-tenant analyst, no. For cross-tenant benchmarking (a future feature), yes. Defer.
5. **Re-scoring policy.** When a tenant activates a new config, should deals currently in `EXTRACTED` (not yet scored) be scored with the new config automatically? Almost certainly yes — but deals already in `SCORED` are more delicate. Current answer: never auto-re-score, always require explicit user action. Revisit if users complain.
6. **Derived field library.** v1 ships with a small set of derived fields (`ebitda_margin`, `revenue_per_employee` if employee count is extracted). Should users be able to define their own derived fields via a safe expression language? Attractive but expensive to build safely. Defer until core scoring is stable.