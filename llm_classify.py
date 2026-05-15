"""
Cache-backed LLM classifier for trading channel messages.

Flow:
  1. Normalize the message text (strip emojis, replace numbers with #)
  2. Check the local cache (cache/classify_cache.json) — cache hit is free
  3. On miss: call Anthropic API (Claude Haiku) and persist the result

Prompt structure:
  - system block  — static description + examples; marked cache_control so
                    Anthropic caches it server-side after the first call
  - user message  — "Channel: X\nMessage: Y"  (dynamic, tiny, never cached)

The cache is pre-populated from journals so the API is only called for
genuinely novel messages that reach the bot for the first time.
"""

import json
import logging
import os
import re

log = logging.getLogger(__name__)

_CACHE_FILE = os.path.join(os.path.dirname(__file__), "cache", "classify_cache.json")

_VALID_TYPES = frozenset({
    "tp_hit", "full_close", "sl_hit", "breakeven",
    "partial_close", "cancelled", "commentary", "noise",
})

_cache: dict[str, str] = {}
_cache_loaded = False

# Static system prompt — identical on every call so Anthropic can cache it.
# Covers all 8 labels, rules for the tricky cases, and one example per label.
_SYSTEM_PROMPT = """\
You classify messages from Telegram Forex/Gold/Crypto trading signal channels.
Reply with exactly one word from this list:

  tp_hit        — a take-profit level was reached
  full_close    — close the entire trade (includes breakeven exits)
  sl_hit        — stop loss hit and a real loss occurred (NOT a breakeven exit)
  breakeven     — instruction to move the stop loss to the entry/breakeven price
  partial_close — close part of the position only
  cancelled     — entry never triggered; cancel or delete the pending order
  commentary    — observation or update, no immediate trade action required
  noise         — spam, reactions, pips-only announcements, irrelevant chatter

Rules:
- "Be hit" / "be hit" / "exit at be" → full_close  (breakeven stop, not a real loss)
- A bare "Sl" as a reply to a signal → sl_hit
- Messages that only state a pips profit amount → noise
- Partial close combined with moving to BE → partial_close

Examples:
  "all tp hitted"                        → full_close
  "XAUUSD ALL TP HIT RUNNING 200 PIPS"  → full_close
  "Am closing this Btc trade here"       → full_close
  "Close now"                            → full_close
  "Be hit"                               → full_close
  "Exit at be"                           → full_close
  "Tp1 hitted"                           → tp_hit
  "XAUUSD TP1 HIT RUNNING 50 PIPS"      → tp_hit
  "Already hitted tp1"                   → tp_hit
  "Btc 50% tapped"                       → tp_hit
  "Sl"                                   → sl_hit
  "Set be"                               → breakeven
  "Keep Btc sl as be"                    → breakeven
  "sl as be"                             → breakeven
  "Close partial and set be"             → partial_close
  "Close partial and set sl as be"       → partial_close
  "Missed close it"                      → cancelled
  "Just missed our limit"                → cancelled
  "Delete"                               → cancelled
  "200 pips profit"                      → noise
  "React"                                → noise
  "I'm in"                               → noise
  "Market looking bullish today"         → commentary\
"""


def _load():
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, encoding="utf-8") as f:
                _cache = json.load(f)
            log.debug("llm_classify: loaded %d cached entries", len(_cache))
        except Exception as e:
            log.warning("llm_classify: failed to load cache: %s", e)


def _save():
    os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(_cache, f, indent=2, sort_keys=True)


def _normalize(text: str) -> str:
    """Produce a stable cache key: lowercase, no emojis, numbers → #."""
    text = text.lower().strip()
    text = re.sub(r"[^\x00-\x7f]+", " ", text)   # emojis / non-ASCII → space
    text = re.sub(r"\d+\.?\d*", "#", text)         # numbers → #
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(" .,!?")
    return text


def get_update_type(text: str, channel_name: str) -> str:
    """
    Return the update_type for a message.

    Checks the local cache first (free). On a cache miss, calls the Anthropic
    API if ANTHROPIC_API_KEY is set, saves the result, and returns it.
    Returns "commentary" when the API is unavailable or the key is not set.

    Safe to call from a thread (e.g. via asyncio.to_thread) — the sync
    Anthropic client is used intentionally; listener.py offloads classify()
    and parse_update() to a thread so the event loop is never blocked.

    Valid return values: tp_hit / full_close / sl_hit / breakeven /
                         partial_close / cancelled / commentary / noise
    """
    _load()
    key = _normalize(text)
    if key in _cache:
        log.debug("llm_classify cache hit: %r -> %s", key, _cache[key])
        return _cache[key]

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.debug("llm_classify: no API key, defaulting to commentary for: %r", text[:60])
        return "commentary"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            system=[{
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"Channel: {channel_name}\nMessage: {text}",
            }],
        )
        result = resp.content[0].text.strip().lower()
        if result not in _VALID_TYPES:
            log.warning("llm_classify: unexpected response %r for %r — using commentary", result, text[:60])
            result = "commentary"

        log.info("llm_classify API: %r -> %s (cached key=%r)", text[:80], result, key)
        _cache[key] = result
        _save()
        return result

    except Exception as e:
        log.warning("llm_classify API error: %s", e)
        return "commentary"
