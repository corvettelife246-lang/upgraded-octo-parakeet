"""
Git operations for workspace projects.

Provides programmatic git: init, status, diff, log, add, commit, push, branch.
All operations are scoped to the workspace directory.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional

from config.settings import WORKSPACE_DIR

logger = logging.getLogger(__name__)


async def _git(args: list[str], cwd: Path, timeout: int = 30) -> dict:
    """Run a git command and return {ok, stdout, stderr, returncode}."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "ok":         proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout":     stdout.decode(errors="replace").strip(),
            "stderr":     stderr.decode(errors="replace").strip(),
        }
    except asyncio.TimeoutError:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"git timed out ({timeout}s)"}
    except FileNotFoundError:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": "git not found — install with: apt install git"}


def _project_path(project: str) -> Path:
    p = (WORKSPACE_DIR / project).resolve()
    if not str(p).startswith(str(WORKSPACE_DIR.resolve())):
        raise PermissionError(f"Project '{project}' escapes workspace")
    return p


async def git_init(project: str, initial_branch: str = "main") -> dict:
    path = _project_path(project)
    path.mkdir(parents=True, exist_ok=True)
    r = await _git(["init", "-b", initial_branch], cwd=path)
    if r["ok"]:
        # Write a default .gitignore
        gi = path / ".gitignore"
        if not gi.exists():
            gi.write_text("__pycache__/\n*.pyc\n.env\n*.log\n.venv/\n")
        await _git(["config", "user.email", "ai-admin@local"], cwd=path)
        await _git(["config", "user.name",  "AI Admin"],       cwd=path)
    return r


async def git_status(project: str) -> dict:
    path = _project_path(project)
    r    = await _git(["status", "--short"], cwd=path)
    log  = await _git(["log", "--oneline", "-5"], cwd=path)
    r["log"]    = log["stdout"]
    r["branch"] = (await _git(["branch", "--show-current"], cwd=path))["stdout"]
    return r


async def git_diff(project: str, staged: bool = False) -> dict:
    path = _project_path(project)
    args = ["diff", "--stat"] + (["--cached"] if staged else [])
    return await _git(args, cwd=path)


async def git_log(project: str, n: int = 10) -> dict:
    path = _project_path(project)
    return await _git(["log", f"-{n}", "--oneline", "--graph", "--decorate"], cwd=path)


async def git_add(project: str, paths: list[str] = None) -> dict:
    p    = _project_path(project)
    args = ["add"] + (paths if paths else ["-A"])
    return await _git(args, cwd=p)


async def git_commit(project: str, message: str) -> dict:
    p = _project_path(project)
    await _git(["add", "-A"], cwd=p)
    return await _git(["commit", "-m", message], cwd=p)


async def git_push(project: str, remote: str = "origin", branch: str = "") -> dict:
    p    = _project_path(project)
    args = ["push", remote] + ([branch] if branch else [])
    return await _git(args, cwd=p)


async def git_branch(project: str, name: str, checkout: bool = True) -> dict:
    p    = _project_path(project)
    args = ["checkout", "-b", name] if checkout else ["branch", name]
    return await _git(args, cwd=p)


async def git_checkout(project: str, branch: str) -> dict:
    return await _git(["checkout", branch], cwd=_project_path(project))


async def git_remote_add(project: str, name: str, url: str) -> dict:
    return await _git(["remote", "add", name, url], cwd=_project_path(project))


async def git_summary(project: str) -> dict:
    """Full overview: branch, status, recent log, remotes."""
    path = _project_path(project)
    git_dir = path / ".git"
    if not git_dir.exists():
        return {"initialized": False, "project": project}
    branch   = (await _git(["branch", "--show-current"], cwd=path))["stdout"]
    status   = (await _git(["status", "--short"],        cwd=path))["stdout"]
    log      = (await _git(["log", "--oneline", "-5"],   cwd=path))["stdout"]
    remotes  = (await _git(["remote", "-v"],             cwd=path))["stdout"]
    return {
        "initialized": True,
        "project":  project,
        "branch":   branch,
        "status":   status,
        "log":      log,
        "remotes":  remotes,
    }
