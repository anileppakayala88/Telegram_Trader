import logging
from telethon import events
from channels import CHANNEL_PARSERS

log = logging.getLogger(__name__)


def register_handlers(client, journal):
    @client.on(events.NewMessage(chats=list(CHANNEL_PARSERS.keys())))
    async def handle_message(event):
        channel_id = event.chat_id
        parser = CHANNEL_PARSERS.get(channel_id)
        if not parser:
            return

        msg = event.message
        try:
            msg_type = parser.classify(msg)

            if msg_type == "noise":
                return

            if msg_type == "new_signal":
                entry = parser.parse_signal(msg)
                if not entry:
                    log.warning(f"[{parser.CHANNEL_NAME}] classify=new_signal but parse failed — msg_id={msg.id}")
                    return
                journal.write(channel_id, entry)
                journal.track_signal(channel_id, msg.id, entry["signal_id"])
                log.info(
                    f"[{parser.CHANNEL_NAME}] SIGNAL {entry['instrument']} "
                    f"{entry['direction']} @ {entry['entry']} "
                    f"SL={entry['sl']} TP={entry['tp']}"
                )

            elif msg_type == "trade_update":
                signal_id = journal.resolve_signal_id(channel_id, msg.reply_to_msg_id)
                entry = parser.parse_update(msg, signal_id)
                if not entry:
                    return
                journal.write(channel_id, entry)
                log.info(
                    f"[{parser.CHANNEL_NAME}] UPDATE {entry['update_type']} "
                    f"→ signal_id={signal_id or 'unlinked'}"
                )

        except Exception:
            log.exception(f"[{parser.CHANNEL_NAME}] Error processing msg_id={msg.id}")
