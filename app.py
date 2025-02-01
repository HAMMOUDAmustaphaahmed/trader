from flask import Flask, render_template, Response, request
import requests
import concurrent.futures
import json
import time
import os
import numpy as np
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-key')

# Configure requests session with retries
session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

BINANCE_API_BASE = "https://api.binance.com/api/v3"
BINANCE_RATE_LIMIT_DELAY = 0.3
VALID_TIMEFRAMES = ['1h', '6h', '12h', '1d', '1w']

def get_all_usdt_pairs():
    url = f"{BINANCE_API_BASE}/exchangeInfo"
    response = session.get(url)
    response.raise_for_status()
    data = response.json()
    return [s['symbol'] for s in data['symbols'] 
            if s['symbol'].endswith('USDT') and s['status'] == 'TRADING']

def get_candles(symbol, interval, limit):
    time.sleep(BINANCE_RATE_LIMIT_DELAY)
    url = f"{BINANCE_API_BASE}/klines"
    params = {'symbol': symbol, 'interval': interval, 'limit': limit}
    response = session.get(url, params=params)
    response.raise_for_status()
    return [{
        'timestamp': int(candle[0]),
        'open': float(candle[1]),
        'high': float(candle[2]),
        'low': float(candle[3]),
        'close': float(candle[4]),
        'volume': float(candle[5]),
    } for candle in response.json()]

def detect_whale_activity(symbol):
    try:
        # 1. Volume Pattern Analysis
        klines = session.get(f"{BINANCE_API_BASE}/klines", params={
            'symbol': symbol,
            'interval': '5m',
            'limit': 100
        }).json()
        
        volumes = [float(k[5]) for k in klines]
        avg_volume = np.mean(volumes[:-5]).item() if len(volumes) > 5 else 0
        current_volume = np.mean(volumes[-5:]).item() if len(volumes) >= 5 else 0
        
        volume_ratio = current_volume / avg_volume if avg_volume != 0 else 0
        volume_surge = bool(volume_ratio > 3)

        # 2. Large Trade Clustering
        trades = session.get(f"{BINANCE_API_BASE}/trades", params={
            'symbol': symbol, 
            'limit': 50
        }).json()
        
        trade_sizes = [float(t['qty']) for t in trades]
        avg_trade = np.median(trade_sizes).item() if trade_sizes else 0
        whale_trades = [t for t in trade_sizes if t > avg_trade * 10] if avg_trade else []
        cluster_flag = bool(len(whale_trades) > 3)

        # 3. Order Book Imbalance
        depth = session.get(f"{BINANCE_API_BASE}/depth", params={
            'symbol': symbol,
            'limit': 50
        }).json()
        
        bid_vol = sum(float(b[1]) for b in depth['bids'][:3])
        ask_vol = sum(float(a[1]) for a in depth['asks'][:3])
        min_vol = min(bid_vol, ask_vol) if bid_vol and ask_vol else 1
        imbalance = abs(bid_vol - ask_vol) / min_vol
        imbalance_flag = bool(imbalance > 2)

        # 4. Hidden Liquidity
        depth_vol = sum(float(b[1]) for b in depth['bids']) + sum(float(a[1]) for a in depth['asks'])
        visible_ratio = (bid_vol + ask_vol) / depth_vol if depth_vol !=0 else 0
        hidden_liquidity = bool(visible_ratio < 0.4)

        # 5. Price Slippage
        if depth['bids'] and depth['asks'] and trades:
            theoretical_price = (float(depth['bids'][0][0]) + float(depth['asks'][0][0]))/2
            last_price = float(trades[-1]['price'])
            slippage = abs(last_price - theoretical_price)/theoretical_price*10000
            slippage_flag = bool(slippage > 5)
        else:
            slippage_flag = False

        flags = [volume_surge, cluster_flag, imbalance_flag, hidden_liquidity, slippage_flag]
        return int(sum(flags))

    except Exception as e:
        print(f"Whale detection error: {str(e)}")
        return 0

def analyze_candles(symbol, candles):
    if len(candles) < 10:
        return None, "Insufficient data"

    relevant = candles[:-2]
    sequences = []
    current_seq = []
    
    for idx, candle in enumerate(relevant):
        if candle['close'] > candle['open']:
            current_seq.append((idx, candle))
        elif current_seq:
            sequences.append(current_seq)
            current_seq = []
    if current_seq:
        sequences.append(current_seq)

    if not sequences:
        return None, "No green sequences"
        
    composites = [{
        'start': seq[0][0],
        'end': seq[-1][0],
        'open': seq[0][1]['open'],
        'close': seq[-1][1]['close'],
        'high': max(c[1]['high'] for c in seq),
        'size': seq[-1][1]['close'] - seq[0][1]['open']
    } for seq in sequences]

    max_comp = max(composites, key=lambda x: x['size'])
    moyenne = (max_comp['open'] + max_comp['close']) / 2
    interval = [moyenne, max_comp['close']]

    for candle in candles[max_comp['end'] + 1:]:
        if not (interval[0] <= candle['open'] <= interval[1] and
                interval[0] <= candle['close'] <= interval[1]):
            return None, "Breakout detected"

    last_candle = candles[-1]
    if last_candle['close'] <= last_candle['open']:
        return None, "Bearish close"

    current_price = get_current_price(symbol)
    if current_price > max_comp['close']:
        return None, "Price exceeded"

    return {
        'max_candle': {'open': max_comp['open'], 'close': max_comp['close']},
        'current_price': current_price
    }, "Success"

def get_current_price(symbol):
    time.sleep(BINANCE_RATE_LIMIT_DELAY)
    response = session.get(f"{BINANCE_API_BASE}/ticker/price", params={'symbol': symbol})
    return float(response.json()['price'])

def analyze_symbol(symbol, timeframe, candle_count):
    try:
        candles = get_candles(symbol, timeframe, candle_count)
        result, msg = analyze_candles(symbol, candles)
        whale_score = detect_whale_activity(symbol)
        
        if result:
            return {
                'status': 'success',
                'data': {
                    'symbol': symbol,
                    'open': result['max_candle']['open'],
                    'close': result['max_candle']['close'],
                    'current_price': result['current_price'],
                    'whale_score': whale_score
                }
            }
        return {'status': 'rejected'}
    except Exception as e:
        return {'status': 'error'}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/stream')
def stream():
    timeframe = request.args.get('timeframe', '1d')
    candle_count = int(request.args.get('candle_count', 10))
    
    if timeframe not in VALID_TIMEFRAMES:
        timeframe = '1d'
    candle_count = max(10, min(candle_count, 100))
    
    return Response(generate_analysis(timeframe, candle_count), mimetype='text/event-stream')

def generate_analysis(timeframe, candle_count):
    symbols = get_all_usdt_pairs()
    total = len(symbols)
    processed = 0
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(analyze_symbol, s, timeframe, candle_count): s for s in symbols}
        
        yield f"data: {json.dumps({'total': total})}\n\n"
        
        for future in concurrent.futures.as_completed(futures):
            processed += 1
            res = future.result()
            
            if res['status'] == 'success':
                yield f"data: {json.dumps(res['data'])}\n\n"
            
            yield f"data: {json.dumps({'processed': processed})}\n\n"
        
        yield "event: done\ndata: {}\n\n"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5001, debug=True)