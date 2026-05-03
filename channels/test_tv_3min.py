"""
test_tv_3min.py — multi-format parser for the dev/test channel.

Tries each registered parser in order and returns the first match.
Add more parsers to _PARSERS to support additional signal formats.
"""
from channels import vip_thrilokh, xauusd_big_lots

CHANNEL_NAME = "Test_TV_3min"
CHANNEL_ID   = 2540865305

# Parsers tried in order — first non-noise / non-None result wins
_PARSERS = [vip_thrilokh, xauusd_big_lots]


def classify(msg) -> str:
    for parser in _PARSERS:
        result = parser.classify(msg)
        if result != "noise":
            return result
    return "noise"


def parse_signal(msg) -> dict | None:
    for parser in _PARSERS:
        if parser.classify(msg) == "new_signal":
            entry = parser.parse_signal(msg)
            if entry:
                entry["source_channel_id"]   = CHANNEL_ID
                entry["source_channel_name"] = CHANNEL_NAME
                return entry
    return None


def parse_update(msg, signal_id: str | None) -> dict | None:
    for parser in _PARSERS:
        if parser.classify(msg) == "trade_update":
            entry = parser.parse_update(msg, signal_id)
            if entry:
                entry["source_channel_id"]   = CHANNEL_ID
                entry["source_channel_name"] = CHANNEL_NAME
                return entry
    return None
