"""
Text embeddings for unstructured evidence retrieval.

Two backends:
  - "mistral": calls Mistral's embeddings API (model "mistral-embed").
    Used only if MISTRAL_API_KEY is configured. Requires network access
    to api.mistral.ai, which this project's sandboxed dev/test
    environment does not have allowlisted — so this path is exercised
    only when actually deployed with that egress available.
  - "local": a small dependency-free hashing bag-of-words vectorizer.
    Deterministic, offline, no API key required. This is the default,
    and what tests run against, so the retrieval layer works out of the
    box for a hackathon demo without any external service.

Both backends produce a fixed-length float vector; cosine similarity in
vector_store.py works identically regardless of which produced it. This
keeps the "if Mistral introduces unnecessary complexity, fall back to
plain Python" guidance easy to satisfy — the rest of the system never
needs to know which backend built a given vector.
"""

from __future__ import annotations

import hashlib
import math
import re

from app.core.config import settings

LOCAL_DIM = 2048
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _local_embedding(text: str, dim: int = LOCAL_DIM, idf: dict | None = None) -> list[float]:
    """Deterministic hashing bag-of-words vector (a minimal 'feature
    hashing' vectorizer), optionally IDF-weighted. Not semantic, but
    captures lexical overlap — good enough for a small, topically
    distinct synthetic corpus, and requires no model, no network, no
    dependency beyond hashlib.

    `idf`, if given, maps token -> inverse-document-frequency weight
    (computed by the caller over its corpus). Without it, every token
    counts equally, which lets very common words (e.g. "payment",
    "failure") dominate the vector and dilute more distinctive terms
    (e.g. a bank name or city). Vector stores that know their corpus
    should compute and pass an idf map for meaningfully better ranking."""
    vec = [0.0] * dim
    tokens = _tokenize(text)
    if not tokens:
        return vec
    default_weight = max(idf.values()) if idf else 1.0
    for tok in tokens:
        weight = idf.get(tok, default_weight) if idf else 1.0
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h // dim) % 2 == 0 else -1.0
        vec[idx] += sign * weight
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _mistral_embedding(text: str) -> list[float] | None:
    """Attempt a real Mistral embedding call. Returns None (triggering the
    local fallback) on any failure — missing key, no network, bad
    response — so callers never need their own try/except."""
    if not settings.mistral_api_key:
        return None
    try:
        import httpx

        resp = httpx.post(
            "https://api.mistral.ai/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.mistral_api_key}"},
            json={"model": "mistral-embed", "input": [text]},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]
    except Exception:
        return None


def embed_text(
    text: str, backend: str | None = None, idf: dict | None = None
) -> tuple[list[float], str]:
    """Embed a single string. Returns (vector, backend_used).

    backend: "mistral", "local", or None (auto — try Mistral only if an
    API key is configured, otherwise go straight to local).
    idf: optional token -> weight map (see _local_embedding) used only by
    the local backend, ignored for Mistral.
    """
    if backend in (None, "mistral") and settings.mistral_api_key:
        vec = _mistral_embedding(text)
        if vec is not None:
            return vec, "mistral"
        if backend == "mistral":
            raise RuntimeError("Mistral embedding requested but the API call failed/unavailable")
    return _local_embedding(text, idf=idf), "local"


def compute_idf(texts: list[str]) -> dict[str, float]:
    """Standard smoothed IDF over a small corpus: idf(t) = ln((N+1)/(df(t)+1)) + 1.
    Used by the local backend so common words (e.g. "payment", "failure")
    don't drown out distinctive ones (e.g. a bank name, a city) when
    ranking a small, topically clustered document set."""
    n_docs = len(texts)
    doc_freq: dict[str, int] = {}
    for text in texts:
        for tok in set(_tokenize(text)):
            doc_freq[tok] = doc_freq.get(tok, 0) + 1
    return {tok: math.log((n_docs + 1) / (df + 1)) + 1.0 for tok, df in doc_freq.items()}


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
