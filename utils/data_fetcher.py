import os
import pickle
from datetime import datetime, timedelta
import yfinance as yf

CACHE_DIR = "data_cache"

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

def get_options_data(ticker_symbol):
    """
    Menarik data opsyen lengkap tanpa menyimpan objek yf.Ticker hidup ke dalam pickle.
    """
    cache_file = os.path.join(CACHE_DIR, f"{ticker_symbol}_cache.pkl")
    cache_expiry_duration = timedelta(hours=1)
    
    # 1. Semak jika fail cache sedia ada dan belum tamat tempoh
    if os.path.exists(cache_file):
        try:
            file_time = datetime.fromtimestamp(os.path.getmtime(cache_file))
            if datetime.now() - file_time < cache_expiry_duration:
                with open(cache_file, 'rb') as f:
                    return pickle.load(f)
        except Exception:
            pass # Jika cache gagal dibaca, jalankan proses muat turun baru
                
    # 2. Jika tiada cache, buat perhubungan baru dengan yfinance
    ticker = yf.Ticker(ticker_symbol)
    
    # Ambil harga spot semasa
    try:
        spot_price = ticker.info.get('regularMarketPrice') or ticker.info.get('currentPrice')
        if spot_price is None:
            spot_price = ticker.history(period="1d")['Close'].iloc[-1]
    except Exception:
        spot_price = ticker.history(period="1d")['Close'].iloc[-1]
        
    expirations = ticker.options
    
    # Simpan data mentah ke dalam pickle, BUKAN objek 'ticker'
    data_to_cache = {
        'spot_price': spot_price,
        'expirations': expirations,
        'timestamp': datetime.now()
    }
    
    with open(cache_file, 'wb') as f:
        pickle.dump(data_to_cache, f)
        
    return data_to_cache
