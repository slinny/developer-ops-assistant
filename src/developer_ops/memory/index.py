"""Persisted LSA embeddings in Chroma; atomic publication of complete generations."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import chromadb
import numpy as np
from chromadb.config import Settings as ChromaSettings
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize as unit_vectors

from developer_ops.memory.chunk import chunk_document
from developer_ops.memory.schema import Chunk, Document

EMBEDDING_VERSION = "lsa-tfidf-v1"


class Embeddings:
    """Corpus-trained semantic projection, with JSON/NPZ persistence (no pickle)."""

    def __init__(self) -> None:
        self.vectorizer = TfidfVectorizer(sublinear_tf=True, ngram_range=(1, 2))
        self.components: Any = None

    def fit(self, texts: list[str]) -> Any:
        matrix = self.vectorizer.fit_transform(texts)
        dimensions = min(128, matrix.shape[0], matrix.shape[1])
        if min(matrix.shape) < 2:
            self.components = unit_vectors(np.asarray(matrix.sum(axis=0)))
        else:
            svd = TruncatedSVD(n_components=dimensions, random_state=42)
            svd.fit(matrix)
            self.components = svd.components_
        return self.encode(texts)

    def encode(self, texts: list[str]) -> Any:
        matrix = self.vectorizer.transform(texts)
        return unit_vectors(np.asarray(matrix @ self.components.T)).astype(np.float32)

    def save(self, path: Path) -> None:
        (path / "vocabulary.json").write_text(json.dumps(self.vectorizer.vocabulary_))
        np.savez(path / "embedding.npz", idf=self.vectorizer.idf_, components=self.components)

    @classmethod
    def load(cls, path: Path) -> "Embeddings":
        model = cls()
        model.vectorizer = TfidfVectorizer(
            sublinear_tf=True,
            ngram_range=(1, 2),
            vocabulary=json.loads((path / "vocabulary.json").read_text()),
        )
        with np.load(path / "embedding.npz", allow_pickle=False) as data:
            model.vectorizer.idf_ = data["idf"]
            model.components = data["components"]
        return model


def client(path: Path) -> Any:
    return chromadb.PersistentClient(
        path=str(path / "chroma"), settings=ChromaSettings(anonymized_telemetry=False)
    )


def build(documents: list[Document], path: Path, size: int = 256, overlap: int = 26) -> str:
    if len({d.id for d in documents}) != len(documents):
        raise ValueError("Normalize duplicate document IDs before indexing")
    chunks = [chunk for doc in documents for chunk in chunk_document(doc, size, overlap)]
    model = Embeddings()
    texts = [f"{c.title}\n{c.heading}\n{c.text}" for c in chunks]
    # Persist a valid model even for an explicitly empty snapshot; no placeholder
    # document or vector is indexed, and all retrieval paths return no results.
    vectors = model.fit(texts or ["__empty_index__"])[: len(chunks)]
    generation = "memory-" + uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    folder = path / generation
    folder.mkdir()
    corpus = "\n".join(d.model_dump_json() for d in sorted(documents, key=lambda d: d.id))
    collection = client(path).create_collection(
        generation, metadata={"hnsw:space": "cosine"}, embedding_function=None
    )
    # Failed generations are never activated; preserve old CURRENT on any error.
    try:
        for start in range(0, len(chunks), 1000):
            batch = chunks[start : start + 1000]
            collection.add(
                ids=[c.id for c in batch],
                embeddings=vectors[start : start + 1000].tolist(),
                documents=[c.text for c in batch],
                metadatas=[
                    {"repository": c.repository, "document_id": c.document_id} for c in batch
                ],
            )
        model.save(folder)
        (folder / "chunks.json").write_text(json.dumps([c.model_dump() for c in chunks]))
        (folder / "corpus.jsonl").write_text(corpus + "\n")
        (folder / "manifest.json").write_text(
            json.dumps(
                {
                    "generation": generation,
                    "embedding": EMBEDDING_VERSION,
                    "built_at": datetime.now(UTC).isoformat(),
                    "size_words": size,
                    "overlap_words": overlap,
                    "documents": len(documents),
                    "chunks": len(chunks),
                    "dimensions": vectors.shape[1],
                    "corpus_sha256": hashlib.sha256(corpus.encode()).hexdigest(),
                },
                indent=2,
            )
        )
        temporary = path / f"CURRENT.{uuid4().hex}"
        temporary.write_text(generation)
        os.replace(temporary, path / "CURRENT")
    except BaseException:
        client(path).delete_collection(generation)
        raise
    return generation


class Index:
    def __init__(self, path: Path) -> None:
        generation = (path / "CURRENT").read_text().strip()
        if not generation.startswith("memory-") or not generation[7:].isalnum():
            raise ValueError("Invalid generation pointer")
        folder = path / generation
        self.manifest = json.loads((folder / "manifest.json").read_text())
        self.chunks = [
            Chunk.model_validate(c) for c in json.loads((folder / "chunks.json").read_text())
        ]
        self.by_id = {c.id: c for c in self.chunks}
        self.model = Embeddings.load(folder)
        self.collection = client(path).get_collection(generation, embedding_function=None)
