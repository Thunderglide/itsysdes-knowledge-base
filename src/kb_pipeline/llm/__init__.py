from __future__ import annotations

from typing import Protocol


class LLMBackend(Protocol):
    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        ...
