"""
Microsoft Foundry Local interface — OpenAI-compatible REST API.

Foundry Local runs on Windows 10/11 and exposes an OpenAI-compatible endpoint.
Default: http://localhost:5273/v1  (Windows)
From WSL-2: http://<windows-host-ip>:5273/v1

Install on Windows:
  winget install Microsoft.FoundryLocal
  foundry model run phi-4-mini         # starts service + downloads model
  foundry service status               # check it's running

Supported models (as of 2025):
  phi-4-mini, phi-4, phi-3.5-mini,
  llama-3.2-3b, llama-3.1-8b,
  mistral-7b, qwen2.5-7b
"""
import asyncio
import logging
from typing import AsyncIterator, Optional

from config.settings import (
    FOUNDRY_LOCAL_MODEL,
    FOUNDRY_LOCAL_URL,
    MAX_REASONING_TOKENS,
)

logger = logging.getLogger(__name__)


class FoundryLocalInterface:
    """OpenAI-compatible client for Microsoft Foundry Local."""

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None) -> None:
        self._base_url = (base_url or FOUNDRY_LOCAL_URL).rstrip("/")
        self._model = model or FOUNDRY_LOCAL_MODEL
        self._client = None
        self._async_client = None
        self._init_clients()

    def _init_clients(self) -> None:
        try:
            from openai import AsyncOpenAI, OpenAI
            self._client = OpenAI(
                api_key="foundry-local",          # Foundry Local ignores the key
                base_url=f"{self._base_url}/v1",
            )
            self._async_client = AsyncOpenAI(
                api_key="foundry-local",
                base_url=f"{self._base_url}/v1",
            )
        except ImportError:
            logger.error("openai package not installed. Run: pip install openai")
            raise

    # ------------------------------------------------------------------
    # List available models
    # ------------------------------------------------------------------
    def list_models(self) -> list[str]:
        try:
            response = self._client.models.list()
            return [m.id for m in response.data]
        except Exception as exc:
            logger.warning("Could not list Foundry Local models: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Synchronous completion
    # ------------------------------------------------------------------
    def complete(
        self,
        messages: list[dict],
        system: str = "",
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        full_messages = self._inject_system(messages, system)
        response = self._client.chat.completions.create(
            model=model or self._model,
            messages=full_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # Async completion
    # ------------------------------------------------------------------
    async def complete_async(
        self,
        messages: list[dict],
        system: str = "",
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        full_messages = self._inject_system(messages, system)
        response = await self._async_client.chat.completions.create(
            model=model or self._model,
            messages=full_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    async def stream(
        self,
        messages: list[dict],
        system: str = "",
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        full_messages = self._inject_system(messages, system)
        stream = await self._async_client.chat.completions.create(
            model=model or self._model,
            messages=full_messages,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ------------------------------------------------------------------
    # Vision — send image as base64 data URL
    # ------------------------------------------------------------------
    async def stream_with_vision(
        self,
        messages: list[dict],
        image_b64: Optional[str] = None,
        media_type: str = "image/jpeg",
        system: str = "",
        model: Optional[str] = None,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        if image_b64:
            vision_msg = {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                    }
                ],
            }
            if messages and messages[-1]["role"] == "user":
                last = messages[-1]
                text = last["content"] if isinstance(last["content"], str) else ""
                if text:
                    vision_msg["content"].append({"type": "text", "text": text})
                messages = messages[:-1] + [vision_msg]
            else:
                messages = messages + [vision_msg]

        async for chunk in self.stream(messages, system=system, model=model, max_tokens=max_tokens):
            yield chunk

    # ------------------------------------------------------------------
    # Simulated reasoning (no native thinking — uses CoT prompt)
    # ------------------------------------------------------------------
    async def complete_with_reasoning(
        self,
        messages: list[dict],
        system: str = "",
        thinking_budget: int = MAX_REASONING_TOKENS,
    ) -> tuple[str, str]:
        """
        Foundry Local models don't have native thinking mode.
        Simulates it with an explicit chain-of-thought prompt.
        Returns (thinking_text, answer_text).
        """
        cot_system = (
            system + "\n\n"
            "Think step by step. First write your full reasoning inside <thinking>...</thinking> tags, "
            "then write your final answer after </thinking>."
        )
        full_messages = self._inject_system(messages, cot_system)
        response = await self._async_client.chat.completions.create(
            model=self._model,
            messages=full_messages,
            max_tokens=min(thinking_budget + 4096, 16384),
            temperature=0.3,
        )
        raw = response.choices[0].message.content or ""
        thinking, answer = self._parse_cot(raw)
        return thinking, answer

    # ------------------------------------------------------------------
    # Tool use (OpenAI function-calling format)
    # ------------------------------------------------------------------
    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str = "",
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> object:
        full_messages = self._inject_system(messages, system)
        return self._client.chat.completions.create(
            model=model or self._model,
            messages=full_messages,
            tools=tools,
            max_tokens=max_tokens,
        )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    def health_check(self) -> dict:
        try:
            models = self.list_models()
            return {"status": "ok", "models": models, "url": self._base_url}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "url": self._base_url}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _inject_system(messages: list[dict], system: str) -> list[dict]:
        if not system:
            return messages
        return [{"role": "system", "content": system}] + messages

    @staticmethod
    def _parse_cot(raw: str) -> tuple[str, str]:
        import re
        m = re.search(r"<thinking>(.*?)</thinking>(.*)", raw, re.DOTALL)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return "", raw.strip()
