# 05 — Deployment: ECS Reference Architecture

Phase 5 of the System Thinking Framework. Parent: `README.md` · Next: `06_operations.md` · Related: `appendices/C_flyio_portfolio_deployment.md`

## 1. Purpose

Specify the production-grade target deployment for the Deal Screening Agent on AWS: ECS Fargate + RDS Postgres + ALB + S3 + Secrets Manager + CloudWatch. This document is the canonical architecture. The actual portfolio-phase deployment runs on Fly.io for cost reasons and is specified in `appendices/C_flyio_portfolio_deployment.md` as an explicit mapping against this reference.

This document answers: *where does the code run, what does it talk to, how does traffic reach it, how do secrets get in, how does it scale, and how does it fail.* Runbooks, incident response, and backup/restore procedures live in `06_operations.md`.

## 2. Scope & Non-Scope

### In scope

- AWS service selection and the rationale behind each choice
- Network topology: VPC, subnets, security groups, routing
- Compute: ECS task definitions, service configurations, autoscaling policies
- Data stores: RDS Postgres sizing and configuration, S3 bucket layout
- Secrets management: how Secrets Manager integrates with ECS task definitions
- Observability wiring: CloudWatch Logs, OpenTelemetry collector, metrics backend
- CI/CD pipeline: image build, push, and ECS service update
- Cost envelope and scaling assumptions for the reference architecture
- Disaster recovery posture (RPO/RTO targets)

### Out of scope

- Runbooks and incident response — those live in `06_operations.md`
- Alert rule definitions — those live in `06_operations.md`
- The Fly.io mapping — that lives in `appendices/C_flyio_portfolio_deployment.md`
- Application-level configuration (env vars, feature flags) — those are owned by `modules/secrets_config.md`
- Tenant onboarding and billing infrastructure — out of v1 scope entirely

## 3. Deployment Topology

```
                          Internet
                             │
                             ▼
                    ┌────────────────┐
                    │  Route 53       │ DNS
                    └────────┬───────┘
                             │
                             ▼
                    ┌────────────────┐
                    │   ACM cert      │ TLS 1.3
                    └────────┬───────┘
                             │
                             ▼
         ┌──────────────────────────────────────┐
         │  Application Load Balancer (ALB)     │
         │  Public subnets, 2 AZs               │
         └──────────┬───────────────────────────┘
                    │ (HTTP, inside VPC)
                    ▼
    ┌───────────────────────────────────────────┐
    │             Private App Subnets            │
    │  ┌─────────────────┐  ┌─────────────────┐ │
    │  │ ECS Service:    │  │ ECS Service:    │ │
    │  │   api           │  │   worker        │ │
    │  │  (Fargate)      │  │  (Fargate)      │ │
    │  │  Dashboard API  │  │  Background     │ │
    │  │  Ingestion      │  │  Jobs +         │ │
    │  │  Auth           │  │  Extraction +   │ │
    │  │                 │  │  Scoring        │ │
    │  └─────────────────┘  └─────────────────┘ │
    └────┬──────────────────────────┬───────────┘
         │                          │
         ▼                          ▼
    ┌──────────┐              ┌──────────┐
    │   RDS    │              │   S3     │
    │ Postgres │              │ deals/   │
    │ Multi-AZ │              │ (KMS)    │
    └──────────┘              └──────────┘

    VPC endpoints (no NAT for AWS traffic):
    - S3 (gateway endpoint)
    - Secrets Manager (interface)
    - CloudWatch Logs (interface)
    - ECR (interface)

    Egress to OpenAI via NAT Gateway (one per AZ in prod).
```

### Service grouping rationale

The ten modules from `01_decomposition.md` collapse into **two** ECS services, not ten. The rule is: modules that share a request lifecycle run in the same process, and modules that cross a time boundary run in different services.

- **`api` service** runs the synchronous request path: Dashboard API, Ingestion Service, Auth Service, plus the cross-cutting middleware (Rate Limiter, Input Validation, Observability). Tasks are stateless and horizontally scalable. ALB health checks hit `/health/readiness` from the Observability module.
- **`worker` service** runs the Background Jobs worker process, which in turn dispatches to Extraction Service and Scoring Engine handlers in-process (per `01_decomposition.md`: "Sync call (in-process)" between Extraction and Scoring). Tasks claim jobs from the `background_jobs` table and have no inbound network listener.

