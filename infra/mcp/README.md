# MCP 독립 개발 환경

역할 3의 business-mcp·rag-mcp를 개인 브랜치에서 컨테이너로 검증하는 역할 5의 구성이다.
업무 서버는 **fake 모드**이며 실제 backend·optimizer·Kafka·DB에는 연결하지 않는다.
서버 소스·테스트는 [출처](SOURCES.md)의 커밋에서 가져왔고 업무 규칙은 수정하지 않았다.

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
서버는 UID 10001로 실행하며, RAG 근거 문서는 docs/rag/를 읽기 전용으로 마운트한다.
문서를 바꾸면 rag-mcp를 재시작해 메모리 색인을 다시 읽는다.
헬스체크는 포트 준비 확인이며 실제 MCP 프로토콜은 아래 테스트에서 확인한다.

## Python 클라이언트·테스트

최적화 환경과 분리된 Python 3.12 환경을 저장소 루트에 준비한다.
uv를 쓰면 첫 두 명령 대신 uv venv --python 3.12 .venv-mcp,
uv pip install --python .venv-mcp/bin/python에 같은 requirements와 패키지를 전달한다.

~~~bash
python3.12 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -r infra/mcp/requirements-dev.txt -e business-mcp -e rag-mcp
.venv-mcp/bin/python -m pytest -q -o asyncio_mode=auto business-mcp/tests rag-mcp/tests

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
CI는 독립 컨테이너를 기동해 해당 테스트를 실행하고 항상 종료한다.

종료는 docker compose -f infra/mcp/compose.dev.yaml down이다.
현재 구성은 업무 backend로 전환하는 설정을 제공하지 않는다. 운영 전환 시에는
backend의 내부 API·서비스 인증·요청 상관관계·실제 solve 연결을 담당자와 함께 통합한다.

## 버전과 경계

런타임 의존성은 requirements.lock으로 고정한다. 두 서버는 같은 고정 목록을 사용하고
각 프로젝트의 패키지는 --no-deps로 설치한다. 갱신은 임시 환경에서 아래처럼 수행한 뒤
기존 테스트·컨테이너 HTTP 테스트를 다시 통과시킨다.

~~~bash
uv pip compile --python-version 3.12 --universal business-mcp/pyproject.toml rag-mcp/pyproject.toml --output-file infra/mcp/requirements.lock --no-annotate
~~~

- 가짜 계산은 OR-Tools·Kafka 결과가 아니다. 실제 경로 계산 검증은 [Kafka 개발 환경](../optimizer/KAFKA.md)과 별개다.
- get_audit는 소스에 있으나 현재 main 내부 API 계약에는 없다. 역할 3·4가 확정하기 전 운영 기능으로 취급하지 않는다.
- 최신 MCP는 대문자 권역 ID·camelCase 결과·Harness 승인 인자를 수용한다. 데이터의 숫자 대여소 ID와 ST-형식 ID의 최종 선택은 아직 별도 합의다.
- 임베딩·pgvector·실제 LLM·에이전트 그래프·승인 카드 UI·전체 서비스 배포는 이번 검증에 포함하지 않는다.

공식 클라이언트 API는 [FastMCP 문서](https://gofastmcp.com/clients/client)를 참고한다.
