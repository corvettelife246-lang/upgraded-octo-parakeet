"""Text-to-speech supporting edge-tts (neural), pyttsx3 (offline), and gTTS."""
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

from config.settings import TTS_ENGINE, TTS_VOICE

logger = logging.getLogger(__name__)


class TextToSpeech:
    def __init__(self, engine: Optional[str] = None, voice: Optional[str] = None) -> None:
        self.engine = engine or TTS_ENGINE
        self.voice = voice or TTS_VOICE

    async def synthesize(self, text: str) -> bytes:
        """Return raw audio bytes (MP3 or WAV depending on engine)."""
        if self.engine == "edge":
            return await self._edge_tts(text)
        elif self.engine == "pyttsx3":
            return await asyncio.to_thread(self._pyttsx3_tts, text)
        elif self.engine == "gtts":
            return await asyncio.to_thread(self._gtts_tts, text)
        else:
            raise ValueError(f"Unknown TTS engine: {self.engine}")

    async def save(self, text: str, output_path: str | Path) -> Path:
        audio_bytes = await self.synthesize(text)
        path = Path(output_path)
        path.write_bytes(audio_bytes)
        return path

    # ------------------------------------------------------------------
    # Backends
    # ------------------------------------------------------------------
    async def _edge_tts(self, text: str) -> bytes:
        try:
            import edge_tts
        except ImportError:
            logger.error("edge-tts not installed. Run: pip install edge-tts")
            raise
        communicate = edge_tts.Communicate(text, self.voice)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    def _pyttsx3_tts(self, text: str) -> bytes:
        try:
            import pyttsx3
        except ImportError:
            logger.error("pyttsx3 not installed. Run: pip install pyttsx3")
            raise
        engine = pyttsx3.init()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            engine.save_to_file(text, tmp_path)
            engine.runAndWait()
            return Path(tmp_path).read_bytes()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _gtts_tts(self, text: str) -> bytes:
        try:
            from gtts import gTTS
        except ImportError:
            logger.error("gTTS not installed. Run: pip install gtts")
            raise
        import io
        buf = io.BytesIO()
        gTTS(text=text, lang="en").write_to_fp(buf)
        return buf.getvalue()
