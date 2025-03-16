import os
import time
import json
import asyncio
import logging
from threading import Lock
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
import websockets
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

# ======== DERIV DATA CONFIGURATION ========
# We'll request historical candle data via Deriv's WebSocket API using the "ticks_history" call.
DERIV_WS_URI = "wss://ws.derivws.com/websockets/v3?app_id=1089"

# Mapping from our timeframe strings to granularity (in seconds)
GRANULARITY_MAP = {
    '15min': 900,
    '5min': 300,
    '1min': 60
}

# ======== SYMBOL MAPPING ========
# All supported instruments are mapped to Deriv's symbol format.
SYMBOL_MAP = {
    # Forex
    "EURUSD": {"symbol": "frxEURUSD", "category": "forex"},
    "GBPUSD": {"symbol": "frxGBPUSD", "category": "forex"},
    "USDJPY": {"symbol": "frxUSDJPY", "category": "forex"},
    "AUDUSD": {"symbol": "frxAUDUSD", "category": "forex"},
    "USDCAD": {"symbol": "frxUSDCAD", "category": "forex"},
    "USDCHF": {"symbol": "frxUSDCHF", "category": "forex"},
    "NZDUSD": {"symbol": "frxNZDUSD", "category": "forex"},
    "EURGBP": {"symbol": "frxEURGBP", "category": "forex"},
    "USDSEK": {"symbol": "frxUSDSEK", "category": "forex"},
    "USDNOK": {"symbol": "frxUSDNOK", "category": "forex"},
    "USDTRY": {"symbol": "frxUSDTRY", "category": "forex"},
    "EURJPY": {"symbol": "frxEURJPY", "category": "forex"},
    "GBPJPY": {"symbol": "frxGBPJPY", "category": "forex"},

    # Commodities
    "XAUUSD": {"symbol": "gold", "category": "commodity"},
    "XAGUSD": {"symbol": "silver", "category": "commodity"},
    "XPTUSD": {"symbol": "platinum", "category": "commodity"},
    "XPDUSD": {"symbol": "palladium", "category": "commodity"},
    "CL1":    {"symbol": "crude_oil", "category": "commodity"},
    "NG1":    {"symbol": "natural_gas", "category": "commodity"},
    "CO1":    {"symbol": "coal", "category": "commodity"},
    "HG1":    {"symbol": "copper", "category": "commodity"},

    # Indices
    "SPX":    {"symbol": "indices_us", "category": "index"},
    "NDX":    {"symbol": "indices_nasdaq", "category": "index"},
    "DJI":    {"symbol": "indices_dow_jones", "category": "index"},
    "FTSE":   {"symbol": "indices_ftse", "category": "index"},
    "DAX":    {"symbol": "indices_dax", "category": "index"},
    "NIKKEI": {"symbol": "indices_nikkei", "category": "index"},
    "HSI":    {"symbol": "indices_hsi", "category": "index"},
    "ASX":    {"symbol": "indices_asx", "category": "index"},
    "CAC":    {"symbol": "indices_cac", "category": "index"},

    # Crypto
    "BTCUSD": {"symbol": "cryptoBTCUSD", "category": "crypto"},
    "ETHUSD": {"symbol": "cryptoETHUSD", "category": "crypto"},
    "XRPUSD": {"symbol": "cryptoXRPUSD", "category": "crypto"},
    "LTCUSD": {"symbol": "cryptoLTCUSD", "category": "crypto"},
    "BCHUSD": {"symbol": "cryptoBCHUSD", "category": "crypto"},
    "ADAUSD": {"symbol": "cryptoADAUSD", "category": "crypto"},
    "DOTUSD": {"symbol": "cryptoDOTUSD", "category": "crypto"},
    "SOLUSD": {"symbol": "cryptoSOLUSD", "category": "crypto"},

    # ETFs
    "SPY": {"symbol": "etfSPY", "category": "etf"},
    "QQQ": {"symbol": "etfQQQ", "category": "etf"},
    "GLD": {"symbol": "etfGLD", "category": "etf"},
    "XLF": {"symbol": "etfXLF", "category": "etf"},
    "IWM": {"symbol": "etfIWM", "category": "etf"},
    "EEM": {"symbol": "etfEEM", "category": "etf"},

    # Stocks
    "AAPL":  {"symbol": "stockAAPL", "category": "stock"},
    "TSLA":  {"symbol": "stockTSLA", "category": "stock"},
    "AMZN":  {"symbol": "stockAMZN", "category": "stock"},
    "GOOGL": {"symbol": "stockGOOGL", "category": "stock"},
    "MSFT":  {"symbol": "stockMSFT", "category": "stock"},
    "META":  {"symbol": "stockMETA", "category": "stock"},
    "NVDA":  {"symbol": "stockNVDA", "category": "stock"},
    "NFLX":  {"symbol": "stockNFLX", "category": "stock"},

    # Synthetics - Boom
    "BOOM1000": {"symbol": "boom1000", "category": "synthetic"},
    "BOOM300":  {"symbol": "boom300",  "category": "synthetic"},
    "BOOM500":  {"symbol": "boom500",  "category": "synthetic"},
    "BOOM600":  {"symbol": "boom600",  "category": "synthetic"},
    "BOOM900":  {"symbol": "boom900",  "category": "synthetic"},

    # Synthetics - Crash
    "CRASH1000": {"symbol": "crash1000", "category": "synthetic"},
    "CRASH300":  {"symbol": "crash300",  "category": "synthetic"},
    "CRASH500":  {"symbol": "crash500",  "category": "synthetic"},
    "CRASH600":  {"symbol": "crash600",  "category": "synthetic"},
    "CRASH900":  {"symbol": "crash900",  "category": "synthetic"},

    # Synthetics - Volatility
    "VOLATILITY100":  {"symbol": "volatility100",  "category": "synthetic"},
    "VOLATILITY75":   {"symbol": "volatility75",   "category": "synthetic"},
    "VOLATILITY50":   {"symbol": "volatility50",   "category": "synthetic"},
    "VOLATILITY10":   {"symbol": "volatility10",   "category": "synthetic"},
    "VOLATILITY25":   {"symbol": "volatility25",   "category": "synthetic"},
    "VOLATILITY75S":  {"symbol": "volatility75s",  "category": "synthetic"},
    "VOLATILITY50S":  {"symbol": "volatility50s",  "category": "synthetic"},
    "VOLATILITY150S": {"symbol": "volatility150s", "category": "synthetic"},
    "VOLATILITY250S": {"symbol": "volatility250s", "category": "synthetic"},

    # Synthetics - Jumps
    "JUMPS": {"symbol": "jumps", "category": "synthetic"}
}

