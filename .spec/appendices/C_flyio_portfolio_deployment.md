# Appendix C — Fly.io Portfolio Deployment

Parent: `README.md` · Canonical reference: `05_deployment.md` · Decision record: `appendices/B_decision_log.md` ADR-005

## C.1 Purpose

Specify the actual running deployment for the portfolio phase, as an explicit mapping against the canonical ECS reference architecture in `05_deployment.md`. This is not an alternative architecture — it's a reduced version with the tradeoffs made explicit. When a portfolio reviewer asks "would you really build this on Fly.io in production," the answer is "no, and here's exactly what I'd change."

The guiding principle: **match the canonical architecture's properties wherever possible, be explicit about where it doesn't, never pretend the reduction doesn't cost something.**

## C.2 Cost envelope

Target: $5–15/month, vs. ~$325/month for ECS reference. Breakdown at steady state:

| Component | Monthly cost | Notes |
|---|---|---|
| Fly.io app (shared-cpu-1x, 256 MB, 2 machines, `api` process) | ~$2 | Scale-to-zero not used; always-on for portfolio demos |
| Fly.io app (shared-cpu-2x, 512 MB, 1 machine, `worker` process) | ~$3 | Single worker for portfolio phase |
| Fly Postgres (development cluster, 1 GB RAM, 10 GB storage) | ~$2 | Single node, not HA |
| Tigris S3-compatible storage (Fly's S3) | ~$1 | Low volume |
| Bandwidth | ~$1 | Dominated by OpenAI egress |
| **Total** | **~$9/month** | |

OpenAI API usage is pass-through cost — same whether running on ECS or Fly. Not included in the envelope since it depends entirely on demo traffic.

## C.3 Architecture mapping

```
                           Internet
                               │
                               ▼
                      ┌────────────────┐
                      │ Fly.io edge     │ TLS termination,
                      │ (anycast)       │ automatic ACME certs
                      └────────┬────────┘
                               │
                               ▼
                      ┌────────────────┐
                      │  Fly proxy      │ Routes by app name
                      └────────┬────────┘
                               │
              ┌────────────────┴────────────────┐
              │                                 │
              ▼                                 ▼
      ┌───────────────┐                 ┌───────────────┐
      │ app: api      │                 │ app: worker   │
      │ 2 machines    │──── 6pn ────────│ 1 machine     │
      │ process=api   │  private net    │ process=worker│
      └───────┬───────┘                 └───────┬───────┘
              │                                 │
              │        ┌────────────────┐       │
              └───────▶│ Fly Postgres   │◀──────┘
                       │ single node    │
                       └────────────────┘
                       
                       ┌────────────────┐
                       │ Tigris (S3)    │
                       │ deals bucket   │
                       └────────────────┘
```

### Service-by-service mapping

| ECS reference | Fly.io equivalent | Notes |
|---|---|---|
| ALB + ACM cert | Fly.io edge + automatic certs | Built-in, no separate service |
| VPC with public/private/data subnets | Fly internal network (6pn) | No explicit subnets; isolation is at the app boundary |
| Security groups | Fly machine-to-machine via 6pn | All apps in an org can reach each other by default; firewall at app level |
| NAT Gateway | Fly egress (built-in) | No explicit NAT; outbound works out of the box |
| VPC endpoints | Not applicable | Fly doesn't have AWS-style private service endpoints |
| ECS Fargate `api` service | Fly app `api` with process group | 2 machines across regions for HA; smaller than ECS baseline |
| ECS Fargate `worker` service | Fly app `worker` with process group | 1 machine only; cost vs. availability tradeoff |
| RDS Multi-AZ Postgres | Fly Postgres dev cluster (single node) | **The biggest reduction — no HA** |
| S3 deals bucket | Tigris (S3-compatible) bucket | API-compatible; same client code, different endpoint |
| Secrets Manager | `fly secrets` | Injected as env vars at machine start |
| CloudWatch Logs | `fly logs` → Grafana Cloud (free tier) | Shipped via Fly's built-in log shipper |
| X-Ray | Grafana Tempo (Grafana Cloud free tier) | OTLP over HTTPS |
| CloudWatch Metrics | Grafana Cloud Prometheus (free tier) | Scraped from `/metrics` via Grafana Agent sidecar |

## C.4 Deliberate reductions from canonical

These are the places where the Fly.io deployment does not match the ECS reference, and the reason why each is acceptable for the portfolio phase. Each is something that would need to change before a real customer depends on the system.

### C.4.1 Single-node Postgres, not Multi-AZ

**Reduction:** Fly Postgres dev cluster runs one node. No synchronous standby.

**Impact:** An availability zone failure, a node crash, or a noisy-neighbor incident takes the database — and therefore the whole system — down. Recovery is a restore from daily snapshot, not a sub-minute failover.

**Why acceptable for portfolio:** the realistic downtime cost is "Chris has to redeploy the demo." No customer SLA, no revenue at stake. The tradeoff is $2/month vs. ~$90/month for the Multi-AZ equivalent.

**What would change for production:** move to Fly Postgres HA (two nodes with streaming replication) or migrate to the ECS RDS architecture. Cost increase: ~$20/month on Fly HA, ~$90/month on RDS Multi-AZ.

### C.4.2 Single worker machine

**Reduction:** ECS reference runs 2+ worker tasks with queue-depth autoscaling. Fly.io portfolio runs 1.

**Impact:** Worker crash → no extraction processing until Fly restarts the machine (typically 30–60s). Under sustained load, the single worker becomes a bottleneck faster than the ECS fleet would.

**Why acceptable for portfolio:** demo traffic is bursty but low-volume. Queue self-heals once the worker is back. The `modules/background_jobs.md` `CLAIMED → PENDING` reaper handles crash recovery automatically, so the single-worker limitation is availability, not correctness.

**What would change for production:** `fly scale count 2 --process-group worker` and add queue-depth-based autoscaling via the Fly metrics API. Cost increase: ~$3/month per additional worker.

### C.4.3 No WAF

**Reduction:** ECS reference includes AWS WAF with managed rule groups. Fly.io has no equivalent.

**Impact:** No application-layer protection against common exploits beyond what the app itself enforces (Input Validation, Rate Limiter). Bot traffic hits the app directly.

**Why acceptable for portfolio:** the app-layer defenses from `modules/input_validation.md` and `modules/rate_limiter.md` are the primary defenses anyway; WAF in the ECS reference is defense in depth. The portfolio system has a small attack surface (demo traffic only) and no sensitive production data.

**What would change for production:** front Fly with a CDN that offers WAF (Cloudflare, Bunny), or migrate to ECS where WAF is native.

### C.4.4 Collapsed observability stack

**Reduction:** ECS uses CloudWatch + X-Ray natively. Fly ships logs via its built-in shipper and relies on Grafana Cloud free tier for metrics and traces.

**Impact:** Grafana Cloud free tier has aggressive retention limits (14 days for logs, 30 days for metrics and traces). Long-term debugging of "what happened last month" is not possible.

**Why acceptable for portfolio:** 14 days is enough for active development and portfolio demos. Audit logs, which have stricter retention requirements, live in Postgres per `modules/observability.md` § 11 — that part of the architecture is unchanged.

**What would change for production:** either pay for Grafana Cloud Pro or migrate to CloudWatch + X-Ray on ECS.

### C.4.5 No VPC endpoints

**Reduction:** ECS reference uses VPC endpoints so AWS control-plane traffic never touches the public internet. Fly.io has no equivalent.

**Impact:** All outbound AWS-equivalent traffic (to Tigris, Grafana Cloud, OpenAI) goes over the public internet. Traffic is TLS-encrypted but the metadata is visible.

**Why acceptable for portfolio:** no AWS control-plane traffic exists in this deployment (Tigris is the storage, not S3 via IAM; Grafana Cloud replaces CloudWatch). The threat model that VPC endpoints address doesn't apply to the same degree on Fly.

**What would change for production:** moving to ECS recovers this property natively.

### C.4.6 No separate `dev` / `staging` / `prod` environments

**Reduction:** ECS reference specifies three accounts for three environments. Fly portfolio has one.

**Impact:** No pre-production validation layer. Every deploy goes to prod.

**Why acceptable for portfolio:** a bug in prod affects only the demo, not customers. The CI pipeline still runs the full test suite before deploy, which catches most issues.

**What would change for production:** add a `staging` Fly app with its own Postgres cluster, or migrate to ECS with proper account separation.

## C.5 Preserved properties

Equally important: what the Fly.io deployment does *not* compromise on. These are invariants the canonical architecture establishes that must hold regardless of where the system runs.

- **One image, two entrypoints** (ADR-004): same Docker image on Fly as on ECS, different process commands per process group. Schema drift between `api` and `worker` remains structurally impossible.
- **Secrets never in env vars at build time or in the image**: `fly secrets` injects at machine start, same contract as ECS Secrets Manager injection.
- **Transactional audit log in Postgres** (`modules/observability.md` § 11): unchanged. Audit log atomicity with state changes holds identically on Fly Postgres and on RDS.
- **PII scrubbing in-process before log emit** (ADR-007): unchanged. Scrubbing runs inside the application, not at the log shipper, so the log sink choice doesn't affect the guarantee.
- **Reversible migrations** (`00_main_spec.md` technical invariant 6): unchanged. CI enforces the invariant regardless of deployment target.
- **PDF sandbox in a subprocess with `rlimit`** (`modules/input_validation.md` § 11): unchanged. The sandbox is an in-process concern, not a deployment one.
- **Circuit breaker and retry policy for LLM calls** (`modules/extraction_service.md` § 10): unchanged.
- **All trust boundaries from `00_main_spec.md` Phase 1**: unchanged at the application layer. Deployment-layer enforcement is weaker (no IAM task roles, no security groups), but the application-layer checks still run.

The pattern: anything the application code enforces is preserved; anything the deployment layer enforces is reduced.

## C.6 Deploy procedure

Deploys from the same CI pipeline as the ECS reference, just with different targets:

```
# In CI, after image build and tests pass:
flyctl deploy --app deal-screener --image <ecr-or-registry>/deal-screener:<git_sha>
```

Fly handles the rolling deploy automatically: new machines start, health checks verify, old machines drain. The `modules/observability.md` readiness gate is honored — new machines only receive traffic once `/health/readiness` returns 200.

Database migrations run as a one-shot Fly machine before the deploy, same pattern as the ECS migration task:

```
flyctl machine run --app deal-screener \
  --command "python -m deal_screener.ops migrate" \
  --rm \
  <image>
```

Rollback is `flyctl releases` + `flyctl deploy --image <previous-sha>`. The same "rollback first, diagnose second" rule from `06_operations.md` § 6.2 applies.

## C.7 Open questions

1. **Fly Postgres HA upgrade trigger.** At what point does paying $20/month for HA become worth it over the current single-node cluster? Probably when the first real user depends on the system for anything nontrivial. Tracked for revisit once portfolio phase has a stakeholder beyond Chris.
2. **Tigris vs. AWS S3.** Tigris is S3-compatible and cheaper, but less battle-tested. If Tigris has an outage, the deals bucket is unavailable. Currently acceptable because demo data is reproducible; revisit if that changes.
3. **Multi-region on Fly.** Fly's whole value prop includes easy multi-region deployment. The portfolio version runs in one region for cost and simplicity. Whether to spread across regions is a decision that depends on demo audience latency, not on availability math. Defer until a real user complains.
4. **Grafana Cloud retention cliff.** 14-day log retention is fine for active development, painful for "what happened three weeks ago" debugging during portfolio review season. Pay for Grafana Cloud Pro or accept the cliff? Defer until it bites.
5. **Portfolio-to-production migration path.** If this project ever needs to become a real product, the migration from Fly.io to ECS is nontrivial but bounded — same image, same secrets contract, same observability contract. Worth writing the migration runbook now while the design is fresh, or defer until the need is concrete? Leaning toward defer — Conductor call.