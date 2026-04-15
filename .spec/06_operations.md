# 06 — Operations: Runbooks, Alerts, Incidents, Recovery

Phase 5 companion doc. Parent: `README.md` · Related: `05_deployment.md`, `modules/observability.md`, `modules/background_jobs.md`

## 1. Purpose

Specify how the system is operated once it is running: what signals wake someone up, what that person does next, how deployments roll forward and back, how backups are verified, and how incidents are run. This document is the playbook — not the architecture. The architecture lives in `05_deployment.md` and the individual module specs.

Every runbook in this document is written to be executable by an on-call engineer at 3am with one screen and no context. That means: exact commands, exact dashboards, exact decision criteria, and an explicit "stop here and escalate" condition for each path.

## 2. Scope & Non-Scope

### In scope

- Alert rule definitions (the ones `05_deployment.md` § 9 deferred here)
- On-call rotation and escalation paths
- Runbooks for the failure modes declared in every module spec
- Deployment procedures: normal rollout, rollback, emergency hotfix
- Backup verification and restore procedures (the *how*, not the *posture*)
- Incident response: severities, declaration, communication, postmortem
- Routine operational tasks: secret rotation, certificate renewal, capacity review

### Out of scope

- Infrastructure topology — see `05_deployment.md`
- Application-layer failure behavior — see individual module specs' § 10
- Alert delivery configuration (PagerDuty routing, Slack webhooks) — deployment detail, not spec
- Org-level incident response policy (legal, PR, customer comms) — company policy, not system spec

## 3. Severity Levels

Four severities, deliberately few. The boundary between SEV-2 and SEV-3 is the most commonly-misused one, so it gets the most explicit definition.

| Severity | Definition | Response | Examples |
|---|---|---|---|
| **SEV-1** | Revenue-path broken, data at risk, or customer data exposed | Page immediately, 24/7, all hands | Upload endpoint returning 5xx; tenant isolation breach; RDS primary down; OpenAI API key leaked |
| **SEV-2** | Core functionality degraded but not broken; workaround exists | Page during business hours, best-effort off-hours | Queue depth persistently high; extraction failure rate > 20%; elevated API latency; single-AZ outage (still serving) |
| **SEV-3** | Non-core functionality degraded or single-tenant issue | Ticket, address within 1 business day | Prompt/response logging backed up; metrics scrape intermittent; one tenant reports slow dashboard |
| **SEV-4** | Cosmetic, noise, or future risk | Ticket, address opportunistically | Log line volume trending up; dashboard chart label wrong; alert fired once and self-resolved |

**The SEV-2 / SEV-3 boundary**: if the user-facing impact is "deals pile up but nothing is lost and they will process when the issue clears," it's SEV-2. If the user-facing impact is "one user notices something weird but the system is correct," it's SEV-3. "Customer submitted a support ticket" is not by itself a severity — severity is set by impact, not by who noticed.

## 4. Alert Rules

Every alert has a name, a query, a threshold, a severity, a runbook link, and a rationale. Alerts without runbooks are banned — an alert you can't act on is noise.

### Request path (`api` service)

| Alert | Query | Threshold | Severity | Runbook |
|---|---|---|---|---|
| `api.error_rate_high` | `rate(http_requests{status=~"5.."}[5m]) / rate(http_requests[5m])` | > 2% for 5 min | SEV-1 | § 5.1 |
| `api.latency_p99_high` | `histogram_quantile(0.99, http_request_duration_ms)` | > 2000ms for 10 min | SEV-2 | § 5.2 |
| `api.health_check_failing` | ALB target health | Any target unhealthy > 3 min | SEV-2 | § 5.3 |
| `api.upload_reject_rate_high` | `rate(ingestion.rejected[5m]) / rate(ingestion.attempts[5m])` | > 20% for 10 min | SEV-2 | § 5.4 |

### Background jobs

| Alert | Query | Threshold | Severity | Runbook |
|---|---|---|---|---|
| `jobs.queue_depth_high` | `jobs.queue_depth{job_type="extraction"}` | > 200 for 10 min | SEV-2 | § 5.5 |
| `jobs.dead_letter_fired` | `increase(jobs.dead_lettered[1m])` | > 0 | SEV-2 | § 5.6 |
| `jobs.claim_age_high` | `jobs.claim_age_seconds` | > 600 | SEV-2 | § 5.7 |
| `jobs.reaper_stalled` | `time() - jobs.reaper_last_run_timestamp` | > 300 | SEV-1 | § 5.7 |

