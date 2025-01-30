# Crypto Trading Pattern Scanner 📊

![Trading Pattern Scanner](https://img.shields.io/badge/Trading-Pattern%20Scanner-blue)
![Python](https://img.shields.io/badge/Python-3.8%2B-brightgreen)
![Flask](https://img.shields.io/badge/Flask-2.0%2B-lightgrey)
![License](https://img.shields.io/badge/License-MIT-yellow)

A real-time cryptocurrency trading pattern scanner that analyzes Binance USDT pairs for profitable trading setups using advanced technical analysis and whale activity detection.

## 🚀 Features

### 📈 Technical Analysis
- Real-time market data monitoring
- Advanced candlestick pattern recognition
- Reference candle identification (excluding last 2 candles)
- Bollinger Bands analysis
- RSI (Relative Strength Index) calculations
- MACD (Moving Average Convergence Divergence) signals
- Volume trend analysis

### 🐋 Whale Activity Detection
- Large trade monitoring
- Order book analysis
- Whale activity scoring system
- Real-time whale movement alerts

### 📊 Market Analysis
- Dynamic market scoring system
- Multi-timeframe analysis
- Trend strength calculation
- Volume profile analysis
- Market condition assessment

### 💻 Technical Features
- Real-time data streaming
- Efficient caching system
- Rate limit handling
- Background tasks management
- RESTful API endpoints
- Server-Sent Events (SSE) for live updates

## 🛠 Installation

1. Clone the repository:
```bash
git clone https://github.com/HAMMOUDAmustaphaahmed/trader.git
cd trader
Create a virtual environment:
bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
Install required packages:
bash
pip install -r requirements.txt
🚀 Usage
Start the application:
bash
python app.py
Open your browser and navigate to:
Code
http://127.0.0.1:5000
📱 Interface Guide
Main Dashboard (/)
Real-time price updates
Active market pairs
Current market status
Profitable Pairs (/profitable_pairs)
Live scanning of trading opportunities
Pattern recognition results
Whale activity indicators
Market condition scores
Market Data API (/api/market_data/)
Get detailed market analysis for specific pairs:

bash
curl http://127.0.0.1:5000/api/market_data/BTCUSDT
📊 Trading Pattern Criteria
The scanner identifies profitable patterns based on:

Reference Candle (CR) Analysis

Must be a strong bullish candle
Cannot be one of the last two candles
Must have significant volume
Pattern Validation

Subsequent candles must maintain price above CR moyenne
Volume requirements must be met
Trend strength must be confirmed
Market Conditions

RSI between 30-70 preferred
Positive MACD signals
Price within Bollinger Bands
Strong volume trends
🔧 Configuration
Key parameters can be adjusted in app.py:

Python
WHALE_THRESHOLD_LARGE = 100000
WHALE_THRESHOLD_SMALL = 100
API_TIMEOUT = 10
MAX_RETRIES = 3
RATE_LIMIT_DELAY = 0.1
📈 Performance Optimization
The application includes:

Efficient caching system
Background cleanup tasks
Rate limiting protection
Request retry mechanism
🔒 Security Features
Rate limiting protection
Error handling
Input validation
Secure API endpoints
🤝 Contributing
Fork the repository
Create your feature branch
Commit your changes
Push to the branch
Create a Pull Request
📝 License
This project is licensed under the MIT License - see the LICENSE file for details.

⚠️ Disclaimer
This tool is for educational purposes only. Cryptocurrency trading carries significant risks. Always do your own research and never trade more than you can afford to lose.

📧 Contact
GitHub: @HAMMOUDAmustaphaahmed
Remember to ⭐ this repository if you find it helpful!
```