# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Guardrails: approval interrupt before destructive tools, personal-data masking, output validation.

"""Guardrails (docs/HARNESS.md section 3.6)."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, HumanInTheLoopMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

from .errors import FailureCode, HarnessFailure

PHONE = re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}")
PHONE_MASK = "010-****-****"
LOCATION_MASK = "[위치]"


class Masker:
    """Replaces driver names with stable aliases (`기사#3fa1`), phone numbers and driver locations with masks.

    Aliases are a hash of the name, so the same driver keeps the same alias across segments and processes.
    """

    def __init__(self, mask_keys: Iterable[str], names: Iterable[str] = ()) -> None:
        self.mask_keys = frozenset(mask_keys)
        self._aliases: dict[str, str] = {}
        for name in names:
            self.alias(name)

    def alias(self, name: str) -> str:
        name = name.strip()
        if name and name not in self._aliases:
            self._aliases[name] = f"기사#{hashlib.sha256(name.encode()).hexdigest()[:4]}"
        return self._aliases.get(name, name)

    def text(self, value: str) -> str:
        for name in sorted(self._aliases, key=len, reverse=True):
            value = value.replace(name, self._aliases[name])
        return PHONE.sub(PHONE_MASK, value)

    def value(self, obj: Any) -> Any:
        self._collect_names(obj)
        return self._mask(obj)

    def json_text(self, text: str) -> str:
        """Masks a tool result: structurally if it is JSON, by pattern otherwise."""
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return self.text(text)
        return json.dumps(self.value(parsed), ensure_ascii=False)

    def _collect_names(self, obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in self.mask_keys and "name" in k and isinstance(v, str):
                    self.alias(v)
                else:
                    self._collect_names(v)
        elif isinstance(obj, list):
            for v in obj:
                self._collect_names(v)

    def _mask(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: self._mask_field(k, v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._mask(v) for v in obj]
        if isinstance(obj, str):
            return self.text(obj)
        return obj

    def _mask_field(self, key: str, value: Any) -> Any:
        if key not in self.mask_keys:
            return self._mask(value)
        if "name" in key and isinstance(value, str):
            return self.alias(value)
        if "phone" in key:
            return PHONE_MASK
        return LOCATION_MASK


def destructive_tools(tools: Iterable[BaseTool]) -> list[str]:
    """Tools the MCP server marked with `destructiveHint` (annotations arrive as tool metadata)."""
    return [t.name for t in tools if (t.metadata or {}).get("destructiveHint")]


def approval_middleware(tool_names: Iterable[str], approver: dict[str, str]) -> HumanInTheLoopMiddleware:
    """Interrupt before every destructive tool. Human answers approve, edit (e.g. exclude stations), or reject."""
    return HumanInTheLoopMiddleware(
        interrupt_on={
            name: {
                "allowed_decisions": ["approve", "edit", "reject"],
                "description": f"승인 필요: {name} (승인 주체: {approver.get(name, 'manager')})",
            }
            for name in tool_names
        }
    )


def _last_ai(response: ModelResponse | AIMessage) -> AIMessage | None:
    if isinstance(response, AIMessage):
        return response
    return next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)


class OutputValidationMiddleware(AgentMiddleware):
    """Destructive tool arguments may only use station IDs that appeared in earlier successful tool results.

    An invalid call is sent back to the model once with the reason; a second invalid call ends the task.
    Runs before the approval interrupt, so a human never reviews a call that references unknown stations.
    """

    def __init__(self, destructive: Iterable[str], id_pattern: str) -> None:
        super().__init__()
        self.destructive = frozenset(destructive)
        self.id_pattern = re.compile(id_pattern)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        response = await handler(request)
        ai = _last_ai(response)
        problems = self._problems(ai, request.messages)
        if not problems:
            return response
        feedback = [
            ToolMessage(
                content=problems.get(tc["id"], "실행되지 않음: 같은 응답의 다른 도구 호출이 검증에 실패했다."),
                tool_call_id=tc["id"],
                name=tc["name"],
                status="error",
            )
            for tc in ai.tool_calls
        ]
        retry = await handler(request.override(messages=[*request.messages, ai, *feedback]))
        if still := self._problems(_last_ai(retry), request.messages):
            raise HarnessFailure(FailureCode.OUTPUT_INVALID, "; ".join(still.values()))
        return retry

    def _problems(self, ai: AIMessage | None, history: list[BaseMessage]) -> dict[str, str]:
        if ai is None or not ai.tool_calls:
            return {}
        calls = [tc for tc in ai.tool_calls if tc["name"] in self.destructive]
        if not calls:
            return {}
        known = {
            match
            for m in history
            if isinstance(m, ToolMessage) and m.status != "error"
            for match in self.id_pattern.findall(str(m.content))
        }
        problems = {}
        for tc in calls:
            used = set(self.id_pattern.findall(json.dumps(tc["args"], ensure_ascii=False)))
            if unknown := sorted(used - known):
                problems[tc["id"]] = (
                    f"{tc['name']} 거부: 조회 결과에 없는 대여소 ID {', '.join(unknown)}. "
                    "조회 도구로 먼저 확인한 ID만 쓴다."
                )
        return problems
