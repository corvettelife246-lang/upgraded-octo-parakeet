"""
Autonomous Planner Agent — decompose a high-level goal into an ordered
sequence of subtasks, assign each to the best specialist agent, execute
them in dependency order (feeding prior outputs forward), and stream
real-time progress events over WebSocket.
"""
import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

_PLAN_SYSTEM = """You are an expert project planner. Given a high-level goal, produce a JSON execution plan.

Rules:
- Break the goal into 2–8 concrete, actionable steps
- Assign each step to ONE agent: admin | code | research | reasoning | ml | vision
- List integer IDs in depends_on for steps that must finish first
- Keep prompts self-contained but reference prior-step context via [Step N result] when needed
- Return ONLY valid JSON — no markdown, no prose

JSON schema:
{
  "goal": "<original goal>",
  "plan": [
    {"id": 1, "title": "<short title>", "agent": "<agent>", "prompt": "<full prompt>", "depends_on": []},
    {"id": 2, "title": "<short title>", "agent": "<agent>", "prompt": "<full prompt>", "depends_on": [1]}
  ]
}"""


class PlannerAgent:
    def __init__(self, llm):
        self.llm     = llm
        self._agents: dict = {}

    def set_agents(self, agents: dict) -> None:
        self._agents = agents

    async def run(self, goal: str, context: dict = None) -> str:
        events = []
        async for ev in self.execute_stream(goal):
            events.append(ev)
        completed = [e for e in events if e.get("type") == "task_done"]
        if completed:
            return "\n\n".join(
                f"**Step {e['task_id']}: {e['title']}**\n{e['result']}"
                for e in completed
            )
        errors = [e for e in events if e.get("type") in ("error", "task_error")]
        if errors:
            return f"Planning failed: {errors[0].get('message') or errors[0].get('error')}"
        return "No tasks executed."

    async def plan(self, goal: str) -> dict:
        from core.backend_router import backend
        llm  = backend()
        resp = await llm.complete(
            system=_PLAN_SYSTEM,
            messages=[{"role": "user", "content": f"Goal: {goal}"}],
            max_tokens=4096,
        )
        text = resp if isinstance(resp, str) else resp.get("content", str(resp))
        start = text.find('{')
        end   = text.rfind('}') + 1
        if start < 0 or end <= start:
            raise ValueError(f"Planner returned no JSON. Response: {text[:300]}")
        return json.loads(text[start:end])

    async def execute_stream(self, goal: str) -> AsyncGenerator[dict, None]:
        plan_id = str(uuid.uuid4())[:8]
        yield {"type": "plan_start", "plan_id": plan_id, "goal": goal}

        # Generate plan
        try:
            yield {"type": "planning", "message": "Decomposing goal into subtasks…"}
            plan_data = await self.plan(goal)
            tasks = plan_data.get("plan", [])
            yield {"type": "plan_ready", "plan": tasks, "task_count": len(tasks)}
        except Exception as exc:
            yield {"type": "error", "message": f"Planning failed: {exc}"}
            return

        # Execute in dependency order
        results: dict[int, str] = {}
        for task in tasks:
            tid    = task.get("id", 0)
            title  = task.get("title", f"Step {tid}")
            agent  = task.get("agent", "admin")
            prompt = task.get("prompt", "")
            deps   = task.get("depends_on", [])

            # Inject prior-step results
            if deps:
                injections = [
                    f"[Step {d} result]:\n{results[d]}"
                    for d in deps if d in results
                ]
                if injections:
                    prompt = "\n\n".join(injections) + "\n\n" + prompt

            yield {"type": "task_start", "task_id": tid, "title": title, "agent": agent}
            try:
                agent_obj = self._agents.get(agent) or self._agents.get("admin")
                if agent_obj:
                    result = await agent_obj.run(prompt, context={})
                else:
                    result = f"Agent '{agent}' unavailable."
                results[tid] = result
                yield {
                    "type":      "task_done",
                    "task_id":   tid,
                    "title":     title,
                    "agent":     agent,
                    "result":    result[:800],
                    "truncated": len(result) > 800,
                }
            except Exception as exc:
                results[tid] = f"Error: {exc}"
                yield {"type": "task_error", "task_id": tid, "title": title, "error": str(exc)}
                logger.exception("Planner task %d failed", tid)

        yield {"type": "plan_complete", "plan_id": plan_id, "task_count": len(tasks)}
