"""
channels/tv_signals.py — parser for TradingView signals forwarded via Telegram.

Vercel's trade-log posts JSON messages to this channel when TradingView fires
an alert. The bot reads them here and routes to MT5 exactly like any other channel.

Entry message:
  {"tv":"entry","id":"MNQ1!-1748000000","ticker":"MNQ1!","action":"buy",
   "price":19500,"sl":19450,"tp1":19600,"tp2":19700}

Exit message:
  {"tv":"exit","id":"MNQ1!-1748000000","ticker":"MNQ1!","action":"exit","tp":"TP1","price":19600}
  {"tv":"exit","id":"MNQ1!-1748000000","ticker":"MNQ1!","action":"sl","price":19450}
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

CHANNEL_NAME = "TradingView Signals"
CHANNEL_ID   = 3720726531

_STATE_FILE = Path("journal/tv_positions.json")

# vercel trade id → bot signal_id
_tv_open: dict[str, str] = {}
# ticker → vercel trade id (quick lookup for exits)
_ticker_to_id: dict[str, str] = {}

TICKER_MAP: dict[str, str] = {
    "MNQ1!": "NAS100", "NQ1!":  "NAS100",
    "MES1!": "SPX500", "ES1!":  "SPX500",
    "MYM1!": "US30",   "YM1!":  "US30",
    "MGC1!": "XAUUSD", "GC1!":  "XAUUSD",
    "XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD",
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY", "AUDUSD": "AUDUSD",
    "USDCAD": "USDCAD", "USDCHF": "USDCHF",
    "NZDUSD": "NZDUSD",
    "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
}

_ASSET_CLASS: dict[str, str] = {
    "NAS100": "index",     "SPX500": "index",    "US30": "index",
    "XAUUSD": "commodity", "XAGUSD": "commodity",
    "BTCUSD": "crypto",    "ETHUSD": "crypto",
}


def load_state() -> None:
    global _tv_open, _ticker_to_id
    if _STATE_FILE.exists():
        with open(_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _tv_open      = data.get("tv_open", {})
        _ticker_to_id = data.get("ticker_to_id", {})
        log.info(f"TV: loaded {len(_tv_open)} open position(s) from disk")


def _save_state() -> None:
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"tv_open": _tv_open, "ticker_to_id": _ticker_to_id}, f, indent=2)


def _parse_payload(msg) -> dict | None:
    text = (msg.text or "").strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if payload.get("tv") not in ("entry", "exit"):
        return None
    return payload


def _iso(dt) -> str:
    if dt is None:
        return datetime.now(timezone.utc).isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def classify(msg) -> str:
    payload = _parse_payload(msg)
    if not payload:
        return "noise"
    action = payload.get("action", "")
    if action in ("buy", "sell"):
        return "new_signal"
    if action in ("exit", "sl", "close"):
        return "trade_update"
    return "noise"


def parse_signal(msg) -> dict | None:
    payload = _parse_payload(msg)
    if not payload:
        return None

    ticker     = payload.get("ticker", "")
    instrument = TICKER_MAP.get(ticker, ticker.replace("1!", ""))
    direction  = payload["action"].upper()
    tp_prices  = [float(payload[k]) for k in ("tp1", "tp2", "tp3") if payload.get(k)]
    sl         = float(payload["sl"]) if payload.get("sl") else None
    entry_px   = float(payload.get("price", 0))
    vercel_id  = payload.get("id", f"{ticker}-{msg.id}")

    # Deduplicate: TradingView sometimes double-fires the same alert
    if vercel_id in _tv_open:
        log.warning(f"TV: duplicate signal ignored — vercel_id={vercel_id} already tracked")
        return None

    signal_id  = str(uuid.uuid4())
    _tv_open[vercel_id]   = signal_id
    _ticker_to_id[ticker] = vercel_id
    _save_state()

    return {
        "signal_id":           signal_id,
        "telegram_msg_id":     msg.id,
        "message_type":        "new_signal",
        "timestamp":           _iso(msg.date),
        "source_channel_id":   CHANNEL_ID,
        "source_channel_name": CHANNEL_NAME,
        "raw_message":         msg.text,
        "asset_class":         _ASSET_CLASS.get(instrument, "forex"),
        "instrument":          instrument,
        "direction":           direction,
        "order_type":          "market",
        "entry":               entry_px,
        "entry_range":         None,
        "sl":                  sl,
        "tp":                  tp_prices,
        "has_image":           False,
        "parse_status":        "parsed" if sl and tp_prices else "partial",
        "notes":               f"vercel_id={vercel_id}",
    }


def parse_update(msg, signal_id: str | None) -> dict | None:
    payload = _parse_payload(msg)
    if not payload:
        return None

    ticker    = payload.get("ticker", "")
    action    = payload.get("action", "")
    tp_label  = (payload.get("tp") or "").upper()
    vercel_id = payload.get("id") or _ticker_to_id.get(ticker)

    # Override the journal's signal_id with our ticker-specific mapping
    if vercel_id and vercel_id in _tv_open:
        signal_id = _tv_open[vercel_id]

    if tp_label == "TP1":
        update_type = "tp_hit"
    elif action == "sl":
        update_type = "sl_hit"
    else:
        update_type = "full_close"

    if update_type in ("full_close", "sl_hit") and vercel_id:
        _tv_open.pop(vercel_id, None)
        _ticker_to_id.pop(ticker, None)
        _save_state()

    return {
        "signal_id":                signal_id,
        "telegram_msg_id":          msg.id,
        "telegram_reply_to_msg_id": msg.reply_to_msg_id,
        "message_type":             "trade_update",
        "timestamp":                _iso(msg.date),
        "source_channel_id":        CHANNEL_ID,
        "source_channel_name":      CHANNEL_NAME,
        "raw_message":              msg.text,
        "instrument":               TICKER_MAP.get(ticker, ticker),
        "update_type":              update_type,
        "notes":                    f"vercel_id={vercel_id}",
    }
