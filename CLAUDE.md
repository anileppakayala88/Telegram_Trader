# Telegram Trader

## Project Overview

Reads trade signal messages from a Telegram channel/group, parses them into structured trade objects, logs them to a human-readable journal, and fires orders to a live broker via the MetaTrader5 Python library (Phase 2).

---

## Goals by Phase

### Phase 1 — Signal Reader + Journal (current)
- Connect to Telegram as a user account (Telethon)
- Listen to multiple channels simultaneously for new messages
- Parse each message into a structured trade signal:
  - Instrument (e.g. XAUUSD, EURUSD, NAS100, BTC/USD)
  - Direction (BUY / SELL)
  - Entry price (or range)
  - Take Profit levels (TP1, TP2, TP3 — variable)
  - Stop Loss
  - Asset class (forex / indices / crypto / futures)
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
- Each order gets its own lot size (LOT_SIZE) and its own TP target
- XAUUSD BIG LOTS range entries: one order per entry price, paired with successive TP levels (SPLIT_RANGE_ENTRIES)
- Move SL to breakeven when TP1 is hit (MOVE_SL_TO_BE_ON_TP1); or close all remaining positions
- Auto partial-close on "close partials" channel message: not yet implemented

### Phase 3 — LLM fallback classifier (complete)
- `llm_classify.py`: Claude Haiku fallback for messages that don't match any regex pattern
- Cache-backed: normalized message → result stored in `cache/classify_cache.json`
- Falls back to `"commentary"` when ANTHROPIC_API_KEY is not set

### Phase 4 — TradingView → MT5 via Telegram (complete)
- TradingView alerts fire to trade-log (Vercel), which posts a structured JSON message to a private Telegram channel (xauusd_bot)
- The bot reads that channel via Telethon — same listener loop, no new infrastructure
- `channels/tv_signals.py` parses the JSON messages and routes to MT5 exactly like any other channel
- No ngrok, no HTTP server — Telegram is the message bus

---

## Architecture

```
TradingView alert
      |
trade-log (Vercel) — logs to GitHub, posts JSON to Telegram channel (xauusd_bot)
      |
      ↓
Telegram (multiple channels, including xauusd_bot for TV signals)
        |
   Telethon Listener
        |
   Channel Router (identifies source channel)
        |
   Channel-Specific Parser
   (per-channel profile — regex fast path + LLM fallback; TV channel uses JSON)
        |
   Message Classifier
   (new signal | trade update | noise)
        |
   ┌────┴────┐
   │         │
Journal    Signal Object
(JSONL)    (structured dict)
                |
         [Phase 2] webhook.py
                |
         MetaTrader5 Python library
                |
         MT5 terminal (local, Windows)
                |
         Broker account (Exness)
```

---

## Lightweight Design Principles

- **Single process, single async event loop** — no threads, no subprocesses, no queues
- **Minimal dependencies** — only `telethon` and `python-dotenv` (pip installable); everything else is Python stdlib
- **Module-level functions for parsers** — no class instantiation overhead; each channel is a plain Python module
- **Append-only JSONL** — no database, no ORM; survives crashes without corruption
- **In-memory state is minimal** — only open signal IDs kept in memory (two dicts); full history stays on disk
- **Fail per message, not per process** — each message is wrapped in try/except; one bad message never kills the listener
- **Logging to file + stdout** — single `logging` config, UTF-8 encoded log file

## Tech Stack

