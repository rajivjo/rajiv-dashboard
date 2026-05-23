import numpy as np
import scipy.stats as si  # 🟢 Kunci penyelesaian ralat ImportError!

def calculate_gamma(S, K, t, r, sigma):
    """Mengira nilai Gamma opsyen menggunakan model Black-Scholes."""
    if t <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * t) / (sigma * np.sqrt(t))
    return si.norm.pdf(d1) / (S * sigma * np.sqrt(t))

def calculate_vanna(S, K, t, r, sigma, option_type="call"):
    """Mengira nilai Vanna opsyen (dDelta / dSigma)."""
    if t <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * t) / (sigma * np.sqrt(t))
    d2 = d1 - sigma * np.sqrt(t)
    return si.norm.pdf(d1) * (-d2 / sigma)

def calculate_charm(S, K, t, r, sigma, option_type="call"):
    """
    Mengira nilai Charm / Delta Bleed (dDelta / dt) menggunakan model Black-Scholes.
    Menunjukkan kadar kebocoran Delta opsyen untuk setiap 1 hari masa yang berlalu.
    """
    if t <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0
        
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * t) / (sigma * np.sqrt(t))
    d2 = d1 - sigma * np.sqrt(t)
    
    nd1 = si.norm.pdf(d1)
    n_d1 = si.norm.cdf(d1)
    
    if option_type == "call":
        charm_year = -nd1 * (r / (sigma * np.sqrt(t)) - d2 / (2 * t)) - r * n_d1
    else: # put
        charm_year = -nd1 * (r / (sigma * np.sqrt(t)) - d2 / (2 * t)) + r * (1 - n_d1)
        
    return charm_year / 365.0

def find_gamma_flip(df):
    """Mencari titik strike di mana Net GEX bertukar arah dari negatif ke positif."""
    if df.empty or 'Net_GEX' not in df.columns:
        return None
    df_sorted = df.sort_values('Strike').reset_index(drop=True)
    for i in range(len(df_sorted) - 1):
        if (df_sorted.loc[i, 'Net_GEX'] < 0 and df_sorted.loc[i+1, 'Net_GEX'] >= 0) or \
           (df_sorted.loc[i, 'Net_GEX'] >= 0 and df_sorted.loc[i+1, 'Net_GEX'] < 0):
            return (df_sorted.loc[i, 'Strike'] + df_sorted.loc[i+1, 'Strike']) / 2
    return None
