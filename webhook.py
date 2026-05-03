"""
webhook.py — MT5 order execution

Connects to a locally running MetaTrader 5 terminal and places/closes orders
in response to parsed Telegram signals.

Set DRY_RUN=true in .env (default) to log orders without executing.
Set DRY_RUN=false only when ready to trade live.

Required .env vars: MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, DRY_RUN
MT5 terminal must be installed and running on this machine (Windows only).
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ── User-configurable ──────────────────────────────────────────────────────────
ENTRY_TOLERANCE_PIPS = 3        # pips from signal price that still qualifies as market order

PIP_SIZE: dict[str, float] = {  # price value of 1 pip per instrument
    # Majors
    "EURUSD": 0.0001, "USDJPY": 0.01,   "GBPUSD": 0.0001,
    "USDCHF": 0.0001, "AUDUSD": 0.0001, "USDCAD": 0.0001, "NZDUSD": 0.0001,
    # Minors / crosses — JPY pairs are 0.01, everything else 0.0001
    "EURGBP": 0.0001, "EURJPY": 0.01,   "EURCAD": 0.0001,
    "EURCHF": 0.0001, "EURAUD": 0.0001, "EURNZD": 0.0001,
    "GBPJPY": 0.01,   "GBPAUD": 0.0001, "GBPCAD": 0.0001,
    "GBPCHF": 0.0001, "GBPNZD": 0.0001,
    "AUDJPY": 0.01,   "AUDNZD": 0.0001, "AUDCAD": 0.0001, "AUDCHF": 0.0001,
    "NZDJPY": 0.01,   "CADJPY": 0.01,   "CADCHF": 0.0001, "CHFJPY": 0.01,
    # Commodities / crypto / indices
    "XAUUSD": 0.10,   "XAGUSD": 0.01,
    "BTCUSD": 10.0,   "ETHUSD": 1.0,
    "NAS100": 1.0,    "US30":   1.0,    "SPX500": 0.25,
}

LOT_SIZE = 0.01                 # lot size per trade — adjust per risk preference

# Broker-specific symbol names — Exness appends 'm' to all symbols
SYMBOL_MAP: dict[str, str] = {
    # Majors
    "EURUSD": "EURUSDm", "USDJPY": "USDJPYm", "GBPUSD": "GBPUSDm",
    "USDCHF": "USDCHFm", "AUDUSD": "AUDUSDm", "USDCAD": "USDCADm",
    "NZDUSD": "NZDUSDm",
    # Minors / crosses
    "EURGBP": "EURGBPm", "EURJPY": "EURJPYm", "EURCAD": "EURCADm",
    "EURCHF": "EURCHFm", "EURAUD": "EURAUDm", "EURNZD": "EURNZDm",
    "GBPJPY": "GBPJPYm", "GBPAUD": "GBPAUDm", "GBPCAD": "GBPCADm",
    "GBPCHF": "GBPCHFm", "GBPNZD": "GBPNZDm",
    "AUDJPY": "AUDJPYm", "AUDNZD": "AUDNZDm", "AUDCAD": "AUDCADm",
    "AUDCHF": "AUDCHFm", "NZDJPY": "NZDJPYm", "CADJPY": "CADJPYm",
    "CADCHF": "CADCHFm", "CHFJPY": "CHFJPYm",
    # Commodities / crypto / indices
    "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm",
    "BTCUSD": "BTCUSDm", "ETHUSD": "ETHUSDm",
    "NAS100": "NAS100m", "US30":   "US30m",   "SPX500": "SPX500m",
}
# ──────────────────────────────────────────────────────────────────────────────

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

_POSITIONS_FILE = Path("journal/positions.json")

# In-memory map: signal_id → MT5 ticket number (int)
# Ticket covers both pending orders and live positions — MT5 uses the same
# ticket number whether the order is pending or has been filled.
_open: dict[str, int] = {}


# ── MT5 connection ─────────────────────────────────────────────────────────────

_MT5_RETRIES    = 3   # attempts before giving up on a single order action
_MT5_RETRY_WAIT = 5   # seconds between retries

def _connect() -> bool:
    """Connect to the running MT5 terminal with up to _MT5_RETRIES attempts.
    Retries handle the case where MT5 is mid-restart when a signal arrives."""
    login    = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server   = os.getenv("MT5_SERVER")

    kwargs: dict = {}
    if login and password and server:
        kwargs = {"login": int(login), "password": password, "server": server}

    for attempt in range(1, _MT5_RETRIES + 1):
        if mt5.initialize(**kwargs):
            return True
        err = mt5.last_error()
        if attempt < _MT5_RETRIES:
            log.warning(f"MT5 initialize failed (attempt {attempt}/{_MT5_RETRIES}): {err} — retrying in {_MT5_RETRY_WAIT}s")
            time.sleep(_MT5_RETRY_WAIT)
        else:
            log.error(f"MT5 initialize failed after {_MT5_RETRIES} attempts: {err}")
    return False


# ── Persistence ────────────────────────────────────────────────────────────────

def load_state() -> None:
    """Load persisted ticket map from disk and reconcile against live MT5 state.
    Prunes any tickets that no longer exist in MT5 (closed/cancelled while bot
    was down) so stale entries don't cause false close attempts."""
    global _open
    if not _POSITIONS_FILE.exists():
        return

    with open(_POSITIONS_FILE, encoding="utf-8") as f:
        _open = {k: int(v) for k, v in json.load(f).items()}
    log.info(f"Loaded {len(_open)} tracked positions from disk")

    if not _open or DRY_RUN:
        return

    if not _connect():
        log.warning("MT5 unavailable at startup — skipping position reconciliation; stale tickets may exist")
        return

    stale = []
    for signal_id, ticket in _open.items():
        live    = mt5.positions_get(ticket=ticket)
        pending = mt5.orders_get(ticket=ticket)
        if not live and not pending:
            log.warning(f"Startup reconcile: ticket {ticket} not found in MT5 — removing (signal_id={signal_id})")
            stale.append(signal_id)

    for sid in stale:
        _open.pop(sid)

    if stale:
        _save()
        log.info(f"Reconciliation removed {len(stale)} stale ticket(s); {len(_open)} active positions remain")


