from __future__ import annotations

from pathlib import Path

PACKAGE_PROMPTS = Path(__file__).parent / "prompts"
REPO_PROMPTS = Path(__file__).resolve().parents[2] / "prompts"


def load_prompt(name: str) -> str:
    filename = name if name.endswith(".md") else f"{name}.md"
    for folder in (REPO_PROMPTS, PACKAGE_PROMPTS, Path.cwd() / "prompts"):
        path = folder / filename
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"Prompt not found: {filename}")
