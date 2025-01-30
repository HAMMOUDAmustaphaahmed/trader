from flask import Flask, render_template, Response
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
HISTORICAL_DAYS = 180
WHALE_THRESHOLD = 100000
MIN_ORDERBOOK_SUM = 1e-9
REFRESH_INTERVAL = 600
API_TIMEOUT = 10
CLUSTER_THRESHOLD = 0.1

WHALE_SIGNAL_WEIGHTS = {
    'large_tx': 0.3,
    'order_clusters': 0.25,
    'depth_changes': 0.2,
    'funding_anomaly': 0.15,
    'social_spike': 0.1
}

class DepthAnalyzer:
    def __init__(self):
        self.prev_depth = {'bids': [], 'asks': []}
    
    def analyze_depth_change(self, current_depth):
        try:
            current_bids = [(safe_convert(b[0]), safe_convert(b[1])) for b in current_depth.get('bids', [])]
            current_asks = [(safe_convert(a[0]), safe_convert(a[1])) for a in current_depth.get('asks', [])]
            
            bid_change = self._calculate_depth_change(self.prev_depth['bids'], current_bids)
            ask_change = self._calculate_depth_change(self.prev_depth['asks'], current_asks)
            
            self.prev_depth = {'bids': current_bids, 'asks': current_asks}
            return {'bid_change': bid_change, 'ask_change': ask_change}
        except Exception as e:
            logging.error(f"Depth analysis error: {str(e)}")
            return {'bid_change': 0, 'ask_change': 0}

    def _calculate_depth_change(self, prev, current):
        try:
            prev_total = sum(q for _, q in prev)
            current_total = sum(q for _, q in current)
            return (current_total - prev_total) / prev_total if prev_total > 0 else 0
        except:
            return 0

depth_analyzer = DepthAnalyzer()

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
            if s.get('symbol', '').endswith('USDT') and 
            not any(e in s['symbol'] for e in ['BTC', 'BNB', 'ETH'])
        ]
    except Exception as e:
        logging.error(f"Error fetching pairs: {str(e)}")
        return []

def detect_order_clusters(order_book):
    try:
        bids = pd.DataFrame(
            [(safe_convert(b[0]), safe_convert(b[1])) for b in order_book.get('bids', [])],
            columns=['price', 'quantity']
        )
        asks = pd.DataFrame(
            [(safe_convert(a[0]), safe_convert(a[1])) for a in order_book.get('asks', [])],
            columns=['price', 'quantity']
        )

        def cluster_orders(df):
            clusters = []
            current_cluster = []
            prev_price = None
            
            for _, row in df.iterrows():
                if prev_price and abs(row['price'] - prev_price) < CLUSTER_THRESHOLD:
                    current_cluster.append(row)
                else:
                    if current_cluster:
                        clusters.append(pd.DataFrame(current_cluster))
                    current_cluster = [row]
                prev_price = row['price']
            return [c['quantity'].sum() for c in clusters if not c.empty]
        
        return {
            'bid_clusters': cluster_orders(bids),
            'ask_clusters': cluster_orders(asks)
        }
    except Exception as e:
        logging.error(f"Order cluster error: {str(e)}")
        return {'bid_clusters': [], 'ask_clusters': []}

def detect_liquidity_snipe(trades):
    try:
        large_trades = [
            t for t in trades 
            if safe_convert(t.get('p')) * safe_convert(t.get('q')) > 100000
        ]
        
        if not large_trades:
            return {'snipe_detected': False, 'total_size': 0}
        
        price_levels = len({round(safe_convert(t['p']), 4) for t in large_trades})
        time_window = safe_convert(trades[-1].get('T', 0)) - safe_convert(trades[0].get('T', 0))
        
        return {
            'snipe_detected': price_levels > 3 and time_window < 5000,
            'total_size': sum(safe_convert(t.get('p')) * safe_convert(t.get('q')) for t in large_trades)
        }
    except:
        return {'snipe_detected': False, 'total_size': 0}

def detect_funding_anomaly(symbol):
    try:
        futures_data = requests.get(
            f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}",
            timeout=API_TIMEOUT
        ).json()
        
        return {
            'funding_anomaly': abs(safe_convert(futures_data.get('lastFundingRate', 0))) > 0.0005,
            'funding_rate': safe_convert(futures_data.get('lastFundingRate', 0))
        }
    except:
        return {'funding_anomaly': False, 'funding_rate': 0}

def calculate_whale_score(signals):
    try:
        return sum(
            min(max(signals[st], 0), 1) * weight 
            for st, weight in WHALE_SIGNAL_WEIGHTS.items()
        )
    except:
        return 0

def analyze_pair(symbol):
    try:
        # Get core data
        ticker = requests.get(
            f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}",
            timeout=API_TIMEOUT
        ).json()
        
        trades = requests.get(
            "https://api.binance.com/api/v3/aggTrades",
            params={'symbol': symbol, 'limit': 100},
            timeout=API_TIMEOUT
        ).json()
        
        order_book = requests.get(
            f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit=1000",
            timeout=API_TIMEOUT
        ).json()

        # Initialize signals
        signals = {
            'large_tx': 0,
            'order_clusters': 0,
            'depth_changes': 0,
            'funding_anomaly': 0,
            'social_spike': 0
        }

        # 1. Large transactions
        large_tx_count = sum(1 for tx in trades 
                           if safe_convert(tx.get('p')) * safe_convert(tx.get('q')) > WHALE_THRESHOLD)
        signals['large_tx'] = min(large_tx_count / 10, 1)

        # 2. Order clusters
        clusters = detect_order_clusters(order_book)
        total_clusters = len(clusters['bid_clusters']) + len(clusters['ask_clusters'])
        signals['order_clusters'] = min(total_clusters / 5, 1)

        # 3. Market depth changes
        depth_changes = depth_analyzer.analyze_depth_change(order_book)
        signals['depth_changes'] = (abs(depth_changes['bid_change']) + 
                                  abs(depth_changes['ask_change'])) / 2

        # 4. Funding rate anomaly
        funding_data = detect_funding_anomaly(symbol)
        signals['funding_anomaly'] = 1 if funding_data['funding_anomaly'] else 0

        # Calculate final score
        whale_score = calculate_whale_score(signals)
        alert = ""
        if whale_score > 0.7:
            alert = "🐋 STRONG WHALE ACTIVITY"
        elif whale_score > 0.4:
            alert = "⚠️ Moderate activity"

        return {
            'symbol': symbol,
            'price': safe_convert(ticker.get('lastPrice')),
            'whale_score': round(whale_score * 100, 1),
            'alert': alert,
            'details': {
                'large_tx': large_tx_count,
                'order_clusters': total_clusters,
                'funding_rate': funding_data['funding_rate'],
                'depth_change': round((depth_changes['bid_change'] + 
                                     depth_changes['ask_change']) * 100, 1)
            }
        }

    except Exception as e:
        logging.error(f"Analysis failed for {symbol}: {str(e)}", exc_info=True)
        return None

@app.route('/stream')
def data_stream():
    def generate():
        while True:
            symbols = get_active_pairs()
            for symbol in symbols:
                if result := analyze_pair(symbol):
                    yield f"data: {json.dumps(result)}\n\n"
                time.sleep(0.5)
            time.sleep(REFRESH_INTERVAL)
    return Response(generate(), mimetype="text/event-stream")

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)