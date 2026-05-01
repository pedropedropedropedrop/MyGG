"""MyGG — personal AI assistant on Telegram. Bot entry point."""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import agent
import tools
import heartbeat
import whatsapp_server
import whatsapp_summarizer
import asyncio
import json as _json
import sys
import modules

ALLOWED_USER_IDS: set[int] = set()
raw = os.getenv("ALLOWED_USER_IDS", "")
if raw.strip():
    ALLOWED_USER_IDS = {int(x.strip()) for x in raw.split(",") if x.strip()}

OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))

from utils import split_long_message
from constants import VOICE_LONG_THRESHOLD_SECONDS, FILE_READ_MAX_CHARS


def _is_allowed(user_id: int) -> bool:
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


async def _send_long(update: Update, text: str):
    """Send a message, splitting if it exceeds Telegram's limit."""
    for chunk in split_long_message(text):
        await update.message.reply_text(chunk)


# ── Handlers ─────────────────────────────────────────────────────────────────


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        f"Hey {agent.USER_NAME}. I'm your AI assistant. Ask me anything, give me todos, "
        "check your calendar, or just dump ideas — I'll remember everything.\n\n"
        f"Your Telegram ID: {update.effective_user.id}"
    )


async def opus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    agent.model_overrides[chat_id] = agent.MODEL_OPUS
    await update.message.reply_text(f"Switched to {agent.MODEL_OPUS} for this chat.")


async def sonnet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    agent.model_overrides.pop(chat_id, None)
    await update.message.reply_text(f"Switched back to {agent.MODEL} (default).")


async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    current = agent.model_overrides.get(chat_id, agent.MODEL)
    is_override = chat_id in agent.model_overrides
    label = " (overridden)" if is_override else " (default)"
    await update.message.reply_text(f"Current model: {current}{label}")


async def _process_and_respond(update: Update, context: ContextTypes.DEFAULT_TYPE, user_content: str | list, force_opus: bool = False):
    """Shared handler: send message to agent, reply with text + optional audio."""
    chat_id = update.effective_chat.id

    # Keep typing indicator alive during long processing (bounded — 10 min max)
    async def _keep_typing():
        try:
            for _ in range(120):  # 120 * 5s = 600s = 10 min upper bound
                await update.effective_chat.send_action("typing")
                await asyncio.sleep(5)
            logger.warning("Typing indicator reached 10 min limit — stopping")
        except asyncio.CancelledError:
            pass  # Normal cancellation after response sent
        except Exception:
            logger.debug("Typing indicator error", exc_info=True)

    typing_task = asyncio.create_task(_keep_typing())
    try:
        response, audio_files = await agent.handle_message(chat_id, user_content, force_opus=force_opus)
        # Send text first (primary), then audio (supplementary)
        await _send_long(update, response)
        for audio_path in audio_files:
            try:
                with open(audio_path, 'rb') as f:
                    await context.bot.send_voice(chat_id, f)
            finally:
                try:
                    os.unlink(audio_path)
                except OSError:
                    logger.warning(f"Failed to clean up temp audio: {audio_path}")
    except Exception:
        logger.exception("Agent error")
        await update.message.reply_text("Something went wrong. Check the logs.")
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except (asyncio.CancelledError, Exception):
            pass


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    await update.effective_chat.send_action("typing")

    text = update.message.text
    force_opus = False
    if text.startswith("!!"):
        text = text[2:].strip()
        force_opus = True
        if not text:
            await update.message.reply_text("Send !! followed by your message to use Opus.")
            return

    await _process_and_respond(update, context, text, force_opus=force_opus)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming voice notes and audio files — transcribe and process."""
    if not _is_allowed(update.effective_user.id):
        return

    await update.effective_chat.send_action("typing")

    # Download audio file — voice notes are on message.voice, audio files on message.audio
    voice = update.message.voice or update.message.audio
    if not voice:
        await update.message.reply_text("Couldn't read that audio file.")
        return

    import tempfile
    tmp = tempfile.NamedTemporaryFile(prefix="gg_voice_", suffix=".ogg", delete=False)
    tmp.close()
    temp_path = tmp.name

    try:
        tg_file = await voice.get_file()
        await tg_file.download_to_drive(temp_path)

        duration = voice.duration or 0
        if duration > VOICE_LONG_THRESHOLD_SECONDS:
            # Long audio — route directly to meeting processing (not through Claude)
            await update.message.reply_text(
                f"Got your {duration}s audio recording. Transcribing and processing — this may take a minute or two."
            )
            try:
                from modules.meetings import _handle_process_meeting
                result = await _handle_process_meeting({
                    "file_path": temp_path,
                    "meeting_date": None,
                    "context_hint": update.message.caption or "",
                })
                await _send_long(update, result)
                # Let agent know about the meeting for follow-up questions
                await _process_and_respond(update, context,
                    f"[System: Meeting audio was just processed. Here are the results — present them to the user and ask the clarifying questions if any.]\n\n{result}")
            except Exception:
                logger.exception("Meeting processing failed")
                await update.message.reply_text("Meeting processing failed. Check the logs.")
        else:
            transcription = await tools.transcribe_audio(temp_path)
            wrapped = f"[Voice note ({duration}s)]: {transcription}"
            await _process_and_respond(update, context, wrapped)
    except Exception:
        logger.exception("Voice note processing failed")
        await update.message.reply_text(
            "I couldn't process your voice note — could you try again or type it out?"
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming photos/screenshots — send to Claude with vision."""
    if not _is_allowed(update.effective_user.id):
        return

    await update.effective_chat.send_action("typing")

    import base64
    import tempfile

    # Get the highest resolution photo
    photo = update.message.photo[-1]
    tg_file = await photo.get_file()

    tmp = tempfile.NamedTemporaryFile(prefix="gg_photo_", suffix=".jpg", delete=False)
    tmp.close()

    try:
        await tg_file.download_to_drive(tmp.name)
        with open(tmp.name, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        caption = update.message.caption or "What's in this image?"
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text", "text": caption},
        ]
        await _process_and_respond(update, context, content)
    except Exception:
        logger.exception("Photo processing failed")
        await update.message.reply_text("I couldn't process that image. Try again?")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


