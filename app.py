import os
import time
import logging
from threading import Lock
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
import requests
import pandas as pd
import pandas_ta as ta
from apscheduler.schedulers.background import BackgroundScheduler

# Load environment variables
load_dotenv()

# Setup logging for improved system robustness
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# ======== GLOBAL TRACKING SYSTEM ========
active_trades = {}
trade_history = []
user_alerts = {}
previous_trends = {}
trade_lock = Lock()
scheduler = BackgroundScheduler()

# ======== TWELVE DATA CONFIGURATION ========
TWELVE_API_KEY = os.getenv('TWELVE_API_KEY')

# Map all supported instruments
SYMBOL_MAP = {
    # Forex
    "EURUSD": {"symbol": "EUR/USD", "category": "forex"},
    "GBPUSD": {"symbol": "GBP/USD", "category": "forex"},
    "USDJPY": {"symbol": "USD/JPY", "category": "forex"},
    "AUDUSD": {"symbol": "AUD/USD", "category": "forex"},
    "USDCAD": {"symbol": "USD/CAD", "category": "forex"},
    "USDCHF": {"symbol": "USD/CHF", "category": "forex"},
    "NZDUSD": {"symbol": "NZD/USD", "category": "forex"},
    "EURGBP": {"symbol": "EUR/GBP", "category": "forex"},
    "USDSEK": {"symbol": "USD/SEK", "category": "forex"},
    "USDNOK": {"symbol": "USD/NOK", "category": "forex"},
    "USDTRY": {"symbol": "USD/TRY", "category": "forex"},
    "EURJPY": {"symbol": "EUR/JPY", "category": "forex"},
    "GBPJPY": {"symbol": "GBP/JPY", "category": "forex"},

    # Commodities
    "XAUUSD": {"symbol": "XAU/USD", "category": "commodity"},
    "XAGUSD": {"symbol": "XAG/USD", "category": "commodity"},
    "XPTUSD": {"symbol": "XPT/USD", "category": "commodity"},
    "XPDUSD": {"symbol": "XPD/USD", "category": "commodity"},
    "CL1":    {"symbol": "CL1", "category": "commodity"},
    "NG1":    {"symbol": "NG1", "category": "commodity"},
    "CO1":    {"symbol": "CO1", "category": "commodity"},
    "HG1":    {"symbol": "HG1", "category": "commodity"},

    # Indices
    "SPX":    {"symbol": "SPX", "category": "index"},
    "NDX":    {"symbol": "NDX", "category": "index"},
    "DJI":    {"symbol": "DJI", "category": "index"},
    "FTSE":   {"symbol": "FTSE", "category": "index"},
    "DAX":    {"symbol": "DAX", "category": "index"},
    "NIKKEI": {"symbol": "NIKKEI", "category": "index"},
    "HSI":    {"symbol": "HSI", "category": "index"},
    "ASX":    {"symbol": "ASX", "category": "index"},
    "CAC":    {"symbol": "CAC", "category": "index"},

    # Crypto
    "BTCUSD": {"symbol": "BTC/USD", "category": "crypto"},
    "ETHUSD": {"symbol": "ETH/USD", "category": "crypto"},
    "XRPUSD": {"symbol": "XRP/USD", "category": "crypto"},
    "LTCUSD": {"symbol": "LTC/USD", "category": "crypto"},
    "BCHUSD": {"symbol": "BCH/USD", "category": "crypto"},
    "ADAUSD": {"symbol": "ADA/USD", "category": "crypto"},
    "DOTUSD": {"symbol": "DOT/USD", "category": "crypto"},
    "SOLUSD": {"symbol": "SOL/USD", "category": "crypto"},

    # ETFs
    "SPY": {"symbol": "SPY", "category": "etf"},
    "QQQ": {"symbol": "QQQ", "category": "etf"},
    "GLD": {"symbol": "GLD", "category": "etf"},
    "XLF": {"symbol": "XLF", "category": "etf"},
    "IWM": {"symbol": "IWM", "category": "etf"},
    "EEM": {"symbol": "EEM", "category": "etf"},

    # Stocks
    "AAPL":  {"symbol": "AAPL", "category": "stock"},
    "TSLA":  {"symbol": "TSLA", "category": "stock"},
    "AMZN":  {"symbol": "AMZN", "category": "stock"},
    "GOOGL": {"symbol": "GOOGL", "category": "stock"},
    "MSFT":  {"symbol": "MSFT", "category": "stock"},
    "META":  {"symbol": "META", "category": "stock"},
    "NVDA":  {"symbol": "NVDA", "category": "stock"},
    "NFLX":  {"symbol": "NFLX", "category": "stock"}
}

