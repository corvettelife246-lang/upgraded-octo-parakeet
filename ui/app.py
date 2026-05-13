"""
FastAPI application — REST + WebSocket backend for the AI Multi-Agent Admin.

Endpoints:
  GET  /                    → serve frontend
  POST /api/chat            → single-turn text chat
  POST /api/voice           → audio upload → STT → LLM → TTS response
  POST /api/snapshot        → upload image → vision analysis
  POST /api/code/execute    → run Python code and return output
  GET  /api/tasks           → list agent tasks
  GET  /api/tasks/{id}      → task detail
  WS   /ws                  → real-time bidirectional streaming
  WS   /ws/video            → MJPEG-style video frame streaming
"""
import asyncio
import base64
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from agents.admin_agent import AdminAgent
from agents.code_agent import CodeAgent
from agents.ml_agent import MLAgent
from agents.project_agent import ProjectAgent
from agents.reasoning_agent import ReasoningAgent
from agents.research_agent import ResearchAgent
from agents.vision_agent import VisionAgent
from config.settings import BASE_DIR, FOUNDRY_LOCAL_MODEL, FOUNDRY_LOCAL_URL, HOST, LLM_BACKEND, PORT, SECRET_KEY, WORKSPACE_DIR
from core.agent_manager import AgentManager
from core.backend_router import backend as get_backend
from core.tools import execute_tool, TOOL_DEFINITIONS
from voice.voice_pipeline import VoicePipeline
from vision.camera import Camera
from vision.image_processor import ImageProcessor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# App bootstrap
# ------------------------------------------------------------------
app = FastAPI(
    title="AI Multi-Agent Admin",
    description="Autonomous AI platform with DL, LLM, ML, Reasoning, Voice, and Vision",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC = BASE_DIR / "ui" / "static"
_TEMPLATES_DIR = BASE_DIR / "ui" / "templates"
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Singletons
agent_manager = AgentManager()
voice_pipeline = VoicePipeline()
camera = Camera()
image_proc = ImageProcessor()

# Agent registry
_agents = {
    "admin":    AdminAgent(agent_manager.llm),
    "code":     CodeAgent(agent_manager.llm),
    "research": ResearchAgent(agent_manager.llm),
    "reasoning":ReasoningAgent(agent_manager.llm),
    "ml":       MLAgent(agent_manager.llm),
    "vision":   VisionAgent(agent_manager.llm),
    "project":  ProjectAgent(agent_manager.llm),
}

# WebSocket connection registry
_ws_connections: dict[str, WebSocket] = {}


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    agent: str = "auto"
    history: list[dict] = []
    image_b64: Optional[str] = None


class CodeExecuteRequest(BaseModel):
    code: str
    language: str = "python"
    filename: Optional[str] = None


# ------------------------------------------------------------------
# HTTP routes
# ------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/chat")
async def chat(req: ChatRequest):
    context = {"history": req.history}
    if req.image_b64:
        context["image_b64"] = req.image_b64

    if req.agent == "auto":
        response = await agent_manager.route(req.message, context=context)
    elif req.agent in _agents:
        response = await _agents[req.agent].run(req.message, context=context)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {req.agent}")

    return {"response": response, "agent": req.agent}


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    context = {"history": req.history}

    async def event_generator():
        async for chunk in agent_manager.route_stream(req.message, context=context):
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/voice")
async def voice_endpoint(
    audio: UploadFile = File(...),
    agent: str = Form("auto"),
):
    audio_bytes = await audio.read()
    ext = Path(audio.filename or "audio.wav").suffix or ".wav"

    transcript = await voice_pipeline.voice_to_text(audio_bytes, ext)
    context: dict = {}
    if agent == "auto":
        response_text = await agent_manager.route(transcript, context=context)
    else:
        response_text = await _agents.get(agent, _agents["admin"]).run(transcript, context=context)

    audio_response = await voice_pipeline.text_to_voice(response_text)
    audio_b64 = base64.b64encode(audio_response).decode("utf-8")

    return {
        "transcript": transcript,
        "response": response_text,
        "audio_b64": audio_b64,
        "agent": agent,
    }


@app.post("/api/snapshot")
async def snapshot_endpoint(
    image: Optional[UploadFile] = File(None),
    prompt: str = Form("Describe this image in detail."),
    use_camera: bool = Form(False),
):
    if use_camera:
        if not camera._cap or not camera._cap.isOpened():
            camera.open()
        image_b64 = camera.snapshot_b64()
        if not image_b64:
            raise HTTPException(status_code=503, detail="Camera not available")
    elif image:
        img_bytes = await image.read()
        image_b64 = image_proc.bytes_to_b64(img_bytes)
    else:
        raise HTTPException(status_code=400, detail="Provide an image file or set use_camera=true")

    response = await _agents["vision"].run(prompt, context={"image_b64": image_b64})
    return {"response": response, "image_b64": image_b64}


@app.post("/api/code/execute")
async def execute_code(req: CodeExecuteRequest):
    code_agent: CodeAgent = _agents["code"]  # type: ignore
    filename = req.filename or f"exec_{uuid.uuid4().hex[:8]}.py"
    result = await code_agent.save_and_execute(req.code, filename)
    return result


@app.get("/api/tasks")
async def list_tasks():
    return {"tasks": agent_manager.list_tasks()}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = agent_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task.task_id,
        "agent_type": task.agent_type,
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "thinking": task.thinking,
        "created_at": task.created_at.isoformat(),
    }


