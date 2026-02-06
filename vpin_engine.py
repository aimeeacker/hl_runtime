import asyncio
import logging
import binance_depth as bd
import binance_depth as be
import time
from datetime import datetime, timezone, timedelta
from collections import OrderedDict, defaultdict, deque
import uvloop; asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
from l4book_websocket import l4book  # type: ignore

from mylogger import setup_logger
from asyncmy_test import AsyncMySQL
logger = setup_logger(logging.INFO)
logger = logging.getLogger("main")

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

bucket = defaultdict(dict)
l4anal_acc = defaultdict(lambda: {
    "bid_fill_notional": 0.0,
    "bid_fill_volume": 0.0,
    "ask_fill_notional": 0.0,
    "ask_fill_volume": 0.0,
    "bid_change_notional": 0.0,
    "bid_change_volume": 0.0,
    "ask_change_notional": 0.0,
    "ask_change_volume": 0.0,
})

def convert_time(input_time=None):
    if input_time is None:
        input_time = time.time()
    else:
        input_time = input_time / 1000
    tz_utc8 = timezone(timedelta(hours=8))
    dt = datetime.fromtimestamp(input_time, tz=tz_utc8)
    time_str = dt.strftime("%H:%M:%S.%f")[:-3]
    return time_str

def add_color(val, pct=True, custom_color = None, _rjust = 8):
    color = RED if val < 0 else GREEN
    if custom_color is not None:
        color = custom_color
    if pct:
        s = f"{val:,.3%}"
    else:
        s = f"{val:,.2f}"
    return f"{color}{s.rjust(_rjust)}{RESET}"

def _format_segment(sym: str, lastPx: float, dev_openPx: float, dev_vwap5: float,
                    b_s_ratio: float, imblance: float, depth_ratio: float,
                    depth_ts: int) -> str:

    color0 = RED if dev_openPx < 0 else GREEN

    time_str = convert_time(depth_ts)
    left = f"{color0}{sym[:-4]} {lastPx:,.2f}{RESET}({add_color(dev_openPx, pct=False, _rjust = 5)})|{add_color(dev_vwap5, pct=False, _rjust = 5)}|"
    right = f"{add_color(b_s_ratio)}|{add_color(imblance)}|{add_color(depth_ratio)}({time_str})"
    return f"{left}{right}"

async def on_kline_closed(data: dict):
    #print(data)
    return
    symbol = data['symbol']
    lastPx = data['lastPx']
    dev_openPx = data['dev_openPx']
    dev_vwap5 = data['dev_vwap_5']
    volume_ema_10 = data['volume_ema_10']
    b_s_ratio = data['active_buy_sell_ratio']
    imblance = data['top_avg']
    depth_ratio = data['ratio_avg']
    depth_ts = data['depth_ts']

    sym = f"{symbol.upper()}"
    segment = _format_segment(sym, lastPx, dev_openPx, dev_vwap5, b_s_ratio, imblance, depth_ratio, depth_ts)
    bucket[sym] = segment
    # Print when both BTC and ETH are present for this group; BTC left
    if "BTCUSDT" in bucket and "ETHUSDT" in bucket:
        time_str = convert_time()
        print(f"{time_str}: {bucket['BTCUSDT']}\t{bucket['ETHUSDT']}")
        bucket.clear()

