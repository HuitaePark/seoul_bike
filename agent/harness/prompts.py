# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Instructions: versioned prompt files per task type and the boundary around untrusted user input.

"""Instructions (docs/HARNESS.md section 3.1)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Prompt:
    text: str
    version: str  # e.g. "common@1+plan_draft@2"; recorded on every trace


def _read(path: Path) -> tuple[str, int]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ValueError(f"{path}: front matter with `version` is required")
    _, header, body = raw.split("---\n", 2)
    return body.strip(), int(yaml.safe_load(header)["version"])


def load_prompt(prompts_dir: Path, task_type: str) -> Prompt:
    common, common_v = _read(prompts_dir / "common.md")
    task, task_v = _read(prompts_dir / f"{task_type}.md")
    return Prompt(text=f"{common}\n\n{task}", version=f"common@{common_v}+{task_type}@{task_v}")


def wrap_user_input(text: str) -> str:
    """Untrusted text from drivers and managers. The closing tag is neutralised so input cannot escape the block."""
    safe = text.replace("</user_input>", "</ user_input>")
    return f"<user_input>\n{safe}\n</user_input>"
