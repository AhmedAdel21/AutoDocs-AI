# Architecture Decisions

## Format

Each entry: **Decision**, **Rejected**, **Why**, **Revisit when**.

---

## D001 — App factory pattern over module-level `app`

**Decision:** `create_app()` factory in `main.py`, called once at module level.
**Rejected:** Module-level `app = FastAPI()`.
**Why:** Tests can build apps with overridden settings. Multi-process workers get fresh state.
**Revisit when:** Never. Industry standard.

## D002 — uv over pip + requirements.txt

**Decision:** uv with pyproject.toml + uv.lock.
**Rejected:** pip + requirements.txt; poetry.
**Why:** uv is 10-100x faster, has proper lockfile, single tool for envs and packages.
**Revisit when:** If a CI provider doesn't support uv. (Unlikely; GitHub Actions supports it natively.)

## D003 — UUID v4 over autoincrement int for user IDs

**Decision:** UUID v4 primary keys.
**Rejected:** BIGSERIAL int.
**Why:** No enumeration leak, distributed-friendly, no central sequence bottleneck.
**Trade-off acknowledged:** UUIDs are 16 bytes vs 8, indexes are larger. Not material at our scale.
**Revisit when:** If we ever need monotonic IDs for cursor pagination. (We use created_at + UUID, so we're fine.)

## D004 — Soft delete via deleted_at, not hard delete

**Decision:** `deleted_at` timestamp column. Indexed. List queries filter `WHERE deleted_at IS NULL`.
**Rejected:** Hard delete.
**Why:** Audit trail, undo support, regulated industries (Valeo's customers) don't allow data loss.
**Revisit when:** If we add explicit GDPR right-to-erasure flow — that DOES hard-delete or anonymize.

## D005 — Optimistic locking via version column

**Decision:** `version` integer, bumped on every UPDATE. Client sends version they read; mismatch = 409.
**Rejected:** Pessimistic locking (SELECT ... FOR UPDATE).
**Why:** Pessimistic locking serializes writes, doesn't scale. Most updates don't conflict; the few that do, retry.
**Revisit when:** If we have a hot row with constant contention. Move that specific resource to pessimistic.

## D006 — Three Pydantic schemas (Create / Update / Read), not one

**Decision:** UserCreate has password, UserRead doesn't. UserUpdate has all-optional fields.
**Rejected:** Single User schema for in and out.
**Why:** Prevents leaking sensitive fields, prevents accepting fields the client shouldn't set (id, timestamps).
**Revisit when:** Never. Standard practice.

## D007 — Cursor pagination over offset

**Decision:** Opaque base64 cursor encoding (created_at, id) tuple.
**Rejected:** OFFSET/LIMIT.
**Why:** Offset is O(N) — scans and discards at deep pages. Cursor is O(log N) via index.
**Revisit when:** If clients need "jump to page 50" UX. Add a separate offset endpoint then; keep cursor as default.

## D008 — Postgres only on Day 1, no MongoDB

**Decision:** Single Postgres database for users, audit logs (later), AI chat history (later).
**Rejected:** Postgres + MongoDB hybrid.
**Why:** Postgres handles JSONB and high write volume fine. Two databases = two operational surfaces, two backup strategies, two failure modes. Justify Mongo by showing Postgres can't handle it; we haven't.
**Revisit when:** If audit log write volume exceeds 10k/sec sustained. Until then, partition by month.

## D009 — bcrypt for password hashing, called directly (no passlib)

**Decision:** bcrypt algorithm, via the `bcrypt` package called directly. No `passlib` wrapper.
**Rejected:**

- *Algorithm:* argon2.
- *Wrapper:* `passlib[bcrypt]`.
**Why:**
- *Algorithm:* bcrypt has wider ecosystem maturity. argon2 is technically newer/better but bcrypt is battle-tested.
- *Wrapper:* passlib's last release was Oct 2020 and is broken with bcrypt ≥ 4.1 — its `detect_wrap_bug` init probe hashes a >72-byte string, which bcrypt 4.x rejects with `ValueError`. Backend init fails, so every `pwd_context.hash()` 500s. Calling `bcrypt` directly is ~10 lines, removes a dead dependency, and avoids being downstream of an unmaintained shim.
**Revisit when:** If we need GPU-resistance for high-value accounts, reconsider argon2 (still via the dedicated library, not passlib).

## D010 — Error envelope: {error: {code, message, details}}

**Decision:** Every error response uses the same shape.
**Rejected:** Bare `{detail: "..."}` (FastAPI default).
**Why:** Machine-readable code, human-readable message, structured details. Clients write one error parser.
**Revisit when:** Never. Foundational.

## D011 — Partial index for active-user pagination; no standalone `deleted_at` index

**Decision:** `CREATE INDEX ix_users_active_pagination ON users (created_at DESC, id DESC) WHERE deleted_at IS NULL`. Standalone `ix_users_deleted_at` dropped.
**Rejected:**

- Plain composite `(created_at, id)` with no `WHERE` clause.
- Separate indexes on `deleted_at` and `(created_at, id)`.
**Why:** The list query is *always* `WHERE deleted_at IS NULL ORDER BY created_at DESC, id DESC`. A partial index whose columns, sort direction, and filter exactly match that query lets Postgres index-scan with no `Sort` node and no row-filtering step — actually fulfilling D007's "O(log N) via index" claim. DESC in the index matches the query, so no backward-scan-with-tiebreaker subtleties. Dropping the standalone `deleted_at` index avoids paying for a second index that the partial fully subsumes for the hot path.
**Trade-off acknowledged:** Admin queries like "list recently soft-deleted users" lose their index and seq-scan. Acceptable: those queries are rare and don't run on the hot path.
**Revisit when:** If we add a frequent admin/audit endpoint over deleted users — re-add a `WHERE deleted_at IS NOT NULL` partial index then.

## D012 — Composite index for audit log target queries

**Decision:** ix_audit_logs_target_created_id ON audit_logs (target_user_id, created_at DESC, id DESC).
**Rejected:** Separate indexes on each column.
**Why:** The query is always "WHERE target_user_id = ? ORDER BY created_at DESC, id DESC". A composite covers all three predicates with one index walk. Separate indexes force the planner to bitmap-merge — slower at this query shape.
**Revisit when:** If we add queries by actor_user_id or by action — those need their own indexes.

## D013 — PATCH /users/{id} self-edit deferred to Day 8

**Decision:** PATCH currently admin-only. Self-edit deferred until frontend lands.
**Rejected:** Build self-edit on Day 2.
**Why:** Self-edit needs field-level permissions (user can change full_name but not role). Adding without frontend would mean designing the rules without a real client. Defer until we have the call site.
**Revisit when:** Day 8 frontend dashboard exists.

## D014 — JWT HS256 over RS256

**Decision:** HS256 (symmetric, single secret).
**Rejected:** RS256 (asymmetric, public/private key pair).
**Why:** Single-service app. RS256's value is letting consumers verify tokens without sharing the signing secret — relevant for microservices, not for a monolith. HS256 is simpler and the secret stays inside the service.
**Revisit when:** If we split the API into multiple services that all need to verify tokens issued by an auth service.

## D015 — Refresh-token rotation with Redis denylist

**Decision:** Each refresh issues a new pair AND denylists the old refresh token's jti. TTL = remaining token lifetime.
**Rejected:** Plain refresh (no rotation); revoke-all-tokens-on-event approach.
**Why:** Stateless JWTs can't be revoked before exp without state somewhere. Rotation gives us short access tokens (15 min) with long-lived refresh capability AND theft detection: if a refresh is reused, the second use is denied — that's the signal somebody stole it. Denylist TTL = remaining token lifetime so Redis self-cleans.
**Trade-off acknowledged:** Single Redis means single point of failure for revocation. Production: Redis cluster, or fall back to allowing tokens through if Redis is unreachable (depending on threat model).
**Revisit when:** If we need cross-region replication of denylist (Redis cluster + replication).

## D016 — Always run verify_password to defend against timing attacks

**Decision:** Run bcrypt verify even when user lookup returns None.
**Rejected:** Short-circuit return on user-not-found (faster).
**Why:** A timing-side-channel attacker can distinguish "user exists, wrong password" from "user doesn't exist" by response latency. Short-circuit on None creates a measurable diff. Always-verify keeps timings consistent.
**Trade-off:** Wasted CPU on bcrypt for nonexistent users (~250ms each). Cost is bounded by login rate limiting.
**Revisit when:** If we add username enumeration via a different endpoint, this defense is moot anyway and we can drop it.

## D017 — Idempotency-Key via Redis SET NX EX, 24h TTL

**Decision:** POST /users honors Idempotency-Key header. Storage: Redis with atomic SET NX EX. Sentinel for in-progress; cached JSON response on completion. 24-hour TTL.
**Rejected:** No idempotency (rely on unique constraint to fail loud); database-backed idempotency table.
**Why:** Redis SET NX EX is atomic — guarantees one winner on race. 24h covers any reasonable retry window from clients, load balancers, browser back-button. DB-backed would work but adds a table, a write per request, and competes with the same transaction the request is making.
**Trade-off:** Body of cached response is captured assuming JSON. Streaming or binary responses need a different cache strategy.
**Revisit when:** Adding idempotency to streaming endpoints (Day 5+).

## D018 — Audit logs in same DB transaction as the change they audit

**Decision:** write_audit() adds to the session, doesn't commit. Endpoint's commit covers both the change and the audit.
**Rejected:** Async fire-and-forget audit logging; separate audit service.
**Why:** Atomicity. If the change rolls back, the audit rolls back. We never have an audit entry for a state change that didn't happen, OR a state change without an audit entry. Both halves of the bug-state-space eliminated.
**Trade-off:** Audit failures cause user-facing failures. Acceptable: audit failures should be loud anyway.
**Revisit when:** If audit volume causes write-amplification on the hot path. Then partition audit_logs by month; later, archive to cold storage.

## D019 — audit_logs not partitioned today; revisit at 10M rows

**Decision:** Single-table audit_logs without partitioning on Day 3.
**Rejected:** Declarative partitioning by created_at from day one.
**Why:** Premature complexity. At 200k rows we have no measured pain. Partitioning would force a 2-column PK (id, created_at) and add migration overhead. Defer until measured cost.
**Migration plan when revisiting:** CREATE audit_logs_partitioned PARTITION BY RANGE (created_at), copy data, switch names atomically. Monthly partitions. Lifecycle to object storage at 90 days.
**Revisit when:** row count crosses 10M OR audit-list query exceeds 100ms p95.

## D020 — slowapi for rate limiting over custom Redis INCR

**Decision:** slowapi with Redis backend.
**Rejected:** Custom Redis INCR + EXPIRE on every endpoint.
**Why:** slowapi is battle-tested, handles clock skew, has decorator integration. Rebuilding a sliding-window rate limiter is a footgun — easy to write a leaky one.
**Trade-off:** library dependency, but minimal surface area. The custom per-email layer (Hour 4.4) is justified because slowapi's keying doesn't natively support multi-key limits.
**Revisit when:** if we outgrow slowapi for advanced features (token bucket vs sliding window, distributed quotas).

## D021 — OpenTelemetry from Day 3, console exporter in dev

**Decision:** OTel API + SDK with console exporter in dev, OTLP-ready for prod.
**Rejected:** No tracing; ad-hoc logging only.
**Why:** Distributed tracing is the only way to debug a slow request without guessing. Custom spans on audit/token ops capture business operations, not just HTTP/SQL.
**Revisit when:** never. Production-shaped systems need this.

## D022 — Structured logging via structlog with OTel trace_id correlation

**Decision:** structlog with JSON in prod, ConsoleRenderer in dev. Custom processor injects trace_id/span_id from OTel.
**Rejected:** stdlib logging with format strings.
**Why:** Logs need to be machine-parseable in prod (log aggregator ingestion) and human-readable in dev. Trace correlation lets us click from a log line to its trace and back.
**Revisit when:** never.

## D023 — Per-IP and per-email rate limits on /login

**Decision:** Two layers: 5/min per IP (slowapi), 10/hour per email (custom Redis).
**Rejected:** Per-IP only.
**Why:** Per-IP defends scripted-from-one-source brute force. Per-email defends credential stuffing where attackers rotate IPs.
**Trade-off:** Per-email limit can be abused to lock out a specific user (denial-of-service-via-rate-limit). Mitigation: clear the limit on successful login.
**Revisit when:** If we add SSO and emails come from a verified IDP, per-email becomes less useful.

## D024 — Liveness vs readiness probes are distinct

**Decision:** /health is process liveness only (no deps). /health/ready checks Postgres + Redis, returns 503 on dep failure.
**Rejected:** Single /health endpoint that checks dependencies.
**Why:** Conflating them causes restart loops on transient dep outages. Liveness must be self-contained. Readiness controls traffic, liveness controls process restart.
**Revisit when:** never.

## D025 — Graceful shutdown on SIGTERM via lifespan

**Decision:** lifespan handler awaits engine.dispose() and Redis close on shutdown.
**Rejected:** No explicit shutdown handling.
**Why:** Cloud Run sends SIGTERM 10s before SIGKILL. Without graceful shutdown, in-flight queries get cancelled mid-execution and connections leak.
**Revisit when:** never.

## D026 — Frontend in separate repo

**Decision:** autodocs (backend) and autodocs-web (frontend) are separate repos.
**Rejected:** Monorepo with both top-level folders.
**Why:** Different toolchains (Python/Node), different CI eventually, different deploy targets. Monorepo would need workspace tooling (Turborepo, Nx) which adds friction at this stage.
**Revisit when:** If we publish a shared types package (e.g., generated from OpenAPI) — monorepo wins for that case.

## D027 — CORS: explicit origin allowlist, not wildcard

**Decision:** allow_origins=["http://localhost:3000"] in dev, prod-domain only in prod.
**Rejected:** allow_origins=["*"].
**Why:** Wildcard + credentials is forbidden by browsers. Explicit allowlist is also defense-in-depth.
**Revisit when:** never; this is foundational.
