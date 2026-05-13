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

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from agents.admin_agent import AdminAgent
from agents.code_agent import CodeAgent
from agents.ml_agent import MLAgent
from agents.planner_agent import PlannerAgent as AutonomousPlannerAgent
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


async def _run_scheduled_task(prompt: str, agent: str) -> str:
    """Callback used by the scheduler to run an agent task."""
    if agent in _agents:
        return await _agents[agent].run(prompt, context={})
    return await agent_manager.route(prompt, context={})


@asynccontextmanager
async def lifespan(app: FastAPI):
    from core.health_check import print_banner
    from core.scheduler import get_scheduler
    await print_banner()
    sched = get_scheduler()
    sched.set_agent_fn(_run_scheduled_task)
    sched.start()
    yield
    sched.shutdown()

# ------------------------------------------------------------------
# App bootstrap
# ------------------------------------------------------------------
app = FastAPI(
    title="AI Multi-Agent Admin",
    description="Autonomous AI platform with DL, LLM, ML, Reasoning, Voice, and Vision",
    version="1.0.0",
    lifespan=lifespan,
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
_planner = AutonomousPlannerAgent(agent_manager.llm)
_agents = {
    "admin":    AdminAgent(agent_manager.llm),
    "code":     CodeAgent(agent_manager.llm),
    "research": ResearchAgent(agent_manager.llm),
    "reasoning":ReasoningAgent(agent_manager.llm),
    "ml":       MLAgent(agent_manager.llm),
    "vision":   VisionAgent(agent_manager.llm),
    "project":  ProjectAgent(agent_manager.llm),
    "planner":  _planner,
}
# Allow planner to delegate to all other agents
_planner.set_agents(_agents)

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
    from core.code_runner import run_code
    from core.tools import _safe_path
    result = await run_code(req.code, language=req.language, timeout=30)
    # Record artifact if a filename was given
    if req.filename and result.get("ok"):
        from core.artifacts import get_artifacts
        get_artifacts().record(req.filename, agent="code", tags=["executed"])
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
# Real-time system monitor WebSocket
# ------------------------------------------------------------------
@app.websocket("/ws/monitor")
async def websocket_monitor(ws: WebSocket):
    """Stream system metrics (CPU/RAM/disk/GPU/net) every 2 s."""
    await ws.accept()
    try:
        from core.monitor import metrics_stream
        async for metrics in metrics_stream(interval=2.0):
            await ws.send_text(json.dumps(metrics, default=str))
    except WebSocketDisconnect:
        pass


# ------------------------------------------------------------------
# Scheduler API
# ------------------------------------------------------------------
class SchedulerJobRequest(BaseModel):
    name: str
    agent: str
    prompt: str
    trigger: str           # "interval" | "cron" | "date"
    trigger_args: dict


@app.get("/api/scheduler/jobs")
async def scheduler_list_jobs():
    from core.scheduler import get_scheduler
    return {"jobs": get_scheduler().list_jobs()}


@app.post("/api/scheduler/jobs")
async def scheduler_add_job(req: SchedulerJobRequest):
    from core.scheduler import get_scheduler
    job = get_scheduler().add_job(
        name=req.name, agent=req.agent, prompt=req.prompt,
        trigger=req.trigger, trigger_args=req.trigger_args,
    )
    return job.to_dict()


@app.delete("/api/scheduler/jobs/{job_id}")
async def scheduler_remove_job(job_id: str):
    from core.scheduler import get_scheduler
    ok = get_scheduler().remove_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "removed"}


@app.post("/api/scheduler/jobs/{job_id}/toggle")
async def scheduler_toggle_job(job_id: str):
    from core.scheduler import get_scheduler
    enabled = get_scheduler().toggle_job(job_id)
    if enabled is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "enabled": enabled}


# ------------------------------------------------------------------
# Git API
# ------------------------------------------------------------------
class GitCommitRequest(BaseModel):
    message: str


class GitPushRequest(BaseModel):
    remote: str = "origin"
    branch: str = ""


