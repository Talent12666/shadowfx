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
DERIV_WS_URI = "wss://ws.derivws.com/websockets/v3?app_id=1089"
GRANULARITY_MAP = {
    '15min': 900,
    '5min': 300,
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
    "XAUUSD": {"symbol": "WLDAUD", "category": "commodity"},
    "XAGUSD": {"symbol": "SILVER", "category": "commodity"},
    "CL1":    {"symbol": "CL_BRENT", "category": "commodity"},
    "NG1":    {"symbol": "NG_HEN", "category": "commodity"},
    "CO1":    {"symbol": "COAL", "category": "commodity"},
    "HG1":    {"symbol": "XCUUSD", "category": "commodity"},

    # Indices
    "SPX":    {"symbol": "SPX500", "category": "index"},
    "NDX":    {"symbol": "NAS100", "category": "index"},
    "DJI":    {"symbol": "DJ30", "category": "index"},
    "FTSE":   {"symbol": "FTSE100", "category": "index"},
    "DAX":    {"symbol": "DAX40", "category": "index"},
    "NIKKEI": {"symbol": "JP225", "category": "index"},
    "HSI":    {"symbol": "HK50", "category": "index"},
    "ASX":    {"symbol": "AUS200", "category": "index"},
    "CAC":    {"symbol": "FRA40", "category": "index"},

    # Cryptocurrencies
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

    # Synthetics
    "BOOM1000": {"symbol": "BOOM1000", "category": "synthetic"},
    "BOOM300":  {"symbol": "BOOM300",  "category": "synthetic"},
    "BOOM500":  {"symbol": "BOOM500",  "category": "synthetic"},
    "BOOM600":  {"symbol": "BOOM600",  "category": "synthetic"},
    "BOOM900":  {"symbol": "BOOM900",  "category": "synthetic"},
    "CRASH1000": {"symbol": "CRASH1000", "category": "synthetic"},
    "CRASH300":  {"symbol": "CRASH300", "category": "synthetic"},
    "CRASH500":  {"symbol": "CRASH500", "category": "synthetic"},
    "CRASH600":  {"symbol": "CRASH600", "category": "synthetic"},
    "CRASH900":  {"symbol": "CRASH900", "category": "synthetic"},
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
    'sl': '5min',
    'entry': '1min'
}

# ======== CATEGORY CONFIGURATION ========
CATEGORIES = [
    ('forex', 'Forex'),
    ('commodity', 'Commodities'),
    ('index', 'Indices'),
    ('crypto', 'Cryptocurrencies'),
    ('etf', 'ETFs'),
    ('stock', 'Stocks'),
    ('synthetic', 'Synthetics')
]

def get_symbols_by_category(category_name):
    symbols = []
    for sym, info in SYMBOL_MAP.items():
        if isinstance(info, dict) and info.get('category') == category_name:
            symbols.append(sym)
    return symbols

# ======== RISK MANAGEMENT CONFIG ========
DEFAULT_ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", 10000))
RISK_PERCENTAGE = float(os.getenv("RISK_PERCENTAGE", 1))

def calculate_position_size(account_balance, risk_percentage, stop_loss_distance):
    risk_amount = account_balance * (risk_percentage / 100)
    if stop_loss_distance == 0:
        return 0
    return risk_amount / stop_loss_distance

# ======== DERIV WEBSOCKET FUNCTIONS ========
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
            if "error" in data:
                logger.error(f"Deriv API error for {symbol}: {data.get('error', 'Unknown error')}")
                return None
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
                logger.error(f"Unexpected response for {symbol}: {data}")
                return None
    except Exception as e:
        logger.error(f"Error connecting to Deriv WebSocket for {symbol}: {str(e)}")
        return None

