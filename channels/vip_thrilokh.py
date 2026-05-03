import re
import uuid
from datetime import timezone
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

CHANNEL_NAME = "Vip Thrilokh"
CHANNEL_ID = 2133117224

# Normalise instrument names to standard symbols
_INSTRUMENT_MAP = {
    # Majors
    "eurusd": "EURUSD", "eu":     "EURUSD",
    "usdjpy": "USDJPY", "jpy":    "USDJPY",
    "gbpusd": "GBPUSD", "gbp":    "GBPUSD", "cable": "GBPUSD",
    "usdchf": "USDCHF", "chf":    "USDCHF",
    "audusd": "AUDUSD", "aud":    "AUDUSD",
    "usdcad": "USDCAD", "cad":    "USDCAD",
    "nzdusd": "NZDUSD", "nzd":    "NZDUSD", "kiwi": "NZDUSD",
    # Minors / crosses
    "eurgbp": "EURGBP",
    "eurjpy": "EURJPY",
    "eurcad": "EURCAD",
    "eurchf": "EURCHF",
    "euraud": "EURAUD",
    "eurnzd": "EURNZD",
    "gbpjpy": "GBPJPY",
    "gbpaud": "GBPAUD",
    "gbpcad": "GBPCAD",
    "gbpchf": "GBPCHF",
    "gbpnzd": "GBPNZD",
    "audjpy": "AUDJPY",
    "audnzd": "AUDNZD",
    "audcad": "AUDCAD",
    "audchf": "AUDCHF",
    "nzdjpy": "NZDJPY",
    "cadjpy": "CADJPY",
    "cadchf": "CADCHF",
    "chfjpy": "CHFJPY",
    # Commodities / crypto / indices
    "xauusd": "XAUUSD", "gold":   "XAUUSD",
    "xagusd": "XAGUSD", "silver": "XAGUSD",
    "btcusd": "BTCUSD", "btc":    "BTCUSD",
    "ethusd": "ETHUSD", "eth":    "ETHUSD",
    "nas100": "NAS100", "nq":     "NAS100", "nasdaq": "NAS100",
    "us30":   "US30",   "dow":    "US30",
    "spx500": "SPX500", "sp500":  "SPX500",
}

_ASSET_CLASS = {
    "EURUSD": "forex", "USDJPY": "forex", "GBPUSD": "forex",
    "USDCHF": "forex", "AUDUSD": "forex", "USDCAD": "forex", "NZDUSD": "forex",
    "EURGBP": "forex", "EURJPY": "forex", "EURCAD": "forex",
    "EURCHF": "forex", "EURAUD": "forex", "EURNZD": "forex",
    "GBPJPY": "forex", "GBPAUD": "forex", "GBPCAD": "forex",
    "GBPCHF": "forex", "GBPNZD": "forex",
    "AUDJPY": "forex", "AUDNZD": "forex", "AUDCAD": "forex", "AUDCHF": "forex",
    "NZDJPY": "forex", "CADJPY": "forex", "CADCHF": "forex", "CHFJPY": "forex",
    "XAUUSD": "commodity", "XAGUSD": "commodity",
    "BTCUSD": "crypto",    "ETHUSD": "crypto",
    "NAS100": "index",     "US30":   "index",   "SPX500": "index",
}

# Matches all signal variations seen in samples:
#   "Btc @ 74220\nSl  @ 75647\nTp. @ 70450"
#   "Sell nq @ 26678\nSl @ 26744\nTp @ 26400"
#   "Usdjpy 159.390\nSl 159.602\nTp 158.552"
#   "Sell Usdchf  0.78436\nSl 0.78641\nTp @ 0.77928"
#   "BTCUSD SELL 78702\nSL 79000\nTP 78473"  (direction after instrument)
_SIGNAL_RE = re.compile(
    r"^\W*(?:(buy|sell)[ \t]+)?(\w+)(?:[ \t]+(buy|sell))?[ \t]+@?[ \t]*([\d.]+)[ \t]*\r?\n"
    r"[ \t]*sl\d*[. \t]+@?[ \t]*([\d.]+)[ \t]*\r?\n"
    r"[ \t]*tp\d*[. \t]+@?[ \t]*([\d.]+)",
    re.IGNORECASE | re.MULTILINE,
)

