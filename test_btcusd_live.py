"""
Full parser + MT5 order test suite — no Telegram required.

Section 1: Parser unit tests (44 cases across all 3 channels)
  Vip Thrilokh  — new signals (4), updates (10), noise (4)
  XAUUSD BIG LOTS — new signals (3), updates (10), noise (3)
  Kathy ZIP     — new signals (3), updates (8), noise (2)
  No MT5 needed.

Section 2: MT5 order tests — live BTCUSDm price
  6 signals covering every order type (market/limit/stop × BUY/SELL),
  SL and TP derived from live price. DRY_RUN by default.

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

from channels import vip_thrilokh as vt
from channels import xauusd_big_lots as xbl
from channels import kathy_zip_forex as kz
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
#
# Each entry: (parser_module, label, text, reply_to, exp_classify, exp_detail)
#   new_signal   → exp_detail = (direction, instrument, entry, sl, tp[0])
#   trade_update → exp_detail = update_type string OR list of update_type strings
#                               (list used for multi-close Kathy ZIP messages)
#   noise        → exp_detail = None

_PARSER_CASES = [

    # ══════════════════════════════════════════════════════════════════════════
    # VIP THRILOKH
    # ══════════════════════════════════════════════════════════════════════════

    # ── New signals ──────────────────────────────────────────────────────────
    (vt, "VT: SELL BTC — direction inferred from SL > entry",
         "Btc @ 74220\nSl  @ 75647\nTp. @ 70450", None,
         "new_signal", ("SELL", "BTCUSD", 74220.0, 75647.0, 70450.0)),

    (vt, "VT: BUY BTC — direction inferred from SL < entry",
         "Btc @ 74220\nSl @ 72000\nTp @ 78000", None,
         "new_signal", ("BUY", "BTCUSD", 74220.0, 72000.0, 78000.0)),

    (vt, "VT: SELL BTC — explicit direction prefix",
         "Sell Btc @ 74220\nSl @ 73000\nTp @ 70450", None,
         "new_signal", ("SELL", "BTCUSD", 74220.0, 73000.0, 70450.0)),

    (vt, "VT: BTC signal with leading emoji",
         "🔥Btc @ 74220\nSl @ 75647\nTp @ 70450", None,
         "new_signal", ("SELL", "BTCUSD", 74220.0, 75647.0, 70450.0)),

    # ── Trade updates ─────────────────────────────────────────────────────────
    (vt, "VT: all tp hit → full_close",
         "all tp hit", 123, "trade_update", "full_close"),

    (vt, "VT: all tp hitted → full_close",
         "all tp hitted", 123, "trade_update", "full_close"),

    (vt, "VT: closing this → full_close",
         "Am closing this Btc trade here", 123, "trade_update", "full_close"),

    (vt, "VT: close here → full_close",
         "close here", 123, "trade_update", "full_close"),

    (vt, "VT: close now → full_close",
         "close now", 123, "trade_update", "full_close"),

    (vt, "VT: bare 'close' → full_close",
         "close", 123, "trade_update", "full_close"),

    (vt, "VT: exit at be → full_close (not sl_hit)",
         "exit at be", 123, "trade_update", "full_close"),

    (vt, "VT: set be → breakeven",
         "set be", 123, "trade_update", "breakeven"),

    (vt, "VT: sl as be → breakeven",
         "sl as be", 123, "trade_update", "breakeven"),

    (vt, "VT: close partial and set be → partial_close",
         "Close partial and set be", 123, "trade_update", "partial_close"),

    (vt, "VT: tapped → tp_hit",
         "Btc 50% tapped", 123, "trade_update", "tp_hit"),

    (vt, "VT: tp1 hitted → tp_hit",
         "Tp1 hitted", 123, "trade_update", "tp_hit"),

    (vt, "VT: Tp 1 hitted (with space) → tp_hit",
         "Tp 1 hitted", 123, "trade_update", "tp_hit"),

    (vt, "VT: Already hitted tp1 → tp_hit",
         "Already hitted tp1", 123, "trade_update", "tp_hit"),

    (vt, "VT: bare 'Sl' reply → sl_hit",
         "Sl", 123, "trade_update", "sl_hit"),

    # ── Noise ─────────────────────────────────────────────────────────────────
    (vt, "VT: noise — market commentary",
         "Btc is pushing", None, "noise", None),

    (vt, "VT: noise — expectation",
         "Btc expectation", None, "noise", None),

    (vt, "VT: noise — emoji only",
         "🔥🚀", None, "noise", None),

    (vt, "VT: noise — weekly RR summary",
         "VIP signal trades\nTotal RR 1:3 this week", None, "noise", None),

    # ══════════════════════════════════════════════════════════════════════════
    # XAUUSD VIP BIG LOTS
    # ══════════════════════════════════════════════════════════════════════════

    # ── New signals ──────────────────────────────────────────────────────────
    (xbl, "XBL: XAUUSD buy limit with range",
          "XAUUSD Buy limit 4664/4656\nSl 4643\nTP 4669\nTP 4676", None,
          "new_signal", ("BUY", "XAUUSD", 4656.0, 4643.0, 4669.0)),

    (xbl, "XBL: GOLD Buy from (market order alias)",
          "GOLD Buy from 4606/4598\nSl 4585\nTP 4615", None,
          "new_signal", ("BUY", "XAUUSD", 4598.0, 4585.0, 4615.0)),

    (xbl, "XBL: XAUUSD sell single entry",
          "XAUUSD Sell 4700\nSl 4720\nTP 4650", None,
          "new_signal", ("SELL", "XAUUSD", 4700.0, 4720.0, 4650.0)),

    # ── Trade updates ─────────────────────────────────────────────────────────
    (xbl, "XBL: ALL TP HIT → full_close",
          "XAUUSD ALL TP HIT RUNNING 200 PIPS", None, "trade_update", "full_close"),

    (xbl, "XBL: TP1 HIT → tp_hit",
          "XAUUSD TP1 HIT RUNNING 50 PIPS", None, "trade_update", "tp_hit"),

    (xbl, "XBL: TP2 HIT → tp_hit",
          "XAUUSD TP2 HIT RUNNING 100 PIPS", None, "trade_update", "tp_hit"),

    (xbl, "XBL: Be hit → full_close (not sl_hit)",
          "Be hit", None, "trade_update", "full_close"),

    (xbl, "XBL: be hit lowercase → full_close",
          "be hit", None, "trade_update", "full_close"),

    (xbl, "XBL: Missed close it → cancelled",
          "Missed close it", None, "trade_update", "cancelled"),

    (xbl, "XBL: Just missed our limit → cancelled",
          "Just missed our limit", None, "trade_update", "cancelled"),

    (xbl, "XBL: Missed → cancelled",
          "Missed", None, "trade_update", "cancelled"),

    (xbl, "XBL: Delete → cancelled",
          "Delete", None, "trade_update", "cancelled"),

    (xbl, "XBL: Close now → full_close",
          "Close now", None, "trade_update", "full_close"),

    # ── Noise ─────────────────────────────────────────────────────────────────
    (xbl, "XBL: noise — React",
          "React ❤️", None, "noise", None),

    (xbl, "XBL: noise — I'm in",
          "I'm in", None, "noise", None),

    (xbl, "XBL: noise — Go again",
          "Go again", None, "noise", None),

    # ══════════════════════════════════════════════════════════════════════════
    # KATHY ZIP FOREX TRADES
    # ══════════════════════════════════════════════════════════════════════════

    # ── New signals ──────────────────────────────────────────────────────────
    (kz, "KZ: NEW TRADE sell USDJPY",
         "NEW TRADE\n\n📈  Entry: Sell 2 unit USDJPY at 159.36\n🚫  Stop at 159.56\n🎯  Exit at 159.16",
         None, "new_signal", ("SELL", "USDJPY", 159.36, 159.56, 159.16)),

    (kz, "KZ: NEW TRADE buy EURUSD",
         "NEW TRADE\n\n📈  Entry: Buy 2 unit EURUSD at 1.1726\n🚫  Stop at 1.1706\n🎯  Exit at 1.1756",
         None, "new_signal", ("BUY", "EURUSD", 1.1726, 1.1706, 1.1756)),

    (kz, "KZ: NEW TRADE sell NZDCAD (broker symbol fix)",
         "NEW TRADE\n\n📈  Entry: Sell 1 unit NZDCAD at 0.7997\n🚫  Stop at 0.8017\n🎯  Exit at 0.7977",
         None, "new_signal", ("SELL", "NZDCAD", 0.7997, 0.8017, 0.7977)),

    # ── Trade updates ─────────────────────────────────────────────────────────
    (kz, "KZ: CLOSE INSTRUMENT AT PRICE → full_close",
         "CLOSE EURJPY AT 186.80", None, "trade_update", ["full_close"]),

    (kz, "KZ: Close INSTRUMENT at price with comment → full_close",
         "Close EURUSD at 1.1715 - not working out", None, "trade_update", ["full_close"]),

    (kz, "KZ: Close 1/2 → full_close (partial)",
         "Close 1/2 NZDJPY at 93.77", None, "trade_update", ["full_close"]),

    (kz, "KZ: Close rest of → full_close",
         "Close rest of NZDJPY at 93.73", None, "trade_update", ["full_close"]),

    (kz, "KZ: Stopped on → sl_hit",
         "Stopped on EURJPY", None, "trade_update", ["sl_hit"]),

    (kz, "KZ: TARGET HIT → tp_hit",
         "CHFJPY 🎯TARGET HIT", None, "trade_update", ["tp_hit"]),

    (kz, "KZ: two TARGET HITs in one message",
         "CHFJPY 🎯TARGET HIT\nUSDCHF 🎯TARGET HIT", None, "trade_update", ["tp_hit", "tp_hit"]),

    (kz, "KZ: multi-close — 2 closes + 1 stopped",
         "Close NZDCAD at 0.8006 - not enough momentum\nClose EURCHF at 0.9153\nStopped on EURJPY",
         None, "trade_update", ["full_close", "full_close", "sl_hit"]),

    # ── Noise ─────────────────────────────────────────────────────────────────
    (kz, "KZ: noise — weekly pip summary",
         "EURUSD +15\nAUDUSD +22\nTotal +137 pips this week", None, "noise", None),

    (kz, "KZ: noise — commentary block",
         "Here are the reasons why I like selling USDJPY:\n1. USD safe haven bid eases",
         None, "noise", None),
]


def run_parser_tests() -> tuple[int, int]:
    """Run all parser cases. Returns (passed, failed)."""
    passed = failed = 0

    log.info("")
    log.info("=" * 60)
    log.info(f"SECTION 1 — Parser unit tests ({len(_PARSER_CASES)} cases)")
    log.info("=" * 60)

    for p, label, text, reply_to, exp_classify, exp_detail in _PARSER_CASES:
        msg    = _Msg(text, reply_to=reply_to)
        result = p.classify(msg)

        if result != exp_classify:
            log.error(f"FAIL  {label}")
            log.error(f"      classify: got={result!r}  expected={exp_classify!r}")
            failed += 1
            continue

        if exp_classify == "new_signal":
            parsed = p.parse_signal(msg)
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
            upd     = p.parse_update(msg, "fake-signal-id")
            updates = upd if isinstance(upd, list) else [upd]
            actual  = [u["update_type"] for u in updates]
            exp_list = exp_detail if isinstance(exp_detail, list) else [exp_detail]
            if actual == exp_list:
                log.info(f"PASS  {label}\n      update_type={actual}")
                passed += 1
            else:
                log.error(f"FAIL  {label}")
                log.error(f"      got={actual}  expected={exp_list}")
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
