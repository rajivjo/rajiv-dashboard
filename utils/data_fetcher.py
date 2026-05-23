import streamlit as st
import yfinance as yf
from datetime import datetime

@st.cache_data(ttl=3600) # ttl=3600 bermaksud cache selama 1 jam
def get_options_data(ticker_symbol):
    """
    Menarik data opsyen menggunakan cache built-in Streamlit.
    Tidak perlukan folder 'data_cache' atau fail 'pickle'.
    """
    ticker = yf.Ticker(ticker_symbol)
    
    # Ambil harga spot
    try:
        spot_price = ticker.info.get('regularMarketPrice') or ticker.info.get('currentPrice')
        if spot_price is None:
            spot_price = ticker.history(period="1d")['Close'].iloc[-1]
    except Exception:
        spot_price = ticker.history(period="1d")['Close'].iloc[-1]
        
    expirations = ticker.options
    
    return {
        'spot_price': spot_price,
        'expirations': expirations,
        'timestamp': datetime.now()
    }
