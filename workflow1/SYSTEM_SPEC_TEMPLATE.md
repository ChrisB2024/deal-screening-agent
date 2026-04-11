# System Thinking Spec — [PROJECT NAME]

> **Version:** 1.0
> **Date:** [DATE]
> **Author:** [HUMAN ARCHITECT]
> **Status:** Draft | Active | Frozen

---

## Phase 0 — Problem Thinking

### Who Hurts and Why

**The Person:**
<!-- A specific human with a specific pain — not a demographic -->

**The Pain:**
<!-- What are they struggling with right now? -->

**The Ugly Workaround:**
<!-- How do they currently cope? What's broken about it? -->

**The Sentence Test:**
> "[Person] currently does [ugly workaround] because [pain], and my product eliminates this by [mechanism]."

### Product Invariants

<!-- What must ALWAYS be true from the user's perspective? These are non-negotiable. -->

1. [ ] _The user must never..._
2. [ ] _Core actions must always..._
3. [ ] _The system must never..._
4. [ ] _When data is missing, the system must..._
5. [ ] _The system must be honest about..._

### Security Invariants

<!-- What must ALWAYS be true about data, access, and trust? -->

1. [ ] _User data must..._
2. [ ] _No endpoint should..._
3. [ ] _Secrets must never..._
4. [ ] _If a component is compromised, the blast radius is limited to..._
5. [ ] _User data on shutdown/deletion will..._

### Technical Invariants

1. [ ] _Database transactions must..._
2. [ ] _API responses must always..._
3. [ ] _No state transition should occur without..._

### Product Causality Chains

<!-- If you change X, trace the chain: X → user behavior → retention → revenue → survival -->

| Change | Technical Impact | UX Impact | Business Impact | Security Impact |
|--------|----------------|-----------|-----------------|-----------------|
| | | | | |
| | | | | |

### Threat Model

| Data Touched | Who Should Access It | Blast Radius if Compromised | Regulatory Requirements |
|-------------|---------------------|-----------------------------|------------------------|
| | | | |
| | | | |

---

## Phase 1 — System Model

### State Machine

<!-- Define the primary states your product/user/entity moves through -->

```
[STATE_A] --trigger--> [STATE_B] --trigger--> [STATE_C]
     |                                             |
     └──────────failure────────────────────────────┘
```

**States:**
- `STATE_A`: [description]
- `STATE_B`: [description]
- `STATE_C`: [description]

**Transitions:**
- `STATE_A → STATE_B`: triggered by [event], requires [conditions]
- `STATE_B → STATE_C`: triggered by [event], requires [conditions]

**Invalid transitions:**
- `STATE_C → STATE_A` must never happen because [reason]

### Trust Boundaries

<!-- Where does data cross between trust levels? -->

| Boundary | From (Trust Level) | To (Trust Level) | Validation Required |
|----------|-------------------|-------------------|---------------------|
| Client → API | Untrusted | Trusted | Input validation, auth token, rate limiting |
| API → Database | Trusted | Trusted | Parameterized queries, access control |
| API → Third Party | Trusted | Semi-trusted | Response validation, timeout, fallback |
| | | | |

### Data Flow

```
[Source] → [Transform] → [Store] → [Transform] → [Display]
   ↑                        ↑                        ↑
   Who sees it?         Who can access?          Who sees it?
```

---

## Phase 2 — Decomposition

### Module Breakdown

<!-- Each module gets: purpose, inputs, outputs, invariants, security surface, failure modes -->
<!-- For complex projects, break each module into its own file in .spec/modules/[name].md -->

#### Module: [NAME]
- **Purpose:** [one sentence]
- **Inputs:** [what it receives]
- **Outputs:** [what it produces]
- **Invariants:** [what must always be true inside this module]
- **Security surface:** [what an attacker could target]
- **Failure modes:**
  - Invalid input → [what happens]
  - Dependency unavailable → [what happens]
  - Slow/timeout → [what happens]
  - Compromised → [blast radius]
- **What the user sees on failure:** [graceful degradation plan]

#### Module: [NAME]
- **Purpose:**
- **Inputs:**
- **Outputs:**
- **Invariants:**
- **Security surface:**
- **Failure modes:**
  - Invalid input →
  - Dependency unavailable →
  - Slow/timeout →
  - Compromised →
- **What the user sees on failure:**

<!-- Copy this block for each module. For 5+ modules, use .spec/modules/ directory instead. -->

### Module Communication

| Module A | Module B | Communication Pattern | Coupling Level | Contract |
|----------|----------|-----------------------|----------------|----------|
| | | sync / async / shared state | low / medium / high | |
| | | | | |

### Evolution Check

<!-- For each module: if I need to add a feature in 6 months, does this support it or fight it? -->

| Module | Likely Future Change | Current Architecture Supports It? | Risk |
|--------|---------------------|-----------------------------------|------|
| | | Yes / Partially / No | |
| | | | |

---

## Phase 3 — Implementation Plan

### Build Order

<!-- List modules in dependency order — what must be built first? -->

| Order | Module | Depends On | Estimated Sessions |
|-------|--------|------------|--------------------|
| 1 | | — | |
| 2 | | Module 1 | |
| 3 | | Module 1, 2 | |

### Per-Module Security Checklist

| Module | Handles Sensitive Data? | Auth Required? | Input Validation Plan | What If Attacker Controls Input? | What If Storage Breached? |
|--------|------------------------|----------------|----------------------|----------------------------------|--------------------------|
| | | | | | |
| | | | | | |

---

## Phase 4 — Validation Plan

### Technical Validation
- [ ] All tests pass
- [ ] Edge cases covered: [list them]
- [ ] Malicious input handled: [list scenarios]

### Security Validation
- [ ] Inputs sanitized at every trust boundary
- [ ] Auth enforced at API level (not just UI)
- [ ] Secrets externalized (no hardcoded values)
- [ ] Dependencies audited and pinned
- [ ] HTTPS enforced on all network boundaries

### Product Validation
- [ ] Put in front of a real user (even if that user is you)
- [ ] Does it solve the pain from Phase 0?
- [ ] What surprised you? [capture here after testing]

---

## Appendix A: Lifecycle Maps

### Entity: [e.g., User Account]
```
Created → Active → Modified → Archived → Deleted
                                    ↑
                            What happens to shared resources?
                            What happens to their data?
                            What happens to orphaned references?
```

### Entity: [e.g., Session]
```
Initiated → Active → Expired → Cleaned Up
                ↑
        What if token is stolen?
        What if session is hijacked?
```

---

## Appendix B: Decision Log Seed

<!-- Pre-populate with the big architectural decisions made during spec writing.
     These get expanded in claude_log.md during implementation. -->

| Decision | Options Considered | Chosen | Tradeoff | Reversible? |
|----------|--------------------|--------|----------|-------------|
| | | | | |
| | | | | |
