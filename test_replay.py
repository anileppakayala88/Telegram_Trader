"""
Offline end-to-end test: fetch recent messages from each channel and run
them through the same classify → parse → journal pipeline that main.py uses.
Does NOT require posting anything — reads existing channel history.
"""
import asyncio
import logging
import os
from telethon import TelegramClient
from dotenv import load_dotenv
from channels import CHANNEL_PARSERS
from journal import JournalManager

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("test_replay")

LIMIT = 100  # messages per channel to replay


async def replay():
    api_id   = int(os.getenv("TELEGRAM_API_ID"))
    api_hash = os.getenv("TELEGRAM_API_HASH")

    journal = JournalManager()

    client = TelegramClient("session_fetch", api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        log.error("Not authorized — run auth.py first")
        await client.disconnect()
        return

    totals = {"noise": 0, "new_signal": 0, "trade_update": 0, "error": 0}

    for channel_id, parser in CHANNEL_PARSERS.items():
        log.info(f"\n{'='*60}")
        log.info(f"Channel: {parser.CHANNEL_NAME} (ID: {channel_id})")
        log.info(f"{'='*60}")

        try:
            entity = await client.get_entity(channel_id)
            messages = await client.get_messages(entity, limit=LIMIT)
        except Exception:
            log.exception(f"Failed to fetch messages from {parser.CHANNEL_NAME}")
            continue

        channel_counts = {"noise": 0, "new_signal": 0, "trade_update": 0, "error": 0}

        for msg in reversed(messages):
            if not msg.text and not msg.media:
                continue
            try:
                msg_type = parser.classify(msg)

                if msg_type == "noise":
                    channel_counts["noise"] += 1
                    continue

                if msg_type == "new_signal":
                    entry = parser.parse_signal(msg)
                    if not entry:
                        log.warning(
                            f"  [msg_id={msg.id}] classify=new_signal but parse failed\n"
                            f"  text: {(msg.text or '').splitlines()[0][:80]}"
                        )
                        channel_counts["error"] += 1
                        continue
                    journal.write(channel_id, entry)
                    journal.track_signal(channel_id, msg.id, entry["signal_id"])
                    channel_counts["new_signal"] += 1
                    log.info(
                        f"  SIGNAL  {entry['instrument']} {entry['direction']}"
                        f" @ {entry['entry']}  SL={entry['sl']}  TP={entry['tp']}"
                        f"  status={entry['parse_status']}"
                    )

                elif msg_type == "trade_update":
                    signal_id = journal.resolve_signal_id(channel_id, msg.reply_to_msg_id)
                    entry = parser.parse_update(msg, signal_id)
                    if not entry:
                        continue
                    journal.write(channel_id, entry)
                    channel_counts["trade_update"] += 1
                    log.info(
                        f"  UPDATE  {entry['update_type']}"
                        f" → signal_id={signal_id or 'unlinked'}"
                        f"  [{(msg.text or '').splitlines()[0][:60]}]"
                    )

            except Exception:
                log.exception(f"  Error processing msg_id={msg.id}")
                channel_counts["error"] += 1

        log.info(
            f"\n  {parser.CHANNEL_NAME} summary: "
            f"signals={channel_counts['new_signal']}  "
            f"updates={channel_counts['trade_update']}  "
            f"noise={channel_counts['noise']}  "
            f"errors={channel_counts['error']}"
        )
        for k, v in channel_counts.items():
            totals[k] += v

    await client.disconnect()

    log.info(f"\n{'='*60}")
    log.info(
        f"TOTAL — signals={totals['new_signal']}  "
        f"updates={totals['trade_update']}  "
        f"noise={totals['noise']}  "
        f"errors={totals['error']}"
    )
    log.info("Journal written to journal/ — check *.jsonl files")


asyncio.run(replay())
