from app.services.chunking import chunk_text


def test_short_text_one_chunk():
    text = "Hello world."
    chunks = chunk_text(text, chunk_size=100, chunk_overlap=50)
    assert len(chunks) == 1
    assert chunks[0].content == "Hello world."
    assert chunks[0].start_char == 0
    assert chunks[0].end_char == 12


def test_long_text_multiple_chunks_with_overlap():
    # 5 paragraphs of ~200 chars each
    paragraphs = [f"Paragraph {i}. " + "word " * 40 for i in range(5)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_text(text, chunk_size=300, chunk_overlap=50)
    assert len(chunks) > 1
    # All chunks should be within size
    for c in chunks:
        assert c.length <= 350  # chunk_size + small slack for boundary respecting
    # Offsets should be monotonically non-decreasing
    for i in range(1, len(chunks)):
        assert chunks[i].start_char >= chunks[i - 1].start_char


def test_overlap_preserves_concepts_across_boundaries():
    text = "First. Second. Third. " * 50  # repetitive
    chunks = chunk_text(text, chunk_size=100, chunk_overlap=30)
    # Consecutive chunks should share some content via overlap
    if len(chunks) >= 2:
        # The end of chunk 0 should appear in the start of chunk 1 (overlap)
        # We're not asserting exact substring because of word-boundary trimming,
        # but offsets should overlap
        assert chunks[0].end_char > chunks[1].start_char
