# AutoDocs AI

Internal Q&A platform for automotive engineers over technical specs (AUTOSAR, ISO 26262, supplier datasheets).
Engineers ask questions in natural language; the system retrieves relevant spec chunks and generates answers grounded in those sources, with citations.

## Why this project

Demonstrates senior full-stack capability across:

- FastAPI + Pydantic v2 + SQLAlchemy 2.0 async
- Cursor pagination, optimistic locking, soft deletes, error envelope
- JWT auth with refresh rotation + Redis denylist (Day 2)
- LangChain LCEL retrieval chain with pgvector (Days 5-7)
- SSE streaming with discriminated event types
- Next.js 14 App Router frontend with TanStack Query + Tailwind tokens
- OpenTelemetry instrumentation
- Real EXPLAIN ANALYZE before/after on a 1M-row search query

## Architecture highlights

- **No starter kit.** Every file in this repo is hand-written and defensible.
- **Single database.** Postgres + pgvector. The decision NOT to add Mongo is logged in DECISIONS.md.
- **Cursor pagination throughout.** Offset pagination doesn't survive at scale.
- **Three Pydantic schemas per resource** (Create / Update / Read). Prevents leak and over-acceptance.
- **Error envelope on every error.** Machine + human + structured details.

## Quickstart

\`\`\`powershell

# 1. Postgres

docker run --name autodocs-pg -e POSTGRES_USER=autodocs -e POSTGRES_PASSWORD=autodocs -e POSTGRES_DB=autodocs -p 5432:5432 -d postgres:16

# 2. Python

uv venv
.venv\Scripts\activate
uv sync

# 3. Migrate

alembic upgrade head

# 4. Run

uvicorn app.main:app --reload
\`\`\`

Open <http://localhost:8000/docs>.

## Status

Day 1/14. See DAY_NN.md for the day-by-day build log. See DECISIONS.md for trade-offs.

## Auth flow

1. POST /api/v1/auth/login → returns access + refresh tokens
2. Access token: 15-minute TTL, sent as `Authorization: Bearer <token>`
3. On 401, client calls POST /api/v1/auth/refresh with the refresh token
4. Refresh issues a new pair AND denylists the old refresh's jti (rotation)
5. Logout denylists current access AND refresh tokens

Refresh-token reuse triggers an immediate 401 with code `unauthorized` — possible token theft.

## Permission model

- **RBAC:** ENGINEER, LEAD, ADMIN. Most endpoints require LEAD or ADMIN.
- **ABAC:** GET /users/{id} allows self-read regardless of role.
- **Idempotency:** POST /users honors `Idempotency-Key` header. 24h TTL.
- **Audit:** Every state-changing action writes to audit_logs in the same transaction.

docker exec -it autodocs-pg psql -U autodocs -d autodocs

## RAG pipeline (Days 5-7)

Day 5 (today): document ingestion. Recursive chunking with overlap, local sentence-transformers embeddings, pgvector HNSW index, content-hash idempotency, atomic single-transaction ingestion.

Day 6: frontend integration — document upload UI, document list with status indicator.

Day 7: LangChain LCEL retrieval chain. Layered on top of the manual primitives built in Day 5 — same algorithm, abstracted.

### Key files

- `app/services/chunking.py` — recursive character splitter with overlap
- `app/services/embedding.py` — local embedding via sentence-transformers
- `app/services/ingestion.py` — atomic ingest pipeline
- `app/services/retrieval.py` — vector similarity search with threshold guard
