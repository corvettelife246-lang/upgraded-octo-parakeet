"""Code agent — generation, review, debugging, and sandboxed execution."""
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from agents.base_agent import BaseAgent
from config.settings import CODE_OUTPUT_DIR, DEFAULT_MODEL

_SYSTEM = """You are the Code Agent — an expert software engineer.

Capabilities:
- Write complete, production-ready code in Python, JS/TS, Bash, C, C++, Rust, Go and more
- Review and critique existing code
- Debug errors and explain root causes
- Refactor for clarity, performance, and security
- Generate unit tests

Rules:
- Always output complete, runnable files — never truncate with "# ..."
- Include imports and all dependencies
- Add type annotations for Python
- Explain briefly what you built after the code block
- Flag security issues immediately
"""


class CodeAgent(BaseAgent):
    name = "Code Agent"
    description = "Writes, reviews, debugs, and executes code."

    def _system_prompt(self) -> str:
        return _SYSTEM

    async def run(self, prompt: str, context: Optional[dict] = None) -> str:
        history = (context or {}).get("history", [])
        messages = [*history, {"role": "user", "content": prompt}]
        return await self.llm.complete_async(
            messages, system=_SYSTEM, model=DEFAULT_MODEL, max_tokens=8192
        )

    async def execute_python(self, code: str, timeout: int = 30) -> dict:
        """Run Python code in a subprocess and return stdout/stderr."""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(code)
            script_path = f.name
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "stdout": stdout.decode(),
                "stderr": stderr.decode(),
                "returncode": proc.returncode,
            }
        except asyncio.TimeoutError:
            return {"stdout": "", "stderr": f"Execution timed out after {timeout}s", "returncode": -1}
        finally:
            Path(script_path).unlink(missing_ok=True)

    async def save_and_execute(self, code: str, filename: str) -> dict:
        out_path = CODE_OUTPUT_DIR / filename
        out_path.write_text(code)
        if filename.endswith(".py"):
            return await self.execute_python(code)
        return {"stdout": f"Saved to {out_path}", "stderr": "", "returncode": 0}
