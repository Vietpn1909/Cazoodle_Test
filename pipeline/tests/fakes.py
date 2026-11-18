"""Test doubles."""

import hashlib
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray


class FakeEmbedder:
    """Deterministic, model-free embedder: same text -> same unit vector; counts calls."""

    def __init__(self, name: str = "fake-model", dim: int = 384) -> None:
        self.name = name
        self.dim = dim
        self.embedded: list[str] = []

    def embed(self, texts: Sequence[str]) -> NDArray[np.float32]:
        self.embedded.extend(texts)
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            seed = int.from_bytes(hashlib.sha256(t.encode()).digest()[:8], "little")
            v = np.random.default_rng(seed).standard_normal(self.dim).astype(np.float32)
            out[i] = v / np.linalg.norm(v)
        return out