Splitting these into ten services would multiply the deployment surface without buying isolation the module boundaries don't already give us. Combining them into one service would couple API latency to LLM latency, which the async decomposition specifically exists to prevent.

### Why Fargate, not EC2 or Lambda

- **Not EC2**: AMI management, patching, and capacity planning are operational tax we don't want to pay at this scale. The break-even against Fargate is somewhere around 60% sustained utilization — we're nowhere near that.
- **Not Lambda**: the worker runs handlers that hit a 30s LLM timeout plus retries, and cold starts plus the 15-minute Lambda cap interact badly with that. Lambda would also split observability across two runtimes.
- **Fargate**: right-sized per service, no host management, native ECS autoscaling, and a clean mental model ("a service is a fleet of identical tasks").

## 4. Network Topology

### VPC layout

One VPC per environment (`staging`, `prod`), `10.0.0.0/16`:

| Subnet | CIDR | Purpose | AZs |
|---|---|---|---|
| `public-a`, `public-b` | `10.0.0.0/24`, `10.0.1.0/24` | ALB, NAT Gateways | 2 |
| `app-a`, `app-b` | `10.0.10.0/24`, `10.0.11.0/24` | ECS tasks (api + worker) | 2 |
| `data-a`, `data-b` | `10.0.20.0/24`, `10.0.21.0/24` | RDS | 2 |

Two AZs is the minimum for RDS Multi-AZ and ALB availability. Three AZs is over-provisioned for the traffic profile and adds cross-AZ data transfer cost with no resilience gain at this scale.

### Security groups

Named by what they protect, not by what's inside them:

- `sg-alb`: ingress 443 from `0.0.0.0/0`, egress to `sg-app` on the app port
- `sg-app`: ingress from `sg-alb` on app port only, egress to `sg-data` on 5432, egress to VPC endpoints, egress to `0.0.0.0/0` via NAT for OpenAI
- `sg-data`: ingress from `sg-app` on 5432 only, no egress rules
- `sg-vpce`: ingress from `sg-app` on 443 only

The `sg-data` group has no egress rules by design: the database has no business initiating connections anywhere.

### VPC endpoints

S3, Secrets Manager, CloudWatch Logs, and ECR are reached via VPC endpoints, not through the NAT Gateway. This is both a cost decision (NAT Gateway data processing is a real line item at volume) and a security decision (AWS control-plane traffic never touches the public internet).

The only traffic that does go through NAT is OpenAI API calls from the worker service. That's a single external dependency with a bounded request rate (capped by the extraction quota from `modules/extraction_service.md` § 11), which makes NAT costs predictable.

### TLS termination

TLS terminates at the ALB using an ACM-managed certificate. Between ALB and ECS tasks, traffic is plain HTTP inside the VPC. This matches the trust boundary in `00_main_spec.md` Phase 1: "API → Database" is Trusted → Trusted, and so is ALB → ECS inside the same VPC. In-VPC TLS adds complexity for a marginal threat (an attacker who has already compromised the VPC network), which is not where our risk budget should go first.

## 5. Compute: ECS Task Definitions

### `api` service

- **Image**: `<account>.dkr.ecr.<region>.amazonaws.com/deal-screener-api:<git_sha>`
- **Launch type**: Fargate
- **CPU / memory**: 1 vCPU / 2 GB (baseline); scales to 2 vCPU / 4 GB per task under load
- **Desired count**: 2 (one per AZ) minimum, autoscale 2–10
- **Port mapping**: container port 8000 → ALB target group
- **Health check**: `GET /health/readiness` every 10s, 3 consecutive failures to unhealthy
- **Task role**: `api-task-role` — permissions scoped to exactly: Secrets Manager `GetSecretValue` on its own secrets, S3 `GetObject`/`PutObject` on the deals bucket under `tenants/*/deals/*`, CloudWatch Logs `PutLogEvents`
- **Execution role**: `api-execution-role` — ECR pull, CloudWatch Logs create, Secrets Manager fetch at container start
- **Log driver**: `awslogs`, group `/ecs/deal-screener/api`, retention 30 days

### `worker` service

- **Image**: same repo, `deal-screener-worker` tag — or the same image with a different entrypoint (see § 8)
- **Launch type**: Fargate
- **CPU / memory**: 2 vCPU / 4 GB (LLM work is network-bound but the PDF sandbox subprocess wants headroom)
- **Desired count**: 2 minimum, autoscale 2–8 based on queue depth
- **Port mapping**: none — workers have no inbound listener
- **Health check**: ECS container-level `exec`-based liveness probe running `python -m background_jobs.worker --healthcheck`
- **Task role**: `worker-task-role` — everything the api role has, *plus* read/write on `background_jobs` and `deal_extractions`, plus NAT egress allowed for OpenAI
- **Stop timeout**: 60 seconds — matches Observability's `OBS_DRAIN_TIMEOUT_S` plus headroom for handler completion on SIGTERM