class GitBranchRequest(BaseModel):
    name: str
    checkout: bool = True


class GitInitRequest(BaseModel):
    initial_branch: str = "main"


class GitRemoteRequest(BaseModel):
    name: str
    url: str


class GitAddRequest(BaseModel):
    paths: Optional[list[str]] = None


@app.post("/api/git/{project}/init")
async def git_init_ep(project: str, req: GitInitRequest):
    from core.git_ops import git_init
    return await git_init(project, initial_branch=req.initial_branch)


@app.get("/api/git/{project}/summary")
async def git_summary_ep(project: str):
    from core.git_ops import git_summary
    return await git_summary(project)


@app.get("/api/git/{project}/status")
async def git_status_ep(project: str):
    from core.git_ops import git_status
    return await git_status(project)


@app.get("/api/git/{project}/diff")
async def git_diff_ep(project: str, staged: bool = False):
    from core.git_ops import git_diff
    return await git_diff(project, staged=staged)


@app.get("/api/git/{project}/log")
async def git_log_ep(project: str, n: int = 10):
    from core.git_ops import git_log
    return await git_log(project, n=n)


@app.post("/api/git/{project}/add")
async def git_add_ep(project: str, req: GitAddRequest):
    from core.git_ops import git_add
    return await git_add(project, paths=req.paths)


@app.post("/api/git/{project}/commit")
async def git_commit_ep(project: str, req: GitCommitRequest):
    from core.git_ops import git_commit
    return await git_commit(project, req.message)


@app.post("/api/git/{project}/push")
async def git_push_ep(project: str, req: GitPushRequest):
    from core.git_ops import git_push
    return await git_push(project, remote=req.remote, branch=req.branch)


@app.post("/api/git/{project}/branch")
async def git_branch_ep(project: str, req: GitBranchRequest):
    from core.git_ops import git_branch
    return await git_branch(project, name=req.name, checkout=req.checkout)


@app.post("/api/git/{project}/remote")
async def git_remote_ep(project: str, req: GitRemoteRequest):
    from core.git_ops import git_remote_add
    return await git_remote_add(project, name=req.name, url=req.url)


# ------------------------------------------------------------------
# Autonomous Planner WebSocket
# ------------------------------------------------------------------
@app.websocket("/ws/planner")
async def websocket_planner(ws: WebSocket):
    """
    Autonomous goal planner — decomposes a goal into agent tasks and executes them.
    Client sends: {"goal": "Build a REST API with authentication"}
    Server streams: plan_start → planning → plan_ready → task_start → task_done → plan_complete
    """
    await ws.accept()
    try:
        raw  = await ws.receive_text()
        data = json.loads(raw)
        goal = data.get("goal", "").strip()
        if not goal:
            await ws.send_text(json.dumps({"type": "error", "message": "goal required"}))
            return

        planner = _agents.get("planner")
        if not planner:
            await ws.send_text(json.dumps({"type": "error", "message": "planner not registered"}))
            return

        async for event in planner.execute_stream(goal):
            await ws.send_text(json.dumps(event, default=str))

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Planner WS error")
        try:
            await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
        except Exception:
            pass


# ------------------------------------------------------------------
# Knowledge Base (RAG) API
# ------------------------------------------------------------------
class KBAddRequest(BaseModel):
    text: str
    title: str = ""
    source: str = ""
    tags: list[str] = []
    chunk_size: int = 0


@app.post("/api/kb")
async def kb_add(req: KBAddRequest):
    from core.knowledge_base import get_kb
    ids = get_kb().add(
        req.text, title=req.title, source=req.source,
        tags=req.tags, chunk_size=req.chunk_size or 0,
    )
    return {"ids": ids, "count": len(ids)}


@app.get("/api/kb")
async def kb_list():
    from core.knowledge_base import get_kb
    kb = get_kb()
    return {"entries": kb.list_all(), "count": kb.count()}


@app.get("/api/kb/search")
async def kb_search(q: str, top_k: int = 5):
    from core.knowledge_base import get_kb
    return {"results": await get_kb().search(q, top_k=top_k)}


