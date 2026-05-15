"""
Pre-warm the LLM classify cache from historical journal entries.

The journals already contain the correct update_type for every message ever
seen. This script reads them and writes each normalized message → update_type
into cache/classify_cache.json so the LLM API is never called for a
previously-seen pattern.

Run once before going live:
  python prewarm_cache.py
"""

import json
import os
import sys

sys.path.insert(0, ".")
import llm_classify

JOURNALS = [
    ("journal/vip_thrilokh.jsonl",    "Vip Thrilokh"),
    ("journal/xauusd_big_lots.jsonl",  "XAUUSD VIP BIG LOTS"),
    ("journal/kathy_zip_forex.jsonl",  "Kathy ZIP Forex Trades"),
]

VALID_UPDATE_TYPES = {
    "tp_hit", "full_close", "sl_hit", "breakeven",
    "partial_close", "cancelled", "commentary",
}

def main():
    llm_classify._load()
    before = len(llm_classify._cache)

    added = skipped_already = skipped_signal = skipped_bad = 0
    conflicts: list[tuple[str, str, str, str]] = []  # (raw, channel, journal_type, cached_type)

    for path, channel in JOURNALS:
        if not os.path.exists(path):
            print(f"  [skip] {path} not found")
            continue

        with open(path, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]

        print(f"\n{channel} — {len(lines)} entries")

        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("message_type") != "trade_update":
                skipped_signal += 1
                continue

            raw         = entry.get("raw_message", "").strip()
            update_type = entry.get("update_type", "")

            if not raw or update_type not in VALID_UPDATE_TYPES:
                skipped_bad += 1
                continue

            key = llm_classify._normalize(raw)

            if key in llm_classify._cache:
                existing = llm_classify._cache[key]
                if existing != update_type:
                    conflicts.append((raw, channel, update_type, existing))
                skipped_already += 1
                continue

            llm_classify._cache[key] = update_type
            added += 1

    llm_classify._save()
    after = len(llm_classify._cache)

    print(f"\n{'='*55}")
    print(f"  Entries processed : signals/noise skipped = {skipped_signal}")
    print(f"  Already cached    : {skipped_already}")
    print(f"  Newly added       : {added}")
    print(f"  Cache size        : {before} -> {after}")

    if conflicts:
        print(f"\n  CONFLICTS ({len(conflicts)}) — journal says X, cache already had Y:")
        for raw, ch, jtype, ctype in conflicts:
            print(f"    [{ch}] {raw!r}")
            print(f"      journal={jtype}  cache={ctype}")
    else:
        print("  No conflicts.")

    # Summary by update_type
    from collections import Counter
    counts = Counter(llm_classify._cache.values())
    print(f"\n  Cache breakdown:")
    for utype, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {utype:<16} {n}")

    print(f"\nDone. API key only needed for genuinely novel messages.")


if __name__ == "__main__":
    main()