@app.get("/api/camera/snapshot")
async def camera_snapshot():
    if not camera._cap or not camera._cap.isOpened():
        camera.open()
    frame = camera.read_frame()
    if frame is None:
        raise HTTPException(status_code=503, detail="Camera not available")
    return Response(content=frame, media_type="image/jpeg")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "agents": list(_agents.keys()),
        "llm_backend": LLM_BACKEND,
    }


@app.get("/api/backend")
async def backend_info():
    """Return active LLM backend details and available models."""
    llm = get_backend()
    if LLM_BACKEND == "foundry":
        health = llm.health_check()
        return {
            "backend": "foundry_local",
            "url": FOUNDRY_LOCAL_URL,
            "active_model": FOUNDRY_LOCAL_MODEL,
            "available_models": health.get("models", []),
            "status": health.get("status"),
        }
    return {
        "backend": "anthropic",
        "active_model": "claude-opus-4-7",
        "available_models": [
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ],
        "status": "ok" if __import__("config.settings", fromlist=["ANTHROPIC_API_KEY"]).ANTHROPIC_API_KEY else "no_api_key",
    }


@app.get("/api/models")
async def list_models():
    """List locally available Foundry Local models (or Claude models if using Anthropic)."""
    llm = get_backend()
    if LLM_BACKEND == "foundry":
        models = llm.list_models()
        return {
            "backend": "foundry_local",
            "models": models,
            "note": "Run 'foundry model list' on Windows to see all downloaded models",
        }
    return {
        "backend": "anthropic",
        "models": [
            {"id": "claude-opus-4-7",          "description": "Most capable — reasoning, coding, analysis"},
            {"id": "claude-sonnet-4-6",         "description": "Balanced — fast and capable"},
            {"id": "claude-haiku-4-5-20251001", "description": "Fastest — lightweight tasks"},
        ],
    }


class SwitchModelRequest(BaseModel):
    model: str


@app.post("/api/switch-model")
async def switch_model(req: SwitchModelRequest):
    """Hot-swap the active model on the Foundry Local backend (no restart needed)."""
    import core.backend_router as br
    llm = br.backend()
    if LLM_BACKEND != "foundry":
        raise HTTPException(status_code=400, detail="Model switching only supported on Foundry Local backend")
    available = llm.list_models()
    if available and req.model not in available:
        raise HTTPException(status_code=404, detail=f"Model '{req.model}' not loaded. Available: {available}")
    llm._model = req.model
    # Also update agents
    for agent in _agents.values():
        agent.llm._model = req.model
    logger.info("Switched active model to: %s", req.model)
    return {"status": "ok", "active_model": req.model}


# ------------------------------------------------------------------
# Session history — persist/load chat history from disk
# ------------------------------------------------------------------
_HISTORY_DIR = BASE_DIR / "data" / "sessions"
_HISTORY_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/sessions")
async def list_sessions():
    sessions = []
    for f in sorted(_HISTORY_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        try:
            data = json.loads(f.read_text())
            sessions.append({"id": f.stem, "created": data.get("created"), "turns": len(data.get("messages", []))})
        except Exception:
            pass
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    path = _HISTORY_DIR / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    return json.loads(path.read_text())


@app.post("/api/sessions/{session_id}/save")
async def save_session(session_id: str, messages: list[dict]):
    from datetime import datetime
    path = _HISTORY_DIR / f"{session_id}.json"
    data = {"id": session_id, "created": datetime.utcnow().isoformat(), "messages": messages}
    path.write_text(json.dumps(data, indent=2))
    return {"status": "saved", "path": str(path)}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    path = _HISTORY_DIR / f"{session_id}.json"
    if path.exists():
        path.unlink()
    return {"status": "deleted"}


# ------------------------------------------------------------------
# WebSocket — bidirectional real-time chat
# ------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    await ws.accept()
    conn_id = str(uuid.uuid4())
    _ws_connections[conn_id] = ws
    logger.info("WS connected: %s", conn_id)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"error": "Invalid JSON"}))
                continue

            msg_type = data.get("type", "chat")
            payload = data.get("payload", {})

            if msg_type == "chat":
                await _handle_ws_chat(ws, payload)
            elif msg_type == "voice":
                await _handle_ws_voice(ws, payload)
            elif msg_type == "snapshot":
                await _handle_ws_snapshot(ws, payload)
            elif msg_type == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            else:
                await ws.send_text(json.dumps({"error": f"Unknown type: {msg_type}"}))

    except WebSocketDisconnect:
        logger.info("WS disconnected: %s", conn_id)
    finally:
        _ws_connections.pop(conn_id, None)


