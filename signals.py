"""
Signal Engine — generates high-quality BUY/SELL signals.
Only emits signals when confidence score >= MIN_SIGNAL_SCORE (90%).
Multi-timeframe confirmation required (5m, 15m, 1h, 4h, 1d).
"""

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass

import binance_client as bc
import database as db
from analysis import analyze, AnalysisResult
from adaptive_learning import get_weighted_score
from config import MIN_SIGNAL_SCORE, TARGET_RR_OPTIONS, ANALYSIS_TIMEFRAMES

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    symbol: str
    direction: str           # 'BUY' | 'SELL'
    entry: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    score: float             # 0–100
    reasons: list[str]
    timeframe: str           # primary timeframe
    trend: str
    volume_status: str
    momentum_status: str
    indicators: dict

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "direction": self.direction,
            "entry": self.entry, "stop_loss": self.stop_loss,
            "take_profit": self.take_profit, "rr_ratio": self.rr_ratio,
            "score": self.score, "reasons": self.reasons,
            "timeframe": self.timeframe, "trend": self.trend,
            "volume_status": self.volume_status,
            "momentum_status": self.momentum_status,
        }


# ── Scoring functions ──────────────────────────────────────────────────────────

def _score_trend(ar: AnalysisResult) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    t = ar.trend
    if t == "strong_uptrend":
        score += 20
        reasons.append("Strong uptrend (EMA stack)")
    elif t == "uptrend":
        score += 12
        reasons.append("Uptrend (EMA alignment)")
    elif t == "strong_downtrend":
        score += 20
        reasons.append("Strong downtrend (EMA stack)")
    elif t == "downtrend":
        score += 12
        reasons.append("Downtrend (EMA alignment)")
    return score, reasons


def _score_momentum(ar: AnalysisResult) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    if ar.momentum in ("bullish", "oversold"):
        score += 10
        reasons.append(f"Momentum: {ar.momentum} (RSI {ar.rsi_val:.1f})")
    elif ar.momentum in ("bearish", "overbought"):
        score += 10
        reasons.append(f"Momentum: {ar.momentum} (RSI {ar.rsi_val:.1f})")
    if not math.isnan(ar.macd_hist):
        if ar.macd_hist > 0:
            score += 8
            reasons.append("MACD histogram positive (bullish)")
        elif ar.macd_hist < 0:
            score += 8
            reasons.append("MACD histogram negative (bearish)")
    if not math.isnan(ar.adx_val) and ar.adx_val > 25:
        score += 7
        reasons.append(f"ADX {ar.adx_val:.1f} — trending market")
    return score, reasons


def _score_volume(ar: AnalysisResult) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    vs = ar.volume_info.get("status", "normal")
    ratio = ar.volume_info.get("ratio", 1.0)
    if vs == "spike":
        score += 12
        reasons.append(f"Volume spike ({ratio:.1f}x avg)")
    elif vs == "high":
        score += 6
        reasons.append(f"Above-average volume ({ratio:.1f}x)")
    if ar.obv_trend == "rising":
        score += 5
        reasons.append("OBV rising (buying pressure)")
    elif ar.obv_trend == "falling":
        score += 5
        reasons.append("OBV falling (selling pressure)")
    return score, reasons


def _score_structure(ar: AnalysisResult) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    ms = ar.market_structure
    if ms.get("bos"):
        score += 10
        reasons.append("Break of Structure detected")
    if ms.get("choch"):
        score += 8
        reasons.append("Change of Character detected")
    if ar.supertrend_dir == 1:
        score += 7
        reasons.append("SuperTrend bullish")
    elif ar.supertrend_dir == -1:
        score += 7
        reasons.append("SuperTrend bearish")
    if ar.candle_patterns:
        score += min(8, len(ar.candle_patterns) * 4)
        reasons.append(f"Candlestick: {', '.join(ar.candle_patterns[:3])}")
    if ar.order_blocks.get("bullish") or ar.order_blocks.get("bearish"):
        score += 5
        reasons.append("Order block present near price")
    if ar.fvgs.get("bullish") or ar.fvgs.get("bearish"):
        score += 3
        reasons.append("Fair Value Gap detected")
    return score, reasons


