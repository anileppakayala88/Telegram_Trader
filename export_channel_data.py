import argparse
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient


DEFAULT_CHANNELS = [2133117224, 1481325093]
SESSION_NAME = "session_fetch"


def safe_name(value):
    value = str(value).strip() or "unknown"
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.ASCII).strip("._")
    return value or "unknown"


def parse_channel(value):
    value = value.strip()
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def isoformat(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def message_url(entity, msg_id):
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"

    channel_id = str(getattr(entity, "id", "")).removeprefix("-100")
    return f"https://t.me/c/{channel_id}/{msg_id}" if channel_id else None


def media_kind(msg):
    if not msg.media:
        return None
    if msg.photo:
        return "photo"
    document = getattr(msg, "document", None)
    if document and getattr(document, "mime_type", None):
        mime_type = document.mime_type
        if mime_type.startswith("image/"):
            return "image_document"
        return mime_type
    return type(msg.media).__name__


async def export_channel(client, channel, limit, output_dir, download_media):
    entity = await client.get_entity(channel)
    channel_title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(channel)
    channel_dir = output_dir / safe_name(f"{getattr(entity, 'id', channel)}_{channel_title}")
    media_dir = channel_dir / "media"
    channel_dir.mkdir(parents=True, exist_ok=True)
    if download_media:
        media_dir.mkdir(exist_ok=True)

    jsonl_path = channel_dir / "messages.jsonl"
    text_path = channel_dir / "messages.txt"
    count = 0
    media_count = 0

    with jsonl_path.open("w", encoding="utf-8") as jsonl_file, text_path.open("w", encoding="utf-8") as text_file:
        async for msg in client.iter_messages(entity, limit=limit, reverse=True):
            if not msg.message and not msg.media:
                continue

            media_path = None
            if download_media and msg.media:
                media_path = await msg.download_media(file=str(media_dir / f"{msg.id}_"))
                if media_path:
                    media_count += 1
                    try:
                        media_path = str(Path(media_path).relative_to(channel_dir))
                    except ValueError:
                        media_path = os.path.relpath(media_path, channel_dir)

            record = {
                "channel_id": getattr(entity, "id", None),
                "channel_title": channel_title,
                "channel_username": getattr(entity, "username", None),
                "message_id": msg.id,
                "date": isoformat(msg.date),
                "sender_id": getattr(msg, "sender_id", None),
                "text": msg.message or "",
                "has_media": bool(msg.media),
                "media_kind": media_kind(msg),
                "media_path": media_path,
                "url": message_url(entity, msg.id),
            }
            jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")

            text_file.write(f"--- [{record['date']}] message_id={msg.id}")
            if record["media_kind"]:
                text_file.write(f" media={record['media_kind']}")
            text_file.write("\n")
            text_file.write(record["text"] or "[media only - no text]")
            if media_path:
                text_file.write(f"\n[media saved: {media_path}]")
            text_file.write("\n\n")
            count += 1

    return {
        "channel": channel_title,
        "channel_id": getattr(entity, "id", None),
        "messages": count,
        "media_downloads": media_count,
        "output": str(channel_dir),
    }


async def main():
    parser = argparse.ArgumentParser(
        description="Export Telegram channel text and attached media using the existing Telethon session."
    )
    parser.add_argument(
        "channels",
        nargs="*",
        type=parse_channel,
        help="Channel IDs or @usernames. Defaults to the two configured trade channels.",
    )
    parser.add_argument("--limit", type=int, default=100, help="Messages per channel to export.")
    parser.add_argument("--output", default="exports/telegram_channels", help="Output directory.")
    parser.add_argument("--session", default=SESSION_NAME, help="Telethon session name to use.")
    parser.add_argument(
        "--no-media",
        action="store_true",
        help="Only export message text/metadata; do not download attached media.",
    )
    args = parser.parse_args()

    load_dotenv()
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit("Missing TELEGRAM_API_ID or TELEGRAM_API_HASH in .env")

    output_dir = Path(args.output) / datetime.now().strftime("%Y%m%d_%H%M%S")
    channels = args.channels or DEFAULT_CHANNELS

    client = TelegramClient(args.session, int(api_id), api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise SystemExit("Not authorized. Run: python auth.py")

        results = []
        for channel in channels:
            try:
                results.append(
                    await export_channel(
                        client=client,
                        channel=channel,
                        limit=args.limit,
                        output_dir=output_dir,
                        download_media=not args.no_media,
                    )
                )
            except Exception as exc:
                results.append({"channel": str(channel), "error": str(exc)})

        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = output_dir / "summary.json"
        summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

        for result in results:
            if "error" in result:
                print(f"ERROR {result['channel']}: {result['error']}")
            else:
                print(
                    f"OK {result['channel']}: {result['messages']} messages, "
                    f"{result['media_downloads']} media files -> {result['output']}"
                )
        print(f"Summary: {summary_path}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
