import asyncio
from telethon import TelegramClient
from dotenv import load_dotenv
import os

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")
phone = os.getenv("TELEGRAM_PHONE")

async def auth():
    async with TelegramClient("session_fetch", api_id, api_hash) as client:
        await client.start(phone=phone)
        me = await client.get_me()
        print(f"\nAuthenticated as: {me.first_name} (@{me.username})")
        print("Session saved. You can now run fetch_samples.py")

asyncio.run(auth())
