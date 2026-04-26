# Telegram Trader

Listens to Telegram trade signal channels, parses signals into structured objects, logs them to a journal, and places live orders on a broker account via MetaAPI.

---

## How It Works

```
Telegram channels (Vip Thrilokh, XAUUSD VIP BIG LOTS)
                    |
              Telethon (user account — reads channels in real time)
                    |
              Channel-specific parser
              (classifies as signal / update / noise, extracts instrument, direction, entry, SL, TP)
                    |
           ┌────────┴────────┐
           │                 │
      journal/             webhook.py
   <channel>.jsonl          │
  (append-only log)    MetaAPI cloud
                            │
                      MT4/MT5 broker account
                      (prop firm — no local terminal needed)
```

Every message goes through three steps:
1. **Classify** — is this a new trade signal, a trade update, or noise?
2. **Parse** — extract instrument, direction, entry, SL, TP into a structured dict
3. **Act** — write to journal + place/cancel order on broker

---

## Project Structure

```
Telegram_Trader/
├── .env                          # credentials — never committed
├── requirements.txt
├── auth.py                       # run once to authenticate Telegram session
├── main.py                       # entry point — start the bot here
├── listener.py                   # Telethon event loop — receives messages and routes them
├── journal.py                    # writes JSONL entries, tracks signal IDs in memory
├── webhook.py                    # MetaAPI integration — places and closes orders
├── channels/
│   ├── __init__.py               # maps channel ID → parser module
│   ├── vip_thrilokh.py           # parser for Vip Thrilokh channel
│   └── xauusd_big_lots.py        # parser for XAUUSD VIP BIG LOTS channel
├── journal/
│   ├── vip_thrilokh.jsonl        # trade log for channel 1
│   ├── xauusd_big_lots.jsonl     # trade log for channel 2
│   └── positions.json            # MetaAPI position/order IDs (survives restarts)
├── fetch_samples.py              # pull historical messages for testing
├── test_replay.py                # replay historical messages through the full pipeline
├── generate_viewer.py            # build journal_viewer.html from JSONL files
└── journal_viewer_template.html  # HTML template for the browser journal viewer
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create `.env`

```
TELEGRAM_API_ID=        # from my.telegram.org
TELEGRAM_API_HASH=      # from my.telegram.org
TELEGRAM_PHONE=         # your phone number including country code

METAAPI_TOKEN=          # from app.metaapi.cloud/token
METAAPI_ACCOUNT_ID=     # your MT4/MT5 account ID registered on MetaAPI