def _score_ichimoku(ar: AnalysisResult, direction: str) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    ichi = ar.ichi
    n = -1
    try:
        tenkan = ichi["tenkan"][n]
        kijun = ichi["kijun"][n]
        span_a = ichi["span_a"][n]
        span_b = ichi["span_b"][n]
        close = ar.close
        if math.isnan(tenkan) or math.isnan(kijun):
            return score, reasons
        cloud_top = max(span_a, span_b) if not (math.isnan(span_a) or math.isnan(span_b)) else float("nan")
        cloud_bot = min(span_a, span_b) if not (math.isnan(span_a) or math.isnan(span_b)) else float("nan")
        if direction == "BUY":
            if tenkan > kijun:
                score += 5
                reasons.append("Ichimoku: TK cross bullish")
            if not math.isnan(cloud_bot) and close > cloud_top:
                score += 5
                reasons.append("Price above Ichimoku cloud")
        else:
            if tenkan < kijun:
                score += 5
                reasons.append("Ichimoku: TK cross bearish")
            if not math.isnan(cloud_top) and close < cloud_bot:
                score += 5
                reasons.append("Price below Ichimoku cloud")
    except Exception:
        pass
    return score, reasons


def _determine_direction(ar: AnalysisResult) -> str | None:
    """Return 'BUY', 'SELL', or None if sideways/unclear."""
    if ar.trend == "sideways":
        return None
    if ar.trend in ("strong_uptrend", "uptrend"):
        if ar.supertrend_dir == 1 and ar.momentum in ("bullish", "oversold", "neutral"):
            return "BUY"
    if ar.trend in ("strong_downtrend", "downtrend"):
        if ar.supertrend_dir == -1 and ar.momentum in ("bearish", "overbought", "neutral"):
            return "SELL"
    return None


def _calculate_levels(ar: AnalysisResult, direction: str) -> tuple[float, float, float, float] | None:
    """Returns (entry, stop_loss, take_profit, rr_ratio) or None."""
    atr_v = ar.atr_val
    if math.isnan(atr_v) or atr_v <= 0:
        return None
    entry = ar.close
    if direction == "BUY":
        stop_loss = entry - atr_v * 1.5
        # Try to align SL with nearest support
        supports = [s for s in ar.support if s < entry]
        if supports:
            nearest_support = max(supports)
            stop_loss = min(stop_loss, nearest_support - atr_v * 0.3)
    else:
        stop_loss = entry + atr_v * 1.5
        resistances = [r for r in ar.resistance if r > entry]
        if resistances:
            nearest_resistance = min(resistances)
            stop_loss = max(stop_loss, nearest_resistance + atr_v * 0.3)
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None
    # Choose best RR
    for rr in sorted(TARGET_RR_OPTIONS, reverse=True):
        tp = entry + risk * rr if direction == "BUY" else entry - risk * rr
        if tp > 0:
            return entry, stop_loss, tp, rr
    return None


# ── Multi-TF analysis ──────────────────────────────────────────────────────────

async def _multi_tf_analysis(symbol: str) -> dict[str, AnalysisResult | None]:
    """Fetch klines and analyse for all timeframes in parallel."""
    async def fetch_tf(tf: str) -> tuple[str, AnalysisResult | None]:
        klines = await bc.get_klines(symbol, tf, limit=250, futures=True)
        if not klines:
            return tf, None
        return tf, analyze(symbol, tf, klines)

    results = await asyncio.gather(*[fetch_tf(tf) for tf in ANALYSIS_TIMEFRAMES], return_exceptions=True)
    out = {}
    for res in results:
        if isinstance(res, Exception):
            continue
        tf, ar = res
        out[tf] = ar
    return out


def _mtf_alignment_bonus(tf_results: dict[str, AnalysisResult | None], direction: str) -> tuple[float, list[str]]:
    """Add bonus score for higher-TF confirmation."""
    score, reasons = 0.0, []
    confirm_map = {
        "1h": 6, "4h": 8, "1d": 10,
    }
    for tf, bonus in confirm_map.items():
        ar = tf_results.get(tf)
        if ar is None:
            continue
        if direction == "BUY" and ar.trend in ("uptrend", "strong_uptrend"):
            score += bonus
            reasons.append(f"{tf} confirms uptrend")
        elif direction == "SELL" and ar.trend in ("downtrend", "strong_downtrend"):
            score += bonus
            reasons.append(f"{tf} confirms downtrend")
    return score, reasons


