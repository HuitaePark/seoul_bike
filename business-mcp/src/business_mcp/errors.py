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

# 403 APPROVAL_REQUIRED는 사람의 승인을 기다려야 하므로 모델이 인자만 바꿔 다시 부를 수 없다.
NOT_RETRYABLE_STATUS = {401, 403}


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
        hint: dict[str, Any] = {}
        if body.get("suggestions"):
            hint["candidates"] = body["suggestions"]
        return cls(
            body.get("code") or "INVALID_ARGUMENT",
            body.get("detail") or body.get("title") or f"요청이 거부되었습니다({status}).",
            retryable=status not in NOT_RETRYABLE_STATUS,
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