async def on_vpin_update(data: dict, write_db=True):
    def format_num(num):
        if abs(num) >= 1e6:
            return f"{num/1e6:+,.3f}".rjust(7) + "M"
        elif abs(num) >= 1e3:
            return f"{num/1e3:+,.3f}".rjust(7) + "K"
        else:
            return f"{num:,.3f}".rjust(9)
    #print(f"VPIN update data: {data}")
    #return
    symbol = data['symbol'].lower()
    blank = ""

    if symbol == "ethusdt":
        blank = f" " * 39
    #{'symbol': 'btcusdt', 'signed_vpin': 0.12037735772310947, 'ema_vpin': 0.06683004207525375, 'bucket_turnover': 4995678.33, 'bucket_qty': 56.19148864487296,
    # 'total_speed': 86492.0300585567, 'total_acceleration': 27648.322341742947, 'net_speed': -34847.74292485831, 'net_acceleration': -22803.289463284753, 'net_turnover': 601366.5573999961}
    bucket_qty = data['bucket_qty']
    bucket_turnover = data['bucket_turnover']
    signed_vpin = data['signed_vpin']
    bucket_open_price = data["bucket_open_price"]
    bucket_close_price = data["bucket_close_price"]
    price_delta = bucket_close_price - bucket_open_price
    net_turnover = data['net_turnover']
    #net_speed = data['net_speed']
    #net_acceleration = data['net_acceleration']
    #total_speed = data['total_speed']
    #total_acceleration = data['total_acceleration']
    #signed_vpin_qty = data['signed_vpin_qty']
    #market_dominant = data['market_dominant']
    #volume_phase = data['volume_phase']
    #predict_10m = data['predict_10m']
    #logger.info(f"{blank}{symbol.upper()}: VPIN={bucket_vpin_signed:+.3f}({bucket_volume:,.3f}), net_s={net_speed:+.3f}({net_acceleration:+.3f})\t{market_dominant.upper()}|{volume_phase.upper().rjust(6)}|{predict_10m:+,.0f}")
    #"""
    absorption_score = None
    #directional_absorption = data.get('directional_absorption')
    absorption_str = f"{absorption_score:+.3f}" if absorption_score is not None else "nan"
    #directional_str = f"{directional_absorption:+.3f}" if directional_absorption is not None else "nan"
    #logger.info(f"{blank}{symbol[:-4].upper()}: VPIN={signed_vpin:+.3f}({format_num(bucket_turnover)}@{format_num(bucket_qty)});abs={absorption_str}")#;dabs={directional_str}, net_s={net_speed:+.3f}({net_acceleration:+.3f})")

    coin = symbol[:-4].upper()
    acc = l4anal_acc.get(coin)
    if acc:
        bid_net = acc["bid_change_notional"]
        ask_net = acc["ask_change_notional"]
        bid_fill = acc["bid_fill_notional"]
        ask_fill = acc["ask_fill_notional"]
        #bid_fill_volume = acc["bid_fill_volume"]
        #ask_fill_volume = acc["ask_fill_volume"]
        bid_change_volume = acc["bid_change_volume"]
        ask_change_volume = acc["ask_change_volume"]
        bid_change_vwap = (bid_net / bid_change_volume) if abs(bid_change_volume) > 1e-9 else 0.0
        ask_change_vwap = (ask_net / ask_change_volume) if abs(ask_change_volume) > 1e-9 else 0.0
        #bid_ratio = (bid_net / bid_fill) if abs(bid_fill) > 1e-9 else 0.0
        #ask_ratio = (ask_net / ask_fill) if abs(ask_fill) > 1e-9 else 0.0
        #logger.info(
        #    f"{blank}{coin}: net_in={format_num(bid_net)}/{format_num(ask_net)} "
        #    f"ratio={bid_ratio:+.3f}/{ask_ratio:+.3f}"
        #)
        acc["bid_fill_notional"] = 0.0
        acc["ask_fill_notional"] = 0.0
        acc["bid_change_notional"] = 0.0
        acc["ask_change_notional"] = 0.0
        acc["bid_fill_volume"] = 0.0
        acc["ask_fill_volume"] = 0.0
        acc["bid_change_volume"] = 0.0
        acc["ask_change_volume"] = 0.0
    if not write_db:
        return
    if symbol == "btcusdt":
        await mysql.insert("VPIN_new1", 
                                VPIN_btc=signed_vpin,
                                qV_btc=bucket_turnover,
                                vol_btc=bucket_qty,
                                nqV_btc=net_turnover,
                                open_btc=bucket_open_price,
                                close_btc=bucket_close_price,
                                pc_btc=price_delta,
                                bIn_btc=bid_net, 
                                bInAp_btc=bid_change_vwap,
                                aInAp_btc=ask_change_vwap,
                                bFill_btc=bid_fill,
                                aIn_btc=ask_net,
                                aFill_btc=ask_fill)
    elif symbol == "ethusdt":
        await mysql.insert("VPIN_new1", 
                                VPIN_eth=signed_vpin, 
                                qV_eth=bucket_turnover, 
                                vol_eth=bucket_qty,
                                nqV_eth=net_turnover, 
                                open_eth=bucket_open_price,
                                close_eth=bucket_close_price,
                                pc_eth=price_delta,
                                bIn_eth=bid_net, 
                                bInAp_eth=bid_change_vwap,
                                aInAp_eth=ask_change_vwap,
                                bFill_eth=bid_fill,
                                aIn_eth=ask_net,
                                aFill_eth=ask_fill)
    #"""

