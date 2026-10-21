FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt /app/requirements.txt
COPY or-tools/requirements-worker.txt /app/requirements-worker.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -r /app/requirements-worker.txt
COPY or-tools/rebalancing /app/rebalancing
CMD ["python", "-m", "rebalancing", "--json"]
