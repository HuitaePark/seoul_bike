FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /app
COPY infra/mcp/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock
ARG MCP_PACKAGE
COPY ${MCP_PACKAGE}/pyproject.toml /app/service/pyproject.toml
COPY ${MCP_PACKAGE}/src /app/service/src
RUN pip install --no-cache-dir --no-deps /app/service
USER 10001
