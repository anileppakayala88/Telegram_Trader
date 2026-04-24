import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

JOURNAL_DIR = Path("journal")

CHANNEL_NAMES = {
    2133117224: "vip_thrilokh",
    1481325093: "xauusd_big_lots",
}


class JournalManager:
    def __init__(self):
        JOURNAL_DIR.mkdir(exist_ok=True)
        # {channel_id: {telegram_msg_id: signal_id}}
        self._msg_to_signal: dict[int, dict[int, str]] = {}
        # {channel_id: signal_id} — fallback when update has no reply_to
        self._last_signal: dict[int, str] = {}

    def _path(self, channel_id: int) -> Path:
        name = CHANNEL_NAMES.get(channel_id, str(channel_id))
        return JOURNAL_DIR / f"{name}.jsonl"

    def write(self, channel_id: int, entry: dict):
        with open(self._path(channel_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def track_signal(self, channel_id: int, telegram_msg_id: int, signal_id: str):
        self._msg_to_signal.setdefault(channel_id, {})[telegram_msg_id] = signal_id
        self._last_signal[channel_id] = signal_id

    def resolve_signal_id(self, channel_id: int, reply_to_msg_id: int | None) -> str | None:
        if reply_to_msg_id:
            return self._msg_to_signal.get(channel_id, {}).get(reply_to_msg_id)
        return self._last_signal.get(channel_id)

    def load_state(self):
        """Rebuild in-memory signal map from journal files on startup."""
        for channel_id, name in CHANNEL_NAMES.items():
            path = JOURNAL_DIR / f"{name}.jsonl"
            if not path.exists():
                continue
            self._msg_to_signal[channel_id] = {}
            count = 0
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("message_type") == "new_signal":
                            tid = entry.get("telegram_msg_id")
                            sid = entry.get("signal_id")
                            if tid and sid:
                                self._msg_to_signal[channel_id][tid] = sid
                                self._last_signal[channel_id] = sid
                                count += 1
                    except json.JSONDecodeError:
                        continue
            log.info(f"Loaded {count} signals from {name}.jsonl")
