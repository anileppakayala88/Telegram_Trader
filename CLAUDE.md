# Telegram Trader

## Project Overview

Reads trade signal messages from Telegram channels, parses them into structured trade objects, logs them to an append-only journal, and fires orders to live MT5 broker accounts via the MetaTrader5 Python library.

---

## Goals by Phase

### Phase 1 — Signal Reader + Journal (complete)
- Connect to Telegram as a user account (Telethon)
- Listen to multiple channels simultaneously for new messages
- Parse each message into a structured trade signal:
  - Instrument (e.g. XAUUSD, EURUSD, NAS100, BTC/USD)
  - Direction (BUY / SELL)
  - Entry price (or range)
  - Take Profit levels (TP1, TP2, TP3 — variable)
  - Stop Loss
  - Asset class (forex / indices / crypto / commodity)
- Classify message type: new signal, trade update, or noise
- Handle optional images attached to messages
- Write parsed signal to a trade journal (human-readable log)
- Flag unparseable messages for manual review

### Phase 2 — MT5 Direct Order Execution (complete)
- Connect to locally running MT5 terminal via MetaTrader5 Python library (Windows only)
- On new_signal: determine order type from live price vs signal price, place one order per TP level (SL shared)
- On exit update: close open position or cancel pending order automatically
- Persist open position/order IDs to disk so restarts don't lose track of live trades
- DRY_RUN mode for safe testing without real order execution

### Phase 2.1 — Multiple TPs + Split Range Entries (complete)
- Place one order per TP level (TP1 + TP2 always; TP3 optional via USE_TP3 flag)
- XAUUSD BIG LOTS range entries: one order per entry price, paired with successive TP levels (SPLIT_RANGE_ENTRIES)
- Move SL to breakeven when TP1 is hit (MOVE_SL_TO_BE_ON_TP1); or close all remaining positions
- Auto partial-close on "close partials" channel message: not yet implemented

### Phase 3 — LLM fallback classifier (complete)
- `llm_classify.py`: Claude Haiku fallback for messages that don't match any regex pattern
- Cache-backed: normalized message → result stored in `cache/classify_cache.json`
- Falls back to `"commentary"` when ANTHROPIC_API_KEY is not set

### Phase 4 — Multi-account routing + dynamic lot sizing (code-complete on `phase3-multi-account`; not yet live)
- Route signals to 6 MT5 accounts by asset class (2 accounts per class: commodity / forex / index)
- Dynamic lot sizing: `lot = risk_amount / (sl_ticks × tick_value)` using live MT5 `symbol_info()`
- Risk configured per asset class: `risk_pct` (% of equity) or `risk_usd` (fixed $ per trade)
- Circuit breaker: account halted after N consecutive SL hits; resets at calendar day rollover
- All account credentials and risk settings loaded from `.env`; accounts with `login=0` are skipped automatically
- Kathy ZIP Forex Trades (channel 3) added — instrument-based signal linking, multi-close support
- **Blocking go-live:** forex and index MT5 credentials not yet configured (commodity account active)

---

## Architecture

```
Telegram (multiple channels)
        |
   Telethon Listener
        |
   Channel Router (identifies source channel)
        |
   Channel-Specific Parser
   (per-channel profile — regex fast path + LLM fallback)
        |
   Message Classifier
   (new signal | trade update | noise)
        |
   ┌────┴────┐
   │         │
Journal    Signal Object
(JSONL)    (structured dict — asset_class field drives routing)
                |
         webhook.py
                |
   ┌────────────┼────────────┐
commodity accs  forex accs  index accs
(MT5 terminal) (MT5 terminal) (MT5 terminal)
   connect → trade → shutdown → next account (sequential)
```

---

## Lightweight Design Principles

- **Single process, single async event loop** — no threads, no subprocesses, no queues
- **Minimal dependencies** — only `telethon` and `python-dotenv` (pip installable); everything else is Python stdlib
- **Module-level functions for parsers** — no class instantiation overhead; each channel is a plain Python module
- **Append-only JSONL** — no database, no ORM; survives crashes without corruption
- **In-memory state is minimal** — only open signal IDs kept in memory; full history stays on disk
- **Fail per message, not per process** — each message is wrapped in try/except; one bad message never kills the listener
- **Logging to file + stdout** — single `logging` config, UTF-8 encoded log file

## Tech Stack

