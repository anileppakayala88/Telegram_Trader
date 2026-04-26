# Telegram Trader

## Project Overview

Reads trade signal messages from a Telegram channel/group, parses them into structured trade objects, logs them to a human-readable journal, and fires orders to a live broker via MetaAPI (Phase 2).

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

### Phase 2 — MetaAPI Order Execution (current)
- Connect to broker MT4/MT5 account via MetaAPI cloud bridge (no local terminal)
- On new_signal: determine order type from live price vs signal price, place order with SL + TP1
- On exit update: close open position or cancel pending order automatically
- Persist open position/order IDs to disk so restarts don't lose track of live trades
- DRY_RUN mode for safe testing without real order execution

### Phase 2.1 — Multiple TPs + Partial Closes (deferred)
- Split position across TP1 / TP2 / TP3
- Auto partial-close when channel sends "close partials"
- Move SL to breakeven after partial close

### Phase 3 — MT5 Direct (future consideration)
- Optionally route orders directly to MT5 via Python MT5 library
- Not in scope until Phase 2 is validated

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
(JSONL)    (structured dict)
                |
         [Phase 2] webhook.py
                |
         MetaAPI cloud bridge
                |
         MT4/MT5 broker account
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
- **Parsing:** Regex only — Claude API (claude-haiku) fallback to be added once API key is available (TODO)
- **Journal:** Append-only JSONL (`journal/<channel>.jsonl`) — one file per channel
- **Order execution:** MetaAPI cloud SDK (`metaapi-cloud-sdk`) — Phase 2
- **Config:** `python-dotenv` for credentials
- **pip dependencies:** `telethon`, `python-dotenv`, `metaapi-cloud-sdk`

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
├── list_channels.py          # list all channels the account is in
├── test_replay.py            # replay historical messages through the full pipeline
├── generate_viewer.py        # generate journal_viewer.html from journal JSONL files
├── journal_viewer_template.html  # HTML template for the journal viewer
├── main.py                   # entry point — creates client, loads state, starts listener
├── listener.py               # Telethon event handler — routes messages to channel parsers
├── journal.py                # JSONL writer + in-memory state manager
├── webhook.py                # MetaAPI order execution (Phase 2)
├── channels/
│   ├── __init__.py           # channel registry: maps channel ID → parser module
│   ├── vip_thrilokh.py       # parser for Channel 1 (Vip Thrilokh)
│   └── xauusd_big_lots.py    # parser for Channel 2 (XAUUSD VIP BIG LOTS)
└── journal/                  # created at runtime
    ├── vip_thrilokh.jsonl    # append-only signal log for channel 1
    ├── xauusd_big_lots.jsonl # append-only signal log for channel 2
    └── positions.json        # persisted MetaAPI position/order IDs (Phase 2)
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

Access channels by **numeric ID** (no username available for either).

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

**Signal format:** Explicit direction + order type, multiple TPs, entry can be a range. Parser scans all lines for the XAUUSD signal line so leading emoji lines are handled transparently.

```
XAUUSD Buy limit 4664/4656
Sl 4643
TP 4669
TP 4676
TP 4720 USE BIG LOTS ✅✔️
```

**Direction:** Explicitly stated — `Buy` / `Sell` + order type (`limit` / market implied)
**Entry:** Single price or range (`4664/4656` — use lower for buy limit, upper for sell limit)
**TPs:** Multiple (TP1, TP2, TP3) — each on its own line starting with `TP`
**Images:** Rarely attached
**Instrument:** XAUUSD only

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
ANTHROPIC_API_KEY=       # for LLM-assisted parsing fallback (TODO)
METAAPI_TOKEN=           # from app.metaapi.cloud/token (Phase 2)
METAAPI_ACCOUNT_ID=      # MT4/MT5 account ID registered on MetaAPI (Phase 2)
DRY_RUN=true             # set to false to place real orders (Phase 2)
```

---

## Key Decisions

- **User account over bot:** Bots cannot read channel history and require admin access. User account via Telethon reads any channel the account is a member of.
- **Channel-aware parsing:** Each channel has its own parser profile. A message from Channel 1 is never parsed with Channel 2's rules.
- **Direction inference (Channel 1):** No direction keyword in messages — inferred by comparing SL vs Entry price.
- **LLM fallback:** Regex handles the known clean formats; Claude Haiku handles edge cases and new patterns cheaply.
- **Message classification first:** Every incoming message is classified (new_signal / trade_update / noise) before parsing. Only `new_signal` messages proceed to full parsing.
- **JSONL journal:** Append-only, easy to tail/grep, survives crashes without corruption.
- **Async throughout:** Telethon is async; keep the whole stack async to avoid blocking the listener.
- **MetaAPI over local MT5:** Broker account is on a prop firm that uses MT5. MetaAPI provides a cloud bridge so no local MT5 terminal is required.
- **create_task for orders:** Order execution is fired as an asyncio task so it never blocks the Telegram listener from receiving the next message.
- **DRY_RUN default true:** Orders are logged but never sent until DRY_RUN is explicitly set to false in .env — prevents accidental live trading during development.

---

## Phase 2 — Order Type Logic

### Vip Thrilokh (dynamic — based on live price vs signal price)

```
tolerance = ENTRY_TOLERANCE_PIPS × PIP_SIZE[instrument]

|current - signal_entry| ≤ tolerance  →  Market order (enter now)

BUY signal:
  current > entry + tolerance  →  BUY_LIMIT  (price ran up; wait for pullback to signal level)
  current < entry - tolerance  →  BUY_STOP   (price hasn't reached entry; enter on rise)

SELL signal:
  current < entry - tolerance  →  SELL_LIMIT (price dropped; wait for pullback up to entry)
  current > entry + tolerance  →  SELL_STOP  (price hasn't dropped to entry; enter on fall)
```

### XAUUSD VIP BIG LOTS (explicit — from signal text)

- Order type stated directly: "Buy limit", "Sell limit", or market implied
- Entry range (e.g. 4664/4656): use closer price to market for quicker fill
  - BUY limit  → higher of the two (4664)
  - SELL limit → lower  of the two (4656)

### Auto-cancel triggers (both channels)

Pending limit/stop orders are cancelled automatically when any of these update types arrive:
- `cancelled`  — channel explicitly cancels ("Missed close it")
- `tp_hit`     — TP1 reached; if order still pending, price blew past entry without filling
- `full_close` — all TPs hit; trade is fully over

### Take Profit

- TP1 only for Phase 2
- TP2 / TP3 splitting deferred to Phase 2.1

### Position closing

- Full position close only (no partial closes in Phase 2)
- Triggered by: `full_close`, `sl_hit`, `cancelled`, `tp_hit` update types

---

## Out of Scope (for now)

- Web UI or dashboard
- Multi-account Telegram support
- Risk management / position sizing
- Multiple TP splitting and partial closes (Phase 2.1)

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

## Open Questions

- [ ] TradingView webhook URL format — confirm when Phase 2 begins
- [ ] More channels to be added later — onboard using the same channel onboarding process defined above

## TODO (deferred)

- [ ] Add Claude API (claude-haiku) fallback parser once Anthropic API key is available
- [ ] Online journal hosting (Google Sheets) — after Phase 1 logic is working
