from flask import Flask, render_template, Response, request
import requests
import json
import pandas as pd
from datetime import datetime
import time
import logging
import re

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Configuration
WHALE_THRESHOLD_LARGE = 100000  # Large transaction threshold
WHALE_THRESHOLD_SMALL = 100     # Small transaction threshold
API_TIMEOUT = 10

def safe_convert(value, default=0.0):
    try:
        if isinstance(value, (list, tuple)):
            return [safe_convert(v, default) for v in value]
        if isinstance(value, str):
            cleaned = re.sub(r"[^\d\.\-eE]", "", value)
            parts = cleaned.split('.')
            if len(parts) > 1:
                cleaned = f"{parts[0]}.{''.join(parts[1:])[:10]}"
            return float(cleaned) if cleaned else default
        return float(value)
    except:
        return default

def get_active_pairs():
    try:
        response = requests.get(
            "https://api.binance.com/api/v3/exchangeInfo",
            timeout=API_TIMEOUT
        )
        return [
            s['symbol'] for s in response.json().get('symbols', [])
            if s.get('symbol', '').endswith('USDT')
        ]
    except Exception as e:
        logging.error(f"Error fetching pairs: {str(e)}")
        return []

def get_candles(symbol, interval, limit):
    try:
        response = requests.get(
            f"https://api.binance.com/api/v3/klines",
            params={'symbol': symbol, 'interval': interval, 'limit': limit},
            timeout=API_TIMEOUT
        )
        data = response.json()
        return [{'open': float(candle[1]), 'close': float(candle[4])} for candle in data]
    except Exception as e:
        logging.error(f"Error fetching candles: {str(e)}")
        return []

def detect_whale_activity(symbol):
    try:
        # Get recent trades
        trades = requests.get(
            "https://api.binance.com/api/v3/trades",
            params={'symbol': symbol, 'limit': 1000},
            timeout=API_TIMEOUT
        ).json()

        # Analyze large transactions
        large_trades = [t for t in trades if safe_convert(t['price']) * safe_convert(t['qty']) > WHALE_THRESHOLD_LARGE]
        small_trades = [t for t in trades if safe_convert(t['price']) * safe_convert(t['qty']) < WHALE_THRESHOLD_SMALL]

        # Get dark pool data if available (example implementation)
        try:
            dark_pool_data = requests.get(
                f"https://api.binance.com/api/v3/depth",
                params={'symbol': symbol, 'limit': 1000},
                timeout=API_TIMEOUT
            ).json()
            large_orders = [
                order for order in dark_pool_data.get('bids', []) + dark_pool_data.get('asks', [])
                if safe_convert(order[0]) * safe_convert(order[1]) > WHALE_THRESHOLD_LARGE
            ]
        except:
            large_orders = []

        return {
            'has_whale_activity': bool(large_trades) or bool(large_orders),
            'large_trades_count': len(large_trades),
            'small_trades_count': len(small_trades),
            'large_orders_count': len(large_orders)
        }
    except Exception as e:
        logging.error(f"Error detecting whale activity: {str(e)}")
        return {'has_whale_activity': False, 'large_trades_count': 0, 'small_trades_count': 0, 'large_orders_count': 0}

@app.route('/stream')
def data_stream():
    def generate():
        while True:
            pairs = get_active_pairs()
            for symbol in pairs:
                try:
                    # Get current price
                    ticker = requests.get(
                        f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}",
                        timeout=API_TIMEOUT
                    ).json()
                    current_price = safe_convert(ticker.get('price'))
                    
                    if current_price == 0:
                        continue

                    # Get last 3 candles
                    candles = get_candles(symbol, '1h', 3)
                    
                    # Get whale activity
                    whale_data = detect_whale_activity(symbol)

                    data = {
                        'symbol': symbol,
                        'price': current_price,
                        'candles': candles,
                        'whale_activity': whale_data
                    }
                    
                    yield f"data: {json.dumps(data)}\n\n"
                except Exception as e:
                    logging.error(f"Error processing {symbol}: {str(e)}")
                time.sleep(0.5)
            time.sleep(10)  # Refresh interval
    return Response(generate(), mimetype="text/event-stream")

@app.route('/profitable_pairs', methods=['GET', 'POST'])
def profitable_pairs():
    if request.method == 'POST':
        timeframe = request.form.get('timeframe')
        n_candles = int(request.form.get('n_candles'))
        
        profitable_pairs_data = []
        pairs = get_active_pairs()
        
        for symbol in pairs:
            try:
                # Get current price
                ticker = requests.get(
                    f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}",
                    timeout=API_TIMEOUT
                ).json()
                current_price = safe_convert(ticker.get('price'))
                
                if current_price == 0:
                    continue

                # Get candles
                candles = get_candles(symbol, timeframe, n_candles)
                if not candles:
                    continue

                # Find reference candle (CR)
                cr = max(candles, key=lambda x: x['close'] - x['open'])
                if cr['close'] <= cr['open']:  # Skip red candles
                    continue

                # Calculate moyenne
                moyenne = (cr['close'] + cr['open']) / 2

                # Check if all subsequent candles are within range
                cr_index = candles.index(cr)
                valid = all(
                    moyenne <= c['open'] <= cr['close'] and moyenne <= c['close'] <= cr['close']
                    for c in candles[cr_index+1:]
                )

                if valid:
                    # Check whale activity
                    whale_data = detect_whale_activity(symbol)
                    
                    profitable_pairs_data.append({
                        'symbol': symbol,
                        'cr_open': cr['open'],
                        'cr_close': cr['close'],
                        'current_price': current_price,
                        'whale_activity': whale_data['has_whale_activity']
                    })

            except Exception as e:
                logging.error(f"Error processing {symbol}: {str(e)}")

        return render_template('profitable_pairs.html', pairs=profitable_pairs_data)
    
    return render_template('profitable_pairs.html', pairs=[])

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)