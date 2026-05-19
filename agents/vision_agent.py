"""Vision agent — image/video analysis via Claude's vision API."""
from typing import AsyncIterator, Optional

from agents.base_agent import BaseAgent
from config.settings import DEFAULT_MODEL
from core.llm_interface import LLMInterface

_SYSTEM = """You are the Vision Agent — an expert at understanding images and video frames.

Capabilities:
- Describe images in detail (objects, people, text, scenes)
- Perform OCR on screenshots and documents
- Analyze charts, diagrams, and technical drawings
- Detect objects and their spatial relationships
- Answer questions about visual content

Be precise and thorough. Always mention confidence level for uncertain observations.
"""


class VisionAgent(BaseAgent):
    name = "Vision Agent"
    description = "Analyzes images and video frames using Claude vision."

    def _system_prompt(self) -> str:
        return _SYSTEM

    async def run(self, prompt: str, context: Optional[dict] = None) -> str:
        image_b64 = (context or {}).get("image_b64")
        if not image_b64:
            return "No image provided. Please attach an image or take a snapshot first."
        history = (context or {}).get("history", [])
        messages = [*history, {"role": "user", "content": prompt}]
        result = []
        async for chunk in self.llm.stream_with_vision(
            messages,
            image_b64=image_b64,
            system=_SYSTEM,
            model=DEFAULT_MODEL,
        ):
            result.append(chunk)
        return "".join(result)

    async def describe(self, image_b64: str) -> str:
        return await self.run("Describe this image in detail.", context={"image_b64": image_b64})

    async def extract_text(self, image_b64: str) -> str:
        return await self.run(
            "Extract all visible text from this image. Preserve formatting where possible.",
            context={"image_b64": image_b64},
        )