TIMEFRAMES = {
    'analysis': '15min',
    'sl': '5min',
    'entry': '1min'
}

# ======== RISK MANAGEMENT CONFIG ========
DEFAULT_ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", 10000))
RISK_PERCENTAGE = float(os.getenv("RISK_PERCENTAGE", 1))  # 1%

def calculate_position_size(account_balance, risk_percentage, stop_loss_distance):
    risk_amount = account_balance * (risk_percentage / 100)
    if stop_loss_distance == 0:
        return 0
    position_size = risk_amount / stop_loss_distance
    return position_size

# ======== MARKET MONITORING SYSTEM ========
def check_market_conditions():
    with trade_lock:
        try:
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

                # SL/TP checks for pure price action trades
                if (direction == 'BUY' and current_price <= sl) or (direction == 'SELL' and current_price >= sl):
                    logger.info(f"SL hit for trade {trade_id}")
                    handle_sl_hit(trade_id)
                elif (direction == 'BUY' and current_price >= tp2) or (direction == 'SELL' and current_price <= tp2):
                    logger.info(f"TP2 hit for trade {trade_id}")
                    handle_tp2_hit(trade_id)
                elif not trade.get('breakeven') and ((direction == 'BUY' and current_price >= tp1) or (direction == 'SELL' and current_price <= tp1)):
                    logger.info(f"Moving trade {trade_id} to breakeven")
                    move_to_breakeven(trade_id)
        except Exception as e:
            logger.error(f"Error in market conditions check: {str(e)}")

def handle_sl_hit(trade_id):
    with trade_lock:
        trade = active_trades.pop(trade_id, None)
        if trade is not None:
            trade_history.append(False)

def handle_tp2_hit(trade_id):
    with trade_lock:
        trade = active_trades.pop(trade_id, None)
        if trade is not None:
            trade_history.append(True)

def move_to_breakeven(trade_id):
    with trade_lock:
        if trade_id in active_trades:
            active_trades[trade_id]['sl'] = active_trades[trade_id]['entry']
            active_trades[trade_id]['breakeven'] = True

def notify_trend_change(symbol, new_trend, users):
    message = f"📈 {symbol} Trend Changed: {new_trend}"
    for user in users:
        send_whatsapp_alert(user, message)

def send_whatsapp_alert(user, message):
    # Implement your Twilio alert logic here
    logger.info(f"Sending alert to {user}: {message}")
    pass

# Schedule the market monitoring to run every minute
scheduler.add_job(check_market_conditions, 'interval', minutes=1)

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

        response = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=10)
        data = response.json()

        if data.get('status') != 'ok':
            logger.error(f"Twelve Data API error for {symbol}: {data.get('message')}")
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
        logger.error(f"Data Error ({symbol}): {str(e)}")
        return None

def calculate_winrate(data):
    if len(data) < 100:
        return "N/A"
    df_sorted = data.sort_index(ascending=True)
    changes = df_sorted['close'].pct_change().dropna()
    return f"{(len(changes[changes > 0]) / len(changes)) * 100:.1f}%"

# ======== NEW STRATEGY: PURE PRICE ACTION ========
def analyze_price_action(symbol):
    df_15m = get_twelve_data(symbol, TIMEFRAMES['analysis'])
    df_5m = get_twelve_data(symbol, TIMEFRAMES['sl'])
    df_1m = get_twelve_data(symbol, TIMEFRAMES['entry'])

    if df_15m is None or len(df_15m) < 2:
        return None
    if df_5m is None or df_5m.empty:
        return None
    if df_1m is None or df_1m.empty:
        return None

    # Pure Price Action: Breakout of previous candle's range
    last_close = df_15m['close'].iloc[0]
    prev_high = df_15m['high'].iloc[1]
    prev_low = df_15m['low'].iloc[1]

    signal = None
    if last_close > prev_high:
        # Bullish breakout
        entry = last_close
        sl = prev_low  # using previous low as stop loss
        risk = entry - sl
        tp1 = entry + 2 * risk
        tp2 = entry + 4 * risk
        signal = ('BUY', entry, sl, tp1, tp2)
    elif last_close < prev_low:
        # Bearish breakout
        entry = last_close
        sl = prev_high  # using previous high as stop loss
        risk = sl - entry
        tp1 = entry - 2 * risk
        tp2 = entry - 4 * risk
        signal = ('SELL', entry, sl, tp1, tp2)

    if not signal:
        return None

    # Calculate position size based on risk management (kept internally, not sent to user)
    stop_loss_distance = abs(signal[1] - signal[2])
    position_size = calculate_position_size(DEFAULT_ACCOUNT_BALANCE, RISK_PERCENTAGE, stop_loss_distance)

    return {
        'symbol': symbol,
        'signal': signal[0],
        'winrate': calculate_winrate(df_15m),
        'strategy': "Pure Price Action",
        'entry': signal[1],
        'sl': signal[2],
        'tp1': signal[3],
        'tp2': signal[4],
        'position_size': position_size
    }