### Task role separation rationale

`api-task-role` and `worker-task-role` are deliberately different even though they share an AWS account. The API role cannot call OpenAI (no egress to that destination is strictly enforced at the IAM level where possible, plus at the security group level definitely). The worker role cannot bind inbound listeners because it has no service-level port mapping. This is defense in depth against a compromise of either service: neither one gets the other's capabilities by default.

### Autoscaling

- **`api`**: target tracking on ALB `RequestCountPerTarget`, target 50 rps per task. Scale-out cooldown 60s, scale-in cooldown 300s. The asymmetric cooldowns exist because scaling out under load is cheap and scaling in too fast during a traffic valley causes thrash.
- **`worker`**: target tracking on a CloudWatch custom metric `jobs.queue_depth` (emitted by the Observability module per `modules/observability.md` § 4), target 20 pending jobs per task. Same cooldown asymmetry.

Queue depth, not CPU, is the right worker scaling signal. The worker is network-bound on OpenAI — CPU stays low even when the queue is hours deep, and CPU-based scaling would never fire.

## 6. Data Stores

### RDS Postgres

- **Engine**: PostgreSQL 16
- **Instance class**: `db.t4g.medium` baseline (2 vCPU, 4 GB), upgradable to `db.m7g.large` for production load
- **Storage**: gp3, 100 GB initial, autoscaling to 500 GB
- **Multi-AZ**: yes (primary in `data-a`, standby in `data-b`)
- **Backup**: automated daily snapshots, 7-day retention, plus point-in-time recovery (PITR) enabled
- **Encryption at rest**: KMS with a customer-managed key (`alias/deal-screener-rds`)
- **Parameter group**: custom, with `log_min_duration_statement = 500` (log queries over 500ms), `log_connections = on`, `log_disconnections = on`
- **Connection pooling**: RDS Proxy in front of the primary — the `api` service has bursty connection patterns under autoscaling and RDS Proxy smooths that out without making the application handle pool management

Two DB roles are provisioned per the security model in `modules/background_jobs.md` § 11:

- `app_rw`: used by both `api` and `worker` tasks. INSERT and SELECT on everything, UPDATE on non-terminal state rows. No DELETE on `background_jobs` or `deal_audit_log`.
- `ops_admin`: used only by the admin CLI and migration runner. Full DML plus DDL. Credentials in a separate secret, rotated more aggressively.

This separation is what makes `modules/observability.md` invariant "audit log has no UPDATE or DELETE permission granted to the application DB role" actually true at the infrastructure layer, not just in code.

### S3 — deals bucket

- **Bucket**: `deal-screener-deals-<env>-<account-id>`
- **Versioning**: enabled (protects against accidental overwrites; also supports the "hard delete within 30 days" security invariant via lifecycle rules, not against it — see below)
- **Encryption**: SSE-KMS with a customer-managed key (`alias/deal-screener-s3`)
- **Public access**: fully blocked via bucket-level Block Public Access
- **Key layout**: `tenants/{tenant_id}/deals/{deal_id}/source.pdf` — matches Ingestion's invariant 3 exactly
- **Lifecycle rules**:
  - Transition to `STANDARD_IA` after 90 days (deals are rarely re-read after scoring)
  - Expire noncurrent versions after 30 days (bounds the versioning blast radius and makes hard-delete-within-30-days achievable)
- **Access logging**: server access logs to a separate `deal-screener-access-logs` bucket

### S3 — observability artifacts bucket

Separate bucket for prompt/response debug logs (per `modules/extraction_service.md` § 11, retention 30 days). Keeping this separate from the deals bucket prevents a bug in one retention policy from corrupting the other.

- **Bucket**: `deal-screener-obs-<env>-<account-id>`
- **Lifecycle**: delete objects after 30 days, no exceptions

## 7. Secrets Management

Secrets Manager holds every credential. There is no secret in environment variables set at task-definition time, no secret baked into images, no secret in git.