### Extraction service

| Alert | Query | Threshold | Severity | Runbook |
|---|---|---|---|---|
| `extraction.failure_rate_high` | `rate(extraction.failed[15m]) / rate(extraction.attempts[15m])` | > 20% for 15 min | SEV-2 | § 5.8 |
| `extraction.llm_circuit_breaker_open` | `extraction.circuit_breaker_state == 1` | Open for 2 min | SEV-1 | § 5.9 |
| `extraction.cost_spike` | `increase(extraction.cost_usd[1h])` | > 2× 7-day hourly avg | SEV-2 | § 5.10 |
| `extraction.openai_auth_failure` | `rate(extraction.auth_errors[1m])` | > 0 | SEV-1 | § 5.11 |

### Data layer

| Alert | Query | Threshold | Severity | Runbook |
|---|---|---|---|---|
| `rds.cpu_high` | RDS `CPUUtilization` | > 80% for 15 min | SEV-2 | § 5.12 |
| `rds.storage_low` | RDS `FreeStorageSpace` | < 20% free | SEV-2 | § 5.12 |
| `rds.connections_high` | RDS `DatabaseConnections` | > 80% of max | SEV-2 | § 5.12 |
| `rds.replica_lag_high` | RDS `ReplicaLag` | > 30s | SEV-1 | § 5.13 |
| `s3.4xx_spike` | S3 `4xxErrors` on deals bucket | > 10/min | SEV-2 | § 5.14 |

### Security

| Alert | Query | Threshold | Severity | Runbook |
|---|---|---|---|---|
| `security.sandbox_crash_spike` | `rate(sandbox.crashes[60s])` | > 5 in 60s | SEV-1 | § 5.15 |
| `security.scrubber_key_hits` | `logging.scrub_hits{pattern="openai_key"}` | > 0 | SEV-1 | § 5.16 |
| `security.tenant_mismatch` | `rate(extraction.tenant_mismatch[5m])` | > 0 | SEV-1 | § 5.17 |
| `security.auth_failures_spike` | `rate(auth.failures[5m])` | > 50/min | SEV-2 | § 5.18 |

### Meta

| Alert | Query | Threshold | Severity | Runbook |
|---|---|---|---|---|
| `meta.backup_restore_test_failed` | Weekly restore test | Non-success | SEV-1 | § 7.3 |
| `meta.alert_pipeline_silent` | `time() - alerts.last_fired_timestamp` | > 24h across all services | SEV-3 | § 5.19 |

The `meta.alert_pipeline_silent` alert exists because a silent alert pipeline is indistinguishable from a healthy system until it isn't. If nothing has fired for 24 hours — including routine ones like low-priority housekeeping alerts — something is wrong with the alerting itself.

## 5. Runbooks

Each runbook is structured: **Symptom → Immediate Actions → Diagnose → Mitigate → Resolve → Escalate**. The "Escalate" path is mandatory and exists so on-call never gets stuck in diagnosis loops.

### 5.1 `api.error_rate_high`

**Symptom:** 5xx rate above 2% on the `api` service.

**Immediate actions (first 2 min):**
1. Open the API service CloudWatch dashboard.
2. Check deploy history: was there a deploy in the last 30 min? If yes, **roll back immediately** (§ 6.2) and then diagnose — do not diagnose first.
3. Check RDS health in the dashboard. If RDS is in failover or high CPU, jump to § 5.12.

**Diagnose (next 5 min):**
- Log Insights query: `fields @timestamp, event, fields.error | filter level = "ERROR" | stats count() by event | sort count desc`
- If one `event` dominates, read its handler code and check the last change.
- If errors are distributed across events, suspect a shared dependency (DB, secrets, OpenAI auth).

**Mitigate:**
- Deploy-related: roll back (§ 6.2).
- DB-related: jump to § 5.12.
- Secret-related: check Secrets Manager CloudTrail for recent `PutSecretValue`; if a rotation broke something, restore the previous version via Secrets Manager console and force task restart (`aws ecs update-service --force-new-deployment`).
- Unknown: scale `api` to 2× current desired count to buy headroom, declare SEV-1, escalate.

**Escalate:** after 15 min without mitigation, or immediately if diagnosis points outside the `api` service.

