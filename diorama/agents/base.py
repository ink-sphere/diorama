from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from tau_agent.provider import ModelProvider


class BaseDioramaAgent[OutputT](ABC):
    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: str,
        **kwargs: Any,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be non-empty")
        self.provider = provider
        self.model = model
        self.configure(**kwargs)

    @abstractmethod
    def configure(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> OutputT:
        raise NotImplementedError