DRY_RUN=true            # set to false when ready to place real orders
```

### 3. Authenticate Telegram (once only)

```bash
python auth.py
```

This creates `session_fetch.session`. You only need to do this once — the session is reused on every subsequent run.

### 4. Run the bot

```bash
python main.py
```

---

## Active Channels

| Channel | Telegram ID | Instruments |
|---|---|---|
| Vip Thrilokh | 2133117224 | XAUUSD, BTCUSD, EURUSD, USDJPY, USDCAD, AUDUSD, USDCHF, NAS100 |
| XAUUSD VIP BIG LOTS | 1481325093 | XAUUSD only |

---

## Channel Signal Formats

### Vip Thrilokh

Minimal 3-line format. Direction is inferred from SL position when not stated explicitly. Leading emojis before the instrument line are handled transparently.

```
Btc @ 74220
Sl  @ 75647        ← SL > Entry → SELL
Tp. @ 70450
```

```
Buy xauusd @ 4674
Sl @ 4660
Tp1 @ 4688
Tp2 @ 4706
```

**Direction rule:** `SL > Entry → SELL`, `SL < Entry → BUY`

### XAUUSD VIP BIG LOTS

Explicit direction + order type + multiple TPs. Entry can be a price range. Leading emojis before the XAUUSD line are handled transparently.

```
XAUUSD Buy limit 4664/4656
Sl 4643
TP 4669
TP 4676
TP 4720
```

---

## Order Execution Logic

### Vip Thrilokh — dynamic order type

The bot fetches the live market price when a signal arrives and compares it to the signal price:

| Condition | Order type |
|---|---|
| Price within 3 pips of signal | Market order |
| BUY signal, price above entry | Buy Limit — wait for pullback |
| BUY signal, price below entry | Buy Stop — enter when price rises to signal level |
| SELL signal, price below entry | Sell Limit — wait for pullback up |
| SELL signal, price above entry | Sell Stop — enter when price drops to signal level |

The pip tolerance (default 3) and pip size per instrument are configurable at the top of `webhook.py`.

### XAUUSD BIG LOTS — explicit order type

Order type is taken directly from the signal text (`buy limit`, `sell limit`, or market). For entry ranges like `4664/4656`:
- BUY → uses the **higher** price (4664) — closer to market, fills quicker
- SELL → uses the **lower** price (4656) — closer to market, fills quicker

### Take Profit

TP1 only. The first TP level in the signal is attached to the order. Multiple TP splitting is planned for Phase 2.1.

### Auto-cancel (pending orders)

Pending limit/stop orders are automatically cancelled when the channel sends:
- `"Missed close it"` / `"Just missed our limit"` / `"Missed"` / `"Delete"` — entry never triggered
- TP1 hit message (including `"Tp1 hitted"`, `"Already hitted tp1"`) — price blew past entry to TP without filling the pending order
- All TP hit message — trade is completely over

### Position closing

Full position close is triggered by these update types: `full_close`, `sl_hit`, `cancelled`, `tp_hit`.

---

## Journal

Every signal and update is appended to a JSONL file in `journal/`. One file per channel.

**New signal entry:**
```json
{
  "signal_id": "uuid",
  "telegram_msg_id": 12345,
  "message_type": "new_signal",
  "timestamp": "2026-04-06T12:51:49+00:00",
  "source_channel_name": "Vip Thrilokh",
  "instrument": "XAUUSD",
  "direction": "BUY",
  "order_type": "market",
  "entry": 4674.0,
  "sl": 4660.0,
  "tp": [4688.0],
  "parse_status": "parsed"
}
```

**Trade update entry:**
```json
{
  "signal_id": "uuid-of-original-signal",
  "telegram_msg_id": 12346,
  "message_type": "trade_update",
  "timestamp": "2026-04-06T14:22:00+00:00",
  "source_channel_name": "Vip Thrilokh",
  "instrument": "XAUUSD",
  "update_type": "tp_hit"
}
```

### Journal Viewer

Generate a browser-based HTML viewer from the journal files:

```bash
python generate_viewer.py
# opens journal_viewer.html — filterable table of all signals and updates
```

---

## Testing Without Live Trading

**Test parser offline** (no new Telegram messages needed):

```bash
python test_replay.py
# fetches last 50 messages from each channel and replays through the full pipeline
# prints per-channel summary: signals / updates / noise / errors
```

**Dry run mode** (orders logged but not sent):

Set `DRY_RUN=true` in `.env`. Every `place_order` and `handle_close` call will log what it *would* do without touching the broker.

---

## Configuration Reference (`webhook.py`)

| Variable | Default | Description |
|---|---|---|
| `ENTRY_TOLERANCE_PIPS` | `3` | Max pips from signal price to still use a market order |
| `PIP_SIZE` | per instrument | Price value of 1 pip — adjust if broker quotes differently |
| `LOT_SIZE` | `0.01` | Lot size per trade |
| `DRY_RUN` | `true` (from .env) | Log orders without executing — set to false for live trading |

---

## Adding a New Channel

1. Pull sample messages: `python fetch_samples.py`
2. Add channel ID + name to `channels/__init__.py` and `journal.py` (`CHANNEL_NAMES` dict)
3. Create `channels/<name>.py` with: `CHANNEL_NAME`, `CHANNEL_ID`, `classify(msg)`, `parse_signal(msg)`, `parse_update(msg, signal_id)`
4. Add order logic for the new channel in `webhook.py` → `place_order()` (new `elif channel_id == ...` branch)

---

## Roadmap

| Phase | Status | Description |
|---|---|---|
| Phase 1 | Complete | Signal reader, parser, journal |
| Phase 2 | In progress | MetaAPI order execution — market / limit / stop orders, TP1, full close |
| Phase 2.1 | Planned | Multiple TP splitting, partial closes, breakeven SL moves |
| Phase 3 | Future | LLM fallback parser (Claude Haiku) for edge-case messages |
