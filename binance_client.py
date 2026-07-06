"""
Async Binance client wrapper supporting Spot and USDT-Futures on both
Testnet and Real networks.  Uses the official binance-connector-python
library for REST calls and wraps them in asyncio.
"""

import asyncio
import logging
import time
from functools import lru_cache
from typing import Any

from binance.spot import Spot
from binance.um_futures import UMFutures

import database as db
import security

logger = logging.getLogger(__name__)


# ── Factory helpers ────────────────────────────────────────────────────────────

def _build_spot_client(api_key: str, secret_key: str, testnet: bool) -> Spot:
    if testnet:
        return Spot(
            api_key=api_key,
            api_secret=secret_key,
            base_url="https://testnet.binance.vision",
        )
    return Spot(api_key=api_key, api_secret=secret_key)


def _build_futures_client(api_key: str, secret_key: str, testnet: bool) -> UMFutures:
    if testnet:
        return UMFutures(
            key=api_key,
            secret=secret_key,
            base_url="https://testnet.binancefuture.com",
        )
    return UMFutures(key=api_key, secret=secret_key)


# ── Public client factory ──────────────────────────────────────────────────────

def _get_credentials(chat_id: int) -> tuple[str, str, str, str]:
    """
    Returns (api_key, secret_key, account_type, network) for a user.
    Raises ValueError if no API keys are configured.
    """
    row = db.get_api_keys(chat_id)
    if not row:
        raise ValueError("No API keys configured for this user.")
    user = db.get_user(chat_id)
    api_key = security.decrypt(row["enc_api_key"])
    secret_key = security.decrypt(row["enc_secret_key"])
    return api_key, secret_key, user["account_type"], user["network"]


def get_client(chat_id: int) -> Spot | UMFutures:
    """Return the appropriate synchronous client for a user."""
    api_key, secret_key, account_type, network = _get_credentials(chat_id)
    testnet = network == "testnet"
    if account_type == "futures":
        return _build_futures_client(api_key, secret_key, testnet)
    return _build_spot_client(api_key, secret_key, testnet)


def get_public_spot_client() -> Spot:
    """Unauthenticated public Spot client for market data."""
    return Spot()


def get_public_futures_client() -> UMFutures:
    """Unauthenticated public Futures client for market data."""
    return UMFutures()


# ── Async wrappers ─────────────────────────────────────────────────────────────

