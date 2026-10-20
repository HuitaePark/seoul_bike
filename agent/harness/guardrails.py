# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Guardrails: approval interrupt before destructive tools and personal-data masking.

"""Guardrails (docs/HARNESS.md section 3.6)."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.tools import BaseTool

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
