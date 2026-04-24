import asyncio
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
from dotenv import load_dotenv
import os

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")

async def list_channels():
    client = TelegramClient("session_fetch", api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("Not authorized. Run auth.py first.")
        await client.disconnect()
        return

    lines = [f"{'ID':<15} {'USERNAME':<30} {'TITLE'}\n" + "-" * 80]
    async for dialog in client.iter_dialogs():
        if isinstance(dialog.entity, (Channel, Chat)):
            username = getattr(dialog.entity, 'username', None) or 'no-username'
            title = dialog.title.encode('utf-8', errors='replace').decode('utf-8')
            lines.append(f"{dialog.entity.id:<15} {username:<30} {title}")

    output = "\n".join(lines)
    with open("channels_list.txt", "w", encoding="utf-8") as f:
        f.write(output)
    print("Done — see channels_list.txt")

    await client.disconnect()

asyncio.run(list_channels())
