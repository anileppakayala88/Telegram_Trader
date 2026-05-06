"""
webhook.py — MT5 order execution (multi-account)

Routes signals to MT5 accounts by asset class. Each asset class has two
accounts; both receive the same trade. Orders are placed sequentially —
connect → trade → shutdown → next account — because the MT5 Python library
supports one terminal connection at a time.

Account config lives in the ACCOUNTS list below. Credentials are loaded from
.env (dummy defaults are in place until real creds are provided). Circuit-
breaker state is persisted to journal/account_state.json.

Required .env vars per account group (see ACCOUNTS):
    MT5_COMMODITY_1_LOGIN / _PASSWORD / _SERVER
    MT5_COMMODITY_2_LOGIN / _PASSWORD / _SERVER
    MT5_FOREX_1_LOGIN     / _PASSWORD / _SERVER
    MT5_FOREX_2_LOGIN     / _PASSWORD / _SERVER
    MT5_INDEX_1_LOGIN     / _PASSWORD / _SERVER
    MT5_INDEX_2_LOGIN     / _PASSWORD / _SERVER

Risk per account group (shared between the two accounts in each group):
    MT5_COMMODITY_RISK_PCT=1.0   (% of equity; ignored when RISK_USD is set)
    MT5_COMMODITY_RISK_USD=      (fixed $ per trade; overrides RISK_PCT if set)
    MT5_FOREX_RISK_PCT=1.0
    MT5_FOREX_RISK_USD=
    MT5_INDEX_RISK_PCT=1.0
    MT5_INDEX_RISK_USD=

Other flags:
    DRY_RUN=true
    USE_TP3=false
    SPLIT_RANGE_ENTRIES=true
    MOVE_SL_TO_BE_ON_TP1=true
    CIRCUIT_BREAKER_SL_LIMIT=3
"""

import asyncio
import json
import logging
import os
import time
from datetime import date
from pathlib import Path

import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ── Market / tolerance config (unchanged from single-account version) ──────────

ENTRY_TOLERANCE_PIPS: dict[str, float] = {
    "EURUSD": 3, "GBPUSD": 3, "AUDUSD": 3, "NZDUSD": 3,
    "USDCAD": 3, "USDCHF": 3, "USDJPY": 3,
    "EURGBP": 3, "EURJPY": 3, "EURCAD": 3, "EURCHF": 3,
    "EURAUD": 3, "EURNZD": 3, "GBPJPY": 3, "GBPAUD": 3,
    "GBPCAD": 3, "GBPCHF": 3, "GBPNZD": 3, "AUDJPY": 3,
    "AUDNZD": 3, "AUDCAD": 3, "AUDCHF": 3, "NZDJPY": 3,
    "CADJPY": 3, "CADCHF": 3, "CHFJPY": 3,
    "XAUUSD": 5, "XAGUSD": 5,
    "NAS100": 10, "US30": 10, "SPX500": 5,
    "BTCUSD": 50, "ETHUSD": 20,
}
_DEFAULT_TOLERANCE_PIPS = 3

PIP_SIZE: dict[str, float] = {
    "EURUSD": 0.0001, "USDJPY": 0.01,   "GBPUSD": 0.0001,
    "USDCHF": 0.0001, "AUDUSD": 0.0001, "USDCAD": 0.0001, "NZDUSD": 0.0001,
    "EURGBP": 0.0001, "EURJPY": 0.01,   "EURCAD": 0.0001,
    "EURCHF": 0.0001, "EURAUD": 0.0001, "EURNZD": 0.0001,
    "GBPJPY": 0.01,   "GBPAUD": 0.0001, "GBPCAD": 0.0001,
    "GBPCHF": 0.0001, "GBPNZD": 0.0001,
    "AUDJPY": 0.01,   "AUDNZD": 0.0001, "AUDCAD": 0.0001, "AUDCHF": 0.0001,
    "NZDJPY": 0.01,   "CADJPY": 0.01,   "CADCHF": 0.0001, "CHFJPY": 0.01,
    "XAUUSD": 0.10,   "XAGUSD": 0.01,
    "BTCUSD": 10.0,   "ETHUSD": 1.0,
    "NAS100": 1.0,    "US30":   1.0,    "SPX500": 0.25,
}