- **Language:** Python 3.11+
- **Telegram:** Telethon (user account MTProto API)
- **Auth:** Session file (`session_fetch.session`) — created once via `auth.py`
- **Parsing:** Regex fast path + Claude Haiku fallback (`llm_classify.py`) for unmatched messages; cache-backed to avoid repeat API calls
- **Journal:** Append-only JSONL (`journal/<channel>.jsonl`) — one file per channel
- **Order execution:** MetaTrader5 Python library — connects to locally running MT5 terminal (Windows only); sequential connect/shutdown per account
- **Config:** `python-dotenv` for credentials
- **pip dependencies:** `telethon`, `python-dotenv`, `MetaTrader5`, `anthropic` (optional — enables LLM fallback)

---

## Project Structure

```
Telegram_Trader/
├── .env                      # credentials (never committed)
├── CLAUDE.md                 # project instructions for AI assistant
├── README.md                 # human-readable project documentation
├── requirements.txt
├── auth.py                   # one-time Telegram session auth
├── fetch_samples.py          # pull historical messages for offline parser testing
├── fetch_kathy.py            # one-off script to fetch Kathy ZIP sample messages
├── analyze_msgs.py           # fetch last 1000 msgs per channel, classify + display grouped report
├── list_channels.py          # list all channels the account is in
├── test_replay.py            # replay historical messages through the full pipeline
├── generate_viewer.py        # generate journal_viewer.html from journal JSONL files
├── journal_viewer_template.html  # HTML template for the journal viewer
├── main.py                   # entry point — restart loop + PID lock + starts listener
├── listener.py               # Telethon event handler — routes messages to channel parsers
├── journal.py                # JSONL writer + in-memory state manager
├── webhook.py                # MT5 order execution — multi-account routing, lot sizing, circuit breaker
├── llm_classify.py           # Claude Haiku LLM fallback classifier (Phase 3)
├── bot.pid                   # PID of running bot — prevents duplicate instances (auto-created)
├── channels/
│   ├── __init__.py           # channel registry: maps channel ID → parser module
│   ├── vip_thrilokh.py       # parser for Channel 1 (Vip Thrilokh)
│   ├── xauusd_big_lots.py    # parser for Channel 2 (XAUUSD VIP BIG LOTS)
│   └── kathy_zip_forex.py    # parser for Channel 3 (Kathy ZIP Forex Trades)
├── cache/
│   └── classify_cache.json   # LLM fallback cache (auto-created)
└── journal/                  # created at runtime
    ├── vip_thrilokh.jsonl    # append-only signal log for channel 1
    ├── xauusd_big_lots.jsonl # append-only signal log for channel 2
    ├── kathy_zip_forex.jsonl # append-only signal log for channel 3
    ├── positions.json        # persisted MT5 position/order IDs per account
    └── account_state.json    # circuit breaker state per account (halted, consecutive_sl)
```

### Adding a New Channel
1. Pull sample messages via `fetch_samples.py`
2. Add channel ID + name to `channels/__init__.py` registry and `journal.py` CHANNEL_NAMES
3. Create `channels/<name>.py` implementing: `CHANNEL_NAME`, `CHANNEL_ID`, `classify(msg)`, `parse_signal(msg)`, `parse_update(msg, signal_id)`
4. Add asset class routing in `webhook.py` ACCOUNTS if the new channel trades a new asset class

---

## Telegram Access

- **Method:** Telethon user account (MTProto API) — not a bot
- **Credentials:** stored in `.env` (never committed)
- **Session file:** `session_fetch.session` — created by running `auth.py` once; reused on all subsequent runs
- **Account phone:** Canadian number (+1 416 528 7743)

### Active Channels

| Channel Name            | Telegram ID  | Username     | Asset Focus                         |
|-------------------------|--------------|--------------|-------------------------------------|
| Vip Thrilokh            | 2133117224   | no-username  | Multi-asset (BTC, Forex, NQ, XAUUSD)|
| XAUUSD VIP BIG LOTS     | 1481325093   | no-username  | XAUUSD only                         |
| Kathy ZIP Forex Trades  | 2249628758   | no-username  | Forex pairs                         |
| Test_TV_3min            | 2540865305   | no-username  | Dev/test — uses Vip Thrilokh parser |

Access channels by **numeric ID** (no username available for any).

---

## Channel Profiles

### Channel 1 — Vip Thrilokh (ID: 2133117224)

