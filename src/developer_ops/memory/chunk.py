"""Heading-aware, bounded word windows with exact source substrings."""

import hashlib
import re

from developer_ops.memory.schema import Chunk, Document


def chunk_document(document: Document, size: int = 256, overlap: int = 26) -> list[Chunk]:
    if size < 16 or not 0 <= overlap < size:
        raise ValueError("Require size >= 16 and 0 <= overlap < size")
    sections: list[tuple[str, int, int]] = []
    heading, start, offset = document.title, 0, 0
    fence = ""
    for line in document.text.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if not fence:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = ""
        if not fence and re.match(r"^#{1,6}\s+", line):
            if offset > start:
                sections.append((heading, start, offset))
            heading, start = line.strip(), offset
        offset += len(line)
    sections.append((heading, start, len(document.text)))
    chunks = []
    version = f"words-v1:{size}:{overlap}"
    for heading, start, end in sections:
        words = list(re.finditer(r"\S+", document.text[start:end]))
        for index in range(0, len(words), size - overlap):
            window = words[index : index + size]
            begin = start + window[0].start()
            finish = start + window[-1].end()
            identity = f"{document.id}:{document.content_hash}:{version}:{begin}:{finish}"
            chunks.append(
                Chunk(
                    id=hashlib.sha256(identity.encode()).hexdigest(),
                    document_id=document.id,
                    repository=document.repository,
                    title=document.title,
                    url=document.url,
                    source_type=document.source_type,
                    updated_at=document.updated_at,
                    position=len(chunks),
                    heading=heading,
                    text=document.text[begin:finish],
                    word_count=len(window),
                    start_line=document.text.count("\n", 0, begin) + 1,
                    chunking_version=version,
                )
            )
            if index + size >= len(words):
                break
    return chunks
