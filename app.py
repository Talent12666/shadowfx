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
GRANULARITY_MAP = {'15min': 900, '5min': 300, '1min': 60}

# ======== VALIDATED SYMBOL MAPPING ========
SYMBOL_MAP = {
    # Forex (verified frx pairs)
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

    # Commodities (corrected symbols)
    "XAUUSD": {"symbol": "WLDAUD", "category": "commodity"},
    "XAGUSD": {"symbol": "XAGUSD", "category": "commodity"},
    "CL1":    {"symbol": "CL_BRENT", "category": "commodity"},
    "NG1":    {"symbol": "NG_HENRY", "category": "commodity"},
    "CO1":    {"symbol": "COAL", "category": "commodity"},
    "HG1":    {"symbol": "XCUUSD", "category": "commodity"},

    # Indices (verified symbols)
    "SPX":    {"symbol": "SPX500", "category": "index"},
    "NDX":    {"symbol": "NAS100", "category": "index"},
    "DJI":    {"symbol": "DJ30", "category": "index"},
    "FTSE":   {"symbol": "FTSE100", "category": "index"},
    "DAX":    {"symbol": "DAX40", "category": "index"},
    "NIKKEI": {"symbol": "JP225", "category": "index"},
    "HSI":    {"symbol": "HK50", "category": "index"},
    "ASX":    {"symbol": "AUS200", "category": "index"},
    "CAC":    {"symbol": "FRA40", "category": "index"},

    # Cryptocurrencies (direct symbols)
    "BTCUSD": {"symbol": "BTCUSD", "category": "crypto"},
    "ETHUSD": {"symbol": "ETHUSD", "category": "crypto"},
    "XRPUSD": {"symbol": "XRPUSD", "category": "crypto"},

    # Synthetics (corrected symbols)
    "BOOM1000": {"symbol": "BOOM1000N", "category": "synthetic"},
    "BOOM300":  {"symbol": "BOOM300N",  "category": "synthetic"},
    "BOOM500":  {"symbol": "BOOM500N",  "category": "synthetic"},
    "BOOM600":  {"symbol": "BOOM600N",  "category": "synthetic"},
    "BOOM900":  {"symbol": "BOOM900N",  "category": "synthetic"},
    "CRASH1000": {"symbol": "CRASH1000N", "category": "synthetic"},
    "CRASH300":  {"symbol": "CRASH300N",  "category": "synthetic"},
    "CRASH500":  {"symbol": "CRASH500N",  "category": "synthetic"},
    "CRASH600":  {"symbol": "CRASH600N",  "category": "synthetic"},
    "CRASH900":  {"symbol": "CRASH900N",  "category": "synthetic"},
}

CATEGORIES = [
    ('forex', 'Forex'),
    ('commodity', 'Commodities'),
    ('index', 'Indices'),
    ('crypto', 'Cryptocurrencies'),
    ('synthetic', 'Synthetics')
]

TIMEFRAMES = {'analysis': '15min', 'sl': '5min', 'entry': '1min'}

# ======== IMPROVED DATA FETCHING ========
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
        async with websockets.connect(DERIV_WS_URI, timeout=15) as websocket:
            await websocket.send(json.dumps(request_payload))
            response = await websocket.recv()
            data = json.loads(response)
            
            if "candles" in data:
                df = pd.DataFrame(data["candles"])
                df['time'] = pd.to_datetime(df['epoch'], unit='s')
                return df[['time', 'open', 'high', 'low', 'close']].sort_values('time', ascending=False).set_index('time').astype(float)
            return None
    except Exception as e:
        logger.error(f"Data fetch error for {symbol}: {str(e)}")
        return None

def get_deriv_data(symbol, interval='15min'):
    try:
        config = SYMBOL_MAP.get(symbol.upper())
        if not config or not config.get('symbol'):
            logger.warning(f"Invalid symbol requested: {symbol}")
            return None
            
        df = asyncio.run(async_get_deriv_data(config["symbol"], interval))
        if df is None or df.empty:
            logger.warning(f"No data returned for {symbol}")
        return df
    except Exception as e:
        logger.error(f"Data processing error for {symbol}: {str(e)}")
        return None

# ======== ROBUST ANALYSIS FUNCTION ========
def analyze_price_action(symbol):
    try:
        # Get data with fallbacks
        df_15m = get_deriv_data(symbol, '15min') or get_deriv_data(symbol, '5min') or get_deriv_data(symbol, '1min')
        df_1m = get_deriv_data(symbol, '1min')
        
        if df_15m is None or len(df_15m) < 2 or df_1m is None:
            return None

        current_price = df_1m['close'].iloc[0]
        prev_high = df_15m['high'].iloc[1]
        prev_low = df_15m['low'].iloc[1]

        if current_price > prev_high:
            direction = 'BUY'
            entry = current_price
            sl = prev_low
        elif current_price < prev_low:
            direction = 'SELL'
            entry = current_price
            sl = prev_high
        else:
            return None

        # Special handling for XAUUSD
        if symbol == "XAUUSD":
            entry = current_price  # Force current price as entry

        risk = abs(entry - sl)
        return {
            'symbol': symbol,
            'signal': direction,
            'entry': entry,
            'sl': sl,
            'tp1': entry + (2 * risk) if direction == 'BUY' else entry - (2 * risk),
            'tp2': entry + (4 * risk) if direction == 'BUY' else entry - (4 * risk)
        }
    except Exception as e:
        logger.error(f"Analysis failed for {symbol}: {str(e)}")
        return None

# ======== WEBHOOK HANDLER WITH GROUP SUPPORT ========
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        incoming_msg = request.form.get("Body", "").strip().upper()
        resp = MessagingResponse()
        
        if incoming_msg.isdigit():
            group_num = int(incoming_msg)
            if 1 <= group_num <= len(CATEGORIES):
                category = CATEGORIES[group_num-1]
                symbols = [s for s, d in SYMBOL_MAP.items() if d['category'] == category[0]]
                responses = []
                
                for sym in symbols:
                    analysis = analyze_price_action(sym)
                    if analysis:
                        responses.append(
                            f"{analysis['symbol']} {analysis['signal']}\n"
                            f"Entry: {analysis['entry']:.5f}\n"
                            f"SL: {analysis['sl']:.5f}\n"
                            f"TP1: {analysis['tp1']:.5f}\n"
                            f"TP2: {analysis['tp2']:.5f}\n"
                        )
                
                resp.message("\n".join(responses) if responses else "No signals found")
            else:
                resp.message("Invalid group number")
        elif incoming_msg in SYMBOL_MAP:
            analysis = analyze_price_action(incoming_msg)
            if analysis:
                msg = (f"{analysis['symbol']} {analysis['signal']}\nEntry: {analysis['entry']:.5f}\n"
                       f"SL: {analysis['sl']:.5f}\nTP1: {analysis['tp1']:.5f}\nTP2: {analysis['tp2']:.5f}")
                resp.message(msg)
            else:
                resp.message(f"No signal for {incoming_msg}")
        elif incoming_msg in ["HI", "HELLO"]:
            menu = "Choose group:\n" + "\n".join([f"{i+1}. {cat[1]}" for i, cat in enumerate(CATEGORIES)])
            resp.message(menu)
        else:
            resp.message("Invalid command. Send 'HI' for help")
            
        return str(resp)
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return "Server Error", 500

if __name__ == "__main__":
    scheduler.start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
