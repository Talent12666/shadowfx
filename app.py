from dotenv import load_dotenv
load_dotenv()  # This loads from .env automatically

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import requests
import pandas as pd
import pandas_ta as ta
import os
import time
from threading import Lock
from apscheduler.schedulers.background import BackgroundScheduler

# Initialize environment and app
app = Flask(__name__)

# ======== GLOBAL TRACKING SYSTEM ========
active_trades = {}
trade_history = []
user_alerts = {}
previous_trends = {}
winrate_thresholds = [80, 50, 40]
trade_lock = Lock()
scheduler = BackgroundScheduler()

# ======== TWELVE DATA CONFIGURATION ========
TWELVE_API_KEY = os.getenv('TWELVE_API_KEY')
SYMBOL_MAP = {
    # Forex (Major Pairs)
    "EURUSD": {"symbol": "EUR/USD", "category": "forex"},
    "GBPUSD": {"symbol": "GBP/USD", "category": "forex"},
    "USDJPY": {"symbol": "USD/JPY", "category": "forex"},
    "AUDUSD": {"symbol": "AUD/USD", "category": "forex"},
    "USDCAD": {"symbol": "USD/CAD", "category": "forex"},
    "USDCHF": {"symbol": "USD/CHF", "category": "forex"},
    "NZDUSD": {"symbol": "NZD/USD", "category": "forex"},
    # Commodities
    "XAUUSD": {"symbol": "XAU/USD", "category": "commodities"},
    "XAGUSD": {"symbol": "XAG/USD", "category": "commodities"},
    "XPTUSD": {"symbol": "XPT/USD", "category": "commodities"},
    "XPDUSD": {"symbol": "XPD/USD", "category": "commodities"},
    "CL1": {"symbol": "CL/F", "category": "commodities"},
    "NG1": {"symbol": "NG/F", "category": "commodities"},
    # Indices
    "SPX": {"symbol": "SPX", "category": "indices"},
    "NDX": {"symbol": "NDX", "category": "indices"},
    "DJI": {"symbol": "DJI", "category": "indices"},
    "FTSE": {"symbol": "FTSE", "category": "indices"},
    "DAX": {"symbol": "DAX", "category": "indices"},
    "NIKKEI": {"symbol": "NIKKEI", "category": "indices"},
    # Cryptocurrencies
    "BTCUSD": {"symbol": "BTC/USD", "category": "crypto"},
    "ETHUSD": {"symbol": "ETH/USD", "category": "crypto"},
    "XRPUSD": {"symbol": "XRP/USD", "category": "crypto"},
    "LTCUSD": {"symbol": "LTC/USD", "category": "crypto"},
    # ETFs
    "SPY": {"symbol": "SPY", "category": "etf"},
    "QQQ": {"symbol": "QQQ", "category": "etf"},
    "GLD": {"symbol": "GLD", "category": "etf"},
    # Stocks
    "AAPL": {"symbol": "AAPL", "category": "stocks"},
    "TSLA": {"symbol": "TSLA", "category": "stocks"},
    "AMZN": {"symbol": "AMZN", "category": "stocks"},
    "GOOGL": {"symbol": "GOOGL", "category": "stocks"},
    "MSFT": {"symbol": "MSFT", "category": "stocks"}
}

TIMEFRAMES = {
    'analysis': '15min',
    'sl': '5min',
    'entry': '1min'
}