**Signal format:** Minimal, 3-line, no direction keyword. Direction inferred from SL position. Parser handles leading emojis on any line (e.g. 🔥 before the instrument name) via `\W*` prefix in the signal regex.

```
Btc @ 74220
Sl  @ 75647        ← SL > Entry = SELL
Tp. @ 70450
```

**Pipe-delimited fallback format** (occasional variant — also handled):
```
eurusd @1.17052 | Sl@ 1.17161 | Tp @1.16510
```
Both formats support optional direction prefix and multiple TPs. Direction inference (SL vs entry) applies to both.

**Direction inference rule:**
- `SL > Entry` → SELL
- `SL < Entry` → BUY

**Separator:** `@` (with optional `.` after field name, e.g. `Tp.`)
**TPs:** Single TP primary; multiple TPs in pipe-delimited format (`| Tp2 @...`)
**Images:** Almost always attached (chart image)

**Instruments traded:**
| Symbol  | Asset Class |
|---------|-------------|
| BTC     | Crypto      |
| XAUUSD  | Commodity   |
| USDCAD  | Forex       |
| AUDUSD  | Forex       |
| USDCHF  | Forex       |
| USDJPY  | Forex       |
| EURUSD  | Forex       |
| NQ      | Index       |

**Instrument aliases to normalise:**
- `Btc` → `BTCUSD`
- `Nq` → `NAS100`
- `Eu` → `EURUSD` (used in update messages)

**Update / management messages (not new signals — classify as trade_update):**
- `"Set be"` — move SL to breakeven (`breakeven`)
- `"Close partial and set be"` — take partial profit, move SL to breakeven (`partial_close`)
- `"Close partial and set sl as be"` — same
- `"Keep Btc sl as be"` — reminder to hold breakeven SL (`breakeven`)
- `"Am closing this Btc trade here"` — manual close (`full_close`)
- `"Exit at be"` — position closed at breakeven (`full_close`)
- `"Sl"` — bare word reply to a signal meaning SL was hit (`sl_hit`)
- `"Tp1 hitted"` / `"Tp 1 hitted"` / `"Already hitted tp1"` / `"tapped"` — TP hit (`tp_hit`)
- `"Btc slow price action"` — commentary
- `"<instrument> close partials"` — partial close notification
- `"<instrument> is pushing"` — market commentary

**Noise messages (ignore):**
- Single emoji or reaction messages
- `"VIP signal trades"` + RR summary (weekly recap, not a signal)
- `"Daily crt"` — commentary
- `"PWH reaction"` — commentary

---

### Channel 2 — XAUUSD VIP BIG LOTS (ID: 1481325093)

**Signal format:** Explicit direction + order type, multiple TPs, entry can be a range. Parser scans all lines for the signal line so leading emoji lines are handled transparently.

```
XAUUSD Buy limit 4664/4656
Sl 4643
TP 4669
TP 4676
TP 4720 USE BIG LOTS ✅✔️
```

Also accepted instrument prefix: `GOLD` (alias for XAUUSD). Also accepted order-type keyword: `from` (treated as market order).

```
GOLD Buy from 4606/4598
Sl 4585
TP 4615
```

**Direction:** Explicitly stated — `Buy` / `Sell` + order type (`limit` / `from` / market implied)
**Entry:** Single price or range (`4664/4656` — use lower for buy limit, upper for sell limit)
**TPs:** Multiple (TP1, TP2, TP3) — each on its own line starting with `TP`
**Images:** Rarely attached
**Instrument:** XAUUSD only (sent as `XAUUSD` or `GOLD`)

**Update / management messages (classify as trade_update):**
- `"XAUUSD TP1 HIT RUNNING X PIPS"` — first TP reached (`tp_hit`)
- `"XAUUSD TP2 HIT RUNNING X PIPS"` — second TP reached (`tp_hit`)
- `"XAUUSD ALL TP HIT RUNNING X PIPS"` — all TPs hit (`full_close`)
- `"Be hit"` — breakeven SL was triggered; position closed at 0 P&L (`full_close`, **not** `sl_hit` — must not penalise circuit breaker)
- `"X PIPS PROFIT"` — running profit update (noise)
- `"Missed close it"` / `"Just missed our limit"` / `"Missed"` / `"Delete"` — entry not triggered (`cancelled`)

**Noise messages (ignore):**
- `"React ❤️"` — engagement prompt
- `"I'm in"` — confirmation of own entry
- `"Go again"` — commentary

---

### Channel 3 — Kathy ZIP Forex Trades (ID: 2249628758)

