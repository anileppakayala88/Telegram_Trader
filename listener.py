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
    @client.on(events.NewMessage(chats=list(CHANNEL_PARSERS.keys())))
    async def handle_message(event):
        channel_id = event.chat_id
        parser = CHANNEL_PARSERS.get(channel_id)
        if not parser:
            return

        msg = event.message
        try:
            msg_type = parser.classify(msg)

            if msg_type == "noise":
                return

            if msg_type == "new_signal":
                entry = parser.parse_signal(msg)
                if not entry:
                    log.warning(f"[{parser.CHANNEL_NAME}] classify=new_signal but parse failed — msg_id={msg.id}")
                    return
                journal.write(channel_id, entry)
                journal.track_signal(channel_id, msg.id, entry["signal_id"])
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
                entry = parser.parse_update(msg, signal_id)
                if not entry:
                    return
                journal.write(channel_id, entry)
                log.info(
                    f"[{parser.CHANNEL_NAME}] UPDATE {entry['update_type']} "
                    f"→ signal_id={signal_id or 'unlinked'}"
                )
                # These four update types all mean the trade is done:
                #   full_close  — channel closed the trade manually
                #   sl_hit      — stop loss was triggered
                #   cancelled   — entry limit/stop order was never filled
                #   tp_hit      — TP1 reached; for pending orders this also means
                #                 price blew through entry without filling us
                if entry["update_type"] in ("full_close", "sl_hit", "cancelled", "tp_hit") and signal_id:
                    asyncio.create_task(webhook.handle_close(signal_id))

        except Exception:
            log.exception(f"[{parser.CHANNEL_NAME}] Error processing msg_id={msg.id}")