- **Language:** Python 3.11+
- **Telegram:** Telethon (user account MTProto API)
- **Auth:** Session file (`session_fetch.session`) — created once via `auth.py`
- **Parsing:** Regex fast path + Claude Haiku fallback (`llm_classify.py`) for unmatched messages; cache-backed to avoid repeat API calls
- **Journal:** Append-only JSONL (`journal/<channel>.jsonl`) — one file per channel
- **Order execution:** MetaTrader5 Python library (`MetaTrader5`) — connects directly to a locally running MT5 terminal (Windows only)
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
├── analyze_msgs.py           # fetch last 1000 msgs per channel, classify + display grouped report
├── list_channels.py          # list all channels the account is in
├── test_replay.py            # replay historical messages through the full pipeline
├── generate_viewer.py        # generate journal_viewer.html from journal JSONL files
├── journal_viewer_template.html  # HTML template for the journal viewer
├── main.py                   # entry point — restart loop + PID lock + starts listener
├── listener.py               # Telethon event handler — routes messages to channel parsers
├── journal.py                # JSONL writer + in-memory state manager
├── webhook.py                # MT5 order execution (Phase 2)
├── bot.pid                   # PID of running bot — prevents duplicate instances (auto-created)
├── channels/
│   ├── __init__.py           # channel registry: maps channel ID → parser module
│   ├── vip_thrilokh.py       # parser for Channel 1 (Vip Thrilokh)
│   ├── xauusd_big_lots.py    # parser for Channel 2 (XAUUSD VIP BIG LOTS)
│   └── tv_signals.py         # parser for Channel 3 (TradingView via xauusd_bot Telegram channel)
└── journal/                  # created at runtime
    ├── vip_thrilokh.jsonl    # append-only signal log for channel 1
    ├── xauusd_big_lots.jsonl # append-only signal log for channel 2
    ├── tv_signals.jsonl      # append-only signal log for TradingView signals
    ├── positions.json        # persisted MT5 position/order IDs (Phase 2)
    └── tv_positions.json     # persisted TradingView open positions (ticker → signal_id)
