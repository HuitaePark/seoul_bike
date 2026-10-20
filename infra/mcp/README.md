# MCP 독립 개발 환경

역할 3의 business-mcp·rag-mcp를 개인 브랜치에서 컨테이너로 검증하는 역할 5의 구성이다.
업무 서버는 **fake 모드**이며 실제 backend·optimizer·Kafka·업무 DB에는 연결하지 않는다.
기본 RAG는 메모리 검색이며, 별도 overlay로 일회용 pgvector DB도 검증할 수 있다.
서버 소스·테스트는 최신 main을 따르며 [출처](SOURCES.md)에 반영 기준을 기록했다.

## 실행

Docker 엔진이 실행된 상태에서 저장소 루트에서 실행한다.

~~~bash
docker compose -f infra/mcp/compose.dev.yaml up -d --build --wait
docker compose -f infra/mcp/compose.dev.yaml ps
~~~

호스트 주소는 business-mcp http://127.0.0.1:19101/mcp,
rag-mcp http://127.0.0.1:19102/mcp다.
같은 개발 Compose 네트워크 안에서는 각각 http://business-mcp:9101/mcp,
http://rag-mcp:9102/mcp다. 실제 에이전트 컨테이너는 이 구성에 아직 없다.

포트가 겹치면 BUSINESS_MCP_PORT=19111 RAG_MCP_PORT=19112를 Compose 명령에 지정하고
클라이언트 URL도 바꾼다. 모든 호스트 포트는 127.0.0.1에만 열린다.
서버는 UID 10001로 실행하며, RAG 근거 문서는 main의 docs/evidence/를 읽기 전용으로 마운트한다.
RAG_STORE=memory를 명시해 DB·임베딩 모델 없이 BM25 검색을 검증한다.
문서를 바꾸면 rag-mcp를 재시작해 메모리 색인을 다시 읽는다.
헬스체크는 포트 준비 확인이며 실제 MCP 프로토콜은 아래 테스트에서 확인한다.

## Python 클라이언트·테스트

최적화 환경과 분리된 Python 3.12 환경을 저장소 루트에 준비한다.
uv를 쓰면 첫 두 명령 대신 uv venv --python 3.12 .venv-mcp,
uv pip install --python .venv-mcp/bin/python에 같은 requirements와 패키지를 전달한다.

~~~bash
python3.12 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -r infra/mcp/requirements-dev.txt -e business-mcp -e rag-mcp
.venv-mcp/bin/python -m pytest -q -c rag-mcp/pyproject.toml --strict-markers business-mcp/tests rag-mcp/tests

MCP_SMOKE_BUSINESS_URL=http://127.0.0.1:19101/mcp \
MCP_SMOKE_RAG_URL=http://127.0.0.1:19102/mcp \
.venv-mcp/bin/python -m pytest -q infra/mcp/tests
~~~

[클라이언트 예시](client.host.example.json)를 FastMCP Client에 전달하면
도구가 business_·rag_ 접두어로 구분된다. 클라이언트 사용 예:

~~~python
import asyncio
import json
from pathlib import Path
from fastmcp import Client

async def main():
    config = json.loads(Path("infra/mcp/client.host.example.json").read_text())
    async with Client(config) as client:
        print([tool.name for tool in await client.list_tools()])

asyncio.run(main())
~~~

HTTP 검증은 도구 탐색, 가짜 계산 결과, 무승인 변경 차단, 문서 검색·원문 조회와
여러 서버를 묶은 접두어를 확인한다. 환경 변수가 없으면 HTTP 테스트는 생략한다.
CI는 메모리 검색과 아래 pgvector 검색을 각각 실제 HTTP로 검사하고 항상 정리한다.

종료는 docker compose -f infra/mcp/compose.dev.yaml down이다.
현재 구성은 업무 backend로 전환하는 설정을 제공하지 않는다. 운영 전환 시에는
backend의 내부 API·서비스 인증·요청 상관관계·실제 solve 연결을 담당자와 함께 통합한다.

## pgvector 색인·검색 검증

