import re
import uuid
from datetime import timezone
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

CHANNEL_NAME = "XAUUSD VIP BIG LOTS"
CHANNEL_ID = 1481325093

# Matches: "XAUUSD Buy limit 4664/4656", "GOLD Buy limit 4664/4656", etc.
_SIGNAL_RE = re.compile(
    r"^(?:XAUUSD|GOLD)[ \t]+(buy|sell)[ \t]+(limit|market)?[ \t]*([\d.]+)(?:/([\d.]+))?",
    re.IGNORECASE,
)
_SL_RE = re.compile(r"^sl[ \t]+([\d.]+)", re.IGNORECASE)
_TP_RE = re.compile(r"^tp[ \t]+([\d.]+)", re.IGNORECASE)

_ALL_TP_RE  = re.compile(r"ALL\s+TP\s+HIT",    re.I)
_TP_HIT_RE  = re.compile(r"TP\d+\s+HIT",       re.I)
_BE_HIT_RE  = re.compile(r"^be\s+hit$",         re.I)
_MISSED_RE  = re.compile(r"missed|^delete$",     re.I)

_NOISE_RE = re.compile(
    r"^react\b"
    r"|^i'?m\s+in$"
    r"|^go\s+again$"
    r"|^\d+\s*pips?\b"   # standalone pips announcements
    r"|^400\s*pips"
    r"|^300\s*pips"
    r"|^200\s*pips",
    re.IGNORECASE,
)


def _iso(dt) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def classify(msg) -> str:
    text = (msg.text or "").strip()
    if not text:
        return "noise"
    if _NOISE_RE.match(text):
        return "noise"
    if any(_SIGNAL_RE.match(l.strip()) for l in text.split("\n")):
        return "new_signal"
    if _ALL_TP_RE.search(text) or _TP_HIT_RE.search(text):
        return "trade_update"
    if _BE_HIT_RE.match(text) or _MISSED_RE.search(text):
        return "trade_update"
    if msg.reply_to_msg_id:
        return "trade_update"
    return "noise"


def parse_signal(msg) -> dict | None:
    text = (msg.text or "").strip()
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return None

    m, signal_idx = None, 0
    for i, line in enumerate(lines):
        m = _SIGNAL_RE.match(line)
        if m:
            signal_idx = i
            break
    if not m:
        return None

    direction   = m.group(1).upper()
    order_type  = (m.group(2) or "market").lower()
    price1      = float(m.group(3))
    price2      = float(m.group(4)) if m.group(4) else None

    if price2:
        lo, hi = sorted([price1, price2])
        entry       = lo if direction == "BUY" else hi
        entry_range = [lo, hi]
    else:
        entry       = price1
        entry_range = None

    sl, tps = None, []
    for line in lines[signal_idx + 1:]:
        sm = _SL_RE.match(line)
        if sm:
            sl = float(sm.group(1))
            continue
        tm = _TP_RE.match(line)
        if tm:
            tps.append(float(tm.group(1)))

    has_image = isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument))

    return {
        "signal_id":           str(uuid.uuid4()),
        "telegram_msg_id":     msg.id,
        "message_type":        "new_signal",
        "timestamp":           _iso(msg.date),
        "source_channel_id":   CHANNEL_ID,
        "source_channel_name": CHANNEL_NAME,
        "raw_message":         text,
        "asset_class":         "commodity",
        "instrument":          "XAUUSD",
        "direction":           direction,
        "order_type":          order_type,
        "entry":               entry,
        "entry_range":         entry_range,
        "sl":                  sl,
        "tp":                  tps,
        "has_image":           has_image,
        "parse_status":        "parsed" if sl and tps else "partial",
        "notes":               "",
    }


def parse_update(msg, signal_id: str | None) -> dict | None:
    text = (msg.text or "").strip()

    if _ALL_TP_RE.search(text):
        update_type = "full_close"
    elif _TP_HIT_RE.search(text):
        update_type = "tp_hit"
    elif _BE_HIT_RE.match(text):
        update_type = "sl_hit"
    elif _MISSED_RE.search(text):
        update_type = "cancelled"
    else:
        update_type = "commentary"

    return {
        "signal_id":                signal_id,
        "telegram_msg_id":          msg.id,
        "telegram_reply_to_msg_id": msg.reply_to_msg_id,
        "message_type":             "trade_update",
        "timestamp":                _iso(msg.date),
        "source_channel_id":        CHANNEL_ID,
        "source_channel_name":      CHANNEL_NAME,
        "raw_message":              text,
        "instrument":               "XAUUSD",
        "update_type":              update_type,
        "notes":                    "",
    }