# Broker-specific symbol names — Exness appends 'm' to all symbols
SYMBOL_MAP: dict[str, str] = {
    "EURUSD": "EURUSDm", "USDJPY": "USDJPYm", "GBPUSD": "GBPUSDm",
    "USDCHF": "USDCHFm", "AUDUSD": "AUDUSDm", "USDCAD": "USDCADm",
    "NZDUSD": "NZDUSDm",
    "EURGBP": "EURGBPm", "EURJPY": "EURJPYm", "EURCAD": "EURCADm",
    "EURCHF": "EURCHFm", "EURAUD": "EURAUDm", "EURNZD": "EURNZDm",
    "GBPJPY": "GBPJPYm", "GBPAUD": "GBPAUDm", "GBPCAD": "GBPCADm",
    "GBPCHF": "GBPCHFm", "GBPNZD": "GBPNZDm",
    "AUDJPY": "AUDJPYm", "AUDNZD": "AUDNZDm", "AUDCAD": "AUDCADm",
    "AUDCHF": "AUDCHFm", "NZDJPY": "NZDJPYm", "CADJPY": "CADJPYm",
    "CADCHF": "CADCHFm", "CHFJPY": "CHFJPYm",
    "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm",
    "BTCUSD": "BTCUSDm", "ETHUSD": "ETHUSDm",
    "NAS100": "NAS100m", "US30":   "US30m",   "SPX500": "SPX500m",
}

# ── Feature flags ──────────────────────────────────────────────────────────────

USE_TP3              = os.getenv("USE_TP3",              "false").lower() == "true"
_MAX_TP_ORDERS       = 3 if USE_TP3 else 2
SPLIT_RANGE_ENTRIES  = os.getenv("SPLIT_RANGE_ENTRIES",  "true").lower()  == "true"
MOVE_SL_TO_BE_ON_TP1 = os.getenv("MOVE_SL_TO_BE_ON_TP1","true").lower()  == "true"

# Number of consecutive SL hits on one account before it is halted for the day.
# Change CIRCUIT_BREAKER_SL_LIMIT in .env to adjust without touching code.
CIRCUIT_BREAKER_SL_LIMIT = int(os.getenv("CIRCUIT_BREAKER_SL_LIMIT", "3"))

# ── Account configuration ──────────────────────────────────────────────────────
# To add an account: copy an entry, change name/login/password/server/asset_classes.
# To disable an account without removing it: set its login to 0 — place_order
# will skip it (no valid MT5 account number).
#
# risk_pct  : risk per trade as % of current equity
# risk_usd  : fixed risk per trade in account currency (overrides risk_pct when set)
#
# The runtime state fields (halted / consecutive_sl / last_reset_date) are
# initialised here and then overwritten by _load_account_state() on startup.

def _opt_float(val: str | None) -> float | None:
    try:
        return float(val) if val else None
    except ValueError:
        return None


