import asyncio
import logging
import os
from telethon import TelegramClient
from dotenv import load_dotenv
from journal import JournalManager
from listener import register_handlers
from channels import CHANNEL_PARSERS
import webhook

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
    webhook.load_state()

    client = TelegramClient("session_fetch", api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        log.error("Not authorized — run auth.py first")
        return

    # Fetch all dialogs first so Telethon caches channel entities,
    # then resolve each channel so the NewMessage filter can match by ID.
    log.info("Fetching dialogs to cache channel entities...")
    await client.get_dialogs()

    for channel_id in CHANNEL_PARSERS:
        try:
            await client.get_entity(channel_id)
            log.info(f"Resolved entity for channel {channel_id}")
        except Exception as e:
            log.warning(f"Could not resolve entity for channel {channel_id}: {e}")

    register_handlers(client, journal)
    log.info("Listening for trade signals...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