# Ordered by severity — first match wins
_UPDATE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\ball\s+tp\s+hit|all\s+tp\s+hitted",   re.I),       "full_close"),
    (re.compile(r"\bclosing\s+this\b|\bclose\s+(here|full|now|trade)\b", re.I), "full_close"),
    (re.compile(r"\bclose\s+partials?\b",                 re.I),       "partial_close"),
    (re.compile(r"^close$",                               re.I),       "full_close"),
    (re.compile(r"\bsl\s+(as\s+)?be\b|\bset\s+be\b",     re.I),       "breakeven"),
    (re.compile(r"\btapped\b"
                r"|\btp\s*\d*\s+hitted?\b"   # "Tp1 hitted", "Tp 1 hitted"
                r"|\bhitted?\s+tp\s*\d*\b",  # "Already hitted tp1"
                re.I),                                               "tp_hit"),
]

_NOISE_RE = re.compile(
    r"^[\W\s]+$"                  # emoji/whitespace only
    r"|vip signal trades"
    r"|^daily crt$"
    r"|pwh reaction"
    r"|patience pay off"
    r"|slow price action"
    r"|is pushing"
    r"|total rr"
    r"|\bRR\b.*$",                # weekly RR summary lines
    re.IGNORECASE,
)


def _normalise(raw: str) -> str:
    return _INSTRUMENT_MAP.get(raw.lower(), raw.upper())


def _iso(dt) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def classify(msg) -> str:
    text = (msg.text or "").strip()
    if not text:
        return "noise"
    if _NOISE_RE.search(text):
        return "noise"
    if msg.reply_to_msg_id:
        return "trade_update"
    if _SIGNAL_RE.search(text):
        return "new_signal"
    for pat, _ in _UPDATE_PATTERNS:
        if pat.search(text):
            return "trade_update"
    return "noise"


def parse_signal(msg) -> dict | None:
    text = (msg.text or "").strip()
    m = _SIGNAL_RE.search(text)
    if not m:
        return None

    dir_before, instrument_raw, dir_after, entry_raw, sl_raw, tp_raw = m.groups()
    instrument = _normalise(instrument_raw)
    entry = float(entry_raw)
    sl    = float(sl_raw)
    tp    = float(tp_raw)

    direction_raw = dir_before or dir_after
    if direction_raw:
        direction = direction_raw.upper()
    else:
        direction = "SELL" if sl > entry else "BUY"

    has_image = isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument))

    return {
        "signal_id":           str(uuid.uuid4()),
        "telegram_msg_id":     msg.id,
        "message_type":        "new_signal",
        "timestamp":           _iso(msg.date),
        "source_channel_id":   CHANNEL_ID,
        "source_channel_name": CHANNEL_NAME,
        "raw_message":         text,
        "asset_class":         _ASSET_CLASS.get(instrument, "unknown"),
        "instrument":          instrument,
        "direction":           direction,
        "order_type":          "market",
        "entry":               entry,
        "entry_range":         None,
        "sl":                  sl,
        "tp":                  [tp],
        "has_image":           has_image,
        "parse_status":        "parsed",
        "notes":               "",
    }


def parse_update(msg, signal_id: str | None) -> dict | None:
    text = (msg.text or "").strip()

    update_type = "commentary"
    for pat, utype in _UPDATE_PATTERNS:
        if pat.search(text):
            update_type = utype
            break

    return {
        "signal_id":              signal_id,
        "telegram_msg_id":        msg.id,
        "telegram_reply_to_msg_id": msg.reply_to_msg_id,
        "message_type":           "trade_update",
        "timestamp":              _iso(msg.date),
        "source_channel_id":      CHANNEL_ID,
        "source_channel_name":    CHANNEL_NAME,
        "raw_message":            text,
        "instrument":             None,
        "update_type":            update_type,
        "notes":                  "",
    }