### 5.2 `api.latency_p99_high`

**Symptom:** p99 latency above 2s for 10 min.

**Immediate actions:** check if RDS CPU is elevated (§ 5.12); check if ALB target count is below desired (autoscaling may be stuck).

**Diagnose:** X-Ray trace search for slow spans on the `api` service — group by operation. A single slow endpoint is usually a missing index or a new query; broad slowness is usually a capacity or downstream issue.

**Mitigate:** scale `api` manually if autoscaling hasn't caught up; if a specific endpoint is slow, feature-flag it off via config if possible, otherwise declare SEV-2 and escalate.

### 5.3 `api.health_check_failing`

**Symptom:** ALB reports unhealthy targets.

**Immediate actions:** check ECS service events for task stop reasons. Common causes: OOMKilled (bump memory), secret fetch failure at startup (check Secrets Manager), image pull failure (check ECR).

**Diagnose:** if tasks are starting but failing readiness, exec into a task: `aws ecs execute-command --task <id> --command "/bin/sh" --interactive` and check `GET localhost:8000/health/readiness` — the response body indicates which subsystem is not ready.

**Mitigate:** if Observability's readiness gate is blocking (exporters not connected), check OTLP collector status. If DB is blocking, jump to § 5.12.

### 5.4 `api.upload_reject_rate_high`

**Symptom:** Ingestion rejecting > 20% of uploads.

**Diagnose:** Log Insights on `input_validation.rejected` grouped by `reason`. If one reason dominates (e.g., `MIME_MAGIC_MISMATCH`), suspect a client-side bug or a new upload source sending wrong content types. If `DECOMPRESSION_BOMB` spikes, suspect an active attack — jump to § 5.15 escalation path.

**Mitigate:** legitimate client bug → contact the affected tenant; attack → declare SEV-1, involve security.

### 5.5 `jobs.queue_depth_high`

**Symptom:** Extraction queue depth > 200 sustained.

**Immediate actions:**
1. Check worker task count vs desired — is autoscaling firing?
2. Check `extraction.llm_circuit_breaker_open` — if open, jump to § 5.9.
3. Check OpenAI status page.

**Diagnose:** Log Insights on the `worker` service for `extraction.failed` with breakdown by `reason`. If `LLM_TIMEOUT` dominates, OpenAI is slow. If `STORAGE_UNAVAILABLE` dominates, jump to § 5.14.

**Mitigate:** manually scale worker desired count up. If the cause is OpenAI slowness and not recoverable in minutes, declare SEV-2, post a status update, let the queue drain naturally.

**Do not** clear the queue or mark jobs as succeeded to make the alert go away. The jobs represent real user work and the queue is self-healing.

### 5.6 `jobs.dead_letter_fired`

**Symptom:** A job moved to `DEAD_LETTERED`.

**Immediate actions:** `python -m background_jobs.admin list-dead-letter --since 1h` — read the `last_error` field for each row.

**Diagnose by reason:**
- `SCHEMA_EVOLUTION`: a deploy went out that changed a Pydantic schema without a migration plan. Check deploy history. The fix is code, not ops — file a P0 bug, leave the job dead-lettered until the fix ships, then retry via admin CLI.
- `UNKNOWN_JOB_TYPE`: deployment version skew between `api` and `worker`. This should be impossible with the one-image-two-entrypoints strategy from `05_deployment.md` § 8 — if you see it, the image strategy broke, escalate immediately.
- `TENANT_MISMATCH`: security event, jump to § 5.17.
- `INSUFFICIENT_FIELDS` / `SCHEMA_VIOLATION` from Extraction: not actually a dead-letter under normal policy; if it reached dead-letter, something about the retry config changed.

**Resolve:** once the root cause is fixed, retry via `python -m background_jobs.admin retry <job_id>` which creates a new `PENDING` row (per `modules/background_jobs.md` § 11). Do not mutate the dead-lettered row.

### 5.7 `jobs.reaper_stalled` and `jobs.claim_age_high`

**Symptom:** Either the reaper hasn't run in 5 min (SEV-1) or jobs are stuck in `CLAIMED` for > 10 min (SEV-2).

**Immediate actions:** check worker task health. The reaper runs inside the worker process; if no workers are healthy, the reaper is also down.