TIMEFRAMES = {
    'analysis': '15min',
    'sl': '5min',
    'entry': '1min'
}

# ======== RISK MANAGEMENT CONFIG ========
# (Position size is calculated internally and not sent in responses.)
DEFAULT_ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", 10000))
RISK_PERCENTAGE = float(os.getenv("RISK_PERCENTAGE", 1))  # 1%

def calculate_position_size(account_balance, risk_percentage, stop_loss_distance):
    risk_amount = account_balance * (risk_percentage / 100)
    if stop_loss_distance == 0:
        return 0
    position_size = risk_amount / stop_loss_distance
    return position_size

# ======== DERIV WEBSOCKET DATA FUNCTIONALITY ========
async def async_get_deriv_data(symbol, interval='15min'):
    granularity = GRANULARITY_MAP.get(interval, 60)
    request_payload = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": 200,
        "end": "latest",
        "granularity": granularity
    }
    try:
        async with websockets.connect(DERIV_WS_URI) as websocket:
            await websocket.send(json.dumps(request_payload))
            response = await websocket.recv()
            data = json.loads(response)
            if "candles" in data:
                candles = data["candles"]
                df = pd.DataFrame(candles)
                df['time'] = pd.to_datetime(df['epoch'], unit='s')
                df = df[['time', 'open', 'high', 'low', 'close']]
                df = df.sort_values('time', ascending=False)
                df = df.set_index('time')
                df = df.astype(float)
                return df
            else:
                logger.error(f"Deriv API error for {symbol}: {data.get('error', 'Unknown error')}")
                return None
    except Exception as e:
        logger.error(f"Error connecting to Deriv WebSocket for {symbol}: {str(e)}")
        return None