**Signal format:** Structured `NEW TRADE` block with emoji-prefixed lines.

```
NEW TRADE

📈  Entry: Sell 2 unit USDJPY at 159.36
🚫  Stop at 159.56
🎯  Exit at 159.16
```

**Direction:** Explicitly stated in entry line (`Buy` / `Sell`)
**Units:** Ignored — always uses standard lot size configured in account risk settings
**Entry:** Single price only (no ranges)
**TPs:** Single TP (`Exit at`)
**Signal linking:** No reply threading — updates match to open signals by instrument name via `_instrument_to_signal` map in `JournalManager`
**Multi-close:** A single message can close multiple instruments; `parse_update` returns `list[dict]` (one entry per instrument)

**Update messages (classify as trade_update):**
- `"CLOSE EURJPY AT 186.80"` — full close (`full_close`)
- `"Close 1/2 NZDJPY at 93.77"` — partial close (`full_close`)
- `"Close rest of NZDJPY at 93.73"` — full close (`full_close`)
- `"Stopped on EURJPY"` — SL hit (`sl_hit`)
- `"CHFJPY 🎯TARGET HIT"` — TP hit (`tp_hit`)
- Multi-instrument: `"Close NZDCAD ... \nClose EURCHF ... \nStopped on EURJPY"` — yields 3 update dicts

**Noise messages (ignore):**
- Weekly pip summaries (lines like `EURUSD +15`, `Total +137 pips this week`)
- `"Here are the reasons why I like..."` commentary blocks
- Image-only messages

---

## Signal Schema

```json
{
  "signal_id": "uuid",
  "telegram_msg_id": 12345,
  "message_type": "new_signal | trade_update | noise",
  "timestamp": "ISO8601",
  "source_channel_id": 2133117224,
  "source_channel_name": "Vip Thrilokh",
  "raw_message": "original text",
  "asset_class": "forex | crypto | index | commodity",
  "instrument": "XAUUSD",
  "direction": "BUY | SELL",
  "order_type": "market | limit",
  "entry": 2345.00,
  "entry_range": [2340.00, 2350.00],
  "sl": 2310.00,
  "tp": [2370.00, 2400.00, 2450.00],
  "has_image": false,
  "parse_status": "parsed | partial | failed",
  "notes": ""
}
```

---

## Environment Variables

```
# Telegram
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_PHONE=

# LLM fallback (optional — falls back to "commentary" if not set)
ANTHROPIC_API_KEY=

# MT5 — single account (all asset classes: commodity, forex, index, crypto)
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
MT5_RISK_PCT=1.0              # % of equity per trade (used when RISK_USD not set)
MT5_RISK_USD=                 # fixed $ per trade (overrides RISK_PCT if set)

# Order behaviour
DRY_RUN=true                  # set to false to place real orders
USE_TP3=false                 # set to true to also place a third order targeting TP3
SPLIT_RANGE_ENTRIES=true      # one order per entry price in a range (XAUUSD BIG LOTS)
MOVE_SL_TO_BE_ON_TP1=true     # move remaining positions to breakeven when TP1 is hit

# Circuit breaker
CIRCUIT_BREAKER_SL_LIMIT=3   # consecutive SL hits before halting an account for the day
```

---

## Key Decisions

