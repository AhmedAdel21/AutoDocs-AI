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