def _save() -> None:
    with open(_POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(_open, f, indent=2)


# ── Channel-specific entry logic ───────────────────────────────────────────────

def _resolve_thrilokh(signal: dict) -> tuple[int, int, float]:
    """Decide MT5 order type for Vip Thrilokh signals by comparing live price
    to signal entry price. Returns (action, mt5_order_type, price)."""
    direction    = signal["direction"]
    signal_entry = signal["entry"]
    instrument   = signal["instrument"]
    symbol       = SYMBOL_MAP.get(instrument, instrument)
    tolerance    = ENTRY_TOLERANCE_PIPS * PIP_SIZE.get(instrument, 0.0001)

    tick    = mt5.symbol_info_tick(symbol)
    current = tick.ask if direction == "BUY" else tick.bid
    diff    = current - signal_entry

    if abs(diff) <= tolerance:
        mt5_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
        return mt5.TRADE_ACTION_DEAL, mt5_type, current

    if direction == "BUY":
        mt5_type = mt5.ORDER_TYPE_BUY_LIMIT if diff > 0 else mt5.ORDER_TYPE_BUY_STOP
    else:
        mt5_type = mt5.ORDER_TYPE_SELL_LIMIT if diff < 0 else mt5.ORDER_TYPE_SELL_STOP
    return mt5.TRADE_ACTION_PENDING, mt5_type, signal_entry


def _resolve_xauusd(signal: dict) -> tuple[int, int, float]:
    """Resolve MT5 order type for XAUUSD VIP BIG LOTS signals.
    Order type is explicit in the signal text. Returns (action, mt5_order_type, price)."""
    direction  = signal["direction"]
    order_type = signal.get("order_type", "market").lower()

    if signal.get("entry_range"):
        lo, hi = signal["entry_range"]
        price  = hi if direction == "BUY" else lo
    else:
        price = signal["entry"]

    if order_type == "limit":
        mt5_type = mt5.ORDER_TYPE_BUY_LIMIT if direction == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
        return mt5.TRADE_ACTION_PENDING, mt5_type, price

    mt5_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    return mt5.TRADE_ACTION_DEAL, mt5_type, price


# ── Order placement ────────────────────────────────────────────────────────────

async def place_order(signal: dict) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _place_order_sync, signal)


