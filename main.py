"""MyGG — personal AI assistant on Telegram. Bot entry point."""

import os
import logging
from functools import partial
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
# HTTP request URLs from Telegram include the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import agent
import audio
import llm
import tools
import heartbeat
from modules import feedback, protagonism
import whatsapp_server
import whatsapp_summarizer
import asyncio
import json as _json
import sys
import modules

ALLOWED_USER_IDS: set[int] = set()

from utils import split_long_message, validate_access
from constants import TELEGRAM_MAX_LENGTH, VOICE_LONG_THRESHOLD_SECONDS, FILE_READ_MAX_CHARS


def _is_allowed(update: Update) -> bool:
    user, chat = update.effective_user, update.effective_chat
    return bool(user and chat and chat.type == "private" and chat.id == user.id
                and user.id in ALLOWED_USER_IDS)


async def _send_long(update: Update, text: str):
    """Send a message, splitting if it exceeds Telegram's limit."""
    for chunk in split_long_message(text):
        await update.message.reply_text(chunk)


# ── Handlers ─────────────────────────────────────────────────────────────────


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    await update.message.reply_text(
        f"Hey {agent.USER_NAME}. I'm your AI assistant. Ask me anything, give me todos, "
        "check your calendar, or talk through an idea.\n\n"
        f"Your Telegram ID: {update.effective_user.id}"
    )


async def opus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    if llm.uses_local():
        await update.message.reply_text(f"Local mode uses {llm.MODEL} for this chat.")
        return
    chat_id = update.effective_chat.id
    agent.model_overrides[chat_id] = agent.MODEL_OPUS
    await update.message.reply_text(f"Switched to {agent.MODEL_OPUS} for this chat.")


async def sonnet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    chat_id = update.effective_chat.id
    agent.model_overrides.pop(chat_id, None)
    await update.message.reply_text(f"Switched back to {agent.MODEL} (default).")


async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    chat_id = update.effective_chat.id
    current = agent.model_overrides.get(chat_id, agent.MODEL)
    is_override = chat_id in agent.model_overrides
    label = " (overridden)" if is_override else " (default)"
    await update.message.reply_text(f"Current model: {current}{label}")


async def evolve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update) or update.effective_user.id != heartbeat.OWNER_CHAT_ID:
        return
    import evolve
    import subprocess
    message = update.message
    if message.forward_origin or llm.LOCAL or not os.getenv("EVOLVE_SAFEWORD"):
        await message.reply_text("Code updates require an original owner command, Claude mode and EVOLVE_SAFEWORD.")
        return
    if len(context.args) < 2 or not evolve.authorized(context.args[0]):
        await message.reply_text("Use /evolve followed by your safeword and the module change you want.")
        return
    await message.reply_text("Preparing and testing the change in a clean copy.")
    try:
        result = await asyncio.to_thread(evolve.evolve, " ".join(context.args[1:]), context.args[0])
    except ValueError as exc:
        await message.reply_text(str(exc))
        return
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Code update failed (%s)", type(exc).__name__)
        await message.reply_text("The update failed. Check Git, Claude Code authentication and the test dependencies.")
        return
    await message.reply_text(result + ". Restarting GG.")
    context.application.bot_data["restart_after_update"] = True
    context.application.stop_running()


async def _show_draft(update: Update, channel: str, preview: str, payload: dict) -> str:
    """Keep one approval in this private chat. The token identifies the exact action."""
    from modules import _outgoing
    token = _outgoing.prepare(update.effective_chat.id, channel, preview, payload)
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("Send", callback_data=f"draft:send:{token}"),
        InlineKeyboardButton("Cancel", callback_data=f"draft:cancel:{token}"),
    ]])
    for offset in range(0, len(preview), TELEGRAM_MAX_LENGTH):
        last = offset + TELEGRAM_MAX_LENGTH >= len(preview)
        await update.message.reply_text(preview[offset:offset + TELEGRAM_MAX_LENGTH],
                                        reply_markup=buttons if last else None,
                                        disable_web_page_preview=True)
    _outgoing.presented(update.effective_chat.id, token)
    return "Draft shown in Telegram. Awaiting the user's Send button."


