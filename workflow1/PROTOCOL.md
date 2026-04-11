# Dual-Agent Development Protocol (DADP)

> **Purpose:** Define the rules, roles, handoff process, and log formats for a two-agent development loop where Claude (Builder) and Codex (Validator) operate asynchronously through file-based communication, with the human architect reviewing agent logs as a learning and oversight mechanism.

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    HUMAN ARCHITECT                          │
│         Reviews logs · Learns concepts · Steers direction   │
│                  ▲                 ▲                         │
│                  │ reads           │ reads                   │
│                  │                 │                         │
│    ┌─────────────┴──┐       ┌─────┴──────────────┐         │
│    │ claude_log.md   │       │ codex_log.md        │         │
│    └─────────────┬──┘       └─────┬──────────────┘         │
│                  │                │                          │
│           ┌──────┴──────┐  ┌─────┴───────┐                 │
│           │   CLAUDE    │  │   CODEX     │                  │
│           │  (Builder)  │──│ (Validator) │                  │
│           └─────────────┘  └─────────────┘                  │
│                                                             │
│    Claude writes code + log → Codex reads both →            │
│    Codex writes tests + log → Claude reads on next pass     │
└─────────────────────────────────────────────────────────────┘
```

### The Loop

1. **Human** produces or approves a System Thinking Spec (see `SYSTEM_SPEC_TEMPLATE.md`)
2. **Claude** reads the spec + any existing `codex_log.md`, writes code, documents reasoning in `claude_log.md`
3. **Human** reviews `claude_log.md` (Feynman learning loop)
4. **Codex** reads the codebase + `claude_log.md`, writes tests, documents findings in `codex_log.md`
5. **Claude** reads `codex_log.md` on the next iteration, addresses failures, continues building
6. **Repeat** until the current build phase is complete

### File Locations

```
project-root/
├── .spec/                         # System Thinking — the blueprint (planning)
│   ├── system_spec.md             # Filled-out project spec
│   ├── threat_model.md            # Detailed threat model (grows over time)
│   ├── state_machines/            # State diagrams, detailed transition docs
│   │   └── [entity]_states.md
│   └── modules/                   # Per-module spec sheets
│       └── [module_name].md
├── .agent/                        # Agent execution loop (runtime)
│   ├── claude_log.md              # Builder reasoning + decision log
│   ├── codex_log.md               # Validator test results + findings log
│   ├── handoff.json               # Structured state between agents
│   └── prompts/                   # Session prompt templates
│       ├── claude_session.md
│       └── codex_session.md
├── src/                           # Source code (Claude writes here)
├── tests/                         # Test files (Codex writes here)
└── codex.md                       # Codex CLI persistent instructions
```

**Why the separation:** `.spec/` is the *thinking* — it evolves slowly and represents architectural decisions. `.agent/` is the *doing* — it grows every session and represents execution state. Mixing them makes it hard to find the blueprint when you need to check alignment, and hard to scan execution logs when you need to debug a loop.

---

## 2. Agent Roles

### Claude — The Builder

**Reads:** `system_spec.md`, `codex_log.md`, `handoff.json`, existing codebase
**Writes:** Source code, `claude_log.md`, `handoff.json`

**Rules:**
1. Never write code without first logging the *why* in `claude_log.md`
2. Every function/module entry must include: purpose, inputs, outputs, invariants, and security considerations
3. When reading `codex_log.md`, acknowledge every `[BLOCKER]` and `[FAILED]` item before writing new code
4. Explain architectural decisions at two levels: (a) technical rationale, (b) conceptual explanation a non-expert could follow
5. Flag any deviation from `system_spec.md` with a `[SPEC_DEVIATION]` tag and justification
6. Update `handoff.json` at the end of every session

### Codex — The Validator

**Reads:** Source code, `claude_log.md`, `handoff.json`, `system_spec.md`
**Writes:** Test files, `codex_log.md`, `handoff.json`

**Rules:**
1. Read `claude_log.md` before writing any tests — understand *intent* before validating *implementation*
2. Write three categories of tests:
   - **Correctness tests:** Does the code do what `claude_log.md` says it should?
   - **Spec compliance tests:** Does the code satisfy the invariants defined in `system_spec.md`?
   - **Adversarial tests:** What happens with malicious input, missing data, broken dependencies?
3. Log every test result with `[PASSED]`, `[FAILED]`, or `[BLOCKER]` tags
4. When a test fails, explain *what* failed, *why* it likely failed (referencing Claude's stated logic), and *what needs to change*
5. Never modify source code — only write tests and log findings
6. Update `handoff.json` at the end of every session

---

## 3. Log Format

Both agents use the same log structure. Each log is **append-only** — agents never delete or overwrite previous entries. Each session is a new section.

### Log Entry Structure

```markdown
---

## Session [N] — [DATE] — [PHASE]

