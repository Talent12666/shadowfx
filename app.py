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

# Setup logging
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
DERIV_WS_URI = "wss://ws.derivws.com/websockets/v3?app_id=1089"  # Replace with your app_id if needed

GRANULARITY_MAP = {
    '15min': 900,
    '1min': 60
}

# ======== SYMBOL MAPPING ========
SYMBOL_MAP = {
    # Forex (frx pairs)
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
    # Previously we used WLDAUD for gold vs AUD;
    # To get gold vs USD we now use "XAUUSD" and set its category to synthetic so the active symbol check is skipped.
    "XAUUSD": {"symbol": "XAUUSD", "category": "synthetic"},
    "XAGUSD": {"symbol": "SILVER", "category": "commodity"},   # Silver
    "CL1":    {"symbol": "CL_BRENT", "category": "commodity"},  # Brent Crude Oil
    "NG1":    {"symbol": "NG_HEN", "category": "commodity"},    # Natural Gas
    "CO1":    {"symbol": "COAL", "category": "commodity"},      # Coal
    "HG1":    {"symbol": "XCUUSD", "category": "commodity"},    # Copper

    # Indices
    "SPX":    {"symbol": "SPX500", "category": "index"},        # S&P 500
    "NDX":    {"symbol": "NAS100", "category": "index"},        # NASDAQ 100
    "DJI":    {"symbol": "DJ30", "category": "index"},          # Dow Jones 30
    "FTSE":   {"symbol": "FTSE100", "category": "index"},       # FTSE 100
    "DAX":    {"symbol": "DAX40", "category": "index"},         # DAX 40
    "NIKKEI": {"symbol": "JP225", "category": "index"},          # Nikkei 225
    "HSI":    {"symbol": "HK50", "category": "index"},           # Hang Seng 50
    "ASX":    {"symbol": "AUS200", "category": "index"},         # Australia 200
    "CAC":    {"symbol": "FRA40", "category": "index"},          # France 40

    # Cryptocurrencies
    # Reverting to the mapping that previously worked: no prefix.
    "BTCUSD": {"symbol": "BTCUSD", "category": "crypto"},
    "ETHUSD": {"symbol": "ETHUSD", "category": "crypto"},
    "XRPUSD": {"symbol": "XRPUSD", "category": "crypto"},
    "LTCUSD": {"symbol": "LTCUSD", "category": "crypto"},
    "BCHUSD": {"symbol": "BCHUSD", "category": "crypto"},
    "ADAUSD": {"symbol": "ADAUSD", "category": "crypto"},
    "DOTUSD": {"symbol": "DOTUSD", "category": "crypto"},
    "SOLUSD": {"symbol": "SOLUSD", "category": "crypto"},

    # ETFs
    "SPY": {"symbol": "ETF_SPY", "category": "etf"},
    "QQQ": {"symbol": "ETF_QQQ", "category": "etf"},
    "GLD": {"symbol": "ETF_GLD", "category": "etf"},
    "XLF": {"symbol": "ETF_XLF", "category": "etf"},
    "IWM": {"symbol": "ETF_IWM", "category": "etf"},
    "EEM": {"symbol": "ETF_EEM", "category": "etf"},

    # Stocks
    "AAPL":  {"symbol": "stocksAAPL.us", "category": "stock"},
    "TSLA":  {"symbol": "stocksTSLA.us", "category": "stock"},
    "AMZN":  {"symbol": "stocksAMZN.us", "category": "stock"},
    "GOOGL": {"symbol": "stocksGOOGL.us", "category": "stock"},
    "MSFT":  {"symbol": "stocksMSFT.us", "category": "stock"},
    "META":  {"symbol": "stocksMETA.us", "category": "stock"},
    "NVDA":  {"symbol": "stocksNVDA.us", "category": "stock"},
    "NFLX":  {"symbol": "stocksNFLX.us", "category": "stock"},

    # Synthetics - Boom indices
    "BOOM1000": {"symbol": "BOOM1000", "category": "synthetic"},
    "BOOM300":  {"symbol": "BOOM300",  "category": "synthetic"},
    "BOOM500":  {"symbol": "BOOM500",  "category": "synthetic"},
    "BOOM600":  {"symbol": "BOOM600",  "category": "synthetic"},
    "BOOM900":  {"symbol": "BOOM900",  "category": "synthetic"},

    # Synthetics - Crash indices
    "CRASH1000": {"symbol": "CRASH1000", "category": "synthetic"},
    "CRASH300":  {"symbol": "CRASH300", "category": "synthetic"},
    "CRASH500":  {"symbol": "CRASH500", "category": "synthetic"},
    "CRASH600":  {"symbol": "CRASH600", "category": "synthetic"},
    "CRASH900":  {"symbol": "CRASH900", "category": "synthetic"},

    # Synthetics - Volatility (Grouped)
    "VOLATILITY": {
         "STANDARD": {
             "10": "1HZ10V",
             "25": "1HZ25V",
             "50": "1HZ50V",
             "75": "1HZ75SV",
             "100": "1HZ100V",
             "150": "1HZ150V"
         },
         "SHORT": {
             "10": "1HZ10SV",
             "25": "1HZ25SV",
             "50": "1HZ50SV",
             "75": "1HZ75V",
             "150": "1HZ150SV",
             "250": "1HZ250SV"
         }
    },

    # Synthetics - Jump Indices (Grouped)
    "JUMP": {
         "10": "JD10",
         "25": "JD25",
         "50": "JD50",
         "75": "JD75",
         "100": "JD100"
    }
}

