# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 운영 연결. MCP 서버·모델·체크포인터·Kafka를 묶어 agent 서버를 띄운다 (python -m graph).

"""agent 서버 조립.

환경 변수
- `MCP_BUSINESS_URL` (기본 http://business-mcp:9101/mcp), `MCP_RAG_URL` (기본 http://rag-mcp:9102/mcp)
- `MCP_FAKE=1`: 실제 MCP 서버 대신 평가용 가짜 MCP(`evaluation/fake_mcp.py`, 메모리 전송). 3번 서버가 오기 전 통합 확인용.
  `MCP_FAKE_GRADE=MAJOR|MINOR|NONE`이면 가짜 MCP의 변경 등급을 고정한다 (큰 변경 두 장 확인용)
- `AGENT_LLM`: 비우면 harness.yaml 모델, `none`이면 LLM 없는 경로만, `ollama:<모델>`이면 Ollama (`OLLAMA_BASE_URL`),
  `openai:<모델>`이면 OpenAI (`OPENAI_API_KEY`). 지정하면 harness.yaml의 대체 모델은 끈다
- `AGENT_READY_FILE`: 브로커에 붙은 뒤 10초마다 갱신하는 준비 파일 (기본 /tmp/agent-ready, compose healthcheck가 본다)
- `AGENT_PG_URL`: PostgreSQL (체크포인터, 처리 기록). 없으면 메모리
- `KAFKA_BOOTSTRAP`: 브로커 주소 (기본 localhost:9092)
- `AGENT_DRIVER_NAMES`: 자유 글에서 가릴 기사 이름 (쉼표로 구분). 계약에 이름 목록이 없어 설정으로 받는다
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path

from langchain.chat_models import init_chat_model
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver

from harness import EventSink, HarnessAgent, HarnessConfig, load_config
from harness.tools import mcp_client

from .checkpoint import open_checkpointer
from .shift import ShiftAgent
from .worker import MemoryProcessed, PostgresProcessed, run_kafka

log = logging.getLogger(__name__)


async def load_tools(servers: dict[str, str], trace_header: str) -> list[BaseTool]:
    """MCP 도구 (Harness `connect`와 같은 클라이언트). 승인 ID·작업 ID는 Harness가 인자로 채운다 (injected_args)."""
    return await mcp_client(servers, trace_header).get_tools()


def record_approvals_in(world, shift: ShiftAgent) -> None:
    """MCP_FAKE 전용. 가짜 MCP는 backend `/internal`도 흉내 내므로, 실제 backend가 남긴 승인 기록을 모른다.
    RESUME 승인이 오면 그 카드를 가짜 MCP에도 기록해 변경 도구가 승인 확인(403)을 통과하게 한다."""
    handle = shift.handle

    async def recording(command: dict) -> dict:
        resume = command.get("resume") or {}
        if command.get("type") == "RESUME" and resume.get("decision") == "APPROVE" and not resume.get("excludeStationIds"):
            pending = ((await shift.state(command["shiftId"])).get("approvals") or {}).get(resume.get("approvalId"))
            if pending and pending["status"] == "PENDING" and pending["card"]["approver"] == resume.get("decidedBy", {}).get("role"):
                world.approve(pending["card"])
        return await handle(command)

    shift.handle = recording


def harness_factory(
    config: HarnessConfig,
    tools: list[BaseTool],
    checkpointer: BaseCheckpointSaver,
    llm: str | None = None,
) -> Callable[[EventSink], HarnessAgent] | None:
    """`AGENT_LLM` 값에 따라 Harness를 만드는 함수. `none`이면 None (모든 작업을 LLM 없는 경로로).

    Ollama 같은 로컬 모델은 대체 모델이 따로 없으므로 harness.yaml의 대체 모델을 끈다 (API 키가 필요 없게).
    """
    llm = (llm or "").strip()
    if llm.lower() == "none":
        return None
    if not llm:
        return lambda sink: HarnessAgent(config, tools, sink, checkpointer=checkpointer)
    kwargs = dict(config.models.kwargs)
    if llm.startswith("ollama:") and os.environ.get("OLLAMA_BASE_URL"):
        kwargs["base_url"] = os.environ["OLLAMA_BASE_URL"]
    model = init_chat_model(llm, **kwargs)
    local = replace(config, models=replace(config.models, primary=llm, fallback=None))
    return lambda sink: HarnessAgent(local, tools, sink, model=model, checkpointer=checkpointer)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ready = os.environ.get("AGENT_READY_FILE", "/tmp/agent-ready")
    Path(ready).unlink(missing_ok=True)  # 지난 실행이 남긴 파일이면 다시 붙기 전에 healthcheck가 속는다
    config = load_config()
    servers = {
        "business": os.environ.get("MCP_BUSINESS_URL", "http://business-mcp:9101/mcp"),
        "rag": os.environ.get("MCP_RAG_URL", "http://rag-mcp:9102/mcp"),
    }
    pg = os.environ.get("AGENT_PG_URL")
    world = None
    async with AsyncExitStack() as stack:
        if os.environ.get("MCP_FAKE") == "1":
            from evaluation.fake_mcp import World, connect_world  # 개발용

            log.warning("가짜 MCP로 돈다 (MCP_FAKE=1). 도구 결과는 평가용 예시 데이터다")
            world = World(force_grade=os.environ.get("MCP_FAKE_GRADE") or None)  # 변경 등급 고정 (MAJOR 두 장 확인용)
            tools = await stack.enter_async_context(connect_world(world, config.observability.trace_header))
        else:
            tools = await load_tools(servers, config.observability.trace_header)
        checkpointer = await stack.enter_async_context(open_checkpointer(pg))
        processed = await stack.enter_async_context(PostgresProcessed.connect(pg)) if pg else MemoryProcessed()
        factory = harness_factory(config, tools, checkpointer, os.environ.get("AGENT_LLM"))
        log.info("도구 %d개, 모델 %s", len(tools), os.environ.get("AGENT_LLM") or config.models.primary)

        names = [n.strip() for n in os.environ.get("AGENT_DRIVER_NAMES", "").split(",") if n.strip()]

        def make_shift(publish: Callable[[dict], None]) -> ShiftAgent:
            shift = ShiftAgent(tools, publish, config=config, harness=factory, checkpointer=checkpointer,
                               driver_names=names)
            if world is not None:
                record_approvals_in(world, shift)
            return shift

        await run_kafka(make_shift, processed, os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"), ready_file=ready)


if __name__ == "__main__":
    asyncio.run(main())