async def handle_draft_approval(update: Update, context):
    query = update.callback_query
    if not _is_allowed(update):
        await query.answer("Approval requires an authorized private chat.")
        return
    _, action, token = query.data.split(":")
    from modules import _outgoing
    await query.answer()
    result = await _outgoing.resolve(update.effective_chat.id, token, action)
    await query.message.reply_text(result)


async def _process_and_respond(update: Update, context: ContextTypes.DEFAULT_TYPE, user_content: str | list, force_opus: bool = False):
    """Shared handler: send message to agent, reply with text + optional audio."""
    chat_id = update.effective_chat.id

    protagonism.note_user_activity()

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
        response, audio_files = await agent.handle_message(
            chat_id, user_content, force_opus=force_opus, show_draft=partial(_show_draft, update)
        )
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
    if not _is_allowed(update):
        return
    protagonism.note_user_activity()
    await update.effective_chat.send_action("typing")

    text = update.message.text
    reply = update.message.reply_to_message
    saved = feedback.apply_reply_feedback(update.effective_chat.id, text, reply)
    if saved:
        await _send_long(update, saved)
        return
    text += feedback.get_reply_context(update.effective_chat.id, reply)
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
    if not _is_allowed(update):
        return
    if not audio.can_transcribe():
        await update.message.reply_text(audio.VOICE_SETUP)
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
        if hasattr(duration, "total_seconds"):
            duration = int(duration.total_seconds())
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
    if not _is_allowed(update):
        return
    if llm.uses_local() and not llm.LOCAL_VISION:
        await update.message.reply_text("My local model handles text. Send the text from that image.")
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
    """Extract PDF text locally when the selected model has no document input."""
    if llm.uses_local():
        from modules._documents import read_pdf_text
        from constants import PDF_EXTRACT_TIMEOUT_SECONDS
        try:
            extracted = await asyncio.wait_for(asyncio.to_thread(read_pdf_text, tmp_name),
                                               timeout=PDF_EXTRACT_TIMEOUT_SECONDS)
        except TimeoutError:
            await _send_long(update, "PDF text extraction timed out. Try a smaller document or select the pages you need.")
            return
        content = f"[PDF: {file_name}; extracted text, not layout or images]\n{extracted}\n\n{caption or 'Please read and analyze this document.'}"
        await _process_and_respond(update, context, content)
        return
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
    if not _is_allowed(update):
        return

    await update.effective_chat.send_action("typing")

    import tempfile

    doc = update.message.document
    if not doc:
        await update.message.reply_text("Couldn't read that file.")
        return

    mime = doc.mime_type or ""
    file_name = doc.file_name or "unknown"
    audio_file = mime.startswith("audio/") or file_name.lower().endswith((".ogg", ".mp3", ".m4a", ".wav", ".flac", ".aac", ".wma"))
    if audio_file and not audio.can_transcribe():
        await update.message.reply_text(audio.VOICE_SETUP)
        return
    if llm.uses_local() and not llm.LOCAL_VISION and mime.startswith("image/"):
        await update.message.reply_text("My local model handles text. Send the text from that file or image.")
        return
    tg_file = await doc.get_file()

    suffix = os.path.splitext(file_name)[1] or ".bin"
    tmp = tempfile.NamedTemporaryFile(prefix="gg_doc_", suffix=suffix, delete=False)
    tmp.close()

    try:
        await tg_file.download_to_drive(tmp.name)
        caption = update.message.caption or ""

        if mime.startswith("image/"):
            await _handle_image_document(tmp.name, mime, caption, update, context)
        elif mime == "application/pdf" or file_name.lower().endswith(".pdf"):
            await _handle_pdf_document(tmp.name, file_name, caption, update, context)
        elif audio_file:
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
    if not _is_allowed(update):
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
    for reminder_id, chat_id, text, ref in await tools.get_due_reminders():
        if chat_id not in ALLOWED_USER_IDS:
            await tools.finish_reminder(reminder_id)
            continue
        ref = ref or f"reminder:{reminder_id}"
        decision = feedback.get_suppression(ref, text)
        if decision == "snoozed":
            continue
        if decision != "dismissed":
            try:
                if text.startswith(protagonism.FOLLOWUP_PREFIX):
                    if chat_id != heartbeat.OWNER_CHAT_ID:
                        await tools.finish_reminder(reminder_id)
                        continue
                    if not await protagonism.compose_followup(context, text, ref):
                        continue
                else:
                    sent = await context.bot.send_message(chat_id=chat_id, text=f"🔔 Reminder: {text}")
                    if sent is not None:
                        feedback.record_notice(chat_id, sent.message_id, ref, text)
            except Exception:
                logger.warning("Reminder %s could not be delivered; will retry", reminder_id)
                continue
        await tools.finish_reminder(reminder_id)


