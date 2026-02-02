"""
Minute-level Liquidity Factors for BTCUSDT Perpetual (Binance USDT-M Futures)

Outputs (every minute close):
1) LHF: Liquidity Health Factor (0~100, higher = healthier liquidity)
2) COLD: Cold/Fragility Factor (0~100, higher = colder / more fragile / easier to manipulate)

Data streams used:
- btcusdt@aggTrade        : for taker-side orderflow (CVD delta), dollar volume, trade count
- btcusdt@bookTicker      : for true best bid/ask (spread distribution) + mid open/close
- btcusdt@depth10@100ms   : top10 partial depth snapshots (we treat as full top10 snapshot each update)

Key concepts:
- Healthy liquidity means: tight spread, sufficient depth, low impact per dollar, good resilience, flow-price consistency.
- Cold/fragile means: low volume + thin depth + wide/spiky spread + high impact + slow recovery.
"""

import asyncio
import json
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict, Any

import numpy as np
import websockets

EPS = 1e-12


# =========================
# Robust statistics helpers
# =========================
def robust_z(x: float, arr: np.ndarray) -> float:
    """
    Robust z-score using rolling median & MAD:
      z = (x - median) / (1.4826 * MAD)

    If arr too small, return 0 (neutral).
    """
    if arr.size < 5:
        return 0.0
    med = np.median(arr)
    mad = np.median(np.abs(arr - med)) + EPS
    return (x - med) / (1.4826 * mad)


def sigmoid(x: float) -> float:
    """Map real -> (0,1)."""
    return 1.0 / (1.0 + math.exp(-x))


# ===========================================
# Minute Aggregator: bookTicker (best bid/ask)
# ===========================================
class BookTickerMinuteAgg:
    """
    Collects high-frequency best bid/ask updates inside a minute.

    Outputs:
      - mid_open / mid_close  : minute open/close mid price
      - spread_median/p95/max : distribution stats of relative spread
      - spike_ratio           : max_spread / median_spread
    """
    def __init__(self):
        self.spreads = []
        self.first_mid = None
        self.last_mid = None

    def update(self, bid1: float, ask1: float):
        mid = (bid1 + ask1) / 2.0
        rel_spread = (ask1 - bid1) / (mid + EPS)  # relative spread

        if self.first_mid is None:
            self.first_mid = mid
        self.last_mid = mid

        self.spreads.append(rel_spread)

    def close_minute(self) -> Optional[Dict[str, float]]:
        if not self.spreads or self.first_mid is None or self.last_mid is None:
            self._reset()
            return None

        x = np.array(self.spreads, dtype=float)
        out = {
            "mid_open": float(self.first_mid),
            "mid_close": float(self.last_mid),
            "spread_median": float(np.median(x)),
            "spread_p95": float(np.quantile(x, 0.95)),
            "spread_max": float(np.max(x)),
            "spike_ratio": float(np.max(x) / (np.median(x) + EPS)),
            "n_updates_bt": float(len(x)),
        }
        self._reset()
        return out

    def _reset(self):
        self.spreads.clear()
        self.first_mid = None
        self.last_mid = None