- **User account over bot:** Bots cannot read channel history and require admin access. User account via Telethon reads any channel the account is a member of.
- **Channel-aware parsing:** Each channel has its own parser profile. A message from Channel 1 is never parsed with Channel 2's rules.
- **Direction inference (Vip Thrilokh):** No direction keyword in messages — inferred by comparing SL vs Entry price. Direction keyword can also appear after the instrument name (e.g. `BTCUSD SELL 78702`).
- **Pipe-delimited fallback (Vip Thrilokh):** Parser tries the standard 3-line block format first; if it doesn't match, falls back to `_PIPE_SIGNAL_RE` for single-line `instrument @entry | Sl@ sl | Tp @tp` format. Direction inference applies to both.
- **LLM fallback:** Regex handles the known clean formats; Claude Haiku handles edge cases and new patterns cheaply.
- **Message classification first:** Every incoming message is classified (new_signal / trade_update / noise) before parsing. Only `new_signal` messages proceed to full parsing.
- **JSONL journal:** Append-only, easy to tail/grep, survives crashes without corruption.
- **Async throughout:** Telethon is async; keep the whole stack async to avoid blocking the listener.
- **MT5 Python library direct:** Orders are placed via the `MetaTrader5` Python library connecting to locally running MT5 terminals (Windows only). No MetaAPI cloud bridge needed.
- **MT5 sequential per-account:** The MT5 Python library is single-connection-at-a-time. For each signal, the bot iterates matching accounts: connect → place orders → `mt5.shutdown()` → next account. No extra threads.
- **MT5 order_send — no `comment` field:** The MetaTrader5 Python library rejects any `comment` key in the order request dict (returns error -2 even for an empty string). The `comment` key must be omitted entirely.
- **Telethon marked IDs:** `event.chat_id` returns negative marked IDs (e.g. `-1002540865305` for channel `2540865305`). `listener.py` normalises these back to raw positive IDs before looking up the parser.
- **outgoing=True on NewMessage:** When testing with own account messages, Telethon's `NewMessage` handler must include `outgoing=True` — by default it only fires for incoming messages.
- **create_task for orders:** Order execution is fired as an asyncio task so it never blocks the Telegram listener from receiving the next message.
- **DRY_RUN default true:** Orders are logged but never sent until DRY_RUN is explicitly set to false in .env — prevents accidental live trading during development.
- **Restart loop in main.py:** `asyncio.run(main())` is wrapped in `while True` — if the bot crashes or disconnects, it logs the error, waits 30s, and restarts automatically. Only `KeyboardInterrupt` breaks the loop.
- **PID lock file (`bot.pid`):** On startup, `main.py` writes its PID. Any subsequent launch checks if that PID is still alive; if yes, the new instance exits immediately. Prevents multiple instances fighting over the Telethon sqlite session file. PID file is removed on clean exit via `finally`.
- **Launch with `python.exe` directly:** Always start the bot with the full path to `python.exe` (`C:\Users\avaid\AppData\Local\Programs\Python\Python311\python.exe`), not `py.exe`. Using `py.exe` spawns two processes (launcher + interpreter) which looks like two instances; `python.exe` gives a single process.
- **Auto-restart on network loss:** The `while True` restart loop in `main.py` catches `ConnectionError` from Telethon and restarts after 30 s. Without this loop, a temporary network blip (e.g. `WinError 1232`) kills the process permanently — learned from a 2-day outage on 2026-05-12 where the live `main.py` was missing the loop.
- **Auto-start on boot via startup shortcut:** `TelegramTraderBot.lnk` in the Windows startup folder re-launches the bot after any machine reboot. The shortcut must target `python.exe` (not `py.exe`) and point to `Telegram_Trader-live\`, not `Telegram_Trader-master\`.
- **Git worktree isolation:** The live bot runs from `Telegram_Trader-live\` (locked to `master`). Development happens in `Telegram_Trader-master\` on `phase3-multi-account`. The bot's restart loop only reloads from the live folder, so dev edits never interrupt live trading.
- **GOLD prefix for XAUUSD BIG LOTS:** The channel sometimes sends signals with `GOLD` instead of `XAUUSD` as the instrument prefix. The `_SIGNAL_RE` regex accepts both: `^(?:XAUUSD|GOLD)`.
- **`from` keyword in XAUUSD BIG LOTS:** Channel sends `GOLD Buy from 4606/4595` — `from` appears where order type would be. Captured as order type group and normalised to `"market"` in `parse_signal`. The parsed `order_type` is stored in the journal but ignored when placing orders.
- **Shared live-price resolver:** `_resolve_order_type(direction, instrument, symbol, entry_price)` in `webhook.py` is the single source of market/limit/stop logic. All channels delegate to it.
- **XAUUSD BIG LOTS ignores signal's explicit order type:** The signal may say "Buy limit" but the bot resolves the actual MT5 order type from live price vs entry price — identical to Vip Thrilokh. The rationale: the signal's stated type can be stale by the time the bot sees it; live-price comparison is always accurate.
- **Split range entries:** With `SPLIT_RANGE_ENTRIES=true`, a range signal like `SELL 4583/4590` places two orders — one at each price — paired with TP1 and TP2 respectively. Entry prices are ordered so the price closest to filling first gets TP1.
- **LLM fallback is cache-backed:** `llm_classify.py` normalizes message text (strip emojis, numbers → `#`) to a stable cache key before checking the API. Repeated variants of the same phrase are free after the first call. Cache lives at `cache/classify_cache.json`.
- **Routing by asset class:** `webhook.py` reads `signal["asset_class"]` and sends the trade to all accounts whose `asset_classes` list contains that value. XAUUSD → commodity accounts, EURUSD → forex accounts, NAS100 → index accounts. Unmatched asset classes (e.g. crypto — no accounts configured yet) log a warning and skip the trade.
- **Dynamic lot sizing:** `lot = risk / (sl_ticks × tick_value)` using live MT5 `symbol_info()`. Per-account-group risk set via `MT5_<GROUP>_RISK_PCT` (% of equity) or `MT5_<GROUP>_RISK_USD` (fixed $ — overrides if set).
- **Circuit breaker:** Each account tracks `consecutive_sl`. After `CIRCUIT_BREAKER_SL_LIMIT` consecutive SL hits, `halted=True` — the account is skipped for all new signals until the calendar day rolls over (UTC). A TP hit resets the streak to 0. State persists in `journal/account_state.json`.
- **`_open` dict shape:** `{signal_id: {acc_name: [tickets]}}`. Old single-account format (`signal_id: [tickets]`) is migrated to `{signal_id: {"legacy": [tickets]}}` on load with a warning. `handle_close` and `handle_tp_hit` iterate all accounts in the dict for each signal.
- **Kathy ZIP instrument-based linking:** Kathy ZIP messages are never sent as replies, so `reply_to_msg_id` is always None. The `_instrument_to_signal` map in `JournalManager` links updates to signals by `(channel_id, instrument)` key. Updated on every new signal; `parse_update` returns `signal_id=None` and the listener resolves it after the fact.
- **Kathy ZIP multi-close:** A single Kathy ZIP message can close several instruments. `parse_update` uses `re.MULTILINE` anchored patterns and returns `list[dict]` — one dict per instrument found. `listener.py` handles `list | dict` from all parsers.
- **`update_type` passed to `handle_close`:** `listener.py` passes `entry["update_type"]` to `webhook.handle_close()` so the circuit breaker can distinguish `sl_hit` (increments streak) from `full_close` / `cancelled` (neutral).
- **`"Be hit"` is `full_close`, not `sl_hit`:** Breakeven hit closes at 0 P&L — not a real loss. Classifying it as `sl_hit` would incorrectly count it against the circuit breaker streak. Same applies to `"exit at be"` in Vip Thrilokh.
- **Bare `"Sl"` reply is `sl_hit`:** Vip Thrilokh sometimes posts a single word `"Sl"` as a reply to a signal. Matched by `^sl$` (reply-threaded so false positives are not possible) → `sl_hit`.
- **Index price thousands-dot separator:** Vip Thrilokh sends US30/NAS100/SPX500 prices with `.` as thousands separator (e.g. `50.027` meaning 50,027). Detected by `_parse_price()`: if the instrument is an index and the raw string matches `\d+\.\d{3}` (exactly 3 decimal digits), the dot is stripped before `float()` conversion. Forex pairs with 3dp (e.g. `1.082`) are unaffected because they are not index instruments.
- **NZDCAD and NZDCHF in webhook tables:** Both pairs appear in Kathy ZIP Forex Trades. Added to `SYMBOL_MAP` (`NZDCADm`, `NZDCHFm`), `PIP_SIZE` (0.0001), and `ENTRY_TOLERANCE_PIPS` (3 pips). Missing from `SYMBOL_MAP` would cause MT5 `symbol_info()` to return None and skip the order silently.

