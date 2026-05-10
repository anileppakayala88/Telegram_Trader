"""
BTCUSD test suite — no Telegram required.

Section 1: Parser unit tests
  Hardcoded channel messages → assert classify + parse results.
  No MT5 needed.

Section 2: MT5 order tests
  Connects to MT5, reads live BTCUSDm price, constructs 6 signals
  covering every order type (market / limit / stop, both directions),
  with SL and TP calculated from live price.
  DRY_RUN by default; pass --live to place real orders.

Usage:
  python test_btcusd_live.py           # parser tests + MT5 dry-run
  python test_btcusd_live.py --live    # parser tests + place real orders
  python test_btcusd_live.py --parser  # parser tests only (no MT5)
"""

import logging
import os
import sys
import types
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, ".")

# Stub llm_classify so tests never hit the API
_llm_stub = types.ModuleType("llm_classify")
_llm_stub.get_update_type = lambda text, ch: "noise"
sys.modules.setdefault("llm_classify", _llm_stub)

from channels import vip_thrilokh as parser
import webhook

# ── Config ────────────────────────────────────────────────────────────────────

DRY_RUN     = "--live"   not in sys.argv
PARSER_ONLY = "--parser" in sys.argv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s — %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("test_btcusd")

_ORDER_TYPE_NAME = {}   # populated after mt5 import below


# ── Fake message helper ───────────────────────────────────────────────────────

class _Msg:
    """Minimal stand-in for a Telethon Message object."""
    def __init__(self, text, msg_id=1, reply_to=None):
        self.text            = text
        self.id              = msg_id
        self.reply_to_msg_id = reply_to
        self.media           = None
        self.date            = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — Parser unit tests
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: (label, message_text, reply_to, expected_classify, expected_update_type_or_parse)
# For new_signal rows:  expected field = (direction, instrument, entry, sl, tp[0])
# For trade_update rows: expected field = update_type string
# For noise rows:        expected field = None
_PARSER_CASES = [
    # ── New signals ──────────────────────────────────────────────────────────
    (
        "SELL BTC — direction inferred from SL > entry",
        "Btc @ 74220\nSl  @ 75647\nTp. @ 70450",
        None,
        "new_signal",
        ("SELL", "BTCUSD", 74220.0, 75647.0, 70450.0),
    ),
    (
        "SELL BTC — explicit direction prefix",
        "Sell Btc @ 74220\nSl @ 73000\nTp @ 70450",
        None,
        "new_signal",
        ("SELL", "BTCUSD", 74220.0, 73000.0, 70450.0),
    ),
    (
        "BUY BTC — direction inferred from SL < entry",
        "Btc @ 74220\nSl @ 72000\nTp @ 78000",
        None,
        "new_signal",
        ("BUY", "BTCUSD", 74220.0, 72000.0, 78000.0),
    ),
    (
        "BTC signal with leading emoji",
        "🔥Btc @ 74220\nSl @ 75647\nTp @ 70450",
        None,
        "new_signal",
        ("SELL", "BTCUSD", 74220.0, 75647.0, 70450.0),
    ),
    # ── Trade updates ─────────────────────────────────────────────────────────
    (
        "Breakeven reminder",
        "Keep Btc sl as be",
        123,
        "trade_update",
        "breakeven",
    ),
    (
        "Full close — 'closing this' phrasing",
        "Am closing this Btc trade here",
        123,
        "trade_update",
        "full_close",
    ),
    (
        "Full close — 'close here' phrasing",
        "close here",
        123,
        "trade_update",
        "full_close",
    ),
    (
        "Exit at breakeven → full_close (not sl_hit)",
        "exit at be",
        123,
        "trade_update",
        "full_close",
    ),
    (
        "TP hit — 'tapped'",
        "Btc 50% tapped",
        123,
        "trade_update",
        "tp_hit",
    ),
    (
        "TP hit — 'tp1 hitted'",
        "Tp1 hitted",
        123,
        "trade_update",
        "tp_hit",
    ),
    (
        "SL hit — bare 'Sl' reply",
        "Sl",
        123,
        "trade_update",
        "sl_hit",
    ),
    (
        "Partial close",
        "Close partial and set be",
        123,
        "trade_update",
        "partial_close",
    ),
    # ── Noise ────────────────────────────────────────────────────────────────
    (
        "Noise — market commentary",
        "Btc is pushing",
        None,
        "noise",
        None,
    ),
    (
        "Noise — expectation message",
        "Btc expectation",
        None,
        "noise",
        None,
    ),
    (
        "Noise — emoji only",
        "🔥🚀",
        None,
        "noise",
        None,
    ),
    (
        "Noise — weekly RR summary",
        "VIP signal trades\nTotal RR 1:3 this week",
        None,
        "noise",
        None,
    ),
]


