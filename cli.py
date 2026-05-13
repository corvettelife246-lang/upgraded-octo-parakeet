#!/usr/bin/env python3
"""
AI Multi-Agent Admin — CLI management tool.

Usage:
  python cli.py start              Start the server
  python cli.py status             Show backend + server status
  python cli.py models             List available models
  python cli.py switch <model>     Hot-swap the active Foundry Local model
  python cli.py chat <message>     Send a one-shot message from the terminal
  python cli.py sessions           List saved sessions
  python cli.py backend            Show active LLM backend details
"""
import asyncio
import json
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import httpx

BASE_URL = "http://localhost:8000"


def _get(path: str) -> dict:
    with httpx.Client(timeout=10) as client:
        r = client.get(f"{BASE_URL}{path}")
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict) -> dict:
    with httpx.Client(timeout=60) as client:
        r = client.post(f"{BASE_URL}{path}", json=body)
        r.raise_for_status()
        return r.json()


def cmd_start():
    from config.settings import HOST, PORT, DEBUG
    print(f"Starting AI Multi-Agent Admin on {HOST}:{PORT} …")
    import uvicorn
    uvicorn.run("ui.app:app", host=HOST, port=PORT, reload=DEBUG, log_level="info")


def cmd_status():
    try:
        h = _get("/api/health")
        b = _get("/api/backend")
        print("─── Server ───────────────────────────────")
        print(f"  Status  : {h.get('status', '?')}")
        print(f"  Agents  : {', '.join(h.get('agents', []))}")
        print("─── LLM Backend ──────────────────────────")
        print(f"  Backend : {b.get('backend', '?')}")
        print(f"  Model   : {b.get('active_model', '?')}")
        if b.get('url'):
            print(f"  URL     : {b['url']}")
        print(f"  Status  : {b.get('status', '?')}")
    except httpx.ConnectError:
        print("✗  Server not running. Start with: python cli.py start")
        sys.exit(1)


def cmd_models():
    try:
        data = _get("/api/models")
        print(f"Backend : {data.get('backend', '?')}")
        print("─── Available Models ─────────────────────")
        models = data.get("models", [])
        for m in models:
            if isinstance(m, dict):
                print(f"  {m['id']:<40} {m.get('description','')}")
            else:
                print(f"  {m}")
        if data.get("note"):
            print(f"\n  Note: {data['note']}")
    except httpx.ConnectError:
        print("✗  Server not running.")
        sys.exit(1)


def cmd_switch(model: str):
    try:
        data = _post("/api/switch-model", {"model": model})
        print(f"✓  Switched to: {data.get('active_model', model)}")
    except httpx.HTTPStatusError as e:
        err = e.response.json().get("detail", str(e))
        print(f"✗  Switch failed: {err}")
        sys.exit(1)
    except httpx.ConnectError:
        print("✗  Server not running.")
        sys.exit(1)


def cmd_chat(message: str):
    try:
        data = _post("/api/chat", {"message": message, "agent": "auto", "history": []})
        agent = data.get("agent", "auto")
        response = data.get("response", "")
        print(f"[{agent}] {response}")
    except httpx.ConnectError:
        # Fallback: run directly without server
        print("Server not running — running agent directly…\n")
        from config.settings import LLM_BACKEND
        from core.backend_router import backend as get_backend
        llm = get_backend()
        async def _run():
            return await llm.complete_async([{"role": "user", "content": message}])
        result = asyncio.run(_run())
        print(result)


def cmd_sessions():
    try:
        data = _get("/api/sessions")
        sessions = data.get("sessions", [])
        if not sessions:
            print("No saved sessions.")
            return
        print("─── Saved Sessions ───────────────────────")
        for s in sessions:
            print(f"  {s['id']:<30} {s.get('turns', 0)} turns  {s.get('created','')[:19]}")
    except httpx.ConnectError:
        # Read from disk directly
        from config.settings import DATA_DIR
        sess_dir = DATA_DIR / "sessions"
        files = sorted(sess_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            print("No saved sessions.")
            return
        for f in files[:20]:
            try:
                d = json.loads(f.read_text())
                turns = len(d.get("messages", []))
                created = d.get("created", "")[:19]
                print(f"  {f.stem:<30} {turns} turns  {created}")
            except Exception:
                pass


def cmd_backend():
    try:
        data = _get("/api/backend")
        print(json.dumps(data, indent=2))
    except httpx.ConnectError:
        from config.settings import FOUNDRY_LOCAL_MODEL, FOUNDRY_LOCAL_URL, LLM_BACKEND
        print(f"LLM_BACKEND      : {LLM_BACKEND}")
        if LLM_BACKEND == "foundry":
            print(f"FOUNDRY_LOCAL_URL: {FOUNDRY_LOCAL_URL}")
            print(f"FOUNDRY_LOCAL_MODEL: {FOUNDRY_LOCAL_MODEL}")
        else:
            from config.settings import DEFAULT_MODEL
            print(f"DEFAULT_MODEL    : {DEFAULT_MODEL}")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0].lower()

    if cmd == "start":
        cmd_start()
    elif cmd == "status":
        cmd_status()
    elif cmd == "models":
        cmd_models()
    elif cmd == "switch":
        if len(args) < 2:
            print("Usage: python cli.py switch <model-name>")
            sys.exit(1)
        cmd_switch(args[1])
    elif cmd == "chat":
        if len(args) < 2:
            print("Usage: python cli.py chat <message>")
            sys.exit(1)
        cmd_chat(" ".join(args[1:]))
    elif cmd == "sessions":
        cmd_sessions()
    elif cmd == "backend":
        cmd_backend()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