| Secret | Consumer | Rotation |
|---|---|---|
| `deal-screener/<env>/db/app_rw` | `api`, `worker` tasks | 30 days, automatic via Secrets Manager Lambda |
| `deal-screener/<env>/db/ops_admin` | Admin CLI, migration runner | 7 days, manual trigger |
| `deal-screener/<env>/openai/api_key` | `worker` only | Manual on incident, no automatic rotation (OpenAI keys don't rotate cleanly) |
| `deal-screener/<env>/jwt/signing_key` | `api` only | 90 days with overlap window per `modules/auth_service.md` |
| `deal-screener/<env>/webhook/sendgrid_hmac` | `api` only | 90 days with 7-day overlap per `modules/ingestion_service.md` § 11 |
| `deal-screener/<env>/otel/collector_token` | `api`, `worker` | 90 days |

ECS task definitions reference secrets via the `secrets` field, which injects them as environment variables *at container start*. The Secrets & Config module is then responsible for reading them into typed config objects and never logging them — the Observability PII scrubber catches this anyway as a second line of defense (`modules/observability.md` § 11).

Rotation without downtime works because the application reads secrets via the Secrets & Config module's periodic refresh, not just at startup. A rotation triggers a fetch of the new value within the refresh interval (default 5 minutes), and both old and new values are valid during the overlap window.

## 8. Container Images & CI/CD

### Image strategy

**One image, two entrypoints.** Both services ship from the same Dockerfile and the same ECR repository. The ECS task definitions differ only in the command:

- `api` command: `uvicorn deal_screener.api.main:app --host 0.0.0.0 --port 8000`
- `worker` command: `python -m background_jobs.worker --concurrency 4`

This matters because it means the `api` and `worker` can never drift in library versions, schema definitions, or handler registrations. A schema evolution in an Extraction Pydantic model is deployed to both services atomically or to neither. The `modules/background_jobs.md` dead-letter reason `SCHEMA_EVOLUTION` exists precisely for the case where this invariant fails — and this image strategy is the first line of defense against it happening.

### Base image

`python:3.12-slim-bookworm` with:

- Non-root user `app` (uid 10001)
- No build tools in the final stage (multi-stage build)
- Read-only root filesystem, with `/tmp` as a tmpfs mount for the PDF sandbox scratch dir (per `modules/input_validation.md` § 11)
- `HEALTHCHECK` directive for container runtimes that honor it

### CI/CD pipeline

GitHub Actions, triggered on push to `main` and on tags:

1. **Lint + unit tests** (parallel): ruff, mypy, pytest unit suites
2. **Build image**: docker buildx, push to ECR with tag `<git_sha>`
3. **Integration tests**: spin up ephemeral Postgres via testcontainers, run the cross-module integration suite from Batch 4
4. **Security scan**: `trivy image` against the built image, fail on HIGH or CRITICAL CVEs
5. **Deploy to staging**: `aws ecs update-service` with the new image tag on both services, wait for stable
6. **Smoke tests**: hit the staging ALB with the end-to-end test suite from `01_decomposition.md` step 14
7. **Manual gate → prod**: staging deploy is automatic on every main commit; prod deploy requires a git tag matching `v*` and a human approval in GitHub Actions
8. **Deploy to prod**: same update-service flow, but with ECS deployment circuit breaker enabled — if the new task set fails health checks, ECS automatically rolls back

The prod deployment uses ECS rolling deployment, not blue-green. Blue-green is the right choice once we need zero-downtime guarantees for revenue-critical traffic; at v1 the rolling deployment with health-check-gated cutover and deployment circuit breaker is sufficient and much simpler. Revisit in `06_operations.md` under deployment runbooks when the business case changes.

### Migrations

Database migrations run as a one-shot ECS task triggered by the pipeline *before* the service update. The migration task uses the `ops_admin` role, not `app_rw`. Migrations are reversible per `00_main_spec.md` technical invariant 6, which is enforced by a CI check that every `up` migration has a corresponding `down` migration that CI applies and reverts on a scratch DB.

If the migration task fails, the service update is skipped and the pipeline fails loudly. There is no scenario where the new image runs against an un-migrated database.

## 9. Observability Wiring

The application-level contract is already defined in `modules/observability.md`. This section specifies how that contract maps onto AWS services.

- **Logs**: stdout JSON → `awslogs` driver → CloudWatch Logs group per service. Retention 30 days on operational logs (matches `modules/observability.md` § 11). Log Insights queries are the primary debugging interface.
- **Metrics**: the `/metrics` Prometheus endpoint on each `api` task is scraped by an AWS Distro for OpenTelemetry (ADOT) collector running as a sidecar container in each task. The sidecar pushes to CloudWatch Metrics via the EMF embedded metric format. The worker service runs the same sidecar and pushes the same metric taxonomy.
- **Traces**: OTLP exporter in the application targets the ADOT sidecar; sidecar forwards to AWS X-Ray. This gives us the connected trace across Ingestion → Background Jobs → Extraction → Scoring that `modules/observability.md` invariant 3 requires.
- **Audit log**: stays in Postgres, per `modules/observability.md` § 11. CloudWatch is for operational data only.
- **Alarms**: defined in `06_operations.md`, not here — per the non-scope section above.

### Why a sidecar collector, not direct push from the app

Two reasons. First, the ADOT sidecar owns the buffering and retry logic for telemetry export, which means the application never blocks on CloudWatch latency. Second, rotating credentials for the telemetry backend happens at the sidecar layer via the task role, not by touching application config.

The `/metrics` endpoint binding per `modules/observability.md` invariant 7 ("never exposed on a public listener") is satisfied because the sidecar scrapes over localhost inside the task's network namespace — the port is never exposed in the task definition's port mappings.

## 10. Cost Envelope

Rough monthly cost for the reference architecture at idle-to-light-production load (≈ 1000 deals/day, single tenant, one region):

| Component | Monthly cost (USD) | Notes |
|---|---|---|
| ECS Fargate `api` (2× 1 vCPU / 2 GB, always on) | ~$35 | Spot not used for API; interruption tolerance is low |
| ECS Fargate `worker` (2× 2 vCPU / 4 GB, always on) | ~$70 | Could use Fargate Spot for cost savings; see Open Questions |
| RDS `db.t4g.medium` Multi-AZ + 100 GB gp3 | ~$90 | The biggest single line item |
| ALB | ~$20 | Flat + LCU |
| NAT Gateway (1 per AZ × 2 AZs) | ~$65 | Data processing charges scale with OpenAI traffic |
| S3 (deals + obs + access logs) | ~$5 | Dominated by request costs, not storage |
| CloudWatch Logs + Metrics + X-Ray | ~$25 | Scales with log volume; bounded by retention |
| Secrets Manager | ~$5 | $0.40 per secret per month |
| Data transfer (out to OpenAI + clients) | ~$10 | Dominated by OpenAI traffic |
| **Total** | **~$325/month** | |

This is the reference architecture, not the portfolio deployment. `appendices/C_flyio_portfolio_deployment.md` targets $5–15/month by collapsing onto Fly.io managed Postgres and a single Fly app with internal routing between processes — an explicit tradeoff of durability and AZ isolation for cost.

The cost envelope is a planning tool, not a budget commitment. Actual spend will vary with OpenAI token consumption, which dominates variable cost once deal volume ramps.

## 11. Disaster Recovery

- **RPO (Recovery Point Objective)**: 5 minutes. Driven by RDS PITR, which continuously ships WAL to S3. Anything less tight requires synchronous cross-region replication, which we're not buying.
- **RTO (Recovery Time Objective)**: 1 hour. Driven by the time to restore RDS from PITR into a new instance and redeploy the ECS services pointing at it.
- **Backup testing**: a scheduled Lambda restores the latest PITR snapshot into an isolated VPC every Sunday and runs a schema-level integrity check. Failures page on-call. This is the only way to know backups actually work; untested backups are folklore.
- **Region failure**: out of scope for v1. The reference architecture is single-region. Multi-region active-passive is a legitimate future need for enterprise tier but adds substantial complexity to data consistency (particularly around the `deal_audit_log` append-only guarantee). Tracked as an Open Question.
- **Account compromise**: the incident response runbook lives in `06_operations.md`. The deployment-layer defense is least-privilege task roles (§ 5), customer-managed KMS keys with CloudTrail logging on key use, and Secrets Manager rotation.

Disaster recovery procedures — the actual step-by-step of "the region is on fire, what do I type" — are in `06_operations.md`. This section specifies the *posture*, not the playbook.

## 12. Environments

Three environments, each in its own AWS account under an AWS Organization:

| Env | Account | Purpose | Data |
|---|---|---|---|
| `dev` | `deal-screener-dev` | Developer sandbox, ephemeral | Synthetic only |
| `staging` | `deal-screener-staging` | Pre-production, mirrors prod topology | Synthetic + scrubbed prod samples |
| `prod` | `deal-screener-prod` | Production | Real tenant data |

Separate accounts (not separate VPCs in one account) because:

- Blast radius of IAM misconfiguration stops at the account boundary
- CloudTrail and billing are per-account, which makes cost attribution and audit straightforward
- Deleting a dev environment is `aws organizations close-account`, not a multi-hour teardown script

`dev` and `staging` use downsized resources (single-AZ RDS, single-task ECS services) — the topology is identical to prod so deployment bugs surface in staging, but the capacity is the minimum that still exercises the architecture.

Cross-account image promotion happens via ECR replication from the CI account into `staging` and `prod`. Images are immutable and identified by git SHA; the same image that passes staging smoke tests is the exact image promoted to prod.

## 13. Trust & Security Boundaries at the Deployment Layer

This is the deployment-layer view of the trust boundaries from `00_main_spec.md` Phase 1. Application-layer enforcement is specified in the individual module specs; the table below is about what the *infrastructure* contributes on top of that.

| Boundary | Infrastructure enforcement |
|---|---|
| Internet → ALB | ACM TLS 1.3 termination, WAF rule set (rate-based rules, AWS managed rule groups for common exploits), Shield Standard |
| ALB → `api` tasks | Security group restricts ingress to `sg-alb` only; ALB listener rules enforce path-based routing |
| `api` → `worker` | No direct path exists; they communicate only via the `background_jobs` table in RDS. Security groups have no rule permitting direct task-to-task traffic. |
| `api` / `worker` → RDS | `sg-data` accepts only from `sg-app` on 5432; RDS Proxy enforces IAM auth; `app_rw` role has constrained DML permissions |
| `worker` → OpenAI | Egress via NAT Gateway only; application-layer circuit breaker and quota enforcement per `modules/extraction_service.md` § 10 |
| `api` / `worker` → S3 | Task role scoped to bucket prefix `tenants/*/deals/*`; bucket policy blocks public access unconditionally |
| `api` / `worker` → Secrets Manager | Task role scoped to secrets under `deal-screener/<env>/*`; secrets are fetched at task start and refreshed in-process |
| Admin CLI → RDS | Separate `ops_admin` role with a separate credential in a separate secret; access is audited via CloudTrail on Secrets Manager `GetSecretValue` |

## 14. Open Questions

1. **Fargate Spot for `worker`.** Workers tolerate interruption (jobs re-claim after `claim_expires_at` per `modules/background_jobs.md` § 5), so Fargate Spot could cut ~50% off worker compute cost. The cost is increased tail latency on extraction under Spot reclamations. Decide after first load profile is captured and we can measure the latency impact.
2. **Multi-region DR.** Single-region is fine for v1. Enterprise tier likely requires active-passive across regions. The hard part isn't infrastructure — it's maintaining the `deal_audit_log` append-only invariant across regions under failover. Defer until enterprise tier is a committed roadmap item.
3. **RDS → Aurora Serverless v2.** Aurora Serverless v2 would give us elastic scaling and faster failover at higher baseline cost. Not justified at v1 traffic; revisit if the t4g.medium becomes CPU-bound on read load.
4. **Separate VPCs for `api` and `worker`.** Currently they share a VPC and a subnet group. A compromise of one gives network-level reach to the other. Full separation would require VPC peering and complicate observability. Leaning toward keeping them shared — the IAM-layer and security-group-layer separation is sufficient for the threat model in `00_main_spec.md` — but flagging it for Conductor review.
5. **WAF rule set scope.** AWS managed rule groups catch common exploits but generate false positives on legitimate multipart uploads. Tuning the rule set without weakening it is a judgment call. Start with core rules only, add managed rule groups one at a time after measuring false-positive rate in staging.
6. **CloudWatch vs Grafana Cloud for metrics.** CloudWatch Metrics is the default in this document because it has zero operational cost, but query ergonomics are worse than Grafana and cardinality limits are tighter. A later migration to Grafana Cloud (or self-hosted Prometheus + Grafana on EKS) is plausible; defer until query frustration or cardinality walls actually hit.
7. **Image signing.** Cosign-signed images with ECS pulling via a verifier would close the "what if the image registry is compromised" gap. Adds CI complexity; the break-even is unclear until we have a concrete threat. Defer.
8. **Per-tenant encryption keys.** Enterprise tier may demand customer-managed KMS keys per tenant for the deals bucket. The S3 key layout already supports it (tenant prefix), but the IAM and KMS plumbing is nontrivial. Defer until a specific tenant asks.