async def _handle_image_document(tmp_name, mime, caption, update, context):
    """Handle an image file sent as a document — use vision."""
    import base64
    with open(tmp_name, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")
    file_name = os.path.basename(tmp_name)
    media_type = mime if mime in ("image/jpeg", "image/png", "image/gif", "image/webp") else "image/jpeg"
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
        {"type": "text", "text": caption or f"What's in this image? (file: {file_name})"},
    ]
    await _process_and_respond(update, context, content)


async def _handle_pdf_document(tmp_name, file_name, caption, update, context):
    """Handle a PDF file — send directly to Claude via native document support."""
    import base64
    with open(tmp_name, "rb") as f:
        pdf_data = base64.standard_b64encode(f.read()).decode("utf-8")
    content = [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}},
        {"type": "text", "text": caption or f"Here's a PDF: {file_name}. Please read and analyze it."},
    ]
    await _process_and_respond(update, context, content)


async def _handle_audio_document(tmp_name, file_name, caption, update, context):
    """Handle an audio file — route directly to meeting processing."""
    await update.message.reply_text(
        f"Got your audio file ({file_name}). Transcribing and processing — this may take a minute or two."
    )
    try:
        from modules.meetings import _handle_process_meeting
        result = await _handle_process_meeting({
            "file_path": tmp_name,
            "meeting_date": None,
            "context_hint": caption,
        })
        await _send_long(update, result)
        await _process_and_respond(update, context,
            f"[System: Meeting audio was just processed. Here are the results — present them to the user and ask the clarifying questions if any.]\n\n{result}")
    except Exception:
        logger.exception("Meeting processing failed")
        await update.message.reply_text("Meeting processing failed. Check the logs.")