# ==================================
# Minute Aggregator: depth10 snapshots
# ==================================
class Depth10MinuteAgg:
    """
    Collects depth10 snapshot updates inside a minute.

    Input depth format (Binance fstream depth):
      data["b"] = [[price_str, qty_str], ...]  # bids
      data["a"] = [[price_str, qty_str], ...]  # asks

    Outputs (USD-approx):
      - depth_usd_median / depth_usd_p10 / depth_usd_min : depth distribution
      - imb_median : (bid_depth - ask_depth) / total_depth
      - depth_recover: depth_end / depth_start
    """
    def __init__(self, n_levels=10):
        self.n = n_levels
        self.depth_usd = []
        self.imb = []
        self.first_depth = None
        self.last_depth = None

    @staticmethod
    def _sum_usd(levels):
        s = 0.0
        for px_s, qty_s in levels:
            s += float(px_s) * float(qty_s)
        return s

    def update(self, bids_levels, asks_levels):
        bids = bids_levels[: self.n]
        asks = asks_levels[: self.n]

        bid_usd = self._sum_usd(bids)
        ask_usd = self._sum_usd(asks)
        tot = bid_usd + ask_usd
        imb = (bid_usd - ask_usd) / (tot + EPS)

        if self.first_depth is None:
            self.first_depth = tot
        self.last_depth = tot

        self.depth_usd.append(tot)
        self.imb.append(imb)

    def close_minute(self) -> Optional[Dict[str, float]]:
        if not self.depth_usd or self.first_depth is None or self.last_depth is None:
            self._reset()
            return None

        x = np.array(self.depth_usd, dtype=float)
        y = np.array(self.imb, dtype=float)

        out = {
            "depth_usd_median": float(np.median(x)),
            "depth_usd_p10": float(np.quantile(x, 0.10)),
            "depth_usd_min": float(np.min(x)),
            "imb_median": float(np.median(y)),
            "depth_recover": float((self.last_depth + EPS) / (self.first_depth + EPS)),
            "n_updates_depth": float(len(x)),
        }
        self._reset()
        return out

    def _reset(self):
        self.depth_usd.clear()
        self.imb.clear()
        self.first_depth = None
        self.last_depth = None


# ==================================
# Minute Aggregator: aggTrade (taker)
# ==================================
class AggTradeMinuteAgg:
    """
    Collects aggTrade updates inside a minute.

    Binance fstream aggTrade fields:
      - p: price (str)
      - q: quantity (str)
      - m: isBuyerMaker (bool)
          True  => buyer is maker => taker is seller => "aggressive sell"
          False => taker is buyer  => "aggressive buy"

    Outputs:
      - dollar_vol : sum(price * qty)
      - cvd_delta  : buy_qty - sell_qty (taker-side)
      - qty_total  : total traded qty
      - n_trades
    """
    def __init__(self):
        self.dollar_vol = 0.0
        self.buy_qty = 0.0
        self.sell_qty = 0.0
        self.qty_total = 0.0
        self.n_trades = 0

    def update(self, price_s: str, qty_s: str, is_buyer_maker: bool):
        px = float(price_s)
        qty = float(qty_s)

        self.dollar_vol += px * qty
        self.qty_total += qty
        self.n_trades += 1

        if is_buyer_maker:
            self.sell_qty += qty
        else:
            self.buy_qty += qty

    def close_minute(self) -> Dict[str, float]:
        out = {
            "dollar_vol": float(self.dollar_vol),
            "buy_qty": float(self.buy_qty),
            "sell_qty": float(self.sell_qty),
            "cvd_delta": float(self.buy_qty - self.sell_qty),
            "qty_total": float(self.qty_total),
            "n_trades": float(self.n_trades),
        }
        self._reset()
        return out

    def _reset(self):
        self.dollar_vol = 0.0
        self.buy_qty = 0.0
        self.sell_qty = 0.0
        self.qty_total = 0.0
        self.n_trades = 0


# =====================================
# Rolling 30-minute buffers for scaling
# =====================================
@dataclass
class Rolling30m:
    """
    Rolling window (default 30 minutes) for robust normalization.

    We store raw metrics; later compute robust z on each.
    """
    impact: deque
    spread_med: deque
    spread_p95: deque
    spike_ratio: deque
    depth_med: deque
    depth_p10: deque
    depth_recover: deque
    dollar_vol: deque  # used for COLD (volume coldness)

    def __init__(self, maxlen=30):
        self.impact = deque(maxlen=maxlen)
        self.spread_med = deque(maxlen=maxlen)
        self.spread_p95 = deque(maxlen=maxlen)
        self.spike_ratio = deque(maxlen=maxlen)
        self.depth_med = deque(maxlen=maxlen)
        self.depth_p10 = deque(maxlen=maxlen)
        self.depth_recover = deque(maxlen=maxlen)
        self.dollar_vol = deque(maxlen=maxlen)

    @staticmethod
    def _np(dq: deque) -> np.ndarray:
        return np.array(list(dq), dtype=float)

    def push(self, m: Dict[str, float]):
        self.impact.append(m["impact"])
        self.spread_med.append(m["spread_median"])
        self.spread_p95.append(m["spread_p95"])
        self.spike_ratio.append(m["spike_ratio"])
        self.depth_med.append(m["depth_usd_median"])
        self.depth_p10.append(m["depth_usd_p10"])
        self.depth_recover.append(m["depth_recover"])
        self.dollar_vol.append(m["dollar_vol"])

    # convenient getters
    def np_impact(self): return self._np(self.impact)
    def np_spread_med(self): return self._np(self.spread_med)
    def np_spread_p95(self): return self._np(self.spread_p95)
    def np_spike_ratio(self): return self._np(self.spike_ratio)
    def np_depth_med(self): return self._np(self.depth_med)
    def np_depth_p10(self): return self._np(self.depth_p10)
    def np_depth_recover(self): return self._np(self.depth_recover)
    def np_dollar_vol(self): return self._np(self.dollar_vol)