@app.delete("/api/kb/{entry_id}")
async def kb_delete(entry_id: str):
    from core.knowledge_base import get_kb
    ok = get_kb().delete(entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


@app.post("/api/kb/ingest")
async def kb_ingest(
    file: UploadFile = File(...),
    chunk_size: int = Form(800),
    title: str = Form(""),
):
    from core.document_reader import read_document_bytes
    from core.knowledge_base import get_kb
    raw  = await file.read()
    text = await read_document_bytes(raw, filename=file.filename or "")
    doc_title = title or file.filename or "document"
    ids  = get_kb().add(
        text, title=doc_title, source="upload",
        tags=["document"], chunk_size=chunk_size,
    )
    return {"filename": file.filename, "chars": len(text), "chunks": len(ids)}


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
# Memory API
# ------------------------------------------------------------------
class MemoryAddRequest(BaseModel):
    text: str
    tags: list[str] = []
    source: str = "user"


@app.post("/api/memory")
async def memory_add(req: MemoryAddRequest):
    from core.memory import get_memory
    rec = await get_memory().add(req.text, tags=req.tags, source=req.source)
    return {"id": rec.id, "text": rec.text}


@app.get("/api/memory")
async def memory_list():
    from core.memory import get_memory
    return {"memories": get_memory().list_all(), "count": get_memory().count()}


@app.get("/api/memory/search")
async def memory_search(q: str, top_k: int = 5):
    from core.memory import get_memory
    return {"results": await get_memory().search(q, top_k=top_k)}


@app.delete("/api/memory/{memory_id}")
async def memory_delete(memory_id: str):
    from core.memory import get_memory
    ok = get_memory().delete(memory_id)
    return {"ok": ok}


# ------------------------------------------------------------------
# Web search API
# ------------------------------------------------------------------
@app.get("/api/search")
async def search_web(q: str, max_results: int = 6):
    from core.search import web_search
    results = await web_search(q, max_results=max_results)
    return {"query": q, "results": results}


# ------------------------------------------------------------------
# Document ingestion
# ------------------------------------------------------------------
@app.post("/api/ingest")
async def ingest_document(
    file: UploadFile = File(...),
    save_to_workspace: bool = Form(False),
    save_to_memory: bool   = Form(False),
):
    from core.document_reader import read_document_bytes
    raw  = await file.read()
    text = await read_document_bytes(raw, filename=file.filename or "")
    result: dict = {"filename": file.filename, "chars": len(text), "preview": text[:400]}

    if save_to_workspace:
        from core.tools import _safe_path
        dest = _safe_path(file.filename or "document.txt")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        result["workspace_path"] = str(dest.relative_to(WORKSPACE_DIR))

    if save_to_memory:
        from core.memory import get_memory
        chunks = [text[i:i+800] for i in range(0, min(len(text), 8000), 800)]
        for chunk in chunks:
            await get_memory().add(chunk, tags=["document", file.filename or ""], source="ingest")
        result["memory_chunks"] = len(chunks)

    result["content"] = text[:60000]
    return result


# ------------------------------------------------------------------
# Export conversation
# ------------------------------------------------------------------
class ExportRequest(BaseModel):
    messages: list[dict]
    format: str = "markdown"     # "markdown" | "html" | "json"
    title: str = "AI Chat Export"


@app.post("/api/export")
async def export_conversation(req: ExportRequest):
    from datetime import datetime
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if req.format == "json":
        content  = json.dumps({"title": req.title, "exported": ts, "messages": req.messages}, indent=2)
        media    = "application/json"
        filename = "chat_export.json"

    elif req.format == "html":
        rows = []
        for m in req.messages:
            role  = m.get("role", "user")
            text  = m.get("content", "").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            color = "#1a1e29" if role == "user" else "#13161e"
            rows.append(f'<div style="background:{color};padding:12px;margin:6px 0;border-radius:8px"><strong>{role}</strong><br>{text}</div>')
        content  = f"<!DOCTYPE html><html><head><meta charset=utf-8><title>{req.title}</title></head><body style='font-family:sans-serif;background:#0d0f14;color:#e4e6f0;padding:20px'><h2>{req.title}</h2><p>{ts}</p>{''.join(rows)}</body></html>"
        media    = "text/html"
        filename = "chat_export.html"

    else:  # markdown
        lines = [f"# {req.title}", f"*{ts}*", ""]
        for m in req.messages:
            role  = m.get("role", "user")
            text  = m.get("content", "")
            lines += [f"**{role.capitalize()}**", "", text, ""]
        content  = "\n".join(lines)
        media    = "text/markdown"
        filename = "chat_export.md"

    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ------------------------------------------------------------------
# Streaming code execution WebSocket
# ------------------------------------------------------------------
@app.websocket("/ws/code")
async def websocket_code(ws: WebSocket):
    """
    Stream code execution output line by line.
    Client sends: {"code": "print('hi')", "language": "python", "timeout": 30}
    Server streams: {type: output|error|exit, text|code, duration_ms}
    """
    await ws.accept()
    try:
        raw  = await ws.receive_text()
        data = json.loads(raw)
        code     = data.get("code", "")
        language = data.get("language", "python")
        timeout  = int(data.get("timeout", 30))

        if not code:
            await ws.send_text(json.dumps({"type": "error", "text": "code required"}))
            return

        from core.code_runner import stream_code
        await ws.send_text(json.dumps({"type": "start", "language": language}))
        async for event in stream_code(code, language=language, timeout=timeout):
            await ws.send_text(json.dumps(event))

        # Auto-record if code wrote files (simple heuristic: scan after execution)
        from core.artifacts import get_artifacts
        get_artifacts().scan_workspace(agent="code")

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Code WS error")
        try:
            await ws.send_text(json.dumps({"type": "error", "text": str(exc)}))
        except Exception:
            pass


# ------------------------------------------------------------------
# Artifacts API
# ------------------------------------------------------------------
@app.get("/api/artifacts")
async def artifacts_list(agent: str = "", file_type: str = ""):
    from core.artifacts import get_artifacts
    store = get_artifacts()
    return {
        "artifacts": store.list_all(agent=agent, file_type=file_type),
        "stats":     store.stats(),
    }


@app.post("/api/artifacts/scan")
async def artifacts_scan():
    from core.artifacts import get_artifacts
    added = get_artifacts().scan_workspace(agent="scan")
    return {"added": added}


@app.delete("/api/artifacts/{artifact_id}")
async def artifact_delete(artifact_id: str, delete_file: bool = False):
    from core.artifacts import get_artifacts
    ok = get_artifacts().delete(artifact_id, delete_file=delete_file)
    if not ok:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"ok": True}


