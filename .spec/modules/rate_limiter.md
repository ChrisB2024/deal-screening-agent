# Module: Rate Limiter

> Cross-cutting module. Parent: `../01_decomposition.md`
> Build order: step 5 (depends on Observability, DB or Redis)

---

## 1. Purpose

Throttle requests across multiple scopes (per-user, per-tenant, per-IP, per-endpoint) using a token bucket strategy, returning standard `429 Too Many Requests` responses with retry hints, and protecting downstream modules from abuse, DoS, and runaway cost.

## 2. Responsibilities

- Provide FastAPI middleware that runs on every request, after Auth (where applicable) and before handler dispatch
- Apply layered limits: per-IP (always), per-user (when authenticated), per-tenant, per-endpoint group
- Use a token bucket algorithm with configurable burst and refill rate per scope
- Return `429 RATE_LIMITED` responses with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers
- Apply stricter limits to auth endpoints (login, refresh) than to general API endpoints
- Apply stricter limits to expensive endpoints (upload, manual rescore) than to cheap reads
- Emit metrics for every limit check, decision, and rejection
- Fail open on backing store unavailability (with alert), to avoid taking down the API when Redis hiccups
- Support per-tenant override of default limits (enterprise tier feature scaffolded but not exposed in v1)

## 3. Non-Responsibilities

- **Does not authenticate.** Auth Service runs first; rate limiting on authenticated endpoints uses the verified `user_id` and `tenant_id`.
- **Does not block IPs permanently.** This is a rate limiter, not a WAF. Long-term blocks belong to the edge layer.
- **Does not enforce quotas (cost, storage, document count).** Quotas are per-tenant business limits enforced inside the relevant business modules (Extraction enforces token quotas, Ingestion enforces storage quotas).
- **Does not detect bot traffic or CAPTCHAs.** Out of scope.
- **Does not cache responses.** It only decides allow/deny.
- **Does not store request bodies.** It looks at scope keys only.

## 4. Inputs / Outputs

### Inputs (per request)

- HTTP method + path → endpoint group lookup
- Source IP (from `X-Forwarded-For`, validated against trusted proxy list)
- `AuthContext` if present (user_id, tenant_id from Auth middleware)
- Current timestamp

### Outputs

**Allowed:** request proceeds to the handler. Response includes:
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 47
X-RateLimit-Reset: 1712842802
```

**Denied:** request is rejected with HTTP 429:
```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Too many requests. Please slow down.",
    "details": { "scope": "user", "retry_after_seconds": 12 },
    "request_id": "req_01HW..."
  }
}
```
With headers:
```
Retry-After: 12
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1712842814
```

### Side Effects

- Token bucket state updated in backing store (Redis in production, in-memory LRU in portfolio phase)
- Metrics emitted: `rate_limit.checks`, `rate_limit.allowed`, `rate_limit.denied{scope}`, `rate_limit.store_errors`
- Audit log entry on denial only if the scope is sensitive (auth endpoints)

## 5. State Machine

The rate limiter has no persisted state machine. Each scope has a token bucket with two values: `tokens_remaining` and `last_refill_at`. On each check:

```
LOAD bucket → REFILL based on elapsed time → CHECK tokens >= cost
                                                     │
                                  ┌──────────────────┴──────────────────┐
                                  ▼                                     ▼
                          ALLOW: decrement tokens             DENY: compute retry_after
                                  │                                     │
                                  ▼                                     ▼
                          STORE bucket                          STORE bucket (no decrement)
