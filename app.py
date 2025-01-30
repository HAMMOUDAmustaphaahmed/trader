from flask import Flask, render_template, Response, request, jsonify, stream_with_context
from datetime import datetime, UTC
import requests
import json
import pandas as pd
import time
import logging
import re
from threading import Thread, Lock
from queue import Queue
import numpy as np
from collections import deque

app = Flask(__name__)
app.config['PROPAGATE_EXCEPTIONS'] = True
logging.basicConfig(level=logging.INFO)

# Configuration
WHALE_THRESHOLD_LARGE = 100000
WHALE_THRESHOLD_SMALL = 100
API_TIMEOUT = 10
MAX_RETRIES = 3
RATE_LIMIT_DELAY = 0.1
MIN_CANDLES_FOR_ANALYSIS = 5

# Global variables
profitable_pairs_queue = Queue()
analysis_lock = Lock()
pair_analysis_cache = {}
cache_timeout = 300  # 5 minutes

class MarketAnalyzer:
    def __init__(self):
        self.cache = {}
        self.cache_timeout = 300
        self.lock = Lock()

    def calculate_technical_indicators(self, candles):
        try:
            if not candles or len(candles) < MIN_CANDLES_FOR_ANALYSIS:
                return None

            closes = np.array([c['close'] for c in candles])
            highs = np.array([c['high'] for c in candles])
            lows = np.array([c['low'] for c in candles])
            volumes = np.array([c['volume'] for c in candles])

            # RSI calculation
            delta = np.diff(closes)
            gain = (delta > 0) * delta
            loss = (delta < 0) * -delta
            avg_gain = np.mean(gain)
            avg_loss = np.mean(loss)
            rs = avg_gain / avg_loss if avg_loss != 0 else 0
            rsi = 100 - (100 / (1 + rs))

            # MACD calculation
            exp1 = np.mean(closes[-12:])
            exp2 = np.mean(closes[-26:])
            macd = exp1 - exp2

            # Bollinger Bands
            sma = np.mean(closes)
            std = np.std(closes)
            upper_band = sma + (std * 2)
            lower_band = sma - (std * 2)

            # Volume analysis
            vol_sma = np.mean(volumes)
            vol_trend = volumes[-1] / vol_sma if vol_sma > 0 else 1

            return {
                'rsi': float(rsi),
                'macd': float(macd),
                'bollinger': {
                    'upper': float(upper_band),
                    'lower': float(lower_band),
                    'middle': float(sma)
                },
                'volume_trend': float(vol_trend)
            }
        except Exception as e:
            logging.error(f"Error calculating indicators: {str(e)}")
            return None

    def analyze_market_conditions(self, candles):
        try:
            if not candles or len(candles) < MIN_CANDLES_FOR_ANALYSIS:
                return False, {'score': 0, 'message': "Insufficient data"}

            indicators = self.calculate_technical_indicators(candles)
            if not indicators:
                return False, {'score': 0, 'message': "Could not calculate indicators"}

            score = 0
            conditions = []

            # RSI Analysis
            if 30 <= indicators['rsi'] <= 70:
                score += 20
                conditions.append(('RSI in normal range', True))
            elif indicators['rsi'] < 30:
                score += 15
                conditions.append(('RSI oversold', True))
            else:
                conditions.append(('RSI overbought', False))

            # MACD Analysis
            if indicators['macd'] > 0:
                score += 20
                conditions.append(('MACD positive', True))
            else:
                conditions.append(('MACD negative', False))

            # Volume Analysis
            if indicators['volume_trend'] > 1.2:
                score += 30
                conditions.append(('Strong volume', True))
            elif indicators['volume_trend'] > 0.8:
                score += 15
                conditions.append(('Normal volume', True))
            else:
                conditions.append(('Weak volume', False))

            # Price Position relative to Bollinger Bands
            last_close = candles[-1]['close']
            if indicators['bollinger']['lower'] <= last_close <= indicators['bollinger']['upper']:
                score += 30
                conditions.append(('Price within BB', True))
            else:
                conditions.append(('Price outside BB', False))

            return score >= 60, {
                'score': score,
                'conditions': conditions,
                'indicators': indicators
            }

        except Exception as e:
            logging.error(f"Market analysis error: {str(e)}")
            return False, {'score': 0, 'message': str(e)}

market_analyzer = MarketAnalyzer()

