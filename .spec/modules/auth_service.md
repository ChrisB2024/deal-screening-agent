# Module: Auth Service

> Cross-cutting module. Parent: `../01_decomposition.md`
> Build order: step 4 (depends on Secrets, Observability, DB Models)

---

## 1. Purpose

Issue and verify short-lived JWT access tokens, manage refresh token rotation with reuse detection, enforce session lifecycle (login, logout, revocation), and provide the middleware that every other module relies on for authenticated request handling.

## 2. Responsibilities

- Authenticate users via email + password (Argon2id hashing)
- Issue 15-minute JWT access tokens signed with an asymmetric key (Ed25519 or RS256)
- Issue refresh tokens (30-day lifetime, rotated on every use, single-use semantics)
- Detect refresh token reuse and revoke the entire session family on detection
- Provide the FastAPI dependency / middleware that verifies access tokens on every protected request
- Extract and inject `tenant_id`, `user_id`, and roles from verified JWT claims into the request context
- Support session revocation: explicit logout, password change, admin action
- Rotate signing keys on a schedule with overlap window
- Emit audit log entries for every login, logout, refresh, and revocation event

## 3. Non-Responsibilities

- **Does not manage user CRUD beyond auth-relevant fields.** A future user-management module owns profile, preferences, etc.
- **Does not handle authorization beyond tenant scoping.** Per-feature RBAC is deferred to a team-tier milestone.
- **Does not store secrets directly.** Signing keys come from Secrets & Config.
- **Does not rate-limit login attempts itself.** Rate Limiter middleware applies stricter limits to auth endpoints.
- **Does not send email.** Password reset and email verification flows enqueue jobs handled by an email worker (out of v1 scope; password reset deferred).
- **Does not federate identity.** SSO/OAuth/SAML are deferred to enterprise tier.

## 4. Inputs / Outputs

### Inputs

**Login:**
```json
POST /api/v1/auth/login
{ "email": "analyst@fund.com", "password": "..." }
```

**Refresh:**
```json
POST /api/v1/auth/refresh
{ "refresh_token": "rt_01HW..." }
```

**Logout:**
```json
POST /api/v1/auth/logout
{ "refresh_token": "rt_01HW..." }
```
Authenticated header for logout: access token in `Authorization: Bearer`.

### Outputs

**Successful login or refresh:**
```json
{
  "access_token": "eyJhbGc...",
  "token_type": "Bearer",
  "expires_in": 900,
  "refresh_token": "rt_01HW...",
  "refresh_expires_in": 2592000,
  "user": {
    "user_id": "usr_01HW...",
    "tenant_id": "tnt_01HW...",
    "email": "analyst@fund.com"
  }
}
```

**JWT access token claims:**
```json
{
  "iss": "deal-screener",
  "sub": "usr_01HW...",
  "tnt": "tnt_01HW...",
  "sid": "sess_01HW...",
  "iat": 1712841802,
  "exp": 1712842702,
  "kid": "key_2026_q2"
}
```

`sid` ties the access token to a session, which lets revocation invalidate all access tokens issued under that session even before they expire.

### Side Effects

- New row in `sessions` table on login
- New row in `refresh_tokens` table on login or refresh; old row marked `consumed`
- New row in `auth_audit_log` for every login, refresh, logout, failed attempt, revocation
- Metrics: `auth.login.success`, `auth.login.failed`, `auth.refresh.success`, `auth.refresh.reuse_detected`, `auth.session.revoked`

## 5. State Machine

### Session lifecycle

```
        ┌──────────┐
        │  ACTIVE  │
        └────┬─────┘
             │ logout / refresh reuse / password change / admin action
             ▼
        ┌──────────┐
        │ REVOKED  │
        └──────────┘
```

Sessions are append-only state. A revoked session is never reactivated; logging in again creates a new session.

### Refresh token lifecycle

```
ISSUED → CONSUMED → (replaced by new token in same family)
   │
   ▼
REUSED  →  triggers full session family revocation
```

