# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Failure codes shared by the agent, the task history screen, and the evaluation report.

"""Failure codes (docs/HARNESS.md section 5)."""
from __future__ import annotations

from enum import StrEnum


class FailureCode(StrEnum):
    LOOP_MAX_TURNS = "LOOP_MAX_TURNS"
    LOOP_TIMEOUT = "LOOP_TIMEOUT"
    LOOP_SAME_TOOL = "LOOP_SAME_TOOL"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_ERROR = "MODEL_ERROR"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class HarnessFailure(Exception):
    """Ends a task. Raised by middleware; the agent turns it into a FAILED event."""

    def __init__(self, code: FailureCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
