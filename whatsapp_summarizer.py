"""Periodic WhatsApp message summarizer — extracts actionable context into mem0."""

import logging
import asyncio
from collections import defaultdict

from anthropic import APIStatusError

import llm
import tools

logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_CHAT = 50


async def summarize_whatsapp(context=None):
    """Periodic job: summarize unsummarized WhatsApp messages into mem0."""
    if not tools.memory_enabled():
        return
    logger.info("Running WhatsApp summarization")

    try:
        # Fetch unsummarized messages
        def _fetch():
            conn = tools.get_db()
            try:
                rows = conn.execute(
                    """SELECT id, chat_jid, chat_name, sender_jid, sender_name,
                              message_text, is_from_me, timestamp
                       FROM whatsapp_messages
                       WHERE summarized = 0 AND message_text IS NOT NULL
                       ORDER BY timestamp ASC
                       LIMIT 500"""
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

        messages = await asyncio.to_thread(_fetch)

        if not messages:
            logger.info("No unsummarized WhatsApp messages")
            return

        # Group by chat
        chats: dict[str, list] = defaultdict(list)
        for msg in messages:
            chats[msg["chat_jid"]].append(msg)

        summarized_ids = []

        for chat_jid, chat_msgs in chats.items():
            chat_name = chat_msgs[0]["chat_name"] or chat_jid
            is_group = chat_jid.endswith("@g.us")

            # Skip group chats with fewer than 2 messages (noise filter)
            # But always process 1:1 DMs — a single message can be high-signal
            if is_group and len(chat_msgs) < 2:
                continue

            # Cap per batch to control API costs
            batch = chat_msgs[:MAX_MESSAGES_PER_CHAT]

            # Format messages for Claude
            formatted = []
            for m in batch:
                sender = "User" if m["is_from_me"] else (m["sender_name"] or m["sender_jid"])
                ts = m["timestamp"][:16].replace("T", " ")
                formatted.append(f"[{ts}] {sender}: {m['message_text']}")

            messages_text = "\n".join(formatted)

            try:
                response = None
                for attempt in range(3):
                    try:
                        response = await llm.create(
                            max_tokens=500,
                            messages=[
                                {
                                    "role": "user",
                                    "content": (
                                        f"Extract actionable context from these WhatsApp messages in the chat '{chat_name}'. "
                                        f"Focus on: commitments, meetings, requests, decisions, important information, "
                                        f"and anything the user should know or act on. Be concise (2-4 sentences max). "
                                        f"If there's nothing actionable, say 'No actionable items.'\n\n"
                                        f"{messages_text}"
                                    ),
                                }
                            ],
                        )
                        break
                    except APIStatusError as e:
                        if e.status_code in (429, 529) and attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        raise
                if response is None:
                    logger.warning("WhatsApp summary retries exhausted")
                    continue

                summary_text = llm.text(response)
                if not summary_text:
                    continue

                # Store in mem0 (skip if nothing actionable)
                if summary_text.casefold().strip(" .!") != "no actionable items":
                    date = batch[-1]["timestamp"][:10]
                    tagged_summary = f"[WhatsApp {chat_name} ({chat_jid}), {date}] {summary_text}"
                    await tools.remember(
                        tagged_summary,
                        tags=["whatsapp", chat_name, chat_jid, date],
                        infer=False,  # The summary is already extracted. Save it verbatim.
                    )
                    logger.info("Summarized %d WhatsApp messages", len(batch))
                else:
                    logger.info("No actionable items in %d WhatsApp messages", len(batch))

            except Exception:
                logger.exception("Failed to summarize a WhatsApp chat")
                continue  # don't mark as summarized if summary failed

            # Mark batch as summarized
            summarized_ids.extend(m["id"] for m in batch)

        # Mark all processed messages as summarized
        if summarized_ids:
            def _mark():
                conn = tools.get_db()
                try:
                    placeholders = ",".join("?" * len(summarized_ids))
                    conn.execute(
                        f"UPDATE whatsapp_messages SET summarized = 1 WHERE id IN ({placeholders})",
                        summarized_ids,
                    )
                    conn.commit()
                finally:
                    conn.close()

            await asyncio.to_thread(_mark)
            logger.info(f"Marked {len(summarized_ids)} messages as summarized")

    except Exception:
        logger.exception("WhatsApp summarization failed")