### Context
- **Reading from:** [list of files/logs read this session]
- **Building/Testing:** [module or feature name]
- **Spec reference:** [which section of system_spec.md this addresses]

### Decisions

#### [DECISION-N.1] [Short title]
- **What:** [What was decided or built]
- **Why:** [Technical rationale]
- **Concept:** [Plain-language explanation of the underlying concept — this is the learning payload]
- **Tradeoff:** [What was sacrificed and why]
- **Alternatives considered:** [What else could have been done]

### Work Done
- [File path]: [What was created/modified and why]
- [File path]: [What was created/modified and why]

### Invariants Verified
- [ ] [Invariant from spec]: [How it was maintained]
- [ ] [Invariant from spec]: [How it was maintained]

### Security Considerations
- [What security implications were considered]
- [What trust boundaries were affected]

### Open Questions
- [Anything unresolved that the other agent or human should address]

### Handoff
- **Status:** [READY_FOR_VALIDATION | READY_FOR_BUILD | BLOCKED]
- **Next agent should:** [Specific instructions for what to do next]
- **Blockers:** [Any blockers preventing progress]
```

### Tag Reference

| Tag | Used By | Meaning |
|-----|---------|---------|
| `[DECISION-N.X]` | Both | Architectural or testing decision (session N, decision X) |
| `[PASSED]` | Codex | Test passed |
| `[FAILED]` | Codex | Test failed — needs Claude's attention |
| `[BLOCKER]` | Both | Cannot proceed until this is resolved |
| `[SPEC_DEVIATION]` | Claude | Implementation deviates from spec (with justification) |
| `[CONCEPT]` | Claude | Marks an explanation intended for human learning |
| `[SECURITY]` | Both | Security-relevant observation |
| `[REGRESSION]` | Codex | A previously passing test now fails |

---

## 4. Handoff State (`handoff.json`)

This file is the structured handoff between agents. It provides machine-readable state so agents can quickly understand where things stand without parsing the full logs.

```json
{
  "last_updated_by": "claude",
  "last_updated_at": "2026-03-22T14:30:00Z",
  "current_phase": "implementation",
  "session_number": 3,
  "status": "READY_FOR_VALIDATION",
  "spec_version": "1.0",
  "modules": {
    "auth": {
      "status": "implemented",
      "tests": "pending",
      "files": ["src/auth/handler.py", "src/auth/models.py"],
      "blockers": []
    },
    "api_gateway": {
      "status": "in_progress",
      "tests": "none",
      "files": ["src/api/routes.py"],
      "blockers": ["Waiting on auth module validation"]
    }
  },
  "open_blockers": [
    {
      "id": "B-001",
      "source": "codex",
      "session": 2,
      "description": "Rate limiter fails under concurrent load",
      "severity": "high",
      "resolved": false
    }
  ],
  "next_action": "Codex: validate auth module, run adversarial tests on session handling"
}
```

---

## 5. Session Lifecycle

### Starting a Claude Session

```
1. Read handoff.json → understand current state
2. Read codex_log.md (latest session) → understand what was tested, what failed
3. Address all [BLOCKER] and [FAILED] items first
4. Read .spec/system_spec.md → confirm alignment with spec
5. Begin work → log decisions as you go
6. Update handoff.json → set status to READY_FOR_VALIDATION
7. Append to claude_log.md → complete session entry
```

### Starting a Codex Session

```
1. Read handoff.json → understand current state
2. Read claude_log.md (latest session) → understand what was built and why
3. Read the source files listed in the latest Claude session
4. Write tests in three categories (correctness, spec compliance, adversarial)
5. Run all tests → log results
6. Update handoff.json → set status to READY_FOR_BUILD (or BLOCKED if critical failures)
7. Append to codex_log.md → complete session entry
```

### Human Review Cycle

After each agent session (or at minimum after each full loop), the human reviews:

1. **`claude_log.md`** — Focus on `[CONCEPT]` tags and `Concept` fields. This is your Feynman loop. If you can't explain it after reading Claude's explanation, flag it as `[NEEDS_DEEPER_EXPLANATION]` and Claude must re-explain in the next session.
2. **`codex_log.md`** — Focus on `[FAILED]` and `[BLOCKER]` tags. Understand *what* broke and *why*.
3. **`handoff.json`** — Quick status check on module progress and blockers.

---

## 6. Prompt Templates

### Claude Session Prompt

```
You are the Builder agent in a dual-agent development loop.

YOUR ROLE: Write production code and document your reasoning.

BEFORE YOU START:
1. Read .agent/handoff.json for current state
2. Read .agent/codex_log.md (latest session) for test results and findings
3. Read .spec/system_spec.md for project spec and invariants
4. Read .spec/modules/ for per-module specs if they exist
4. Address all [BLOCKER] and [FAILED] items before new work