def run_parser_tests() -> tuple[int, int]:
    """Run all parser cases. Returns (passed, failed)."""
    passed = failed = 0

    log.info("")
    log.info("=" * 60)
    log.info("SECTION 1 — Parser unit tests")
    log.info("=" * 60)

    for label, text, reply_to, exp_classify, exp_detail in _PARSER_CASES:
        msg    = _Msg(text, reply_to=reply_to)
        result = parser.classify(msg)

        if result != exp_classify:
            log.error(f"FAIL  {label}")
            log.error(f"      classify: got={result!r}  expected={exp_classify!r}")
            failed += 1
            continue

        if exp_classify == "new_signal":
            parsed = parser.parse_signal(msg)
            if parsed is None:
                log.error(f"FAIL  {label}")
                log.error("      parse_signal returned None")
                failed += 1
                continue
            exp_dir, exp_inst, exp_entry, exp_sl, exp_tp0 = exp_detail
            checks = [
                parsed["direction"]  == exp_dir,
                parsed["instrument"] == exp_inst,
                parsed["entry"]      == exp_entry,
                parsed["sl"]         == exp_sl,
                parsed["tp"][0]      == exp_tp0,
            ]
            if all(checks):
                log.info(
                    f"PASS  {label}\n"
                    f"      {parsed['direction']} {parsed['instrument']}"
                    f" @ {parsed['entry']}  SL={parsed['sl']}  TP={parsed['tp']}"
                )
                passed += 1
            else:
                log.error(f"FAIL  {label}")
                log.error(
                    f"      got:      {parsed['direction']} {parsed['instrument']}"
                    f" @ {parsed['entry']}  SL={parsed['sl']}  TP={parsed['tp']}"
                )
                log.error(f"      expected: {exp_dir} {exp_inst} @ {exp_entry}  SL={exp_sl}  TP=[{exp_tp0}]")
                failed += 1

        elif exp_classify == "trade_update":
            upd  = parser.parse_update(msg, "fake-signal-id")
            utype = (upd if isinstance(upd, dict) else upd[0])["update_type"]
            if utype == exp_detail:
                log.info(f"PASS  {label}\n      update_type={utype}")
                passed += 1
            else:
                log.error(f"FAIL  {label}")
                log.error(f"      update_type: got={utype!r}  expected={exp_detail!r}")
                failed += 1

        else:  # noise
            log.info(f"PASS  {label}\n      classified as noise")
            passed += 1

    log.info("")
    log.info(f"Parser tests: {passed} passed, {failed} failed out of {passed + failed}")
    return passed, failed


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — MT5 order tests with live price
# ─────────────────────────────────────────────────────────────────────────────

