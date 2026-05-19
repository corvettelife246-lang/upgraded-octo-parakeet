"""Anthropic Claude API interface with prompt caching, streaming, and extended thinking."""
import asyncio
import base64
import json
from typing import AsyncIterator, Optional

import anthropic

from config.settings import (
    ANTHROPIC_API_KEY,
    DEFAULT_MODEL,
    FAST_MODEL,
    MAX_REASONING_TOKENS,
    REASONING_MODEL,
)


class LLMInterface:
    """Thin wrapper around the Anthropic SDK with caching and streaming support."""

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self._async_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    # ------------------------------------------------------------------
    # Synchronous helpers
    # ------------------------------------------------------------------
    def complete(
        self,
        messages: list[dict],
        system: str = "",
        model: str = DEFAULT_MODEL,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        use_cache: bool = True,
    ) -> str:
        system_blocks = self._build_system_blocks(system, use_cache)
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=messages,
            temperature=temperature,
        )
        return response.content[0].text

    def complete_with_reasoning(
        self,
        messages: list[dict],
        system: str = "",
        thinking_budget: int = MAX_REASONING_TOKENS,
    ) -> tuple[str, str]:
        """Returns (thinking_text, answer_text)."""
        response = self._client.messages.create(
            model=REASONING_MODEL,
            max_tokens=thinking_budget + 4096,
            system=system or "You are an expert reasoning assistant.",
            messages=messages,
            thinking={"type": "enabled", "budget_tokens": thinking_budget},
        )
        thinking = ""
        answer = ""
        for block in response.content:
            if block.type == "thinking":
                thinking = block.thinking
            elif block.type == "text":
                answer = block.text
        return thinking, answer

    # ------------------------------------------------------------------
    # Async helpers with streaming
    # ------------------------------------------------------------------
    async def stream(
        self,
        messages: list[dict],
        system: str = "",
        model: str = DEFAULT_MODEL,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        system_blocks = self._build_system_blocks(system, use_cache=True)
        async with self._async_client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def stream_with_vision(
        self,
        messages: list[dict],
        image_b64: Optional[str] = None,
        media_type: str = "image/jpeg",
        system: str = "",
        model: str = DEFAULT_MODEL,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """Stream a response that includes an inline image."""
        if image_b64:
            vision_msg = {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    }
                ],
            }
            # Merge last user message content with image if present
            if messages and messages[-1]["role"] == "user":
                last = messages[-1]
                if isinstance(last["content"], str):
                    vision_msg["content"].append({"type": "text", "text": last["content"]})
                    messages = messages[:-1] + [vision_msg]
                else:
                    messages = messages[:-1] + [vision_msg]
            else:
                messages = messages + [vision_msg]

        async for chunk in self.stream(messages, system=system, model=model, max_tokens=max_tokens):
            yield chunk

    async def complete_async(
        self,
        messages: list[dict],
        system: str = "",
        model: str = DEFAULT_MODEL,
        max_tokens: int = 4096,
    ) -> str:
        result = []
        async for chunk in self.stream(messages, system=system, model=model, max_tokens=max_tokens):
            result.append(chunk)
        return "".join(result)

    # ------------------------------------------------------------------
    # Tool use
    # ------------------------------------------------------------------
    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str = "",
        model: str = DEFAULT_MODEL,
        max_tokens: int = 4096,
    ) -> dict:
        """Returns the full response object for tool-use loops."""
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system or "You are a helpful assistant with access to tools.",
            messages=messages,
            tools=tools,
        )
        return response

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_system_blocks(system: str, use_cache: bool) -> list[dict] | str:
        if not system:
            return "You are a helpful AI assistant."
        if use_cache:
            return [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        return system

    @staticmethod
    def encode_image(image_bytes: bytes) -> str:
        return base64.b64encode(image_bytes).decode("utf-8")