---

## Phase 2 — Order Type Logic

### All channels (dynamic — based on live price vs signal price)

Tolerance is **per-symbol** (defined in `ENTRY_TOLERANCE_PIPS` dict in `webhook.py`):

| Asset class | Tolerance | Rationale |
|---|---|---|
| Forex majors/minors | 3 pips | Tight spreads, stable price action |
| XAUUSD / XAGUSD | 5 pips | Wider spread + volatility |
| NAS100 / US30 | 10 pips | Index volatility |
| SPX500 | 5 pips | Less volatile than NAS/DOW |
| BTCUSD | 50 pips | 30-point swings are noise |
| ETHUSD | 20 pips | Less volatile than BTC |

Decision tree:
```
tolerance = ENTRY_TOLERANCE_PIPS[instrument] × PIP_SIZE[instrument]

|current - signal_entry| ≤ tolerance  →  Market order at live price

BUY signal:
  current > entry + tolerance  →  BUY_LIMIT  (price ran up; wait for pullback to signal level)
  current < entry - tolerance  →  BUY_STOP   (price hasn't reached entry; enter on rise)

SELL signal:
  current < entry - tolerance  →  SELL_LIMIT (price dropped; wait for pullback up to entry)
  current > entry + tolerance  →  SELL_STOP  (price hasn't dropped to entry; enter on fall)
```