def _place_order_sync(signal: dict) -> None:
    signal_id  = signal["signal_id"]
    instrument = signal["instrument"]
    sl         = signal.get("sl")
    tp1        = signal["tp"][0] if signal.get("tp") else None
    channel_id = signal["source_channel_id"]
    symbol     = SYMBOL_MAP.get(instrument, instrument)

    if DRY_RUN:
        log.info(
            f"[DRY RUN] place_order — {signal['direction']} {instrument} "
            f"SL={sl} TP={tp1} signal_id={signal_id}"
        )
        return

    if not _connect():
        return

    try:
        if channel_id == 2133117224:    # Vip Thrilokh
            action, mt5_type, price = _resolve_thrilokh(signal)
        elif channel_id == 1481325093:  # XAUUSD VIP BIG LOTS
            action, mt5_type, price = _resolve_xauusd(signal)
        else:
            log.warning(f"place_order: no order logic for channel {channel_id}")
            return

        # Market orders use IOC; pending orders use RETURN (partial fill allowed)
        filling = mt5.ORDER_FILLING_IOC if action == mt5.TRADE_ACTION_DEAL else mt5.ORDER_FILLING_RETURN

        request: dict = {
            "action":       action,
            "symbol":       symbol,
            "volume":       LOT_SIZE,
            "type":         mt5_type,
            "price":        price,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }
        if sl  is not None: request["sl"] = sl
        if tp1 is not None: request["tp"] = tp1

        log.info(f"Sending order: {instrument} {signal['direction']} @ {price} SL={sl} TP={tp1} request={request}")
        result = mt5.order_send(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else None
            err     = result.comment if result else mt5.last_error()
            log.error(f"order_send failed retcode={retcode} err={err}: {instrument} {signal['direction']}")
            return

        _open[signal_id] = result.order
        _save()
        log.info(f"Order placed: ticket={result.order} {instrument} {signal['direction']} @ {price}")

    except Exception:
        log.exception(f"place_order failed — signal_id={signal_id}")


# ── Close / cancel ─────────────────────────────────────────────────────────────

async def handle_close(signal_id: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _handle_close_sync, signal_id)


def _handle_close_sync(signal_id: str) -> None:
    if DRY_RUN:
        log.info(f"[DRY RUN] handle_close — signal_id={signal_id}")
        return

    ticket = _open.get(signal_id)
    if not ticket:
        log.warning(f"handle_close: no tracked ticket for signal_id={signal_id}")
        return

    if not _connect():
        return

    try:
        # Check for a live position first
        positions = mt5.positions_get(ticket=ticket)
        if positions:
            pos        = positions[0]
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick       = mt5.symbol_info_tick(pos.symbol)
            price      = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
            request = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       pos.symbol,
                "volume":       pos.volume,
                "type":         close_type,
                "position":     pos.ticket,
                "price":        price,
                "comment":      f"close:{signal_id[:24]}",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                err = result.comment if result else mt5.last_error()
                log.error(f"Close failed ({err}): ticket={ticket}")
                return
            log.info(f"Position closed: ticket={ticket} signal_id={signal_id}")

        else:
            # Check for a pending order
            orders = mt5.orders_get(ticket=ticket)
            if orders:
                result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
                if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                    err = result.comment if result else mt5.last_error()
                    log.error(f"Cancel failed ({err}): ticket={ticket}")
                    return
                log.info(f"Pending order cancelled: ticket={ticket} signal_id={signal_id}")
            else:
                log.warning(f"Ticket {ticket} not found — already closed/cancelled?")

    except Exception:
        log.exception(f"handle_close failed — signal_id={signal_id}")
    finally:
        _open.pop(signal_id, None)
        _save()
