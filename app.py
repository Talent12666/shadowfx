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

# ======== ORIGINAL SYMBOL MAPPING ========
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
}

CATEGORIES = [
    ('forex', 'Forex'),
    ('commodity', 'Commodities'),
    ('index', 'Indices'),
    ('crypto', 'Cryptocurrencies'),
    ('etf', 'ETFs'),
    ('stock', 'Stocks'),
    ('synthetic', 'Synthetics')
]

TIMEFRAMES = {
    'analysis': '15min',
    'sl': '5min',
    'entry': '1min'
}

# ======== WEB SOCKET HANDLER (FIXED) ========
async def async_get_deriv_data(symbol, interval='15min'):
    """Enhanced WebSocket handler with proper timeout management"""
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
        async with websockets.connect(
            DERIV_WS_URI,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10
        ) as websocket:
            await asyncio.wait_for(
                websocket.send(json.dumps(request_payload)),
                timeout=10
            )
            response = await asyncio.wait_for(
                websocket.recv(),
                timeout=15
            )
            data = json.loads(response)
            
            if "candles" in data:
                df = pd.DataFrame(data["candles"])
                df['time'] = pd.to_datetime(df['epoch'], unit='s')
                return df[['time', 'open', 'high', 'low', 'close']].sort_values('time', ascending=False).set_index('time').astype(float)
            return None
    except Exception as e:
        logger.error(f"Connection error for {symbol}: {str(e)}")
        return None

def get_deriv_data(symbol, interval='15min'):
    """Maintain original data fetching logic with improved error handling"""
    try:
        config = SYMBOL_MAP.get(symbol.upper())
        if not config or not config.get('symbol'):
            logger.warning(f"Invalid symbol requested: {symbol}")
            return None
            
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        df = loop.run_until_complete(async_get_deriv_data(config["symbol"], interval))
        loop.close()
        
        return df if not df.empty else None
    except Exception as e:
        logger.error(f"Data processing error: {str(e)}")
        return None

# ======== SPACE ZERO 2.0 ANALYSIS CORE ========
def analyze_price_action(symbol):
    """Original analysis logic with XAUUSD special handling"""
    df_15m = get_deriv_data(symbol, TIMEFRAMES['analysis'])
    df_5m = get_deriv_data(symbol, TIMEFRAMES['sl'])
    df_1m = get_deriv_data(symbol, TIMEFRAMES['entry'])
    
    if df_15m is None or len(df_15m) < 2:
        return None
    if df_5m is None or df_5m.empty:
        return None
    if df_1m is None or df_1m.empty:
        return None

    prev_high = df_15m['high'].iloc[1]
    prev_low = df_15m['low'].iloc[1]
    current_price = df_1m['close'].iloc[0]

    # Special handling for XAUUSD
    if symbol == "XAUUSD":
        current_price = df_1m['close'].iloc[0]  # Force current price

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

    return {
        'symbol': symbol,
        'signal': signal[0],
        'entry': entry,
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
    }

# ======== WEBHOOK HANDLER ========
@app.route("/webhook", methods=["POST"])
def webhook():
    """Original webhook logic with Space Zero 2.0 branding"""
    try:
        incoming_msg = request.form.get("Body", "").strip().upper()
        response = MessagingResponse()
        user_number = request.form.get("From")

        if incoming_msg.isdigit():
            group_number = int(incoming_msg)
            if 1 <= group_number <= len(CATEGORIES):
                category_key, category_name = CATEGORIES[group_number-1]
                symbols = [sym for sym, info in SYMBOL_MAP.items() if info.get('category') == category_key]
                
                analyses = []
                for symbol in symbols:
                    analysis = analyze_price_action(symbol)
                    if analysis:
                        msg = (f"🚀 Space Zero 2.0 - {analysis['symbol']}\n"
                               f"Signal: {analysis['signal']}\n"
                               f"Entry: {analysis['entry']:.5f}\n"
                               f"SL: {analysis['sl']:.5f}\n"
                               f"TP1: {analysis['tp1']:.5f}\n"
                               f"TP2: {analysis['tp2']:.5f}")
                        analyses.append(msg)
                
                response.message("\n\n".join(analyses) if analyses else f"No signals in {category_name}")
                return str(response)

        elif incoming_msg in SYMBOL_MAP:
            analysis = analyze_price_action(incoming_msg)
            if analysis:
                msg = (f"🚀 Space Zero 2.0 - {analysis['symbol']}\n"
                       f"Signal: {analysis['signal']}\n"
                       f"Entry: {analysis['entry']:.5f}\n"
                       f"SL: {analysis['sl']:.5f}\n"
                       f"TP1: {analysis['tp1']:.5f}\n"
                       f"TP2: {analysis['tp2']:.5f}")
            else:
                msg = f"Space Zero 2.0 - No signal for {incoming_msg}"
            response.message(msg)
            return str(response)

        elif incoming_msg in ["HI", "HELLO", "START"]:
            greeting = ("🚀 Welcome to Space Zero 2.0 📈\n"
                        "Choose analysis group:\n"
                        "1. Forex\n2. Commodities\n3. Indices\n"
                        "4. Crypto\n5. ETFs\n6. Stocks\n7. Synthetics\n\n"
                        "Send group number or asset name")
            response.message(greeting)
            return str(response)

        else:
            response.message("Space Zero 2.0 - Invalid command. Send 'HI' for help")
        
        return str(response)
    except Exception as e:
        logger.error(f"Space Zero 2.0 Error: {str(e)}")
        return "System Error", 500

if __name__ == "__main__":
    scheduler.start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
