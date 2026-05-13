"""
Agent tool definitions — file I/O, shell execution, directory listing, search.
All tools are safe-sandboxed to the workspace directory.
"""
import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from config.settings import WORKSPACE_DIR


# ── Safety guard ──────────────────────────────────────────────────────────────
def _safe_path(rel: str) -> Path:
    """Resolve a relative path inside WORKSPACE_DIR; raise if it escapes."""
    resolved = (WORKSPACE_DIR / rel).resolve()
    if not str(resolved).startswith(str(WORKSPACE_DIR.resolve())):
        raise PermissionError(f"Path '{rel}' escapes workspace")
    return resolved


# ── Tool implementations ───────────────────────────────────────────────────────
async def tool_file_read(path: str) -> dict:
    try:
        p = _safe_path(path)
        if not p.exists():
            return {"ok": False, "error": f"File not found: {path}"}
        content = p.read_text(errors="replace")
        return {"ok": True, "path": str(path), "content": content, "size": len(content)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def tool_file_write(path: str, content: str) -> dict:
    try:
        p = _safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return {"ok": True, "path": str(path), "bytes_written": len(content)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def tool_file_delete(path: str) -> dict:
    try:
        p = _safe_path(path)
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)
        return {"ok": True, "path": str(path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def tool_list_dir(path: str = ".") -> dict:
    try:
        p = _safe_path(path)
        if not p.exists():
            return {"ok": False, "error": f"Directory not found: {path}"}
        entries = []
        for item in sorted(p.iterdir()):
            entries.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            })
        return {"ok": True, "path": str(path), "entries": entries}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def tool_shell(command: str, cwd: str = ".", timeout: int = 60) -> dict:
    try:
        work_dir = _safe_path(cwd)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
        }
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def tool_search_files(pattern: str, path: str = ".") -> dict:
    try:
        base = _safe_path(path)
        matches = [
            str(f.relative_to(WORKSPACE_DIR))
            for f in base.rglob(pattern)
            if f.is_file()
        ]
        return {"ok": True, "pattern": pattern, "matches": matches[:100]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def tool_grep(text: str, path: str = ".", file_pattern: str = "*") -> dict:
    try:
        base = _safe_path(path)
        results = []
        for f in base.rglob(file_pattern):
            if not f.is_file():
                continue
            try:
                for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                    if text.lower() in line.lower():
                        results.append({"file": str(f.relative_to(WORKSPACE_DIR)), "line": i, "text": line.strip()})
            except Exception:
                pass
        return {"ok": True, "query": text, "results": results[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Tool registry for LLM tool-use ────────────────────────────────────────────
TOOL_DEFINITIONS = [
    {
        "name": "file_read",
        "description": "Read the contents of a file in the project workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative path within workspace"}},
            "required": ["path"],
        },
    },
    {
        "name": "file_write",
        "description": "Write or overwrite a file in the project workspace. Creates parent directories automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Relative file path"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "file_delete",
        "description": "Delete a file or directory from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files and subdirectories at a path in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
        },
    },
    {
        "name": "shell",
        "description": "Run a shell command in the workspace directory. Use for installing packages, running tests, building, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "cwd":     {"type": "string", "default": ".", "description": "Working directory within workspace"},
                "timeout": {"type": "integer", "default": 60},
            },
            "required": ["command"],
        },
    },
    {
        "name": "search_files",
        "description": "Find files matching a glob pattern in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path":    {"type": "string", "default": "."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": "Search for text inside files in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text":         {"type": "string"},
                "path":         {"type": "string", "default": "."},
                "file_pattern": {"type": "string", "default": "*"},
            },
            "required": ["text"],
        },
    },
]

# Dispatcher
_TOOL_FN_MAP = {
    "file_read":    tool_file_read,
    "file_write":   tool_file_write,
    "file_delete":  tool_file_delete,
    "list_dir":     tool_list_dir,
    "shell":        tool_shell,
    "search_files": tool_search_files,
    "grep":         tool_grep,
}


async def execute_tool(name: str, inputs: dict) -> Any:
    fn = _TOOL_FN_MAP.get(name)
    if fn is None:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    return await fn(**inputs)
