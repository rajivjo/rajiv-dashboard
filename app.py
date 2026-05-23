import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import yfinance as yf
from datetime import datetime, timedelta
from utils.data_fetcher import get_options_data
from utils.math_engine import calculate_gamma, calculate_vanna, calculate_charm, find_gamma_flip

# CONFIG HALAMAN DASHBOARD
st.set_page_config(page_title="Institutional GEX, VEX & CEX Dashboard", layout="wide")
st.title("📊 Rajiv Exposure Matrix")

# FUNGSI DINAMIK UNTUK KESAN MONTHLY EXPIRY (TERMASUK JIKA JUMAAT CUTI UMUM)
def get_expiry_tag(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        year, month = dt.year, dt.month
        
        # Cari hari Jumaat pertama dalam bulan tersebut
        first_day = datetime(year, month, 1)
        first_friday = first_day + timedelta(days=(4 - first_day.weekday() + 7) % 7)
        
        # Jumaat Ketiga (Standard Monthly Expiry) biasanya ialah first_friday + 2 minggu
        third_friday = first_friday + timedelta(weeks=2)
        
        # Khas untuk kes Juneteenth / Cuti Pasaran: Jika Jumaat Ketiga adalah Cuti, Expiry diganjak ke Khamis (18hb)
        if month == 6 and third_friday.day == 19:
            monthly_expiry_day = 18  # Khamis
        else:
            monthly_expiry_day = third_friday.day
            
        if dt.day == monthly_expiry_day:
            return f"{date_str} (m)"
        else:
            return f"{date_str} (w)"
    except:
        return date_str

# INPUT SIDEBAR
st.sidebar.header("Tetapan Parameter")
ticker_symbol = st.sidebar.text_input("Simbol Saham / ETF (US):", value="GLD").upper().strip()
risk_free_rate = st.sidebar.number_input("Risk-Free Rate (r):", value=0.04, step=0.01)
spot_range_pct = st.sidebar.slider("Julat Strike dari Harga Spot (%):", min_value=5, max_value=30, value=7)

if ticker_symbol:
    with st.spinner(f"Memproses data bagi {ticker_symbol}..."):
        try:
            cached_data = get_options_data(ticker_symbol)
            spot_price = cached_data['spot_price']
            expirations = cached_data['expirations']
            
            st.sidebar.metric(label=f"Harga Semasa ({ticker_symbol})", value=f"${spot_price:,.2f}")
            
            if not expirations:
                st.error("Tiada data opsyen ditemui.")
                st.stop()
                
            # 🟢 BINA PEMETAAN TAG TANPA MENGUBAH STRUKTUR ASAL EXPIRATIONS
            expiry_mapping = {get_expiry_tag(d): d for d in expirations}
            display_options = list(expiry_mapping.keys())
            
            # 🟢 MULTISELECT AGREGAT
            selected_display_expiries = st.sidebar.multiselect(
                "Pilih Tarikh Tamat Opsyen (Boleh Pilih Banyak):", 
                options=display_options,
                default=[display_options[0]] if display_options else None
            )
            
            if not selected_display_expiries:
                st.warning("Sila pilih sekurang-kurangnya satu tarikh tamat opsyen.")
                st.stop()
                
            # Tukar semula string paparan ber-tag kepada tarikh asal untuk kegunaan yfinance
            selected_expiries = [expiry_mapping[tag] for tag in selected_display_expiries]
            
            ticker = yf.Ticker(ticker_symbol)
            all_calls_list = []
            all_puts_list = []
            
            # Kira purata baki masa (t) berasaskan pilihan expiries yang dipilih tanpa ubah formula asal
            today = datetime.now().date()
            t_total = 0
            
            # Buat cubaan tarik data baru
            data_fetch_success = True
            try:
                for expiry in selected_expiries:
                    expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
                    t_expiry = max((expiry_date - today).days, 0.5) / 365.0
                    t_total += t_expiry
                    
                    opt_chain = ticker.option_chain(expiry)
                    
                    c_df = opt_chain.calls[['strike', 'openInterest', 'volume', 'impliedVolatility']].copy()
                    p_df = opt_chain.puts[['strike', 'openInterest', 'volume', 'impliedVolatility']].copy()
                    
                    # 🛡️ BAJU HUJAN PEMBERSIHAN DATA (ANTI-CRASH)
                    if not c_df.empty:
                        c_df['impliedVolatility'] = c_df['impliedVolatility'].fillna(0.15)
                        c_df.loc[c_df['impliedVolatility'] <= 0.01, 'impliedVolatility'] = 0.15
                        c_df['openInterest'] = c_df['openInterest'].fillna(0)
                        c_df['volume'] = c_df['volume'].fillna(0)

                    if not p_df.empty:
                        p_df['impliedVolatility'] = p_df['impliedVolatility'].fillna(0.15)
                        p_df.loc[p_df['impliedVolatility'] <= 0.01, 'impliedVolatility'] = 0.15
                        p_df['openInterest'] = p_df['openInterest'].fillna(0)
                        p_df['volume'] = p_df['volume'].fillna(0)
                    
                    all_calls_list.append(c_df)
                    all_puts_list.append(p_df)
                t = t_total / len(selected_expiries)
            except Exception:
                data_fetch_success = False

            df_gex = pd.DataFrame()

            # Process data baru jika proses tarik dari yfinance tadi berjaya
            if data_fetch_success and all_calls_list and all_puts_list:
                calls_combined = pd.concat(all_calls_list).groupby('strike').agg({
                    'openInterest': 'sum',
                    'volume': 'sum',
                    'impliedVolatility': 'mean'
                }).reset_index().rename(columns={'strike': 'Strike', 'volume': 'Call_Vol', 'openInterest': 'Call_OI', 'impliedVolatility': 'Call_IV'})
                
                puts_combined = pd.concat(all_puts_list).groupby('strike').agg({
                    'openInterest': 'sum',
                    'volume': 'sum',
                    'impliedVolatility': 'mean'
                }).reset_index().rename(columns={'strike': 'Strike', 'volume': 'Put_Vol', 'openInterest': 'Put_OI', 'impliedVolatility': 'Put_IV'})
                
                lower_bound = spot_price * (1 - (spot_range_pct / 100))
                upper_bound = spot_price * (1 + (spot_range_pct / 100))
                
                strikes = sorted(list(set(calls_combined['Strike']).union(set(puts_combined['Strike']))))
                df_gex = pd.DataFrame({'Strike': strikes})
                df_gex = df_gex[(df_gex['Strike'] >= lower_bound) & (df_gex['Strike'] <= upper_bound)].copy()
                
                df_gex = df_gex.merge(calls_combined, on='Strike', how='left')
                df_gex = df_gex.merge(puts_combined, on='Strike', how='left')
                df_gex = df_gex.fillna(0)
                df_gex = df_gex[(df_gex['Call_OI'] > 0) | (df_gex['Put_OI'] > 0) | (df_gex['Call_Vol'] > 0) | (df_gex['Put_Vol'] > 0)].copy()

            # 🔵 ENGINE FREEZE DATA (SESSION STATE)
            if not df_gex.empty:
                st.session_state['frozen_df_gex'] = df_gex.copy()
                st.session_state['frozen_t'] = t
                st.session_state['frozen_spot'] = spot_price
            elif 'frozen_df_gex' in st.session_state:
                df_gex = st.session_state['frozen_df_gex'].copy()
                t = st.session_state['frozen_t']
                spot_price = st.session_state['frozen_spot']
                st.info("⚠️ Menggunakan data terakhir yang 'Disimpan/Freeze' (yfinance sedang mengalami kelewatan data).")
            else:
                st.warning("Tiada data aktif ditemui untuk setup kali pertama ini. Sila luaskan julat % atau pilih tarikh lain.")
                st.stop()
            
            # PENGIRAAN ENGINE MATEMATIK GREEKS STATIK
            df_gex['Call_Gamma'] = df_gex.apply(lambda r: calculate_gamma(spot_price, r['Strike'], t, risk_free_rate, r['Call_IV'] if r['Call_IV'] > 0.01 else 0.15), axis=1)
            df_gex['Put_Gamma'] = df_gex.apply(lambda r: calculate_gamma(spot_price, r['Strike'], t, risk_free_rate, r['Put_IV'] if r['Put_IV'] > 0.01 else 0.15), axis=1)
            
            df_gex['Call_Vanna'] = df_gex.apply(lambda r: calculate_vanna(spot_price, r['Strike'], t, risk_free_rate, r['Call_IV'] if r['Call_IV'] > 0.01 else 0.15, "call"), axis=1)
            df_gex['Put_Vanna'] = df_gex.apply(lambda r: calculate_vanna(spot_price, r['Strike'], t, risk_free_rate, r['Put_IV'] if r['Put_IV'] > 0.01 else 0.15, "put"), axis=1)
            
            df_gex['Call_Charm'] = df_gex.apply(lambda r: calculate_charm(spot_price, r['Strike'], t, risk_free_rate, r['Call_IV'] if r['Call_IV'] > 0.01 else 0.15, "call"), axis=1)
            df_gex['Put_Charm'] = df_gex.apply(lambda r: calculate_charm(spot_price, r['Strike'], t, risk_free_rate, r['Put_IV'] if r['Put_IV'] > 0.01 else 0.15, "put"), axis=1)
            
            # RAW EXPOSURE STATIK
            df_gex['Call_GEX_Raw'] = df_gex['Call_Gamma'] * df_gex['Call_OI'] * (spot_price ** 2) * 0.01
            df_gex['Put_GEX_Raw'] = df_gex['Put_Gamma'] * df_gex['Put_OI'] * (spot_price ** 2) * 0.01 * (-1)
            df_gex['Net_GEX_Raw'] = df_gex['Call_GEX_Raw'] + df_gex['Put_GEX_Raw']
            
            df_gex['Call_VEX_Raw'] = df_gex['Call_Vanna'] * df_gex['Call_OI'] * spot_price * 0.01
            df_gex['Put_VEX_Raw'] = df_gex['Put_Vanna'] * df_gex['Put_OI'] * spot_price * 0.01
            df_gex['Net_VEX_Raw'] = df_gex['Call_VEX_Raw'] + df_gex['Put_VEX_Raw']
            
            df_gex['Call_CEX_Raw'] = df_gex['Call_Charm'] * df_gex['Call_OI'] * spot_price
            df_gex['Put_CEX_Raw'] = df_gex['Put_Charm'] * df_gex['Put_OI'] * spot_price
            df_gex['Net_CEX_Raw'] = df_gex['Call_CEX_Raw'] + df_gex['Put_CEX_Raw']
            
            df_gex = df_gex.sort_values('Strike').reset_index(drop=True)
            
            # SKALA MILLIONS
            df_gex['Call_GEX_M'] = df_gex['Call_GEX_Raw'] / 1_000_000
            df_gex['Put_GEX_M'] = df_gex['Put_GEX_Raw'] / 1_000_000
            df_gex['Net_GEX_M'] = df_gex['Net_GEX_Raw'] / 1_000_000
            df_gex['Absolute_GEX_M'] = (df_gex['Call_GEX_Raw'].abs() + df_gex['Put_GEX_Raw'].abs()) / 1_000_000
            df_gex['Net_VEX_M'] = df_gex['Net_VEX_Raw'] / 1_000_000
            df_gex['Net_CEX_M'] = df_gex['Net_CEX_Raw'] / 1_000_000
            
            gamma_flip_strike = find_gamma_flip(df_gex.rename(columns={'Net_GEX_M': 'Net_GEX'}))
            gamma_wall_strike = df_gex.loc[df_gex['Absolute_GEX_M'].idxmax()]['Strike']
            
            # PANEL KPI
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total Net GEX", f"${df_gex['Net_GEX_M'].sum():,.2f}M")
            col2.metric("Major Gamma Wall", f"${gamma_wall_strike:,.2f}")
            col3.metric("Total Net Vanna", f"${df_gex['Net_VEX_M'].sum():,.2f}M/1%Δ")
            col4.metric("Total Net Charm (Bleed)", f"${df_gex['Net_CEX_M'].sum():,.2f}M/Hari")
            
            st.markdown("---")
            flip_point = gamma_flip_strike if gamma_flip_strike else spot_price

            # 1. GRAF NET GEX
            st.markdown("<h2 style='color: #00838F; font-family: sans-serif; font-size: 26px; font-weight: bold;'>1. Net Gamma Exposure Profile</h2>", unsafe_allow_html=True)
            fig1 = go.Figure()
            colors_net = ['#0D47A1' if x >= 0 else '#FF9800' for x in df_gex['Net_GEX_M']]
            fig1.add_trace(go.Bar(x=df_gex['Strike'], y=df_gex['Net_GEX_M'], marker_color=colors_net, name="Net GEX", hovertemplate="Net GEX: %{y:.2f}M<extra></extra>"))
            fig1.add_vrect(x0=df_gex['Strike'].min(), x1=flip_point, fillcolor="#FFCDD2", opacity=0.15, line_width=0, layer="below")
            fig1.add_vrect(x0=flip_point, x1=df_gex['Strike'].max(), fillcolor="#C8E6C9", opacity=0.15, line_width=0, layer="below")
            fig1.add_vline(x=spot_price, line_dash="solid", line_color="#212121", line_width=2, annotation_text="Last Price")
            if gamma_flip_strike:
                fig1.add_vline(x=gamma_flip_strike, line_dash="dash", line_color="#2E7D32", line_width=2, annotation_text="Gamma Flip")
            fig1.update_layout(template="plotly_white", height=400, margin=dict(t=30, b=60, l=60, r=40), legend=dict(orientation="h", y=1.05, x=0.5, xanchor="center"), hovermode="x unified")
            fig1.update_xaxes(title_text="Strike Price", tickangle=-45, nticks=24, tickformat=".2f")
            st.plotly_chart(fig1, use_container_width=True)
            
            # 2. GRAF ABSOLUTE GEX
            st.markdown("<h2 style='color: #2E7D32; font-family: sans-serif; font-size: 26px; font-weight: bold;'>2. Absolute Gamma Exposure Profile</h2>", unsafe_allow_html=True)
            fig2 = go.Figure()
            fig2.add_trace(go.Bar(x=df_gex['Strike'], y=df_gex['Call_GEX_M'], marker_color='#0D47A1', name="Call GEX", hovertemplate="Call GEX: %{y:.2f}M<extra></extra>"))
            fig2.add_trace(go.Bar(x=df_gex['Strike'], y=df_gex['Put_GEX_M'], marker_color='#FF9800', name="Put GEX", hovertemplate="Put GEX: %{y:.2f}M<extra></extra>"))
            fig2.add_trace(go.Scatter(x=df_gex['Strike'], y=df_gex['Absolute_GEX_M'], mode='lines+markers', line=dict(color='#2E7D32', width=2.5), marker=dict(size=5), name="Total Absolute", hovertemplate="Total Abs Wall: %{y:.2f}M<extra></extra>"))
            fig2.add_vrect(x0=df_gex['Strike'].min(), x1=flip_point, fillcolor="#FFCDD2", opacity=0.15, line_width=0, layer="below")
            fig2.add_vrect(x0=flip_point, x1=df_gex['Strike'].max(), fillcolor="#C8E6C9", opacity=0.15, line_width=0, layer="below")
            fig2.add_vline(x=spot_price, line_dash="solid", line_color="#212121", line_width=2, annotation_text="Last Price")
            fig2.update_layout(template="plotly_white", height=400, barmode='overlay', margin=dict(t=30, b=60, l=60, r=40), legend=dict(orientation="h", y=1.05, x=0.5, xanchor="center"), hovermode="x unified")
            fig2.update_xaxes(title_text="Strike Price", tickangle=-45, nticks=24, tickformat=".2f")
            st.plotly_chart(fig2, use_container_width=True)

            # 🛡️ ENGINE KHAS 0DTE/1DTE: SIMULASI SPOT PERPULUHAN SEN (FLOATING SPOT SENSI)
            is_short_dated = t <= (2.0 / 365.0) 
            
            if is_short_dated:
                sim_spots = np.arange(spot_price * 0.985, spot_price * 1.015, 0.05)
                sim_charm_list = []
                
                for s_sim in sim_spots:
                    c_total = 0
                    for _, row in df_gex.iterrows():
                        cc = calculate_charm(s_sim, row['Strike'], t, risk_free_rate, row['Call_IV'] if row['Call_IV'] > 0.01 else 0.15, "call")
                        pc = calculate_charm(s_sim, row['Strike'], t, risk_free_rate, row['Put_IV'] if row['Put_IV'] > 0.01 else 0.15, "put")
                        c_total += (cc * row['Call_OI'] + pc * row['Put_OI']) * s_sim
                        
                    sim_charm_list.append(c_total / 1_000_000)

            st.markdown("---")

            # 3. GRAF NET VANNA EXPOSURE (VEX)
            st.markdown("<h2 style='color: #4A148C; font-family: sans-serif; font-size: 26px; font-weight: bold;'>3. Net Vanna Exposure Profile (VEX)</h2>", unsafe_allow_html=True)
            fig3 = go.Figure()
            colors_vex = ['#4A148C' if x >= 0 else '#D32F2F' for x in df_gex['Net_VEX_M']]
            fig3.add_trace(go.Bar(x=df_gex['Strike'], y=df_gex['Net_VEX_M'], marker_color=colors_vex, name="Net Vanna", hovertemplate="Net Vanna: %{y:.2f}M<extra></extra>"))
            fig3.add_vline(x=spot_price, line_dash="solid", line_color="#212121", line_width=2, annotation_text=f"Live Spot: {spot_price:.2f}")
            fig3.update_layout(template="plotly_white", height=400, margin=dict(t=30, b=60, l=60, r=40), legend=dict(orientation="h", y=1.05, x=0.5, xanchor="center"), hovermode="x unified")
            fig3.update_xaxes(title_text="Strike Price", tickangle=-45, nticks=24, tickformat=".2f")
            st.plotly_chart(fig3, use_container_width=True)
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # 4. GRAF NET CHARM EXPOSURE (CEX) - TUNED WITH HIGH-GRAVITY MAGNET PROFILE
            st.markdown("<h2 style='color: #004D40; font-family: sans-serif; font-size: 26px; font-weight: bold;'>4. Net Charm Exposure Profile (CEX / Time Bleed)</h2>", unsafe_allow_html=True)
            if is_short_dated:
                st.caption("⚡ **Mod Interaktif 0DTE/1DTE Profil Magnet Jitu Aktif**")
                fig4 = go.Figure()
                
                # Sembunyikan kesan hover daripada kotak bar
                fig4.add_trace(go.Bar(
                    x=df_gex['Strike'], 
                    y=df_gex['Net_CEX_M'], 
                    marker_color='#B2DFDB', 
                    name="Static Strike CEX (Bar)", 
                    opacity=0.6,
                    hoverinfo='skip'
                ))
                
                # Garisan ombak penentu sensiviti institusi (Continuous Sensi Curve)
                fig4.add_trace(go.Scatter(
                    x=sim_spots, 
                    y=sim_charm_list, 
                    mode='lines+markers',
                    line=dict(color='#004D40', width=3),
                    marker=dict(size=0, color='#004D40', symbol='circle'), 
                    hoverlabel=dict(bgcolor='#004D40'),
                    name="Continuous Sensi Curve (Line)", 
                    hovertemplate="Sim Spot: %{x:.2f}<br>Charm Bleed: %{y:.2f}M<extra></extra>"
                ))
                
                # Diubah semula ke "closest" bagi membolehkan koordinat paksi-Y disedut secara bebas
                hover_mode_selection = "closest"
            else:
                fig4 = go.Figure()
                colors_cex = ['#00695C' if x >= 0 else '#C62828' for x in df_gex['Net_CEX_M']]
                fig4.add_trace(go.Bar(x=df_gex['Strike'], y=df_gex['Net_CEX_M'], marker_color=colors_cex, name="Net Charm"))
                hover_mode_selection = "x unified"
                
            fig4.add_vline(x=spot_price, line_dash="solid", line_color="#212121", line_width=2, annotation_text=f"Live Spot: {spot_price:.2f}")
            
            fig4.update_layout(
                template="plotly_white", height=400, 
                margin=dict(t=30, b=60, l=60, r=40), 
                legend=dict(orientation="h", y=1.05, x=0.5, xanchor="center"),
                hovermode=hover_mode_selection,
                # 🟢 PELUASAN RADIUS TARIKAN GRAVITI MAGNET (250 PIKSEL)
                hoverdistance=250 
            )
            fig4.update_xaxes(
                title_text="Asset Price Grid (Strike / Continuous Spot)", 
                tickangle=-45, nticks=24, tickformat=".2f",
                showspikes=True, spikecolor="#004D40", spikethickness=1, spikemode="across"
            )
            st.plotly_chart(fig4, use_container_width=True)
            
            # SEKSYEN DOWNLOAD
            st.markdown("### 📥 Simpan Data Mentah")
            csv_data = df_gex[['Strike', 'Call_OI', 'Put_OI', 'Call_Vol', 'Put_Vol', 'Net_GEX_M', 'Absolute_GEX_M', 'Net_VEX_M', 'Net_CEX_M']].to_csv(index=False)
            st.download_button(label="Muat Turun Fail CSV", data=csv_data, file_name=f"{ticker_symbol}_aggregated_gex_data.csv", mime="text/csv")
            
        except Exception as e:
            st.error(f"Ralat susunan grafik: {e}")
