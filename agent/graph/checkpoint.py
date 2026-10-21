# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 근무조·작업 스레드를 저장하는 체크포인터. 운영은 PostgreSQL, 개발·테스트는 메모리.

"""체크포인터.

근무조 스레드(`shiftId`)와 Harness 작업 스레드(`{shiftId}:{taskId}`)를 같은 저장소에 둔다. 스레드 이름이 달라 섞이지 않는다.
프로세스가 죽었다 살아도 같은 thread_id로 이어서 실행된다 (tests/test_graph_postgres.py가 프로세스를 나눠 확인한다).
체크포인트는 감사 로그가 아니다 (PRD 6.8-2). 업무 기록의 원본은 backend가 ai.events를 받아 저장한다.

DB 인코딩은 UTF8이어야 한다. SQL_ASCII(예: `LC_ALL=C`로 만든 initdb)면 psycopg가 문자열 열을 bytes로 돌려주고,
LangGraph가 그 값을 thread_id로 다시 써서 재개한 실행이 엉뚱한 스레드(`\\x6d69…`)에 저장된다. 오류 없이 승인 대기가
풀리지 않으므로 열 때 막는다.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver


@asynccontextmanager
async def open_checkpointer(conn_string: str | None) -> AsyncIterator[BaseCheckpointSaver]:
    """주소가 있으면 PostgreSQL, 없으면 메모리. 처음 쓸 때 필요한 테이블을 만든다 (여러 번 불러도 안전).

    주소 예: postgresql://agent:secret@db:5432/agent, 로컬 Homebrew: postgresql://minkyu@127.0.0.1:5432/ai_capstone_agent
    """
    if not conn_string:
        yield InMemorySaver()
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # 운영에서만 필요한 의존성

    async with AsyncPostgresSaver.from_conn_string(conn_string) as saver:
        cur = await saver.conn.execute("SHOW server_encoding")
        row = await cur.fetchone()  # 저장소 연결은 dict_row다
        encoding = next(iter(row.values())) if isinstance(row, dict) else row[0]
        encoding = encoding.decode() if isinstance(encoding, bytes) else encoding
        if encoding.upper() != "UTF8":
            raise RuntimeError(f"체크포인터 DB 인코딩이 {encoding}이다. UTF8 DB가 필요하다 (SQL_ASCII면 재개가 저장되지 않는다)")
        await saver.setup()
        yield saver
