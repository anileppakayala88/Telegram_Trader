"""
Fetch last 1000 messages from each channel, classify them, and print a grouped
report: new_signal | trade_update | noise — with the raw message text.
"""
import asyncio
import os
from collections import defaultdict
from dotenv import load_dotenv
from telethon import TelegramClient
from channels import vip_thrilokh, xauusd_big_lots

load_dotenv()

CHANNELS = {
    2133117224: vip_thrilokh,
    1481325093: xauusd_big_lots,
}
LIMIT = 1000


class _FakeMsg:
    """Minimal stand-in so parser classify() works without a real Telethon message."""
    def __init__(self, msg):
        self.text = msg.text or ""
        self.reply_to_msg_id = msg.reply_to_msg_id
        self.media = msg.media
        self.id = msg.id
        self.date = msg.date


async def main():
    client = TelegramClient("session_fetch",
                            int(os.getenv("TELEGRAM_API_ID")),
                            os.getenv("TELEGRAM_API_HASH"))
    await client.connect()

    if not await client.is_user_authorized():
        print("Not authorized — run auth.py first.")
        await client.disconnect()
        return

    for channel_id, parser in CHANNELS.items():
        entity = await client.get_entity(channel_id)
        messages = await client.get_messages(entity, limit=LIMIT)
        messages = list(reversed(messages))   # oldest → newest

        buckets = defaultdict(list)

        for raw in messages:
            if not raw.text and not raw.media:
                continue
            fake = _FakeMsg(raw)
            label = parser.classify(fake)
            text = (raw.text or "[media only]").strip()
            buckets[label].append((raw.id, raw.date.strftime("%Y-%m-%d %H:%M"), text))

        # ── Print report ────────────────────────────────────────────────
        sep = "=" * 70
        print(f"\n{sep}")
        print(f"CHANNEL: {parser.CHANNEL_NAME}  ({len(messages)} messages fetched)")
        print(sep)
        counts = {k: len(v) for k, v in buckets.items()}
        for label in ("new_signal", "trade_update", "noise"):
            print(f"  {label:<14} {counts.get(label, 0):>4}")
        print()

        for label in ("new_signal", "trade_update", "noise"):
            items = buckets.get(label, [])
            if not items:
                continue
            print(f"{'-'*70}")
            print(f"  {label.upper()}  ({len(items)})")
            print(f"{'-'*70}")
            for msg_id, ts, text in items:
                full = text.replace("\n", " | ")[:200]
                print(f"  [{ts}] id={msg_id}  {full}")
            print()

    await client.disconnect()


asyncio.run(main())
