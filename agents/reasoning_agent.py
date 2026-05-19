"""Reasoning agent — extended thinking, chain-of-thought, and multi-step problem solving."""
from typing import Optional

from agents.base_agent import BaseAgent
from config.settings import MAX_REASONING_TOKENS, REASONING_MODEL

_SYSTEM = """You are the Reasoning Agent — a specialist in rigorous, multi-step thinking.

Use your extended thinking capability to:
- Break complex problems into logical steps
- Verify each step before proceeding
- Identify hidden assumptions and edge cases
- Produce mathematically and logically sound conclusions
- Plan multi-phase solutions for ambitious goals

Think slowly, think deeply, and show your work.
"""


class ReasoningAgent(BaseAgent):
    name = "Reasoning Agent"
    description = "Extended chain-of-thought reasoning for complex problems."

    def _system_prompt(self) -> str:
        return _SYSTEM

    async def run(
        self,
        prompt: str,
        context: Optional[dict] = None,
        thinking_budget: int = MAX_REASONING_TOKENS,
    ) -> str:
        import asyncio
        history = (context or {}).get("history", [])
        messages = [*history, {"role": "user", "content": prompt}]
        thinking, answer = await asyncio.to_thread(
            self.llm.complete_with_reasoning,
            messages,
            system=_SYSTEM,
            thinking_budget=thinking_budget,
        )
        self._last_thinking = thinking
        return answer

    async def plan_solution(self, goal: str) -> str:
        prompt = (
            f"Create a detailed, step-by-step plan to accomplish this goal:\n{goal}\n\n"
            "Include: prerequisites, phases, success criteria, and potential blockers."
        )
        return await self.run(prompt)
