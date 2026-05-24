import sqlite3
import hashlib
import json
import requests
import yfinance as yf
from fastapi import FastAPI, HTTPException
from datetime import datetime, timedelta
import uvicorn

app = FastAPI()

# KONFIGURASI
TIINGO_API_KEY = "TOKEN_TIINGO_ANDA_DI_SINI"
DB_NAME = "rajiv_data.db"
CACHE_EXPIRY_MINUTES = 30

# SETUP DATABASE
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS options_data (
            ticker TEXT PRIMARY KEY,
            data_json TEXT,
            hash TEXT,
            last_updated DATETIME
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_spot_from_tiingo(ticker):
    try:
        url = f"https://api.tiingo.com/iex/{ticker}?token={TIINGO_API_KEY}"
        resp = requests.get(url, timeout=5)
        return float(resp.json()[0]['last']) if resp.status_code == 200 else None
    except:
        return None

@app.get("/get_data/{ticker}")
async def get_data(ticker: str):
    ticker = ticker.upper()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. Semak Database
    cursor.execute("SELECT data_json, last_updated FROM options_data WHERE ticker=?", (ticker,))
    row = cursor.fetchone()
    
    now = datetime.now()
    
    # Jika data wujud dan belum tamat tempoh (30 minit), terus pulangkan
    if row:
        last_updated = datetime.fromisoformat(row[1])
        if now - last_updated < timedelta(minutes=CACHE_EXPIRY_MINUTES):
            conn.close()
            return json.loads(row[0])

    # 2. Jika perlu update, tarik dari Yahoo Finance
    try:
        t = yf.Ticker(ticker)
        expirations = t.options
        
        # Ambil spot dari Tiingo (Laju & Stabil)
        spot = get_spot_from_tiingo(ticker) or t.history(period="1d")['Close'].iloc[-1]
        
        new_data = {"spot_price": float(spot), "expirations": expirations}
        data_json = json.dumps(new_data)
        new_hash = hashlib.md5(data_json.encode()).hexdigest()
        
        # 3. Simpan/Update Database
        cursor.execute('''
            INSERT OR REPLACE INTO options_data (ticker, data_json, hash, last_updated)
            VALUES (?, ?, ?, ?)
        ''', (ticker, data_json, new_hash, now.isoformat()))
        conn.commit()
        conn.close()
        
        return new_data
        
    except Exception as e:
        conn.close()
        # Jika Yahoo gagal, pulangkan data lama (fallback) jika wujud
        if row:
            return json.loads(row[0])
        raise HTTPException(status_code=503, detail=f"Data tidak tersedia: {str(e)}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)