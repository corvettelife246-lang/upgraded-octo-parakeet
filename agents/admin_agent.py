"""Admin agent — system orchestration, task planning, and autonomous management."""
import asyncio
import json
import logging
from typing import Optional

from agents.base_agent import BaseAgent
from config.settings import DEFAULT_MODEL

logger = logging.getLogger(__name__)

_SYSTEM = """You are the Admin Agent, the central orchestrator of an autonomous AI platform.

Responsibilities:
- Decompose complex requests into sub-tasks and delegate to specialist agents
- Monitor system health and agent performance
- Make autonomous decisions within defined boundaries
- Summarize multi-agent results into coherent responses
- Handle operator instructions and translate them to agent directives

Always think step-by-step. When delegating, output a JSON plan like:
{"steps": [{"agent": "code|research|ml|reasoning|vision", "task": "..."}]}
If the task can be handled directly, just respond in natural language.
"""


class AdminAgent(BaseAgent):
    name = "Admin Agent"
    description = "Orchestrates the multi-agent platform and handles system administration."

    def _system_prompt(self) -> str:
        return _SYSTEM

    async def run(self, prompt: str, context: Optional[dict] = None) -> str:
        history = (context or {}).get("history", [])
        messages = [*history, {"role": "user", "content": prompt}]
        return await self.llm.complete_async(
            messages,
            system=_SYSTEM,
            model=DEFAULT_MODEL,
            max_tokens=4096,
        )

    async def plan(self, goal: str) -> list[dict]:
        """Return an ordered list of sub-tasks for a complex goal."""
        planning_prompt = (
            f"Decompose this goal into sub-tasks for specialist agents.\n"
            f"Goal: {goal}\n"
            f"Output ONLY valid JSON: "
            f'{{ "steps": [{{"agent": "code|research|ml|reasoning|vision|admin", "task": "..."}}] }}'
        )
        raw = await self.llm.complete_async(
            [{"role": "user", "content": planning_prompt}],
            system="You are a task planning system. Output only valid JSON.",
            model=DEFAULT_MODEL,
            max_tokens=1024,
        )
        try:
            return json.loads(raw).get("steps", [])
        except json.JSONDecodeError:
            logger.warning("Plan JSON parse failed, returning single step")
            return [{"agent": "admin", "task": goal}]

    async def summarize_results(self, goal: str, results: list[dict]) -> str:
        summary_prompt = (
            f"Original goal: {goal}\n\n"
            f"Agent results:\n"
            + "\n".join(f"- [{r['agent']}]: {r['result']}" for r in results)
            + "\n\nSynthesize a clear, complete final answer."
        )
        return await self.llm.complete_async(
            [{"role": "user", "content": summary_prompt}],
            system=_SYSTEM,
            model=DEFAULT_MODEL,
            max_tokens=4096,
        )
