import re
import uuid
from datetime import timezone

CHANNEL_NAME = "Kathy ZIP Forex Trades"
CHANNEL_ID = 2249628758

_INSTRUMENT_MAP = {
    "eurusd": "EURUSD", "usdjpy": "USDJPY", "gbpusd": "GBPUSD",
    "usdchf": "USDCHF", "audusd": "AUDUSD", "usdcad": "USDCAD", "nzdusd": "NZDUSD",
    "eurgbp": "EURGBP", "eurjpy": "EURJPY", "eurcad": "EURCAD",
    "eurchf": "EURCHF", "euraud": "EURAUD", "eurnzd": "EURNZD",
    "gbpjpy": "GBPJPY", "gbpaud": "GBPAUD", "gbpcad": "GBPCAD",
    "gbpchf": "GBPCHF", "gbpnzd": "GBPNZD",
    "audjpy": "AUDJPY", "audnzd": "AUDNZD", "audcad": "AUDCAD", "audchf": "AUDCHF",
    "nzdjpy": "NZDJPY", "cadjpy": "CADJPY", "cadchf": "CADCHF", "chfjpy": "CHFJPY",
    "nzdcad": "NZDCAD", "nzdchf": "NZDCHF",
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
    "NZDCAD": "forex", "NZDCHF": "forex",
}

# Signal: "NEW TRADE" block with emoji-prefixed Entry / Stop / Exit lines
_NEW_TRADE_RE = re.compile(r"\bNEW TRADE\b", re.IGNORECASE)
_ENTRY_RE     = re.compile(r"Entry:\s*(Buy|Sell)\s+\d+\s+units?\s+(\w+)\s+at\s+([\d.]+)", re.IGNORECASE)
_STOP_RE      = re.compile(r"Stop at\s+([\d.]+)", re.IGNORECASE)
_EXIT_RE      = re.compile(r"Exit at\s+([\d.]+)", re.IGNORECASE)

# Update patterns — anchored to line start so multi-close messages yield one match per line
_CLOSE_RE   = re.compile(
    r"^close(?:\s+(?:rest\s+of|\d+/\d+))?\s+(\w+)\s+at\s+[\d.]+",
    re.IGNORECASE | re.MULTILINE,
)
_STOPPED_RE = re.compile(r"^stopped\s+on\s+(\w+)", re.IGNORECASE | re.MULTILINE)
_TP_HIT_RE  = re.compile(r"^(\w+)\s+[^\n]*target\s+hit",  re.IGNORECASE | re.MULTILINE)


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
    if _NEW_TRADE_RE.search(text) and _ENTRY_RE.search(text):
        return "new_signal"
    if _CLOSE_RE.search(text) or _STOPPED_RE.search(text) or _TP_HIT_RE.search(text):
        return "trade_update"
    import llm_classify
    if llm_classify.get_update_type(text, CHANNEL_NAME) not in ("noise", "commentary"):
        return "trade_update"
    return "noise"


def parse_signal(msg) -> dict | None:
    text = (msg.text or "").strip()

    em = _ENTRY_RE.search(text)
    if not em:
        return None
    sm = _STOP_RE.search(text)
    xm = _EXIT_RE.search(text)
    if not sm or not xm:
        return None

    direction  = em.group(1).upper()
    instrument = _normalise(em.group(2))
    entry      = float(em.group(3))
    sl         = float(sm.group(1))
    tp         = float(xm.group(1))

    return {
        "signal_id":           str(uuid.uuid4()),
        "telegram_msg_id":     msg.id,
        "message_type":        "new_signal",
        "timestamp":           _iso(msg.date),
        "source_channel_id":   CHANNEL_ID,
        "source_channel_name": CHANNEL_NAME,
        "raw_message":         text,
        "asset_class":         _ASSET_CLASS.get(instrument, "forex"),
        "instrument":          instrument,
        "direction":           direction,
        "order_type":          "market",
        "entry":               entry,
        "entry_range":         None,
        "sl":                  sl,
        "tp":                  [tp],
        "has_image":           msg.media is not None,
        "parse_status":        "parsed",
        "notes":               "",
    }


def parse_update(msg, signal_id: str | None) -> list[dict]:
    """Returns one dict per affected instrument — a single message can close multiple positions."""
    text = (msg.text or "").strip()
    ts   = _iso(msg.date)
    updates = []

    for m in _CLOSE_RE.finditer(text):
        updates.append(_make_update(msg, _normalise(m.group(1)), "full_close", ts))

    for m in _STOPPED_RE.finditer(text):
        updates.append(_make_update(msg, _normalise(m.group(1)), "sl_hit", ts))

    for m in _TP_HIT_RE.finditer(text):
        updates.append(_make_update(msg, _normalise(m.group(1)), "tp_hit", ts))

    if not updates:
        import llm_classify
        update_type = llm_classify.get_update_type(text, CHANNEL_NAME)
        if update_type not in ("noise", "commentary"):
            updates.append(_make_update(msg, None, update_type, ts))

    return updates


def _make_update(msg, instrument: str | None, update_type: str, ts: str) -> dict:
    return {
        "signal_id":                None,   # resolved by listener via instrument lookup
        "telegram_msg_id":          msg.id,
        "telegram_reply_to_msg_id": msg.reply_to_msg_id,
        "message_type":             "trade_update",
        "timestamp":                ts,
        "source_channel_id":        CHANNEL_ID,
        "source_channel_name":      CHANNEL_NAME,
        "raw_message":              msg.text or "",
        "instrument":               instrument,
        "update_type":              update_type,
        "notes":                    "",
    }
