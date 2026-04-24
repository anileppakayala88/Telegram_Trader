import asyncio
import logging
import os
from telethon import TelegramClient
from dotenv import load_dotenv
from journal import JournalManager
from listener import register_handlers

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("trader.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


async def main():
    api_id   = int(os.getenv("TELEGRAM_API_ID"))
    api_hash = os.getenv("TELEGRAM_API_HASH")

    journal = JournalManager()
    journal.load_state()

    client = TelegramClient("session_fetch", api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        log.error("Not authorized — run auth.py first")
        return

    register_handlers(client, journal)
    log.info("Listening for trade signals...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