# ======== MARKET MONITORING SYSTEM ========
def check_market_conditions():
    with trade_lock:
        # Trade monitoring logic
        for trade_id in list(active_trades.keys()):
            trade = active_trades[trade_id]
            df = get_twelve_data(trade['symbol'], '1min')
            if df is None or df.empty:
                continue

            current_price = df['close'].iloc[0]
            direction = trade['direction']
            entry = trade['entry']
            sl = trade['sl']
            tp1 = trade['tp1']
            tp2 = trade['tp2']

            # SL/TP checks
            if (direction == 'BUY' and current_price <= sl) or (direction == 'SELL' and current_price >= sl):
                handle_sl_hit(trade_id)
            elif (direction == 'BUY' and current_price >= tp2) or (direction == 'SELL' and current_price <= tp2):
                handle_tp2_hit(trade_id)
            elif not trade['breakeven'] and ((direction == 'BUY' and current_price >= tp1) or (direction == 'SELL' and current_price <= tp1)):
                move_to_breakeven(trade_id)

        # Trend monitoring
        for symbol, users in user_alerts.items():
            df = get_twelve_data(symbol, TIMEFRAMES['analysis'])
            if df is None:
                continue

            current_trend = determine_trend(df)
            if symbol in previous_trends and previous_trends[symbol] != current_trend:
                notify_trend_change(symbol, current_trend, users)
            previous_trends[symbol] = current_trend

def handle_sl_hit(trade_id):
    trade = active_trades.pop(trade_id)
    trade_history.append(False)

def handle_tp2_hit(trade_id):
    trade = active_trades.pop(trade_id)
    trade_history.append(True)

def move_to_breakeven(trade_id):
    active_trades[trade_id]['sl'] = active_trades[trade_id]['entry']
    active_trades[trade_id]['breakeven'] = True

def notify_trend_change(symbol, new_trend, users):
    message = f"📈 {symbol} Trend Changed: {new_trend}"
    for user in users:
        send_whatsapp_alert(user, message)

def send_whatsapp_alert(user, message):
    # Implement your Twilio alert logic here
    pass

# Start the scheduler before the first request
@app.before_first_request
def start_scheduler():
    scheduler.add_job(check_market_conditions, 'interval', minutes=5)
    scheduler.start()

# ======== CORE FUNCTIONS ========
def convert_symbol(symbol):
    return SYMBOL_MAP.get(symbol.upper(), {"symbol": symbol, "category": None})

def get_twelve_data(symbol, interval='15min'):
    try:
        config = convert_symbol(symbol)
        params = {
            "symbol": config["symbol"],
            "interval": interval,
            "outputsize": 200,
            "apikey": TWELVE_API_KEY
        }
        if config["category"]:
            params["category"] = config["category"]

        response = requests.get("https://api.twelvedata.com/time_series", params=params)
        data = response.json()

        if data.get('status') != 'ok':
            return None

        df = pd.DataFrame(data['values'])
        df = df.rename(columns={
            'datetime': 'time',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close'
        }).sort_values('time', ascending=False)

        return df.set_index('time').astype(float)
    except Exception as e:
        print(f"Data Error ({symbol}): {str(e)}")
        return None

def calculate_winrate(data):
    if len(data) < 100:
        return "N/A"
    changes = data['close'].pct_change().dropna()
    return f"{(len(changes[changes > 0])/len(changes))*100:.1f}%"

def determine_trend(data):
    if len(data) < 50:
        return "N/A"
    data['EMA20'] = ta.ema(data['close'], 20)
    data['EMA50'] = ta.ema(data['close'], 50)
    return "Up trend" if data['EMA20'].iloc[-1] > data['EMA50'].iloc[-1] else "Down trend"

def analyze_price_action(symbol):
    df_15m = get_twelve_data(symbol, TIMEFRAMES['analysis'])
    df_5m = get_twelve_data(symbol, TIMEFRAMES['sl'])
    df_1m = get_twelve_data(symbol, TIMEFRAMES['entry'])

    if df_15m is None or len(df_15m) < 100:
        return None
    if df_5m is None or len(df_5m) < 50:
        return None
    if df_1m is None or len(df_1m) < 10:
        return None

    df_5m['ATR'] = ta.atr(df_5m['high'], df_5m['low'], df_5m['close'], 14)
    df_15m['ATR'] = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], 14)

    support = df_15m['low'].rolling(50).min().iloc[-1]
    resistance = df_15m['high'].rolling(50).max().iloc[-1]
    last_close = df_1m['close'].iloc[0]

    trend = determine_trend(df_15m)

    signal = None
    if last_close > resistance:
        signal = ('BUY', last_close, last_close - (df_5m['ATR'].iloc[-1] * 3))
    elif last_close < support:
        signal = ('SELL', last_close, last_close + (df_5m['ATR'].iloc[-1] * 3))

    if not signal:
        return None

    direction, entry, sl = signal
    tp1 = entry + (df_15m['ATR'].iloc[-1] * 1) if direction == 'BUY' else entry - (df_15m['ATR'].iloc[-1] * 1)
    tp2 = entry + (df_15m['ATR'].iloc[-1] * 2) if direction == 'BUY' else entry - (df_15m['ATR'].iloc[-1] * 2)

    return {
        'symbol': symbol,
        'signal': direction,
        'winrate': calculate_winrate(df_15m),
        'trend': trend,
        'entry': entry,
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2
    }