ACCOUNTS: list[dict] = [
    # ── Commodity (XAUUSD, XAGUSD) ────────────────────────────────────────────
    {
        "name":           "commodity_1",
        "login":          int(os.getenv("MT5_COMMODITY_1_LOGIN",    "10000001")),
        "password":       os.getenv("MT5_COMMODITY_1_PASSWORD",     "dummy"),
        "server":         os.getenv("MT5_COMMODITY_1_SERVER",       "Exness-MT5Trial"),
        "asset_classes":  ["commodity"],
        "risk_pct":       float(os.getenv("MT5_COMMODITY_RISK_PCT", "1.0")),
        "risk_usd":       _opt_float(os.getenv("MT5_COMMODITY_RISK_USD")),
        "halted": False, "consecutive_sl": 0, "last_reset_date": None,
    },
    {
        "name":           "commodity_2",
        "login":          int(os.getenv("MT5_COMMODITY_2_LOGIN",    "10000002")),
        "password":       os.getenv("MT5_COMMODITY_2_PASSWORD",     "dummy"),
        "server":         os.getenv("MT5_COMMODITY_2_SERVER",       "Exness-MT5Trial"),
        "asset_classes":  ["commodity"],
        "risk_pct":       float(os.getenv("MT5_COMMODITY_RISK_PCT", "1.0")),
        "risk_usd":       _opt_float(os.getenv("MT5_COMMODITY_RISK_USD")),
        "halted": False, "consecutive_sl": 0, "last_reset_date": None,
    },
    # ── Forex ─────────────────────────────────────────────────────────────────
    {
        "name":           "forex_1",
        "login":          int(os.getenv("MT5_FOREX_1_LOGIN",        "10000003")),
        "password":       os.getenv("MT5_FOREX_1_PASSWORD",         "dummy"),
        "server":         os.getenv("MT5_FOREX_1_SERVER",           "Exness-MT5Trial"),
        "asset_classes":  ["forex"],
        "risk_pct":       float(os.getenv("MT5_FOREX_RISK_PCT",     "1.0")),
        "risk_usd":       _opt_float(os.getenv("MT5_FOREX_RISK_USD")),
        "halted": False, "consecutive_sl": 0, "last_reset_date": None,
    },
    {
        "name":           "forex_2",
        "login":          int(os.getenv("MT5_FOREX_2_LOGIN",        "10000004")),
        "password":       os.getenv("MT5_FOREX_2_PASSWORD",         "dummy"),
        "server":         os.getenv("MT5_FOREX_2_SERVER",           "Exness-MT5Trial"),
        "asset_classes":  ["forex"],
        "risk_pct":       float(os.getenv("MT5_FOREX_RISK_PCT",     "1.0")),
        "risk_usd":       _opt_float(os.getenv("MT5_FOREX_RISK_USD")),
        "halted": False, "consecutive_sl": 0, "last_reset_date": None,
    },
    # ── Index (NAS100, US30, SPX500) ──────────────────────────────────────────
    {
        "name":           "index_1",
        "login":          int(os.getenv("MT5_INDEX_1_LOGIN",        "10000005")),
        "password":       os.getenv("MT5_INDEX_1_PASSWORD",         "dummy"),
        "server":         os.getenv("MT5_INDEX_1_SERVER",           "Exness-MT5Trial"),
        "asset_classes":  ["index"],
        "risk_pct":       float(os.getenv("MT5_INDEX_RISK_PCT",     "1.0")),
        "risk_usd":       _opt_float(os.getenv("MT5_INDEX_RISK_USD")),
        "halted": False, "consecutive_sl": 0, "last_reset_date": None,
    },
    {
        "name":           "index_2",
        "login":          int(os.getenv("MT5_INDEX_2_LOGIN",        "10000006")),
        "password":       os.getenv("MT5_INDEX_2_PASSWORD",         "dummy"),
        "server":         os.getenv("MT5_INDEX_2_SERVER",           "Exness-MT5Trial"),
        "asset_classes":  ["index"],
        "risk_pct":       float(os.getenv("MT5_INDEX_RISK_PCT",     "1.0")),
        "risk_usd":       _opt_float(os.getenv("MT5_INDEX_RISK_USD")),
        "halted": False, "consecutive_sl": 0, "last_reset_date": None,
    },
    # ── Crypto (BTCUSD, ETHUSD) — add accounts here when ready ───────────────
    # {
    #     "name":          "crypto_1",
    #     "login":         int(os.getenv("MT5_CRYPTO_1_LOGIN",    "10000007")),
    #     "password":      os.getenv("MT5_CRYPTO_1_PASSWORD",     "dummy"),
    #     "server":        os.getenv("MT5_CRYPTO_1_SERVER",       "Exness-MT5Trial"),
    #     "asset_classes": ["crypto"],
    #     "risk_pct":      float(os.getenv("MT5_CRYPTO_RISK_PCT", "1.0")),
    #     "risk_usd":      _opt_float(os.getenv("MT5_CRYPTO_RISK_USD")),
    #     "halted": False, "consecutive_sl": 0, "last_reset_date": None,
    # },
]


def _account_by_name(name: str) -> dict | None:
    return next((a for a in ACCOUNTS if a["name"] == name), None)

# ──────────────────────────────────────────────────────────────────────────────

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

