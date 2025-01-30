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
        data = response.json()
        return [
            s['symbol'] for s in data.get('symbols', [])
            if s.get('symbol', '').endswith('USDT') and 
            s.get('status') == 'TRADING'  # Only get active trading pairs
        ]
    except Exception as e:
        logging.error(f"Error fetching pairs: {str(e)}")
        return []

def get_candles(symbol, interval, limit):
    try:
        response = requests.get(
            f"https://api.binance.com/api/v3/klines",
            params={
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            },
            timeout=API_TIMEOUT
        )
        data = response.json()
        return [
            {
                'open': float(candle[1]),
                'close': float(candle[4]),
                'timestamp': candle[0]
            } 
            for candle in data
        ]
    except Exception as e:
        logging.error(f"Error fetching candles for {symbol}: {str(e)}")
        return []

def detect_whale_activity(symbol):
    try:
        # Get recent trades
        trades_response = requests.get(
            "https://api.binance.com/api/v3/trades",
            params={
                'symbol': symbol,
                'limit': 1000
            },
            timeout=API_TIMEOUT
        )
        trades = trades_response.json()

        # Calculate transaction volumes
        large_trades = []
        small_trades = []
        
        for trade in trades:
            try:
                price = float(trade.get('price', 0))
                quantity = float(trade.get('qty', 0))
                volume = price * quantity
                
                if volume > WHALE_THRESHOLD_LARGE:
                    large_trades.append(trade)
                elif volume < WHALE_THRESHOLD_SMALL:
                    small_trades.append(trade)
            except (ValueError, TypeError) as e:
                continue

        # Get order book data
        depth_response = requests.get(
            f"https://api.binance.com/api/v3/depth",
            params={
                'symbol': symbol,
                'limit': 1000
            },
            timeout=API_TIMEOUT
        )
        depth_data = depth_response.json()
        
        # Analyze large orders in order book
        large_orders = []
        for side in ['bids', 'asks']:
            for order in depth_data.get(side, []):
                try:
                    price = float(order[0])
                    quantity = float(order[1])
                    if price * quantity > WHALE_THRESHOLD_LARGE:
                        large_orders.append(order)
                except (ValueError, TypeError):
                    continue

        return {
            'has_whale_activity': bool(large_trades) or bool(large_orders),
            'large_trades_count': len(large_trades),
            'small_trades_count': len(small_trades),
            'large_orders_count': len(large_orders)
        }
    except Exception as e:
        logging.error(f"Error detecting whale activity for {symbol}: {str(e)}")
        return {
            'has_whale_activity': False,
            'large_trades_count': 0,
            'small_trades_count': 0,
            'large_orders_count': 0
        }

@app.route('/profitable_pairs', methods=['GET', 'POST'])
def profitable_pairs():
    if request.method == 'POST':
        timeframe = request.form.get('timeframe', '1h')
        n_candles = int(request.form.get('n_candles', 10))
        
        profitable_pairs_data = []
        pairs = get_active_pairs()
        
        for symbol in pairs:
            try:
                # Get current price
                ticker_response = requests.get(
                    f"https://api.binance.com/api/v3/ticker/price",
                    params={'symbol': symbol},
                    timeout=API_TIMEOUT
                )
                ticker_data = ticker_response.json()
                current_price = float(ticker_data.get('price', 0))
                
                if current_price == 0:
                    continue

                # Exclude new coins (after 2024)
                symbol_info_response = requests.get(
                    f"https://api.binance.com/api/v3/ticker/24h",
                    params={'symbol': symbol},
                    timeout=API_TIMEOUT
                )
                symbol_info = symbol_info_response.json()
                first_trade_time = int(symbol_info.get('firstId', 0))  # Using firstId as approximation
                if first_trade_time > 1704067200000:  # 2024-01-01 00:00:00 UTC in milliseconds
                    continue

                # Get candles
                candles = get_candles(symbol, timeframe, n_candles)
                if len(candles) < n_candles:
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
                    moyenne <= c['open'] <= cr['close'] and 
                    moyenne <= c['close'] <= cr['close']
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
                continue

        return render_template(
            'profitable_pairs.html',
            pairs=profitable_pairs_data,
            timeframe=timeframe,
            n_candles=n_candles
        )
    
    return render_template('profitable_pairs.html', pairs=[])

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)