Each refresh token is single-use. Consuming a refresh token issues a new one in the same session family. A second consumption attempt of an already-consumed token is a **reuse event** — interpreted as token theft, regardless of which party (legit user or attacker) used it first. The entire session is revoked, all descendant tokens invalidated.

## 6. Data Model

### `users` table

```sql
CREATE TABLE users (
    user_id            TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    email              CITEXT NOT NULL,
    password_hash      TEXT NOT NULL,        -- argon2id
    password_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_email UNIQUE (email)
);
```

### `sessions` table

```sql
CREATE TABLE sessions (
    session_id     TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES users(user_id),
    tenant_id      TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at     TIMESTAMPTZ,
    revoked_reason TEXT,                      -- logout | reuse | password_change | admin
    user_agent     TEXT,
    ip_address     INET
);

CREATE INDEX idx_sessions_user_active ON sessions(user_id) WHERE revoked_at IS NULL;
```

### `refresh_tokens` table

```sql
CREATE TABLE refresh_tokens (
    token_id       TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES sessions(session_id),
    parent_token_id TEXT REFERENCES refresh_tokens(token_id),
    token_hash     TEXT NOT NULL,             -- sha256 of the opaque token
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at     TIMESTAMPTZ NOT NULL,
    consumed_at    TIMESTAMPTZ,
    consumed_by_ip INET,

    CONSTRAINT uq_token_hash UNIQUE (token_hash)
);

CREATE INDEX idx_refresh_session ON refresh_tokens(session_id);
```

The token itself is opaque (`rt_<ulid>_<random>`) and never stored — only its hash. Lookups are by hash. The `parent_token_id` chain reconstructs the rotation history of a session, which is what reuse detection walks.

### `auth_audit_log` table

```sql
CREATE TABLE auth_audit_log (
    audit_id    TEXT PRIMARY KEY,
    user_id     TEXT,                          -- null for failed logins with unknown email
    tenant_id   TEXT,
    session_id  TEXT,
    event_type  TEXT NOT NULL,                 -- LOGIN_SUCCESS, LOGIN_FAILED, REFRESH, REUSE_DETECTED, LOGOUT, REVOKED
    ip_address  INET,
    user_agent  TEXT,
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## 7. API Contract

All under `/api/v1/auth`.

**`POST /login`** — body above. Responses: `200` with token bundle, `401 INVALID_CREDENTIALS` (uniform timing, see §11), `429 RATE_LIMITED` from rate limiter.

**`POST /refresh`** — body above. Responses: `200` with new token bundle, `401 INVALID_REFRESH_TOKEN` if token unknown, expired, or already consumed (reuse path), `401 SESSION_REVOKED` if the session was revoked.

**`POST /logout`** — revokes the current session. Always returns `204` even if already logged out (to prevent enumeration of valid sessions).

**`GET /me`** — returns the authenticated user's identity. Requires valid access token. Useful for UI to verify auth state.

### Middleware contract

Other modules consume Auth via a FastAPI dependency:

```python
async def require_auth(request: Request) -> AuthContext: ...

class AuthContext:
    user_id: str
    tenant_id: str
    session_id: str
    roles: list[str]
