# 서버(Apple Silicon)와 로컬에서 같은 이미지로 빌드한다. python:3.12-slim은 arm64 이미지를 제공한다.
FROM python:3.12-slim
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# 의존성 층을 먼저 받아 두면 소스만 바뀐 재빌드가 빨라진다.
RUN pip install "fastmcp>=4.1,<5" "httpx>=0.27"
COPY pyproject.toml ./
COPY src src
RUN pip install --no-deps .
RUN useradd --system --uid 10001 app
USER app
ENV MCP_HOST=0.0.0.0 MCP_PORT=9101
EXPOSE 9101
CMD ["business-mcp"]
