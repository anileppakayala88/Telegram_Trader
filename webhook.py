"""
webhook.py — Phase 2 order execution via MetaAPI

Flow:
  Telegram signal parsed by listener.py
      → place_order(signal)   fires a market/limit/stop order on the broker
      → handle_close(signal_id)  closes or cancels when an exit update arrives

MetaAPI is a cloud bridge: your MT4/MT5 account credentials are registered once
on app.metaapi.cloud, and this module talks to their REST/WebSocket API instead
of running a local MT5 terminal.

Each channel has its own entry logic:
  - Vip Thrilokh  : compares live price to signal price → market / limit / stop
  - XAUUSD Big Lots: reads order type directly from the signal text

Set DRY_RUN=true in .env to log orders without executing them (safe for testing).
Required .env vars: METAAPI_TOKEN, METAAPI_ACCOUNT_ID, DRY_RUN
"""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# ── User-configurable ──────────────────────────────────────────────────────────
ENTRY_TOLERANCE_PIPS = 3        # pips from signal price that still qualifies as market order
                                # increase to widen the window, decrease to be stricter

PIP_SIZE = {                    # price value of 1 pip per instrument
    "EURUSD": 0.0001,           # standard 4-decimal forex pairs
    "USDCAD": 0.0001,
    "AUDUSD": 0.0001,
    "USDCHF": 0.0001,
    "USDJPY": 0.01,             # JPY pairs are 2-decimal
    "XAUUSD": 0.10,             # gold quoted to 2dp — 1 pip = $0.10
    "NAS100": 1.0,              # index points
    "BTCUSD": 10.0,             # crypto — 1 pip = $10
}

LOT_SIZE = 0.01                 # lot size per trade — adjust per risk preference
# ──────────────────────────────────────────────────────────────────────────────