_POSITIONS_FILE     = Path("journal/positions.json")
_ACCOUNT_STATE_FILE = Path("journal/account_state.json")

# In-memory map: signal_id → {account_name: [ticket, ...]}
# Each account that received the signal has its own ticket list.
_open: dict[str, dict[str, list[int]]] = {}


# ── MT5 connection ─────────────────────────────────────────────────────────────

_MT5_RETRIES    = 3
_MT5_RETRY_WAIT = 5


def _connect(account: dict) -> bool:
    """Initialise MT5 for a specific account. Always call mt5.shutdown() after."""
    for attempt in range(1, _MT5_RETRIES + 1):
        if mt5.initialize(
            login=account["login"],
            password=account["password"],
            server=account["server"],
        ):
            return True
        err = mt5.last_error()
        if attempt < _MT5_RETRIES:
            log.warning(
                f"MT5 initialize [{account['name']}] attempt {attempt}/{_MT5_RETRIES} failed: {err}"
                f" — retrying in {_MT5_RETRY_WAIT}s"
            )
            time.sleep(_MT5_RETRY_WAIT)
        else:
            log.error(f"MT5 initialize [{account['name']}] failed after {_MT5_RETRIES} attempts: {err}")
    return False


# ── Circuit breaker ────────────────────────────────────────────────────────────

def _daily_reset_if_needed(account: dict) -> None:
    """Reset consecutive SL counter and halted flag at the start of each calendar day."""
    today = str(date.today())
    if account.get("last_reset_date") != today:
        account["consecutive_sl"]  = 0
        account["halted"]          = False
        account["last_reset_date"] = today
        _save_account_state()


# ── Persistence ────────────────────────────────────────────────────────────────