# ======== BACKTESTING MODULE ========
def backtest_price_action(symbol):
    df = get_twelve_data(symbol, '15min')
    if df is None or len(df) < 2:
        return None
    df_sorted = df.sort_index(ascending=True)
    trades = []
    for i in range(1, len(df_sorted)):
        prev = df_sorted.iloc[i - 1]
        current = df_sorted.iloc[i]
        # Bullish breakout signal
        if current['close'] > prev['high']:
            entry = current['close']
            sl = prev['low']
            risk = entry - sl
            tp1 = entry + 2 * risk
            win = current['close'] >= tp1
            trades.append(win)
        # Bearish breakout signal
        elif current['close'] < prev['low']:
            entry = current['close']
            sl = prev['high']
            risk = sl - entry
            tp1 = entry - 2 * risk
            win = current['close'] <= tp1
            trades.append(win)
    total_trades = len(trades)
    if total_trades == 0:
        return {"win_rate": "N/A", "total_trades": 0}
    win_rate = (sum(trades) / total_trades) * 100
    return {"win_rate": f"{win_rate:.1f}%", "total_trades": total_trades}

# ======== WEB ENDPOINTS ========
@app.route("/")
def home():
    return "ShadowFx Trading Bot - Operational"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        incoming_msg = request.form.get("Body", "").strip().upper()
        response = MessagingResponse()
        user_number = request.form.get("From")

        if incoming_msg in ["HI", "HELLO", "START"]:
            response.message(
                "📈 Space_Zero 2.0 Trading Bot 📈\n"
                "Supported Instruments:\n"
                "• Forex: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURGBP, USDSEK, USDNOK, USDTRY, EURJPY, GBPJPY\n"
                "• Commodities: XAUUSD, XAGUSD, XPTUSD, XPDUSD, CL1, NG1, CO1, HG1\n"
                "• Indices: SPX, NDX, DJI, FTSE, DAX, NIKKEI, HSI, ASX, CAC\n"
                "• Crypto: BTCUSD, ETHUSD, XRPUSD, LTCUSD, BCHUSD, ADAUSD, DOTUSD, SOLUSD\n"
                "• ETFs: SPY, QQQ, GLD, XLF, IWM, EEM\n"
                "• Stocks: AAPL, TSLA, AMZN, GOOGL, MSFT, META, NVDA, NFLX\n\n"
                "Commands:\n"
                "➤ Analysis: XAUUSD\n"
                "➤ Price: PRICE BTCUSD\n"
                "➤ Alert: ALERT SPX"
            )
            return str(response)

        if incoming_msg.startswith("PRICE "):
            symbol = incoming_msg.split(" ")[1]
            df = get_twelve_data(symbol, interval='1min')
            if df is not None and not df.empty:
                price = df['close'].iloc[0]
                response.message(f"Current {symbol}: {price:.5f}")
            else:
                response.message("❌ Price unavailable")
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
                        'position_size': analysis['position_size'],
                        'breakeven': False,
                        'user': user_number
                    }

                msg = (f"📊 {analysis['symbol']} Analysis\n"
                       f"Signal: {analysis['signal']}\n"
                       f"Winrate: {analysis['winrate']}\n"
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
    except Exception as e:
        logger.error(f"Error in webhook: {str(e)}")
        return "Error processing request", 500

@app.route("/backtest", methods=["GET"])
def backtest():
    symbol = request.args.get("symbol", "").upper()
    if not symbol:
        return jsonify({"error": "Symbol parameter is required"}), 400
    result = backtest_price_action(symbol)
    if result is None:
        return jsonify({"error": "Insufficient data for backtesting"}), 400
    return jsonify({"symbol": symbol, "backtest": result})

@app.route("/keep-alive")
def keep_alive():
    return "OK"

# ======== ENTRY POINT ========
if __name__ == "__main__":
    try:
        scheduler.start()
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port)
    except Exception as e:
        logger.error(f"Error starting application: {str(e)}")
