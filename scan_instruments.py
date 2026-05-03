"""
Fetch last 2000 messages from each channel and print every unique instrument
name that the parser extracts, plus any new_signal lines that fail to parse.
"""
import asyncio
import logging
import os
from collections import Counter
from telethon import TelegramClient
from dotenv import load_dotenv
from channels import CHANNEL_PARSERS

load_dotenv()

logging.basicConfig(level=logging.WARNING)

LIMIT = 2000


async def scan():
    api_id   = int(os.getenv("TELEGRAM_API_ID"))
    api_hash = os.getenv("TELEGRAM_API_HASH")

    client = TelegramClient("session_fetch", api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("Not authorized — run auth.py first")
        await client.disconnect()
        return

    for channel_id, parser in CHANNEL_PARSERS.items():
        print(f"\n{'='*60}")
        print(f"Channel: {parser.CHANNEL_NAME}  (fetching last {LIMIT} msgs)")
        print(f"{'='*60}")

        entity   = await client.get_entity(channel_id)
        messages = await client.get_messages(entity, limit=LIMIT)

        instruments: Counter = Counter()
        parse_failures = []

        for msg in messages:
            if not msg.text:
                continue
            try:
                if parser.classify(msg) == "new_signal":
                    entry = parser.parse_signal(msg)
                    if entry:
                        instruments[entry["instrument"]] += 1
                    else:
                        parse_failures.append(msg.text.strip()[:120])
            except Exception as e:
                parse_failures.append(f"[ERROR: {e}] {(msg.text or '').strip()[:80]}")

        print(f"\nInstruments found ({sum(instruments.values())} signals total):")
        for name, count in instruments.most_common():
            print(f"  {name:<15} {count} signals")

        if parse_failures:
            print(f"\nParse failures ({len(parse_failures)}):")
            for f in parse_failures:
                print(f"  !! {f}")
        else:
            print("\nNo parse failures.")

    await client.disconnect()


asyncio.run(scan())