def get_deriv_data(symbol, interval='15min'):
    try:
        config = convert_symbol(symbol)
        return asyncio.run(async_get_deriv_data(config["symbol"], interval))
    except Exception as e:
        logger.error(f"Data Error ({symbol}): {str(e)}")
        return None

# ======== MARKET MONITORING SYSTEM ========
def check_market_conditions():
    with trade_lock:
        try:
            for trade_id in list(active_trades.keys()):
                trade = active_trades[trade_id]
                df = get_deriv_data(trade['symbol'], '1min')
                if df is None or df.empty:
                    continue
                current_price = df['close'].iloc[0]
                direction = trade['direction']
                entry = trade['entry']
                sl = trade['sl']
                tp1 = trade['tp1']
                tp2 = trade['tp2']
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
    # Implement your Twilio alert logic here.
    logger.info(f"Sending alert to {user}: {message}")
    pass

scheduler.add_job(check_market_conditions, 'interval', minutes=1)

# ======== CORE FUNCTIONS ========
def convert_symbol(symbol):
    return SYMBOL_MAP.get(symbol.upper(), {"symbol": symbol, "category": None})

def calculate_winrate(data):
    if len(data) < 100:
        return "N/A"
    df_sorted = data.sort_index(ascending=True)
    changes = df_sorted['close'].pct_change().dropna()
    return f"{(len(changes[changes > 0]) / len(changes)) * 100:.1f}%"

# ======== NEW STRATEGY: PURE PRICE ACTION ========
def analyze_price_action(symbol):
    df_15m = get_deriv_data(symbol, TIMEFRAMES['analysis'])
    df_5m = get_deriv_data(symbol, TIMEFRAMES['sl'])
    df_1m = get_deriv_data(symbol, TIMEFRAMES['entry'])
    if df_15m is None or len(df_15m) < 2:
        return None
    if df_5m is None or df_5m.empty:
        return None
    if df_1m is None or df_1m.empty:
        return None
    last_close = df_15m['close'].iloc[0]
    prev_high = df_15m['high'].iloc[1]
    prev_low = df_15m['low'].iloc[1]
    signal = None
    if last_close > prev_high:
        entry = last_close
        sl = prev_low
        risk = entry - sl
        tp1 = entry + 2 * risk
        tp2 = entry + 4 * risk
        signal = ('BUY', entry, sl, tp1, tp2)
    elif last_close < prev_low:
        entry = last_close
        sl = prev_high
        risk = sl - entry
        tp1 = entry - 2 * risk
        tp2 = entry - 4 * risk
        signal = ('SELL', entry, sl, tp1, tp2)
    if not signal:
        return None
    stop_loss_distance = abs(signal[1] - signal[2])
    position_size = calculate_position_size(DEFAULT_ACCOUNT_BALANCE, RISK_PERCENTAGE, stop_loss_distance)
    return {
        'symbol': symbol,
        'signal': signal[0],
        'winrate': calculate_winrate(df_15m),
        'entry': signal[1],
        'sl': signal[2],
        'tp1': signal[3],
        'tp2': signal[4],
        'position_size': position_size  # stored internally but not sent in the response
    }

