"""Central configuration — reads from environment with sensible defaults."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Anthropic
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "claude-opus-4-7")
REASONING_MODEL: str = os.getenv("REASONING_MODEL", "claude-opus-4-7")
FAST_MODEL: str = os.getenv("FAST_MODEL", "claude-haiku-4-5-20251001")

# Server
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")

# Voice
WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "base")
TTS_ENGINE: str = os.getenv("TTS_ENGINE", "edge")          # "edge" | "pyttsx3" | "gtts"
TTS_VOICE: str = os.getenv("TTS_VOICE", "en-US-AriaNeural")
AUDIO_SAMPLE_RATE: int = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
AUDIO_CHUNK_SIZE: int = int(os.getenv("AUDIO_CHUNK_SIZE", "1024"))

# Vision
CAMERA_INDEX: int = int(os.getenv("CAMERA_INDEX", "0"))
SNAPSHOT_DIR: Path = BASE_DIR / "data" / "snapshots"
VIDEO_FPS: int = int(os.getenv("VIDEO_FPS", "30"))
VIDEO_WIDTH: int = int(os.getenv("VIDEO_WIDTH", "1280"))
VIDEO_HEIGHT: int = int(os.getenv("VIDEO_HEIGHT", "720"))

# Agents
MAX_AGENTS: int = int(os.getenv("MAX_AGENTS", "10"))
AGENT_TIMEOUT: int = int(os.getenv("AGENT_TIMEOUT", "120"))
MAX_REASONING_TOKENS: int = int(os.getenv("MAX_REASONING_TOKENS", "8000"))

# Storage
DATA_DIR: Path = BASE_DIR / "data"
LOG_DIR: Path = BASE_DIR / "logs"
CODE_OUTPUT_DIR: Path = BASE_DIR / "data" / "code_output"

for _d in (DATA_DIR, LOG_DIR, CODE_OUTPUT_DIR, SNAPSHOT_DIR):
    _d.mkdir(parents=True, exist_ok=True)
