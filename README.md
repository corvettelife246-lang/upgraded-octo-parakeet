# AI Multi-Agent Admin

Autonomous AI platform with **DL, LLM, ML, Reasoning, Voice, and Vision** capabilities.
Built for **WSL-2** on Windows 10 x64 Home.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Web UI (Browser)                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Chat Popup  │  │ Video Popup  │  │    Dashboard          │  │
│  │  Text/Voice  │  │  Live Cam    │  │  Tasks / Status       │  │
│  │  Snapshot    │  │  Snapshot    │  │  Agent Monitor        │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────────────────┘  │
│         │WebSocket        │WebSocket                            │
└─────────┼─────────────────┼───────────────────────────────────-─┘
          │                 │
┌─────────▼─────────────────▼─────────────────────────────────────┐
│                   FastAPI Backend  (ui/app.py)                   │
│   REST /api/*    WebSocket /ws    WebSocket /ws/video            │
└───────┬──────────────┬───────────────────┬──────────────────────┘
        │              │                   │
┌───────▼────┐  ┌──────▼──────┐  ┌────────▼──────────────────────┐
│  Agent     │  │   Voice     │  │   Vision                      │
│  Manager   │  │  Pipeline   │  │   Camera + Image Processor    │
│  (router)  │  │  STT/TTS    │  │                               │
└───────┬────┘  └─────────────┘  └───────────────────────────────┘
        │
        ├── AdminAgent      (orchestration, planning)
        ├── CodeAgent       (write/debug/execute code)
        ├── ResearchAgent   (knowledge synthesis)
        ├── ReasoningAgent  (extended thinking, CoT)
        ├── MLAgent         (DL/ML model design)
        └── VisionAgent     (image/video analysis)
                │
        ┌───────▼───────────────────────────────┐
        │     Anthropic Claude API              │
        │  claude-opus-4-7 / claude-haiku-4-5   │
        │  Streaming · Caching · Tool Use       │
        │  Extended Thinking (Reasoning Agent)  │
        └───────────────────────────────────────┘
```

## Quick Start (WSL-2)

```bash
# 1. Clone
git clone https://github.com/corvettelife246-lang/upgraded-octo-parakeet.git
cd upgraded-octo-parakeet

# 2. Run setup (Ubuntu WSL-2)
chmod +x setup/install.sh
./setup/install.sh

# 3. Configure
cp .env.example .env
nano .env   # set ANTHROPIC_API_KEY

# 4. Start
source .venv/bin/activate
python main.py

# 5. Open browser on Windows
# http://localhost:8000
```

## Features

| Feature | Description |
|---------|-------------|
| **Multi-Agent Routing** | Auto-selects the best agent per request |
| **Voice to Voice** | Mic → Whisper STT → Claude → Edge-TTS → Audio |
| **Text to Voice** | Any text response can be played as audio |
| **Voice to Text** | Real-time transcription via Whisper |
| **Text to Text** | Full LLM chat with streaming |
| **Snapshot Analysis** | Browser webcam capture → Claude Vision |
| **Live Video Feed** | Real-time MJPEG stream in popup |
| **Code Execution** | Write and run Python in a sandbox |
| **Extended Reasoning** | Claude's chain-of-thought thinking mode |
| **Draggable Popups** | Repositionable chat & video windows |
| **Dark/Light Theme** | Toggle with 🌙 button |

## Agents

| Agent | Model | Role |
|-------|-------|------|
| **Admin** | Opus 4.7 | Orchestration, planning, autonomous decisions |
| **Code** | Opus 4.7 | Write, review, debug, execute code |
| **Research** | Opus 4.7 | Knowledge synthesis, summarization |
| **Reasoning** | Opus 4.7 | Extended thinking, complex problem solving |
| **ML** | Opus 4.7 | Neural nets, training pipelines, data analysis |
| **Vision** | Opus 4.7 | Image/video understanding, OCR |

## API Reference

```
POST /api/chat              Text chat (agent auto-selected or specified)
POST /api/chat/stream       SSE streaming chat
POST /api/voice             Audio upload → transcript + audio response
POST /api/snapshot          Image upload → vision analysis
POST /api/code/execute      Python code sandbox execution
GET  /api/tasks             List all agent tasks
GET  /api/tasks/{id}        Task details + thinking trace
GET  /api/camera/snapshot   JPEG snapshot from server camera
WS   /ws                    Bidirectional real-time chat
WS   /ws/video              Live camera frames (base64 MJPEG)
```

## WSL-2 Notes

- **Audio**: See `setup/wsl2_audio_video.md` for PulseAudio setup
- **Camera**: Use USB/IP passthrough OR the browser webcam (recommended)
- **Browser webcam** works with no WSL camera config needed
- The web UI runs on `http://localhost:8000` — accessible from Windows browser

## Environment Variables

See `.env.example` for the full list. Required: `ANTHROPIC_API_KEY`.
