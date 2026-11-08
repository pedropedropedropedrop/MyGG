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
TTS_ENGINE = os.getenv("TTS_ENGINE", "edge").lower()

PROJECT_ROOT = Path(__file__).parent
VOICE_SETUP = "To enable voice, run python -m pip install -r requirements/voice.txt and restart GG."


def can_transcribe() -> bool:
    return find_spec("faster_whisper") is not None


def can_synthesize(*, local_only=False) -> bool:
    if TTS_ENGINE == "chatterbox":
        return find_spec("chatterbox") is not None or (not local_only and edge_fallback())
    return not local_only and TTS_ENGINE == "edge" and find_spec("edge_tts") is not None


def edge_fallback() -> bool:
    return os.getenv("TTS_EDGE_FALLBACK", "").lower() == "true" and find_spec("edge_tts") is not None


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
    """Cloud speech or an optional local multilingual voice model."""

    def __init__(self):
        self._voice = TTS_VOICE
        self._model = None
        self._lock = threading.Lock()

    def _local_speech(self, text: str, language: str | None, path: Path):
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        import soundfile
        ref = os.path.expanduser(os.getenv("GG_VOICE_REF", "").strip())
        if ref and not Path(ref).is_file():
            raise ValueError("GG_VOICE_REF must point to your own local audio file.")
        with self._lock:
            if self._model is None:
                self._model = ChatterboxMultilingualTTS.from_pretrained(device=os.getenv("TTS_DEVICE", "cpu"))
            wave = self._model.generate(text, language_id=language or os.getenv("GG_VOICE_LANG", "en"),
                                        audio_prompt_path=ref or None,
                                        exaggeration=float(os.getenv("TTS_EXAGGERATION", "0.5")))
            soundfile.write(str(path), wave.squeeze(0).cpu().numpy(), self._model.sr, format="MP3")

    async def speak_async(self, text: str, language: str | None = None) -> str:
        import llm
        with tempfile.NamedTemporaryFile(prefix="gg_", suffix=".mp3", delete=False) as tmp:
            path = Path(tmp.name)
        try:
            if TTS_ENGINE == "chatterbox":
                try:
                    await asyncio.to_thread(self._local_speech, text, language, path)
                    return str(path)
                except Exception:
                    if llm.uses_local(llm.tool_caller.get()) or not edge_fallback():
                        raise
                    logger.warning("Local speech failed. Using the configured Edge fallback.")
            if not llm.uses_local(llm.tool_caller.get()) and (TTS_ENGINE == "edge" or edge_fallback()):
                import edge_tts
                communicate = edge_tts.Communicate(text, self._voice)
                await asyncio.wait_for(communicate.save(str(path)), timeout=30)
            else:
                raise ValueError("Select Chatterbox for local voice, or use Edge in Claude mode.")
        except (Exception, asyncio.CancelledError):
            path.unlink(missing_ok=True)
            raise
        return str(path)

    def speak(self, text: str) -> str:
        """Sync wrapper for speak_async. Safe to call from threads."""
        return asyncio.run(self.speak_async(text))


synthesizer = Synthesizer()
