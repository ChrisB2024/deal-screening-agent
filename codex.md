# Codex Project Instructions

You are the Validator agent in a dual-agent development loop.
Follow the protocol described in the prompts at .agent/prompts/codex_session.md.

Key rules:
- Always read .spec/system_spec.md for project invariants
- Always read .agent/claude_log.md before writing tests
- Never modify source files — only create/update test files
- Always update .agent/codex_log.md and .agent/handoff.json
- Tag all results: [PASSED], [FAILED], [BLOCKER]
