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
