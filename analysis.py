"""
Technical analysis module.
All indicator calculations are done on raw OHLCV data (no TA-Lib dependency).
Supports: EMA, VWAP, MACD, RSI, ADX, ATR, SuperTrend, Ichimoku, Bollinger Bands,
          Volume Spike, OBV, Support/Resistance, Order Blocks, FVG,
          Candlestick Patterns, CHOCH/BOS, Market Structure.
"""

from __future__ import annotations
import math
import logging
from dataclasses import dataclass, field
from typing import NamedTuple

logger = logging.getLogger(__name__)


# ── Data helpers ───────────────────────────────────────────────────────────────

def _parse_klines(raw: list[list]) -> dict[str, list[float]]:
    """Convert raw Binance klines to typed lists."""
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for k in raw:
        opens.append(float(k[1]))
        highs.append(float(k[2]))
        lows.append(float(k[3]))
        closes.append(float(k[4]))
        volumes.append(float(k[5]))
    return {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}


# ── Basic indicators ───────────────────────────────────────────────────────────

def ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average."""
    if len(values) < period:
        return [float("nan")] * len(values)
    k = 2 / (period + 1)
    result = [float("nan")] * (period - 1)
    seed = sum(values[:period]) / period
    result.append(seed)
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def sma(values: list[float], period: int) -> list[float]:
    result = [float("nan")] * (period - 1)
    for i in range(period, len(values) + 1):
        result.append(sum(values[i - period:i]) / period)
    return result


def vwap(highs: list[float], lows: list[float], closes: list[float], volumes: list[float]) -> list[float]:
    """Cumulative VWAP over the session window."""
    typical = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    cum_tp_v, cum_v = 0.0, 0.0
    result = []
    for tp, v in zip(typical, volumes):
        cum_tp_v += tp * v
        cum_v += v
        result.append(cum_tp_v / cum_v if cum_v else tp)
    return result


def rsi(closes: list[float], period: int = 14) -> list[float]:
    if len(closes) < period + 1:
        return [float("nan")] * len(closes)
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result = [float("nan")] * (period)
    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(100 - 100 / (1 + rs))
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))
    return result


def macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float], list[float], list[float]]:
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [
        f - s if not (math.isnan(f) or math.isnan(s)) else float("nan")
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid_from = next((i for i, v in enumerate(macd_line) if not math.isnan(v)), len(macd_line))
    signal_line = [float("nan")] * valid_from
    if len(macd_line) - valid_from >= signal:
        sub = macd_line[valid_from:]
        sig = ema(sub, signal)
        signal_line += sig
    else:
        signal_line += [float("nan")] * (len(macd_line) - valid_from)
    histogram = [
        m - s if not (math.isnan(m) or math.isnan(s)) else float("nan")
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    if len(closes) < 2:
        return [float("nan")] * len(closes)
    trs = [float("nan")]
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hpc = abs(highs[i] - closes[i - 1])
        lpc = abs(lows[i] - closes[i - 1])
        trs.append(max(hl, hpc, lpc))
    valid = [v for v in trs if not math.isnan(v)]
    if len(valid) < period:
        return [float("nan")] * len(closes)
    result = [float("nan")] * period
    atr_val = sum(valid[:period]) / period
    result.append(atr_val)
    for tr in valid[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
        result.append(atr_val)
    # Pad result to match length
    while len(result) < len(closes):
        result.insert(0, float("nan"))
    return result[-len(closes):]


def adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """Average Directional Index."""
    n = len(closes)
    if n < period * 2:
        return [float("nan")] * n
    dm_plus, dm_minus, tr_list = [], [], []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        dm_plus.append(max(up, 0) if up > down else 0)
        dm_minus.append(max(down, 0) if down > up else 0)
        hl = highs[i] - lows[i]
        hpc = abs(highs[i] - closes[i - 1])
        lpc = abs(lows[i] - closes[i - 1])
        tr_list.append(max(hl, hpc, lpc))

    def _smooth(series: list[float], p: int) -> list[float]:
        out = [sum(series[:p])]
        for v in series[p:]:
            out.append(out[-1] - out[-1] / p + v)
        return out

    atr_s = _smooth(tr_list, period)
    dmp_s = _smooth(dm_plus, period)
    dmm_s = _smooth(dm_minus, period)

    dx_list = []
    for a, p, m in zip(atr_s, dmp_s, dmm_s):
        di_plus = 100 * p / a if a else 0
        di_minus = 100 * m / a if a else 0
        dsum = di_plus + di_minus
        dx_list.append(100 * abs(di_plus - di_minus) / dsum if dsum else 0)

    adx_vals = [sum(dx_list[:period]) / period]
    for dx in dx_list[period:]:
        adx_vals.append((adx_vals[-1] * (period - 1) + dx) / period)

    pad = n - len(adx_vals)
    return [float("nan")] * pad + adx_vals


def bollinger_bands(
    closes: list[float], period: int = 20, std_dev: float = 2.0
) -> tuple[list[float], list[float], list[float]]:
    """Returns (upper, middle, lower)."""
    middle = sma(closes, period)
    upper, lower = [], []
    for i, m in enumerate(middle):
        if math.isnan(m):
            upper.append(float("nan"))
            lower.append(float("nan"))
        else:
            window = closes[max(0, i - period + 1): i + 1]
            std = math.sqrt(sum((x - m) ** 2 for x in window) / len(window))
            upper.append(m + std_dev * std)
            lower.append(m - std_dev * std)
    return upper, middle, lower


def obv(closes: list[float], volumes: list[float]) -> list[float]:
    result = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            result.append(result[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            result.append(result[-1] - volumes[i])
        else:
            result.append(result[-1])
    return result


def supertrend(
    highs: list[float], lows: list[float], closes: list[float],
    period: int = 10, multiplier: float = 3.0,
) -> tuple[list[float], list[int]]:
    """Returns (supertrend_line, direction) where direction 1=up, -1=down."""
    atr_vals = atr(highs, lows, closes, period)
    n = len(closes)
    st = [float("nan")] * n
    direction = [1] * n
    upper_band = [float("nan")] * n
    lower_band = [float("nan")] * n
    for i in range(period, n):
        hl2 = (highs[i] + lows[i]) / 2
        atr_v = atr_vals[i]
        if math.isnan(atr_v):
            continue
        ub = hl2 + multiplier * atr_v
        lb = hl2 - multiplier * atr_v
        if i > period and not math.isnan(upper_band[i - 1]):
            ub = min(ub, upper_band[i - 1]) if closes[i - 1] <= upper_band[i - 1] else ub
            lb = max(lb, lower_band[i - 1]) if closes[i - 1] >= lower_band[i - 1] else lb
        upper_band[i] = ub
        lower_band[i] = lb
        if i == period:
            direction[i] = 1
        else:
            prev_dir = direction[i - 1]
            if prev_dir == 1 and closes[i] < lower_band[i]:
                direction[i] = -1
            elif prev_dir == -1 and closes[i] > upper_band[i]:
                direction[i] = 1
            else:
                direction[i] = prev_dir
        st[i] = lower_band[i] if direction[i] == 1 else upper_band[i]
    return st, direction


def ichimoku(
    highs: list[float], lows: list[float],
    tenkan: int = 9, kijun: int = 26, senkou_b: int = 52, displacement: int = 26,
) -> dict[str, list[float]]:
    n = len(highs)

    def _donchian_mid(h: list[float], l: list[float], p: int, idx: int) -> float:
        if idx < p - 1:
            return float("nan")
        return (max(h[idx - p + 1: idx + 1]) + min(l[idx - p + 1: idx + 1])) / 2

    tenkan_sen = [_donchian_mid(highs, lows, tenkan, i) for i in range(n)]
    kijun_sen = [_donchian_mid(highs, lows, kijun, i) for i in range(n)]
    span_a = [
        (t + k) / 2 if not (math.isnan(t) or math.isnan(k)) else float("nan")
        for t, k in zip(tenkan_sen, kijun_sen)
    ]
    span_b = [_donchian_mid(highs, lows, senkou_b, i) for i in range(n)]
    chikou = closes[displacement:] + [float("nan")] * displacement
    return {
        "tenkan": tenkan_sen,
        "kijun": kijun_sen,
        "span_a": span_a,
        "span_b": span_b,
        "chikou": chikou,
    }


# ── Support / Resistance ───────────────────────────────────────────────────────

def find_support_resistance(highs: list[float], lows: list[float], closes: list[float], window: int = 5) -> tuple[list[float], list[float]]:
    """Simple pivot-based S/R detection."""
    supports, resistances = [], []
    for i in range(window, len(closes) - window):
        if lows[i] == min(lows[i - window: i + window + 1]):
            supports.append(lows[i])
        if highs[i] == max(highs[i - window: i + window + 1]):
            resistances.append(highs[i])
    return supports[-5:], resistances[-5:]


# ── Order Blocks ───────────────────────────────────────────────────────────────

def find_order_blocks(opens: list[float], closes: list[float], highs: list[float], lows: list[float]) -> dict:
    """
    Detect bullish and bearish order blocks.
    A bearish OB is the last up-candle before a strong down move.
    A bullish OB is the last down-candle before a strong up move.
    """
    bullish_obs, bearish_obs = [], []
    n = len(closes)
    for i in range(2, n - 1):
        body_size = abs(closes[i + 1] - opens[i + 1])
        prev_body = abs(closes[i] - opens[i])
        if body_size < 0.0001:
            continue
        if closes[i] < opens[i] and closes[i + 1] > opens[i + 1] and body_size > prev_body * 1.5:
            bullish_obs.append({"top": highs[i], "bottom": lows[i], "index": i})
        if closes[i] > opens[i] and closes[i + 1] < opens[i + 1] and body_size > prev_body * 1.5:
            bearish_obs.append({"top": highs[i], "bottom": lows[i], "index": i})
    return {"bullish": bullish_obs[-3:], "bearish": bearish_obs[-3:]}


# ── Fair Value Gaps ────────────────────────────────────────────────────────────

def find_fair_value_gaps(highs: list[float], lows: list[float]) -> dict:
    """FVGs: gaps between candle i+1 high/low and candle i-1 low/high."""
    bullish_fvgs, bearish_fvgs = [], []
    for i in range(1, len(lows) - 1):
        if lows[i + 1] > highs[i - 1]:
            bullish_fvgs.append({"top": lows[i + 1], "bottom": highs[i - 1], "index": i})
        if highs[i + 1] < lows[i - 1]:
            bearish_fvgs.append({"top": lows[i - 1], "bottom": highs[i + 1], "index": i})
    return {"bullish": bullish_fvgs[-3:], "bearish": bearish_fvgs[-3:]}


# ── Market Structure (CHOCH / BOS) ─────────────────────────────────────────────

def detect_market_structure(highs: list[float], lows: list[float], closes: list[float]) -> dict:
    """
    Detect Change of Character (CHOCH) and Break of Structure (BOS).
    Returns last trend direction and recent structure events.
    """
    n = len(closes)
    if n < 10:
        return {"trend": "unknown", "choch": False, "bos": False}
    recent_highs = highs[-20:]
    recent_lows = lows[-20:]
    hh = all(recent_highs[i] >= recent_highs[i - 1] for i in range(1, len(recent_highs)))
    hl = all(recent_lows[i] >= recent_lows[i - 1] for i in range(1, len(recent_lows)))
    lh = all(recent_highs[i] <= recent_highs[i - 1] for i in range(1, len(recent_highs)))
    ll = all(recent_lows[i] <= recent_lows[i - 1] for i in range(1, len(recent_lows)))
    if hh and hl:
        trend = "uptrend"
    elif lh and ll:
        trend = "downtrend"
    else:
        trend = "sideways"
    prev_high = max(highs[-10:-1])
    prev_low = min(lows[-10:-1])
    bos = closes[-1] > prev_high or closes[-1] < prev_low
    choch = (trend == "uptrend" and closes[-1] < prev_low) or (trend == "downtrend" and closes[-1] > prev_high)
    return {"trend": trend, "choch": choch, "bos": bos, "prev_high": prev_high, "prev_low": prev_low}


# ── Candlestick patterns ───────────────────────────────────────────────────────

def detect_candlestick_patterns(opens: list[float], highs: list[float], lows: list[float], closes: list[float]) -> list[str]:
    patterns = []
    if len(closes) < 3:
        return patterns
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    po, ph, pl, pc = opens[-2], highs[-2], lows[-2], closes[-2]
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    total_range = h - l or 0.0001
    # Hammer / Shooting Star
    if lower_wick > body * 2 and upper_wick < body * 0.3:
        patterns.append("Hammer")
    if upper_wick > body * 2 and lower_wick < body * 0.3:
        patterns.append("Shooting Star")
    # Doji
    if body / total_range < 0.1:
        patterns.append("Doji")
    # Engulfing
    if c > o and pc < po and c > po and o < pc:
        patterns.append("Bullish Engulfing")
    if c < o and pc > po and c < po and o > pc:
        patterns.append("Bearish Engulfing")
    # Marubozu
    if body / total_range > 0.9:
        patterns.append("Marubozu Bullish" if c > o else "Marubozu Bearish")
    # Pinbar
    if lower_wick > body * 2.5:
        patterns.append("Bullish Pinbar")
    if upper_wick > body * 2.5:
        patterns.append("Bearish Pinbar")
    # Morning/Evening Star (3-candle)
    if len(closes) >= 3:
        ppo, pph, ppl, ppc = opens[-3], highs[-3], lows[-3], closes[-3]
        mid_body = abs(pc - po)
        if ppc > ppo and mid_body < abs(ppc - ppo) * 0.3 and c > o and c > (ppo + ppc) / 2:
            patterns.append("Morning Star")
        if ppc < ppo and mid_body < abs(ppc - ppo) * 0.3 and c < o and c < (ppo + ppc) / 2:
            patterns.append("Evening Star")
    return patterns


# ── Volume analysis ────────────────────────────────────────────────────────────

def volume_spike(volumes: list[float], period: int = 20) -> dict:
    if len(volumes) < period + 1:
        return {"spike": False, "ratio": 1.0, "status": "normal"}
    avg = sum(volumes[-period - 1:-1]) / period
    current = volumes[-1]
    ratio = current / avg if avg else 1.0
    return {
        "spike": ratio > 2.0,
        "ratio": ratio,
        "status": "spike" if ratio > 2.0 else "high" if ratio > 1.5 else "normal",
    }


# ── Liquidity zones ────────────────────────────────────────────────────────────

def find_liquidity_zones(highs: list[float], lows: list[float], closes: list[float]) -> dict:
    """Identify equal highs/lows which attract liquidity."""
    tolerance = closes[-1] * 0.002  # 0.2% tolerance
    recent_h = highs[-50:]
    recent_l = lows[-50:]
    eq_highs, eq_lows = [], []
    for i in range(len(recent_h)):
        for j in range(i + 1, len(recent_h)):
            if abs(recent_h[i] - recent_h[j]) < tolerance:
                eq_highs.append((recent_h[i] + recent_h[j]) / 2)
    for i in range(len(recent_l)):
        for j in range(i + 1, len(recent_l)):
            if abs(recent_l[i] - recent_l[j]) < tolerance:
                eq_lows.append((recent_l[i] + recent_l[j]) / 2)
    return {
        "buy_side": list(set(round(v, 6) for v in eq_highs))[:3],
        "sell_side": list(set(round(v, 6) for v in eq_lows))[:3],
    }


# ── Full analysis result ───────────────────────────────────────────────────────

@dataclass
class AnalysisResult:
    symbol: str
    timeframe: str
    close: float
    ema9: float
    ema21: float
    ema50: float
    ema200: float
    vwap_val: float
    rsi_val: float
    macd_val: float
    macd_sig: float
    macd_hist: float
    adx_val: float
    atr_val: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    obv_val: float
    obv_trend: str
    supertrend_val: float
    supertrend_dir: int
    ichi: dict
    volume_info: dict
    support: list[float]
    resistance: list[float]
    order_blocks: dict
    fvgs: dict
    market_structure: dict
    candle_patterns: list[str]
    liquidity: dict
    trend: str
    momentum: str
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "timeframe": self.timeframe,
            "close": self.close, "ema9": self.ema9, "ema21": self.ema21,
            "ema50": self.ema50, "ema200": self.ema200,
            "vwap": self.vwap_val, "rsi": self.rsi_val,
            "macd": self.macd_val, "macd_signal": self.macd_sig,
            "macd_hist": self.macd_hist, "adx": self.adx_val,
            "atr": self.atr_val, "bb_upper": self.bb_upper,
            "bb_lower": self.bb_lower, "supertrend_dir": self.supertrend_dir,
            "trend": self.trend, "momentum": self.momentum,
            "candle_patterns": self.candle_patterns,
            "volume_status": self.volume_info.get("status", "normal"),
            "market_structure": self.market_structure,
        }


def analyze(symbol: str, timeframe: str, raw_klines: list[list]) -> AnalysisResult | None:
    """Run all indicators on raw kline data and return an AnalysisResult."""
    try:
        if len(raw_klines) < 60:
            return None
        d = _parse_klines(raw_klines)
        o, h, l, c, v = d["open"], d["high"], d["low"], d["close"], d["volume"]

        def _last(series: list[float]) -> float:
            for val in reversed(series):
                if not math.isnan(val):
                    return val
            return float("nan")

        ema9_s = ema(c, 9)
        ema21_s = ema(c, 21)
        ema50_s = ema(c, 50)
        ema200_s = ema(c, 200)
        vwap_s = vwap(h, l, c, v)
        rsi_s = rsi(c, 14)
        macd_l, macd_sig_s, macd_hist_s = macd(c)
        adx_s = adx(h, l, c, 14)
        atr_s = atr(h, l, c, 14)
        bb_up, bb_mid, bb_lo = bollinger_bands(c, 20, 2)
        obv_s = obv(c, v)
        st_vals, st_dirs = supertrend(h, l, c)
        ichi_d = ichimoku(h, l)
        vol_info = volume_spike(v)
        supports, resistances = find_support_resistance(h, l, c)
        obs = find_order_blocks(o, c, h, l)
        fvgs = find_fair_value_gaps(h, l)
        ms = detect_market_structure(h, l, c)
        patterns = detect_candlestick_patterns(o, h, l, c)
        liq = find_liquidity_zones(h, l, c)
        obv_trend = "rising" if _last(obv_s) > (obv_s[-20] if len(obv_s) > 20 else 0) else "falling"

        # Trend classification
        e9, e21, e50 = _last(ema9_s), _last(ema21_s), _last(ema50_s)
        close = c[-1]
        if e9 > e21 > e50 and close > e9:
            trend = "strong_uptrend"
        elif e9 > e21 and close > e21:
            trend = "uptrend"
        elif e9 < e21 < e50 and close < e9:
            trend = "strong_downtrend"
        elif e9 < e21 and close < e21:
            trend = "downtrend"
        else:
            trend = "sideways"

        # Momentum classification
        rsi_v = _last(rsi_s)
        if rsi_v > 70:
            momentum = "overbought"
        elif rsi_v > 55:
            momentum = "bullish"
        elif rsi_v < 30:
            momentum = "oversold"
        elif rsi_v < 45:
            momentum = "bearish"
        else:
            momentum = "neutral"

        return AnalysisResult(
            symbol=symbol, timeframe=timeframe, close=close,
            ema9=e9, ema21=e21, ema50=e50, ema200=_last(ema200_s),
            vwap_val=_last(vwap_s), rsi_val=rsi_v,
            macd_val=_last(macd_l), macd_sig=_last(macd_sig_s), macd_hist=_last(macd_hist_s),
            adx_val=_last(adx_s), atr_val=_last(atr_s),
            bb_upper=_last(bb_up), bb_middle=_last(bb_mid), bb_lower=_last(bb_lo),
            obv_val=_last(obv_s), obv_trend=obv_trend,
            supertrend_val=_last(st_vals), supertrend_dir=st_dirs[-1],
            ichi=ichi_d,
            volume_info=vol_info,
            support=supports, resistance=resistances,
            order_blocks=obs, fvgs=fvgs,
            market_structure=ms,
            candle_patterns=patterns,
            liquidity=liq,
            trend=trend, momentum=momentum,
        )
    except Exception as exc:
        logger.error("analysis error %s %s: %s", symbol, timeframe, exc)
        return None