# ======== WEB ENDPOINTS ========
@app.route("/")
def home():
    return "ShadowFx Trading Bot - Operational"

@app.route("/webhook", methods=["POST"])
def webhook():
    incoming_msg = request.form.get("Body").strip().upper()
    response = MessagingResponse()
    user_number = request.form.get("From")

    if incoming_msg in ["HI", "HELLO", "START"]:
        response.message(
            "📈 ShadowFx Trading Bot 📈\n"
            "Supported Instruments:\n"
            "• Forex: EURUSD, GBPUSD, USDJPY\n"
            "• Commodities: XAUUSD, XAGUSD, CL1, NG1\n"
            "• Indices: SPX, NDX, DJI\n"
            "• Crypto: BTCUSD, ETHUSD\n"
            "• ETFs: SPY\n"
            "• Stocks: AAPL, TSLA\n\n"
            "Commands:\n"
            "➤ Analysis: XAUUSD\n"
            "➤ Price: PRICE BTCUSD\n"
            "➤ Alert: ALERT SPX"
        )
        return str(response)

    if incoming_msg.startswith("PRICE "):
        symbol = incoming_msg.split(" ")[1]
        df = get_twelve_data(symbol, interval='1min')
        price = df['close'].iloc[0] if df is not None else None
        response.message(f"Current {symbol}: {price:.5f}" if price else "❌ Price unavailable")
        return str(response)

    if incoming_msg in SYMBOL_MAP:
        symbol = incoming_msg
        analysis = analyze_price_action(symbol)

        if analysis:
            trade_id = f"{symbol}_{int(time.time())}"
            with trade_lock:
                active_trades[trade_id] = {
                    'symbol': symbol,
                    'direction': analysis['signal'],
                    'entry': analysis['entry'],
                    'sl': analysis['sl'],
                    'tp1': analysis['tp1'],
                    'tp2': analysis['tp2'],
                    'breakeven': False,
                    'user': user_number
                }
            msg = (f"📊 {analysis['symbol']} Analysis\n"
                   f"Signal: {analysis['signal']}\n"
                   f"Winrate: {analysis['winrate']}\n"
                   f"M15 trend: {analysis['trend']}\n"
                   f"Entry: {analysis['entry']:.5f}\n"
                   f"SL: {analysis['sl']:.5f}\n"
                   f"TP1: {analysis['tp1']:.5f}\n"
                   f"TP2: {analysis['tp2']:.5f}")
        else:
            msg = f"No trading opportunity found for {symbol}"

        response.message(msg)
        return str(response)

    elif incoming_msg.startswith("ALERT "):
        symbol = incoming_msg.split(" ")[1]
        if symbol in SYMBOL_MAP:
            if symbol not in user_alerts:
                user_alerts[symbol] = []
            if user_number not in user_alerts[symbol]:
                user_alerts[symbol].append(user_number)
                response.message(f"🔔 Alerts activated for {symbol}")
            else:
                response.message(f"🔔 Already receiving alerts for {symbol}")
        else:
            response.message("❌ Unsupported asset")
        return str(response)

    else:
        response.message("❌ Invalid command. Send 'HI' for help")
    return str(response)

@app.route("/keep-alive")
def keep_alive():
    return "OK"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
