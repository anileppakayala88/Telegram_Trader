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
# Pips from signal price that still qualifies as a market order, per instrument.
# Tighter for stable forex; wider for volatile crypto/indices.
ENTRY_TOLERANCE_PIPS: dict[str, float] = {
    # Forex majors / minors — 3 pips
    "EURUSD": 3, "GBPUSD": 3, "AUDUSD": 3, "NZDUSD": 3,
    "USDCAD": 3, "USDCHF": 3, "USDJPY": 3,
    "EURGBP": 3, "EURJPY": 3, "EURCAD": 3, "EURCHF": 3,
    "EURAUD": 3, "EURNZD": 3, "GBPJPY": 3, "GBPAUD": 3,
    "GBPCAD": 3, "GBPCHF": 3, "GBPNZD": 3, "AUDJPY": 3,
    "AUDNZD": 3, "AUDCAD": 3, "AUDCHF": 3, "NZDJPY": 3,
    "CADJPY": 3, "CADCHF": 3, "CHFJPY": 3,
    # Commodities — wider due to spread + volatility
    "XAUUSD": 5, "XAGUSD": 5,
    # Indices
    "NAS100": 10, "US30": 10, "SPX500": 5,
    # Crypto — very wide; 30-pt swings are noise
    "BTCUSD": 50, "ETHUSD": 20,
}
_DEFAULT_TOLERANCE_PIPS = 3     # fallback for any symbol not listed above

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

LOT_SIZE = 0.01                 # lot size per TP order — each TP level gets its own order at this size

# TP order configuration
# TP1 and TP2 are always placed when available in the signal.
# Set USE_TP3=true in .env to also place a third order targeting TP3.
# If the signal has fewer TPs than the active limit, only available TPs are used.
USE_TP3 = os.getenv("USE_TP3", "false").lower() == "true"
_MAX_TP_ORDERS = 3 if USE_TP3 else 2

# Place one order per range price (one at each price in the entry range).
# Each order is paired with a TP level: first range price → TP1, second → TP2.
# Only applies to XAUUSD VIP BIG LOTS limit orders with a range (e.g. 4583/4590).
# Set SPLIT_RANGE_ENTRIES=false to use the old behaviour (single entry, one order per TP).
SPLIT_RANGE_ENTRIES = os.getenv("SPLIT_RANGE_ENTRIES", "true").lower() == "true"

# After TP1 is hit, move the SL of remaining open positions to their fill price (breakeven).
# Set MOVE_SL_TO_BE_ON_TP1=false to close all remaining positions on tp_hit instead.
MOVE_SL_TO_BE_ON_TP1 = os.getenv("MOVE_SL_TO_BE_ON_TP1", "true").lower() == "true"

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