def load_state() -> None:
    """Load position map and account circuit-breaker state from disk on startup."""
    global _open
    _load_account_state()

    if not _POSITIONS_FILE.exists():
        return

    with open(_POSITIONS_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    _open = {}
    migrated = 0
    for signal_id, val in raw.items():
        if isinstance(val, dict):
            _open[signal_id] = {k: [int(t) for t in v] for k, v in val.items()}
        else:
            # Old single-account format: list or int — wrap under "legacy" key
            tickets = [int(t) for t in val] if isinstance(val, list) else [int(val)]
            _open[signal_id] = {"legacy": tickets}
            migrated += 1

    if migrated:
        log.warning(
            f"Migrated {migrated} signal(s) from old single-account positions format "
            f"— legacy tickets will not reconcile against named accounts"
        )

    total_tickets = sum(len(t) for acc in _open.values() for t in acc.values())
    log.info(f"Loaded {len(_open)} tracked signals ({total_tickets} tickets) from disk")

    if not _open or DRY_RUN:
        return

    # Reconcile: prune tickets that no longer exist in MT5 (closed while bot was down)
    stale_signals = []
    for signal_id, acc_tickets in _open.items():
        for acc_name, tickets in list(acc_tickets.items()):
            if acc_name == "legacy":
                continue
            account = _account_by_name(acc_name)
            if not account:
                continue
            if not _connect(account):
                mt5.shutdown()
                continue
            remaining = []
            for ticket in tickets:
                if mt5.positions_get(ticket=ticket) or mt5.orders_get(ticket=ticket):
                    remaining.append(ticket)
                else:
                    log.warning(
                        f"Startup reconcile [{acc_name}]: ticket {ticket} not in MT5"
                        f" — removing (signal_id={signal_id})"
                    )
            acc_tickets[acc_name] = remaining
            mt5.shutdown()

        if all(len(t) == 0 for t in acc_tickets.values()):
            stale_signals.append(signal_id)

    for sid in stale_signals:
        _open.pop(sid)

    if stale_signals:
        _save()
        log.info(
            f"Reconciliation removed {len(stale_signals)} fully closed signal(s);"
            f" {len(_open)} active remain"
        )


def _save() -> None:
    with open(_POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(_open, f, indent=2)


def _load_account_state() -> None:
    if not _ACCOUNT_STATE_FILE.exists():
        return
    with open(_ACCOUNT_STATE_FILE, encoding="utf-8") as f:
        saved = json.load(f)
    for account in ACCOUNTS:
        s = saved.get(account["name"])
        if s:
            account["halted"]          = s.get("halted",           False)
            account["consecutive_sl"]  = s.get("consecutive_sl",   0)
            account["last_reset_date"] = s.get("last_reset_date")


def _save_account_state() -> None:
    state = {
        a["name"]: {
            "halted":          a["halted"],
            "consecutive_sl":  a["consecutive_sl"],
            "last_reset_date": a["last_reset_date"],
        }
        for a in ACCOUNTS
    }
    with open(_ACCOUNT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ── Dynamic lot sizing ─────────────────────────────────────────────────────────

def _calc_lot(account: dict, symbol: str, instrument: str, entry: float, sl: float) -> float:
    """Lot size = risk_amount / (sl_ticks × tick_value).
    Uses risk_usd if set, otherwise risk_pct % of current equity."""
    sl_dist = abs(entry - sl)
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Cannot get symbol_info for {symbol}")

    if sl_dist == 0:
        log.warning(f"SL distance is 0 for {instrument} — using minimum lot")
        return info.volume_min

    if account.get("risk_usd"):
        risk = float(account["risk_usd"])
    else:
        acc_info = mt5.account_info()
        if acc_info is None:
            raise RuntimeError(f"Cannot get account_info for {account['name']}")
        risk = acc_info.equity * account["risk_pct"] / 100

    sl_ticks = sl_dist / info.trade_tick_size
    lot      = risk / (sl_ticks * info.trade_tick_value)

    step = info.volume_step
    lot  = round(lot / step) * step
    lot  = max(info.volume_min, min(lot, info.volume_max))
    return round(lot, 2)


# ── Channel-specific entry logic ───────────────────────────────────────────────

def _resolve_order_type(direction: str, instrument: str, symbol: str, entry_price: float) -> tuple[int, int, float]:
    """Compare live price to signal entry → market, limit, or stop order.
    Returns (mt5_action, mt5_order_type, execution_price)."""
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
    instrument = signal["instrument"]
    return _resolve_order_type(
        signal["direction"],
        instrument,
        SYMBOL_MAP.get(instrument, instrument),
        signal["entry"],
    )


def _resolve_xauusd(signal: dict) -> list[tuple[int, int, float]]:
    """Resolve one order spec per entry price for XAUUSD BIG LOTS range signals."""
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
    asset_class = signal.get("asset_class", "")
    signal_id   = signal["signal_id"]
    instrument  = signal["instrument"]
    sl          = signal.get("sl")
    tps         = signal.get("tp") or []
    channel_id  = signal["source_channel_id"]
    symbol      = SYMBOL_MAP.get(instrument, instrument)

    if not tps or sl is None:
        log.warning(
            f"place_order skipped — signal must have both SL and at least one TP"
            f" (sl={sl}, tps={tps}) signal_id={signal_id}"
        )
        return

    tp_levels = tps[:_MAX_TP_ORDERS]

    # Accounts that handle this asset class
    matching = [a for a in ACCOUNTS if asset_class in a["asset_classes"]]
    if not matching:
        log.warning(
            f"No accounts configured for asset_class={asset_class!r} — "
            f"signal {signal_id} ({instrument}) will not be traded"
        )
        return

    if DRY_RUN:
        if channel_id == 1481325093 and signal.get("entry_range") and SPLIT_RANGE_ENTRIES:
            lo, hi = signal["entry_range"]
            prices = [hi, lo] if signal["direction"] == "BUY" else [lo, hi]
            dry_pairs = list(zip(prices, tp_levels))
        else:
            dry_pairs = [(signal["entry"], tp) for tp in tp_levels]
        for acc in matching:
            for i, (price, tp) in enumerate(dry_pairs, 1):
                log.info(
                    f"[DRY RUN] [{acc['name']}] TP{i}/{len(dry_pairs)} — "
                    f"{signal['direction']} {instrument} @ {price} SL={sl} TP={tp}"
                    f" signal_id={signal_id}"
                )
        return

    signal_tickets: dict[str, list[int]] = {}

    for account in matching:
        _daily_reset_if_needed(account)
        if account["halted"]:
            log.warning(
                f"[{account['name']}] halted (consecutive_sl={account['consecutive_sl']})"
                f" — skipping signal_id={signal_id}"
            )
            continue

        if not _connect(account):
            mt5.shutdown()
            continue

        try:
            if channel_id == 2133117224:      # Vip Thrilokh — single entry
                spec       = _resolve_thrilokh(signal)
                orders_tps = [(spec, tp) for tp in tp_levels]
            elif channel_id == 1481325093:    # XAUUSD VIP BIG LOTS — range aware
                specs = _resolve_xauusd(signal)
                orders_tps = (
                    list(zip(specs, tp_levels)) if len(specs) > 1
                    else [(specs[0], tp) for tp in tp_levels]
                )
            else:                             # Kathy ZIP and any future channels
                spec       = _resolve_thrilokh(signal)
                orders_tps = [(spec, tp) for tp in tp_levels]

            tickets = []
            for i, ((action, mt5_type, price), tp) in enumerate(orders_tps, 1):
                lot     = _calc_lot(account, symbol, instrument, price, sl)
                filling = (
                    mt5.ORDER_FILLING_IOC
                    if action == mt5.TRADE_ACTION_DEAL
                    else mt5.ORDER_FILLING_RETURN
                )
                request: dict = {
                    "action":       action,
                    "symbol":       symbol,
                    "volume":       lot,
                    "type":         mt5_type,
                    "price":        price,
                    "sl":           sl,
                    "tp":           tp,
                    "type_time":    mt5.ORDER_TIME_GTC,
                    "type_filling": filling,
                }
                log.info(
                    f"[{account['name']}] Sending TP{i}/{len(orders_tps)}: "
                    f"{instrument} {signal['direction']} @ {price} SL={sl} TP={tp} lot={lot}"
                )
                result = mt5.order_send(request)

                if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                    retcode = result.retcode if result else None
                    err     = result.comment if result else mt5.last_error()
                    log.error(
                        f"[{account['name']}] order_send TP{i} failed"
                        f" retcode={retcode} err={err}: {instrument} {signal['direction']}"
                    )
                    continue

                tickets.append(result.order)
                log.info(
                    f"[{account['name']}] TP{i} placed: ticket={result.order}"
                    f" {instrument} @ {price} TP={tp} lot={lot}"
                )

            if tickets:
                signal_tickets[account["name"]] = tickets

        except Exception:
            log.exception(f"[{account['name']}] place_order failed — signal_id={signal_id}")
        finally:
            mt5.shutdown()

    if signal_tickets:
        _open[signal_id] = signal_tickets
        _save()
        total = sum(len(t) for t in signal_tickets.values())
        log.info(
            f"Signal {signal_id}: {total} orders placed across"
            f" {len(signal_tickets)} account(s)"
        )


# ── TP1 hit — move remaining positions to breakeven ───────────────────────────

async def handle_tp_hit(signal_id: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _handle_tp_hit_sync, signal_id)


def _handle_tp_hit_sync(signal_id: str) -> None:
    if DRY_RUN:
        log.info(f"[DRY RUN] handle_tp_hit — signal_id={signal_id}")
        return

    if not MOVE_SL_TO_BE_ON_TP1:
        _handle_close_sync(signal_id, "tp_hit")
        return

    acc_tickets = _open.get(signal_id)
    if not acc_tickets:
        log.warning(f"handle_tp_hit: no tracked tickets for signal_id={signal_id}")
        return

    for acc_name, tickets in list(acc_tickets.items()):
        account = _account_by_name(acc_name)
        if not account:
            continue

        if not _connect(account):
            mt5.shutdown()
            continue

        try:
            remaining = []
            for ticket in list(tickets):
                positions = mt5.positions_get(ticket=ticket)
                if positions:
                    pos    = positions[0]
                    result = mt5.order_send({
                        "action":   mt5.TRADE_ACTION_SLTP,
                        "position": pos.ticket,
                        "sl":       pos.price_open,
                        "tp":       pos.tp,
                    })
                    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                        err = result.comment if result else mt5.last_error()
                        log.error(f"[{acc_name}] Move SL to BE failed ({err}): ticket={ticket}")
                    else:
                        log.info(
                            f"[{acc_name}] SL moved to BE ({pos.price_open}):"
                            f" ticket={ticket} signal_id={signal_id}"
                        )
                    remaining.append(ticket)
                else:
                    orders = mt5.orders_get(ticket=ticket)
                    if orders:
                        result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
                        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                            err = result.comment if result else mt5.last_error()
                            log.error(f"[{acc_name}] Cancel pending on tp_hit failed ({err}): ticket={ticket}")
                        else:
                            log.info(f"[{acc_name}] Pending cancelled on tp_hit: ticket={ticket} signal_id={signal_id}")
                    else:
                        log.info(f"[{acc_name}] Ticket {ticket} already closed by MT5: signal_id={signal_id}")

            acc_tickets[acc_name] = remaining

        except Exception:
            log.exception(f"[{acc_name}] handle_tp_hit failed — signal_id={signal_id}")
        finally:
            mt5.shutdown()

        # TP hit = win → reset the SL streak for this account
        account["consecutive_sl"] = 0

    _save_account_state()

    if all(len(t) == 0 for t in acc_tickets.values()):
        _open.pop(signal_id, None)
    _save()


# ── Close / cancel ─────────────────────────────────────────────────────────────

async def handle_close(signal_id: str, update_type: str = "full_close") -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _handle_close_sync, signal_id, update_type)


def _handle_close_sync(signal_id: str, update_type: str = "full_close") -> None:
    if DRY_RUN:
        log.info(f"[DRY RUN] handle_close update_type={update_type} — signal_id={signal_id}")
        return

    acc_tickets = _open.get(signal_id)
    if not acc_tickets:
        log.warning(f"handle_close: no tracked tickets for signal_id={signal_id}")
        return

    for acc_name, tickets in list(acc_tickets.items()):
        account = _account_by_name(acc_name)
        if not account:
            continue

        if not _connect(account):
            mt5.shutdown()
            continue

        try:
            for ticket in list(tickets):
                positions = mt5.positions_get(ticket=ticket)
                if positions:
                    pos        = positions[0]
                    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
                    tick       = mt5.symbol_info_tick(pos.symbol)
                    price      = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
                    request    = {
                        "action":       mt5.TRADE_ACTION_DEAL,
                        "symbol":       pos.symbol,
                        "volume":       pos.volume,
                        "type":         close_type,
                        "position":     pos.ticket,
                        "price":        price,
                        "type_time":    mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_IOC,
                    }
                    result = mt5.order_send(request)
                    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                        err = result.comment if result else mt5.last_error()
                        log.error(f"[{acc_name}] Close failed ({err}): ticket={ticket}")
                    else:
                        log.info(f"[{acc_name}] Position closed: ticket={ticket} signal_id={signal_id}")
                else:
                    orders = mt5.orders_get(ticket=ticket)
                    if orders:
                        result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
                        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                            err = result.comment if result else mt5.last_error()
                            log.error(f"[{acc_name}] Cancel failed ({err}): ticket={ticket}")
                        else:
                            log.info(f"[{acc_name}] Pending cancelled: ticket={ticket} signal_id={signal_id}")
                    else:
                        log.warning(f"[{acc_name}] Ticket {ticket} not found — already closed/cancelled?")

        except Exception:
            log.exception(f"[{acc_name}] handle_close failed — signal_id={signal_id}")
        finally:
            mt5.shutdown()

        # Circuit breaker: SL hit increments streak; anything else (full_close, cancelled) does not
        if update_type == "sl_hit":
            account["consecutive_sl"] += 1
            if account["consecutive_sl"] >= CIRCUIT_BREAKER_SL_LIMIT:
                account["halted"] = True
                log.warning(
                    f"[{acc_name}] CIRCUIT BREAKER TRIPPED:"
                    f" {CIRCUIT_BREAKER_SL_LIMIT} consecutive SLs — account halted until tomorrow"
                )

    _save_account_state()
    _open.pop(signal_id, None)
    _save()
