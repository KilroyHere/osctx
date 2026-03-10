"""
e5-small-v2 embedding wrapper.

- Lazy loading: model is not imported until first encode call
- e5 models require prefixes: "query: " for search queries, "passage: " for documents
- encode_batch handles lists efficiently via sentence-transformers batch inference
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model: "SentenceTransformer | None" = None
MODEL_NAME = "intfloat/e5-small-v2"
EMBEDDING_DIM = 384


def _get_model() -> "SentenceTransformer":
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def encode_query(text: str) -> list[float]:
    """Encode a search query. Adds required 'query: ' prefix."""
    model = _get_model()
    prefixed = f"query: {text}"
    result = model.encode(prefixed, normalize_embeddings=True)
    return result.tolist()


def encode_passage(text: str) -> list[float]:
    """Encode a document/passage for storage. Adds required 'passage: ' prefix."""
    model = _get_model()
    prefixed = f"passage: {text}"
    result = model.encode(prefixed, normalize_embeddings=True)
    return result.tolist()


def encode_batch(texts: list[str], *, is_query: bool = False) -> list[list[float]]:
    """Encode a batch of texts efficiently.

    Args:
        texts: List of strings to encode.
        is_query: If True, applies 'query: ' prefix; else 'passage: ' prefix.
    """
    if not texts:
        return []
    model = _get_model()
    prefix = "query: " if is_query else "passage: "
    prefixed = [f"{prefix}{t}" for t in texts]
    results = model.encode(prefixed, normalize_embeddings=True, batch_size=32)
    return [r.tolist() for r in results]
