"""
Cache-backed LLM classifier for trading channel messages.

Flow:
  1. Normalize the message text (strip emojis, replace numbers with #)
  2. Check the local cache (cache/classify_cache.json) — cache hit is free
  3. On miss: call Anthropic API and persist the result for next time

The cache is pre-populated with all phrases seen to date so the API
is only called for genuinely novel messages.
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

    Valid return values: tp_hit / full_close / sl_hit / breakeven /
                         partial_close / cancelled / commentary / noise
    """
    _load()
    key = _normalize(text)
    if key in _cache:
        log.debug("llm_classify cache hit: %r → %s", key, _cache[key])
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
            messages=[{
                "role": "user",
                "content": (
                    f"Classify this message from a Telegram trading signal channel ({channel_name}).\n"
                    "Reply with exactly one word from this list:\n"
                    "  tp_hit        — a take-profit level was reached\n"
                    "  full_close    — close the trade entirely\n"
                    "  sl_hit        — stop loss or breakeven stop was hit\n"
                    "  breakeven     — instruction to move stop loss to breakeven\n"
                    "  partial_close — close part of the position\n"
                    "  cancelled     — entry not triggered, cancel pending order\n"
                    "  commentary    — channel update or commentary, no trade action\n"
                    "  noise         — spam, reactions, engagement prompts, irrelevant\n\n"
                    f"Message: {text}"
                ),
            }],
        )
        result = resp.content[0].text.strip().lower()
        if result not in _VALID_TYPES:
            result = "commentary"

        log.info("llm_classify API: %r → %s (key=%r)", text[:80], result, key)
        _cache[key] = result
        _save()
        return result

    except Exception as e:
        log.warning("llm_classify API error: %s", e)
        return "commentary"