async def _handle_ws_chat(ws: WebSocket, payload: dict):
    message = payload.get("message", "")
    agent = payload.get("agent", "auto")
    history = payload.get("history", [])
    image_b64 = payload.get("image_b64")
    context = {"history": history}
    if image_b64:
        context["image_b64"] = image_b64

    await ws.send_text(json.dumps({"type": "start", "agent": agent}))
    full_response = []
    async for chunk in agent_manager.route_stream(message, context=context):
        full_response.append(chunk)
        await ws.send_text(json.dumps({"type": "chunk", "text": chunk}))
    await ws.send_text(json.dumps({"type": "done", "full_text": "".join(full_response)}))


async def _handle_ws_voice(ws: WebSocket, payload: dict):
    audio_b64 = payload.get("audio_b64", "")
    audio_bytes = base64.b64decode(audio_b64)
    audio_ext = payload.get("ext", ".wav")
    agent = payload.get("agent", "auto")

    transcript = await voice_pipeline.voice_to_text(audio_bytes, audio_ext)
    await ws.send_text(json.dumps({"type": "transcript", "text": transcript}))

    context: dict = {}
    if agent == "auto":
        response_text = await agent_manager.route(transcript, context=context)
    else:
        response_text = await _agents.get(agent, _agents["admin"]).run(transcript, context=context)

    audio_resp = await voice_pipeline.text_to_voice(response_text)
    await ws.send_text(json.dumps({
        "type": "voice_response",
        "text": response_text,
        "audio_b64": base64.b64encode(audio_resp).decode("utf-8"),
    }))


async def _handle_ws_snapshot(ws: WebSocket, payload: dict):
    image_b64 = payload.get("image_b64")
    prompt = payload.get("prompt", "Describe this image.")
    if not image_b64:
        if not camera._cap or not camera._cap.isOpened():
            camera.open()
        image_b64 = camera.snapshot_b64()
    if not image_b64:
        await ws.send_text(json.dumps({"error": "No image available"}))
        return
    response = await _agents["vision"].run(prompt, context={"image_b64": image_b64})
    await ws.send_text(json.dumps({"type": "snapshot_response", "text": response, "image_b64": image_b64}))


# ------------------------------------------------------------------
# Video streaming WebSocket (MJPEG-over-WS)
# ------------------------------------------------------------------
@app.websocket("/ws/video")
async def websocket_video(ws: WebSocket):
    await ws.accept()
    if not camera._cap or not camera._cap.isOpened():
        camera.open()
    try:
        async for frame_bytes in camera.stream_frames(fps=15):
            b64 = base64.b64encode(frame_bytes).decode("utf-8")
            await ws.send_text(json.dumps({"type": "frame", "data": b64}))
    except WebSocketDisconnect:
        pass


# ------------------------------------------------------------------
# Workspace — file tree, read, write, delete, download ZIP
# ------------------------------------------------------------------
class FileWriteRequest(BaseModel):
    path: str
    content: str


@app.get("/api/workspace/tree")
async def workspace_tree(path: str = "."):
    from core.tools import tool_list_dir
    result = await tool_list_dir(path)
    return result


@app.get("/api/workspace/read")
async def workspace_read(path: str):
    from core.tools import tool_file_read
    return await tool_file_read(path)


@app.post("/api/workspace/write")
async def workspace_write(req: FileWriteRequest):
    from core.tools import tool_file_write
    return await tool_file_write(req.path, req.content)


@app.delete("/api/workspace/delete")
async def workspace_delete(path: str):
    from core.tools import tool_file_delete
    return await tool_file_delete(path)


