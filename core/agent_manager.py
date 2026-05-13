"""Multi-agent orchestration: spawn, route, and coordinate autonomous agents."""
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Optional

import yaml

from config.settings import AGENT_TIMEOUT, BASE_DIR, LLM_BACKEND, MAX_AGENTS
from core.backend_router import backend as get_backend

logger = logging.getLogger(__name__)

_AGENT_CFG_PATH = BASE_DIR / "config" / "agents.yaml"


@dataclass
class AgentTask:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_type: str = "admin"
    prompt: str = ""
    context: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "pending"    # pending | running | completed | failed
    result: Optional[str] = None
    error: Optional[str] = None
    thinking: Optional[str] = None


class AgentManager:
    """Central hub that routes tasks to specialized agents and tracks execution."""

    def __init__(self) -> None:
        self.llm = get_backend()
        self._tasks: dict[str, AgentTask] = {}
        self._agent_configs: dict[str, dict] = self._load_agent_configs()
        self._active_count = 0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def run_task(
        self,
        prompt: str,
        agent_type: str = "admin",
        context: Optional[dict] = None,
        stream: bool = False,
    ) -> AsyncIterator[str] | str:
        task = AgentTask(agent_type=agent_type, prompt=prompt, context=context or {})
        self._tasks[task.task_id] = task

        async with self._lock:
            if self._active_count >= MAX_AGENTS:
                task.status = "failed"
                task.error = "Max concurrent agents reached"
                return "Error: max agent capacity reached."
            self._active_count += 1

        task.status = "running"
        try:
            if stream:
                return self._stream_task(task)
            result = await asyncio.wait_for(self._execute_task(task), timeout=AGENT_TIMEOUT)
            task.status = "completed"
            task.result = result
            return result
        except asyncio.TimeoutError:
            task.status = "failed"
            task.error = "Timeout"
            return f"Agent '{agent_type}' timed out after {AGENT_TIMEOUT}s."
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            logger.exception("Agent task %s failed", task.task_id)
            return f"Error: {exc}"
        finally:
            async with self._lock:
                self._active_count -= 1

    async def route(self, prompt: str, context: Optional[dict] = None) -> str:
        """Auto-select the best agent for the given prompt using a fast classifier."""
        agent_type = await self._classify_prompt(prompt)
        result = await self.run_task(prompt, agent_type=agent_type, context=context)
        return result  # type: ignore[return-value]

    async def route_stream(self, prompt: str, context: Optional[dict] = None) -> AsyncIterator[str]:
        agent_type = await self._classify_prompt(prompt)
        task = AgentTask(agent_type=agent_type, prompt=prompt, context=context or {})
        self._tasks[task.task_id] = task
        task.status = "running"
        async for chunk in self._stream_task(task):
            yield chunk

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict]:
        return [
            {
                "task_id": t.task_id,
                "agent_type": t.agent_type,
                "status": t.status,
                "created_at": t.created_at.isoformat(),
                "prompt_preview": t.prompt[:80],
            }
            for t in self._tasks.values()
        ]

    # ------------------------------------------------------------------
    # Internal execution
    # ------------------------------------------------------------------
    async def _execute_task(self, task: AgentTask) -> str:
        cfg = self._agent_configs.get(task.agent_type, {})
        system_prompt = self._build_system_prompt(task.agent_type, cfg)
        messages = self._build_messages(task)

        if task.agent_type == "reasoning":
            thinking, answer = await asyncio.to_thread(
                self.llm.complete_with_reasoning,
                messages,
                system=system_prompt,
                thinking_budget=cfg.get("thinking_budget", 8000),
            )
            task.thinking = thinking
            return answer

        return await self.llm.complete_async(
            messages,
            system=system_prompt,
            model=cfg.get("model", "claude-opus-4-7"),
            max_tokens=cfg.get("max_tokens", 4096),
        )

    async def _stream_task(self, task: AgentTask) -> AsyncIterator[str]:
        cfg = self._agent_configs.get(task.agent_type, {})
        system_prompt = self._build_system_prompt(task.agent_type, cfg)
        messages = self._build_messages(task)
        chunks = []
        async for chunk in self.llm.stream(
            messages,
            system=system_prompt,
            model=cfg.get("model", "claude-opus-4-7"),
            max_tokens=cfg.get("max_tokens", 4096),
        ):
            chunks.append(chunk)
            yield chunk
        task.status = "completed"
        task.result = "".join(chunks)

    async def _classify_prompt(self, prompt: str) -> str:
        lower = prompt.lower()
        if any(w in lower for w in ("write code", "implement", "debug", "function", "class", "script", "program")):
            return "code"
        if any(w in lower for w in ("train", "model", "dataset", "neural", "machine learning", "deep learning", "pytorch", "tensorflow")):
            return "ml"
        if any(w in lower for w in ("search", "research", "find information", "summarize", "what is", "explain")):
            return "research"
        if any(w in lower for w in ("reason", "solve", "prove", "logic", "step by step", "analyze", "plan")):
            return "reasoning"
        if any(w in lower for w in ("image", "photo", "screenshot", "picture", "look at", "describe the")):
            return "vision"
        return "admin"

    def _build_system_prompt(self, agent_type: str, cfg: dict) -> str:
        caps = ", ".join(cfg.get("capabilities", []))
        return (
            f"You are the {cfg.get('name', agent_type)} — {cfg.get('description', '')}. "
            f"Your capabilities include: {caps}. "
            "Respond concisely, accurately, and autonomously. "
            "When writing code always include complete, runnable implementations. "
            "You are part of an autonomous multi-agent AI platform running on WSL-2 Linux."
        )

    @staticmethod
    def _build_messages(task: AgentTask) -> list[dict]:
        msgs: list[dict] = []
        history = task.context.get("history", [])
        msgs.extend(history)
        msgs.append({"role": "user", "content": task.prompt})
        return msgs

    @staticmethod
    def _load_agent_configs() -> dict:
        try:
            with open(_AGENT_CFG_PATH) as f:
                data = yaml.safe_load(f)
            return data.get("agents", {})
        except Exception:
            return {}
