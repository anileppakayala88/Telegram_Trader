"""
listener.py — Telethon event handler

Receives every new message from the registered Telegram channels, routes it
through the channel-specific parser, writes it to the journal, and triggers
order execution via webhook.py.

Message lifecycle:
  1. classify()    → "new_signal" | "trade_update" | "noise"
  2. parse_*()     → structured dict (see CLAUDE.md signal schema)
  3. journal.write()  → appended to per-channel .jsonl file
  4. webhook.*()   → order placed or closed on the broker (Phase 2)
"""

import asyncio
import logging
from telethon import events
from channels import CHANNEL_PARSERS
import webhook

log = logging.getLogger(__name__)


def register_handlers(client, journal):
    @client.on(events.NewMessage(chats=list(CHANNEL_PARSERS.keys()), incoming=True, outgoing=True))
    async def handle_message(event):
        raw_chat_id = event.chat_id
        # Telethon returns marked IDs: channels as -100XXXXXXXXXX, groups as -XXXXXXX
        # Normalize back to the raw positive ID used in CHANNEL_PARSERS
        if raw_chat_id <= -1_000_000_000_000:
            channel_id = abs(raw_chat_id) - 1_000_000_000_000
        elif raw_chat_id < 0:
            channel_id = abs(raw_chat_id)
        else:
            channel_id = raw_chat_id
        log.debug(f"Message received: raw_chat_id={raw_chat_id} normalized={channel_id} msg_id={event.message.id}")
        parser = CHANNEL_PARSERS.get(channel_id)
        if not parser:
            log.warning(f"No parser for channel_id={channel_id} (raw={raw_chat_id})")
            return

        msg = event.message
        try:
            # classify() and parse_update() may call the LLM (sync HTTP).
            # Run them in a thread so the Telethon event loop is never blocked.
            msg_type = await asyncio.to_thread(parser.classify, msg)
            log.info(f"[{parser.CHANNEL_NAME}] classify={msg_type} msg_id={msg.id} text={repr((msg.text or '')[:80])}")

            if msg_type == "noise":
                return

            if msg_type == "new_signal":
                entry = parser.parse_signal(msg)
                if not entry:
                    log.warning(f"[{parser.CHANNEL_NAME}] classify=new_signal but parse failed — msg_id={msg.id}")
                    return

                journal.write(channel_id, entry)
                journal.track_signal(channel_id, msg.id, entry["signal_id"], entry.get("instrument"))
                log.info(
                    f"[{parser.CHANNEL_NAME}] SIGNAL {entry['instrument']} "
                    f"{entry['direction']} @ {entry['entry']} "
                    f"SL={entry['sl']} TP={entry['tp']}"
                )
                # create_task fires place_order without blocking the listener —
                # order execution runs concurrently while the next message can
                # be received and processed immediately
                asyncio.create_task(webhook.place_order(entry))

            elif msg_type == "trade_update":
                signal_id = journal.resolve_signal_id(channel_id, msg.reply_to_msg_id)
                result    = await asyncio.to_thread(parser.parse_update, msg, signal_id)
                if not result:
                    return
                # Parsers may return a single dict or a list (e.g. Kathy ZIP multi-close)
                entries = result if isinstance(result, list) else [result]
                for entry in entries:
                    # Channels without reply threading (e.g. Kathy ZIP) leave signal_id=None
                    # and set instrument — resolve here by instrument lookup
                    if entry.get("signal_id") is None and entry.get("instrument"):
                        entry["signal_id"] = journal.resolve_by_instrument(channel_id, entry["instrument"])
                    sid = entry.get("signal_id")
                    journal.write(channel_id, entry)
                    log.info(
                        f"[{parser.CHANNEL_NAME}] UPDATE {entry['update_type']} "
                        f"→ signal_id={sid or 'unlinked'}"
                    )
                    if entry["update_type"] in ("full_close", "sl_hit", "cancelled") and sid:
                        asyncio.create_task(webhook.handle_close(sid, entry["update_type"]))
                    elif entry["update_type"] == "tp_hit" and sid:
                        # Move remaining positions to breakeven (or close all if MOVE_SL_TO_BE_ON_TP1=false)
                        asyncio.create_task(webhook.handle_tp_hit(sid))

        except Exception:
            log.exception(f"[{parser.CHANNEL_NAME}] Error processing msg_id={msg.id}")