def make_request_with_retry(url, params=None, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=API_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(RATE_LIMIT_DELAY * (attempt + 1))
    return None

def get_active_pairs():
    try:
        data = make_request_with_retry("https://api.binance.com/api/v3/exchangeInfo")
        pairs = [
            s['symbol'] for s in data.get('symbols', [])
            if s.get('symbol', '').endswith('USDT') and 
            s.get('status') == 'TRADING'
        ]
        return [p for p in pairs if not p.startswith('202')]
    except Exception as e:
        logging.error(f"Error fetching pairs: {str(e)}")
        return []

def get_current_price(symbol):
    cache_key = f"price_{symbol}"
    if cache_key in pair_analysis_cache:
        cache_time, cached_value = pair_analysis_cache[cache_key]
        if time.time() - cache_time < 60:
            return cached_value

    try:
        data = make_request_with_retry(
            "https://api.binance.com/api/v3/ticker/price",
            params={'symbol': symbol}
        )
        price = float(data.get('price', 0))
        pair_analysis_cache[cache_key] = (time.time(), price)
        return price
    except Exception as e:
        logging.error(f"Error getting price for {symbol}: {str(e)}")
        return 0

def get_candles(symbol, interval, limit):
    cache_key = f"candles_{symbol}_{interval}_{limit}"
    if cache_key in pair_analysis_cache:
        cache_time, cached_value = pair_analysis_cache[cache_key]
        if time.time() - cache_time < 60:
            return cached_value

    try:
        data = make_request_with_retry(
            "https://api.binance.com/api/v3/klines",
            params={
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            }
        )
        candles = [
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
        pair_analysis_cache[cache_key] = (time.time(), candles)
        return candles
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

        depth_response = requests.get(
            "https://api.binance.com/api/v3/depth",
            params={'symbol': symbol, 'limit': 1000},
            timeout=API_TIMEOUT
        )
        depth_response.raise_for_status()
        depth_data = depth_response.json()
        
        depth_change = calculate_depth_change(depth_data, getattr(detect_whale_activity, 'previous_depth', {}))
        setattr(detect_whale_activity, 'previous_depth', depth_data)

        funding_rate = get_funding_rate(symbol)

        large_trades_score = min(len(large_trades) * 10, 40)
        depth_score = min(abs(depth_change), 30)
        funding_score = min(abs(funding_rate) * 10, 30)
        whale_score = large_trades_score + depth_score + funding_score

        return {
            'has_whale_activity': whale_score > 50,
            'large_trades_count': len(large_trades),
            'small_trades_count': len(small_trades),
            'large_orders_count': len([
                order for side in ['bids', 'asks']
                for order in depth_data.get(side, [])
                if float(order[0]) * float(order[1]) > WHALE_THRESHOLD_LARGE
            ]),
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

def analyze_pair(symbol, timeframe, n_candles):
    try:
        current_price = get_current_price(symbol)
        if current_price == 0:
            return None, "Zero price"

        # Get more candles than needed to account for excluding recent ones
        candles = get_candles(symbol, timeframe, n_candles + 2)
        if len(candles) < n_candles + 2:
            return None, "Insufficient candle data"

        # Exclude the last two candles from the reference candle search
        analysis_candles = candles[:-2]
        recent_candles = candles[-2:]  # Last two candles for validation

        market_ok, market_data = market_analyzer.analyze_market_conditions(analysis_candles)
        if not market_ok:
            return None, f"Poor market conditions: {market_data['score']}%"

        # Find reference candle excluding last two candles
        cr = max(analysis_candles, key=lambda x: x['close'] - x['open'])
        if cr['close'] <= cr['open']:
            return None, "No valid reference candle"

        moyenne = (cr['close'] + cr['open']) / 2
        cr_index = analysis_candles.index(cr)

        # Validate subsequent candles including the recent ones
        subsequent_candles = analysis_candles[cr_index+1:] + recent_candles
        if not subsequent_candles:
            return None, "No subsequent candles"

        valid = all(
            moyenne <= c['open'] <= cr['close'] and 
            moyenne <= c['close'] <= cr['close'] * 1.1 and  # Allow 10% above CR close
            c['volume'] >= cr['volume'] * 0.5  # Volume should be at least 50% of CR
            for c in subsequent_candles
        )

        if not valid:
            return None, "Invalid candle pattern"

        whale_data = detect_whale_activity(symbol)
        
        # Calculate trend strength
        closes = [c['close'] for c in subsequent_candles]
        trend_strength = sum(closes[i] >= closes[i-1] for i in range(1, len(closes))) / (len(closes) - 1)

        return {
            'symbol': symbol,
            'cr_open': cr['open'],
            'cr_close': cr['close'],
            'current_price': current_price,
            'whale_activity': whale_data,
            'market_conditions': market_data,
            'trend_strength': trend_strength,
            'cr_index': cr_index,
            'candles_analyzed': len(analysis_candles),
            'timestamp': datetime.now(UTC).isoformat()
        }, "Success"

    except Exception as e:
        return None, f"Error: {str(e)}"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/profitable_pairs')
def profitable_pairs():
    return render_template('profitable_pairs.html')

@app.route('/profitable_pairs_stream')
def profitable_pairs_stream():
    def generate():
        pairs = deque(get_active_pairs())
        analyzed_pairs = set()
        
        while True:
            try:
                if not pairs:
                    pairs.extend(get_active_pairs())
                    analyzed_pairs.clear()
                    yield f"data: {json.dumps({'refresh': True, 'timestamp': datetime.now(UTC).isoformat()})}\n\n"
                    time.sleep(5)
                    continue

                symbol = pairs.popleft()
                if symbol in analyzed_pairs:
                    time.sleep(0.1)
                    continue

                timeframe = request.args.get('timeframe', '1h')
                n_candles = int(request.args.get('n_candles', 10))
                
                pair_data, message = analyze_pair(symbol, timeframe, n_candles)
                
                # Always yield data about analyzed pair
                analyzed_pairs.add(symbol)
                
                if pair_data:
                    yield f"data: {json.dumps({
                        'symbol': symbol,
                        'data': pair_data,
                        'timestamp': datetime.now(UTC).isoformat(),
                        'status': 'success',
                        'message': message
                    })}\n\n"
                
                time.sleep(RATE_LIMIT_DELAY)

            except Exception as e:
                logging.error(f"Error in stream for {symbol if 'symbol' in locals() else 'unknown'}: {str(e)}")
                yield f"data: {json.dumps({
                    'error': str(e),
                    'symbol': symbol if 'symbol' in locals() else 'unknown',
                    'timestamp': datetime.now(UTC).isoformat(),
                    'status': 'error'
                })}\n\n"
                time.sleep(1)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )

@app.route('/stream')
def data_stream():
    def generate():
        pairs = deque(get_active_pairs())
        while True:
            try:
                if not pairs:
                    pairs.extend(get_active_pairs())
                    yield f"data: {json.dumps({'refresh': True, 'timestamp': datetime.now(UTC).isoformat()})}\n\n"
                    time.sleep(5)
                    continue

                symbol = pairs.popleft()
                current_price = get_current_price(symbol)
                
                if current_price == 0:
                    pairs.append(symbol)
                    continue

                candles = get_candles(symbol, '1h', 3)
                if not candles:
                    pairs.append(symbol)
                    continue
                
                whale_data = detect_whale_activity(symbol)

                data = {
                    'symbol': symbol,
                    'price': current_price,
                    'candles': candles,
                    'whale_activity': whale_data,
                    'timestamp': datetime.now(UTC).isoformat(),
                    'status': 'success'
                }
                
                yield f"data: {json.dumps(data)}\n\n"
                time.sleep(RATE_LIMIT_DELAY)

            except Exception as e:
                logging.error(f"Error in stream for {symbol if 'symbol' in locals() else 'unknown'}: {str(e)}")
                yield f"data: {json.dumps({
                    'error': str(e),
                    'symbol': symbol if 'symbol' in locals() else 'unknown',
                    'timestamp': datetime.now(UTC).isoformat(),
                    'status': 'error'
                })}\n\n"
                time.sleep(1)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )

@app.route('/api/market_data/<symbol>')
def get_market_data(symbol):
    try:
        timeframe = request.args.get('timeframe', '1h')
        n_candles = int(request.args.get('n_candles', 24))
        
        candles = get_candles(symbol, timeframe, n_candles)
        if not candles:
            return jsonify({'error': 'No candle data available'}), 404
            
        whale_data = detect_whale_activity(symbol)
        market_ok, market_data = market_analyzer.analyze_market_conditions(candles)
        
        current_price = get_current_price(symbol)
        
        return jsonify({
            'symbol': symbol,
            'current_price': current_price,
            'market_conditions': market_data,
            'whale_activity': whale_data,
            'candles': candles[-10:],
            'timestamp': datetime.now(UTC).isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/pairs/status')
def get_pairs_status():
    return jsonify({
        'active_pairs': len(get_active_pairs()),
        'cache_size': len(pair_analysis_cache),
        'timestamp': datetime.now(UTC).isoformat()
    })

def cleanup_cache():
    while True:
        try:
            current_time = time.time()
            with analysis_lock:
                expired_keys = [
                    key for key, (timestamp, _) in pair_analysis_cache.items()
                    if current_time - timestamp > cache_timeout
                ]
                for key in expired_keys:
                    del pair_analysis_cache[key]
        except Exception as e:
            logging.error(f"Cache cleanup error: {str(e)}")
        time.sleep(60)

def start_background_tasks():
    cleanup_thread = Thread(target=cleanup_cache, daemon=True)
    cleanup_thread.start()

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({'error': 'Rate limit exceeded'}), 429

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(404)
def not_found_error(e):
    return jsonify({'error': 'Resource not found'}), 404

if __name__ == '__main__':
    start_background_tasks()
    app.run(debug=True, use_reloader=False)