### XAUUSD VIP BIG LOTS (range entries)

With `SPLIT_RANGE_ENTRIES=true` (default) and a range entry (e.g. `4583/4590`):
- **BUY range** → entry prices `[lo, hi]` — `lo` fills first as price drops; `lo` → TP1, `hi` → TP2
- **SELL range** → entry prices `[hi, lo]` — `hi` fills first as price rises; `hi` → TP1, `lo` → TP2
- Each entry price independently resolves to market / limit / stop based on live price

With `SPLIT_RANGE_ENTRIES=false`, only the single entry price closer to market is used.

### Auto-cancel triggers (all channels)

Pending limit/stop orders are cancelled automatically when any of these update types arrive:
- `cancelled`  — channel explicitly cancels ("Missed close it")
- `tp_hit`     — TP1 reached; pending orders that haven't filled are cancelled
- `full_close` — all TPs hit; trade is fully over

### Take Profit

- TP1 + TP2 are always placed when available in the signal (one order per TP level)
- TP3 is placed when `USE_TP3=true` in .env (default false)
- Each TP order is sized independently via `_calc_lot()` using the account's risk setting
- If the signal has fewer TPs than the active limit, only available TPs are used

### Position closing

- Full position close only (no partial closes yet)
- Triggered by: `full_close`, `sl_hit`, `cancelled`, `tp_hit` update types

---

## Account Routing

### Current setup — single account, all asset classes

One `"main"` account entry in `ACCOUNTS` (`webhook.py`) handles all asset classes
(commodity, forex, index, crypto). Credentials read from `MT5_LOGIN / MT5_PASSWORD / MT5_SERVER`.

To expand to per-class accounts later: add entries to `ACCOUNTS`, assign the relevant
`asset_classes` list to each, and set per-account env vars.

### Circuit breaker

```
per account:
  sl_hit              → consecutive_sl += 1
                         if consecutive_sl >= CIRCUIT_BREAKER_SL_LIMIT → halted = True
  tp_hit              → consecutive_sl = 0
  full_close/cancelled → no change to streak (includes BE hits — not real losses)
  new day (UTC)       → consecutive_sl = 0, halted = False

at place_order:
  if account["halted"] → skip this account, log warning
```

State persisted in `journal/account_state.json`. Adjust `CIRCUIT_BREAKER_SL_LIMIT` in `.env`.

### Dynamic lot sizing

```
risk = account["risk_usd"]  if set
     else  account_equity × account["risk_pct"] / 100

sl_ticks = abs(entry - sl) / symbol_info.trade_tick_size
lot      = risk / (sl_ticks × symbol_info.trade_tick_value)
lot      = clamp(round(lot / step) × step, volume_min, volume_max)
```

---

## Out of Scope (for now)

- Web UI or dashboard
- Multi-account Telegram support
- Auto partial-close on "close partials" channel message (Phase 2.1 remainder)

---

## Journal Design

- Each channel writes to its **own journal file** — no mixing between channels
- Journal files live in `journal/` named by channel:
  - `journal/vip_thrilokh.jsonl`
  - `journal/xauusd_big_lots.jsonl`
  - `journal/kathy_zip_forex.jsonl`
- New channels get their own journal file when onboarded

### Trade Linking (update → original signal)

Two strategies are used depending on whether the channel uses Telegram reply threading:

**Reply-threaded channels (Vip Thrilokh, XAUUSD BIG LOTS):**
- Update messages are Telegram replies to the original signal → `message.reply_to_msg_id` is set
- In-memory map: `{channel_id: {telegram_msg_id: signal_id}}`

**Non-reply channels (Kathy ZIP):**
- Updates are standalone messages with no reply_to
- In-memory map: `{(channel_id, instrument): signal_id}` → `_instrument_to_signal`
- `parse_update` returns `signal_id=None`; listener resolves it via `journal.resolve_by_instrument()`

Both maps are rebuilt from the JSONL journal on startup via `JournalManager.load_state()`.

### Journal Entry Schema