아래 overlay는 같은 개발 프로젝트의 rag-mcp를 pgvector 저장소로 전환한다.
테스트 전용 DB·사용자·비밀번호를 사용하고 DB 데이터는 tmpfs에만 저장한다.
DB 포트는 127.0.0.1:19432이며 MCP_TEST_PG_PORT로 바꿀 수 있다. 바꾸면 아래 DSN도 맞춘다.
운영 Compose·DB·볼륨과 연결하지 않으며 down으로 컨테이너를 제거하면 테스트 데이터도 없어진다.

~~~bash
docker compose -f infra/mcp/compose.dev.yaml -f infra/mcp/compose.pgvector.yaml up -d --wait vectordb

RAG_TEST_DSN=postgresql://mcp_test:mcp_test_only@127.0.0.1:19432/mcp_test \
.venv-mcp/bin/python -m pytest -q -c rag-mcp/pyproject.toml --strict-markers -m pgvector rag-mcp/tests

docker compose -f infra/mcp/compose.dev.yaml -f infra/mcp/compose.pgvector.yaml up -d --build --wait

MCP_SMOKE_BUSINESS_URL=http://127.0.0.1:19101/mcp \
MCP_SMOKE_RAG_URL=http://127.0.0.1:19102/mcp \
MCP_SMOKE_RAG_STORE=pgvector \
.venv-mcp/bin/python -m pytest -q infra/mcp/tests -k "rag_http or grouped_client"

docker compose -f infra/mcp/compose.dev.yaml -f infra/mcp/compose.pgvector.yaml down --volumes
~~~

DB 테스트 3개는 색인 생성·동일 입력 건너뛰기, 모델 차원 변경 시 재색인,
검색·필터·원문 Resource·모델 불일치 차단을 확인한다. 테스트마다 별도 스키마를 쓴다.
rag-indexer가 근거 문서를 먼저 색인하고 완료해야 pgvector rag-mcp가 시작한다.
HTTP 테스트는 응답의 source·embedding도 검사해 실제 선택한 저장소를 사용했는지 확인한다.

색인기·검색 서버는 모두 EMBED_PROVIDER=hash(256차원)를 사용한다.
모델 다운로드·API 키 없이 연결을 검증하며, 실제 한국어 의미 검색의 품질 평가는 별도다.
MCP_SMOKE_RAG_STORE=pgvector는 이 hash 테스트 환경을 대상으로 한다.
기본 메모리 모드로 돌아가려면 위 down 이후 기본 실행 명령으로 다시 시작한다.
기존 rag-mcp 단위 테스트에는 실제 모델을 받는 fastembed 검사가 있으며 이번 CI에서는 제외한다.

## 버전과 경계

런타임 의존성은 requirements.lock으로 고정한다. 두 서버는 같은 고정 목록을 사용하고
각 프로젝트의 패키지는 --no-deps로 설치한다. 갱신은 임시 환경에서 아래처럼 수행한 뒤
기존 테스트·컨테이너 HTTP 테스트를 다시 통과시킨다.

~~~bash
uv pip compile --python-version 3.12 --universal business-mcp/pyproject.toml rag-mcp/pyproject.toml --output-file infra/mcp/requirements.lock --no-annotate
~~~

- 가짜 계산은 OR-Tools·Kafka 결과가 아니다. 실제 경로 계산 검증은 [Kafka 개발 환경](../optimizer/KAFKA.md)과 별개다.
- main의 내부 API v0.5에는 서비스 토큰·현장 보고 조회·제약 추가·감사 조회가 구현됐다. 이 독립 환경은 계속 fake 모드이며 실제 API 연결 검증은 별도다.
- 최신 MCP는 대문자 권역 ID·camelCase 결과·Harness 승인 인자를 수용한다. 데이터의 숫자 대여소 ID와 ST-형식 ID의 최종 선택은 아직 별도 합의다.
- 실제 임베딩 모델의 검색 품질·LLM·에이전트 그래프·승인 카드 UI·전체 서비스 배포는 이번 검증에 포함하지 않는다.

공식 클라이언트 API는 [FastMCP 문서](https://gofastmcp.com/clients/client)를 참고한다.
