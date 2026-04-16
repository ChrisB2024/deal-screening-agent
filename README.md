# Deal Screening Agent

AI-powered deal screening for investment firms. Upload a CIM or teaser PDF, get structured data extraction via LLM, and score deals against configurable investment criteria -- ranked by fit.

**Live:** [deal-screener.fly.dev](https://deal-screener.fly.dev)

## What It Does

A deal sourcing analyst reviews 50-200+ inbound deals per week. ~80% are obvious passes (wrong sector, size, geography, margins), but every CIM still has to be opened and mentally evaluated. This tool automates that triage:

1. **Upload** a deal document (PDF)
2. **Extract** structured fields (company name, revenue, EBITDA, sector, geography, etc.) via GPT-4o
3. **Score** the deal against configurable investment criteria with confidence levels
4. **Surface** ranked results so analysts focus on the 3 deals that actually matter

## Architecture

```
React (Vite + Tailwind 4)  -->  FastAPI  -->  PostgreSQL
                                   |
                           Background Jobs
                                   |
                            OpenAI GPT-4o
                          (field extraction)
```

**10 modules**, split into core business logic and cross-cutting infrastructure:

| Core | Cross-Cutting |
|------|---------------|
| Ingestion Service | Auth (JWT + refresh token rotation) |
| Extraction Service (LLM) | Rate Limiter (token bucket) |
| Scoring Engine | Secrets & Config |
| Dashboard API | Observability (structured logging, tracing) |
| | Input Validation |
| | Background Jobs (PostgreSQL-backed queue) |

## Tech Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy (async), Alembic
- **Frontend:** React 19, TypeScript, Vite, Tailwind CSS 4, React Router
- **Database:** PostgreSQL (async via asyncpg)
- **LLM:** OpenAI GPT-4o for document extraction
- **Auth:** JWT access tokens (15 min) + refresh token rotation with session revocation
- **Deployment:** Fly.io (single machine, managed Postgres)

## Built with DADP

This project was built using the **Dual-Agent Development Protocol (DADP)** -- a spec-driven, AI-conducted development methodology:

```
                    HUMAN ARCHITECT
          Reviews logs - Learns concepts - Steers direction
                 |                    |
           claude_log.md         codex_log.md
                 |                    |
            CLAUDE (Builder)  <-->  CODEX (Validator)
```

**How it works:**

1. **Human (Conductor)** writes detailed system specifications in `.spec/` -- the blueprint that defines *what* to build, with module-level contracts, invariants, failure modes, and security requirements
2. **Claude (Builder)** reads the spec and generates implementation code, documenting reasoning in `.agent/claude_log.md`
3. **Codex (Validator)** reads the codebase + Claude's log, generates tests, and reviews against the spec's invariants in `.agent/codex_log.md`
4. **Loop** until the module passes validation, then move to the next module in build order

The spec is the source of truth. The code is generated from it. The quality of the system is bounded by the quality of the spec -- so the specs are written to be *executable* by AI agents with minimal ambiguity.

See [`.spec/README.md`](.spec/README.md) for the full specification and [`.agent/PROTOCOL.md`](.agent/PROTOCOL.md) for the protocol definition.

## Local Development

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # fill in DATABASE_URL, OPENAI_API_KEY
alembic upgrade head
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

## Project Structure

```
.spec/              System specifications (the blueprint)
.agent/             DADP agent logs and handoff state
src/app/
  api/              FastAPI route handlers
  auth/             JWT auth, sessions, refresh tokens
  background_jobs/  PostgreSQL-backed async job queue
  input_validation/ File + payload validation middleware
  models/           SQLAlchemy models
  observability/    Structured logging, tracing, health checks
  rate_limiter/     Token bucket rate limiting
  secrets_config/   Secret loading and rotation
  services/         Business logic (ingestion, extraction, scoring)
frontend/           React + Vite + Tailwind 4
alembic/            Database migrations
tests/              Test suite
```
