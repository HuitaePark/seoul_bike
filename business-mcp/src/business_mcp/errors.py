# Author: 정유은
# Date: 20OCT2026
# Purpose: 백엔드 오류(problem+json)를 모델이 다시 호출할 수 있는 ToolError JSON으로 바꾼다.

"""도구 오류 형식 (docs/MCP.md 2.3).

백엔드는 RFC 9457 problem+json(ToolProblem: code, detail, suggestions)으로 오류를 준다 (docs/specs/internal-api.yaml).
MCP는 이를 모델이 바로 다시 호출할 수 있는 JSON(code, message, retryable, hint)으로 바꿔 ToolError에 담는다.
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp.exceptions import ToolError

# 본문(problem+json)이 없을 때 상태 코드로 정하는 오류 코드와 메시지.
STATUS_CODES = {
    400: ("INVALID_ARGUMENT", "요청 형식이 맞지 않습니다. 인자를 고쳐 다시 호출하세요."),
    401: ("UNAUTHORIZED", "백엔드가 서비스 토큰을 받지 않았습니다 (X-Internal-Token). 운영자에게 알리세요."),
    403: ("FORBIDDEN", "백엔드가 이 요청을 거부했습니다. 승인이나 권한이 필요합니다."),
    404: ("NOT_FOUND", "백엔드에 해당 자원이나 API가 없습니다."),
    409: ("CONFLICT", "백엔드 상태와 충돌합니다. 최신 상태를 다시 조회하세요."),
}
# 인자만 고쳐 다시 부르면 되는 경우. 401·403은 사람(운영자·승인자)이 먼저 움직여야 한다.
RETRYABLE_STATUS = {400, 422}


class InternalApiError(Exception):
    """백엔드 /internal/** 가 돌려준 업무 오류. 가짜 구현도 같은 예외를 쓴다."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        hint: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.hint = hint or {}

    @classmethod
    def from_problem(cls, status: int, body: dict[str, Any]) -> "InternalApiError":
        """retryable: 다시 부를 때 쓸 값(suggestions)이 있거나 입력 오류(400·422)일 때만. 401·403은 언제나 false."""
        hint: dict[str, Any] = {}
        if body.get("suggestions"):
            hint["candidates"] = body["suggestions"]
        default_code, default_message = STATUS_CODES.get(status, ("UPSTREAM_ERROR", f"요청이 거부되었습니다({status})."))
        retryable = status not in (401, 403) and (bool(hint) or status in RETRYABLE_STATUS)
        return cls(
            body.get("code") or default_code,
            body.get("detail") or body.get("title") or default_message,
            retryable=retryable,
            hint=hint,
        )


def tool_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    hint: dict[str, Any] | None = None,
) -> ToolError:
    body = {"code": code, "message": message, "retryable": retryable, "hint": hint or {}}
    return ToolError(json.dumps(body, ensure_ascii=False))


def from_internal(err: InternalApiError) -> ToolError:
    return tool_error(err.code, err.message, retryable=err.retryable, hint=err.hint)