# ── Main signal generation ─────────────────────────────────────────────────────

async def generate_signal(opportunity: dict) -> Signal | None:
    """
    Full pipeline: fetch multi-TF data → analyse → score → validate.
    Returns a Signal if score >= MIN_SIGNAL_SCORE, else None.
    """
    symbol = opportunity["symbol"]
    try:
        tf_results = await _multi_tf_analysis(symbol)
        primary_ar = tf_results.get("15m") or tf_results.get("1h")
        if primary_ar is None:
            return None

        direction = _determine_direction(primary_ar)
        if direction is None:
            return None

        levels = _calculate_levels(primary_ar, direction)
        if levels is None:
            return None
        entry, sl, tp, rr = levels

        # Aggregate score from multiple factors
        total_score = 0.0
        all_reasons: list[str] = []

        s, r = _score_trend(primary_ar)
        total_score += s; all_reasons += r

        s, r = _score_momentum(primary_ar)
        total_score += s; all_reasons += r

        s, r = _score_volume(primary_ar)
        total_score += s; all_reasons += r

        s, r = _score_structure(primary_ar)
        total_score += s; all_reasons += r

        s, r = _score_ichimoku(primary_ar, direction)
        total_score += s; all_reasons += r

        s, r = _mtf_alignment_bonus(tf_results, direction)
        total_score += s; all_reasons += r

        # Apply adaptive learning weight boost
        weight = get_weighted_score(symbol, direction, primary_ar.timeframe)
        total_score *= weight
        total_score = min(total_score, 100.0)

        if total_score < MIN_SIGNAL_SCORE:
            return None

        # Persist signal to DB
        sig_id = db.insert_signal(
            symbol=symbol,
            direction=direction,
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            rr_ratio=rr,
            score=total_score,
            reasons="; ".join(all_reasons),
            timeframe=primary_ar.timeframe,
            trend=primary_ar.trend,
            volume_status=primary_ar.volume_info.get("status"),
            momentum_status=primary_ar.momentum,
            indicators_json=json.dumps(primary_ar.to_dict()),
        )
        db.log_event(
            "Signal Generated",
            f"{direction} {symbol} score={total_score:.1f}% rr={rr}",
            "INFO",
        )
        return Signal(
            symbol=symbol, direction=direction,
            entry=entry, stop_loss=sl, take_profit=tp,
            rr_ratio=rr, score=total_score,
            reasons=all_reasons, timeframe=primary_ar.timeframe,
            trend=primary_ar.trend,
            volume_status=primary_ar.volume_info.get("status", "normal"),
            momentum_status=primary_ar.momentum,
            indicators=primary_ar.to_dict(),
        )

    except Exception as exc:
        logger.error("generate_signal %s error: %s", symbol, exc)
        return None


def format_signal_message(sig: Signal) -> str:
    dir_emoji = "📈" if sig.direction == "BUY" else "📉"
    score_bar = "█" * int(sig.score / 10) + "░" * (10 - int(sig.score / 10))
    reasons_text = "\n".join(f"  • {r}" for r in sig.reasons[:8])
    return (
        f"{dir_emoji} <b>AI SIGNAL — {sig.symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Direction:</b> {sig.direction}\n"
        f"💰 <b>Entry:</b> <code>{sig.entry:.6f}</code>\n"
        f"🛡 <b>Stop Loss:</b> <code>{sig.stop_loss:.6f}</code>\n"
        f"✅ <b>Take Profit:</b> <code>{sig.take_profit:.6f}</code>\n"
        f"⚖️ <b>Risk/Reward:</b> 1:{sig.rr_ratio}\n"
        f"📊 <b>Timeframe:</b> {sig.timeframe.upper()}\n"
        f"🌊 <b>Trend:</b> {sig.trend.replace('_', ' ').title()}\n"
        f"💹 <b>Volume:</b> {sig.volume_status.title()}\n"
        f"⚡ <b>Momentum:</b> {sig.momentum_status.title()}\n"
        f"\n🧠 <b>Confidence Score:</b> {sig.score:.1f}%\n"
        f"<code>[{score_bar}]</code>\n"
        f"\n📋 <b>Reasons:</b>\n{reasons_text}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>Not financial advice. Trade responsibly.</i>"
    )
