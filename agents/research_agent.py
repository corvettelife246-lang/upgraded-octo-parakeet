"""Research agent — information synthesis, summarization, and knowledge retrieval."""
from typing import Optional

from agents.base_agent import BaseAgent
from config.settings import DEFAULT_MODEL

_SYSTEM = """You are the Research Agent — an expert at finding, analyzing, and synthesizing information.

Responsibilities:
- Answer factual and conceptual questions with depth and accuracy
- Summarize lengthy documents or topics into clear explanations
- Compare and contrast concepts, tools, or approaches
- Identify key insights and actionable takeaways
- Cite reasoning and flag uncertainty clearly

Always structure complex answers with headers and bullet points for clarity.
"""


class ResearchAgent(BaseAgent):
    name = "Research Agent"
    description = "Deep research, summarization, and knowledge synthesis."

    def _system_prompt(self) -> str:
        return _SYSTEM

    async def run(self, prompt: str, context: Optional[dict] = None) -> str:
        history = (context or {}).get("history", [])
        messages = [*history, {"role": "user", "content": prompt}]
        return await self.llm.complete_async(
            messages, system=_SYSTEM, model=DEFAULT_MODEL, max_tokens=4096
        )

    async def summarize(self, text: str, max_words: int = 300) -> str:
        prompt = f"Summarize the following in ~{max_words} words:\n\n{text}"
        return await self.llm.complete_async(
            [{"role": "user", "content": prompt}],
            system=_SYSTEM,
            model=DEFAULT_MODEL,
            max_tokens=1024,
        )

    async def compare(self, topic_a: str, topic_b: str) -> str:
        prompt = f"Compare and contrast '{topic_a}' vs '{topic_b}' in detail."
        return await self.llm.complete_async(
            [{"role": "user", "content": prompt}],
            system=_SYSTEM,
            model=DEFAULT_MODEL,
            max_tokens=2048,
        )