async def async_analyze_price_action(symbol):
    config = convert_symbol(symbol)
    tasks = [
        async_get_deriv_data(config["symbol"], TIMEFRAMES['analysis']),
        async_get_deriv_data(config["symbol"], TIMEFRAMES['sl']),
        async_get_deriv_data(config["symbol"], TIMEFRAMES['entry'])
    ]
    try:
        df_15m, df_5m, df_1m = await asyncio.gather(*tasks)
    except Exception as e:
        logger.error(f"Error gathering data for {symbol}: {str(e)}")
        return None

    if df_15m is None or len(df_15m) < 2:
        return None
    if df_5m is None or df_5m.empty:
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

    if symbol == "XAUUSD":
        df_curr = await async_get_deriv_data(config["symbol"], '1min')
        if df_curr is not None and not df_curr.empty:
            entry = df_curr['close'].iloc[0]

    stop_loss_distance = abs(signal[1] - signal[2])
    position_size = calculate_position_size(DEFAULT_ACCOUNT_BALANCE, RISK_PERCENTAGE, stop_loss_distance)

    return {
        'symbol': symbol,
        'signal': signal[0],
        'winrate': calculate_winrate(df_15m),
        'entry': entry,
        'sl': signal[2],
        'tp1': signal[3],
        'tp2': signal[4],
        'position_size': position_size
    }

def analyze_price_action(symbol):
    try:
        return asyncio.run(async_analyze_price_action(symbol))
    except Exception as e:
        logger.error(f"Analysis Error ({symbol}): {str(e)}")
        return None

# ======== SYMBOL CONVERSION ========
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

# ======== CORE TRADING FUNCTIONS ========
def calculate_winrate(data):
    if len(data) < 100:
        return "N/A"
    df_sorted = data.sort_index(ascending=True)
    changes = df_sorted['close'].pct_change().dropna()
    return f"{(len(changes[changes > 0]) / len(changes)) * 100:.1f}%"

# ======== FLASK ROUTES ========
@app.route("/")
def home():
    return "ShadowFx Trading Bot - Operational"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        incoming_msg = request.form.get("Body", "").strip().upper()
        response = MessagingResponse()
        user_number = request.form.get("From")

        if incoming_msg.isdigit():
            group_number = int(incoming_msg)
            if 1 <= group_number <= len(CATEGORIES):
                category_key, category_name = CATEGORIES[group_number-1]
                symbols = get_symbols_by_category(category_key)
                if not symbols:
                    response.message(f"No symbols found in {category_name} group.")
                    return str(response)
                
                async def group_analysis():
                    tasks = [async_analyze_price_action(sym) for sym in symbols]
                    return await asyncio.gather(*tasks)
                analyses_list = asyncio.run(group_analysis())

                analyses = []
                for sym, analysis in zip(symbols, analyses_list):
                    if analysis:
                        msg = (f"📊 {analysis['symbol']} Analysis\n"
                               f"Signal: {analysis['signal']}\n"
                               f"Winrate: {analysis['winrate']}\n"
                               f"Entry: {analysis['entry']:.5f}\n"
                               f"SL: {analysis['sl']:.5f}\n"
                               f"TP1: {analysis['tp1']:.5f}\n"
                               f"TP2: {analysis['tp2']:.5f}")
                        analyses.append(msg)
                    else:
                        analyses.append(f"No trading signal for {sym}")
                response.message("\n\n".join(analyses))
                return str(response)
            else:
                response.message("❌ Invalid group number. Send 'HI' for options.")
                return str(response)

        elif incoming_msg in SYMBOL_MAP or incoming_msg.startswith(("VOLATILITY", "JUMP")):
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
            converted = convert_symbol(symbol)["symbol"]
            df = asyncio.run(async_get_deriv_data(converted, interval='1min'))
            if df is not None and not df.empty:
                price = df['close'].iloc[0]
                response.message(f"Current {symbol}: {price:.5f}")
            else:
                response.message("❌ Price unavailable")
            return str(response)

        elif incoming_msg.startswith("ALERT "):
            symbol = incoming_msg.split(" ")[1]
            if symbol in SYMBOL_MAP or symbol.startswith(("VOLATILITY", "JUMP")):
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
            greeting = "📈 space_zero 2.0 Trading Bot 📈\nChoose a group for analysis:\n"
            for idx, (_, name) in enumerate(CATEGORIES, 1):
                greeting += f"{idx}. {name}\n"
            greeting += "\nSend group number (e.g., 1) for analysis of all assets in that category."
            response.message(greeting)
            return str(response)
        else:
            response.message("❌ Invalid command. Send 'HI' for help")
            return str(response)
    except Exception as e:
        logger.error(f"Error in webhook: {str(e)}")
        return "Error processing request", 500

if __name__ == "__main__":
    try:
        scheduler.start()
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port)
    except Exception as e:
        logger.error(f"Error starting application: {str(e)}")
