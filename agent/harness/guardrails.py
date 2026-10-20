# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Guardrails: approval interrupt before destructive tools and personal-data masking.

"""Guardrails (docs/HARNESS.md section 3.6)."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.tools import BaseTool

from .config import ApprovalRule

PHONE = re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}")
PHONE_MASK = "010-****-****"
LOCATION_MASK = "[위치]"


def _json_or_none(value: str) -> Any:
    """Parsed JSON if the string holds an object or array (raw MCP tool output), else None."""
    if value.lstrip()[:1] not in ("{", "["):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


class Masker:
    """Replaces driver names with stable aliases (`기사#3fa1`), phone numbers and driver locations with masks.

    Aliases are a hash of the name, so one instance can serve every task of a process and the same driver keeps
    the same alias across tasks, segments, and processes. The agent uses it for model input, tool results,
    events, and every payload sent to LangSmith.
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

    def add_names(self, names: Iterable[str]) -> None:
        for name in names:
            self.alias(name)

    def value(self, obj: Any) -> Any:
        """Masks any JSON-like value. Strings that hold JSON (raw MCP tool output) are parsed and masked by key."""
        self._collect_names(obj)
        return self._mask(obj)

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
        elif isinstance(obj, str) and (parsed := _json_or_none(obj)) is not None:
            self._collect_names(parsed)

    def _mask(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: self._mask_field(k, v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._mask(v) for v in obj]
        if isinstance(obj, str):
            return self._mask_string(obj)
        return obj

    def _mask_string(self, value: str) -> str:
        parsed = _json_or_none(value)
        if parsed is None:
            return self.text(value)
        self._collect_names(parsed)
        return json.dumps(self._mask(parsed), ensure_ascii=False)

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


def approval_middleware(tool_names: Iterable[str], rules: Mapping[str, ApprovalRule]) -> HumanInTheLoopMiddleware:
    """Interrupt before every destructive tool. The approval card (contract `ApprovalRequest`) is built from the
    pending call by the agent; the human answers approve, reject (with a reason or excluded stations), or edit."""
    return HumanInTheLoopMiddleware(
        interrupt_on={
            name: {"allowed_decisions": ["approve", "edit", "reject"], "description": f"승인 필요: {rules[name].kind}"}
            for name in tool_names
        }
    )
