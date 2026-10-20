# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Loads harness.yaml into typed settings so no limit is hard-coded in the middleware.

"""Typed view of `harness.yaml`."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

AGENT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = AGENT_DIR / "harness.yaml"
DEFAULT_PROMPTS = AGENT_DIR / "prompts"


@dataclass(frozen=True)
class ModelSettings:
    primary: str
    fallback: str | None
    kwargs: dict[str, Any] = field(default_factory=dict)  # init_chat_model 인자. temperature 고정 (NFR-04)


@dataclass(frozen=True)
class ToolSettings:
    result_max_chars: int
    rag_top_k: int
    rag_tool: str
    read_only_tasks: frozenset[str]
    allow: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ContextSettings:
    token_budget: int
    keep_tokens: int


@dataclass(frozen=True)
class LoopSettings:
    max_turns: int
    max_seconds: dict[str, float]  # task_type별, 없으면 "default"
    max_same_tool_calls: int

    def seconds_for(self, task_type: str) -> float:
        return self.max_seconds.get(task_type, self.max_seconds["default"])


@dataclass(frozen=True)
class RecoverySettings:
    model_retries: int
    backoff_seconds: tuple[float, ...]
    retry_status: frozenset[int]


@dataclass(frozen=True)
class ApprovalRule:
    """How a destructive tool's approval cards look (docs/specs openapi ApprovalRequest).

    `approver` is fixed for plans and constraints. For route changes it is None: the backend decides the approval
    order per candidate (`PlanCandidate.requiredApprovers`) and the agent follows it, never the grade itself.
    """

    kind: str  # SUBMIT_PLAN | ADD_CONSTRAINT | ROUTE_CHANGE
    approver: str | None = None  # MANAGER | DRIVER


@dataclass(frozen=True)
class GuardrailSettings:
    approval: dict[str, ApprovalRule]
    injected_args: tuple[str, ...]
    mask_keys: frozenset[str]
    id_pattern: str


@dataclass(frozen=True)
class ObservabilitySettings:
    project: str
    eval_project: str
    trace_header: str
    tool_labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessConfig:
    models: ModelSettings
    tools: ToolSettings
    context: ContextSettings
    loop: LoopSettings
    recovery: RecoverySettings
    guardrails: GuardrailSettings
    observability: ObservabilitySettings
    prompts_dir: Path = field(default=DEFAULT_PROMPTS)

    def with_loop(self, **changes: Any) -> HarnessConfig:
        """`max_seconds` may be a number (applies to every task type) or a per-task mapping."""
        if isinstance(changes.get("max_seconds"), int | float):
            changes["max_seconds"] = {"default": float(changes["max_seconds"])}
        return replace(self, loop=replace(self.loop, **changes))

    def with_recovery(self, **changes: Any) -> HarnessConfig:
        return replace(self, recovery=replace(self.recovery, **changes))

    def with_tools(self, **changes: Any) -> HarnessConfig:
        return replace(self, tools=replace(self.tools, **changes))


def _seconds(value: Any) -> dict[str, float]:
    seconds = {k: float(v) for k, v in value.items()} if isinstance(value, dict) else {"default": float(value)}
    if "default" not in seconds:
        raise ValueError("loop.max_seconds needs a `default` entry")
    return seconds


def load_config(path: Path | str = DEFAULT_CONFIG, prompts_dir: Path | str = DEFAULT_PROMPTS) -> HarnessConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    t, c, lp, r, g, o = (raw[k] for k in ("tools", "context", "loop", "recovery", "guardrails", "observability"))
    return HarnessConfig(
        models=ModelSettings(
            primary=raw["models"]["primary"],
            fallback=raw["models"].get("fallback"),
            kwargs=dict(raw["models"].get("kwargs") or {}),
        ),
        tools=ToolSettings(
            result_max_chars=int(t["result_max_chars"]),
            rag_top_k=int(t["rag_top_k"]),
            rag_tool=t["rag_tool"],
            read_only_tasks=frozenset(t["read_only_tasks"]),
            allow={task: tuple(names) for task, names in t["allow"].items()},
        ),
        context=ContextSettings(token_budget=int(c["token_budget"]), keep_tokens=int(c["keep_tokens"])),
        loop=LoopSettings(
            max_turns=int(lp["max_turns"]),
            max_seconds=_seconds(lp["max_seconds"]),
            max_same_tool_calls=int(lp["max_same_tool_calls"]),
        ),
        recovery=RecoverySettings(
            model_retries=int(r["model_retries"]),
            backoff_seconds=tuple(float(s) for s in r["backoff_seconds"]),
            retry_status=frozenset(int(s) for s in r["retry_status"]),
        ),
        guardrails=GuardrailSettings(
            approval={name: ApprovalRule(**rule) for name, rule in g["approval"].items()},
            injected_args=tuple(g["injected_args"]),
            mask_keys=frozenset(g["mask_keys"]),
            id_pattern=g["id_pattern"],
        ),
        observability=ObservabilitySettings(
            project=o["project"],
            eval_project=o["eval_project"],
            trace_header=o["trace_header"],
            tool_labels=dict(o.get("tool_labels") or {}),
        ),
        prompts_dir=Path(prompts_dir),
    )