async def on_vpin_update_future(data: dict):
    await on_vpin_update(data, write_db=False)

async def on_depth_update(data: dict):
    print(f"Depth update data: {data}")

async def on_l4anal_message(message: dict):
    try:
        channel = message.get("channel", "")
        if not channel:
            return
        coin = channel.split("@", 1)[0].upper()
        data = message.get("data") or {}
        window_sum_b = data.get("window_sum_bid")
        window_sum_a = data.get("window_sum_ask")
        #logger.info(f"L4Anal message channel: {channel}, data: {data}")
        #logger.info(f"L4Anal message for {coin}: bid={window_sum_b}, ask={window_sum_a}")
        if not window_sum_b or not window_sum_a:
            return
        bid_fill_volume = float(window_sum_b[0])
        ask_fill_volume = float(window_sum_a[0])
        bid_change_volume = float(window_sum_b[2])
        ask_change_volume = float(window_sum_a[2])
        bid_fill_notional = float(window_sum_b[1])
        ask_fill_notional = float(window_sum_a[1])
        bid_change_notional = float(window_sum_b[3])
        ask_change_notional = float(window_sum_a[3])
        acc = l4anal_acc[coin]
        acc["bid_fill_notional"] += bid_fill_notional
        acc["ask_fill_notional"] += ask_fill_notional
        acc["bid_fill_volume"] += bid_fill_volume
        acc["ask_fill_volume"] += ask_fill_volume

        acc["bid_change_notional"] += bid_change_notional# + bid_fill_notional
        acc["ask_change_notional"] += ask_change_notional# + ask_fill_notional
        acc["bid_change_volume"] += bid_change_volume# + bid_fill_volume
        acc["ask_change_volume"] += ask_change_volume# + ask_fill_volume
    except Exception as exc:
        logger.error(f"l4Anal callback error: {exc}")

async def main():
    global mysql
    mysql = AsyncMySQL({
        "host": "172.22.0.198",
        "port": 3306,
        "user": "aimee",
        "password": "02011",
        "db": "eventContract",
        "charset": "utf8mb4",
    })
    await mysql.start()
    try:
        #bd.set_python_logger("tradeModule_OKX")
        symbol_list_s = [{"btc": [18.0, 5, 10.0]},{"eth": [380.0, 5, 10.0]}]#
        symbol_list_f = [{"btc": [4500000.0, 5, 8.0]},{"eth": [3600000.0, 5, 8.0]}]
        symbol_list_okx = [{"btc": [32.0, 5, 8.0]},{"eth": [1080.0, 5, 8.0]}]
        logger.error(f"[init....]")
        # Register kline-close callback: cb(data:dict)
        #spot_module = bd.setup_module(symbol_list_s, 500, "1m", 0, on_kline_closed, on_vpin_update)
        future_module = bd.setup_module(symbol_list_f, 500, "1m", 1, "binance", on_kline_closed, on_vpin_update)
        #future_module = be.init_module(1, symbol_list, 500, "1m")
        # Register depth update callback: cb(data:dict)
        future_module.start_ws()

        l4_streams = ["BTC@l4Anal", "ETH@l4Anal"]
        l4_ws = l4book(url="ws://127.0.0.1:8080/ws", cafile=None, passphrase=None)
        await l4_ws._setup(callback=on_l4anal_message, streams=l4_streams, ex="hyperliquid", ws_name="l4book")
        await l4_ws._start()
        await asyncio.sleep(60)
        #future_module.restart_ws()
        #spot_module.start_ws()
        
        #await asyncio.sleep(60)  # Wait for threads to start
        #bd.restart_ws()
        # Keep process alive; printing happens in callback
        await asyncio.Event().wait()
    finally:
        #spot_module.shutdown()
        future_module.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 收到 Ctrl+C, 程序退出")
    finally:
        logger.info("👋 程序已结束")