async def run_sync(func, *args, **kwargs) -> Any:
    """Run a synchronous Binance call in the default executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


# ── Validation ─────────────────────────────────────────────────────────────────

async def validate_api_keys(chat_id: int) -> tuple[bool, str]:
    """
    Attempt to call account info.  Returns (success, message).
    Updates the api_keys table with the result.
    """
    try:
        client = get_client(chat_id)
        user = db.get_user(chat_id)
        if user["account_type"] == "futures":
            await run_sync(client.account)
        else:
            await run_sync(client.account)
        db.mark_api_valid(chat_id, True)
        db.log_event("API Connected", f"chat_id={chat_id}", "INFO", chat_id)
        return True, "✅ API keys validated successfully!"
    except Exception as exc:
        db.mark_api_valid(chat_id, False)
        db.log_event("API Failed", str(exc), "ERROR", chat_id)
        return False, f"❌ Validation failed: {exc}"


# ── Account info ───────────────────────────────────────────────────────────────

async def get_account_info(chat_id: int) -> dict:
    """
    Returns unified account info dict regardless of account type.
    Keys: wallet_balance, available_balance, margin_balance, equity,
          unrealised_pnl, margin_mode, leverage (futures only).
    """
    client = get_client(chat_id)
    user = db.get_user(chat_id)
    try:
        if user["account_type"] == "futures":
            data = await run_sync(client.account)
            assets = data.get("assets", [])
            usdt = next((a for a in assets if a["asset"] == "USDT"), {})
            positions = data.get("positions", [])
            open_pos = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
            return {
                "wallet_balance": float(usdt.get("walletBalance", 0)),
                "available_balance": float(usdt.get("availableBalance", 0)),
                "margin_balance": float(usdt.get("marginBalance", 0) if usdt else data.get("totalMarginBalance", 0)),
                "equity": float(data.get("totalWalletBalance", 0)),
                "unrealised_pnl": float(data.get("totalUnrealizedProfit", 0)),
                "margin_mode": "cross",
                "leverage": "varies",
                "open_positions": open_pos,
                "total_initial_margin": float(data.get("totalInitialMargin", 0)),
                "account_type": "futures",
            }
        else:
            data = await run_sync(client.account)
            balances = {b["asset"]: b for b in data.get("balances", [])}
            usdt = balances.get("USDT", {"free": "0", "locked": "0"})
            return {
                "wallet_balance": float(usdt.get("free", 0)) + float(usdt.get("locked", 0)),
                "available_balance": float(usdt.get("free", 0)),
                "margin_balance": 0.0,
                "equity": float(usdt.get("free", 0)) + float(usdt.get("locked", 0)),
                "unrealised_pnl": 0.0,
                "margin_mode": "N/A",
                "leverage": "N/A",
                "open_positions": [],
                "balances": balances,
                "account_type": "spot",
            }
    except Exception as exc:
        logger.error("get_account_info error: %s", exc)
        raise


async def get_open_orders(chat_id: int) -> list[dict]:
    client = get_client(chat_id)
    user = db.get_user(chat_id)
    try:
        if user["account_type"] == "futures":
            return await run_sync(client.get_orders)
        return await run_sync(client.get_open_orders)
    except Exception as exc:
        logger.error("get_open_orders error: %s", exc)
        return []


async def get_trade_history(chat_id: int, symbol: str, limit: int = 50) -> list[dict]:
    client = get_client(chat_id)
    user = db.get_user(chat_id)
    try:
        if user["account_type"] == "futures":
            return await run_sync(client.get_account_trades, symbol=symbol, limit=limit)
        return await run_sync(client.my_trades, symbol=symbol, limit=limit)
    except Exception as exc:
        logger.error("get_trade_history error: %s", exc)
        return []


async def get_income_history(chat_id: int, income_type: str = "FUNDING_FEE", limit: int = 50) -> list[dict]:
    client = get_client(chat_id)
    user = db.get_user(chat_id)
    if user["account_type"] != "futures":
        return []
    try:
        return await run_sync(client.get_income_history, incomeType=income_type, limit=limit)
    except Exception as exc:
        logger.error("get_income_history error: %s", exc)
        return []


# ── Market data ────────────────────────────────────────────────────────────────

async def get_klines(symbol: str, interval: str, limit: int = 200, futures: bool = True) -> list[list]:
    """Fetch OHLCV klines. Returns list of [open_time, O, H, L, C, V, ...]."""
    try:
        if futures:
            client = get_public_futures_client()
            return await run_sync(client.klines, symbol=symbol, interval=interval, limit=limit)
        else:
            client = get_public_spot_client()
            return await run_sync(client.klines, symbol=symbol, interval=interval, limit=limit)
    except Exception as exc:
        logger.error("get_klines %s %s error: %s", symbol, interval, exc)
        return []


async def get_ticker_24h(symbol: str | None = None, futures: bool = True) -> list[dict] | dict:
    """Get 24h ticker stats for one or all symbols."""
    try:
        if futures:
            client = get_public_futures_client()
            if symbol:
                return await run_sync(client.ticker_24hr_price_change, symbol=symbol)
            return await run_sync(client.ticker_24hr_price_change)
        else:
            client = get_public_spot_client()
            if symbol:
                return await run_sync(client.ticker_24hr, symbol=symbol)
            return await run_sync(client.ticker_24hr)
    except Exception as exc:
        logger.error("get_ticker_24h error: %s", exc)
        return [] if symbol is None else {}


async def get_orderbook_ticker(symbol: str, futures: bool = True) -> dict:
    """Best bid/ask for a symbol."""
    try:
        if futures:
            client = get_public_futures_client()
            return await run_sync(client.book_ticker, symbol=symbol)
        else:
            client = get_public_spot_client()
            return await run_sync(client.book_ticker, symbol=symbol)
    except Exception as exc:
        logger.error("get_orderbook_ticker error: %s", exc)
        return {}


async def get_exchange_info(futures: bool = True) -> dict:
    try:
        if futures:
            client = get_public_futures_client()
            return await run_sync(client.exchange_info)
        else:
            client = get_public_spot_client()
            return await run_sync(client.exchange_info)
    except Exception as exc:
        logger.error("get_exchange_info error: %s", exc)
        return {}


async def get_all_usdt_symbols(futures: bool = True) -> list[str]:
    """Return all active USDT-quoted symbols."""
    info = await get_exchange_info(futures=futures)
    symbols_data = info.get("symbols", [])
    result = []
    for s in symbols_data:
        if futures:
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                result.append(s["symbol"])
        else:
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                result.append(s["symbol"])
    return result


# ── Order execution ────────────────────────────────────────────────────────────

async def place_market_order(
    chat_id: int,
    symbol: str,
    side: str,
    quantity: float,
    reduce_only: bool = False,
) -> dict:
    """Place a market order. side: 'BUY' | 'SELL'."""
    client = get_client(chat_id)
    user = db.get_user(chat_id)
    try:
        if user["account_type"] == "futures":
            kwargs: dict = dict(
                symbol=symbol, side=side, type="MARKET",
                quantity=quantity, reduceOnly=str(reduce_only).lower(),
            )
            result = await run_sync(client.new_order, **kwargs)
        else:
            result = await run_sync(
                client.new_order,
                symbol=symbol, side=side, type="MARKET",
                quantity=quantity,
            )
        db.log_event("Trade Opened", f"{side} {quantity} {symbol}", "INFO", chat_id)
        return result
    except Exception as exc:
        db.log_event("Trade Error", str(exc), "ERROR", chat_id)
        raise


async def place_limit_order(
    chat_id: int,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    reduce_only: bool = False,
) -> dict:
    client = get_client(chat_id)
    user = db.get_user(chat_id)
    try:
        if user["account_type"] == "futures":
            result = await run_sync(
                client.new_order,
                symbol=symbol, side=side, type="LIMIT",
                quantity=quantity, price=price, timeInForce="GTC",
                reduceOnly=str(reduce_only).lower(),
            )
        else:
            result = await run_sync(
                client.new_order,
                symbol=symbol, side=side, type="LIMIT",
                quantity=quantity, price=price, timeInForce="GTC",
            )
        return result
    except Exception as exc:
        db.log_event("Trade Error", str(exc), "ERROR", chat_id)
        raise


async def place_stop_loss_order(
    chat_id: int,
    symbol: str,
    side: str,
    quantity: float,
    stop_price: float,
) -> dict:
    client = get_client(chat_id)
    user = db.get_user(chat_id)
    close_side = "SELL" if side == "BUY" else "BUY"
    try:
        if user["account_type"] == "futures":
            return await run_sync(
                client.new_order,
                symbol=symbol, side=close_side, type="STOP_MARKET",
                stopPrice=stop_price, closePosition="true",
            )
        else:
            return await run_sync(
                client.new_order,
                symbol=symbol, side=close_side, type="STOP_LOSS_LIMIT",
                quantity=quantity, stopPrice=stop_price, price=stop_price,
                timeInForce="GTC",
            )
    except Exception as exc:
        db.log_event("SL Order Error", str(exc), "ERROR", chat_id)
        raise


async def cancel_order(chat_id: int, symbol: str, order_id: int) -> dict:
    client = get_client(chat_id)
    user = db.get_user(chat_id)
    try:
        if user["account_type"] == "futures":
            return await run_sync(client.cancel_order, symbol=symbol, orderId=order_id)
        return await run_sync(client.cancel_order, symbol=symbol, orderId=order_id)
    except Exception as exc:
        db.log_event("Cancel Order Error", str(exc), "ERROR", chat_id)
        raise


async def get_current_price(symbol: str, futures: bool = True) -> float:
    try:
        if futures:
            client = get_public_futures_client()
            data = await run_sync(client.mark_price, symbol=symbol)
            return float(data.get("markPrice", 0))
        else:
            client = get_public_spot_client()
            data = await run_sync(client.ticker_price, symbol=symbol)
            return float(data.get("price", 0))
    except Exception as exc:
        logger.error("get_current_price %s error: %s", symbol, exc)
        return 0.0


async def set_leverage(chat_id: int, symbol: str, leverage: int) -> dict:
    client = get_client(chat_id)
    user = db.get_user(chat_id)
    if user["account_type"] != "futures":
        return {}
    try:
        return await run_sync(client.change_leverage, symbol=symbol, leverage=leverage)
    except Exception as exc:
        logger.error("set_leverage error: %s", exc)
        raise


async def set_margin_type(chat_id: int, symbol: str, margin_type: str) -> dict:
    """margin_type: 'CROSSED' | 'ISOLATED'"""
    client = get_client(chat_id)
    user = db.get_user(chat_id)
    if user["account_type"] != "futures":
        return {}
    try:
        return await run_sync(client.change_margin_type, symbol=symbol, marginType=margin_type)
    except Exception as exc:
        # Binance returns error if margin type is already set — ignore it
        if "No need to change" in str(exc):
            return {}
        raise


async def get_position_info(chat_id: int, symbol: str) -> dict | None:
    client = get_client(chat_id)
    user = db.get_user(chat_id)
    if user["account_type"] != "futures":
        return None
    try:
        positions = await run_sync(client.get_position_risk, symbol=symbol)
        if positions:
            return positions[0]
        return None
    except Exception as exc:
        logger.error("get_position_info error: %s", exc)
        return None
