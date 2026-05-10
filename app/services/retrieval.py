"""Retrieval service — vector similarity search over document_chunks.

Given a query, embed it, then find the top-k nearest chunks by cosine similarity.
Returns chunks with their parent document for citation rendering.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.document import Document, DocumentChunk
from app.services.embedding import embed_text
from app.observability import tracer


@dataclass(frozen=True)
class RetrievalResult:
    chunk: DocumentChunk
    document: Document
    similarity: float  # 0..1, where 1 = identical


# Tunable: minimum similarity to consider a chunk "relevant"
# Below this we don't return the chunk — informs the LLM "no relevant context found"
DEFAULT_SIMILARITY_THRESHOLD = 0.3


async def retrieve_chunks(
    db: AsyncSession,
    *,
    query: str,
    top_k: int = 5,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[RetrievalResult]:
    """Embed the query and find the top-k most-similar chunks.

    Returns chunks above the similarity threshold, sorted by similarity descending.
    May return fewer than top_k if not enough chunks exceed the threshold —
    that's the signal to the LLM that retrieval didn't find strong matches.
    """
    with tracer.start_as_current_span("retrieval.search") as span:
        span.set_attribute("retrieval.top_k", top_k)
        span.set_attribute("retrieval.threshold", similarity_threshold)

        # Embed the query
        query_embedding = await embed_text(query)

        # pgvector cosine distance operator: <=>
        # cosine_distance ranges 0..2 (0 = identical, 1 = orthogonal, 2 = opposite)
        # similarity = 1 - cosine_distance/2 maps to 0..1 where 1 = identical
        # We use 1 - distance because vectors are L2-normalized, so cosine_distance is in [0, 2]
        # but practically [0, 1] for our embeddings (semantic vectors don't go opposite)
        stmt = (
            select(
                DocumentChunk,
                (1 - DocumentChunk.embedding.cosine_distance(query_embedding)).label(
                    "similarity"
                ),
            )
            .options(joinedload(DocumentChunk.document))
            .order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
            .limit(top_k)
        )

        rows = (await db.execute(stmt)).all()

        results: list[RetrievalResult] = []
        for chunk, similarity in rows:
            if similarity >= similarity_threshold:
                results.append(
                    RetrievalResult(
                        chunk=chunk,
                        document=chunk.document,
                        similarity=float(similarity),
                    )
                )

        span.set_attribute("retrieval.results_count", len(results))
        if results:
            span.set_attribute("retrieval.top_similarity", results[0].similarity)

        return results