**Diagnose:** if workers are healthy but the reaper isn't progressing, something is holding a DB transaction open — check `pg_stat_activity` via the ops admin for long-running transactions.

**Mitigate:** force-restart the worker service (`update-service --force-new-deployment`). If stuck transactions remain, terminate them via `pg_terminate_backend` as `ops_admin` — but note this in the incident log.

### 5.8 `extraction.failure_rate_high`

**Symptom:** > 20% of extractions failing.

**Diagnose:** Log Insights on `extraction.failed` by `reason`. Cross-reference with OpenAI status. If `EMPTY_TEXT` dominates, a tenant is uploading scanned PDFs — not an incident, track in the `EMPTY_TEXT` rate for the OCR decision from `modules/extraction_service.md` § 14.

**Mitigate:** if OpenAI-related, nothing to do at the app layer — wait it out, scale workers down temporarily to avoid cost bleed. If app-related (e.g., a prompt change), roll back (§ 6.2).

### 5.9 `extraction.llm_circuit_breaker_open`

**Symptom:** The circuit breaker in Extraction has opened, per `modules/extraction_service.md` § 10.

**Immediate actions:** check OpenAI status page; check recent deploys for prompt or model changes.

**Diagnose:** the breaker opens after N consecutive failures. Look at the last N failures' `reason` codes. `LLM_UNRECOVERABLE` with no other signal usually means OpenAI itself. `SCHEMA_VIOLATION` spiking means a prompt regression.

**Mitigate:** if OpenAI is down, let the breaker stay open — that's what it's for. If a prompt regression, roll back (§ 6.2). Do not manually reset the breaker; it half-opens on its own.

### 5.10 `extraction.cost_spike`

**Symptom:** Hourly OpenAI spend above 2× trailing 7-day average.

**Immediate actions:** check `extraction.tokens_total` by tenant (Log Insights). If one tenant dominates, check their recent upload count — may be a legitimate onboarding burst or a cost exhaustion attack per `modules/extraction_service.md` § 11.

**Mitigate:** if an attack, tighten the per-tenant quota temporarily via Secrets & Config and force refresh. If legitimate, monitor and let run.

**Escalate:** any cost spike above 5× trailing average is SEV-1 regardless of cause — escalate to budget-owning human immediately.

### 5.11 `extraction.openai_auth_failure`

**Symptom:** OpenAI returning 401. Per `modules/extraction_service.md` § 10, this triggers 0 retries and a critical alert.

**Immediate actions:**
1. Verify the key is in Secrets Manager and readable by the worker task role.
2. Check OpenAI dashboard — was the key revoked?
3. Check CloudTrail for recent Secrets Manager writes on this secret.

**Mitigate:** rotate via OpenAI dashboard, update the secret, force worker restart. Jobs stuck in `UPLOADED` will be re-claimed by the reconciler from `modules/ingestion_service.md` § 10 within 5 minutes — no manual intervention needed on the queue.

**If the key appears in a log line anywhere:** this is a key exposure incident. Jump to § 5.16.

### 5.12 RDS CPU / storage / connections high

**Symptom:** any of the three RDS-level alerts.

**Immediate actions:** Performance Insights → top SQL by load. The answer is almost always one of: a missing index, a new query in a recent deploy, or a runaway reporting query on the `ops_admin` role.

**Mitigate:**
- Missing index: CREATE INDEX CONCURRENTLY via `ops_admin`, note in decision log.
- Runaway query: `pg_terminate_backend` the offender.
- Storage low: check if autoscaling is enabled (§ `05_deployment.md` says it is); if it's not firing, manually extend.
- Connections high: check for a pool leak — connection count should be bounded by task count × pool size per task.

**Escalate:** if none of the above applies, scale up the instance class (Multi-AZ means ~60s failover) and declare SEV-2.

### 5.13 `rds.replica_lag_high`

**Symptom:** Multi-AZ standby lag > 30s.

This is rare on Multi-AZ because replication is synchronous. If it fires, RDS is experiencing a real problem. **Do not fail over manually.** Open an AWS support case, declare SEV-1, and wait. Manual failover during degraded replication can lose data.

### 5.14 `s3.4xx_spike`

**Symptom:** S3 returning 4xx on the deals bucket.

**Diagnose:** check which operation is failing. `403` on GetObject usually means a task role was modified; `404` spikes usually mean a retention policy misfired.

