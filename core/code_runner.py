"""
Multi-language code runner — execute code snippets with streaming output.

Supported languages (interpreter must be installed):
  python / python3  — python3
  javascript / js   — node
  bash / sh         — bash / sh
  ruby              — ruby
  go                — go run (requires Go toolchain)
  rust              — rustscript (pip install rustscript  or  cargo install rust-script)

run_code()    — one-shot execution, returns {ok, stdout, stderr, returncode, duration_ms}
stream_code() — async generator yielding {type: output|error|exit} events in real time
"""
import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# (interpreter_argv, file_extension)
_LANGS: dict[str, tuple[list[str], str]] = {
    "python":     (["python3"],        ".py"),
    "python3":    (["python3"],        ".py"),
    "javascript": (["node"],           ".js"),
    "js":         (["node"],           ".js"),
    "node":       (["node"],           ".js"),
    "typescript": (["npx", "ts-node"], ".ts"),
    "ts":         (["npx", "ts-node"], ".ts"),
    "bash":       (["bash"],           ".sh"),
    "sh":         (["sh"],             ".sh"),
    "shell":      (["bash"],           ".sh"),
    "ruby":       (["ruby"],           ".rb"),
    "rb":         (["ruby"],           ".rb"),
    "go":         (["go", "run"],      ".go"),
    "rust":       (["rust-script"],    ".rs"),
}

_MAX_OUTPUT = 40_000   # chars before truncation
_ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}


def _resolve(language: str) -> Optional[tuple[list[str], str]]:
    return _LANGS.get(language.lower().strip())


async def run_code(
    code: str,
    language: str = "python",
    timeout: int = 30,
    cwd: Optional[Path] = None,
) -> dict:
    """Execute code; return {ok, stdout, stderr, returncode, duration_ms, language}."""
    t0  = time.monotonic()
    res = _resolve(language)
    if not res:
        return {
            "ok": False, "stdout": "", "returncode": -1, "duration_ms": 0,
            "stderr": f"Unsupported language: {language}. "
                      f"Supported: {', '.join(_LANGS)}",
            "language": language,
        }

    cmd, ext = res
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False,
                                         mode="w", encoding="utf-8") as f:
            f.write(code)
            tmp = f.name

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, tmp,
                cwd=str(cwd) if cwd else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_ENV,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try: proc.kill()
            except Exception: pass
            return {
                "ok": False, "stdout": "", "returncode": -1, "language": language,
                "stderr": f"Timed out after {timeout}s",
                "duration_ms": int((time.monotonic() - t0) * 1000),
            }
        except FileNotFoundError:
            return {
                "ok": False, "stdout": "", "returncode": -1, "language": language,
                "stderr": f"Interpreter not found: {cmd[0]}",
                "duration_ms": int((time.monotonic() - t0) * 1000),
            }

        return {
            "ok":          proc.returncode == 0,
            "stdout":      stdout_b.decode(errors="replace")[:_MAX_OUTPUT],
            "stderr":      stderr_b.decode(errors="replace")[:_MAX_OUTPUT],
            "returncode":  proc.returncode,
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "language":    language,
        }
    finally:
        if tmp:
            try: os.unlink(tmp)
            except OSError: pass


async def stream_code(
    code: str,
    language: str = "python",
    timeout: int = 60,
    cwd: Optional[Path] = None,
) -> AsyncGenerator[dict, None]:
    """
    Yield streaming execution events:
      {"type": "output", "text": "..."}   — stdout/stderr line
      {"type": "error",  "text": "..."}   — runner-level error
      {"type": "exit",   "code": int, "duration_ms": int}
    stdout and stderr are merged so output appears in order.
    """
    t0  = time.monotonic()
    res = _resolve(language)
    if not res:
        yield {"type": "error", "text": f"Unsupported language: {language}"}
        yield {"type": "exit", "code": -1, "duration_ms": 0}
        return

    cmd, ext = res
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False,
                                         mode="w", encoding="utf-8") as f:
            f.write(code)
            tmp = f.name

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, tmp,
                cwd=str(cwd) if cwd else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,   # merge stderr → stdout
                env=_ENV,
            )
        except FileNotFoundError:
            yield {"type": "error", "text": f"Interpreter not found: {cmd[0]}. "
                                             "Install it or choose a different language."}
            yield {"type": "exit", "code": -1, "duration_ms": 0}
            return

        total   = 0
        expired = False
        while True:
            elapsed   = time.monotonic() - t0
            remaining = timeout - elapsed
            if remaining <= 0:
                expired = True
                break
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=min(remaining, 1.0)
                )
            except asyncio.TimeoutError:
                if proc.returncode is not None:
                    break
                continue

            if not line:          # EOF
                break

            text   = line.decode(errors="replace").rstrip()
            total += len(text) + 1
            if total > _MAX_OUTPUT:
                yield {"type": "error", "text": "[output limit reached — truncated]"}
                expired = True
                break
            yield {"type": "output", "text": text}

        if expired:
            yield {"type": "error", "text": f"⏱ Timed out after {timeout}s — process killed"}
            try: proc.kill()
            except Exception: pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try: proc.kill()
            except Exception: pass

        yield {
            "type":        "exit",
            "code":        proc.returncode if proc.returncode is not None else -1,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }

    finally:
        if tmp:
            try: os.unlink(tmp)
            except OSError: pass


def supported_languages() -> list[str]:
    """Return deduplicated list of supported language names."""
    seen, out = set(), []
    for k, (cmd, _) in _LANGS.items():
        key = cmd[0]
        if key not in seen:
            seen.add(key)
            out.append(k)
    return out
