# Deal Screening Agent — System Specification

> **Version:** 2.0
> **Date:** 2026-04-10
> **Author:** Chris
> **Status:** Active — Build Phase
> **Build Methodology:** Spec-driven, AI-conducted development (DADP: Claude Builder + Codex Validator)

---

## What This Is

A production-grade specification for an AI-powered deal screening agent that ingests deal documents (CIMs, teasers), extracts structured data via LLM, scores deals against configurable investment criteria, and surfaces ranked results to analysts.

This spec is the artifact. The code is generated from it by AI agents under a Dual-Agent Development Protocol: Claude acts as Builder, Codex acts as Validator, and I (the human) conduct by authoring and refining these specification files. The quality of the built system is bounded by the quality of the spec — so this document is written to be *executable* by a competent AI agent with minimal ambiguity.

## Reading Order

New readers should follow this order:

1. **`00_main_spec.md`** — Problem framing (Phase 0) and system model (Phase 1). Start here.
2. **`01_decomposition.md`** — Module overview (Phase 2): what modules exist, how they communicate, build order.
3. **`modules/*.md`** — Detailed specifications for each module. Read in the order listed in `01_decomposition.md`.
4. **`05_deployment.md`** — Phase 5: ECS reference architecture for production deployment.
5. **`06_operations.md`** — Runbooks, incident response, backup/restore procedures.
6. **`appendices/`** — Lifecycle maps, decision log, and the Fly.io portfolio deployment appendix.

For reviewers evaluating this as a portfolio piece, `portfolio_version.md` contains the concatenated monolithic version.

## Module Template

Every module specification follows the same template to enable consistent AI-driven execution and validation:

1. **Purpose** — one sentence stating what the module does
2. **Responsibilities** — what this module owns
3. **Non-Responsibilities** — what this module explicitly does NOT do (prevents scope creep)
4. **Inputs / Outputs** — data flowing in and out
5. **State Machine** — lifecycle states and transitions (where applicable)
6. **Data Model** — tables, schemas, relationships
7. **API Contract** — endpoints, request/response shapes
8. **Invariants** — what must always be true
9. **Trust Boundaries** — where data crosses trust levels
10. **Failure Modes** — what can go wrong, with user-facing and system-facing behavior
11. **Security Considerations** — attack surface and mitigations
12. **Dependencies** — internal modules and external services
13. **Testing Requirements** — what must be tested to consider the module complete
14. **Open Questions** — known unknowns, deferred decisions

## Directory Structure

```
.spec/
├── README.md                          ← you are here
├── 00_main_spec.md                    Phase 0 (problem) + Phase 1 (system model)
├── 01_decomposition.md                Phase 2 (module overview + build order)
├── modules/
│   ├── ingestion_service.md           Core: document intake
│   ├── extraction_service.md          Core: LLM-powered field extraction
│   ├── scoring_engine.md              Core: criteria-based scoring
│   ├── dashboard_api.md               Core: analyst-facing API + UI
│   ├── auth_service.md                Cross-cutting: JWT + session lifecycle
│   ├── rate_limiter.md                Cross-cutting: request throttling
│   ├── secrets_config.md              Cross-cutting: secrets management
│   ├── observability.md               Cross-cutting: logging, metrics, tracing
│   ├── input_validation.md            Cross-cutting: file + payload validation
│   └── background_jobs.md             Cross-cutting: async job execution
├── 05_deployment.md                   Phase 5: ECS + Fargate + RDS reference architecture
├── 06_operations.md                   Runbooks, incidents, backups, disaster recovery
├── appendices/
│   ├── A_lifecycle_maps.md            Entity lifecycle diagrams
│   ├── B_decision_log.md              Architectural decisions + tradeoffs
│   └── C_flyio_portfolio_deployment.md  Actual deployment target for portfolio phase
└── portfolio_version.md               Monolithic concatenation for showcase
```

## Build Status

| Batch | Files | Status |
|-------|-------|--------|
| 1 | README, 00_main_spec, 01_decomposition | ✅ Complete |
| 2 | Core business modules (ingestion, extraction, scoring, dashboard) | ⏳ Pending |
| 3 | Cross-cutting (auth, rate_limiter, secrets_config) | ⏳ Pending |
| 4 | Cross-cutting (observability, input_validation, background_jobs) | ⏳ Pending |
| 5 | Deployment, operations, appendices | ⏳ Pending |
| 6 | Portfolio concatenation | ⏳ Pending |

## Target Deployment

**Reference architecture:** AWS ECS Fargate + RDS Postgres + ALB + S3 + Secrets Manager. Specified in full in `05_deployment.md` as the production-grade target.

**Portfolio phase deployment:** Fly.io with managed Postgres, documented in `appendices/C_flyio_portfolio_deployment.md`. This is the actual running deployment for the portfolio phase — chosen for cost reasons ($5–15/month vs $80–120/month on ECS). The ECS architecture remains the canonical design and the Fly.io mapping is explicit about the tradeoffs.

## DADP Workflow Notes

This spec is structured for a two-agent build workflow:

- **Builder (Claude)** reads a module spec and generates the implementation. Each module is designed to be buildable in isolation once its dependencies are built.
- **Validator (Codex)** reads the same module spec and generates tests, reviews the Builder's output against the spec's invariants, failure modes, and testing requirements.
- **Conductor (human)** authors and refines the spec, reviews Builder/Validator outputs, makes architectural decisions that get logged in `appendices/B_decision_log.md`.

The `.spec/` directory is the source of truth for *what* to build. The `.agent/` directory (separate from this spec, managed during build) holds Builder/Validator working memory, draft code, and review notes.