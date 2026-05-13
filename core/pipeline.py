"""
Multi-step agentic pipeline — Admin routes tasks to specialist agents,
collects results, and synthesizes a final answer.

Supports:
  - Sequential chaining (step N feeds into step N+1)
  - Parallel fan-out (independent steps run concurrently)
  - Tool-use loops (agent calls tools until task is complete)
  - Live event streaming via async callbacks
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

from core.tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

EventCallback = Callable[[str, Any], None]   # (event_type, data)


@dataclass
class StepResult:
    agent: str
    task: str
    result: str
    tools_called: list[dict] = field(default_factory=list)
    error: Optional[str] = None


class AgentPipeline:
    """
    Runs a sequence of agent steps, optionally with tool use at each step.
    Designed for real-time product creation flows.
    """

    def __init__(self, llm, emit: Optional[EventCallback] = None) -> None:
        self.llm   = llm
        self.emit  = emit or (lambda t, d: None)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    async def run(
        self,
        goal: str,
        steps: list[dict],
        context: Optional[dict] = None,
    ) -> list[StepResult]:
        """
        Execute an ordered list of steps.
        Each step: {"agent": str, "task": str, "use_tools": bool}
        """
        results: list[StepResult] = []
        accumulated = context or {}

        for i, step in enumerate(steps):
            agent  = step.get("agent", "admin")
            task   = step.get("task", "")
            use_tools = step.get("use_tools", False)

            self.emit("step_start", {"index": i, "agent": agent, "task": task})

            # Inject prior results as context
            if results:
                task += "\n\nContext from previous steps:\n" + "\n".join(
                    f"[{r.agent}]: {r.result[:500]}" for r in results
                )

            try:
                if use_tools:
                    result_text, tools_called = await self._run_with_tools(agent, task, accumulated)
                else:
                    result_text = await self._run_plain(agent, task, accumulated)
                    tools_called = []

                sr = StepResult(agent=agent, task=step["task"], result=result_text, tools_called=tools_called)
                results.append(sr)
                self.emit("step_done", {"index": i, "agent": agent, "result_preview": result_text[:200]})

            except Exception as exc:
                sr = StepResult(agent=agent, task=step["task"], result="", error=str(exc))
                results.append(sr)
                logger.exception("Pipeline step %d failed", i)
                self.emit("step_error", {"index": i, "agent": agent, "error": str(exc)})

        self.emit("pipeline_done", {"total_steps": len(results)})
        return results

    # ------------------------------------------------------------------
    # Parallel fan-out
    # ------------------------------------------------------------------
    async def run_parallel(self, tasks: list[dict]) -> list[StepResult]:
        """Run multiple independent tasks at the same time."""
        coros = [self._run_plain(t["agent"], t["task"], {}) for t in tasks]
        texts = await asyncio.gather(*coros, return_exceptions=True)
        return [
            StepResult(
                agent=t["agent"],
                task=t["task"],
                result=str(text) if not isinstance(text, Exception) else "",
                error=str(text) if isinstance(text, Exception) else None,
            )
            for t, text in zip(tasks, texts)
        ]

    # ------------------------------------------------------------------
    # Tool-use agentic loop
    # ------------------------------------------------------------------
    async def _run_with_tools(
        self,
        agent: str,
        task: str,
        context: dict,
    ) -> tuple[str, list[dict]]:
        from config.settings import LLM_BACKEND

        system = self._agent_system(agent)
        messages = [{"role": "user", "content": task}]
        tools_called: list[dict] = []
        max_rounds = 12

        for _ in range(max_rounds):
            # Use OpenAI tool format for Foundry Local, Anthropic format for Claude
            if LLM_BACKEND == "foundry":
                response = await asyncio.to_thread(
                    self.llm.complete_with_tools,
                    messages, self._openai_tools(), system=system,
                )
                choice = response.choices[0]
                if choice.finish_reason == "tool_calls":
                    tool_calls = choice.message.tool_calls or []
                    messages.append({"role": "assistant", "content": None, "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in tool_calls
                    ]})
                    for tc in tool_calls:
                        name   = tc.function.name
                        inputs = json.loads(tc.function.arguments)
                        self.emit("tool_call", {"tool": name, "inputs": inputs})
                        result = await execute_tool(name, inputs)
                        tools_called.append({"tool": name, "inputs": inputs, "result": result})
                        self.emit("tool_result", {"tool": name, "result": result})
                        messages.append({"role": "tool", "tool_call_id": tc.id,
                                         "content": json.dumps(result)})
                else:
                    return choice.message.content or "", tools_called
            else:
                # Anthropic tool-use format
                response = await asyncio.to_thread(
                    self.llm.complete_with_tools,
                    messages, TOOL_DEFINITIONS, system=system,
                )
                if response.stop_reason == "tool_use":
                    tool_results = []
                    messages.append({"role": "assistant", "content": response.content})
                    for block in response.content:
                        if block.type == "tool_use":
                            self.emit("tool_call", {"tool": block.name, "inputs": block.input})
                            result = await execute_tool(block.name, block.input)
                            tools_called.append({"tool": block.name, "inputs": block.input, "result": result})
                            self.emit("tool_result", {"tool": block.name, "result": result})
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(result),
                            })
                    messages.append({"role": "user", "content": tool_results})
                else:
                    return response.content[0].text, tools_called

        return "Max tool rounds reached.", tools_called

    # ------------------------------------------------------------------
    # Plain (no tools)
    # ------------------------------------------------------------------
    async def _run_plain(self, agent: str, task: str, context: dict) -> str:
        system = self._agent_system(agent)
        messages = [{"role": "user", "content": task}]
        return await self.llm.complete_async(messages, system=system, max_tokens=8192)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _agent_system(agent: str) -> str:
        systems = {
            "admin":     "You are the Admin Agent — orchestrate complex multi-step tasks autonomously.",
            "code":      "You are the Code Agent — write complete, production-ready code.",
            "research":  "You are the Research Agent — synthesize knowledge deeply and accurately.",
            "reasoning": "You are the Reasoning Agent — think step by step with rigorous logic.",
            "ml":        "You are the ML Agent — design and implement machine learning systems.",
            "vision":    "You are the Vision Agent — analyze images and visual data.",
            "project":   "You are the Project Agent — build complete software projects file by file.",
        }
        return systems.get(agent, "You are a helpful AI assistant.")

    @staticmethod
    def _openai_tools() -> list[dict]:
        """Convert Anthropic-format tool defs to OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in TOOL_DEFINITIONS
        ]