**Mitigate:** role issue → revert the IAM change; retention issue → disable the lifecycle rule immediately (data loss is in progress), declare SEV-1.

### 5.15 `security.sandbox_crash_spike`

**Symptom:** PDF sandbox crashing > 5 times in 60s, per `modules/input_validation.md` § 10.

This is either a new exploit in the wild or a bug in the sandbox library. **Treat as SEV-1.**

**Immediate actions:**
1. Scale `api` to its minimum (2 tasks) to limit sandbox exposure surface.
2. Capture sample crashing PDFs from the sandbox scratch dir before they rotate.
3. Check Input Validation rejection rates by source and tenant — if one tenant is the source, block their uploads temporarily.

**Mitigate:** block the offending source, patch the sandbox library or upgrade, redeploy. Do not remove the sandbox — that's the whole defense.

### 5.16 `security.scrubber_key_hits`

**Symptom:** Observability's scrubber matched an OpenAI key pattern or similar secret in a log field.

**Immediate actions:** **assume the secret is compromised.** The scrubber caught it before emit, but the code path that produced the key in a log field needs to be treated as a breach.

1. Rotate the affected secret immediately (OpenAI key, JWT signing key, etc.).
2. Check logs over the past 7 days for other occurrences — scrubber hits are logged with source module and event, so grep by those.
3. File a security incident, declare SEV-1.

The rotation comes *before* the investigation. Investigate with a rotated secret, not a compromised one.

### 5.17 `security.tenant_mismatch`

**Symptom:** a job's payload `tenant_id` did not match the deal row's `tenant_id`, per `modules/extraction_service.md` invariant 9.

This should be impossible under correct code. If it fires, suspect either a corrupted DB write or an attacker who has gotten far enough to forge a job payload. **SEV-1 immediately.**

**Immediate actions:**
1. Halt the worker service (`update-service --desired-count 0`) — do not let any more jobs run until the scope of the breach is understood.
2. Snapshot RDS immediately (`aws rds create-db-snapshot`) for forensic preservation.
3. Escalate to security lead, declare incident.

Do not retry the dead-lettered job. Do not delete it. It is evidence.

### 5.18 `security.auth_failures_spike`

**Symptom:** Auth failures > 50/min.

**Diagnose:** check source IPs in Log Insights. A single IP is a bot (let the rate limiter handle it, verify the rule fired). Distributed IPs against one account is credential stuffing — force password reset on the target account. Distributed IPs against many accounts is a broad attack — engage WAF rate-based rules.

### 5.19 `meta.alert_pipeline_silent`

**Symptom:** No alerts fired in 24h.

**Diagnose:** manually trigger a known housekeeping alert by, e.g., running the backup verification out-of-cycle. If it doesn't fire, the alert pipeline is broken.

**Mitigate:** check CloudWatch Alarms state, SNS topic subscriptions, PagerDuty integration. Every link in the chain needs verification; the silent pipeline is the failure mode that eats teams alive.

## 6. Deployment Procedures

### 6.1 Normal deploy

Per `05_deployment.md` § 8: push to `main` → staging auto-deploys → staging smoke tests run → tag a release → human approves → prod deploys via rolling update with circuit breaker.

The on-call's job during a normal deploy is **to watch**, not to intervene. Watch the API error rate dashboard, watch queue depth, watch extraction failure rate. If any of the § 4 alerts fire within 30 min of deploy, the deploy is suspect — roll back first (§ 6.2), diagnose second.

### 6.2 Rollback

**When:** any SEV-1 or SEV-2 alert fires within 30 min of a deploy, or any of the "deploy-related" diagnose paths in § 5 point to a recent deploy.

**Command sequence:**
```
# Find the previous image tag
aws ecs describe-services --cluster prod --services api \
  --query 'services[0].deployments[*].taskDefinition'

# Update services to the previous task definition
aws ecs update-service --cluster prod --service api \
  --task-definition <previous-task-def-arn>
aws ecs update-service --cluster prod --service worker \
  --task-definition <previous-task-def-arn>

# Watch for stable
aws ecs wait services-stable --cluster prod --services api worker
```

**Rollback is always safe** because migrations are reversible (`00_main_spec.md` technical invariant 6). However: if a migration introduced a new NOT NULL column that the old image doesn't know to populate, rollback will cause writes to fail. This is why reversible migrations are CI-enforced and why the Validator agent checks every migration for forward-compatibility with the previous image version.

