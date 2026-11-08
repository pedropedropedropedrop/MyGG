"""Audio processing — speech-to-text and text-to-speech for GG."""

import os
import asyncio
import logging
import threading
import tempfile
from importlib.util import find_spec
from pathlib import Path

logger = logging.getLogger(__name__)

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
TTS_VOICE = os.getenv("TTS_VOICE", "en-IE-EmilyNeural")

PROJECT_ROOT = Path(__file__).parent
VOICE_SETUP = "To enable voice, run python -m pip install -r requirements/voice.txt and restart GG."


def can_transcribe() -> bool:
    return find_spec("faster_whisper") is not None


def can_synthesize() -> bool:
    return find_spec("edge_tts") is not None


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
        """Return the temporary MP3 produced by Edge TTS."""
        import edge_tts

        with tempfile.NamedTemporaryFile(prefix="gg_", suffix=".mp3", delete=False) as tmp:
            path = Path(tmp.name)
        try:
            communicate = edge_tts.Communicate(text, self._voice)
            await asyncio.wait_for(communicate.save(str(path)), timeout=30)
        except (Exception, asyncio.CancelledError):
            path.unlink(missing_ok=True)
            raise
        return str(path)

    def speak(self, text: str) -> str:
        """Sync wrapper for speak_async. Safe to call from threads."""
        return asyncio.run(self.speak_async(text))


synthesizer = Synthesizer()