```

### Adding a New Channel
1. Pull sample messages via `fetch_samples.py`
2. Add channel ID + name to `channels/__init__.py` registry and `journal.py` CHANNEL_NAMES
3. Create `channels/<name>.py` implementing: `CHANNEL_NAME`, `CHANNEL_ID`, `classify(msg)`, `parse_signal(msg)`, `parse_update(msg, signal_id)`
4. Add a new journal file path in `journal/`

---

## Telegram Access

- **Method:** Telethon user account (MTProto API) — not a bot
- **Credentials:** stored in `.env` (never committed)
- **Session file:** `session_fetch.session` — created by running `auth.py` once; reused on all subsequent runs
- **Account phone:** Canadian number (+1 416 528 7743)

### Active Channels

| Channel Name         | Telegram ID  | Username     | Asset Focus                        |
|----------------------|--------------|------------- |------------------------------------|
| Vip Thrilokh         | 2133117224   | no-username  | Multi-asset (BTC, Forex, NQ)       |
| XAUUSD VIP BIG LOTS  | 1481325093   | no-username  | XAUUSD only                        |
| Test_TV_3min         | 2540865305   | no-username  | Dev/test — uses Vip Thrilokh parser |
| xauusd_bot           | 3720726531   | no-username  | TradingView signals (all instruments) |

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

**Direction inference rule:**
- `SL > Entry` → SELL
- `SL < Entry` → BUY

**Separator:** `@` (with optional `.` after field name, e.g. `Tp.`)
**TPs:** Single TP only
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
- `"Set be"` — move SL to breakeven
- `"Close partial and set be"` — take partial profit, move SL to breakeven
- `"Close partial and set sl as be"` — same
- `"Keep Btc sl as be"` — reminder to hold breakeven SL
- `"Am closing this Btc trade here"` — manual close (`full_close`)
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
- `"XAUUSD TP1 HIT RUNNING X PIPS"` — first TP reached
- `"XAUUSD TP2 HIT RUNNING X PIPS"` — second TP reached
- `"XAUUSD ALL TP HIT RUNNING X PIPS"` — all TPs hit
- `"Be hit"` — stop loss moved to breakeven was hit
- `"X PIPS PROFIT"` — running profit update
- `"Missed close it"` / `"Just missed our limit"` / `"Missed"` / `"Delete"` — entry not triggered, cancel

**Noise messages (ignore):**
- `"React ❤️"` — engagement prompt
- `"I'm in"` — confirmation of own entry
- `"Go again"` — commentary

---

### Channel 3 — TradingView Signals via xauusd_bot (ID: 3720726531)

**Source:** Private Telegram channel (`xauusd_bot`) that trade-log (Vercel) posts to when a TradingView alert fires.
**Bot token:** Telegram bot `8955279019:...` posts messages; Telethon user account reads them.
**Parser:** `channels/tv_signals.py` — JSON format, no regex needed.

**Entry message format (Vercel → Telegram):**
```json
{"tv":"entry","id":"MNQ1!-1748000000","ticker":"MNQ1!","action":"buy","price":19500,"sl":19450,"tp1":19600,"tp2":19700}
```

**Exit message format:**
```json
{"tv":"exit","id":"MNQ1!-1748000000","ticker":"MNQ1!","action":"exit","tp":"TP1","price":19600}
{"tv":"exit","id":"MNQ1!-1748000000","ticker":"MNQ1!","action":"sl","price":19450}
```

**Signal linking:** `vercel_id` (the `id` field) links exits to their entry. Parser stores `vercel_id → signal_id` in `journal/tv_positions.json` and looks it up on exit. This survives bot restarts.

**Ticker mapping (TradingView → MT5 instrument):**
| TradingView | MT5 instrument |
|---|---|
| MNQ1!, NQ1! | NAS100 |
| MES1!, ES1! | SPX500 |
| MYM1!, YM1! | US30 |
| MGC1!, GC1! | XAUUSD |
| XAUUSD, EURUSD, GBPUSD, etc. | pass-through |

**Update types:**
- `action=exit, tp=TP1` → `tp_hit` (move SL to breakeven on remaining positions)
- `action=exit` (no TP1 label) → `full_close`
- `action=sl` → `sl_hit`

**Vercel env vars required (trade-log project):**
- `TELEGRAM_BOT_TOKEN` — bot token that posts to xauusd_bot channel
- `TELEGRAM_CHANNEL_ID` — `-1003720726531`

---

## Signal Schema

```json
{
  "id": "uuid",
  "timestamp": "ISO8601",
  "source_channel_id": 2133117224,
  "source_channel_name": "Vip Thrilokh",
  "raw_message": "original text",
  "message_type": "new_signal | trade_update | noise",
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
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_PHONE=
ANTHROPIC_API_KEY=            # optional — enables Claude Haiku LLM fallback classifier
MT5_LOGIN=                    # MT5 account number
MT5_PASSWORD=                 # MT5 account password
MT5_SERVER=                   # broker server name shown on MT5 login screen
DRY_RUN=true                  # set to false to place real orders
USE_TP3=false                 # set to true to also place a third order targeting TP3
SPLIT_RANGE_ENTRIES=true      # place one order per entry price in a range (XAUUSD BIG LOTS)
MOVE_SL_TO_BE_ON_TP1=true     # move remaining positions to breakeven when TP1 is hit
```

**Vercel env vars (trade-log project — set in Vercel dashboard):**
```
TELEGRAM_BOT_TOKEN=<bot token that posts to xauusd_bot channel>
TELEGRAM_CHANNEL_ID=-1003720726531
```

---

## Key Decisions

- **User account over bot:** Bots cannot read channel history and require admin access. User account via Telethon reads any channel the account is a member of.
- **Channel-aware parsing:** Each channel has its own parser profile. A message from Channel 1 is never parsed with Channel 2's rules.
- **Direction inference (Channel 1):** No direction keyword in messages — inferred by comparing SL vs Entry price. Direction keyword can also appear after the instrument name (e.g. `BTCUSD SELL 78702`).
- **LLM fallback:** Regex handles the known clean formats; Claude Haiku handles edge cases and new patterns cheaply.
- **Message classification first:** Every incoming message is classified (new_signal / trade_update / noise) before parsing. Only `new_signal` messages proceed to full parsing.
- **JSONL journal:** Append-only, easy to tail/grep, survives crashes without corruption.
- **Async throughout:** Telethon is async; keep the whole stack async to avoid blocking the listener.
- **MT5 Python library direct:** Orders are placed via the `MetaTrader5` Python library connecting to a locally running MT5 terminal (Windows only). No MetaAPI cloud bridge needed.
- **MT5 order_send — no `comment` field:** The MetaTrader5 Python library rejects any `comment` key in the order request dict (returns error -2 even for an empty string). The `comment` key must be omitted entirely.
- **Telethon marked IDs:** `event.chat_id` returns negative marked IDs (e.g. `-1002540865305` for channel `2540865305`). `listener.py` normalises these back to raw positive IDs before looking up the parser.
- **outgoing=True on NewMessage:** When testing with own account messages, Telethon's `NewMessage` handler must include `outgoing=True` — by default it only fires for incoming messages.
- **create_task for orders:** Order execution is fired as an asyncio task so it never blocks the Telegram listener from receiving the next message.
- **DRY_RUN default true:** Orders are logged but never sent until DRY_RUN is explicitly set to false in .env — prevents accidental live trading during development.
- **Restart loop in main.py:** `asyncio.run(main())` is wrapped in `while True` — if the bot crashes or disconnects, it logs the error, waits 30s, and restarts automatically. Only `KeyboardInterrupt` breaks the loop.
- **PID lock file (`bot.pid`):** On startup, `main.py` writes its PID. Any subsequent launch checks if that PID is still alive; if yes, the new instance exits immediately. Prevents multiple instances fighting over the Telethon sqlite session file. PID file is removed on clean exit via `finally`.
- **Launch with `python.exe` directly:** Always start the bot with the full path to `python.exe` (`C:\Users\avaid\AppData\Local\Programs\Python\Python311\python.exe`), not `py.exe`. Using `py.exe` spawns two processes (launcher + interpreter) which looks like two instances; `python.exe` gives a single process.
- **GOLD prefix for XAUUSD BIG LOTS:** The channel sometimes sends signals with `GOLD` instead of `XAUUSD` as the instrument prefix. The `_SIGNAL_RE` regex accepts both: `^(?:XAUUSD|GOLD)`.
- **`from` keyword in XAUUSD BIG LOTS:** Channel sends `GOLD Buy from 4606/4595` — `from` appears where order type would be. Captured as order type group and normalised to `"market"` in `parse_signal`. The parsed `order_type` is stored in the journal but ignored when placing orders.
- **Shared live-price resolver:** `_resolve_order_type(direction, instrument, symbol, entry_price)` in `webhook.py` is the single source of market/limit/stop logic. Both channels delegate to it, preventing drift between channel-specific implementations.
- **XAUUSD BIG LOTS ignores signal's explicit order type:** The signal may say "Buy limit" but the bot resolves the actual MT5 order type from live price vs entry price — identical to Vip Thrilokh. The rationale: the signal's stated type can be stale by the time the bot sees it; live-price comparison is always accurate.
- **Split range entries:** With `SPLIT_RANGE_ENTRIES=true`, a range signal like `SELL 4583/4590` places two orders — one at each price — paired with TP1 and TP2 respectively. Entry prices are ordered so the price closest to filling first gets TP1.
- **LLM fallback is cache-backed:** `llm_classify.py` normalizes message text (strip emojis, numbers → `#`) to a stable cache key before checking the API. Repeated variants of the same phrase are free after the first call. Cache lives at `cache/classify_cache.json`.
- **Never run two bot instances on the same Telegram session:** Two Telethon clients sharing `session_fetch.session` causes Telegram to deliver updates to only one of them unpredictably. The other bot silently stops receiving messages. Always kill the old process before starting a new one. The PID lock (`bot.pid`) prevents this within the same directory but not across directories.
- **TV signals verified end-to-end (2026-05-25):** TradingView → Vercel → xauusd_bot Telegram channel → Telethon → tv_signals.py → MT5 order_send confirmed working. XAUUSD orders return `retcode=10018` on weekends (market closed) — this is expected, not a bug.

---

## Phase 2 — Order Type Logic

### Vip Thrilokh (dynamic — based on live price vs signal price)

Tolerance is **per-symbol** (defined in `ENTRY_TOLERANCE_PIPS` dict in `webhook.py`):

| Asset class | Tolerance | Rationale |
|---|---|---|
| Forex majors/minors | 3 pips | Tight spreads, stable price action |
| XAUUSD / XAGUSD | 5 pips | Wider spread + volatility |
| NAS100 / US30 | 10 pips | Index volatility |
| SPX500 | 5 pips | Less volatile than NAS/DOW |
| BTCUSD | 50 pips | 30-point swings are noise |
| ETHUSD | 20 pips | Less volatile than BTC |

Dollar value of tolerance at 0.01 lot (micro lot):

| Instrument | Tolerance | $ value |
|---|---|---|
| Forex (e.g. EURUSD) | 3 pips | ~$0.30 |
| XAUUSD | 5 pips | ~$0.50 |
| NAS100 | 10 pips | ~$1.00 |
| BTCUSD | 50 pips | ~$5.00 |

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

Example — BUY signal XAUUSD entry $2000, tolerance $0.50:
- Live ask $2000.30 → Market order (within tolerance)
- Live ask $2001.80 → BUY LIMIT at $2000 (price ran above entry, wait for pullback)
- Live ask $1998.00 → BUY STOP  at $2000 (price below entry, enter when it rises)

Example — SELL signal XAUUSD entry $2000, tolerance $0.50:
- Live bid $1999.80 → Market order (within tolerance)
- Live bid $1998.20 → SELL LIMIT at $2000 (price fell below entry, wait for bounce back up)
- Live bid $2001.80 → SELL STOP  at $2000 (price above entry, enter when it drops)

LIMIT = "enter at a better price than now" (pending below market for BUY, above market for SELL)
STOP  = "enter when price confirms the move by reaching my level"

### XAUUSD VIP BIG LOTS (dynamic — same live-price logic as Vip Thrilokh)

The explicit order type in the signal text ("Buy limit", "Sell limit", `from`) is **parsed but ignored** for order placement. The actual order type is resolved by comparing the live price to each entry price using `_resolve_order_type` — the same shared helper Vip Thrilokh uses.

With `SPLIT_RANGE_ENTRIES=true` (default) and a range entry (e.g. `4583/4590`):
- **BUY range** → entry prices `[lo, hi]` — `lo` fills first as price drops; `lo` → TP1, `hi` → TP2
- **SELL range** → entry prices `[hi, lo]` — `hi` fills first as price rises; `hi` → TP1, `lo` → TP2
- Each entry price independently resolves to market / limit / stop based on live price

With `SPLIT_RANGE_ENTRIES=false`, only a single entry price is used (closer price to market), and one order per TP level is placed at that price.

### Auto-cancel triggers (both channels)

Pending limit/stop orders are cancelled automatically when any of these update types arrive:
- `cancelled`  — channel explicitly cancels ("Missed close it")
- `tp_hit`     — TP1 reached; if order still pending, price blew past entry without filling
- `full_close` — all TPs hit; trade is fully over

### Take Profit

- TP1 + TP2 are always placed when available in the signal (one order per TP level)
- TP3 is placed when `USE_TP3=true` in .env (default false)
- Each TP order is sized at `LOT_SIZE` (0.01 lot by default)
- If the signal has fewer TPs than the active limit, only available TPs are used

### Position closing

- Full position close only (no partial closes in Phase 2)
- Triggered by: `full_close`, `sl_hit`, `cancelled`, `tp_hit` update types

---

## Out of Scope (for now)

- Web UI or dashboard
- Multi-account Telegram support
- Risk management / position sizing
- Auto partial-close on "close partials" channel message (Phase 2.1 remainder)

---

## Journal Design

- Each channel writes to its **own journal file** — no mixing between channels
- Journal files live in `journal/` named by channel:
  - `journal/vip_thrilokh.jsonl`
  - `journal/xauusd_big_lots.jsonl`
- New channels get their own journal file when onboarded

### Trade Linking (update → original signal)

Trade update messages are sent as **Telegram replies** to the original signal message. This means Telethon gives us `message.reply_to_msg_id` — the exact Telegram message ID of the original signal.

Linking strategy:
- When a `new_signal` is parsed, store its Telegram `message_id` alongside the `signal_id` in the journal and in memory
- When a `trade_update` arrives, check `message.reply_to_msg_id`:
  - If set → look up the matching `signal_id` from the in-memory map (`telegram_msg_id → signal_id`)
  - If not set → fall back to most recent open signal on that channel (safety net only)
- Updates are written as separate JSONL entries with `message_type: trade_update` and the resolved `signal_id`
- A signal is considered **closed** when any of these update messages arrive:
  - Channel 1: `"Am closing..."`, `"close partials"` (full close implied)
  - Channel 2: `"ALL TP HIT"`, `"Be hit"`, `"Missed close it"`, `"Just missed our limit"`
- On restart, the in-memory `telegram_msg_id → signal_id` map is rebuilt by replaying the journal

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
  "update_type": "breakeven | partial_close | full_close | tp_hit | cancelled | commentary",
  "notes": ""
}
```

---

## Running the Bot

```powershell
# Start (single instance, hidden window, restart loop active)
Start-Process -FilePath "C:\Users\avaid\AppData\Local\Programs\Python\Python311\python.exe" `
  -ArgumentList "c:\Users\avaid\Downloads\Telegram_Trader-master\main.py" `
  -WorkingDirectory "c:\Users\avaid\Downloads\Telegram_Trader-master" `
  -WindowStyle Hidden

# Check it's running (should show exactly one python process)
Get-Process | Where-Object { $_.Name -match "python|py" } | Format-Table Id, Name, StartTime -AutoSize

# Tail the log
Get-Content "c:\Users\avaid\Downloads\Telegram_Trader-master\trader.log" -Tail 20 -Wait

# Kill the bot
Stop-Process -Id (Get-Content "c:\Users\avaid\Downloads\Telegram_Trader-master\bot.pid") -Force
Remove-Item "c:\Users\avaid\Downloads\Telegram_Trader-master\bot.pid"
```

