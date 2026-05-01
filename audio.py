"""Audio processing — speech-to-text and text-to-speech for GG."""

import os
import asyncio
import logging
import threading
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
TTS_VOICE = os.getenv("TTS_VOICE", "en-IE-EmilyNeural")

PROJECT_ROOT = Path(__file__).parent


class Transcriber:
    """Speech-to-text using faster-whisper."""

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def preload(self):
        """Pre-load the Whisper model at startup (avoids CUDA loading mid-conversation)."""
        self._ensure_model()

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            logger.info(f"Loading Whisper model: {WHISPER_MODEL} on {WHISPER_DEVICE}")
            self._model = WhisperModel(
                WHISPER_MODEL,
                device=WHISPER_DEVICE,
                compute_type="float16" if WHISPER_DEVICE == "cuda" else "int8",
            )
            logger.info("Whisper model loaded")

    def transcribe(self, file_path: str) -> str:
        """Transcribe an audio file to text. Thread-safe via threading.Lock."""
        with self._lock:
            self._ensure_model()
            segments, info = self._model.transcribe(file_path, beam_size=5)
            text = " ".join(segment.text.strip() for segment in segments)
            logger.info(f"Transcribed {info.duration:.1f}s audio → {len(text)} chars")
            return text


transcriber = Transcriber()


class Synthesizer:
    """Text-to-speech using Microsoft Edge TTS."""

    def __init__(self):
        self._voice = TTS_VOICE
        logger.info(f"Edge TTS voice: {self._voice}")

    async def speak_async(self, text: str) -> str:
        """Generate speech from text. Returns path to temp OGG file."""
        import edge_tts

        ogg_tmp = tempfile.NamedTemporaryFile(
            prefix="gg_", suffix=".ogg", delete=False
        )
        ogg_tmp.close()

        communicate = edge_tts.Communicate(text, self._voice)
        # HANG-5 FIX: timeout so a stalled TTS server doesn't block forever
        await asyncio.wait_for(communicate.save(ogg_tmp.name), timeout=30)

        logger.info(f"Generated audio: {len(text)} chars → {ogg_tmp.name}")
        return ogg_tmp.name

    def speak(self, text: str) -> str:
        """Sync wrapper for speak_async. Safe to call from threads."""
        return asyncio.run(self.speak_async(text))


synthesizer = Synthesizer()
