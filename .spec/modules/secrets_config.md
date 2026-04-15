# Module: Secrets & Config

> Cross-cutting module. Parent: `../01_decomposition.md`
> Build order: step 1 (no dependencies; every other module depends on this)

---

## 1. Purpose

Provide a single typed interface for loading secrets and non-secret configuration at process startup and refreshing rotated secrets in-memory at runtime, with fail-fast validation, zero exposure of secret material in logs or errors, and a pluggable provider boundary so the canonical AWS deployment and the Fly.io portfolio deployment can share identical consumer code.

## 2. Responsibilities

- Load all required secrets and config at startup; fail the process before serving any request if a required value is missing or malformed
- Expose a typed `SecretsClient` and `ConfigClient` interface that every other module depends on (no module ever imports a provider SDK directly)
- Validate all loaded values against a single canonical Pydantic schema (`AppConfig`) — consumers read typed fields, never raw strings or dicts
- Refresh rotated secrets on a background interval and notify subscribed consumers via registered callbacks, so long-lived in-memory references (OpenAI client, JWT signer, DB pool credentials) can update without a restart
- Provide envelope-encryption helpers (`encrypt_blob` / `decrypt_blob`) wrapping a `KMSClient` interface, for any module that needs ciphertext at rest
- Maintain a redaction registry: a set of secret keys (by name) and known secret values (by hash) that the Observability module's log formatter consults to scrub log records
- Record an audit entry on startup load and on every rotation event (key name, version, timestamp, source — never the value)
- Provide environment-scoped resolution: `dev`, `staging`, `prod`, `test` — with explicit rules for which providers are valid in each environment

## 3. Non-Responsibilities

- **Does not authenticate users or services.** That is Auth Service. Secrets only provides the JWT signing keys Auth Service uses.
- **Does not implement the structured logger.** That is Observability. Secrets exposes the redaction registry that Observability *reads* — the dependency direction goes Observability → Secrets, not the reverse, because Secrets must be loadable before logging is configured.
- **Does not encrypt the database or object store at rest.** Those use provider-managed encryption (RDS encryption, S3 SSE-KMS). Secrets only manages the application-layer envelope encryption helpers for ciphertext stored in application columns.
- **Does not manage feature flag rollout strategies.** v1 config is static-per-environment. A future feature-flag module (LaunchDarkly-style percentage rollouts) is out of scope and flagged in §14.
- **Does not provide secret generation.** Secrets are generated out-of-band (Terraform, manual ops) and loaded by this module. Generation tooling lives in the deployment repo, not here.
- **Does not rotate secrets in the upstream provider.** Rotation is performed by AWS Secrets Manager's native rotation (or manual op for Fly.io). This module *detects* rotation and refreshes its in-memory copy.
- **Does not publish JWT verification keys to external verifiers.** Auth Service may distribute its public key for in-process verification by other modules in this application, but exposing a JWKS endpoint or pushing public keys to a separate verifier service is out of scope for v1. If/when that need arises, it becomes a new trust boundary out of `SecretsClient` and gets its own contract.

## 4. Inputs / Outputs

### Inputs

- **Environment variable `APP_ENV`** — one of `dev` | `staging` | `prod` | `test`. Required. Determines provider selection and validation strictness.
- **Environment variable `SECRETS_PROVIDER`** — one of `aws_secrets_manager` | `flyio_secrets` | `env_file` | `memory`. Defaults per `APP_ENV` (see §6).
- **Environment variable `KMS_PROVIDER`** — one of `aws_kms` | `local_aes` | `none`. `local_aes` is dev-only; `none` disables envelope encryption helpers entirely.
- **Provider-specific config** — AWS region, secret name prefix, KMS key ARN, etc. Loaded from env vars at bootstrap (chicken-and-egg: the bootstrap config that tells Secrets how to load secrets is itself plain env vars).
- **Canonical `AppConfig` schema** — defined in this module, imported by every consumer.

### Outputs

