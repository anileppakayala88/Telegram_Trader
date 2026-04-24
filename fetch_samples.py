import asyncio
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from dotenv import load_dotenv
import os

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")

CHANNELS = [2133117224, 1481325093]  # Vip Thrilokh, XAUUSD VIP BIG LOTS
LIMIT = 30

async def fetch():
    client = TelegramClient("session_fetch", api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("Not authorized. Run auth.py first.")
        await client.disconnect()
        return

    lines = []
    for channel_id in CHANNELS:
        lines.append(f"\n{'='*60}")
        try:
            entity = await client.get_entity(channel_id)
            lines.append(f"CHANNEL: {entity.title}")
            lines.append(f"{'='*60}\n")
            messages = await client.get_messages(entity, limit=LIMIT)
            for msg in reversed(messages):
                if not msg.text and not msg.media:
                    continue
                has_image = isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument))
                lines.append(f"--- [{msg.date.strftime('%Y-%m-%d %H:%M')}] {'[IMAGE]' if has_image else ''}")
                if msg.text:
                    lines.append(msg.text)
                else:
                    lines.append("[media only — no text]")
                lines.append("")
        except Exception as e:
            lines.append(f"CHANNEL ID: {channel_id}")
            lines.append(f"{'='*60}")
            lines.append(f"Error: {e}\n")

    await client.disconnect()

    with open("samples_output.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Done — see samples_output.txt")

asyncio.run(fetch())