def _build_signals(live_ask: float, live_bid: float) -> list[dict]:
    """
    Construct 6 BTCUSD test signals derived from live price.
    Offsets are sized for BTC volatility (~1-3% moves are noise).

    SL distance: $1500 (~1.5%)
    TP1 distance: $2000, TP2: $4000  (roughly 1.3:1 and 2.7:1 R:R)
    Limit/stop offset: $3000 from market (well outside tolerance of 50 pips × $10 = $500)
    """
    import uuid

    def sig(direction, entry, sl, tp1, tp2, label):
        return {
            "signal_id":           str(uuid.uuid4()),
            "telegram_msg_id":     0,
            "message_type":        "new_signal",
            "timestamp":           datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
            "source_channel_id":   parser.CHANNEL_ID,
            "source_channel_name": parser.CHANNEL_NAME,
            "raw_message":         f"[TEST] {label}",
            "asset_class":         "crypto",
            "instrument":          "BTCUSD",
            "direction":           direction,
            "order_type":          "market",
            "entry":               round(entry, 2),
            "entry_range":         None,
            "sl":                  round(sl, 2),
            "tp":                  [round(tp1, 2), round(tp2, 2)],
            "has_image":           False,
            "parse_status":        "parsed",
            "notes":               label,
        }

    gap = 3000   # price offset for limit/stop tests
    sl_d = 1500  # SL distance from entry
    tp1d = 2000  # TP1 distance from entry
    tp2d = 4000  # TP2 distance from entry

    return [
        # 1. Market BUY — entry at live ask → MARKET BUY
        sig("BUY",  live_ask,       live_ask - sl_d, live_ask + tp1d, live_ask + tp2d,
            f"MARKET BUY @ {live_ask:.0f}"),

        # 2. Market SELL — entry at live bid → MARKET SELL
        sig("SELL", live_bid,       live_bid + sl_d, live_bid - tp1d, live_bid - tp2d,
            f"MARKET SELL @ {live_bid:.0f}"),

        # 3. BUY LIMIT — entry below market (price must pull back down to fill)
        sig("BUY",  live_ask - gap, live_ask - gap - sl_d,
            live_ask - gap + tp1d, live_ask - gap + tp2d,
            f"BUY_LIMIT @ {live_ask - gap:.0f}  (market={live_ask:.0f})"),

        # 4. SELL LIMIT — entry above market (price must pull back up to fill)
        sig("SELL", live_bid + gap, live_bid + gap + sl_d,
            live_bid + gap - tp1d, live_bid + gap - tp2d,
            f"SELL_LIMIT @ {live_bid + gap:.0f}  (market={live_bid:.0f})"),

        # 5. BUY STOP — entry above market (enter on breakout upward)
        sig("BUY",  live_ask + gap, live_ask + gap - sl_d,
            live_ask + gap + tp1d, live_ask + gap + tp2d,
            f"BUY_STOP @ {live_ask + gap:.0f}  (market={live_ask:.0f})"),

        # 6. SELL STOP — entry below market (enter on breakout downward)
        sig("SELL", live_bid - gap, live_bid - gap + sl_d,
            live_bid - gap - tp1d, live_bid - gap - tp2d,
            f"SELL_STOP @ {live_bid - gap:.0f}  (market={live_bid:.0f})"),
    ]