- **`SecretsClient` instance** — singleton, accessed via `get_secrets()`. Methods: `get(name) -> SecretValue`, `subscribe(name, callback)`, `rotate(name)` (test-only).
- **`ConfigClient` instance** — singleton, accessed via `get_config() -> AppConfig`. Returns the typed Pydantic model.
- **`KMSClient` instance** — singleton, accessed via `get_kms()`. Methods: `encrypt(plaintext, context) -> Ciphertext`, `decrypt(ciphertext, context) -> bytes`, `generate_data_key() -> DataKey`.
- **Redaction registry** — a `RedactionRegistry` instance that Observability reads at logger setup. Methods: `register_key(name)`, `register_value(value)`, `redact(record) -> record`.
- **Startup audit log** — written to a bootstrap audit sink (file or stdout JSON, since Observability isn't up yet). Contains: env, provider, list of loaded secret *names*, schema version. Never values.

### `SecretValue` type

```python
@dataclass(frozen=True)
class SecretValue:
    name: str
    version: str
    loaded_at: datetime
    _value: bytes  # private; access via .reveal() in a controlled scope

    def reveal(self) -> bytes: ...  # logged at DEBUG with caller frame
    def reveal_str(self) -> str: ...  # convenience: reveal().decode("utf-8"); same lint rules apply
    def __repr__(self) -> str: return f"SecretValue(name={self.name}, version={self.version})"
    def __str__(self) -> str: return self.__repr__()
```

The `__repr__` and `__str__` overrides are the first line of defense against accidental logging. The `_value` field is name-mangled and the only access is `.reveal()`, which itself emits a DEBUG-level access trace once Observability is online.

### `AppConfig` schema (canonical fields)

```python
class AppConfig(BaseModel):
    env: Literal["dev", "staging", "prod", "test"]
    app_version: str
    log_level: Literal["DEBUG", "INFO", "WARN", "ERROR"]

    database: DatabaseConfig          # host, port, name, pool_size, ssl_mode (creds via SecretsClient)
    object_storage: StorageConfig     # bucket, region, prefix
    llm: LLMConfig                    # provider, model_pin, max_tokens, timeout_s (api_key via SecretsClient)
    auth: AuthConfig                  # access_ttl_s, refresh_ttl_s, issuer, argon2 params (signing keys via SecretsClient)
    rate_limit: RateLimitConfig       # buckets matrix, trusted_proxy_cidrs, store config (password via SecretsClient)
    observability: ObsConfig          # otlp_endpoint, sample_rate, service_name

    class Config:
        frozen = True  # config is immutable post-load
```

Non-secret config lives in `AppConfig`. Secret material lives in `SecretsClient` and is fetched by name. The split is enforced: `AppConfig` fields are validated to never contain known secret patterns.

### Sub-schemas with non-obvious shape

```python
class Argon2Params(BaseModel):
    memory_kib: int      # floor: 65536 (64 MiB)
    iterations: int      # floor: 3
    parallelism: int     # floor: 1

    @model_validator(mode="after")
    def _enforce_floor(self):
        if self.memory_kib < 65536 or self.iterations < 3 or self.parallelism < 1:
            raise ValueError("argon2 params below OWASP floor")
        return self

class AuthConfig(BaseModel):
    access_ttl_s: int
    refresh_ttl_s: int
    issuer: str
    argon2: Argon2Params
    signing_key_overlap_days: int = 7
    signing_key_rotation_days: int = 90

# Rate limiter buckets are a matrix, not three flat scalars.
class Scope(StrEnum):     IP = "ip"; EMAIL = "email"; USER = "user"; TENANT = "tenant"
class EndpointGroup(StrEnum): AUTH_LOGIN = "auth_login"; DEFAULT = "default"; UPLOAD = "upload"; RESCORE = "rescore"

class BucketConfig(BaseModel):
    burst: int
    refill_per_period: int
    period_s: int        # 60 for per-minute, 3600 for per-hour

class RateLimitStoreConfig(BaseModel):
    host: str
    port: int
    db: int = 0
    tls: bool = True
    timeout_ms: int = 50
    # password is NOT here — fetched via SecretsClient.get("rate_limit_store_password")

class RateLimitConfig(BaseModel):
    buckets: dict[tuple[Scope, EndpointGroup], BucketConfig]
    trusted_proxy_cidrs: list[IPv4Network | IPv6Network]   # critical: see rate_limiter §11
    store: RateLimitStoreConfig
```

The `buckets` matrix is loaded from a structured config source, not flattened to scalars; `rate_limiter.md` §6 enumerates the eight default rows. `trusted_proxy_cidrs` is the single most security-critical field in this entire config — a misparse here defeats IP-based rate limiting entirely.

### Canonical secret names

Every consumer fetches secrets by name from `SecretsClient.get(name)`. The full set of names that exist in v1 is fixed and enumerated here; adding a name requires editing this list and updating the bootstrap audit-log expected-names check.

| Name | Type | Owner | Rotated? | Notes |
|---|---|---|---|---|
| `openai_api_key` | str | LLM module | manual | |
| `database_password` | str | DB pool | rare | App role; admin role separate |
| `auth_signing_key_current` | PEM str | Auth Service | 90d | Ed25519 private key, current `kid` |
| `auth_signing_key_previous` | PEM str | Auth Service | 90d | Previous `kid`, kept for the 7-day overlap window |
| `rate_limit_store_password` | str | Rate Limiter | rare | Redis AUTH password |
| `auth_min_iat_watermark` | int (unix ts) | Auth Service | on demand | **Not actually secret** — see §6 "Mutable runtime state" below |

Consumers MUST request these by exact name. An unknown name raises `UnknownSecretError` at `get()` time; this is a build-time-discoverable error since the name list is closed.

## 5. State Machine

Not applicable in the deal-lifecycle sense, but the module itself has a small internal state machine for the rotation refresher loop:

```
   ┌──────────┐ start_refresher()  ┌──────────┐
   │ STOPPED  ├───────────────────▶│  IDLE    │
   └──────────┘                    └────┬─────┘
        ▲                               │ tick (every refresh_interval_s)
        │ stop_refresher()              ▼
        │                          ┌──────────┐
        │                          │ FETCHING │
        │                          └────┬─────┘
        │                               │ success / no-change
        │                               ▼
        │                          ┌──────────┐
        │                          │ NOTIFY   │ (run subscriber callbacks)
        │                          └────┬─────┘
        │                               │
        └───────────────────────────────┘
                            on fatal error after retries
```

Notes:
- `FETCHING → IDLE` on no-change (version unchanged) without invoking callbacks.
- Subscriber callbacks run sequentially with a per-callback timeout. A callback raising an exception is logged but does not block other subscribers and does not abort the refresher.
- The refresher process is a single asyncio task; only one rotation cycle is in flight at a time per process.

## 6. Data Model

This module owns no database tables. State is in-memory and in the upstream provider. However, it consumes two persistent sinks:

### Bootstrap audit log (file or stdout)

A newline-delimited JSON file written at `/var/log/secrets-bootstrap.log` (or stdout in containerized envs). One record per startup load and per rotation:

```json
{
  "ts": "2026-04-11T14:22:01Z",
  "event": "secret_loaded" | "secret_rotated" | "load_failed",
  "name": "openai_api_key",
  "version": "v17",
  "provider": "aws_secrets_manager",
  "env": "prod",
  "request_id": null
}
```

Never contains secret values, even partially. No prefix, no length, no hash that could be brute-forced.

### Provider-specific stores (read-only from this module's perspective)

| `SECRETS_PROVIDER` | Backing store | Notes |
|---|---|---|
| `aws_secrets_manager` | AWS Secrets Manager | Canonical for ECS deployment. Native rotation supported. |
| `flyio_secrets` | Fly.io app secrets (env vars at runtime) | Portfolio deployment. No native rotation; rotation = `fly secrets set` + app restart, refresher just no-ops. |
| `env_file` | A `.env` file on disk | Local dev only. Refresher polls file mtime. |
| `memory` | In-process dict, seeded by test fixture | Test only. Forbidden in non-test envs by validation. |

### Provider × environment validity matrix

| Env \ Provider | aws_secrets_manager | flyio_secrets | env_file | memory |
|---|---|---|---|---|
| `prod`     | ✅ canonical | ✅ portfolio only | ❌ | ❌ |
| `staging`  | ✅ | ✅ | ❌ | ❌ |
| `dev`      | ✅ | ✅ | ✅ | ❌ |
| `test`     | ❌ | ❌ | ✅ | ✅ |

Validation runs at bootstrap. Invalid combinations fail-fast with a clear error before any secret is loaded.

### Mutable runtime state (limited carve-out)

`AppConfig` is immutable post-load (invariant #4). However, two operational needs require values that mutate without a redeploy: rotated secrets (handled by the refresher loop) and the auth emergency-revocation watermark. Rather than introduce a separate `MutableConfig` slice for a single value, the watermark is stored as a named entry in `SecretsClient` (`auth_min_iat_watermark`) and reaches subscribers via the same refresher + callback machinery that secrets use. It is not actually secret material — its value can appear in logs without consequence — but it gets the rotation/subscribe semantics for free, and the bootstrap audit log already records `name` and `version` without the value, which is exactly the right shape.

Auth Service subscribes to `auth_min_iat_watermark` and rejects any token whose `iat` is older than the current watermark. Bumping the watermark via an out-of-band ops action (Terraform / `aws secretsmanager put-secret-value`) propagates to all verifiers within one refresher tick. This is the mechanism `auth_service.md` §11 "Key compromise response" depends on.

If a second mutable-but-not-secret value appears, this carve-out should be promoted to a real `MutableRuntimeState` slice — see §14 Open Question 1.

## 7. API Contract

No public HTTP API. This module is process-internal only. The "contract" is the Python interface:

```python
# Singleton accessors
def get_secrets() -> SecretsClient: ...
def get_config() -> AppConfig: ...
def get_kms() -> KMSClient: ...
def get_redaction_registry() -> RedactionRegistry: ...

# SecretsClient
class SecretsClient(Protocol):
    def get(self, name: str) -> SecretValue: ...
    def subscribe(self, name: str, callback: Callable[[SecretValue], Awaitable[None]]) -> SubscriptionHandle: ...
    async def start_refresher(self) -> None: ...
    async def stop_refresher(self) -> None: ...

# KMSClient
class KMSClient(Protocol):
    def encrypt(self, plaintext: bytes, encryption_context: dict[str, str]) -> Ciphertext: ...
    def decrypt(self, ciphertext: Ciphertext, encryption_context: dict[str, str]) -> bytes: ...
    def generate_data_key(self, context: dict[str, str]) -> DataKey: ...
```

The `encryption_context` parameter is a dict bound to the ciphertext as additional authenticated data. AWS KMS supports this natively; the `local_aes` dev provider implements it via AES-GCM AAD; the `none` provider raises on any call.

Bootstrap entrypoint:

```python
async def bootstrap() -> None:
    """Called once at process start, before any other module initializes.
       Loads config, validates schema, registers secrets in the redaction registry,
       starts the refresher loop. Raises BootstrapError on any failure."""
```

The application's main entrypoint must `await bootstrap()` before importing or instantiating any other module. The DI container fails any `get_*()` call if `bootstrap()` has not run.

**Subscriber registration semantics.** `subscribe(name, callback)` does *not* invoke `callback` at registration time. The initial value comes from a paired `get(name)` call that the consumer makes immediately after subscribing; the callback only fires on subsequent rotation ticks where the version has changed. This means a consumer module's startup sequence is always: `value = secrets.get(name); secrets.subscribe(name, on_rotate)` — never relying on the callback for the initial load. This avoids reentrancy concerns during bootstrap, when the event loop is up but other modules may not yet be ready to handle a callback.

## 8. Invariants

1. **No secret value ever appears in any log line, exception message, traceback, HTTP response, or audit record** — only the secret's name and version.
2. **`bootstrap()` is idempotent within a process and runs exactly once.** A second call is a no-op (returns the existing singleton); a call after `shutdown()` raises.
3. **A required secret missing at bootstrap fails the process** before any HTTP listener binds. There is no degraded mode for missing-required-secret.
4. **`AppConfig` is immutable after load.** Mutating it raises. Hot-reload of non-secret config is explicitly out of scope for v1. The single exception is the `auth_min_iat_watermark` entry, which lives in `SecretsClient` (not `AppConfig`) and reaches consumers via the rotation refresher — see §6 "Mutable runtime state".
5. **Provider × env validity matrix is enforced before any provider call** — no `memory` in prod, no `aws_secrets_manager` in test, etc.
6. **Subscriber callbacks are isolated**: one failing callback never prevents another subscriber from receiving the new value.
7. **The redaction registry is append-only at runtime.** Secrets loaded at bootstrap and on rotation are added; nothing is ever removed (a rotated-away value still gets redacted from any in-flight log records).
8. **Encryption context is mandatory for `KMSClient.encrypt`.** An empty dict raises. This forces every callsite to bind ciphertext to its semantic location (e.g. `{"tenant_id": ..., "field": "deal_notes"}`).
9. **`SecretValue.reveal()` is the only access path to raw bytes**, and every call site is grep-able. The module exposes a CI lint that fails the build if `.reveal()` appears outside an allowlist of files.

## 9. Trust Boundaries

| Boundary | From | To | Validation |
|---|---|---|---|
| Process env vars → bootstrap config | Untrusted (could be set by any container env) | Trusted (after schema validation) | Pydantic schema, env validity matrix |
| AWS Secrets Manager → SecretsClient | Trusted (IAM-scoped role) | Trusted | TLS, IAM role with least-privilege policy scoped to `secret_name_prefix/*` |
| Fly.io secrets → SecretsClient | Trusted (Fly.io platform) | Trusted | Runtime env vars, never written to disk |
| `.env` file → SecretsClient | Trusted in dev only; forbidden elsewhere | Trusted | File permissions checked (0600), provider validity matrix |
| KMS provider → KMSClient | Trusted | Trusted | IAM role, encryption context binding |
| SecretsClient → consumer module | Trusted | Trusted | Typed `SecretValue`, `.reveal()` lint |
| SecretsClient → log sink | Trusted | Trusted | Redaction registry applied by Observability formatter |

There is no untrusted-to-trusted boundary at the *interface* level; all trust transitions happen at provider load time and are validated then.

## 10. Failure Modes

| Failure | Detection | System Behavior | User-Facing Behavior |
|---|---|---|---|
| Required secret missing at bootstrap | Schema validation fails on `AppConfig` construction | Process exits non-zero before HTTP listener binds; bootstrap audit log records `load_failed` with name | Service does not come up; deployment health check fails; orchestrator rolls back |
| Required secret malformed (wrong type / fails validator) | Pydantic validation error | Same as missing — fail-fast, no partial start | Same |
| AWS Secrets Manager unreachable at bootstrap | SDK timeout / network error | Retry with exponential backoff up to `bootstrap_timeout_s` (default 30s), then fail-fast | Deployment fails health check |
| AWS Secrets Manager unreachable at rotation tick | SDK error in refresher loop | Log warning, keep current in-memory value, retry on next tick. Alert fires after `rotation_failure_threshold` consecutive failures (default 3) | None — service continues with last-known value |
| Secret rotated upstream but version unchanged in metadata (provider bug) | Refresher sees same version, skips notify | No-op | None |
| Subscriber callback raises | Caught in refresher | Log error with subscriber name + secret name (no value), continue with remaining subscribers | The specific consumer remains on the old value until next tick — flagged as a known degraded state |
| Subscriber callback hangs | Per-callback timeout (default 5s) | Cancel callback, log timeout, mark subscription unhealthy, continue | Same as above |
| Subscriber callback fails for `auth_signing_key_current` or `auth_signing_key_previous` | Refresher catches exception/timeout | **Escalate immediately** — page on-call rather than waiting for `rotation_failure_threshold`. Auth verifiers running on the old key set will reject any token signed with the new `kid` until recovery. Worst-case window is one `refresh_interval_s`. | Some users see spurious `401 UNAUTHORIZED` and are forced to re-login until recovery |
| KMS decrypt fails (wrong context, wrong key, ciphertext tampered) | KMS provider raises | Caller receives `DecryptError`. The calling module decides whether to fail the request or surface a typed error | The specific operation fails; service overall continues |
| `.reveal()` called outside allowlist | CI lint catches at build time | Build fails | N/A — never reaches production |
| Bootstrap called twice | Idempotency check | Second call returns existing singleton, logs WARN | None |
| Config mutation attempted at runtime | Pydantic frozen model raises | `TypeError` propagates to caller | The specific request fails; not a service-wide outage |
| Redaction registry consulted before bootstrap | Singleton accessor raises `BootstrapNotRunError` | Caller fails fast | Misconfiguration caught in dev; cannot occur in correctly-ordered prod startup |

## 11. Security Considerations

- **No secret in process memory longer than needed.** `SecretValue._value` is the only persistent in-memory copy. Consumers that briefly need raw bytes (`reveal()`) must not store the result in module-level globals; the `.reveal()` lint flags assignment to module scope.
- **No secret in container images, source control, CI logs, or deployment artifacts.** Bootstrap reads from the runtime provider only. CI uses a separate `test` env with the `memory` provider seeded by fixtures.
- **No secret in URL parameters, query strings, or HTTP headers** other than `Authorization`. This is enforced at the Dashboard API layer but originates as a Secrets-module invariant.
- **Encryption context binding** prevents ciphertext-swap attacks: a value encrypted for `{tenant_id: A, field: notes}` cannot be decrypted as `{tenant_id: B, field: notes}` even with the same KMS key.
- **IAM least privilege.** The AWS provider's IAM role is scoped to `secretsmanager:GetSecretValue` and `kms:Decrypt` / `kms:Encrypt` / `kms:GenerateDataKey` on the specific secret name prefix and KMS key ARN — nothing else. No `*` permissions, no broader Secrets Manager actions.
- **Redaction is best-effort but layered.** The first layer is `SecretValue.__repr__` overrides (compile-time defense). The second is the Observability log formatter consulting the redaction registry (runtime defense). The third is the bootstrap audit log being structurally incapable of carrying values (it doesn't accept a `value` field at all).
- **Rotation closes the window on a leaked secret without a redeploy.** The refresher loop is the operational mechanism that makes "rotate the JWT signing key" a 1-minute action rather than a 1-hour deployment.
- **No bootstrap secrets in env vars.** The env vars used by bootstrap (`APP_ENV`, `SECRETS_PROVIDER`, AWS region, etc.) are *configuration*, not secrets. The IAM role that grants access to the actual secrets is provided by the runtime (ECS task role, Fly.io machine identity), not by an env-var credential.
- **Test fixtures cannot leak into prod.** The `memory` provider is forbidden outside `APP_ENV=test` by the validity matrix and by a CI check that asserts no test fixture imports from a prod entrypoint.
- **Audit log integrity.** The bootstrap audit log is written before Observability comes up, so it can't be tampered via the structured logger. In prod it is shipped to a separate log sink (CloudWatch Logs with a write-only role) for forensic use.

## 12. Dependencies

**Internal modules:** none. This is the foundation module.

**External services:**
- AWS Secrets Manager (canonical, prod/staging/dev)
- AWS KMS (canonical, prod/staging/dev)
- Fly.io secrets store (portfolio deployment)

**Libraries:**
- `pydantic>=2` for `AppConfig` schema and validation
- `boto3` for AWS Secrets Manager + KMS clients (lazy-imported only when `SECRETS_PROVIDER=aws_secrets_manager`)
- `cryptography` for the `local_aes` dev KMS provider (AES-GCM with AAD)
- `python-dotenv` for the `env_file` dev provider
- Standard library `asyncio`, `logging`, `dataclasses`, `pathlib`

No dependency on any other application module. This is enforced by an import-linter rule: `secrets_config` may not import from any other `app.modules.*` package.

## 13. Testing Requirements

### Unit tests
- `AppConfig` schema accepts a complete valid config and rejects each required field missing, in turn
- `Argon2Params` validator rejects `memory_kib < 65536`, `iterations < 3`, or `parallelism < 1`; accepts the floor exactly
- `RateLimitConfig.buckets` parses the full 8-row matrix from a structured source; missing a required (scope, group) pair fails validation
- `RateLimitConfig.trusted_proxy_cidrs` rejects malformed CIDRs and an empty list (an empty trusted-proxy list is almost always a misconfig — explicit opt-out via a sentinel is required)
- Subscribing to `auth_min_iat_watermark` and bumping its version in the mocked provider invokes the registered callback within one refresher tick
- `AppConfig` rejects values that look like known secret patterns (e.g. a string starting with `sk-` in a non-secret field)
- Provider × env validity matrix: every invalid combination raises at bootstrap, every valid one succeeds
- `SecretValue.__repr__` and `__str__` never include the value, even when wrapped in f-strings, formatted via `%`, or passed to `logging.info("%s", sv)`
- `SecretValue.reveal()` returns the original bytes
- Redaction registry: a log record containing a registered value is scrubbed; one without is unchanged; rotation adds the new value to the registry without removing the old
- Refresher: version-unchanged tick does not invoke callbacks; version-changed tick invokes all subscribed callbacks exactly once
- Refresher: a callback that raises does not prevent subsequent callbacks from running; a callback that hangs is cancelled at the timeout
- KMS encrypt/decrypt round-trip with matching encryption context succeeds; mismatched context fails
- KMS encrypt with empty encryption context raises
- `bootstrap()` is idempotent: second call returns the same singletons
- `get_*()` accessors before `bootstrap()` raise `BootstrapNotRunError`

### Integration tests
- Full bootstrap against a `moto`-mocked AWS Secrets Manager + KMS, loading 10 secrets, verifying audit log entries
- Full bootstrap against a `.env` file in `dev` env, verifying `prod` env rejects the same provider
- End-to-end rotation: write a new secret version to mocked AWS, advance the refresher tick, verify a registered subscriber receives the new value within one tick interval
- Bootstrap failure: required secret missing → process exits non-zero, audit log contains `load_failed` entry, no HTTP listener bound
- Encryption context binding: encrypt under context A, attempt decrypt under context B → fails with `DecryptError`

### Security tests
- Grep the entire test output and log files after the full test suite for any known fixture secret value — must find zero matches
- Lint test: insert a `.reveal()` call in a non-allowlisted file → CI lint must fail
- Lint test: insert an import of `boto3` from a module other than `secrets_config` → import-linter must fail
- Audit log structural test: attempt to write a record with a `value` field → must raise (the audit writer enforces the schema)
- IAM scope test (in deployment CI): the canonical Terraform IAM role is asserted to contain only the four expected actions on the expected resource ARNs

### Property tests
- For any random byte sequence, `kms.decrypt(kms.encrypt(b, ctx), ctx) == b`
- For any random byte sequence and any context mismatch, `kms.decrypt(kms.encrypt(b, ctx_a), ctx_b)` raises

## 14. Open Questions

1. **Hot-reload of non-secret config.** v1 freezes `AppConfig` post-load. A future need to flip a feature flag without a redeploy would require either (a) a separate `MutableConfig` slice with explicit subscriber semantics, or (b) integrating a dedicated feature-flag service. Deferred until a concrete trigger appears. Logged as Medium risk in the Evolution Check pending update.
2. **Rotation cadence per secret.** Currently the refresher polls all secrets at the same interval (default 60s). Some secrets (JWT signing key) should rotate slowly and be polled rarely; others (DB credentials with short-lived AWS RDS IAM auth) need to refresh much more often. Per-secret intervals are a clean extension but add config surface — deferred.
3. **Subscriber callback ordering.** Currently sequential in registration order. If two consumers depend on the same secret and one must update before the other (e.g. cache invalidation before client rebuild), they must register in order. A topological-ordering API may be needed if this becomes painful.
4. **Bootstrap audit log destination on Fly.io.** ECS/CloudWatch is straightforward; on Fly.io the bootstrap log goes to stdout and is captured by `fly logs`, but there's no separate write-only sink. Acceptable for portfolio phase; would not be acceptable for SOC 2.
5. **`local_aes` dev provider key storage.** Currently the dev key is read from `LOCAL_KMS_KEY_HEX` env var. This is fine for solo dev but awkward for shared dev environments. An option to derive from a developer's OS keychain is plausible but adds platform-specific code. Deferred.
6. **Test for `.reveal()` lint coverage.** The lint allowlist itself needs review periodically — a file added to the allowlist for a legitimate reason may later have a different author add a leak. A quarterly review of the allowlist is in `06_operations.md` rather than enforced in code. Whether that's sufficient is an open question.