import sys
import os
# Fix: Pastikan Python nampak folder 'utils'
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import requests

# Import utiliti yang sudah dibaiki
from utils.data_fetcher import get_options_data
from utils.math_engine import calculate_gamma, calculate_vanna, calculate_charm, find_gamma_flip

# =====================================================================
# STREAMLIT CONFIG
# =====================================================================
st.set_page_config(page_title="Market Matrix Dashboard", layout="wide")

st.title("📊 Market Matrix Dashboard")

# Input Ticker
ticker_symbol = st.sidebar.text_input("Masukkan Ticker (e.g., SPY, QQQ)", value="SPY")

# =====================================================================
# FUNGSI PROSES MATRIX
# =====================================================================
def calculate_market_matrix(ticker_symbol, risk_free_rate=0.04, spot_range_pct=7):
    try:
        cached_data = get_options_data(ticker_symbol)
        spot_price = cached_data['spot_price']
        expirations = cached_data['expirations']

        if not expirations:
            return {"error": "Tiada data opsyen ditemui."}

        selected_expiries = [expirations[0]] 
        ticker = yf.Ticker(ticker_symbol)
        all_calls_list = []
        all_puts_list = []
        today = datetime.now().date()

        for expiry in selected_expiries:
            expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
            t = max((expiry_date - today).days, 0.5) / 365.0
            
            opt_chain = ticker.option_chain(expiry)
            c_df = opt_chain.calls[['strike', 'openInterest', 'volume', 'impliedVolatility']].copy()
            p_df = opt_chain.puts[['strike', 'openInterest', 'volume', 'impliedVolatility']].copy()

            all_calls_list.append(c_df)
            all_puts_list.append(p_df)

        calls_combined = pd.concat(all_calls_list).groupby('strike').agg({'openInterest': 'sum', 'volume': 'sum', 'impliedVolatility': 'mean'}).reset_index().rename(columns={'strike': 'Strike', 'volume': 'Call_Vol', 'openInterest': 'Call_OI', 'impliedVolatility': 'Call_IV'})
        puts_combined = pd.concat(all_puts_list).groupby('strike').agg({'openInterest': 'sum', 'volume': 'sum', 'impliedVolatility': 'mean'}).reset_index().rename(columns={'strike': 'Strike', 'volume': 'Put_Vol', 'openInterest': 'Put_OI', 'impliedVolatility': 'Put_IV'})

        lower_bound = spot_price * (1 - (spot_range_pct / 100))
        upper_bound = spot_price * (1 + (spot_range_pct / 100))

        strikes = sorted(list(set(calls_combined['Strike']).union(set(puts_combined['Strike']))))
        df_gex = pd.DataFrame({'Strike': strikes})
        df_gex = df_gex[(df_gex['Strike'] >= lower_bound) & (df_gex['Strike'] <= upper_bound)].copy()
        df_gex = df_gex.merge(calls_combined, on='Strike', how='left').merge(puts_combined, on='Strike', how='left').fillna(0)

        # Kira Greeks
        df_gex['Call_Gamma'] = df_gex.apply(lambda r: calculate_gamma(spot_price, r['Strike'], t, risk_free_rate, r['Call_IV'] if r['Call_IV'] > 0.01 else 0.15), axis=1)
        df_gex['Put_Gamma'] = df_gex.apply(lambda r: calculate_gamma(spot_price, r['Strike'], t, risk_free_rate, r['Put_IV'] if r['Put_IV'] > 0.01 else 0.15), axis=1)
        
        df_gex['Call_GEX_M'] = (df_gex['Call_Gamma'] * df_gex['Call_OI'] * (spot_price ** 2) * 0.01) / 1_000_000
        df_gex['Put_GEX_M'] = (df_gex['Put_Gamma'] * df_gex['Put_OI'] * (spot_price ** 2) * 0.01 * (-1)) / 1_000_000
        df_gex['Net_GEX_M'] = df_gex['Call_GEX_M'] + df_gex['Put_GEX_M']

        return df_gex
    except Exception as e:
        return {"error": str(e)}

# =====================================================================
# PAPARAN DASHBOARD
# =====================================================================
st.write(f"Menjana data untuk: {ticker_symbol}")
data = calculate_market_matrix(ticker_symbol)

if isinstance(data, dict) and "error" in data:
    st.error(data["error"])
else:
    st.success("Data Berjaya Dijana!")
    st.dataframe(data.head())
    st.line_chart(data.set_index('Strike')['Net_GEX_M'])