async def _handle_text_document(tmp_name, file_name, mime, caption, update, context):
    """Handle a generic file — try to read as text."""
    try:
        with open(tmp_name, "r", encoding="utf-8", errors="replace") as f:
            file_text = f.read(FILE_READ_MAX_CHARS)
        wrapped = f"[Document: {file_name}]\n{file_text}"
        if caption:
            wrapped += f"\n\nUser says: {caption}"
        await _process_and_respond(update, context, wrapped)
    except Exception:
        await update.message.reply_text(f"I can't read this file type: {mime or file_name}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming documents — images, PDFs, audio files, text files."""
    if not _is_allowed(update.effective_user.id):
        return

    await update.effective_chat.send_action("typing")

    import tempfile

    doc = update.message.document
    if not doc:
        await update.message.reply_text("Couldn't read that file.")
        return

    mime = doc.mime_type or ""
    file_name = doc.file_name or "unknown"
    tg_file = await doc.get_file()

    suffix = os.path.splitext(file_name)[1] or ".bin"
    tmp = tempfile.NamedTemporaryFile(prefix="gg_doc_", suffix=suffix, delete=False)
    tmp.close()

    try:
        await tg_file.download_to_drive(tmp.name)
        caption = update.message.caption or ""

        if mime.startswith("image/"):
            await _handle_image_document(tmp.name, mime, caption, update, context)
        elif mime == "application/pdf":
            await _handle_pdf_document(tmp.name, file_name, caption, update, context)
        elif mime.startswith("audio/") or file_name.endswith((".ogg", ".mp3", ".m4a", ".wav", ".flac", ".aac", ".wma")):
            await _handle_audio_document(tmp.name, file_name, caption, update, context)
        else:
            await _handle_text_document(tmp.name, file_name, mime, caption, update, context)

    except Exception:
        logger.exception("Document processing failed")
        await update.message.reply_text("I couldn't process that file. Try again?")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle shared GPS location — update weather location and respond."""
    if not _is_allowed(update.effective_user.id):
        return

    await update.effective_chat.send_action("typing")

    loc = update.message.location
    if not loc:
        return

    try:
        from modules.weather import update_location_from_gps
        result = await update_location_from_gps(loc.latitude, loc.longitude)
        await _send_long(update, result)
    except Exception:
        logger.exception("Location handling failed")
        await update.message.reply_text("Got your location but couldn't update weather. Check the logs.")


# ── Reminder checker (runs every 60s) ───────────────────────────────────────


async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    try:
        due = await tools.check_due_reminders()
        for chat_id, text in due:
            await context.bot.send_message(chat_id=chat_id, text=f"\U0001f514 {text}")
    except Exception:
        logger.exception("Reminder check failed")


# ── Main ─────────────────────────────────────────────────────────────────────


def _register_handlers(app):
    """Add all Telegram command and message handlers to the application."""
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("opus", opus_command))
    app.add_handler(CommandHandler("sonnet", sonnet_command))
    app.add_handler(CommandHandler("model", model_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))


def _schedule_jobs(app, tz):
    """Set up all job_queue entries — reminders, heartbeat, and whatsapp summarizer."""
    from datetime import time as dt_time

    # Reminder checks every 60 seconds
    app.job_queue.run_repeating(check_reminders, interval=60, first=10)

    # Heartbeat jobs
    app.job_queue.run_daily(heartbeat.morning_briefing, time=dt_time(7, 30, tzinfo=tz))
    app.job_queue.run_daily(heartbeat.evening_wrapup, time=dt_time(21, 0, tzinfo=tz))
    app.job_queue.run_repeating(heartbeat.check_upcoming_meetings, interval=300, first=30)

    # WhatsApp summarizer — every 30 minutes
    app.job_queue.run_repeating(whatsapp_summarizer.summarize_whatsapp, interval=1800, first=60)
    logger.info("WhatsApp summarizer scheduled every 30m")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in .env")
        return

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY in .env")
        return

    # Init database
    tools.init_db()
    logger.info("Database initialized")

    # Pre-load Whisper model (CUDA loading mid-conversation can crash the process)
    try:
        from audio import transcriber
        logger.info("Pre-loading Whisper model...")
        transcriber.preload()
    except Exception:
        logger.exception("Failed to pre-load Whisper — voice notes will try lazy loading")

    # Discover domain modules
    modules.discover()
    module_names = modules.get_module_names()
    if module_names:
        logger.info(f"Loaded modules: {', '.join(module_names)}")
    else:
        logger.info("No domain modules loaded")

    # Build bot
    async def _post_init(application):
        try:
            await whatsapp_server.start_server()
        except OSError as e:
            logger.warning(f"WhatsApp server failed to start (port busy?): {e} — bot continues without it")

    app = Application.builder().token(token).post_init(_post_init).build()
    logger.info(f"WhatsApp HTTP server will start on port {whatsapp_server.BRIDGE_PORT}")

    from zoneinfo import ZoneInfo
    tz = ZoneInfo(os.getenv("TIMEZONE", "America/New_York"))

    _register_handlers(app)
    _schedule_jobs(app, tz)

    logger.info("Bot starting...")
    logger.info("Heartbeat: morning 7:30 AM, evening 9:00 PM, meeting alerts every 5m")
    if ALLOWED_USER_IDS:
        logger.info(f"Restricted to user IDs: {ALLOWED_USER_IDS}")
    else:
        logger.info("No user restrictions (open to all)")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