RULES:
- Log every decision in .agent/claude_log.md using the session format
- Every function must have: purpose, inputs, outputs, invariants, security notes
- Explain concepts at two levels: technical rationale + plain-language concept
- Tag spec deviations with [SPEC_DEVIATION]
- Tag learning-relevant explanations with [CONCEPT]
- Update .agent/handoff.json when done
- Set handoff status to READY_FOR_VALIDATION when session is complete

YOUR OUTPUT:
- Source code files
- Updated .agent/claude_log.md (appended, never overwritten)
- Updated .agent/handoff.json

Current spec: .spec/system_spec.md
Module specs: .spec/modules/
Current state: .agent/handoff.json
Validator findings: .agent/codex_log.md
```

### Codex Session Prompt

```
You are the Validator agent in a dual-agent development loop.

YOUR ROLE: Read the builder's code and reasoning, then write comprehensive tests.

BEFORE YOU START:
1. Read .agent/handoff.json for current state
2. Read .agent/claude_log.md (latest session) to understand what was built and why
3. Read the source files referenced in the latest claude_log session
4. Read .spec/system_spec.md for project invariants
5. Read .spec/modules/ for per-module specs

WRITE THREE CATEGORIES OF TESTS:
1. Correctness tests — does the code do what claude_log.md says it should?
2. Spec compliance tests — does the code satisfy system_spec.md invariants?
3. Adversarial tests — malicious input, missing data, broken dependencies

RULES:
- Never modify source code — only write test files and update logs
- Tag every test result: [PASSED], [FAILED], or [BLOCKER]
- For failures, explain what failed, why, and what needs to change
- Reference specific decisions from claude_log.md when relevant
- Update .agent/handoff.json when done
- Set handoff status to READY_FOR_BUILD (or BLOCKED if critical failures)

YOUR OUTPUT:
- Test files in tests/
- Updated .agent/codex_log.md (appended, never overwritten)
- Updated .agent/handoff.json
```

---

## 7. Codex CLI Integration

Since Codex runs via the OpenAI Codex CLI, here is how to invoke each session:

### Running a Codex Validation Session

```bash
# From project root
codex --model o4-mini \
  --approval-mode suggest \
  --prompt "$(cat .agent/prompts/codex_session.md)"
```

**Notes on `--approval-mode`:**
- Use `suggest` for the first few sessions so you can review what Codex writes before it executes
- Move to `auto-edit` once you trust the test-writing patterns
- Never use `full-auto` for the validator — you want to review test logic

### Recommended Codex Configuration

Create a `codex.md` file in your project root for Codex's persistent instructions:

```markdown
# Codex Project Instructions

You are the Validator agent in a dual-agent loop. Follow the protocol in .agent/PROTOCOL.md.

Key rules:
- Always read .spec/system_spec.md for project invariants
- Always read .agent/claude_log.md before writing tests
- Never modify source files — only create/update test files
- Always update .agent/codex_log.md and .agent/handoff.json
- Tag all results: [PASSED], [FAILED], [BLOCKER]
```

---

## 8. Anti-Patterns

### Things That Will Break the Loop

1. **Agents editing each other's logs.** Logs are append-only. Claude never edits `codex_log.md` and vice versa. Violation breaks the audit trail.

2. **Skipping the log.** If Claude writes code without logging, Codex tests blind. If Codex tests without logging, Claude iterates blind. The logs *are* the protocol.

3. **Codex modifying source code.** The validator never writes production code. If Codex needs a code change, it logs a `[FAILED]` or `[BLOCKER]` and Claude handles it in the next session.

4. **Ignoring blockers.** Claude must address every `[BLOCKER]` before writing new code. Unresolved blockers compound into architectural debt.

5. **Overwriting logs.** Every session is appended. Never truncate, never overwrite. The full history is what enables the human to trace decisions backward.

6. **Treating the spec as immutable.** The spec evolves. When implementation reveals the spec was wrong, Claude logs a `[SPEC_DEVIATION]`, explains why, and the human updates the spec. The spec is a living contract, not a mandate.

---

## 9. Bootstrap Checklist

When starting a new project with this protocol:

```
[ ] Create .spec/ directory (with modules/ and state_machines/ subdirs)
[ ] Create .agent/ directory (with prompts/ subdir)
[ ] Write .spec/system_spec.md using SYSTEM_SPEC_TEMPLATE.md
[ ] Write .spec/threat_model.md
[ ] Break out per-module specs into .spec/modules/ if needed
[ ] Initialize .agent/claude_log.md with a Session 0 header
[ ] Initialize .agent/codex_log.md with a Session 0 header
[ ] Initialize .agent/handoff.json with project modules and initial state
[ ] Copy prompt templates into .agent/prompts/
[ ] Create codex.md in project root for Codex CLI
[ ] First Claude session: scaffold project structure
[ ] First Codex session: validate scaffolding + write smoke tests
[ ] Human review: verify logs are readable and concepts are clear
```
