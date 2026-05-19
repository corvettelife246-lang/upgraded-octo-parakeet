"""End-to-end voice pipeline: mic → STT → LLM → TTS → speaker."""
import asyncio
import logging
from typing import AsyncIterator, Callable, Optional

from voice.speech_to_text import SpeechToText
from voice.text_to_speech import TextToSpeech

logger = logging.getLogger(__name__)


class VoicePipeline:
    """
    Orchestrates the full voice interaction loop:
      audio_bytes → transcription → LLM response → synthesized_audio
    """

    def __init__(self) -> None:
        self.stt = SpeechToText()
        self.tts = TextToSpeech()

    async def voice_to_text(self, audio_bytes: bytes, extension: str = ".wav") -> str:
        """Transcribe incoming audio bytes to text."""
        return await self.stt.transcribe_bytes_async(audio_bytes, extension)

    async def text_to_voice(self, text: str) -> bytes:
        """Synthesize text to audio bytes."""
        return await self.tts.synthesize(text)

    async def voice_to_voice(
        self,
        audio_bytes: bytes,
        llm_fn: Callable[[str], asyncio.coroutines],
        audio_ext: str = ".wav",
    ) -> tuple[str, str, bytes]:
        """
        Full round-trip:
          audio_bytes → transcript → LLM → response_text → audio_response

        Returns (transcript, response_text, audio_response_bytes)
        """
        transcript = await self.voice_to_text(audio_bytes, audio_ext)
        logger.debug("Transcript: %s", transcript)
        response_text = await llm_fn(transcript)
        logger.debug("LLM response: %s", response_text[:80])
        audio_response = await self.text_to_voice(response_text)
        return transcript, response_text, audio_response

    async def stream_voice_response(
        self,
        audio_bytes: bytes,
        llm_stream_fn: Callable[[str], AsyncIterator[str]],
        audio_ext: str = ".wav",
    ) -> AsyncIterator[tuple[str, bytes]]:
        """
        Streaming version — yields (text_chunk, audio_bytes) pairs as the LLM streams.
        Sentences are buffered and synthesized incrementally for lower latency.
        """
        transcript = await self.voice_to_text(audio_bytes, audio_ext)
        buffer = ""
        async for chunk in llm_stream_fn(transcript):
            buffer += chunk
            if any(buffer.endswith(p) for p in (".", "!", "?", "\n")):
                sentence = buffer.strip()
                if sentence:
                    audio = await self.text_to_voice(sentence)
                    yield sentence, audio
                buffer = ""
        if buffer.strip():
            audio = await self.text_to_voice(buffer.strip())
            yield buffer.strip(), audio
