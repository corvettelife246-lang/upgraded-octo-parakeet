"""Abstract base class shared by all specialized agents."""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from core.llm_interface import LLMInterface


class BaseAgent(ABC):
    name: str = "base"
    description: str = ""

    def __init__(self, llm: Optional[LLMInterface] = None) -> None:
        self.llm = llm or LLMInterface()

    @abstractmethod
    async def run(self, prompt: str, context: Optional[dict] = None) -> str: ...

    async def stream(self, prompt: str, context: Optional[dict] = None) -> AsyncIterator[str]:
        history = (context or {}).get("history", [])
        messages = [*history, {"role": "user", "content": prompt}]
        async for chunk in self.llm.stream(messages, system=self._system_prompt()):
            yield chunk

    def _system_prompt(self) -> str:
        return f"You are {self.name}. {self.description}"