**If rollback itself fails:** declare SEV-1, escalate, begin recovery planning. Do not retry the rollback more than twice.

### 6.3 Emergency hotfix

When a SEV-1 needs a code change, not a rollback (e.g., a third-party dependency just broke):

1. Branch from the **currently-deployed tag**, not from `main` — this gives a minimal diff.
2. Apply the fix, push, let CI run its full suite (do not skip tests even in emergencies — a broken hotfix is worse than the original incident).
3. Tag as a hotfix release (`v1.2.3-hotfix.1`).
4. Deploy to prod via the normal gate, but with incident commander approval instead of the normal reviewer.
5. File the postmortem (§ 8) including a note on whether the normal deploy pipeline should have caught this.

Skipping staging in a hotfix is allowed only with explicit incident commander approval, logged in the incident record.

## 7. Backup & Restore

### 7.1 Backup posture

Specified in `05_deployment.md` § 11: RPO 5 min via RDS PITR, RTO 1 hr, weekly restore test. This section is the *procedure*.

### 7.2 Restore procedure (RDS)

**Scenario:** production RDS is corrupted or lost.

1. Declare SEV-1, start incident log, post to status page.
2. Identify the target recovery point. For corruption, this is "the most recent point before corruption started" — use audit log timestamps as the reference.
3. Restore to a new instance:
   ```
   aws rds restore-db-instance-to-point-in-time \
     --source-db-instance-identifier deal-screener-prod \
     --target-db-instance-identifier deal-screener-prod-restored \
     --restore-time <iso-timestamp> \
     --db-subnet-group-name deal-screener-data \
     --vpc-security-group-ids <sg-data>
   ```
4. Wait for restore (10–40 min typical for 100 GB).
5. Run schema integrity check via the `ops_admin` CLI: `python -m deal_screener.ops check-schema`.
6. Update the application's DB connection secret in Secrets Manager to point at the restored instance's endpoint.
7. Force-restart `api` and `worker` services.
8. Verify via smoke tests; close incident once green.
9. Decommission the old instance only after 24 hours of successful operation on the restored one.

**Do not** restore in-place by renaming the new instance to the old name. Keeping both around for 24 hours is cheap insurance against finding out the restore was incomplete.

### 7.3 Weekly restore test

Specified in `05_deployment.md` § 11. The procedure:

1. Scheduled Lambda runs Sunday 06:00 UTC.
2. Restores the latest prod PITR to an isolated VPC in the staging account (not prod — you do not test restores in prod).
3. Runs the schema integrity check and a basic query against `deal_audit_log` to verify row counts are plausible.
4. Tears down the restored instance.
5. Emits a success or failure metric; failure triggers `meta.backup_restore_test_failed` SEV-1.

If the alert fires, the on-call runs the restore procedure manually against a current snapshot. If the manual restore also fails, the backup system is broken — halt prod deploys until resolved.

### 7.4 S3 recovery

Deals bucket has versioning enabled. Accidental overwrites recover via `aws s3api list-object-versions` and restoring the prior version. Accidental *deletes* show as delete markers and recover by removing the marker. Lifecycle rules expire noncurrent versions after 30 days, which bounds the recovery window.

**Scenario: lifecycle rule misfired and is deleting current versions.** Disable the rule immediately (§ 5.14), declare SEV-1, then assess scope via access logs before attempting recovery.

## 8. Incident Response

### 8.1 Declaring an incident

Any SEV-1 is automatically an incident. SEV-2 becomes an incident when the on-call believes it will last > 1 hour or affects > 10% of traffic. Incidents need an incident commander (on-call by default) and an incident record (ticket or doc).

### 8.2 During an incident

**One person types.** Multiple people running commands in parallel against prod during an incident is how incidents become outages. The incident commander designates the typist; everyone else is advisory. Every command is narrated in the incident channel before it is run, unless it's from a runbook in this document.

**Timeline is continuous.** Every action, every decision, every hypothesis is logged with a timestamp. The timeline is the primary input to the postmortem, and reconstructing it after the fact is always worse than recording it live.

**Status page updates on a fixed cadence.** Every 30 min for SEV-1, every hour for SEV-2, even if the update is "still investigating, no new information." Silence is worse than no-news.

### 8.3 Postmortem

Every SEV-1 and SEV-2 gets a postmortem, due within 5 business days, using the template in the incident repo. The template's non-negotiable sections:

- **Timeline**: reconstructed from the incident log, in UTC.
- **Impact**: users affected, duration, data at risk.
- **Root cause**: the actual cause, not the trigger. "Deploy X broke it" is a trigger; "we didn't have an integration test for Y" is the root cause.
- **What went well**: the alert fired promptly, the runbook worked, rollback succeeded. Worth recording because these are the things to protect.
- **What went poorly**: missed signals, slow diagnosis, unclear runbook. These become action items.
- **Action items**: each with an owner and a target date. "Add monitoring for X" is acceptable; "be more careful" is not.

Postmortems are blameless. The goal is to improve the system, not to find someone to punish. This is policy, not suggestion.

## 9. Routine Operational Tasks

### 9.1 Secret rotation

Automatic rotations (DB credentials, JWT signing key, webhook HMACs) run on schedule per `05_deployment.md` § 7. The on-call's job is to watch for rotation-related alerts (`api.error_rate_high` within minutes of a scheduled rotation) and know how to roll back via Secrets Manager console.

Manual rotations (OpenAI key, `ops_admin` credentials) happen quarterly on a scheduled maintenance window, or immediately on any suspected compromise. Procedure:

1. Generate new secret at the source (OpenAI dashboard, etc.).
2. Write to Secrets Manager as a new version.
3. Force task restart on the consuming service.
4. Verify via logs that the new version is in use.
5. Revoke the old secret at the source only after 24 hours of successful operation.

### 9.2 Certificate renewal

ACM handles certificate renewal for the ALB automatically. The on-call's job is to watch for `CertificateExpiringSoon` CloudWatch events and investigate if they fire — ACM auto-renewal rarely fails, but "rarely" is not "never."

### 9.3 Capacity review

Monthly. Look at: API p99 latency trend, worker queue depth baseline, RDS CPU baseline, S3 storage growth rate, OpenAI spend rate. Adjust autoscaling thresholds or instance classes if trends warrant. Record decisions in `appendices/B_decision_log.md`.

### 9.4 Alert hygiene review

Monthly. Any alert that fired more than 10 times in the month without an actionable outcome is either miscalibrated (tune the threshold) or genuinely noise (delete it). Alert fatigue is a SEV-3 in slow motion.

## 10. On-Call Rotation

- Primary on-call: 1 week rotations, 24/7 pager.
- Secondary on-call: same rotation, paged if primary doesn't ack within 10 min.
- Handoff: every Monday 10:00 local, 15-min sync covering open incidents, pending action items, and any "watch this" items from the outgoing on-call.
- New on-call: shadow one full rotation before taking primary. Run at least one simulated restore test (§ 7.2) against staging before the first rotation.

**Do not be a hero.** Escalate early. A SEV-1 escalated at 20 min and resolved at 45 min is a better outcome than a SEV-1 solo-diagnosed for 3 hours. The escalation path is documented per team; on-call's job is to know who to wake up, not to avoid waking them up.

## 11. Open Questions

1. **Error budget policy.** Should we adopt an explicit error budget (e.g., 99.5% availability) that gates deploys when burned? Aligns deploys with reliability, but requires an SLO framework we haven't built. Defer until a real SLA exists with a customer.
2. **Game days.** Scheduled chaos exercises (kill a worker, fail over RDS, revoke a secret) are the best way to validate runbooks. Monthly? Quarterly? Needs Conductor decision on cadence and whether to run against prod or staging.
3. **Runbook automation.** Some of these runbook steps (rollback, worker restart, scale actions) are scriptable. ChatOps-style "!rollback prod api" is tempting but introduces a new attack surface. Defer until runbooks are stable and a clear automation candidate emerges.
4. **PagerDuty vs alternatives.** This doc assumes a paging system exists but doesn't name one. Cost and integration complexity vary significantly. Defer to deployment choice.
5. **Incident severity auto-downgrade.** Currently severities are set manually and stay until manually changed. A SEV-1 that self-resolves in 5 min is still a SEV-1 for postmortem purposes. Is that right, or should short-duration SEV-1s downgrade to SEV-2 automatically? Leaning toward "no, the pager fired, it's SEV-1," but flagging it.
6. **Customer communication during incidents.** What goes on the public status page vs. an internal incident channel? Depends on having a customer base and a PR posture; defer until both exist.