TIMEFRAMES = {
    'analysis': '15min',
    'entry': '1min'
}

# ======== RISK MANAGEMENT CONFIG ========
DEFAULT_ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", 10000))
RISK_PERCENTAGE = float(os.getenv("RISK_PERCENTAGE", 1))  # 1%

def calculate_position_size(account_balance, risk_percentage, stop_loss_distance):
    risk_amount = account_balance * (risk_percentage / 100)
    if stop_loss_distance == 0:
        return 0
    return risk_amount / stop_loss_distance

# ======== DERIV WEBSOCKET DATA FUNCTIONALITY ========
async def async_get_deriv_data(symbol, interval='15min'):
    granularity = GRANULARITY_MAP.get(interval, 60)
    request_payload = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": 200,
        "end": "latest",
        "granularity": granularity,
        "style": "candles"
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

async def async_get_active_symbols():
    request_payload = {"active_symbols": "full"}
    try:
        async with websockets.connect(DERIV_WS_URI) as websocket:
            await websocket.send(json.dumps(request_payload))
            response = await websocket.recv()
            data = json.loads(response)
            if "active_symbols" in data:
                return data["active_symbols"]
            else:
                logger.error(f"Error getting active symbols: {data.get('error', 'Unknown error')}")
                return []
    except Exception as e:
        logger.error(f"Error connecting to get active symbols: {str(e)}")
        return []

def get_active_symbols():
    try:
        return asyncio.run(async_get_active_symbols())
    except Exception as e:
        logger.error(f"Error in get_active_symbols: {str(e)}")
        return []

def get_deriv_data(symbol, interval='15min'):
    try:
        config = convert_symbol(symbol)
        # For crypto, synthetic and commodities (if mapped as synthetic), skip active-symbol check.
        if config["category"] not in ["crypto", "synthetic"]:
            active = get_active_symbols()
            active_symbols = [item["symbol"] for item in active]
            if config["symbol"] not in active_symbols:
                logger.error(f"Symbol {config['symbol']} not active.")
                return None
        return asyncio.run(async_get_deriv_data(config["symbol"], interval))
    except Exception as e:
        logger.error(f"Data Error ({symbol}): {str(e)}")
        return None

# Custom convert_symbol to handle nested volatility and jump mappings
def convert_symbol(symbol):
    symbol = symbol.upper()
    if symbol.startswith("VOLATILITY"):
        remainder = symbol[len("VOLATILITY"):]
        if remainder.endswith("S"):
            key = remainder[:-1]
            code = SYMBOL_MAP["VOLATILITY"]["SHORT"].get(key)
        else:
            code = SYMBOL_MAP["VOLATILITY"]["STANDARD"].get(remainder)
        if code:
            return {"symbol": code, "category": "synthetic"}
        else:
            return {"symbol": symbol, "category": "synthetic"}
    elif symbol.startswith("JUMP"):
        remainder = symbol[len("JUMP"):]
        code = SYMBOL_MAP["JUMP"].get(remainder)
        if code:
            return {"symbol": code, "category": "synthetic"}
        else:
            return {"symbol": symbol, "category": "synthetic"}
    else:
        return SYMBOL_MAP.get(symbol, {"symbol": symbol, "category": None})

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
    logger.info(f"Sending alert to {user}: {message}")
    # Implement Twilio alert logic here
    pass

scheduler.add_job(check_market_conditions, 'interval', minutes=1)

# ======== CORE FUNCTIONS ========
def calculate_winrate(data):
    if len(data) < 100:
        return "N/A"
    df_sorted = data.sort_index(ascending=True)
    changes = df_sorted['close'].pct_change().dropna()
    return f"{(len(changes[changes > 0]) / len(changes)) * 100:.1f}%"

def analyze_price_action(symbol):
    df_15m = get_deriv_data(symbol, TIMEFRAMES['analysis'])
    df_1m = get_deriv_data(symbol, TIMEFRAMES['entry'])
    if df_15m is None or len(df_15m) < 2:
        return None
    if df_1m is None or df_1m.empty:
        return None
    prev_high = df_15m['high'].iloc[1]
    prev_low = df_15m['low'].iloc[1]
    current_price = df_1m['close'].iloc[0]
    signal = None
    if current_price > prev_high:
        entry = current_price
        sl = prev_low
        risk = entry - sl
        tp1 = entry + 2 * risk
        tp2 = entry + 4 * risk
        signal = ('BUY', entry, sl, tp1, tp2)
    elif current_price < prev_low:
        entry = current_price
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
        'position_size': position_size
    }

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

