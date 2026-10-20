# Author: 정유은
# Date: 20OCT2026
# Purpose: 문서·질문을 벡터로 바꾸는 Embedder. 색인과 검색이 같은 모델을 쓰도록 모델 이름과 차원을 함께 들고 다닌다.

"""Embedding (ROLES 3 MCP·RAG: "Embedding(모델 이름·차원 기록)").

기본 모델은 `intfloat/multilingual-e5-large`(1024차원)를 fastembed(ONNX)로 돌린다.
- 한국어를 포함한 다국어 검색용으로 학습된 모델이다. 질문에는 "query: ", 문서에는 "passage: "를 붙인다.
- 근거 문서 10문항 점검(rag-mcp/eval/README.md)에서 다국어 MiniLM(384차원)·mpnet(768차원)보다 벡터 검색 적중이 높았다.
- ONNX 런타임이라 PyTorch 없이 CPU에서 돈다. 측정값: 메모리 약 1.6GB, 질문 하나 임베딩 약 36ms (Apple M 계열 CPU).
  서버 Colima 메모리 8GB(infra/README.md) 안에서 쓰고, 부족하면 EMBED_MODEL로 작은 모델을 고른다 (색인도 다시 만든다).
- 모델 파일은 컨테이너 빌드 때 받아 이미지에 넣는다 (rag-mcp/Dockerfile). 실행 중에는 외부 호출이 없다.

`hash` 공급자는 네트워크 없이 같은 입력에 같은 벡터를 주는 글자 2-gram 해시 임베딩이다. 테스트와 CI 전용이다.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Protocol

from .store import tokenize

DEFAULT_PROVIDER = "fastembed"
DEFAULT_MODEL = "intfloat/multilingual-e5-large"


class Embedder(Protocol):
    model: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    def __init__(self, model: str = DEFAULT_MODEL, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding  # 무거운 의존성은 쓸 때만 읽는다

        self.model = model
        self._impl = TextEmbedding(model_name=model, cache_dir=cache_dir)
        # E5 계열은 학습 때처럼 "query: "·"passage: " 접두어를 붙여야 성능이 나온다 (모델 카드 안내).
        self._q, self._p = ("query: ", "passage: ") if "e5" in model.lower() else ("", "")
        self.dim = len(next(iter(self._impl.embed(["차원 확인"]))))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._impl.embed([self._p + t for t in texts])]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._impl.embed([self._q + text]))).tolist()


class HashEmbedder:
    """글자 2-gram을 해시로 dim칸에 흩뿌린 뒤 길이를 1로 맞춘다. 의미는 모르고 겹치는 낱말만 본다."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.model = f"hash-bigram-{dim}"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in tokenize(text):
            h = int.from_bytes(hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def make_embedder(provider: str | None = None, model: str | None = None) -> Embedder:
    """환경 변수 EMBED_PROVIDER(fastembed·hash), EMBED_MODEL, FASTEMBED_CACHE_PATH를 읽는다."""
    provider = provider or os.environ.get("EMBED_PROVIDER", DEFAULT_PROVIDER)
    if provider == "fastembed":
        return FastEmbedEmbedder(model or os.environ.get("EMBED_MODEL", DEFAULT_MODEL),
                                 cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
    if provider == "hash":
        return HashEmbedder()
    raise SystemExit(f"EMBED_PROVIDER는 fastembed 또는 hash여야 합니다: {provider}")
