import asyncio
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from dotenv import load_dotenv
import os

load_dotenv()
api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")

async def fetch():
    client = TelegramClient("session_fetch", api_id, api_hash)
    await client.connect()
    entity = await client.get_entity(2249628758)
    print(f"CHANNEL: {entity.title}\n{'='*60}\n")
    messages = await client.get_messages(entity, limit=80)
    lines = []
    for msg in reversed(messages):
        if not msg.text and not msg.media:
            continue
        has_image = isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument))
        reply = f" [REPLY to {msg.reply_to_msg_id}]" if msg.reply_to_msg_id else ""
        lines.append(f"--- [{msg.date.strftime('%Y-%m-%d %H:%M')}] [id={msg.id}]{reply} {'[IMAGE]' if has_image else ''}")
        lines.append(msg.text if msg.text else "[media only]")
        lines.append("")
    await client.disconnect()
    with open("kathy_samples.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Done — see kathy_samples.txt")

asyncio.run(fetch())
