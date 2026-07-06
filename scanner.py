"""
Market Scanner — runs as a background asyncio task.
Scans all USDT pairs every SCANNER_FAST_INTERVAL or SCANNER_SLOW_INTERVAL seconds,
filters low-quality markets, and feeds the signal engine.
"""

import asyncio
import logging
import time
from typing import Callable, Awaitable

import binance_client as bc
import database as db
from config import (
    SCANNER_FAST_INTERVAL,
    SCANNER_SLOW_INTERVAL,
    MIN_VOLUME_USDT,
    MAX_SPREAD_PCT,
    ANALYSIS_TIMEFRAMES,
)

logger = logging.getLogger(__name__)

# Shared state accessible from admin panel
_scanner_status: dict = {
    "running": False,
    "interval": SCANNER_SLOW_INTERVAL,
    "last_scan": 0.0,
    "pairs_scanned": 0,
    "opportunities": [],
    "errors": 0,
}


def get_scanner_status() -> dict:
    return dict(_scanner_status)


def set_scanner_interval(interval: int) -> None:
    _scanner_status["interval"] = interval


# ── Filtering ──────────────────────────────────────────────────────────────────

def _is_quality_market(ticker: dict) -> bool:
    """
    Returns True if the market passes quality filters:
    - 24h quote volume >= MIN_VOLUME_USDT
    - Price change is not extreme manipulation (> ±30%)
    """
    try:
        quote_vol = float(ticker.get("quoteVolume", 0))
        price_change_pct = abs(float(ticker.get("priceChangePercent", 0)))
        last_price = float(ticker.get("lastPrice", 0))
        if last_price <= 0:
            return False
        if quote_vol < MIN_VOLUME_USDT:
            return False
        if price_change_pct > 30:
            return False
        return True
    except Exception:
        return False


async def _check_spread(symbol: str) -> bool:
    """Check if bid/ask spread is within MAX_SPREAD_PCT."""
    try:
        ticker = await bc.get_orderbook_ticker(symbol, futures=True)
        bid = float(ticker.get("bidPrice", 0))
        ask = float(ticker.get("askPrice", 0))
        if bid <= 0 or ask <= 0:
            return False
        spread_pct = (ask - bid) / bid * 100
        return spread_pct <= MAX_SPREAD_PCT
    except Exception:
        return False


# ── Opportunity ranking ────────────────────────────────────────────────────────

def _rank_opportunities(tickers: list[dict]) -> list[dict]:
    """
    Rank symbols by a composite score:
    volume_rank * |price_change| weighted by volume consistency.
    Returns top 50 symbols.
    """
    scored = []
    for t in tickers:
        try:
            vol = float(t.get("quoteVolume", 0))
            change = abs(float(t.get("priceChangePercent", 0)))
            count = int(t.get("count", 1))
            score = (vol / 1_000_000) * change * (count / 1000)
            scored.append({
                "symbol": t["symbol"],
                "score": round(score, 4),
                "volume": vol,
                "change_pct": float(t.get("priceChangePercent", 0)),
                "price": float(t.get("lastPrice", 0)),
                "count": count,
            })
        except Exception:
            continue
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:50]


# ── Signal callback type ───────────────────────────────────────────────────────

SignalCallback = Callable[[dict], Awaitable[None]]


# ── Main scanner task ──────────────────────────────────────────────────────────

async def run_scanner(signal_callback: SignalCallback | None = None) -> None:
    """
    Continuous scanner loop.  Calls signal_callback(opportunity_dict)
    for each ranked opportunity so the signal engine can analyse it.
    """
    _scanner_status["running"] = True
    db.log_event("Scanner Started", "Market scanner background task started", "INFO")
    logger.info("Market scanner started")

    while True:
        try:
            start = time.monotonic()

            # Fetch all USDT futures tickers
            tickers_raw = await bc.get_ticker_24h(futures=True)
            if not isinstance(tickers_raw, list):
                tickers_raw = [tickers_raw] if tickers_raw else []

            # Filter quality markets
            quality = [t for t in tickers_raw if _is_quality_market(t)]

            # Rank opportunities
            ranked = _rank_opportunities(quality)
            _scanner_status["opportunities"] = ranked[:20]
            _scanner_status["pairs_scanned"] = len(quality)
            _scanner_status["last_scan"] = time.time()

            # Feed top 20 to signal engine
            if signal_callback and ranked:
                tasks = [signal_callback(opp) for opp in ranked[:20]]
                await asyncio.gather(*tasks, return_exceptions=True)

            elapsed = time.monotonic() - start
            interval = _scanner_status["interval"]
            wait = max(0.1, interval - elapsed)
            await asyncio.sleep(wait)

        except asyncio.CancelledError:
            logger.info("Scanner task cancelled")
            break
        except Exception as exc:
            _scanner_status["errors"] += 1
            db.log_event("Scanner Error", str(exc), "ERROR")
            logger.error("Scanner error: %s", exc)
            await asyncio.sleep(10)

    _scanner_status["running"] = False
    logger.info("Market scanner stopped")


async def get_top_opportunities(limit: int = 10) -> list[dict]:
    """Return cached top opportunities from last scan."""
    return _scanner_status["opportunities"][:limit]