# ======== BACKTESTING MODULE ========
def backtest_price_action(symbol):
    df = get_deriv_data(symbol, '15min')
    if df is None or len(df) < 2:
        return None
    df_sorted = df.sort_index(ascending=True)
    trades = []
    for i in range(1, len(df_sorted)):
        prev = df_sorted.iloc[i - 1]
        current = df_sorted.iloc[i]
        if current['close'] > prev['high']:
            entry = current['close']
            sl = prev['low']
            risk = entry - sl
            tp1 = entry + 2 * risk
            win = current['close'] >= tp1
            trades.append(win)
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
    return (
        "ShadowFx Trading Bot - Operational\n"
        "Supported Instruments:\n"
        "• Forex: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURGBP, USDSEK, USDNOK, USDTRY, EURJPY, GBPJPY\n"
        "• Commodities: XAUUSD, XAGUSD, XPTUSD, XPDUSD, CL1, NG1, CO1, HG1\n"
        "• Indices: SPX, NDX, DJI, FTSE, DAX, NIKKEI, HSI, ASX, CAC\n"
        "• Crypto: BTCUSD, ETHUSD, XRPUSD, LTCUSD, BCHUSD, ADAUSD, DOTUSD, SOLUSD\n"
        "• ETFs: SPY, QQQ, GLD, XLF, IWM, EEM\n"
        "• Stocks: AAPL, TSLA, AMZN, GOOGL, MSFT, META, NVDA, NFLX\n"
        "• Synthetics: BOOM1000, BOOM300, BOOM500, BOOM600, BOOM900, CRASH1000, CRASH300, CRASH500, CRASH600, CRASH900, VOLATILITY100, VOLATILITY75, VOLATILITY50, VOLATILITY10, VOLATILITY25, VOLATILITY75S, VOLATILITY50S, VOLATILITY150S, VOLATILITY250S, JUMPS\n\n"
        "Commands:\n"
        "➤ Analysis: XAUUSD\n"
        "➤ Price: PRICE BTCUSD\n"
        "➤ Alert: ALERT SPX"
    )

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        incoming_msg = request.form.get("Body", "").strip().upper()
        response = MessagingResponse()
        user_number = request.form.get("From")
        if incoming_msg in ["HI", "HELLO", "START"]:
            response.message(
                "📈 ShadowFx Trading Bot 📈\n"
                "Supported Instruments:\n"
                "• Forex: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURGBP, USDSEK, USDNOK, USDTRY, EURJPY, GBPJPY\n"
                "• Commodities: XAUUSD, XAGUSD, XPTUSD, XPDUSD, CL1, NG1, CO1, HG1\n"
                "• Indices: SPX, NDX, DJI, FTSE, DAX, NIKKEI, HSI, ASX, CAC\n"
                "• Crypto: BTCUSD, ETHUSD, XRPUSD, LTCUSD, BCHUSD, ADAUSD, DOTUSD, SOLUSD\n"
                "• ETFs: SPY, QQQ, GLD, XLF, IWM, EEM\n"
                "• Stocks: AAPL, TSLA, AMZN, GOOGL, MSFT, META, NVDA, NFLX\n"
                "• Synthetics: BOOM1000, BOOM300, BOOM500, BOOM600, BOOM900, CRASH1000, CRASH300, CRASH500, CRASH600, CRASH900, VOLATILITY100, VOLATILITY75, VOLATILITY50, VOLATILITY10, VOLATILITY25, VOLATILITY75S, VOLATILITY50S, VOLATILITY150S, VOLATILITY250S, JUMPS\n\n"
                "Commands:\n"
                "➤ Analysis: XAUUSD\n"
                "➤ Price: PRICE BTCUSD\n"
                "➤ Alert: ALERT SPX"
            )
            return str(response)
        if incoming_msg.startswith("PRICE "):
            symbol = incoming_msg.split(" ")[1]
            df = get_deriv_data(symbol, interval='1min')
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

if __name__ == "__main__":
    try:
        scheduler.start()
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port)
    except Exception as e:
        logger.error(f"Error starting application: {str(e)}")
