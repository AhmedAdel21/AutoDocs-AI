import pytest

from app.services.embedding import embed_text, embed_texts, EXPECTED_DIM


@pytest.mark.asyncio
async def test_embed_single_text_returns_correct_dim():
    vec = await embed_text("the quick brown fox")
    assert len(vec) == EXPECTED_DIM
    assert all(isinstance(v, float) for v in vec)


@pytest.mark.asyncio
async def test_embed_batch_returns_one_vector_per_input():
    texts = ["first text", "second text", "third text"]
    vecs = await embed_texts(texts)
    assert len(vecs) == 3
    for v in vecs:
        assert len(v) == EXPECTED_DIM


@pytest.mark.asyncio
async def test_normalized_embeddings_have_unit_length():
    """If normalize_embeddings=True, vectors should have L2 norm ~1.0."""
    vec = await embed_text("test")
    norm = sum(x * x for x in vec) ** 0.5
    assert abs(norm - 1.0) < 0.01  # within float tolerance
