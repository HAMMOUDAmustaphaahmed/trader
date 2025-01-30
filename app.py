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
WHALE_THRESHOLD_LARGE = 100000
WHALE_THRESHOLD_SMALL = 100
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
        response.raise_for_status()
        data = response.json()
        return [
            s['symbol'] for s in data.get('symbols', [])
            if s.get('symbol', '').endswith('USDT') and 
            s.get('status') == 'TRADING'
        ]
    except Exception as e:
        logging.error(f"Error fetching pairs: {str(e)}")
        return []

def get_current_price(symbol):
    try:
        response = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={'symbol': symbol},
            timeout=API_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        return float(data.get('price', 0))
    except Exception as e:
        logging.error(f"Error getting price for {symbol}: {str(e)}")
        return 0

def get_candles(symbol, interval, limit):
    try:
        response = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            },
            timeout=API_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        
        if not isinstance(data, list):
            logging.error(f"Invalid candle data format for {symbol}")
            return []
            
        return [
            {
                'open': float(candle[1]),
                'high': float(candle[2]),
                'low': float(candle[3]),
                'close': float(candle[4]),
                'volume': float(candle[5]),
                'timestamp': int(candle[0])
            }
            for candle in data
        ]
    except Exception as e:
        logging.error(f"Error fetching candles for {symbol}: {str(e)}")
        return []

def calculate_depth_change(current_depth, previous_depth):
    try:
        if not previous_depth:
            return 0
        current_total = sum(float(price) * float(qty) for price, qty in current_depth.get('bids', [])[:10])
        previous_total = sum(float(price) * float(qty) for price, qty in previous_depth.get('bids', [])[:10])
        return ((current_total - previous_total) / previous_total) * 100 if previous_total else 0
    except:
        return 0

def get_funding_rate(symbol):
    try:
        response = requests.get(
            f"https://fapi.binance.com/fapi/v1/premiumIndex",
            params={'symbol': symbol},
            timeout=API_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        return float(data.get('lastFundingRate', 0)) * 100
    except:
        return 0

def detect_whale_activity(symbol):
    try:
        # Get recent trades
        trades_response = requests.get(
            "https://api.binance.com/api/v3/trades",
            params={'symbol': symbol, 'limit': 1000},
            timeout=API_TIMEOUT
        )
        trades_response.raise_for_status()
        trades = trades_response.json()

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
            except (ValueError, TypeError):
                continue

        # Get order book data
        depth_response = requests.get(
            "https://api.binance.com/api/v3/depth",
            params={'symbol': symbol, 'limit': 1000},
            timeout=API_TIMEOUT
        )
        depth_response.raise_for_status()
        depth_data = depth_response.json()
        
        # Calculate depth change
        depth_change = calculate_depth_change(depth_data, getattr(detect_whale_activity, 'previous_depth', {}))
        setattr(detect_whale_activity, 'previous_depth', depth_data)

        # Get funding rate
        funding_rate = get_funding_rate(symbol)

        # Calculate whale score (0-100)
        large_trades_score = min(len(large_trades) * 10, 40)  # Max 40 points
        depth_score = min(abs(depth_change), 30)  # Max 30 points
        funding_score = min(abs(funding_rate) * 10, 30)  # Max 30 points
        whale_score = large_trades_score + depth_score + funding_score

        return {
            'has_whale_activity': bool(large_trades) or abs(depth_change) > 5 or abs(funding_rate) > 0.1,
            'large_trades_count': len(large_trades),
            'small_trades_count': len(small_trades),
            'large_orders_count': len([order for side in ['bids', 'asks'] 
                                    for order in depth_data.get(side, [])
                                    if float(order[0]) * float(order[1]) > WHALE_THRESHOLD_LARGE]),
            'whale_score': round(whale_score, 1),
            'funding_rate': round(funding_rate, 4),
            'depth_change': round(depth_change, 2)
        }

    except Exception as e:
        logging.error(f"Error detecting whale activity for {symbol}: {str(e)}")
        return {
            'has_whale_activity': False,
            'large_trades_count': 0,
            'small_trades_count': 0,
            'large_orders_count': 0,
            'whale_score': 0,
            'funding_rate': 0,
            'depth_change': 0
        }
    
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/stream')
def data_stream():
    def generate():
        while True:
            pairs = get_active_pairs()
            for symbol in pairs:
                try:
                    time.sleep(0.1)  # Rate limiting
                    current_price = get_current_price(symbol)
                    
                    if current_price == 0:
                        continue

                    candles = get_candles(symbol, '1h', 3)
                    if not candles:
                        continue
                    
                    whale_data = detect_whale_activity(symbol)

                    data = {
                        'symbol': symbol,
                        'price': current_price,
                        'candles': candles,
                        'whale_activity': whale_data
                    }
                    
                    yield f"data: {json.dumps(data)}\n\n"
                except Exception as e:
                    logging.error(f"Error processing {symbol} in stream: {str(e)}")
                time.sleep(0.5)
            time.sleep(10)
    
    return Response(generate(), mimetype="text/event-stream")

@app.route('/profitable_pairs', methods=['GET', 'POST'])
def profitable_pairs():
    if request.method == 'POST':
        timeframe = request.form.get('timeframe', '1h')
        try:
            n_candles = int(request.form.get('n_candles', 10))
        except ValueError:
            return render_template('profitable_pairs.html', 
                                 error="Invalid number of candles provided",
                                 pairs=[])
        
        profitable_pairs_data = []
        pairs = get_active_pairs()
        
        for symbol in pairs:
            try:
                time.sleep(0.1)  # Rate limiting
                current_price = get_current_price(symbol)
                
                if current_price == 0:
                    continue

                candles = get_candles(symbol, timeframe, n_candles)
                if len(candles) < n_candles:
                    continue

                cr = max(candles, key=lambda x: x['close'] - x['open'])
                if cr['close'] <= cr['open']:
                    continue

                moyenne = (cr['close'] + cr['open']) / 2

                cr_index = candles.index(cr)
                valid = all(
                    moyenne <= c['open'] <= cr['close'] and 
                    moyenne <= c['close'] <= cr['close']
                    for c in candles[cr_index+1:]
                )

                if valid:
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

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)