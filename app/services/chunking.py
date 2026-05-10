"""Document chunking — recursive character splitting with overlap.

Strategy:
1. Try splitting on paragraph breaks (\n\n).
2. If a piece is still too large, split on sentence boundaries (. ! ?).
3. If still too large, split on word boundaries.
4. Add overlap between consecutive chunks so concepts spanning the boundary
   are present in both.

This is similar to LangChain's RecursiveCharacterTextSplitter, but built
manually so we understand every line. Day 7 we'll see the LangChain version
and recognize the same logic.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """A chunk of text with character offsets in the original document."""

    content: str
    start_char: int
    end_char: int

    @property
    def length(self) -> int:
        return len(self.content)


# Tunable constants — D028 in DECISIONS.md documents the choice
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100

# Splitters in order of preference
PARAGRAPH_SEP = "\n\n"
SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")  # split on . ! ? followed by whitespace
WORD_PATTERN = re.compile(r"\s+")


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """Split text into overlapping chunks, preserving natural boundaries when possible.

    Returns a list of Chunk objects with start_char/end_char in the original text.
    """
    if not text:
        return []
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be less than chunk_size")

    # Step 1: split into paragraphs
    paragraphs = _split_with_offsets(text, PARAGRAPH_SEP)

    # Step 2: for each paragraph that's too long, split into sentences (and so on)
    pieces: list[Chunk] = []
    for para in paragraphs:
        if para.length <= chunk_size:
            pieces.append(para)
        else:
            pieces.extend(_split_recursively(para, chunk_size))

    # Step 3: merge small pieces into chunks of target size, with overlap
    chunks = _merge_with_overlap(pieces, chunk_size, chunk_overlap)
    return chunks


def _split_with_offsets(text: str, separator: str) -> list[Chunk]:
    """Split text on separator, returning Chunks with char offsets in original."""
    if not text:
        return []
    pieces: list[Chunk] = []
    cursor = 0
    parts = text.split(separator)
    for part in parts:
        if part:  # skip empty
            start = cursor
            end = cursor + len(part)
            pieces.append(Chunk(content=part, start_char=start, end_char=end))
        cursor += len(part) + len(separator)
    return pieces


def _split_recursively(piece: Chunk, chunk_size: int) -> list[Chunk]:
    """Piece is too large for one chunk. Try sentence split, then word split."""
    # Try sentence split
    sentence_pieces = _split_pattern_with_offsets(piece, SENTENCE_PATTERN)
    if (
        all(p.length <= chunk_size for p in sentence_pieces)
        and len(sentence_pieces) > 1
    ):
        return sentence_pieces

    # Try word split for any pieces still too large
    result: list[Chunk] = []
    for sp in sentence_pieces:
        if sp.length <= chunk_size:
            result.append(sp)
        else:
            result.extend(_split_pattern_with_offsets(sp, WORD_PATTERN))
    return result


def _split_pattern_with_offsets(piece: Chunk, pattern: re.Pattern) -> list[Chunk]:
    """Split a Chunk using a regex pattern, preserving offsets."""
    text = piece.content
    parts: list[Chunk] = []
    last_end = 0
    for match in pattern.finditer(text):
        if match.start() > last_end:
            parts.append(
                Chunk(
                    content=text[last_end : match.start()],
                    start_char=piece.start_char + last_end,
                    end_char=piece.start_char + match.start(),
                )
            )
        last_end = match.end()
    if last_end < len(text):
        parts.append(
            Chunk(
                content=text[last_end:],
                start_char=piece.start_char + last_end,
                end_char=piece.start_char + len(text),
            )
        )
    return parts if parts else [piece]


def _merge_with_overlap(
    pieces: list[Chunk],
    chunk_size: int,
    chunk_overlap: int,
) -> list[Chunk]:
    """Greedily merge small pieces into chunks of target size, with overlap between chunks."""
    if not pieces:
        return []

    chunks: list[Chunk] = []
    buffer: list[Chunk] = []
    buffer_length = 0

    for piece in pieces:
        if buffer_length + piece.length + 1 <= chunk_size:
            # Fits in current buffer
            buffer.append(piece)
            buffer_length += piece.length + 1  # +1 for joiner
        else:
            # Flush buffer as a chunk
            if buffer:
                chunks.append(_buffer_to_chunk(buffer))
                # Build overlap for next buffer: take trailing pieces totaling <= chunk_overlap
                overlap_pieces = _build_overlap(buffer, chunk_overlap)
                buffer = list(overlap_pieces)
                buffer_length = sum(p.length + 1 for p in buffer)
            buffer.append(piece)
            buffer_length += piece.length + 1

    if buffer:
        chunks.append(_buffer_to_chunk(buffer))
    return chunks


def _buffer_to_chunk(buffer: list[Chunk]) -> Chunk:
    """Concat the buffered pieces into a single Chunk."""
    content = " ".join(p.content for p in buffer)
    return Chunk(
        content=content,
        start_char=buffer[0].start_char,
        end_char=buffer[-1].end_char,
    )


def _build_overlap(buffer: list[Chunk], target_overlap: int) -> list[Chunk]:
    """Return the trailing pieces of buffer whose combined length <= target_overlap."""
    overlap: list[Chunk] = []
    length = 0
    for piece in reversed(buffer):
        if length + piece.length + 1 > target_overlap:
            break
        overlap.insert(0, piece)
        length += piece.length + 1
    return overlap