---

## Open Questions

- [ ] More channels to be added later — onboard using the same channel onboarding process defined above

## TODO (deferred)

- [ ] Auto partial-close when channel sends "close partials" (Phase 2.1 remainder)
- [ ] Online journal hosting (Google Sheets) — after Phase 1 logic is working
- [ ] Vip Thrilokh: occasional signals still arrive in old pipe-delimited format (e.g. `eurusd @1.17052 | Sl@ 1.17161 | Tp @1.16510`) — currently classified as noise. Add fallback regex if this recurs.

### Multi-account routing + risk management (planned)

Route signals to 6 MT5 accounts based on asset class:
- Acc1 & Acc4 → XAUUSD (commodity)
- Acc2 & Acc5 → Forex pairs
- Acc3 & Acc6 → NQ / indices

Each account gets its own credentials (`MT5_1_LOGIN` etc.), `daily_loss_limit`, and runtime state in `journal/account_state.json`.

**Lot sizing:** Dynamic at order time — `lot = risk_amount / (sl_distance / tick_size × tick_value)` using live MT5 `symbol_info()`. No hardcoded table.

**Circuit breaker:** After 3 consecutive SLs on an account, mark it `halted=true` and skip all further orders for that account until daily reset. Reset state (consecutive_sl, daily_loss, halted) when the calendar date changes.

**Key implementation notes:**
- MT5 Python library is single-connection-at-a-time → connect → operate → `mt5.shutdown()` → connect next account (sequential, no extra threads)
- `_open` dict shape changes from `signal_id → [tickets]` to `signal_id → {acc_name: [tickets]}`
- `_connect(account)` parameterised; account config list defined at top of `webhook.py`
- Routing: `asset_class` in `account["instruments"]` and `not account["halted"]`
- SL hit detection: check `position.profit < 0` on close; TP hit resets consecutive count to 0
- Open questions before implementing: MT5 terminal paths for all 6 accounts; per-trade risk (fixed $ or % equity); daily reset time (UTC midnight vs market open)