# In-memory map: signal_id → list of MT5 ticket numbers (one per TP order).
# Multiple tickets per signal because each TP level is a separate order.
# MT5 uses the same ticket number whether an order is pending or filled.
_open: dict[str, list[int]] = {}


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
    Migrates old single-ticket format (signal_id → int) to list format automatically.
    Prunes tickets that no longer exist in MT5 (closed while bot was down)."""
    global _open
    if not _POSITIONS_FILE.exists():
        return

    with open(_POSITIONS_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    # Migrate: old format stored a single int; new format stores a list
    _open = {}
    for k, v in raw.items():
        if isinstance(v, list):
            _open[k] = [int(t) for t in v]
        else:
            _open[k] = [int(v)]

    log.info(f"Loaded {len(_open)} tracked signals ({sum(len(t) for t in _open.values())} tickets) from disk")

    if not _open or DRY_RUN:
        return

    if not _connect():
        log.warning("MT5 unavailable at startup — skipping position reconciliation; stale tickets may exist")
        return

    stale_signals = []
    for signal_id, tickets in _open.items():
        remaining = []
        for ticket in tickets:
            live    = mt5.positions_get(ticket=ticket)
            pending = mt5.orders_get(ticket=ticket)
            if live or pending:
                remaining.append(ticket)
            else:
                log.warning(f"Startup reconcile: ticket {ticket} not found in MT5 — removing (signal_id={signal_id})")
        if remaining:
            _open[signal_id] = remaining
        else:
            stale_signals.append(signal_id)

    for sid in stale_signals:
        _open.pop(sid)

    if stale_signals:
        _save()
        log.info(f"Reconciliation removed {len(stale_signals)} fully closed signal(s); {len(_open)} active remain")


def _save() -> None:
    with open(_POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(_open, f, indent=2)


# ── Channel-specific entry logic ───────────────────────────────────────────────

def _resolve_order_type(direction: str, instrument: str, symbol: str, entry_price: float) -> tuple[int, int, float]:
    """Determine MT5 action, order type, and execution price by comparing the
    live market price to a signal entry price.

    Returns (action, mt5_order_type, price):
      - market order if within tolerance  → price is current bid/ask
      - pending limit/stop otherwise      → price is the signal entry
    """
    tol_pips  = ENTRY_TOLERANCE_PIPS.get(instrument, _DEFAULT_TOLERANCE_PIPS)
    tolerance = tol_pips * PIP_SIZE.get(instrument, 0.0001)

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick data for {symbol} — is the symbol subscribed in MT5?")
    current = tick.ask if direction == "BUY" else tick.bid
    diff    = current - entry_price

    if abs(diff) <= tolerance:
        mt5_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
        return mt5.TRADE_ACTION_DEAL, mt5_type, current

    if direction == "BUY":
        mt5_type = mt5.ORDER_TYPE_BUY_LIMIT if diff > 0 else mt5.ORDER_TYPE_BUY_STOP
    else:
        mt5_type = mt5.ORDER_TYPE_SELL_LIMIT if diff < 0 else mt5.ORDER_TYPE_SELL_STOP
    return mt5.TRADE_ACTION_PENDING, mt5_type, entry_price


def _resolve_thrilokh(signal: dict) -> tuple[int, int, float]:
    """Decide MT5 order type for Vip Thrilokh signals. Returns (action, mt5_order_type, price)."""
    instrument = signal["instrument"]
    return _resolve_order_type(
        signal["direction"],
        instrument,
        SYMBOL_MAP.get(instrument, instrument),
        signal["entry"],
    )


def _resolve_xauusd(signal: dict) -> list[tuple[int, int, float]]:
    """Resolve MT5 order types for XAUUSD VIP BIG LOTS signals.

    Uses live price comparison (same logic as Vip Thrilokh) to determine the
    actual order type for each entry price — ignoring the explicit type in the
    signal text. Each entry price independently resolves to market, limit, or stop.

    With SPLIT_RANGE_ENTRIES and a range, returns two tuples (one per price):
      BUY  range → [hi, lo]  — hi fills first as price drops into the range
      SELL range → [lo, hi]  — lo fills first as price rises into the range
    """
    direction  = signal["direction"]
    instrument = signal["instrument"]
    symbol     = SYMBOL_MAP.get(instrument, instrument)

    if signal.get("entry_range") and SPLIT_RANGE_ENTRIES:
        lo, hi = signal["entry_range"]
        entry_prices = [hi, lo] if direction == "BUY" else [lo, hi]
    elif signal.get("entry_range"):
        lo, hi = signal["entry_range"]
        entry_prices = [hi if direction == "BUY" else lo]
    else:
        entry_prices = [signal["entry"]]

    return [_resolve_order_type(direction, instrument, symbol, p) for p in entry_prices]


# ── Order placement ────────────────────────────────────────────────────────────

async def place_order(signal: dict) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _place_order_sync, signal)


def _place_order_sync(signal: dict) -> None:
    signal_id  = signal["signal_id"]
    instrument = signal["instrument"]
    sl         = signal.get("sl")
    tps        = signal.get("tp") or []
    channel_id = signal["source_channel_id"]
    symbol     = SYMBOL_MAP.get(instrument, instrument)

    if not tps or sl is None:
        log.warning(
            f"place_order skipped — signal must have both SL and at least one TP "
            f"(sl={sl}, tps={tps}) signal_id={signal_id}"
        )
        return

    tp_levels = tps[:_MAX_TP_ORDERS]

    if DRY_RUN:
        # No MT5 connection in dry-run — show signal entry prices only (type resolved at live execution)
        if channel_id == 1481325093 and signal.get("entry_range") and SPLIT_RANGE_ENTRIES:
            lo, hi = signal["entry_range"]
            prices = [hi, lo] if signal["direction"] == "BUY" else [lo, hi]
            # Each entry price paired with its own TP; same zip logic as live path
            dry_pairs = list(zip(prices, tp_levels))
        else:
            dry_pairs = [(signal["entry"], tp) for tp in tp_levels]
        for i, (price, tp) in enumerate(dry_pairs, 1):
            log.info(
                f"[DRY RUN] place_order TP{i}/{len(dry_pairs)} — "
                f"{signal['direction']} {instrument} @ {price} SL={sl} TP={tp} signal_id={signal_id}"
            )
        return

    if not _connect():
        return

    try:
        if channel_id == 2133117224:    # Vip Thrilokh — single entry, one order per TP
            spec   = _resolve_thrilokh(signal)
            orders_tps = [(spec, tp) for tp in tp_levels]
        elif channel_id == 1481325093:  # XAUUSD VIP BIG LOTS — live-price resolution per entry
            specs  = _resolve_xauusd(signal)   # list of (action, mt5_type, price)
            if len(specs) == 1:
                # Single entry — replicate for each TP level (same as Thrilokh)
                orders_tps = [(specs[0], tp) for tp in tp_levels]
            else:
                # Split range — each entry price paired with its own TP level
                orders_tps = list(zip(specs, tp_levels))
        else:
            log.warning(f"place_order: no order logic for channel {channel_id}")
            return

        tickets = []
        for i, ((action, mt5_type, price), tp) in enumerate(orders_tps, 1):
            # Market orders use IOC; pending orders use RETURN (partial fill allowed)
            filling = mt5.ORDER_FILLING_IOC if action == mt5.TRADE_ACTION_DEAL else mt5.ORDER_FILLING_RETURN
            request: dict = {
                "action":       action,
                "symbol":       symbol,
                "volume":       LOT_SIZE,
                "type":         mt5_type,
                "price":        price,
                "sl":           sl,
                "tp":           tp,
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            if action == mt5.TRADE_ACTION_DEAL:
                request["deviation"] = 20

            log.info(f"Sending TP{i}/{len(orders_tps)} order: {instrument} {signal['direction']} @ {price} SL={sl} TP={tp}")
            result = mt5.order_send(request)

            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                retcode = result.retcode if result else None
                err     = result.comment if result else mt5.last_error()
                log.error(f"order_send TP{i} failed retcode={retcode} err={err}: {instrument} {signal['direction']}")
                continue

            tickets.append(result.order)
            log.info(f"TP{i} order placed: ticket={result.order} {instrument} {signal['direction']} @ {price} TP={tp}")

        if tickets:
            _open[signal_id] = tickets
            _save()
            log.info(f"Signal {signal_id}: {len(tickets)}/{len(orders_tps)} orders placed — tickets={tickets}")

    except Exception:
        log.exception(f"place_order failed — signal_id={signal_id}")
    finally:
        mt5.shutdown()


# ── TP1 hit — move remaining positions to breakeven ───────────────────────────

async def handle_tp_hit(signal_id: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _handle_tp_hit_sync, signal_id)


def _handle_tp_hit_sync(signal_id: str) -> None:
    if DRY_RUN:
        log.info(f"[DRY RUN] handle_tp_hit — signal_id={signal_id}")
        return

    if not MOVE_SL_TO_BE_ON_TP1:
        # Fallback: close all remaining positions (original tp_hit behaviour)
        _handle_close_sync(signal_id)
        return

    tickets = _open.get(signal_id)
    if not tickets:
        log.warning(f"handle_tp_hit: no tracked tickets for signal_id={signal_id}")
        return

    if not _connect():
        return

    try:
        remaining = []
        for ticket in list(tickets):
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pos = positions[0]
                result = mt5.order_send({
                    "action":   mt5.TRADE_ACTION_SLTP,
                    "position": pos.ticket,
                    "sl":       pos.price_open,
                    "tp":       pos.tp,
                })
                if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                    err = result.comment if result else mt5.last_error()
                    log.error(f"Move SL to BE failed ({err}): ticket={ticket}")
                else:
                    log.info(f"SL moved to BE ({pos.price_open}): ticket={ticket} signal_id={signal_id}")
                remaining.append(ticket)
            else:
                orders = mt5.orders_get(ticket=ticket)
                if orders:
                    # Pending order never filled — cancel it
                    result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
                    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                        err = result.comment if result else mt5.last_error()
                        log.error(f"Cancel pending on tp_hit failed ({err}): ticket={ticket}")
                    else:
                        log.info(f"Pending order cancelled on tp_hit: ticket={ticket} signal_id={signal_id}")
                else:
                    # Already auto-closed by MT5 when its TP was reached
                    log.info(f"Ticket {ticket} closed by MT5 (TP reached): signal_id={signal_id}")

        if remaining:
            _open[signal_id] = remaining
        else:
            _open.pop(signal_id, None)
        _save()

    except Exception:
        log.exception(f"handle_tp_hit failed — signal_id={signal_id}")


# ── Close / cancel ─────────────────────────────────────────────────────────────

async def handle_close(signal_id: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _handle_close_sync, signal_id)


def _handle_close_sync(signal_id: str) -> None:
    if DRY_RUN:
        log.info(f"[DRY RUN] handle_close — signal_id={signal_id}")
        return

    tickets = _open.get(signal_id)
    if not tickets:
        log.warning(f"handle_close: no tracked tickets for signal_id={signal_id}")
        return

    if not _connect():
        return

    try:
        for ticket in list(tickets):
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
                    "deviation":    20,
                    "type_time":    mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                result = mt5.order_send(request)
                if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                    err = result.comment if result else mt5.last_error()
                    log.error(f"Close failed ({err}): ticket={ticket}")
                else:
                    log.info(f"Position closed: ticket={ticket} signal_id={signal_id}")

            else:
                # Check for a pending order
                orders = mt5.orders_get(ticket=ticket)
                if orders:
                    result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
                    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                        err = result.comment if result else mt5.last_error()
                        log.error(f"Cancel failed ({err}): ticket={ticket}")
                    else:
                        log.info(f"Pending order cancelled: ticket={ticket} signal_id={signal_id}")
                else:
                    log.warning(f"Ticket {ticket} not found — already closed/cancelled?")

    except Exception:
        log.exception(f"handle_close failed — signal_id={signal_id}")
    finally:
        mt5.shutdown()
        _open.pop(signal_id, None)
        _save()
