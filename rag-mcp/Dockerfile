# rag-mcp(검색 서버)와 rag-indexer(색인기)가 같은 이미지를 쓴다. 명령만 다르다 (infra/compose.yaml).
# 근거 문서는 compose의 additional_contexts `evidence`(docs/evidence)로 받는다.
FROM python:3.12-slim
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FASTEMBED_CACHE_PATH=/models
# 의존성 층을 먼저 받아 두면 소스만 바뀐 재빌드가 빨라진다.
RUN pip install "fastmcp>=4.1,<5" "pyyaml>=6" "psycopg[binary]>=3.2" "pgvector>=0.3" "numpy>=1.26" "fastembed>=0.7"
# 임베딩 모델을 빌드 때 받아 이미지에 넣는다. 실행 중에는 외부로 나가지 않는다.
# 바꾸면 rag-indexer가 색인을 새 모델로 다시 만든다 (모델 이름·차원이 rag_index와 다르면 rag-mcp가 뜨지 않는다).
ARG EMBED_MODEL=intfloat/multilingual-e5-large
ENV EMBED_MODEL=${EMBED_MODEL}
RUN python -c "import os; from fastembed import TextEmbedding; TextEmbedding(os.environ['EMBED_MODEL'], cache_dir=os.environ['FASTEMBED_CACHE_PATH'])"
COPY pyproject.toml ./
COPY src src
RUN pip install --no-deps .
COPY --from=evidence . /app/docs/evidence
RUN useradd --system --uid 10001 app && chmod -R a+rX /models /app
USER app
ENV RAG_DOCS_DIR=/app/docs/evidence MCP_HOST=0.0.0.0 MCP_PORT=9102
EXPOSE 9102
CMD ["rag-mcp"]