@app.route("/")
def home():
    return (
        "ShadowFx Trading Bot - Operational\n"
        "Supported Instruments:\n"
        "• Forex: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURGBP, USDSEK, USDNOK, USDTRY, EURJPY, GBPJPY\n"
        "• Commodities: XAUUSD, XAGUSD, CL1, NG1, CO1, HG1\n"
        "• Indices: SPX, NDX, DJI, FTSE, DAX, NIKKEI, HSI, ASX, CAC\n"
        "• Crypto: BTCUSD, ETHUSD, XRPUSD, LTCUSD, BCHUSD, ADAUSD, DOTUSD, SOLUSD\n"
        "• ETFs: SPY, QQQ, GLD, XLF, IWM, EEM\n"
        "• Stocks: AAPL, TSLA, AMZN, GOOGL, MSFT, META, NVDA, NFLX\n"
        "• Synthetics:\n"
        "   - Boom: BOOM1000, BOOM300, BOOM500, BOOM600, BOOM900\n"
        "   - Crash: CRASH1000, CRASH300, CRASH500, CRASH600, CRASH900\n"
        "   - Volatility (Standard): VOLATILITY10, VOLATILITY25, VOLATILITY50, VOLATILITY75, VOLATILITY100, VOLATILITY150\n"
        "   - Short-Term Volatility: VOLATILITY10S, VOLATILITY25S, VOLATILITY50S, VOLATILITY75S, VOLATILITY150S, VOLATILITY250S\n"
        "   - Jump Indices: JUMP10, JUMP25, JUMP50, JUMP75, JUMP100\n\n"
        "Commands:\n"
        "➤ Analysis: EURUSD\n"
        "➤ Price: PRICE BTCUSD\n"
        "➤ Alert: ALERT BTCUSD"
    )

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        incoming_msg = request.form.get("Body", "").strip().upper()
        response = MessagingResponse()
        user_number = request.form.get("From")
        # Accept direct symbols or commands starting with VOLATILITY or JUMP
        if incoming_msg in SYMBOL_MAP or incoming_msg.startswith("VOLATILITY") or incoming_msg.startswith("JUMP"):
            analysis = analyze_price_action(incoming_msg)
            if analysis:
                trade_id = f"{incoming_msg}_{int(time.time())}"
                with trade_lock:
                    active_trades[trade_id] = {
                        'symbol': incoming_msg,
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
                msg = f"No trading opportunity found for {incoming_msg}"
            response.message(msg)
            return str(response)
        elif incoming_msg.startswith("PRICE "):
            symbol = incoming_msg.split(" ")[1]
            df = get_deriv_data(symbol, interval='1min')
            if df is not None and not df.empty:
                price = df['close'].iloc[0]
                response.message(f"Current {symbol}: {price:.5f}")
            else:
                response.message("❌ Price unavailable")
            return str(response)
        elif incoming_msg.startswith("ALERT "):
            symbol = incoming_msg.split(" ")[1]
            if symbol in SYMBOL_MAP or symbol.startswith("VOLATILITY") or symbol.startswith("JUMP"):
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
        elif incoming_msg in ["HI", "HELLO", "START"]:
            response.message(
                "📈 ShadowFx Trading Bot 📈\n"
                "Supported Instruments:\n"
                "• Forex: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURGBP, USDSEK, USDNOK, USDTRY, EURJPY, GBPJPY\n"
                "• Commodities: XAUUSD, XAGUSD, CL1, NG1, CO1, HG1\n"
                "• Indices: SPX, NDX, DJI, FTSE, DAX, NIKKEI, HSI, ASX, CAC\n"
                "• Crypto: BTCUSD, ETHUSD, XRPUSD, LTCUSD, BCHUSD, ADAUSD, DOTUSD, SOLUSD\n"
                "• ETFs: SPY, QQQ, GLD, XLF, IWM, EEM\n"
                "• Stocks: AAPL, TSLA, AMZN, GOOGL, MSFT, META, NVDA, NFLX\n"
                "• Synthetics:\n"
                "   - Boom: BOOM1000, BOOM300, BOOM500, BOOM600, BOOM900\n"
                "   - Crash: CRASH1000, CRASH300, CRASH500, CRASH600, CRASH900\n"
                "   - Volatility (Standard): VOLATILITY10, VOLATILITY25, VOLATILITY50, VOLATILITY75, VOLATILITY100, VOLATILITY150\n"
                "   - Short-Term Volatility: VOLATILITY10S, VOLATILITY25S, VOLATILITY50S, VOLATILITY75S, VOLATILITY150S, VOLATILITY250S\n"
                "   - Jump Indices: JUMP10, JUMP25, JUMP50, JUMP75, JUMP100\n\n"
                "Commands:\n"
                "➤ Analysis: EURUSD\n"
                "➤ Price: PRICE BTCUSD\n"
                "➤ Alert: ALERT BTCUSD"
            )
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
