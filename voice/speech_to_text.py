"""Speech-to-text using OpenAI Whisper (local, offline-capable)."""
import asyncio
import io
import logging
import tempfile
from pathlib import Path
from typing import Optional

from config.settings import AUDIO_SAMPLE_RATE, WHISPER_MODEL

logger = logging.getLogger(__name__)


class SpeechToText:
    def __init__(self) -> None:
        self._model = None
        self._model_name = WHISPER_MODEL

    def _load_model(self):
        if self._model is None:
            try:
                import whisper
                self._model = whisper.load_model(self._model_name)
                logger.info("Whisper model '%s' loaded", self._model_name)
            except ImportError:
                logger.error("openai-whisper not installed. Run: pip install openai-whisper")
                raise
        return self._model

    def transcribe_file(self, audio_path: str | Path, language: Optional[str] = None) -> str:
        model = self._load_model()
        opts = {"language": language} if language else {}
        result = model.transcribe(str(audio_path), **opts)
        return result["text"].strip()

    def transcribe_bytes(self, audio_bytes: bytes, extension: str = ".wav") -> str:
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        try:
            return self.transcribe_file(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    async def transcribe_bytes_async(self, audio_bytes: bytes, extension: str = ".wav") -> str:
        return await asyncio.to_thread(self.transcribe_bytes, audio_bytes, extension)

    async def transcribe_file_async(self, audio_path: str | Path) -> str:
        return await asyncio.to_thread(self.transcribe_file, audio_path)