# ------------------------------------------------------------------
# Analytics API — dashboard stats
# ------------------------------------------------------------------
@app.get("/api/analytics")
async def analytics():
    from core.memory import get_memory
    from core.knowledge_base import get_kb
    from core.artifacts import get_artifacts
    from core.scheduler import get_scheduler

    tasks     = agent_manager.list_tasks()
    by_status: dict[str, int] = {}
    by_agent:  dict[str, int] = {}
    for t in tasks:
        by_status[t["status"]]     = by_status.get(t["status"], 0) + 1
        by_agent[t["agent_type"]]  = by_agent.get(t["agent_type"], 0) + 1

    art_stats = get_artifacts().stats()

    return {
        "tasks": {
            "total":     len(tasks),
            "by_status": by_status,
            "by_agent":  by_agent,
        },
        "memory":    {"count": get_memory().count()},
        "kb":        {"count": get_kb().count()},
        "artifacts": art_stats,
        "scheduler": {"jobs": len(get_scheduler().list_jobs())},
        "agents":    list(_agents.keys()),
    }


# ------------------------------------------------------------------
# Health check (detailed)
# ------------------------------------------------------------------
@app.get("/api/health/detailed")
async def health_detailed():
    from core.health_check import run_health_check
    return await run_health_check(verbose=False)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui.app:app", host=HOST, port=PORT, reload=True)
