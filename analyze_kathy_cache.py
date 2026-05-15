"""
Analyze kathy_samples.txt — show what each message classifies as,
flag which ones would hit the LLM, and add correct cache entries.

Run: python analyze_kathy_cache.py
"""

import re
import sys
import types
import json
from datetime import datetime, timezone

sys.path.insert(0, ".")

# ── Stub LLM so regex-only path is visible ────────────────────────────────────
_llm_stub = types.ModuleType("llm_classify")
_llm_stub.get_update_type = lambda text, ch: "__LLM__"   # sentinel — never "noise"
sys.modules["llm_classify"] = _llm_stub

from channels import kathy_zip_forex as kz

# ── Parse kathy_samples.txt ───────────────────────────────────────────────────
SAMPLES_FILE = "kathy_samples.txt"

class _Msg:
    def __init__(self, text, msg_id=0):
        self.text            = text
        self.id              = msg_id
        self.reply_to_msg_id = None
        self.media           = None
        self.date            = datetime(2026, 1, 1, tzinfo=timezone.utc)

def parse_samples(path):
    """Yield (msg_id, text) for each message block in the samples file."""
    with open(path, encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r"^--- \[.*?\] \[id=(\d+)\].*$", content, flags=re.MULTILINE)
    # blocks[0] is empty, then alternates: id, text, id, text ...
    for i in range(1, len(blocks) - 1, 2):
        msg_id = int(blocks[i])
        text   = blocks[i + 1].strip()
        if text == "[media only]" or not text:
            continue
        yield msg_id, text

# ── Classify every message and collect LLM-fallthrough ones ──────────────────
llm_hits = []   # messages where regex fell through to LLM

print(f"{'ID':<6}  {'CLASSIFY':<14}  {'DETAIL':<40}  TEXT[:60]")
print("-" * 110)

for msg_id, text in parse_samples(SAMPLES_FILE):
    msg    = _Msg(text, msg_id)
    result = kz.classify(msg)

    # detect LLM sentinel
    hit_llm = "__LLM__" in str(result) or (
        result == "noise" and
        kz._NEW_TRADE_RE.search(text) is None and
        kz._CLOSE_RE.search(text) is None and
        kz._STOPPED_RE.search(text) is None and
        kz._TP_HIT_RE.search(text) is None
    )

    # For trade_update, show update_types from parse_update
    detail = ""
    if result == "trade_update":
        updates = kz.parse_update(msg, None)
        detail  = str([u["update_type"] for u in updates]) if updates else "[]"
    elif result == "new_signal":
        sig = kz.parse_signal(msg)
        if sig:
            detail = f"{sig['direction']} {sig['instrument']} @{sig['entry']}"

    flag = "  <-- LLM" if hit_llm else ""
    print(f"{msg_id:<6}  {result:<14}  {detail:<40}  {text[:60].replace(chr(10),' ')}{flag}")

    if hit_llm:
        llm_hits.append((msg_id, text))

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print(f"Messages that would hit the LLM ({len(llm_hits)}):")
for msg_id, text in llm_hits:
    print(f"  [{msg_id}] {text[:80].replace(chr(10),' ')}")
