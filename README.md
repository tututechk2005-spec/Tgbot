# 🤖 Telegram Binance Trading Bot

A production-ready, fully-featured Telegram bot for Binance trading with AI-powered market scanning, signal generation, adaptive learning, and complete trade management.

---

## ✨ Features

| Feature | Description |
|---|---|
| 📊 Live Dashboard | Real-time wallet balance, margin, equity, PnL |
| 📈 Spot & Futures | Full USDT-M Futures and Spot support |
| 🧪 Testnet + Real | Switch between testnet and live Binance |
| 🤖 AI Market Scanner | Scans all USDT pairs every 1s or 10s |
| 🎯 AI Signals | 15+ indicators, ≥90% confidence, 1:2–1:3 RR |
| 📉 Trade Manager | Open, close, partial, trailing stop, reverse |
| 📡 Position Monitor | Auto-closes on trend reversal / structure break |
| 🧠 Adaptive Learning | Adjusts strategy weights from trade outcomes |
| 👑 Admin Panel | Users, logs, stats, broadcast, system status |
| 📣 Broadcast | Send text/photo/video to all users with progress |
| 🔒 Security | AES-256 encrypted API keys, rate limiting |

---

## 🚀 Railway Deployment (Recommended)

### Step 1 — Upload to GitHub

1. Create a new GitHub repository
2. Upload all project files to the root of the repository
3. **Do NOT commit `.env` or `database.db`** (already in `.gitignore`)

### Step 2 — Deploy on Railway

1. Go to [railway.app](https://railway.app) and create a new project
2. Click **"Deploy from GitHub repo"** and select your repository
3. Railway will auto-detect the `railway.toml` and start the build

### Step 3 — Set Environment Variables

In Railway → your service → **Variables**, add:

```
BOT_TOKEN=your_telegram_bot_token_here
ADMIN_CHAT_ID=123456789
```

> Get your bot token from [@BotFather](https://t.me/BotFather)  
> Get your chat ID from [@userinfobot](https://t.me/userinfobot)

### Step 4 — Start

Railway will automatically deploy and start the bot. The database is created automatically on first run.

---

## 🛠 Local Development

```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables
cp .env.example .env
# Edit .env with your BOT_TOKEN and ADMIN_CHAT_ID

# 4. Run
python main.py
```

---

## 📁 Project Structure

```
├── main.py              # Entry point, handler registration, background tasks
├── config.py            # Environment variables and constants
├── database.py          # SQLite schema + all DB helpers
├── security.py          # AES-256 encryption for API keys
├── binance_client.py    # Async Binance REST client (Spot + Futures)
├── analysis.py          # 15+ technical indicators (no TA-Lib)
├── scanner.py           # Background market scanner
├── signals.py           # Signal engine (90%+ confidence)
├── adaptive_learning.py # Trade outcome learning + weight adjustment
├── trade_manager.py     # All trade execution operations
├── position_monitor.py  # Continuous position health monitoring
├── keyboards.py         # All InlineKeyboard layouts
├── bot_handlers.py      # User-facing Telegram handlers
├── admin_handlers.py    # Admin panel handlers
├── broadcast.py         # Multi-media broadcast system
├── requirements.txt     # Python dependencies
├── railway.toml         # Railway deployment config
├── Procfile             # Process definition
└── .env.example         # Example environment file
```

---

## 🔐 Security

- API keys are encrypted with **Fernet (AES-256)** before storage
- Encryption key is generated on first run and stored in SQLite
- Admin-only commands enforce `ADMIN_CHAT_ID` check
- Rate limiting: 0.5s minimum between commands per user
- Auto-reconnect on Binance network errors
- Detailed error logging in DB and console

---

## 📊 Indicators Used

EMA (9/21/50/200) · VWAP · MACD · RSI · ADX · ATR · SuperTrend · Ichimoku · Bollinger Bands · Volume Spike · OBV · Support/Resistance · Order Blocks · Fair Value Gaps · Market Structure (CHOCH/BOS) · Candlestick Patterns · Liquidity Zones

---

## ⚠️ Disclaimer

This bot is for **educational purposes only**. Cryptocurrency trading involves significant financial risk. Always trade responsibly and never risk more than you can afford to lose. Past performance does not guarantee future results.

---

## 🔄 Environment Variables

| Variable | Description | Required |
|---|---|---|
| `BOT_TOKEN` | Telegram bot token from @BotFather | ✅ |
| `ADMIN_CHAT_ID` | Your Telegram user ID | ✅ |

All other configuration is stored in `database.db` automatically.
