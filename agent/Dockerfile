# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: agent 서버 이미지 (근무조 그래프, python -m graph). infra/compose.yaml의 agent 서비스가 빌드한다.

# 서버(Apple Silicon)에서 직접 빌드한다. python slim은 arm64 이미지를 제공하고, 의존성은 모두 휠이라 컴파일러가 필요 없다.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# 의존성 층을 먼저 받아 두면 소스만 바뀐 재빌드가 빨라진다. 목록은 pyproject.toml에서만 읽는다 (개발용 의존성 없음).
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# harness/config.py는 harness.yaml·prompts/를 패키지 옆(agent/)에서 찾는다. site-packages로 복사하면 못 찾으므로
# 소스를 /app에 그대로 두고 편집 모드로 설치한다. evaluation은 MCP_FAKE=1(가짜 MCP)에 필요하다.
COPY harness.yaml ./
COPY prompts prompts
COPY harness harness
COPY evaluation evaluation
COPY graph graph
RUN pip install --no-deps -e .

RUN useradd --system --uid 10001 agent
USER agent
ENTRYPOINT ["python", "-m", "graph"]