**New signal entry:**
```json
{
  "signal_id": "uuid",
  "telegram_msg_id": 12345,
  "message_type": "new_signal",
  "timestamp": "ISO8601",
  "source_channel_id": 2133117224,
  "source_channel_name": "Vip Thrilokh",
  "raw_message": "...",
  "asset_class": "forex | crypto | index | commodity",
  "instrument": "XAUUSD",
  "direction": "BUY | SELL",
  "order_type": "market | limit",
  "entry": 2345.00,
  "entry_range": [2340.00, 2350.00],
  "sl": 2310.00,
  "tp": [2370.00, 2400.00, 2450.00],
  "has_image": false,
  "parse_status": "parsed | partial | failed",
  "notes": ""
}
```

**Trade update entry:**
```json
{
  "signal_id": "uuid-of-original-signal",
  "telegram_msg_id": 12346,
  "telegram_reply_to_msg_id": 12345,
  "message_type": "trade_update",
  "timestamp": "ISO8601",
  "source_channel_id": 2133117224,
  "source_channel_name": "Vip Thrilokh",
  "raw_message": "Close partial and set be",
  "instrument": "USDCAD",
  "update_type": "breakeven | partial_close | full_close | tp_hit | sl_hit | cancelled | commentary",
  "notes": ""
}
```

---

## Running the Bot

The live bot runs from the `Telegram_Trader-live\` worktree (locked to `master`).
Development happens in `Telegram_Trader-master\` on `phase3-multi-account`.

### Auto-start on boot

A Windows startup shortcut at
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TelegramTraderBot.lnk`
launches the bot automatically whenever the user logs in. It runs hidden (`WindowStyle=7`).

Shortcut config (set via PowerShell `WScript.Shell`):
- **Target:** `C:\Users\avaid\AppData\Local\Programs\Python\Python311\python.exe`
- **Arguments:** `c:\Users\avaid\Downloads\Telegram_Trader-live\main.py`
- **WorkingDirectory:** `c:\Users\avaid\Downloads\Telegram_Trader-live`
- **WindowStyle:** `7` (minimised/hidden)

**Important:** the shortcut must use `python.exe`, not `py.exe`. `py.exe` is a launcher that spawns a second process — the PID lock will see two PIDs and may reject a valid restart.

### Manual start / stop

```powershell
# Start (single instance, hidden window, restart loop active)
Start-Process -FilePath "C:\Users\avaid\AppData\Local\Programs\Python\Python311\python.exe" `
  -ArgumentList "c:\Users\avaid\Downloads\Telegram_Trader-live\main.py" `
  -WorkingDirectory "c:\Users\avaid\Downloads\Telegram_Trader-live" `
  -WindowStyle Hidden

# Check it's running (should show exactly one python process)
Get-Process | Where-Object { $_.Name -match "python|py" } | Format-Table Id, Name, StartTime -AutoSize

# Tail the log
Get-Content "c:\Users\avaid\Downloads\Telegram_Trader-live\trader.log" -Tail 20 -Wait

# Kill the bot
Stop-Process -Id (Get-Content "c:\Users\avaid\Downloads\Telegram_Trader-live\bot.pid") -Force
Remove-Item "c:\Users\avaid\Downloads\Telegram_Trader-live\bot.pid"
```

---

## Open Questions

- [ ] More channels to be added later — onboard using the channel onboarding process above
- [ ] Crypto accounts (BTCUSD/ETHUSD from Vip Thrilokh) — add to ACCOUNTS list in webhook.py when ready

## TODO (deferred)

- [ ] **Phase 4 go-live:** merge `phase3-multi-account` → `master`, copy to live folder, restart bot. Live `.env` already has `MT5_LOGIN/PASSWORD/SERVER`; add `MT5_RISK_PCT` (default 1.0). No other credential changes needed — single account covers all asset classes.
- [ ] **NAS100 (and US30 / SPX500) orders blocked** — Exness symbol name unverified: live bot uses `"NAS100m"`, dev branch uses `"USTECm"`. Orders to NAS100 are skipped at runtime (`_BLOCKED_INSTRUMENTS` set in `webhook.py`) until the correct broker symbol is confirmed. US30/SPX500 are also index instruments and may have the same naming issue — block them too until verified.
- [ ] Auto partial-close when channel sends "close partials" (Phase 2.1 remainder)
- [ ] Online journal hosting (Google Sheets) — after trading is stable
- [ ] Kathy ZIP Swing Trades (channel 2186423407) — separate channel, not yet onboarded
