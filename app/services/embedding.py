"""Embedding service — local inference via sentence-transformers.

Singleton-ish: the model is loaded once per process and reused.
Loading takes ~5 seconds the first time (downloads weights, ~100MB).
Inference is batchable — 32 chunks at once is much faster than 32 separate calls.
"""

import asyncio
from typing import Sequence

import numpy as np
from sentence_transformers import SentenceTransformer

from app.observability import tracer


# Singleton model instance — loaded lazily on first use
_model: SentenceTransformer | None = None
_model_lock = asyncio.Lock()


# Configuration — pinned to match the EMBEDDING_DIM=384 in document.py
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EXPECTED_DIM = 384
DEFAULT_BATCH_SIZE = 32


async def get_embedding_model() -> SentenceTransformer:
    """Lazily load the model once per process. Subsequent calls return the same instance.

    Why async-locked: model loading is a 5-second blocking operation. Without the
    lock, two concurrent requests both trigger the load, causing duplicate downloads
    and double memory.
    """
    global _model
    if _model is not None:
        return _model

    async with _model_lock:
        if _model is not None:  # double-check after acquiring
            return _model
        # SentenceTransformer.__init__ is sync and slow — run in a thread
        # so we don't block the event loop
        _model = await asyncio.to_thread(SentenceTransformer, MODEL_NAME)
        # Sanity check the dimension matches our DB schema
        sample_embed = _model.encode(["sanity check"], show_progress_bar=False)
        actual_dim = sample_embed.shape[1]
        if actual_dim != EXPECTED_DIM:
            raise RuntimeError(
                f"embedding model produced dim={actual_dim}, schema expects {EXPECTED_DIM}. "
                f"Migration needed."
            )
    return _model


async def embed_texts(
    texts: Sequence[str],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[list[float]]:
    """Embed a batch of texts, returning a list of vectors (each is a list of floats).

    Returns: list of length len(texts), each item is a 384-dim list of floats.
    """
    if not texts:
        return []

    with tracer.start_as_current_span("embedding.embed_texts") as span:
        span.set_attribute("embedding.count", len(texts))
        span.set_attribute("embedding.batch_size", batch_size)

        model = await get_embedding_model()

        # SentenceTransformer.encode is sync. Run in a thread to avoid blocking the loop.
        # convert_to_numpy=True (default) returns a numpy array (n, dim)
        embeddings: np.ndarray = await asyncio.to_thread(
            model.encode,
            list(texts),
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,  # L2-normalize so cosine similarity == dot product
        )

        span.set_attribute("embedding.dim", embeddings.shape[1])

        # Convert to plain Python lists for SQLAlchemy + pgvector
        return embeddings.tolist()


async def embed_text(text: str) -> list[float]:
    """Embed a single text. Convenience wrapper."""
    results = await embed_texts([text])
    return results[0]
