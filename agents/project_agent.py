"""
Project Agent — autonomously builds complete software projects.

Given a description like "Build a REST API with FastAPI and SQLite",
it plans the file structure, writes every file, installs dependencies,
and runs tests — all via tool calls streamed in real-time.
"""
import asyncio
import json
from typing import AsyncIterator, Callable, Optional

from agents.base_agent import BaseAgent
from config.settings import DEFAULT_MODEL, WORKSPACE_DIR
from core.pipeline import AgentPipeline
from core.tools import TOOL_DEFINITIONS, execute_tool

_SYSTEM = """You are the Project Agent — an expert software architect and developer.

Your job is to build COMPLETE, working software projects from a description.

Process:
1. Plan the project structure (files, dirs, dependencies)
2. Write EVERY file completely — never skip or truncate
3. Install dependencies via shell tool
4. Run the project to verify it works
5. Fix any errors you encounter

Rules:
- Use tool calls to write files — never output code in text
- Write complete, production-quality files with proper error handling
- Include README.md, requirements.txt / package.json as appropriate
- Test the project after building it
- Report final status: success or what needs fixing

Available tools: file_write, file_read, list_dir, shell, search_files, grep
"""


class ProjectAgent(BaseAgent):
    name = "Project Agent"
    description = "Builds complete software projects autonomously using tool calls."

    def _system_prompt(self) -> str:
        return _SYSTEM

    async def run(
        self,
        prompt: str,
        context: Optional[dict] = None,
        project_name: Optional[str] = None,
        emit: Optional[Callable] = None,
    ) -> str:
        emit = emit or (lambda t, d: None)
        project_dir = WORKSPACE_DIR / (project_name or _slugify(prompt[:40]))
        project_dir.mkdir(parents=True, exist_ok=True)

        emit("project_start", {"name": project_dir.name, "path": str(project_dir)})

        pipeline = AgentPipeline(self.llm, emit=emit)
        steps = [
            {
                "agent": "project",
                "task": (
                    f"Build this project in the workspace directory '{project_dir.name}':\n\n"
                    f"{prompt}\n\n"
                    f"Start by listing what files you will create with list_dir('.'), "
                    f"then write every file with file_write. "
                    f"Finally run `ls -la` and any startup command to verify the project works."
                ),
                "use_tools": True,
            }
        ]
        results = await pipeline.run(prompt, steps)
        result_text = results[0].result if results else "No result."

        emit("project_done", {"name": project_dir.name, "result": result_text[:200]})
        return result_text

    async def build_stream(
        self,
        prompt: str,
        project_name: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """
        Async generator — yields event dicts as the project is built.
        Events: {type: "tool_call"|"tool_result"|"step_done"|"project_done", ...}
        """
        events: asyncio.Queue = asyncio.Queue()

        def emit(event_type: str, data: dict):
            events.put_nowait({"type": event_type, **data})

        async def _run():
            await self.run(prompt, project_name=project_name, emit=emit)
            events.put_nowait({"type": "__done__"})

        task = asyncio.create_task(_run())
        while True:
            event = await events.get()
            if event.get("type") == "__done__":
                break
            yield event
        await task

    def list_projects(self) -> list[dict]:
        projects = []
        for d in sorted(WORKSPACE_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                files = list(d.rglob("*"))
                projects.append({
                    "name": d.name,
                    "files": len([f for f in files if f.is_file()]),
                    "path": str(d.relative_to(WORKSPACE_DIR)),
                })
        return projects


def _slugify(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_-]", "_", text.lower().strip())[:40].strip("_") or "project"
