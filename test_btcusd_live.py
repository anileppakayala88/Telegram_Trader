"""
BTCUSD live integration test.

Fetches recent messages from Vip Thrilokh, runs the full
classify → parse pipeline, then for every BTCUSD signal:
  - connects to the commodity MT5 account (MT5_COMMODITY_1_*)
  - fetches live BTCUSDm price
  - resolves order type (market / limit / stop)
  - calculates lot size per TP
  - places the order, or logs what would be placed in DRY_RUN mode

Usage:
  python test_btcusd_live.py              # DRY_RUN — no orders placed
  python test_btcusd_live.py --live       # place real orders on the test account
  python test_btcusd_live.py --limit 200  # fetch more messages (default 100)
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient
import MetaTrader5 as mt5

load_dotenv()
sys.path.insert(0, ".")

from channels import vip_thrilokh as parser
from journal import JournalManager
import webhook

# ── Config ────────────────────────────────────────────────────────────────────

DRY_RUN     = "--live" not in sys.argv
FETCH_LIMIT = int(next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--limit"), 100))

_ORDER_TYPE_NAME = {
    mt5.ORDER_TYPE_BUY:        "MARKET  BUY",
    mt5.ORDER_TYPE_SELL:       "MARKET  SELL",
    mt5.ORDER_TYPE_BUY_LIMIT:  "BUY_LIMIT",
    mt5.ORDER_TYPE_SELL_LIMIT: "SELL_LIMIT",
    mt5.ORDER_TYPE_BUY_STOP:   "BUY_STOP",
    mt5.ORDER_TYPE_SELL_STOP:  "SELL_STOP",
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("test_btcusd")


# ── MT5 helpers ───────────────────────────────────────────────────────────────

def _connect_test_account() -> dict | None:
    """Connect to MT5 commodity_1 (the only configured live account)."""
    account = webhook._account_by_name("commodity_1")
    if account is None:
        log.error("commodity_1 not found in webhook.ACCOUNTS")
        return None
    if account["login"] == 0:
        log.error("MT5_COMMODITY_1_LOGIN=0 — set real credentials in .env first")
        return None
    if not webhook._connect(account):
        return None
    acc_info = mt5.account_info()
    if acc_info:
        log.info(
            f"MT5 connected: login={acc_info.login}  server={acc_info.server}"
            f"  balance={acc_info.balance:.2f}  equity={acc_info.equity:.2f}"
        )
    return account


def _run_signal(signal: dict, account: dict) -> None:
    """Resolve order type, calculate lots, and place (or dry-run) one BTCUSD signal."""
    instrument = signal["instrument"]
    symbol     = webhook.SYMBOL_MAP.get(instrument, instrument)
    direction  = signal["direction"]
    sl         = signal["sl"]
    tps        = signal["tp"][: webhook._MAX_TP_ORDERS]

    log.info("")
    log.info("─" * 60)
    log.info(f"SIGNAL  {direction} {instrument}  entry={signal['entry']}  SL={sl}  TP={tps}")
    log.info(f"        raw: {signal['raw_message'][:80].replace(chr(10), ' | ')}")
    log.info(f"        broker symbol: {symbol}")

    # Live price
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log.error(f"No tick data for {symbol} — make sure {symbol} is visible in MT5 terminal")
        return
    log.info(f"Live:   ask={tick.ask}  bid={tick.bid}")

    # Order type resolution
    try:
        action, mt5_type, exec_price = webhook._resolve_order_type(
            direction, instrument, symbol, signal["entry"]
        )
    except RuntimeError as e:
        log.error(f"Order type resolution failed: {e}")
        return

    type_name = _ORDER_TYPE_NAME.get(mt5_type, str(mt5_type))
    log.info(f"Order:  {type_name} @ {exec_price}")

    if not tps:
        log.warning("Signal has no TPs — nothing to place")
        return

    # Per-TP: lot size + place
    for i, tp in enumerate(tps, 1):
        try:
            lot = webhook._calc_lot(account, symbol, instrument, exec_price, sl)
        except RuntimeError as e:
            log.error(f"  TP{i}: lot calc error — {e}")
            continue

        log.info(f"  TP{i}={tp}  lot={lot}")

        if DRY_RUN:
            log.info(f"  TP{i}: [DRY_RUN] would send {type_name}  {lot}x {symbol}"
                     f"  @ {exec_price}  SL={sl}  TP={tp}")
            continue

        req = {
            "action":        action,
            "symbol":        symbol,
            "volume":        lot,
            "type":          mt5_type,
            "price":         exec_price,
            "sl":            sl,
            "tp":            tp,
            "deviation":     20,
            "type_filling":  mt5.ORDER_FILLING_IOC,
        }
        if action == mt5.TRADE_ACTION_PENDING:
            req["type_time"] = mt5.ORDER_TIME_GTC

        result = mt5.order_send(req)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(f"  TP{i}: ORDER PLACED — ticket={result.order}")
        else:
            retcode = result.retcode if result else "?"
            log.error(f"  TP{i}: order_send FAILED — retcode={retcode}  {mt5.last_error()}")


# ── Telegram fetch + classify ─────────────────────────────────────────────────

async def run():
    log.info("=" * 60)
    log.info(f"BTCUSD live test  |  DRY_RUN={'YES' if DRY_RUN else 'NO — PLACING REAL ORDERS'}")
    log.info(f"Fetching last {FETCH_LIMIT} messages from {parser.CHANNEL_NAME}")
    log.info("=" * 60)

    api_id   = int(os.getenv("TELEGRAM_API_ID"))
    api_hash = os.getenv("TELEGRAM_API_HASH")

    journal = JournalManager()
    client  = TelegramClient("session_fetch", api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        log.error("Not authorized — run auth.py first")
        await client.disconnect()
        return

    entity   = await client.get_entity(parser.CHANNEL_ID)
    messages = await client.get_messages(entity, limit=FETCH_LIMIT)
    await client.disconnect()

    log.info(f"Fetched {len(messages)} messages\n")

    # ── Classify + parse every message ────────────────────────────────────────
    counts      = {"new_signal": 0, "trade_update": 0, "noise": 0}
    btc_signals = []

    for msg in reversed(messages):
        if not msg.text and not msg.media:
            continue
        try:
            msg_type = parser.classify(msg)
            counts[msg_type] = counts.get(msg_type, 0) + 1

            if msg_type == "new_signal":
                entry = parser.parse_signal(msg)
                if not entry:
                    log.warning(f"[id={msg.id}] classify=new_signal but parse failed — {repr((msg.text or '')[:60])}")
                    continue
                journal.track_signal(parser.CHANNEL_ID, msg.id, entry["signal_id"], entry.get("instrument"))
                flag = " ← BTC" if entry["instrument"] == "BTCUSD" else ""
                log.info(
                    f"[id={msg.id}] SIGNAL  {entry['instrument']} {entry['direction']}"
                    f" @ {entry['entry']}  SL={entry['sl']}  TP={entry['tp']}{flag}"
                )
                if entry["instrument"] == "BTCUSD":
                    btc_signals.append(entry)

            elif msg_type == "trade_update":
                signal_id = journal.resolve_signal_id(parser.CHANNEL_ID, msg.reply_to_msg_id)
                upd       = parser.parse_update(msg, signal_id)
                updates   = upd if isinstance(upd, list) else [upd]
                for u in updates:
                    log.info(
                        f"[id={msg.id}] UPDATE  {u['update_type']}"
                        f" → {signal_id or 'unlinked'}"
                        f"  [{(msg.text or '')[:60]}]"
                    )

        except Exception:
            log.exception(f"Error processing msg_id={msg.id}")

    log.info(
        f"\nPipeline summary: signals={counts['new_signal']}"
        f"  updates={counts['trade_update']}"
        f"  noise={counts['noise']}"
    )
    log.info(f"BTC signals found: {len(btc_signals)}")

    if not btc_signals:
        log.warning(
            f"No BTCUSD signals in the last {FETCH_LIMIT} messages. "
            "Try --limit 300 to look further back."
        )
        return

    # ── MT5: connect and test each BTC signal ─────────────────────────────────
    account = _connect_test_account()
    if account is None:
        return

    try:
        for signal in btc_signals:
            _run_signal(signal, account)
    finally:
        mt5.shutdown()
        log.info("\nMT5 disconnected")
        if DRY_RUN:
            log.info("Re-run with --live to place real orders on the test account")


asyncio.run(run())
