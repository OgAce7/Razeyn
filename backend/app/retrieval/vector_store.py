"""
Lightweight local vector store for the unstructured evidence corpus.

Loads the synthetic document corpus (corpus/unstructured_evidence.json),
embeds each document once, and caches the resulting vectors to local disk
(store/embeddings_cache.json) so repeated runs don't recompute them. This
is intentionally simple — a JSON file, brute-force cosine similarity over
~25 documents — appropriate for this corpus size and hackathon scope. No
external vector database is needed.

If MISTRAL_API_KEY is configured and reachable, embeddings are computed
via Mistral; otherwise (the default for this environment) a deterministic
local hashing vectorizer is used. Whichever backend built the corpus is
recorded in the cache and reused for query embedding, so corpus and query
vectors always stay comparable.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.retrieval.embeddings import compute_idf, cosine_similarity, embed_text

CORPUS_PATH = Path(__file__).parent / "corpus" / "unstructured_evidence.json"
CACHE_PATH = Path(__file__).parent / "store" / "embeddings_cache.json"


def load_corpus(path: Path | str = CORPUS_PATH) -> list[dict]:
    with open(path) as f:
        return json.load(f)["documents"]


class UnstructuredEvidenceStore:
    """In-memory index over the unstructured evidence corpus, backed by an
    on-disk embedding cache for speed across repeated runs."""

    def __init__(
        self,
        corpus_path: Path | str = CORPUS_PATH,
        cache_path: Path | str = CACHE_PATH,
        backend: str | None = None,
    ):
        self.corpus_path = Path(corpus_path)
        self.cache_path = Path(cache_path)
        self.documents = load_corpus(self.corpus_path)
        self._backend_override = backend
        self._vectors: dict[str, list[float]] = {}
        self._backend_used: str | None = None
        self._idf = compute_idf([f"{d['title']}. {d['text']}" for d in self.documents])
        self._load_or_build_cache()

    def _load_or_build_cache(self) -> None:
        cached = self._read_cache()
        doc_ids = {d["doc_id"] for d in self.documents}

        if (
            cached
            and cached.get("doc_ids") == sorted(doc_ids)
            and (self._backend_override is None or cached.get("backend") == self._backend_override)
        ):
            self._vectors = cached["vectors"]
            self._backend_used = cached["backend"]
            return

        self._build_and_cache()

    def _read_cache(self) -> dict | None:
        if not self.cache_path.exists():
            return None
        try:
            with open(self.cache_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _build_and_cache(self) -> None:
        vectors: dict[str, list[float]] = {}
        backend_used = None
        for doc in self.documents:
            text_for_embedding = f"{doc['title']}. {doc['text']}"
            vec, backend = embed_text(
                text_for_embedding, backend=self._backend_override, idf=self._idf
            )
            vectors[doc["doc_id"]] = vec
            backend_used = backend  # all docs embedded with the same backend per build

        self._vectors = vectors
        self._backend_used = backend_used

        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "w") as f:
            json.dump(
                {
                    "backend": backend_used,
                    "doc_ids": sorted(vectors.keys()),
                    "vectors": vectors,
                },
                f,
            )

    def search(self, query: str, top_k: int = 5, filter_fn=None) -> list[tuple[dict, float]]:
        """Return up to `top_k` (document, relevance_score) pairs, sorted by
        descending cosine similarity. `filter_fn(doc) -> bool` can restrict
        the candidate set before ranking (e.g. by type or date range)."""
        query_vec, _ = embed_text(query, backend=self._backend_used, idf=self._idf)

        scored = []
        for doc in self.documents:
            if filter_fn is not None and not filter_fn(doc):
                continue
            vec = self._vectors.get(doc["doc_id"])
            if vec is None:
                continue
            score = cosine_similarity(query_vec, vec)
            scored.append((doc, score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    @property
    def backend_used(self) -> str | None:
        return self._backend_used