DRY_RUN            = os.getenv("DRY_RUN", "true").lower() == "true"
METAAPI_TOKEN      = os.getenv("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

_THRILOKH_ID = 2133117224       # Vip Thrilokh Telegram channel ID
_XAUUSD_ID   = 1481325093       # XAUUSD VIP BIG LOTS Telegram channel ID

_POSITIONS_FILE = Path("journal/positions.json")

# In-memory map: signal_id → {position_id, order_id, instrument}
# Market orders  → broker returns both positionId and orderId (same value)
# Pending orders → broker returns only orderId (no positionId until filled)
# This distinction drives whether handle_close() calls close_position or cancel_order
_open: dict[str, dict] = {}

_connection = None


# ── Connection ─────────────────────────────────────────────────────────────────

async def _get_connection():
    """Lazy-initialise and return the MetaAPI RPC connection.

    MetaApi is imported here (not at module top) so that the SDK is never
    loaded when DRY_RUN=true — keeping startup fast and dependency-free
    during testing.  Connection is reused across all subsequent calls.
    """
    global _connection
    if _connection is not None:
        return _connection
    from metaapi_cloud_sdk import MetaApi
    api     = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    conn    = account.get_rpc_connection()
    await conn.connect()
    await conn.wait_synchronized()  # blocks until terminal state is fully synced
    _connection = conn
    log.info("MetaAPI connected")
    return _connection


# ── Position tracking ──────────────────────────────────────────────────────────

def load_positions():
    """Load persisted position map from disk on startup.

    Called once from main.py so that open positions survive a bot restart —
    without this, handle_close() would have no record of orders placed before
    the restart and couldn't close them.
    """
    global _open
    if _POSITIONS_FILE.exists():
        with open(_POSITIONS_FILE, encoding="utf-8") as f:
            _open = json.load(f)
        log.info(f"Loaded {len(_open)} tracked positions")

def _save():
    """Persist the in-memory position map to disk after every change."""
    with open(_POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(_open, f, indent=2)

def _track(signal_id: str, result: dict, instrument: str):
    """Store the broker's position/order IDs against our signal_id.

    MetaAPI returns:
      positionId — set for market orders (the live position on the account)
      orderId    — set for all orders; for pending orders this is the only ID
                   until the order is filled and becomes a position
    Both are stored so handle_close() can choose the right action.
    """
    _open[signal_id] = {
        "instrument":  instrument,
        "position_id": result.get("positionId"),
        "order_id":    result.get("orderId"),
    }
    _save()
    log.info(f"Tracked {signal_id} → pos={result.get('positionId')} ord={result.get('orderId')}")


# ── Channel-specific entry logic ───────────────────────────────────────────────

async def _resolve_thrilokh(conn, signal: dict) -> tuple[str, float]:
    """Decide order type for Vip Thrilokh signals by comparing live price to signal price.

    Within tolerance  → market order (enter now)
    BUY,  price above → BUY_LIMIT  (price ran up; wait for pullback to signal level)
    BUY,  price below → BUY_STOP   (price hasn't reached signal level yet; enter on rise)
    SELL, price below → SELL_LIMIT (price dropped; wait for pullback up to signal level)
    SELL, price above → SELL_STOP  (price hasn't dropped to signal level yet; enter on fall)
    """
    direction  = signal["direction"]
    entry      = signal["entry"]
    instrument = signal["instrument"]
    tolerance  = ENTRY_TOLERANCE_PIPS * PIP_SIZE.get(instrument, 0.0001)

    price_data = await conn.get_symbol_price(instrument)
    # use ask for BUY (what you pay), bid for SELL (what you receive)
    current    = price_data["ask"] if direction == "BUY" else price_data["bid"]
    diff       = current - entry    # positive = market is above signal entry

    if abs(diff) <= tolerance:
        return f"MARKET_{direction}", entry
    if direction == "BUY":
        return ("BUY_LIMIT" if diff > 0 else "BUY_STOP"), entry
    else:
        return ("SELL_LIMIT" if diff < 0 else "SELL_STOP"), entry


def _resolve_xauusd(signal: dict) -> tuple[str, float]:
    """Resolve order type and entry price for XAUUSD VIP BIG LOTS signals.

    Order type is explicit in the signal text (buy limit / sell limit / market).
    Entry range (e.g. 4664/4656): use the price closer to market for quicker fill —
      BUY limit  → higher of the two (less of a pullback needed to trigger)
      SELL limit → lower  of the two (less of a rally needed to trigger)
    """
    direction      = signal["direction"]
    order_type_str = signal.get("order_type", "market").lower()

    if signal.get("entry_range"):
        lo, hi = signal["entry_range"]
        entry  = hi if direction == "BUY" else lo
    else:
        entry = signal["entry"]

    if order_type_str == "limit":
        return f"{direction}_LIMIT", entry
    return f"MARKET_{direction}", entry


# ── Order execution ────────────────────────────────────────────────────────────

async def _execute(conn, order_type: str, instrument: str,
                   lots: float, entry: float, sl, tp) -> dict:
    """Send the order to the broker via MetaAPI and return the broker result.

    sl / tp are passed as keyword args so they can be omitted when None —
    MetaAPI raises an error if None is passed explicitly for these fields.
    """
    kw = {}
    if sl is not None: kw["stop_loss"]   = sl
    if tp is not None: kw["take_profit"] = tp

    if   order_type == "MARKET_BUY":  return await conn.create_market_buy_order(instrument, lots, **kw)
    elif order_type == "MARKET_SELL": return await conn.create_market_sell_order(instrument, lots, **kw)
    elif order_type == "BUY_LIMIT":   return await conn.create_limit_buy_order(instrument, lots, entry, **kw)
    elif order_type == "SELL_LIMIT":  return await conn.create_limit_sell_order(instrument, lots, entry, **kw)
    elif order_type == "BUY_STOP":    return await conn.create_stop_buy_order(instrument, lots, entry, **kw)
    elif order_type == "SELL_STOP":   return await conn.create_stop_sell_order(instrument, lots, entry, **kw)
    else: raise ValueError(f"Unknown order_type: {order_type}")


async def place_order(signal: dict) -> None:
    """Entry point called by listener.py when a new_signal is parsed.

    Routes to the correct channel logic, determines order type, fires the order,
    and stores the broker's position/order ID for later close/cancel.
    Errors are caught and logged — a failed order never crashes the listener.
    """
    signal_id  = signal["signal_id"]
    instrument = signal["instrument"]
    sl         = signal.get("sl")
    tp         = signal["tp"][0] if signal.get("tp") else None   # TP1 only for now
    channel_id = signal["source_channel_id"]

    if DRY_RUN:
        log.info(f"[DRY RUN] place_order — {signal['direction']} {instrument} "
                 f"SL={sl} TP={tp} signal_id={signal_id}")
        return

    try:
        conn = await _get_connection()

        if channel_id == _THRILOKH_ID:
            order_type, entry = await _resolve_thrilokh(conn, signal)
        elif channel_id == _XAUUSD_ID:
            order_type, entry = _resolve_xauusd(signal)
        else:
            log.warning(f"place_order: no order logic for channel {channel_id}")
            return

        log.info(f"{order_type} {instrument} @ {entry} SL={sl} TP={tp} lots={LOT_SIZE}")
        result = await _execute(conn, order_type, instrument, LOT_SIZE, entry, sl, tp)
        _track(signal_id, result, instrument)

    except Exception:
        log.exception(f"place_order failed — signal_id={signal_id}")


# ── Close / cancel ─────────────────────────────────────────────────────────────

async def handle_close(signal_id: str) -> None:
    """Close an open position or cancel a pending order when an exit update arrives.

    Distinguishes between the two cases using what was stored at order time:
      position_id present → market order was filled → close the live position
      only order_id       → pending order not yet filled → cancel it

    The finally block always removes the signal from _open and saves to disk,
    even if the broker call fails (e.g. position already closed by SL/TP).
    This prevents stale entries from accumulating in the tracker.
    """
    tracked = _open.get(signal_id)
    if not tracked:
        log.warning(f"handle_close: nothing tracked for signal_id={signal_id}")
        return

    if DRY_RUN:
        log.info(f"[DRY RUN] handle_close — signal_id={signal_id}")
        return

    try:
        conn        = await _get_connection()
        position_id = tracked.get("position_id")
        order_id    = tracked.get("order_id")

        if position_id:
            await conn.close_position(position_id)
            log.info(f"Closed position {position_id} for signal {signal_id}")
        elif order_id:
            await conn.cancel_order(order_id)
            log.info(f"Cancelled order {order_id} for signal {signal_id}")

    except Exception:
        log.exception(f"handle_close failed — signal_id={signal_id}")
    finally:
        _open.pop(signal_id, None)
        _save()
