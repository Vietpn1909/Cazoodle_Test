"""Embedding model wrapper (local ONNX via fastembed, no API key).

``Embedder`` is a small protocol so the pipeline and tests do not depend on the real model:
tests use a deterministic fake, production uses ``FastEmbedder``.

Note: fastembed serves ``BAAI/bge-small-en-v1.5`` as the int8-quantized ONNX build
(``Qdrant/bge-small-en-v1.5-onnx-Q``): ~3x smaller and CPU-friendly, vectors are 384-d and
L2-normalised, so cosine similarity == dot product (pgvector ``<=>`` / ``vector_cosine_ops``).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from app.core.config import REPO_ROOT

MODEL_CACHE_DIR = REPO_ROOT / "data" / "models"  # gitignored; survives OS temp cleanup


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> NDArray[np.float32]:
        """Return an ``(n, dim)`` float32 array of L2-normalised vectors."""
        ...


class FastEmbedder:
    def __init__(
        self, name: str, dim: int, *, cache_dir: Path = MODEL_CACHE_DIR, batch_size: int = 32
    ) -> None:
        from fastembed import TextEmbedding  # heavy import: only when really embedding

        self.name = name
        self.dim = dim
        self._batch_size = batch_size
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._model = TextEmbedding(name, cache_dir=str(cache_dir))

    def embed(self, texts: Sequence[str]) -> NDArray[np.float32]:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vectors = np.vstack(list(self._model.embed(list(texts), batch_size=self._batch_size)))
        if vectors.shape[1] != self.dim:
            raise ValueError(f"model returned {vectors.shape[1]}-d vectors, expected {self.dim}")
        return vectors.astype(np.float32, copy=False)


def to_pgvector(vector: Sequence[float]) -> str:
    """Text literal accepted by pgvector (also by COPY): ``[0.1,0.2,...]``."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"