# ── Main ─────────────────────────────────────────────────────────────────────


def _register_handlers(app):
    """Add all Telegram command and message handlers to the application."""
    app.add_handler(CallbackQueryHandler(handle_draft_approval, pattern=r"^draft:(send|cancel):[0-9a-f]{24}$"))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("evolve", evolve_command))
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
    import connection_health
    connection_health.schedule(app, tz, heartbeat.OWNER_CHAT_ID)

    # Reminder checks every 60 seconds
    app.job_queue.run_repeating(check_reminders, interval=60, first=10)

    # Heartbeat jobs
    if heartbeat.OWNER_CHAT_ID:
        for hour in (9, 13, 17):
            app.job_queue.run_daily(heartbeat.email_triage, time=dt_time(hour, 0, tzinfo=tz))
        for hour in (10, 15):
            app.job_queue.run_daily(protagonism.anchor_scout, time=dt_time(hour, 0, tzinfo=tz))
        app.job_queue.run_daily(protagonism.leisure_menu, time=dt_time(16, 0, tzinfo=tz), days=(5,))
        app.job_queue.run_daily(heartbeat.morning_briefing, time=dt_time(7, 30, tzinfo=tz))
        app.job_queue.run_daily(heartbeat.evening_wrapup, time=dt_time(21, 0, tzinfo=tz))
        app.job_queue.run_repeating(heartbeat.check_upcoming_meetings, interval=300, first=30)

    # WhatsApp summarizer — every 30 minutes
    if whatsapp_server.BRIDGE_SECRET and tools.memory_enabled():
        app.job_queue.run_repeating(whatsapp_summarizer.summarize_whatsapp, interval=1800, first=60)
        logger.info("WhatsApp summarizer scheduled every 30m")


def main():
    global ALLOWED_USER_IDS
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env")

    if not llm.LOCAL and not os.getenv("ANTHROPIC_API_KEY", "").strip():
        raise SystemExit("Set ANTHROPIC_API_KEY in .env or choose LLM_BACKEND=local")

    try:
        ALLOWED_USER_IDS, heartbeat.OWNER_CHAT_ID = validate_access()
        protagonism.OWNER_CHAT_ID = heartbeat.OWNER_CHAT_ID
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    # Init database
    tools.init_db()
    logger.info("Database initialized")

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

    from zoneinfo import ZoneInfo
    tz = ZoneInfo(os.getenv("TIMEZONE", "America/New_York"))

    _register_handlers(app)
    _schedule_jobs(app, tz)

    logger.info("Bot starting...")
    if heartbeat.OWNER_CHAT_ID:
        logger.info("Heartbeat: morning 7:30 AM, evening 9:00 PM, meeting alerts every 5m")
    logger.info("Restricted to %d authorized private chat(s)", len(ALLOWED_USER_IDS))

    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return app.bot_data.get("restart_after_update")


if __name__ == "__main__":
    from supervise import instance_lock
    with instance_lock("bot"):
        restart = main()
    if restart and os.getenv("MYGG_SUPERVISED") != "1":
        os.execv(sys.executable, [sys.executable, *sys.argv])
