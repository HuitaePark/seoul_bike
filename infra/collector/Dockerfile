# 실시간 재고 collector (합의 12). 6번의 수집 코드(analysis/collect/bikelist.py)를 그대로 쓰고 DB에 넣는다.
# 빌드 context는 저장소 루트다 (compose `context: ..`). 루트 .dockerignore가 수집 데이터(data/)를 뺀다.
FROM python:3.12-slim
WORKDIR /app
COPY infra/collector/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && useradd --system --uid 10001 app
# bikelist.py는 자기 위치의 두 단계 위(저장소 루트)를 기준으로 삼으므로 같은 경로 구조로 둔다.
COPY analysis/collect/bikelist.py analysis/collect/live_db.py analysis/collect/
USER app
ENV PYTHONUNBUFFERED=1
CMD ["python", "analysis/collect/live_db.py"]