# ===========================================
# Factor computation: LHF (healthy) + COLD (fragile)
# ===========================================
def compute_factors(minute: Dict[str, float], roll: Rolling30m) -> Dict[str, Any]:
    """
    Input minute fields (must exist):
      - ret_1m: minute return using mid_close/mid_open
      - dollar_vol: sum(price*qty)
      - qty_total: total qty
      - cvd_delta: taker buy qty - taker sell qty
      - spread_median, spread_p95, spike_ratio
      - depth_usd_median, depth_usd_p10, depth_recover

    Derived:
      - impact: |ret| / dollar_vol  (higher => worse)
      - absorption_flag: large CVD intensity + large vol but price barely moves (risk)
      - flow_cons: sign(ret) vs sign(cvd_delta) consistency (health bonus)
      - z-scores: robust z within rolling 30m
    """
    # ============ derived microstructure metrics ============
    impact = abs(minute["ret_1m"]) / (minute["dollar_vol"] + EPS)
    minute["impact"] = impact

    # absorption: big orderflow energy but price barely moves => absorption / hidden liquidity
    cvd_intensity = abs(minute["cvd_delta"]) / (minute["qty_total"] + EPS)
    absorption = (cvd_intensity > 0.55) and (minute["dollar_vol"] > 0) and (abs(minute["ret_1m"]) < 0.0003)
    minute["absorption_flag"] = 1.0 if absorption else 0.0

    # flow-price consistency: in healthy discovery, orderflow direction often matches return direction
    if minute["ret_1m"] == 0.0 or minute["cvd_delta"] == 0.0:
        flow_cons = 0.5
    else:
        flow_cons = 1.0 if np.sign(minute["ret_1m"]) == np.sign(minute["cvd_delta"]) else 0.0
    minute["flow_cons"] = float(flow_cons)

    # ============ robust normalization with rolling 30m ============
    # LHF normalization
    z_impact = robust_z(impact, roll.np_impact())
    z_spread = robust_z(minute["spread_median"], roll.np_spread_med())
    z_spread_tail = robust_z(minute["spread_p95"], roll.np_spread_p95())
    z_spike = robust_z(minute["spike_ratio"], roll.np_spike_ratio())

    # depth uses log compression (depth distribution is heavy-tailed)
    z_depth = robust_z(
        math.log(minute["depth_usd_median"] + 1.0),
        np.log(roll.np_depth_med() + 1.0) + EPS
    )
    z_depth_p10 = robust_z(
        math.log(minute["depth_usd_p10"] + 1.0),
        np.log(roll.np_depth_p10() + 1.0) + EPS
    )
    z_res = robust_z(minute["depth_recover"], roll.np_depth_recover())

    # COLD additionally uses dollar volume (low vol => colder)
    z_dv = robust_z(
        math.log(minute["dollar_vol"] + 1.0),
        np.log(roll.np_dollar_vol() + 1.0) + EPS
    )

    # ======================================================
    # LHF: Liquidity Health Factor (higher is better)
    #   Good signals:
    #     - low impact (good = -z_impact)
    #     - tight spread (good = -z_spread)
    #     - less spiky spread (good = -z_spike)
    #     - higher depth (good = +z_depth, +z_depth_p10)
    #     - better resilience (good = +z_res)
    #     - flow consistency (bonus)
    #     - absorption risk (penalty)
    # ======================================================
    lhf_good = 0.0
    lhf_good += 0.30 * (-z_impact)
    lhf_good += 0.18 * (-z_spread)
    lhf_good += 0.10 * (-z_spread_tail)
    lhf_good += 0.07 * (-z_spike)
    lhf_good += 0.20 * (z_depth)
    lhf_good += 0.10 * (z_depth_p10)
    lhf_good += 0.10 * (z_res)
    lhf_good += 0.05 * (2.0 * flow_cons - 1.0)         # 0/1 -> -1/+1
    lhf_good += 0.10 * (-(minute["absorption_flag"]))   # absorption => penalty

    lhf = 100.0 * sigmoid(1.2 * lhf_good)

    # ======================================================
    # COLD: Cold/Fragility Factor (higher is worse / colder)
    #   Cold/fragile signals:
    #     - low dollar volume      (cold = -z_dv)
    #     - thin depth (median/p10)(cold = -(z_depth, z_depth_p10))
    #     - wide spread            (cold = +z_spread, +z_spread_tail)
    #     - spiky spread           (cold = +z_spike)
    #     - high impact            (cold = +z_impact)
    #     - poor resilience        (cold = -z_res)
    #
    # Interpretation:
    #   High COLD => easier for small flows to move price, more vacuum moves, more manipulation risk.
    # ======================================================
    cold_bad = 0.0
    cold_bad += 0.28 * (-z_dv)            # low volume => colder
    cold_bad += 0.18 * (-z_depth)         # thin depth => colder
    cold_bad += 0.12 * (-z_depth_p10)     # thin tail depth => colder
    cold_bad += 0.15 * (z_spread)         # wider spread => colder
    cold_bad += 0.07 * (z_spread_tail)    # tail spread pressure
    cold_bad += 0.06 * (z_spike)          # spiky spread
    cold_bad += 0.10 * (z_impact)         # vacuum impact
    cold_bad += 0.04 * (-z_res)           # slow recovery => colder
    cold_bad += 0.05 * (minute["absorption_flag"])  # absorption increases fragility

    cold = 100.0 * sigmoid(1.2 * cold_bad)

    # Push current minute into rolling window (after computing z based on history)
    roll.push({
        "impact": impact,
        "spread_median": minute["spread_median"],
        "spread_p95": minute["spread_p95"],
        "spike_ratio": minute["spike_ratio"],
        "depth_usd_median": minute["depth_usd_median"],
        "depth_usd_p10": minute["depth_usd_p10"],
        "depth_recover": minute["depth_recover"],
        "dollar_vol": minute["dollar_vol"],
    })

    return {
        "LHF": float(lhf),
        "COLD": float(cold),
        "lhf_good_score": float(lhf_good),
        "cold_bad_score": float(cold_bad),
        "flags": {
            "absorption": bool(absorption),
            "flow_cons": float(flow_cons),
        }
    }