```

The dependency raises `401 UNAUTHORIZED` on any verification failure. It is the *only* sanctioned way to extract identity from a request — no module reads JWT claims directly.

## 8. Invariants

1. Passwords are hashed with Argon2id (memory ≥ 64 MB, iterations ≥ 3, parallelism ≥ 1) and never stored or logged in plaintext.
2. Access tokens are stateless JWTs verified by signature + expiry + session-revoked check (the revoked check is fast: a small bloom filter or short-TTL Redis cache, fall through to DB).
3. Refresh tokens are single-use. Consuming one issues a new one and marks the old as `consumed`.
4. Refresh token reuse detection revokes the entire session family atomically. No descendant token remains valid after a reuse event.
5. The JWT signing key is asymmetric. The private key lives only in the auth service process memory and Secrets & Config; the public key may be distributed for verification.
6. Every signing key has a `kid` (key ID); tokens carry their `kid` so verification can pick the right key during rotation overlap.
7. `tenant_id` in any token is the user's tenant at issuance time — never read from request input.
8. Failed logins emit an audit log entry but never reveal whether the email exists.
9. Logout, password change, and admin revocation all invalidate the session synchronously before returning success.
10. Auth-related DB writes (session create, token consume, revocation) and audit log writes happen in the same transaction.

## 9. Trust Boundaries

| Boundary | Validation |
|---|---|
| Browser → Auth endpoints | TLS, strict rate limit (5 login attempts / minute / IP, 20 / hour / email), schema validation |
| Auth Service → DB | Parameterized queries, password hash never logged |
| Auth Service → Secrets & Config | Signing key loaded at startup, refreshed on rotation, never logged |
| Other modules → Auth middleware | Modules trust the `AuthContext` returned by the dependency; they do not verify JWTs themselves |
| Token issuer → Token verifier | Asymmetric signature; verifier needs only the public key |

## 10. Failure Modes

| Failure | Detection | System Behavior | User-Facing Behavior |
|---|---|---|---|
| Wrong password | Argon2 verify | `401 INVALID_CREDENTIALS`, audit log, increment rate counter | "Email or password is incorrect." (uniform message) |
| Unknown email | User lookup | Same as wrong password (uniform timing — see §11) | Same |
| Expired access token | JWT `exp` check | `401 TOKEN_EXPIRED` with hint header for client to refresh | UI silently triggers refresh flow |
| Tampered access token | Signature verify | `401 UNAUTHORIZED`, security event emitted | UI forces re-login |
| Refresh token unknown | Hash lookup miss | `401 INVALID_REFRESH_TOKEN` | UI forces re-login |
| Refresh token already consumed | `consumed_at IS NOT NULL` | **Reuse event:** revoke entire session family, audit log `REUSE_DETECTED`, alert | UI forces re-login |
| Session revoked | Session check | `401 SESSION_REVOKED` | UI forces re-login |
| Signing key unavailable at startup | Secrets load fails | Service refuses to start, alerts on-call | All auth fails — service down |
| Argon2 verify times out (CPU starvation) | Process metrics | Login slow but correct; trigger autoscale | User sees loading state |
| Clock skew between services | JWT `iat`/`exp` rejection | Allow ±30s leeway in verifier | Transparent if within leeway |
| Public key rotation race | Verifier doesn't yet have new `kid` | Verifier fetches public key set on cache miss | Transparent (one-time fetch) |

## 11. Security Considerations

- **Uniform login timing.** Wrong password and unknown email must take the same time. Implementation: always run a dummy Argon2 verify even when the user doesn't exist, to prevent user enumeration via timing.
- **Uniform login response.** Same error message and status for both cases.
- **Refresh reuse as theft signal.** A consumed refresh token being presented again means *something* went wrong: either the legit user replayed (rare with a correct client), or an attacker stole and used the token before/after the legit user. Both possibilities are handled the same way: revoke the whole session, force re-login. False positives are acceptable — the alternative is silent compromise.
- **Argon2 parameters.** Tuned per environment via Secrets & Config; verified at startup that parameters meet a minimum floor. Parameters logged at startup (without the hash itself).
- **Key rotation.** Signing keys rotate every 90 days with a 7-day overlap window. During overlap, the verifier accepts tokens signed by either `kid`. After overlap, tokens signed with the old `kid` fail verification — clients with stale tokens are forced to refresh, which issues a new token signed with the new `kid`.
- **Key compromise response.** If a signing key is suspected compromised, an emergency rotation revokes all sessions globally (by bumping a "min issued at" watermark stored in Secrets & Config and checked by the verifier). This is documented in the operations runbook.
- **JWT in `Authorization` header, not cookies.** No CSRF surface. No `SameSite` to configure.
- **Token storage on the client.** The client SHOULD store the refresh token in `httpOnly` storage (if it ever uses cookies) or in memory + `localStorage` with awareness of XSS risk. The minimal UI uses memory + `localStorage`; the security tradeoff is documented in the decision log.
- **Session enumeration via logout.** Logout always returns `204`, even for invalid tokens, to prevent attackers from probing whether a session exists.
- **Bcrypt vs Argon2id.** Argon2id is the choice because it's memory-hard, resistant to GPU and ASIC attacks, and is the OWASP recommendation as of the spec date.
- **Password reset.** Out of scope for v1. When added, must use single-use, short-lived (15 min), rate-limited tokens delivered via email; reset must invalidate all existing sessions for the user.
- **Account lockout.** Not implemented in v1. Rate limiting on login (5/min/IP, 20/hr/email) is the defense. Account lockout has DoS-via-victim-email risk and is deferred until needed.

## 12. Dependencies

**Internal modules:**
- Secrets & Config (signing keys, Argon2 parameters)
- Observability (audit log, metrics, structured logs with PII scrubbed)
- Rate Limiter (stricter limits on auth endpoints)
- DB Models (`users`, `sessions`, `refresh_tokens`, `auth_audit_log`)

**External services:**
- PostgreSQL only

**Libraries:**
- `argon2-cffi` for password hashing
- `pyjwt` or `python-jose` for JWT signing/verification
- `fastapi` for HTTP layer
- `sqlalchemy[asyncio]` for DB access

## 13. Testing Requirements

### Unit tests
- Argon2 verify accepts correct password, rejects wrong password
- JWT signing produces a verifiable token with correct claims
- JWT verification rejects: wrong signature, expired, future `iat`, missing `kid`, unknown `kid`
- Refresh token hash is deterministic; lookup by hash works
- Reuse detection: consuming a token twice marks the session for revocation

### Integration tests
- Login → access token works on a protected endpoint → refresh → new access token works → logout → both tokens fail
- Reuse attack: legit user logs in, refreshes; attacker replays the original refresh token; both legit and attacker tokens are now invalid; legit user must re-login
- Password change: all existing sessions for the user are revoked
- Admin revocation: targeted session revoked, others preserved
- Cross-tenant claim: a token issued for tenant A cannot access tenant B's data (verified at every consumer module's tenant check)

### Security tests
- Login timing: wrong password and unknown email take within ±20ms of each other across 1000 trials
- Login response: identical body and status for both failure cases
- JWT tampering: changing any claim invalidates the signature
- Key rotation: tokens signed with old `kid` work during overlap, fail after
- Emergency revocation: bumping the "min iat" watermark invalidates all existing tokens immediately
- Audit log completeness: every auth event produces a row; failed logins for unknown emails produce a row with `user_id = NULL`
- Password hash never appears in logs, error messages, or API responses (grep assertion)

### Load tests
- 100 concurrent logins do not starve Argon2 CPU; p95 < 500ms
- 1000 concurrent JWT verifications hit the revocation cache, not the DB

## 14. Open Questions

1. **Bloom filter vs Redis for revocation cache.** A bloom filter is cheap and stateless but has false positives (which fall through to DB — acceptable). Redis is precise but adds an operational dependency. For portfolio phase: in-process LRU with short TTL + DB fallback. For production: Redis. Decision logged in `appendices/B_decision_log.md`.
2. **Refresh token storage on client.** `httpOnly` cookie vs `localStorage` is a real tradeoff. Cookies resist XSS but reintroduce CSRF concerns; `localStorage` is XSS-vulnerable but simpler. v1 uses `localStorage` with documented risk. Revisit for enterprise tier.
3. **MFA / TOTP.** Not in v1. When added, slot in between password verify and session creation. The session row needs a `mfa_verified_at` field. Defer until enterprise tier.
4. **WebAuthn / passkeys.** Increasingly the right answer for new products. Defer until v1 ships and we have user feedback on whether password-only is too friction-heavy.
5. **Audit log retention.** Currently no retention policy on `auth_audit_log`. Compliance frameworks (SOC 2) typically want 1 year. Defer to operations spec.
6. **Account lockout.** Pure rate limiting may not be enough for high-value tenants. A configurable lockout threshold (e.g., 10 failures in 1 hour locks the account for 15 minutes) is a candidate. Risk: DoS by guessing victim emails. Defer.