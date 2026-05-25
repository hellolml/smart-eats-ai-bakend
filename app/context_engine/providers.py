from __future__ import annotations

from typing import Protocol

from app.context_engine.types import ContextBlock, ContextRequest


class ContextProvider(Protocol):
    name: str

    async def collect(self, request: ContextRequest) -> list[ContextBlock]:
        ...