def _run_mt5_test(signal: dict, account: dict) -> bool:
    """Run one test signal through order resolution + placement. Returns True on pass."""
    import MetaTrader5 as mt5

    instrument = signal["instrument"]
    symbol     = webhook.SYMBOL_MAP.get(instrument, instrument)
    direction  = signal["direction"]
    sl         = signal["sl"]
    tps        = signal["tp"][: webhook._MAX_TP_ORDERS]
    label      = signal["notes"]

    log.info("")
    log.info(f"  TEST: {label}")
    log.info(f"  Signal: {direction} @ {signal['entry']}  SL={sl}  TP={tps}")

    # Order type resolution
    try:
        action, mt5_type, exec_price = webhook._resolve_order_type(
            direction, instrument, symbol, signal["entry"]
        )
    except RuntimeError as e:
        log.error(f"  FAIL — order type resolution error: {e}")
        return False

    type_name = _ORDER_TYPE_NAME.get(mt5_type, str(mt5_type))
    log.info(f"  Resolved: {type_name} @ {exec_price}")

    ok = True
    for i, tp in enumerate(tps, 1):
        try:
            lot = webhook._calc_lot(account, symbol, instrument, exec_price, sl)
        except RuntimeError as e:
            log.error(f"  TP{i}: lot calc error — {e}")
            ok = False
            continue

        if DRY_RUN:
            log.info(f"  TP{i}={tp}  lot={lot}  [DRY_RUN] {type_name} {lot}x {symbol} @ {exec_price}  SL={sl}  TP={tp}")
            continue

        req = {
            "action":       action,
            "symbol":       symbol,
            "volume":       lot,
            "type":         mt5_type,
            "price":        exec_price,
            "sl":           sl,
            "tp":           tp,
            "deviation":    20,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if action == mt5.TRADE_ACTION_PENDING:
            req["type_time"] = mt5.ORDER_TIME_GTC

        result = mt5.order_send(req)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(f"  TP{i}: ORDER PLACED — ticket={result.order}")
        else:
            retcode = result.retcode if result else "?"
            log.error(f"  TP{i}: FAILED — retcode={retcode}  {mt5.last_error()}")
            ok = False

    return ok


def run_mt5_tests() -> tuple[int, int]:
    import MetaTrader5 as mt5

    # Populate order type name map now that mt5 is imported
    global _ORDER_TYPE_NAME
    _ORDER_TYPE_NAME = {
        mt5.ORDER_TYPE_BUY:        "MARKET  BUY",
        mt5.ORDER_TYPE_SELL:       "MARKET  SELL",
        mt5.ORDER_TYPE_BUY_LIMIT:  "BUY_LIMIT",
        mt5.ORDER_TYPE_SELL_LIMIT: "SELL_LIMIT",
        mt5.ORDER_TYPE_BUY_STOP:   "BUY_STOP",
        mt5.ORDER_TYPE_SELL_STOP:  "SELL_STOP",
    }

    log.info("")
    log.info("=" * 60)
    log.info(f"SECTION 2 — MT5 order tests  (DRY_RUN={'YES' if DRY_RUN else 'NO — LIVE'})")
    log.info("=" * 60)

    # Connect
    account = webhook._account_by_name("commodity_1")
    if account is None or account["login"] == 0:
        log.error("commodity_1 not configured — set MT5_COMMODITY_1_LOGIN in .env")
        return 0, 6
    if not webhook._connect(account):
        return 0, 6

    try:
        acc_info = mt5.account_info()
        if acc_info:
            log.info(
                f"Connected: login={acc_info.login}  server={acc_info.server}"
                f"  balance={acc_info.balance:.2f}  equity={acc_info.equity:.2f}"
            )

        # Live price
        symbol = webhook.SYMBOL_MAP.get("BTCUSD", "BTCUSDm")
        tick   = mt5.symbol_info_tick(symbol)
        if tick is None:
            log.error(f"No tick for {symbol} — add it to Market Watch in MT5 terminal")
            return 0, 6

        log.info(f"Live {symbol}: ask={tick.ask}  bid={tick.bid}")

        signals = _build_signals(tick.ask, tick.bid)
        passed = failed = 0

        for signal in signals:
            if _run_mt5_test(signal, account):
                passed += 1
            else:
                failed += 1

    finally:
        mt5.shutdown()
        log.info("\nMT5 disconnected")

    log.info(f"\nMT5 tests: {passed} passed, {failed} failed out of {passed + failed}")
    return passed, failed


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p_pass, p_fail = run_parser_tests()

    if not PARSER_ONLY:
        m_pass, m_fail = run_mt5_tests()
    else:
        m_pass = m_fail = 0
        log.info("\n(Skipped MT5 tests — remove --parser flag to run them)")

    log.info("")
    log.info("=" * 60)
    total_pass = p_pass + m_pass
    total_fail = p_fail + m_fail
    log.info(f"TOTAL: {total_pass} passed, {total_fail} failed")
    if total_fail == 0:
        log.info("ALL TESTS PASSED")
    log.info("=" * 60)

    if not DRY_RUN and not PARSER_ONLY:
        log.info("Live orders were placed — check MT5 terminal to review/close them")

    sys.exit(0 if total_fail == 0 else 1)
