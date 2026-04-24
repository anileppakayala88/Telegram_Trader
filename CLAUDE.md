# Telegram Trader

## Project Overview

Reads trade signal messages from a Telegram channel/group, parses them into structured trade objects, logs them to a human-readable journal, and (in a later phase) fires orders via TradingView webhooks.

---

## Goals by Phase

### Phase 1 — Signal Reader + Journal (current)
- Connect to Telegram as a user account (Telethon)
- Listen to a specific channel/group for new messages
- Parse each message into a structured trade signal:
  - Instrument (e.g. XAUUSD, EURUSD, NAS100, BTC/USD)
  - Direction (BUY / SELL)
  - Entry price (or range)
  - Take Profit levels (TP1, TP2, TP3 — variable)
  - Stop Loss
  - Asset class (forex / indices / crypto / futures)
- Handle optional images attached to messages
- Write parsed signal to a trade journal (human-readable log)
- Flag unparseable messages for manual review

### Phase 2 — TradingView Webhook Trigger
- Format parsed signal into a TradingView-compatible webhook payload
- POST to a TradingView alert webhook endpoint
- Handle retries, failures, and confirmations
- Support partial signals (e.g. TP-only updates, SL moves)

### Phase 3 — MT5 Direct (future consideration)
- Optionally route orders directly to MT5 via Python MT5 library
- Not in scope until Phase 2 is validated

---

## Architecture

```
Telegram (channel/group)
        |
   Telethon Listener
        |
   Message Parser
   (LLM-assisted or regex — handles messy/informal signal text)
        |
   ┌────┴────┐
   │         │
Journal    Signal Object
(log file) (structured dict)
                |
         [Phase 2] TradingView Webhook
```

---

## Tech Stack

- **Language:** Python 3.11+
- **Telegram:** Telethon (user account MTProto API)
- **Parsing:** Claude API (claude-haiku for speed/cost) + regex fallback
- **Journal:** Append-only JSONL file (`journal/trades.jsonl`) + human-readable markdown summary
- **Webhooks:** `httpx` (async HTTP client)
- **Config:** `python-dotenv` for credentials

---

## Project Structure

```
Telegram_Trader/
├── .env                   # credentials (never committed)
├── CLAUDE.md
├── requirements.txt
├── main.py                # entry point — starts listener
├── listener.py            # Telethon event handler
├── parser.py              # signal parsing logic
├── journal.py             # write to trade journal
├── webhook.py             # TradingView webhook sender (Phase 2)
└── journal/
    └── trades.jsonl       # append-only signal log
```

---

## Signal Schema

```json
{
  "id": "uuid",
  "timestamp": "ISO8601",
  "source_channel": "channel name or id",
  "raw_message": "original text",
  "asset_class": "forex | indices | crypto | futures",
  "instrument": "XAUUSD",
  "direction": "BUY | SELL",
  "entry": 2345.00,
  "entry_range": [2340.00, 2350.00],
  "sl": 2310.00,
  "tp": [2370.00, 2400.00, 2450.00],
  "has_image": false,
  "parse_status": "parsed | partial | failed",
  "notes": "any additional context from message"
}
```

---

## Environment Variables

```
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_PHONE=
TELEGRAM_CHANNEL=        # channel username or numeric ID to monitor
ANTHROPIC_API_KEY=       # for LLM-assisted parsing (Phase 1)
TRADINGVIEW_WEBHOOK_URL= # TradingView alert webhook URL (Phase 2)
```

---

## Key Decisions

- **User account over bot:** Bots cannot read channel history and require admin access. User account via Telethon reads any channel/group the account is a member of.
- **LLM-assisted parsing:** Signal messages are informal and inconsistent. Claude Haiku parses ambiguous text cheaply; regex handles well-structured messages as a fast path.
- **JSONL journal:** Append-only, easy to tail/grep, survives crashes without corruption.
- **Async throughout:** Telethon is async; keep the whole stack async to avoid blocking the listener.

---

## Out of Scope (for now)

- Web UI or dashboard
- Multi-account Telegram support
- Risk management / position sizing
- Broker integration beyond TradingView webhooks

---

## Channel Strategy

- **Multiple channels** are monitored simultaneously, each with a different signal style/format
- Each channel gets its own parser profile — the system must be channel-aware
- Parser training approach: use Option B (live message pull via Telethon) to sample each channel's last N messages, then build/tune a parser profile per channel
- Channel profiles stored in `channels/` — one config file per channel with its known format, instrument focus, and parsing hints

### Channel Onboarding Process (per channel)
1. Pull last 50 messages via listener script
2. Manually review and label a sample (instrument, direction, entry, TP, SL)
3. Write/tune parser profile for that channel
4. Validate parser against labeled sample
5. Add channel to active monitoring list

---

## Open Questions

- [ ] List of channel usernames/IDs to monitor (share when ready)
- [ ] TradingView webhook URL format — confirm when Phase 2 begins
- [ ] Should partial updates (e.g. "Move SL to entry") be linked back to the original signal?
- [ ] Should each channel route to a separate journal, or one unified journal with channel tag?
