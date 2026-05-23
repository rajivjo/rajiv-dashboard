# app.py
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import requests

# Import utiliti matematik sedia ada dalam projek awak
from utils.data_fetcher import get_options_data
from utils.math_engine import calculate_gamma, calculate_vanna, calculate_charm, find_gamma_flip

# =====================================================================
# ENJIN TELEGRAM ALERT (ANTI-DUPLICATE GLOBAL MATRIX)
# =====================================================================
LAST_ALERTS = {}

def send_telegram_alert(token, chat_id, message, alert_key, current_strikes):
    global LAST_ALERTS
    if LAST_ALERTS.get(alert_key) != current_strikes:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
            res = requests.post(url, json=payload, timeout=5)
            if res.status_code == 200:
                LAST_ALERTS[alert_key] = current_strikes
        except Exception as e:
            print(f"Telegram Alert Gagal: {e}")

# =====================================================================
# FUNGSI DINAMIK UNTUK KESAN MONTHLY EXPIRY
# =====================================================================
def get_expiry_tag(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        year, month = dt.year, dt.month
        first_day = datetime(year, month, 1)
        first_friday = first_day + timedelta(days=(4 - first_day.weekday() + 7) % 7)
        third_friday = first_friday + timedelta(weeks=2)

        if month == 6 and third_friday.day == 19:
            monthly_expiry_day = 18
        else:
            monthly_expiry_day = third_friday.day

        return f"{date_str} (m)" if dt.day == monthly_expiry_day else f"{date_str} (w)"
    except:
        return date_str

# =====================================================================
# FUNGSI PROSES MATRIX (UNTUK DIPANGGIL OLEH FASTAPI)
# =====================================================================
def calculate_market_matrix(ticker_symbol, risk_free_rate=0.04, spot_range_pct=7, alert_on=False):
    try:
        cached_data = get_options_data(ticker_symbol)
        spot_price = cached_data['spot_price']
        expirations = cached_data['expirations']

        if not expirations:
            return {"error": "Tiada data opsyen ditemui."}

        # Secara default, ambil tarikh tamat opsyen yang pertama (terdekat) seperti kod asal
        selected_expiries = [expirations[0]] 

        ticker = yf.Ticker(ticker_symbol)
        all_calls_list = []
        all_puts_list = []
        t_total = 0
        today = datetime.now().date()

        for expiry in selected_expiries:
            expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
            t_expiry = max((expiry_date - today).days, 0.5) / 365.0
            t_total += t_expiry

            opt_chain = ticker.option_chain(expiry)
            c_df = opt_chain.calls[['strike', 'openInterest', 'volume', 'impliedVolatility']].copy()
            p_df = opt_chain.puts[['strike', 'openInterest', 'volume', 'impliedVolatility']].copy()

            all_calls_list.append(c_df)
            all_puts_list.append(p_df)
            
        t = t_total / len(selected_expiries)

        calls_combined = pd.concat(all_calls_list).groupby('strike').agg({'openInterest': 'sum', 'volume': 'sum', 'impliedVolatility': 'mean'}).reset_index().rename(columns={'strike': 'Strike', 'volume': 'Call_Vol', 'openInterest': 'Call_OI', 'impliedVolatility': 'Call_IV'})
        puts_combined = pd.concat(all_puts_list).groupby('strike').agg({'openInterest': 'sum', 'volume': 'sum', 'impliedVolatility': 'mean'}).reset_index().rename(columns={'strike': 'Strike', 'volume': 'Put_Vol', 'openInterest': 'Put_OI', 'impliedVolatility': 'Put_IV'})

        lower_bound = spot_price * (1 - (spot_range_pct / 100))
        upper_bound = spot_price * (1 + (spot_range_pct / 100))

        strikes = sorted(list(set(calls_combined['Strike']).union(set(puts_combined['Strike']))))
        df_gex = pd.DataFrame({'Strike': strikes})
        df_gex = df_gex[(df_gex['Strike'] >= lower_bound) & (df_gex['Strike'] <= upper_bound)].copy()
        df_gex = df_gex.merge(calls_combined, on='Strike', how='left').merge(puts_combined, on='Strike', how='left').fillna(0)

        if df_gex.empty:
            return {"error": "Tiada data ditemui pada julat harga opsyen ini."}

        # Kira Greeks Exposures
        df_gex['Call_Gamma'] = df_gex.apply(lambda r: calculate_gamma(spot_price, r['Strike'], t, risk_free_rate, r['Call_IV'] if r['Call_IV'] > 0.01 else 0.15), axis=1)
        df_gex['Put_Gamma'] = df_gex.apply(lambda r: calculate_gamma(spot_price, r['Strike'], t, risk_free_rate, r['Put_IV'] if r['Put_IV'] > 0.01 else 0.15), axis=1)
        df_gex['Call_Vanna'] = df_gex.apply(lambda r: calculate_vanna(spot_price, r['Strike'], t, risk_free_rate, r['Call_IV'] if r['Call_IV'] > 0.01 else 0.15, "call"), axis=1)
        df_gex['Put_Vanna'] = df_gex.apply(lambda r: calculate_vanna(spot_price, r['Strike'], t, risk_free_rate, r['Put_IV'] if r['Put_IV'] > 0.01 else 0.15, "put"), axis=1)
        df_gex['Call_Charm'] = df_gex.apply(lambda r: calculate_charm(spot_price, r['Strike'], t, risk_free_rate, r['Call_IV'] if r['Call_IV'] > 0.01 else 0.15, "call"), axis=1)
        df_gex['Put_Charm'] = df_gex.apply(lambda r: calculate_charm(spot_price, r['Strike'], t, risk_free_rate, r['Put_IV'] if r['Put_IV'] > 0.01 else 0.15, "put"), axis=1)

        # Convert ke Juta (M)
        df_gex['Call_GEX_M'] = (df_gex['Call_Gamma'] * df_gex['Call_OI'] * (spot_price ** 2) * 0.01) / 1_000_000
        df_gex['Put_GEX_M'] = (df_gex['Put_Gamma'] * df_gex['Put_OI'] * (spot_price ** 2) * 0.01 * (-1)) / 1_000_000
        df_gex['Net_GEX_M'] = df_gex['Call_GEX_M'] + df_gex['Put_GEX_M']
        df_gex['Absolute_GEX_M'] = df_gex['Call_GEX_M'].abs() + df_gex['Put_GEX_M'].abs()
        df_gex['Net_VEX_M'] = (df_gex['Call_Vanna'] * df_gex['Call_OI'] * spot_price * 0.01 + df_gex['Put_Vanna'] * df_gex['Put_OI'] * spot_price * 0.01) / 1_000_000
        df_gex['Net_CEX_M'] = (df_gex['Call_Charm'] * df_gex['Call_OI'] * spot_price + df_gex['Put_Charm'] * df_gex['Put_OI'] * spot_price) / 1_000_000

        gamma_flip_strike = find_gamma_flip(df_gex.rename(columns={'Net_GEX_M': 'Net_GEX'}))
        gamma_wall_strike = df_gex.loc[df_gex['Absolute_GEX_M'].idxmax()]['Strike']

        # Sediakan Simulasi Charm Continuous Curve
        spot_sensi_grid = np.linspace(df_gex['Strike'].min(), df_gex['Strike'].max(), 100)
        continuous_cex_values = []
        for s_v in spot_sensi_grid:
            total_charm_at_sv = 0
            for _, row in df_gex.iterrows():
                ch_c = calculate_charm(s_v, row['Strike'], t, risk_free_rate, row['Call_IV'] if row['Call_IV'] > 0.01 else 0.15, "call")
                ch_p = calculate_charm(s_v, row['Strike'], t, risk_free_rate, row['Put_IV'] if row['Put_IV'] > 0.01 else 0.15, "put")
                total_charm_at_sv += (ch_c * row['Call_OI'] * spot_price) + (ch_p * row['Put_OI'] * spot_price)
            continuous_cex_values.append(total_charm_at_sv / 1_000_000)

        # Sorting untuk Rank
        vanna_desc = df_gex.sort_values(by='Net_VEX_M', ascending=False).reset_index(drop=True)
        vanna_asc = df_gex.sort_values(by='Net_VEX_M', ascending=True).reset_index(drop=True)
        
        # Sediakan Data Akhir JSON untuk dihantar ke Frontend
        payload_result = {
            "spot_price": round(spot_price, 2),
            "total_net_gex": round(df_gex['Net_GEX_M'].sum(), 2),
            "gamma_wall": round(gamma_wall_strike, 2),
            "gamma_flip": round(gamma_flip_strike, 2) if gamma_flip_strike else "N/A",
            "total_net_vanna": round(df_gex['Net_VEX_M'].sum(), 2),
            "total_net_charm": round(df_gex['Net_CEX_M'].sum(), 2),
            "vanna_ranks": {
                "pos_1": {"strike": round(vanna_desc.loc[0, 'Strike'], 2), "val": round(vanna_desc.loc[0, 'Net_VEX_M'], 3)},
                "pos_2": {"strike": round(vanna_desc.loc[1, 'Strike'], 2), "val": round(vanna_desc.loc[1, 'Net_VEX_M'], 3)},
                "neg_1": {"strike": round(vanna_asc.loc[0, 'Strike'], 2), "val": round(vanna_asc.loc[0, 'Net_VEX_M'], 3)},
                "neg_2": {"strike": round(vanna_asc.loc[1, 'Strike'], 2), "val": round(vanna_asc.loc[1, 'Net_VEX_M'], 3)}
            },
            "raw_chart_data": {
                "strikes": df_gex['Strike'].tolist(),
                "net_gex": df_gex['Net_GEX_M'].tolist(),
                "net_vanna": df_gex['Net_VEX_M'].tolist(),
                "price_grid": spot_sensi_grid.tolist(),
                "continuous_cex": continuous_cex_values
            }
        }
        return payload_result
    except Exception as e:
        return {"error": str(e)}
