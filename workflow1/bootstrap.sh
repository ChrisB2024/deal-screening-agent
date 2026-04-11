#!/bin/bash
# DADP Bootstrap — Dual-Agent Development Protocol
# Run this from your project root to scaffold the full structure.

set -e

echo "🔧 Bootstrapping DADP structure..."

# Create .spec/ directory (the thinking)
mkdir -p .spec/modules
mkdir -p .spec/state_machines

# Create .agent/ directory (the doing)
mkdir -p .agent/prompts

# Initialize handoff state
cat > .agent/handoff.json << 'EOF'
{
  "last_updated_by": "bootstrap",
  "last_updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "current_phase": "spec",
  "session_number": 0,
  "status": "NOT_STARTED",
  "spec_version": "0.1",
  "modules": {},
  "open_blockers": [],
  "next_action": "Human: fill out .spec/system_spec.md"
}
EOF

# Initialize agent logs
cat > .agent/claude_log.md << 'EOF'
# Claude Agent Log (Builder)

> Append-only. Each session is a new section. Never overwrite previous entries.

---

## Session 0 — Bootstrap

Project initialized with DADP protocol. Awaiting system spec and first build session.
EOF

cat > .agent/codex_log.md << 'EOF'
# Codex Agent Log (Validator)

> Append-only. Each session is a new section. Never overwrite previous entries.

---

## Session 0 — Bootstrap

Project initialized with DADP protocol. Awaiting first validation session.
EOF

# Copy prompt templates (these should be customized per project)
cat > .agent/prompts/claude_session.md << 'PROMPT'
<!-- Copy the full Claude session prompt from protocol/prompts/claude_session.md -->
<!-- Customize the spec references for this project -->
PROMPT

cat > .agent/prompts/codex_session.md << 'PROMPT'
<!-- Copy the full Codex session prompt from protocol/prompts/codex_session.md -->
<!-- Customize the spec references for this project -->
PROMPT

# Create Codex CLI config
cat > codex.md << 'EOF'
# Codex Project Instructions

You are the Validator agent in a dual-agent development loop.
Follow the protocol described in the prompts at .agent/prompts/codex_session.md.

Key rules:
- Always read .spec/system_spec.md for project invariants
- Always read .agent/claude_log.md before writing tests
- Never modify source files — only create/update test files
- Always update .agent/codex_log.md and .agent/handoff.json
- Tag all results: [PASSED], [FAILED], [BLOCKER]
EOF

# Create placeholder spec
cat > .spec/system_spec.md << 'EOF'
# System Thinking Spec — [PROJECT NAME]

<!-- Fill this out using the SYSTEM_SPEC_TEMPLATE.md -->
<!-- This is the blueprint. Don't skip Phase 0. -->

> **Version:** 0.1
> **Date:** $(date +%Y-%m-%d)
> **Author:**
> **Status:** Draft
EOF

echo ""
echo "✅ DADP structure created:"
echo ""
echo "  .spec/                    ← System Thinking (the blueprint)"
echo "    system_spec.md          ← Fill this out first"
echo "    modules/                ← Per-module specs (when needed)"
echo "    state_machines/         ← State diagrams (when needed)"
echo "  .agent/                   ← Agent execution (the loop)"
echo "    claude_log.md           ← Builder reasoning log"
echo "    codex_log.md            ← Validator findings log"
echo "    handoff.json            ← Structured state between agents"
echo "    prompts/                ← Session prompt templates"
echo "  codex.md                  ← Codex CLI persistent instructions"
echo ""
echo "Next steps:"
echo "  1. Copy SYSTEM_SPEC_TEMPLATE.md content into .spec/system_spec.md"
echo "  2. Fill out Phase 0 (Problem Thinking) completely"
echo "  3. Fill out Phase 1-3 as far as you can"
echo "  4. Copy prompt templates into .agent/prompts/"
echo "  5. Run your first Claude session"