@app.post("/api/workspace/upload")
async def workspace_upload(
    file: UploadFile = File(...),
    dest_path: str = Form("."),
):
    from core.tools import _safe_path
    dest = _safe_path(dest_path) / file.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    return {"ok": True, "path": str(dest.relative_to(WORKSPACE_DIR))}


@app.get("/api/workspace/download/{project_name}")
async def workspace_download(project_name: str):
    """Download a workspace project as a ZIP archive."""
    import io
    import zipfile
    project_path = WORKSPACE_DIR / project_name
    if not project_path.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in project_path.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(WORKSPACE_DIR))
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project_name}.zip"'},
    )


@app.get("/api/workspace/projects")
async def list_projects():
    proj_agent: ProjectAgent = _agents["project"]  # type: ignore
    return {"projects": proj_agent.list_projects()}


# ------------------------------------------------------------------
# Project builder — stream build events over WebSocket
# ------------------------------------------------------------------
class BuildRequest(BaseModel):
    description: str
    project_name: Optional[str] = None


@app.post("/api/build")
async def build_project(req: BuildRequest):
    """One-shot project build (non-streaming). Returns final result."""
    proj_agent: ProjectAgent = _agents["project"]  # type: ignore
    result = await proj_agent.run(req.description, project_name=req.project_name)
    return {"result": result}


@app.websocket("/ws/build")
async def websocket_build(ws: WebSocket):
    """
    Real-time project build — stream tool calls and results as they happen.
    Client sends: {"description": "...", "project_name": "..."}
    Server streams: {"type": "tool_call"|"tool_result"|"step_done"|..., ...}
    """
    await ws.accept()
    try:
        raw  = await ws.receive_text()
        data = json.loads(raw)
        description  = data.get("description", "")
        project_name = data.get("project_name")

        if not description:
            await ws.send_text(json.dumps({"type": "error", "message": "description required"}))
            return

        proj_agent: ProjectAgent = _agents["project"]  # type: ignore
        async for event in proj_agent.build_stream(description, project_name=project_name):
            await ws.send_text(json.dumps(event, default=str))

        await ws.send_text(json.dumps({"type": "build_complete"}))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Build WS error")
        try:
            await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
        except Exception:
            pass


# ------------------------------------------------------------------
# Live terminal WebSocket — stream shell output in real-time
# ------------------------------------------------------------------
@app.websocket("/ws/terminal")
async def websocket_terminal(ws: WebSocket):
    """
    Interactive terminal streamed over WebSocket.
    Client sends: {"cmd": "ls -la", "cwd": "my_project"}
    Server streams stdout/stderr lines as they appear.
    """
    await ws.accept()
    try:
        while True:
            raw  = await ws.receive_text()
            data = json.loads(raw)
            cmd  = data.get("cmd", "").strip()
            cwd  = data.get("cwd", ".")
            if not cmd:
                continue

            from core.tools import _safe_path
            work_dir = _safe_path(cwd)

            await ws.send_text(json.dumps({"type": "cmd", "text": f"$ {cmd}"}))
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    cwd=str(work_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                async for line in proc.stdout:
                    await ws.send_text(json.dumps({"type": "output", "text": line.decode(errors="replace").rstrip()}))
                await proc.wait()
                await ws.send_text(json.dumps({"type": "exit", "code": proc.returncode}))
            except Exception as exc:
                await ws.send_text(json.dumps({"type": "error", "text": str(exc)}))
    except WebSocketDisconnect:
        pass


# ------------------------------------------------------------------
# File upload into chat (multi-part — returns text content for vision/analysis)
# ------------------------------------------------------------------
@app.post("/api/upload/chat")
async def upload_for_chat(file: UploadFile = File(...)):
    """Accept a file upload and return its content ready for chat context."""
    raw = await file.read()
    ext = Path(file.filename or "").suffix.lower()

    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        b64 = base64.b64encode(raw).decode("utf-8")
        return {"type": "image", "filename": file.filename, "b64": b64, "size": len(raw)}

    if ext in (".py", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml",
               ".md", ".txt", ".sh", ".rs", ".go", ".c", ".cpp", ".java"):
        text = raw.decode(errors="replace")
        return {"type": "text", "filename": file.filename, "content": text[:50000], "size": len(text)}

    # Try to decode anything else as text
    try:
        text = raw.decode(errors="replace")
        return {"type": "text", "filename": file.filename, "content": text[:50000]}
    except Exception:
        b64 = base64.b64encode(raw).decode("utf-8")
        return {"type": "binary", "filename": file.filename, "b64": b64, "size": len(raw)}


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui.app:app", host=HOST, port=PORT, reload=True)