# =========================
# Websocket + minute switch
# =========================
STREAM_URL = "wss://fstream.binance.com/stream?streams=" + "/".join([
    "btcusdt@aggTrade",
    "btcusdt@bookTicker",
    "btcusdt@depth10@100ms",
])


def floor_minute_ms(ts_ms: int) -> int:
    return (ts_ms // 60000) * 60000


async def run():
    bt = BookTickerMinuteAgg()
    dp = Depth10MinuteAgg(n_levels=10)
    at = AggTradeMinuteAgg()

    roll = Rolling30m(maxlen=30)

    current_minute_ms: Optional[int] = None

    print("Connecting:", STREAM_URL)

    while True:
        try:
            async with websockets.connect(STREAM_URL, ping_interval=20, ping_timeout=20) as ws:
                async for raw in ws:
                    msg = json.loads(raw)
                    stream = msg.get("stream", "")
                    data = msg.get("data", {})

                    # Event time 'E' (ms). Fallback to local time if missing.
                    ts_ms = int(data.get("E", int(time.time() * 1000)))
                    m_ms = floor_minute_ms(ts_ms)

                    if current_minute_ms is None:
                        current_minute_ms = m_ms

                    # If minute changed: close & output the previous minute factors
                    if m_ms != current_minute_ms:
                        bt_out = bt.close_minute()
                        dp_out = dp.close_minute()
                        at_out = at.close_minute()

                        # Require bookTicker + depth to exist; aggTrade may be empty in extreme edge cases
                        if bt_out and dp_out:
                            # Use mid open/close for minute return (cleaner than last trade)
                            ret_1m = (bt_out["mid_close"] / (bt_out["mid_open"] + EPS)) - 1.0

                            minute = {
                                "minute_ts_ms": current_minute_ms,
                                "ret_1m": float(ret_1m),

                                # spread distribution from bookTicker
                                "spread_median": bt_out["spread_median"],
                                "spread_p95": bt_out["spread_p95"],
                                "spread_max": bt_out["spread_max"],
                                "spike_ratio": bt_out["spike_ratio"],

                                # depth distribution & resilience from depth10 snapshots
                                "depth_usd_median": dp_out["depth_usd_median"],
                                "depth_usd_p10": dp_out["depth_usd_p10"],
                                "depth_usd_min": dp_out["depth_usd_min"],
                                "imb_median": dp_out["imb_median"],
                                "depth_recover": dp_out["depth_recover"],

                                # orderflow from aggTrade (taker side)
                                "dollar_vol": at_out["dollar_vol"],
                                "qty_total": at_out["qty_total"],
                                "cvd_delta": at_out["cvd_delta"],
                                "n_trades": at_out["n_trades"],
                            }

                            out = compute_factors(minute, roll)

                            # Minute close output
                            # For downstream LLMs: include fields with self-explanatory names.
                            ts_sec = minute["minute_ts_ms"] // 1000
                            print(json.dumps({
                                "t": ts_sec,  # unix seconds of minute start
                                "LHF": out["LHF"],          # 0~100, higher=healthier
                                "COLD": out["COLD"],        # 0~100, higher=colder/fragile
                                "ret_1m": minute["ret_1m"],
                                "impact": minute["impact"], # |ret|/dollar_vol, higher=worse
                                "dollar_vol": minute["dollar_vol"],
                                "cvd_delta": minute["cvd_delta"],

                                "spread_median": minute["spread_median"],
                                "spread_p95": minute["spread_p95"],
                                "spike_ratio": minute["spike_ratio"],

                                "depth_usd_median": minute["depth_usd_median"],
                                "depth_usd_p10": minute["depth_usd_p10"],
                                "depth_recover": minute["depth_recover"],
                                "imb_median": minute["imb_median"],

                                "scores": {
                                    "lhf_good": out["lhf_good_score"],   # latent score before sigmoid
                                    "cold_bad": out["cold_bad_score"],   # latent score before sigmoid
                                },
                                "flags": out["flags"],  # absorption / flow_cons
                            }, ensure_ascii=False))

                        # Switch minute
                        current_minute_ms = m_ms

                    # Handle current tick update
                    if stream.endswith("@bookTicker"):
                        # bookTicker fields: b bidPrice, a askPrice (strings)
                        bid1 = float(data["b"])
                        ask1 = float(data["a"])
                        bt.update(bid1, ask1)

                    elif stream.endswith("@aggTrade"):
                        # aggTrade fields: p price, q qty, m isBuyerMaker
                        at.update(data["p"], data["q"], bool(data["m"]))

                    elif "@depth10@100ms" in stream:
                        # depth10 snapshot fields: b bids, a asks
                        bids = data.get("b", [])
                        asks = data.get("a", [])
                        if bids and asks:
                            dp.update(bids, asks)

        except Exception as e:
            print("WS error, retry in 3s:", repr(e))
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(run())
