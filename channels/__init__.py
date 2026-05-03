from channels import vip_thrilokh, xauusd_big_lots, test_tv_3min

# Maps Telegram channel ID → parser module
# Each parser module must implement:
#   CHANNEL_NAME: str
#   CHANNEL_ID: int
#   classify(msg) -> "new_signal" | "trade_update" | "noise"
#   parse_signal(msg) -> dict | None
#   parse_update(msg, signal_id) -> dict | None
CHANNEL_PARSERS = {
    2133117224: vip_thrilokh,
    1481325093: xauusd_big_lots,
    2540865305: test_tv_3min,   # Test_TV_3min — tries all parsers in order
}