```

Buckets are stored with a TTL equal to the time to fully refill, so idle buckets clean themselves up.

## 6. Data Model

The rate limiter does not own any SQL tables. State lives in the backing store as keys:

```
rl:{scope}:{key}:{endpoint_group}  →  { tokens: float, last_refill_at: float }
```

Examples:
- `rl:ip:203.0.113.42:auth_login` → bucket for an IP hitting login
- `rl:user:usr_01HW...:default` → bucket for an authenticated user on general endpoints
- `rl:tenant:tnt_01HW...:upload` → bucket for tenant-wide uploads
- `rl:user:usr_01HW...:rescore` → bucket for a user manually re-scoring

### Default limits (v1)

| Scope | Endpoint Group | Burst | Refill (per minute) |
|---|---|---|---|
| IP | `auth_login` | 5 | 5 |
| Email | `auth_login` | 20 | 20 (per hour) |
| IP | `default` | 60 | 60 |
| User | `default` | 120 | 120 |
| User | `upload` | 10 | 10 |
| Tenant | `upload` | 100 | 100 |
| User | `rescore` | 20 | 20 |
| Tenant | `default` | 600 | 600 |

A request must pass *all* applicable scopes. If any bucket denies, the request is denied with the most-restrictive `Retry-After`.

These values are loaded from Secrets & Config at startup so they can be tuned without code changes.

## 7. API Contract

The rate limiter has **no public HTTP API**. It is consumed exclusively as FastAPI middleware:

```python
async def rate_limit_middleware(request: Request, call_next): ...
```

Endpoint groups are declared on routes via a decorator or route metadata:

```python
@router.post("/api/v1/deals/upload", rate_limit_group="upload")
async def upload_deal(...): ...
```

Routes without an explicit group default to `default`.

A small admin endpoint (gated to admin-role tokens, not exposed in v1) supports:
- `GET /admin/rate-limits/{scope}/{key}` — current bucket state, for debugging
- `POST /admin/rate-limits/{scope}/{key}/reset` — manual reset, audit-logged

## 8. Invariants

1. Every request to the API passes through the rate limiter exactly once. There is no opt-out for protected endpoints; the framework enforces it.
2. Per-IP limits apply to *all* requests, including unauthenticated ones, before any other check.
3. Auth endpoints are subject to stricter per-IP and per-email limits than general endpoints.
4. A denied request never executes the handler. Side effects are limited to bucket update + metrics + log.
5. Bucket state is monotonic in time within a single key: `last_refill_at` only moves forward. Out-of-order checks (e.g., across processes with clock skew) reconcile to the later timestamp without producing negative tokens.
6. The backing store is consulted, not the source of truth — the limit *configuration* is loaded from Secrets & Config and never from the store. A compromised store cannot raise limits.
7. On store unavailability, the limiter fails open (allow) and emits a critical alert. Failing closed would let a Redis outage take down the entire API.
8. `Retry-After` header is always present on 429 responses and is an integer number of seconds, never a date string.
9. The `cost` of a request is normally 1, but expensive endpoints may declare higher costs (e.g., bulk operations cost N). Cost is never derived from request body content.

## 9. Trust Boundaries

| Boundary | Validation |
|---|---|
| Client → Rate limiter | IP from `X-Forwarded-For` only if request comes from a trusted proxy CIDR; otherwise from socket peer |
| Rate limiter → Backing store | Connection over TLS or private network; auth via password from Secrets & Config |
| Rate limiter → Secrets & Config | Limits and trusted-proxy CIDRs loaded at startup, refreshed on rotation |
| Auth-derived keys | `user_id` and `tenant_id` come from `AuthContext`, never from request body or headers |

The trusted-proxy list is critical: a misconfigured `X-Forwarded-For` parser is the classic way IP-based rate limiters get bypassed (attacker spoofs the header, every request looks like a different IP). The parser walks the chain right-to-left, accepts the first IP not in the trusted CIDR list, and ignores everything left of it.

## 10. Failure Modes

| Failure | Detection | System Behavior | User-Facing Behavior |
|---|---|---|---|
| Backing store unavailable | Redis client error | **Fail open**: allow request, emit critical alert, set `X-RateLimit-Status: degraded` | Transparent — request succeeds |
| Backing store slow (>50ms) | Per-call timeout | Fail open after timeout, log warning | Slight latency, transparent |
| Clock skew between processes | `last_refill_at` in future | Take `max(now, last_refill_at)` to keep monotonicity | Transparent |
| Spoofed `X-Forwarded-For` | Trusted-proxy parser | Header ignored, socket peer IP used | Attacker cannot bypass IP limits |
| Missing endpoint group on route | Default group applied | Logged at warning level for engineering to fix | Standard limits applied |
| Bucket key explosion (cardinality attack) | Store memory pressure | Per-IP limit on key creation rate; oldest keys evicted under pressure | Some legit users may briefly see lower limits during attack |
| Authenticated user hits limit | Bucket exhausted | `429 RATE_LIMITED` with `Retry-After`, audit log if auth endpoint | "Too many requests. Please slow down." |
| Tenant hits aggregate limit while individual users have headroom | Tenant bucket exhausted first | All users in the tenant see 429 until refill | "Your team has hit a rate limit. Try again shortly." |

### Fail-open rationale

Failing closed on Redis outage means: Redis goes down → entire API returns 429 → cascading failure across the product. The cost of fail-open is a temporary window where rate limits aren't enforced. The cost of fail-closed is an outage that affects every customer. For a deal-screening tool (not a payment system), fail-open is the right tradeoff. The decision is logged.

## 11. Security Considerations

- **`X-Forwarded-For` spoofing.** Covered above. The trusted-proxy CIDR list is the single most important config value in this module. A bug here defeats IP-based rate limiting entirely.
- **Cardinality attacks against the store.** An attacker who can create many distinct scope keys (e.g., by rotating IPs) can exhaust store memory. Mitigations: TTL on every key, max-keys-per-IP cap, eviction policy on the store.
- **Race conditions across processes.** Two API instances checking the same bucket simultaneously can both see "tokens available" and both decrement, briefly allowing 2× the rate. Mitigation: Redis Lua script for atomic refill+check+decrement, or a small overshoot tolerance (acceptable for non-payment use cases).
- **Limit configuration in the store.** Limits live in Secrets & Config, not in the store. A compromised Redis cannot raise limits — it can only corrupt bucket state, which fails open with alerts.
- **Denial via slow store.** If Redis is healthy but slow, per-call timeout (50ms) ensures the rate limiter doesn't add latency to every request. Slow store → fail open → alert.
- **Authenticated abuse.** A legitimate user with valid credentials can still abuse the system. Per-user limits catch this. Repeated triggering of per-user limits is a signal worth logging for security review.
- **Pre-auth limits matter most.** Login and refresh endpoints have the strictest per-IP and per-email limits because they're the only path an unauthenticated attacker has into the system. Brute-force defense lives here.
- **Denial via tenant exhaustion.** A noisy user can exhaust a tenant-wide bucket, blocking their teammates. Acceptable for v1; team-level fairness is a future enhancement.
- **Audit on auth-endpoint denials.** Rate-limit denials on auth endpoints are written to the auth audit log (not just metrics) because they're the leading indicator of a credential-stuffing attack.

## 12. Dependencies

**Internal modules:**
- Secrets & Config (limit values, trusted-proxy CIDRs, store credentials)
- Observability (metrics, logging, audit on auth-endpoint denials)
- Auth Service (provides `AuthContext` for user/tenant scope keys; runs before this middleware on authenticated routes)

**External services:**
- Redis (production) or in-process LRU (portfolio phase) — abstracted behind a `RateLimitStore` interface

**Libraries:**
- `redis-py` async client (when using Redis)
- `fastapi` for middleware
- A small custom token-bucket implementation; not worth a third-party library for this scope

## 13. Testing Requirements

### Unit tests
- Token bucket refills correctly over elapsed time (boundary: 0s, 1s, 60s, 1 hour)
- Bucket never goes negative; never exceeds burst capacity
- Cost > 1 deducts correctly and denies if insufficient
- `Retry-After` calculation matches actual time until next token
- Trusted-proxy parser walks the chain correctly with and without trusted CIDRs
- Default endpoint group applies when no group declared

### Integration tests
- Authenticated request hitting per-user limit returns 429 with correct headers
- Per-IP limit on `auth_login` blocks 6th login attempt within a minute from the same IP
- Per-email limit on login blocks the 21st attempt for the same email within an hour, even from rotating IPs
- Tenant-wide upload limit blocks all users in a tenant once exceeded; users in other tenants unaffected
- A denied request never reaches the handler (handler invocation counter stays at 0)
- Successful request decrements the bucket; bucket state observable via admin endpoint

### Failure tests
- Redis unavailable: requests succeed, alert emitted, response includes `X-RateLimit-Status: degraded`
- Redis slow: timeout fires at 50ms, request continues
- Two concurrent processes hitting the same bucket: combined throughput within burst + small overshoot, never zero throughput

### Security tests
- Spoofed `X-Forwarded-For` from a non-trusted source IP is ignored; rate limit applied to socket peer
- Spoofed `X-Forwarded-For` from a trusted proxy is parsed correctly; rate limit applied to claimed client IP
- Per-user limit cannot be bypassed by manipulating a header (user_id only sourced from `AuthContext`)
- Cardinality attack (10,000 distinct scope keys from one IP) does not exhaust store memory; per-IP key cap kicks in

### Load tests
- 1000 RPS sustained on a single endpoint with allowed traffic does not add > 5ms p95 latency
- A burst of 10× the limit produces exactly burst-capacity allowed responses, then 429s, then resumes after refill

## 14. Open Questions

1. **Redis vs in-process for portfolio phase.** Portfolio phase runs a single Fly.io machine, so an in-process LRU is sufficient. Production needs Redis (or equivalent) for cross-instance consistency. The `RateLimitStore` interface accommodates both. Decision logged.
2. **Atomic bucket update.** Lua script in Redis is the canonical way to make refill+check+decrement atomic. The alternative (optimistic concurrency with retry) is simpler to test. v1 will use Lua; if Lua becomes operationally awkward, fall back to optimistic + accept small overshoot.
3. **Per-tenant overrides.** The data model accommodates tenant-specific limits, but v1 ships with global defaults only. Enterprise tier will expose this. Defer until enterprise tier is scoped.
4. **Cost-based rate limiting for LLM-heavy endpoints.** Currently `cost=1` per request. Should manual rescore or extraction-trigger endpoints cost more to reflect their downstream LLM cost? Probably yes. Defer until cost data from production exists.
5. **Sliding window vs token bucket.** Token bucket is simpler and burst-friendly. Sliding window is more accurate for "exactly N per minute" guarantees. Token bucket is the right call for an API where bursts are normal (a user batch-uploading deals). No revisit planned.
6. **Account-level vs session-level limits.** Currently scoped by `user_id`. A user with multiple sessions across devices shares the same bucket — correct behavior. No change planned.
7. **Distributed rate limiting at the edge.** A future enhancement is to push per-IP limits to the load balancer / CDN layer so abusive traffic never reaches the API. Out of scope for v1; revisit when traffic patterns justify it.