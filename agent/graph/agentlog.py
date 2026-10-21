# Author: Jung Minkyu
# Date: 22OCT2026
# Purpose: 추론 기록(관리자 로그) CLI. backend 관리자 API를 실시간으로 따라가거나, 저장한 JSONL을 터미널에 한 줄씩 보여 준다.

"""에이전트 추론 기록 CLI (docs/AGENT-GRAPH.md 10절).

    # 서버의 관리자 로그를 실시간으로 (관리자 계정, 비밀번호는 AGENT_LOG_PASSWORD 또는 입력)
    python -m graph.agentlog --url https://baki-macbookpro.tail3286a4.ts.net --login manager
    python -m graph.agentlog --url http://localhost:3000 --login manager --task <taskId> --history 500

    # 평가가 남긴 기록 (python -m evaluation --trace /tmp/trace.jsonl)
    python -m graph.agentlog --file /tmp/trace.jsonl --only SC-02 -v

화면(관제 화면의 '에이전트 로그')과 같은 API(`GET /api/admin/agent-traces`, `/stream`)를 읽는다. 관리자(MANAGER)만 열린다.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any, TextIO

KIND_LABEL = {
    "RUN_START": "시작", "ROUTE": "경로", "DECISION": "판단", "GUARD": "안전장치", "TOOL_CALL": "도구 호출",
    "TOOL_RESULT": "도구 결과", "RECOVERY": "복구", "APPROVAL_WAIT": "승인 대기", "RUN_END": "끝",
}
COLOR = {
    "RUN_START": "1;37", "ROUTE": "36", "DECISION": "1;33", "GUARD": "1;31", "TOOL_CALL": "34",
    "TOOL_RESULT": "32", "RECOVERY": "35", "APPROVAL_WAIT": "1;35", "RUN_END": "1;37",
}
SOURCE_LABEL = {"HARNESS": "모델", "CODE": "규칙", "GRAPH": "그래프"}
TASK_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _time(value: str | None) -> str:
    if not value:
        return "--:--:--"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return value[:8]


def _paint(text: str, code: str, color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if color else text


def render(event: dict[str, Any], *, verbose: bool = False, color: bool = False) -> list[str]:
    """추론 기록 한 건 → 터미널 줄. 판단(DECISION)은 고른 도구마다 이유를 한 줄씩 덧붙인다."""
    kind = event.get("kind", "")
    detail = event.get("detail") or {}
    turn = f"턴{event['turn']}" if event.get("turn") else "   "
    head = (f"{_time(event.get('occurredAt'))} {str(event.get('taskId', ''))[:8]} "
            f"{SOURCE_LABEL.get(event.get('source', ''), event.get('source', '')):<3} {turn:<4} ")
    lines = [head + _paint(f"{KIND_LABEL.get(kind, kind):<6}", COLOR.get(kind, "0"), color) + " " + event.get("title", "")]
    pad = " " * len(head)
    if kind == "DECISION":
        for c in detail.get("calls") or []:
            if c.get("rationale"):
                lines.append(f"{pad}  └ {c.get('name')}: {c['rationale']}")
        if detail.get("thought"):
            lines.append(f"{pad}  생각: {detail['thought']}")
    elif kind == "RUN_START" and verbose:
        lines.append(f"{pad}  고를 수 있는 도구: {', '.join(detail.get('tools') or [])}")
        for w in detail.get("withheld") or []:
            lines.append(f"{pad}  빠진 도구: {w.get('name')} ({w.get('why')})")
    elif kind == "GUARD":
        lines += [f"{pad}  - {r}" for r in detail.get("reasons") or []]
    if verbose and kind in {"TOOL_CALL", "TOOL_RESULT"}:
        body = detail.get("args") if kind == "TOOL_CALL" else detail.get("preview")
        if body:
            lines.append(f"{pad}  {body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)}")
    return lines


def wanted(event: dict[str, Any], task: str | None, shift: str | None, only: str | None) -> bool:
    return ((not task or str(event.get("taskId", "")).startswith(task))
            and (not shift or event.get("shiftId") == shift)
            and (not only or event.get("scenario") == only))


def read_jsonl(stream: TextIO) -> Iterator[dict[str, Any]]:
    for line in stream:
        line = line.strip()
        if line:
            yield json.loads(line)


def sse_events(lines: Iterable[str]) -> Iterator[tuple[str | None, dict[str, Any]]]:
    """text/event-stream 줄 → (id, data JSON). 주석(`: ping`)은 건너뛴다."""
    event_id, data = None, []
    for line in lines:
        if line == "":
            if data:
                yield event_id, json.loads("\n".join(data))
            event_id, data = None, []
        elif line.startswith("id:"):
            event_id = line[3:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def follow(args: argparse.Namespace, out: TextIO) -> int:
    import httpx  # langchain-core·mcp가 이미 쓰는 의존성

    base = args.url.rstrip("/")
    password = os.environ.get("AGENT_LOG_PASSWORD") or getpass.getpass(f"{args.login} 비밀번호: ")
    color = out.isatty()
    with httpx.Client(base_url=base, timeout=httpx.Timeout(30, read=None)) as http:
        res = http.post("/api/auth/login", json={"loginId": args.login, "password": password})
        if res.status_code != 200:
            print(f"로그인 실패 ({res.status_code}): {res.text[:200]}", file=sys.stderr)
            return 1
        headers = {"Authorization": f"Bearer {res.json()['accessToken']}"}
        # 서버는 전체 작업 ID(UUID)로만 거른다. 앞부분만 주면 최근 기록을 받아 여기서 거른다
        full_task = args.task if args.task and TASK_ID.fullmatch(args.task) else None
        filters = {k: v for k, v in {"taskId": full_task, "shiftId": args.shift}.items() if v}
        page = http.get("/api/admin/agent-traces", params={**filters, "limit": args.history}, headers=headers)
        if page.status_code != 200:
            print(f"기록을 읽지 못했다 ({page.status_code}): {page.text[:200]}", file=sys.stderr)
            return 1
        last = None
        for event in page.json().get("items", []):
            last = event["id"]
            if wanted(event, args.task, None, None):
                print("\n".join(render(event, verbose=args.verbose, color=color)), file=out, flush=True)
        if args.no_follow:
            return 0
        print("── 실시간으로 따라간다 (Ctrl+C로 끝) ──", file=out, flush=True)
        delay = 1.0
        while True:
            try:
                stream_headers = {**headers, "Accept": "text/event-stream"}
                if last is not None:
                    stream_headers["Last-Event-ID"] = str(last)
                with http.stream("GET", "/api/admin/agent-traces/stream", params=filters, headers=stream_headers) as s:
                    if s.status_code in (401, 403):
                        print(f"스트림 권한 없음 ({s.status_code})", file=sys.stderr)
                        return 1
                    delay = 1.0
                    for event_id, event in sse_events(s.iter_lines()):
                        last = int(event_id) if event_id else last
                        if wanted(event, args.task, None, None):
                            print("\n".join(render(event, verbose=args.verbose, color=color)), file=out, flush=True)
            except KeyboardInterrupt:
                return 0
            except httpx.HTTPError as exc:
                print(f"연결이 끊겼다 ({type(exc).__name__}). {delay:g}초 뒤 다시 붙는다", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m graph.agentlog", description="에이전트 추론 기록(관리자 로그)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="서비스 주소 (backend /api가 열린 곳). 예: http://localhost:3000")
    source.add_argument("--file", help="추론 기록 JSONL 경로. '-'면 표준 입력")
    parser.add_argument("--login", default="manager", help="관리자 아이디 (기본 manager)")
    parser.add_argument("--task", help="이 작업만. 전체 ID면 서버에서, 앞부분이면 최근 기록에서 거른다")
    parser.add_argument("--shift", help="이 근무조만")
    parser.add_argument("--only", help="평가 기록에서 이 시나리오만 (예: SC-02)")
    parser.add_argument("--history", type=int, default=200, help="처음에 보여 줄 지난 기록 수 (최대 500)")
    parser.add_argument("--no-follow", action="store_true", help="지난 기록만 보고 끝낸다")
    parser.add_argument("-v", "--verbose", action="store_true", help="도구 인자·결과 미리보기와 빠진 도구까지")
    args = parser.parse_args(argv)
    if args.url:
        try:
            return follow(args, sys.stdout)
        except KeyboardInterrupt:
            return 0
    stream = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")  # noqa: SIM115
    with stream:
        for event in read_jsonl(stream):
            if wanted(event, args.task, args.shift, args.only):
                print("\n".join(render(event, verbose=args.verbose, color=sys.stdout.isatty())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
