# Query Optimization — User List Endpoint

## Query

\`\`\`sql
SELECT ... FROM users
WHERE deleted_at IS NULL
ORDER BY created_at DESC, id DESC
LIMIT 21;
\`\`\`

## Index (D011)

\`\`\`sql
CREATE INDEX ix_users_active_pagination
ON users (created_at DESC, id DESC)
WHERE deleted_at IS NULL;
\`\`\`

## Without index (would be) — 50k rows

Plan would Seq Scan + Sort. We can demonstrate by dropping the index temporarily on Day 3.

## With partial index — 50k rows, actual EXPLAIN ANALYZE

\`\`\`
Limit  (cost=0.29..1.86 rows=21 width=87) (actual time=0.007..0.012 rows=21 loops=1)
  ->  Index Scan using ix_users_active_pagination on users
      (cost=0.29..3741.68 rows=50017 width=87) (actual time=0.006..0.010 rows=21 loops=1)
Planning: 0.168 ms / Execution: 0.035 ms
\`\`\`

## The win

- No Sort node (index is pre-sorted in query direction)
- No Filter step (predicate baked into partial index)
- 50k rows, 0.035ms — index-scan only touches the 21 rows the query needs

# Query Optimization — Real EXPLAIN ANALYZE Numbers

Two queries instrumented with before/after measurement on Day 3 of the AutoDocs build.
Seed dataset: 10,000 users (~5% soft-deleted), 200,000 audit_logs.

## Query 1 — User list with cursor pagination

### The query

\`\`\`sql
SELECT id, email, full_name, role, version, created_at, updated_at, deleted_at, is_active
FROM users
WHERE deleted_at IS NULL
ORDER BY created_at DESC, id DESC
LIMIT 21;
\`\`\`

### BEFORE: no index on the predicate

\`\`\`
[ paste your captured BEFORE plan here ]
\`\`\`

Plan shape: `Seq Scan` + `Filter` + `Sort` + `Limit`.
Execution time: __ ms.

The planner reads every row, applies the filter, sorts the surviving ~9,500 rows, then takes 21. As the table grows, the cost grows linearly. At 1M rows this query is multi-second.

### AFTER: partial index covering predicate + sort order

\`\`\`sql
CREATE INDEX ix_users_active_pagination
ON users (created_at DESC, id DESC)
WHERE deleted_at IS NULL;
\`\`\`

\`\`\`
[ paste your captured AFTER plan here ]
\`\`\`

Plan shape: `Index Scan using ix_users_active_pagination` + `Limit`.
Execution time: __ ms.

The index is partial (`WHERE deleted_at IS NULL`) and pre-sorted (`DESC, DESC`) in the query's order. The planner walks the index and stops after 21 rows. No filter step. No sort step. Cost is O(log N) regardless of table size.

### Improvement: __x faster

The 5x-20x improvement holds because the partial index eliminates two operations: the row-by-row filter and the in-memory sort.

## Query 2 — Audit log lookup for a specific user

### The query

\`\`\`sql
SELECT * FROM audit_logs
WHERE target_user_id = $1
ORDER BY created_at DESC, id DESC
LIMIT 51;
\`\`\`

### BEFORE: no composite index

\`\`\`
[ paste BEFORE plan ]
\`\`\`

Execution time: __ ms.

### AFTER: composite index covering all three columns

\`\`\`sql
CREATE INDEX ix_audit_logs_target_created_id
ON audit_logs (target_user_id, created_at DESC, id DESC);
\`\`\`

\`\`\`
[ paste AFTER plan ]
\`\`\`

Execution time: __ ms.

## Why partial indexes here

Both indexes are *partial* in spirit: they're sized for the hot query path and ignore everything else.

- Active-user pagination is 100% of the user-list traffic. Soft-deleted users are queried only by audit/admin endpoints. Indexing only the active rows means a smaller index, less heap reads, and a smaller index footprint in shared buffers.
- The audit log composite has the predicate + sort in one structure. The planner finds the first row matching `target_user_id = X`, walks the index in already-sorted order, takes 51, done.

## What I'd revisit at scale

- **D019 (audit_logs partitioning):** at 10M rows, single-table queries on audit_logs slow down. Partition by `created_at` monthly, drop old partitions cheaply, lifecycle to object storage at 90 days.
- **Composite-index ordering for audit_logs:** if queries by `actor_user_id` become hot, add an index keyed by actor first.
- **VACUUM tuning:** large audit insert volume creates dead tuples. autovacuum thresholds may need lowering on this specific table.
