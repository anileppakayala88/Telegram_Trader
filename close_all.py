"""Close all open positions and cancel all pending orders in MT5."""
import os
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

if not mt5.initialize(
    login=int(os.getenv("MT5_LOGIN")),
    password=os.getenv("MT5_PASSWORD"),
    server=os.getenv("MT5_SERVER"),
):
    print(f"MT5 init failed: {mt5.last_error()}")
    raise SystemExit(1)

# Close open positions
positions = mt5.positions_get()
if positions:
    for pos in positions:
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
        result = mt5.order_send({
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       pos.symbol,
            "volume":       pos.volume,
            "type":         close_type,
            "position":     pos.ticket,
            "price":        price,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        })
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"  Closed position  ticket={pos.ticket}  {pos.symbol}  vol={pos.volume}")
        else:
            err = result.comment if result else mt5.last_error()
            print(f"  FAILED to close  ticket={pos.ticket}  {pos.symbol}  err={err}")
else:
    print("No open positions.")

# Cancel pending orders
orders = mt5.orders_get()
if orders:
    for order in orders:
        result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket})
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"  Cancelled order  ticket={order.ticket}  {order.symbol}")
        else:
            err = result.comment if result else mt5.last_error()
            print(f"  FAILED to cancel ticket={order.ticket}  {order.symbol}  err={err}")
else:
    print("No pending orders.")

mt5.shutdown()
print("Done.")
