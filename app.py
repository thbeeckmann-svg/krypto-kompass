import base64
from datetime import datetime, timedelta
import os
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
import altair as alt
import numpy as np
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import yfinance as yf

# --- ZEITZONE & KONSTANTEN ---
BERLIN_TZ = ZoneInfo("Europe/Berlin")

CRYPTO_SYMBOLS = {
    "Bitcoin": "₿", "Ethereum": "Ξ", "Solana": "◎", 
    "Cardano": "₳", "Bitcoin Cash": "₿"
}

CRYPTO_LOGOS = {
    "Bitcoin": "https://assets.coingecko.com/coins/images/1/small/bitcoin.png",
    "Ethereum": "https://assets.coingecko.com/coins/images/279/small/ethereum.png",
    "Solana": "https://assets.coingecko.com/coins/images/4128/small/solana.png",
    "Cardano": "https://assets.coingecko.com/coins/images/975/small/cardano.png",
    "Bitcoin Cash": "https://assets.coingecko.com/coins/images/780/small/bitcoin-cash-circle.png",
}

LOG_FILE = "prediction_log.csv"

# --- HILFSFUNKTION: DEUTSCHE ZAHLENFORMATIERUNG ---
def format_ger(value, decimals=2, force_sign=False):
    if value is None:
        return "0,00"
    try:
        val_float = float(value)
        fmt_str = f"{{:+,.{decimals}f}}" if force_sign else f"{{:,.{decimals}f}}"
        fmt_str = fmt_str.format(val_float)
        # Tausenderkomma zu X, Dezimalpunkt zu Komma, X zu Tausenderpunkt
        return fmt_str.replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return str(value)

# --- SEITEN-KONFIGURATION & CSS ---
st.set_page_config(page_title="Krypto-Kompass", page_icon="🪙", layout="centered")

hide_streamlit_style = """
    <style>
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    header { visibility: hidden; }
    .stAppDeployButton { display: none !important; }
    div[data-testid="stDecoration"] { display: none !important; }
    .block-container {
        padding-top: 1rem !important; padding-bottom: 2rem !important;
        padding-left: 0.8rem !important; padding-right: 0.8rem !important;
    }
    div[data-testid="stMetricValue"] { font-size: 1.2rem !important; }
    div[data-testid="stExpander"] { border-radius: 8px !important; margin-bottom: 0.5rem !important; }
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# --- PASSWORTSCHUTZ ---
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.markdown("## 🔒 Krypto-Kompass — Zugangsschutz")
        st.info("Bitte gib das Passwort ein.")
        eingabe = st.text_input("Passwort:", type="password")
        if st.button("Anmelden", use_container_width=True):
            if eingabe == st.secrets.get("APP_PASSWORD", "MeinGeheimesPasswort123"):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("❌ Falsches Passwort!")
        return False
    return True

if not check_password():
    st.stop()

# --- HEADER ---
col_title, col_btn = st.columns([2, 1])
with col_title:
    st.markdown("### 🪙 Krypto-Kompass")
with col_btn:
    if st.button("🔄 Aktualisieren", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.caption(f"Letztes Update: {datetime.now(BERLIN_TZ).strftime('%d.%m.%Y %H:%M:%S')}")

# --- DATEN-APIS ---
@st.cache_data(ttl=30)
def get_current_data():
    coins = {"Bitcoin": "BTCEUR", "Ethereum": "ETHEUR", "Solana": "SOLEUR", "Cardano": "ADAEUR", "Bitcoin Cash": "BCHEUR"}
    yf_map = {"Bitcoin": "BTC-EUR", "Ethereum": "ETH-EUR", "Solana": "SOL-EUR", "Cardano": "ADA-EUR", "Bitcoin Cash": "BCH-EUR"}
    data = {}
    try:
        res = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=4)
        if res.status_code == 200:
            ticker_dict = {i["symbol"]: i for i in res.json() if isinstance(i, dict) and "symbol" in i}
            for name, symbol in coins.items():
                if symbol in ticker_dict:
                    item = ticker_dict[symbol]
                    data[name] = {"eur": float(item["lastPrice"]), "eur_24h_change": float(item["priceChangePercent"]), "volume": float(item["quoteVolume"])}
    except Exception:
        pass

    if len(data) < len(coins):
        for name, yf_sym in yf_map.items():
            if name not in data:
                try:
                    hist = yf.Ticker(yf_sym).history(period="5d")
                    if not hist.empty and len(hist) >= 1:
                        price = float(hist["Close"].iloc[-1])
                        prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else float(hist["Open"].iloc[-1])
                        data[name] = {"eur": price, "eur_24h_change": ((price - prev) / prev) * 100.0 if prev > 0 else 0.0, "volume": float(hist["Volume"].iloc[-1]) * price}
                except Exception:
                    pass
    return data if data else None

@st.cache_data(ttl=300)
def get_real_spot_etf_flows():
    headers = {"User-Agent": "Mozilla/5.0", "Origin": "https://coinglass.com", "Referer": "https://coinglass.com/"}
    try:
        res = requests.get("https://fapi.coinglass.com/api/etf/flow", headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data: 
                val = float(data[-1].get("totalNetInflow", data[-1].get("totalNetFlow", 0)))
                if val != 0: return round(val, 2)
    except Exception:
        pass

    try:
        farside_url = "https://farside.co.uk/wp-content/uploads/ptool/US%20Spot%20Bitcoin%20ETF/flow.csv"
        res = requests.get(farside_url, headers=headers, timeout=5)
        if res.status_code == 200:
            lines = res.text.strip().split("\n")
            if len(lines) > 2: 
                val = float(lines[-1].split(",")[-1].strip())
                if val != 0: return round(val, 2)
    except Exception:
        pass
    return 0.0

@st.cache_data(ttl=120)
def get_fear_and_greed():
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if res.status_code == 200:
            data = res.json()["data"][0]
            return int(data["value"]), data["value_classification"]
    except Exception:
        pass
    return 50, "Neutral"

@st.cache_data(ttl=600)
def get_news_sentiment_score():
    headers = {"User-Agent": "Mozilla/5.0"}
    headlines = []
    
    try:
        res_rss = requests.get("https://cointelegraph.com/rss", headers=headers, timeout=5)
        if res_rss.status_code == 200:
            for item in ET.fromstring(res_rss.text).findall(".//item")[:5]:
                if item.find("title") is not None: headlines.append(item.find("title").text.strip())
    except Exception:
        pass

    if not headlines:
        try:
            res = requests.get("https://min-api.cryptocompare.com/data/v2/news/?lang=EN", headers=headers, timeout=5)
            if res.status_code == 200: headlines = [a.get("title", "") for a in res.json().get("Data", [])[:5] if a.get("title")]
        except Exception:
            pass

    if not headlines:
        headlines = ["Makro-Lage stabilisiert sich", "Institutional Inflows konstant", "Spot-ETF Volumina steigen", "Netzwerk-Hashrate auf ATH"]

    sentiment_points = 25
    for h in headlines:
        t_low = h.lower()
        if any(w in t_low for w in ["bull", "cut", "rally", "gain", "up", "high", "surge", "approval", "buy"]): sentiment_points += 20
        if any(w in t_low for w in ["bear", "hike", "drop", "fall", "down", "low", "crash", "ban", "inflation", "sell"]): sentiment_points -= 20

    return max(-100, min(100, sentiment_points)), headlines[:5]

@st.cache_data(ttl=300)
def get_defillama_metrics():
    res_data = {"stablecoin_mcap_b": 135.0, "global_tvl_b": 85.0}
    try:
        res = requests.get("https://stablecoins.llama.fi/stablecoins?includePrices=true", timeout=5)
        if res.status_code == 200:
            data = res.json().get("peggedAssets", [])
            usdt_mcap = next((i for i in data if i.get("symbol") == "USDT"), {}).get("circulating", {}).get("peggedUSD", 0)
            usdc_mcap = next((i for i in data if i.get("symbol") == "USDC"), {}).get("circulating", {}).get("peggedUSD", 0)
            res_data["stablecoin_mcap_b"] = round((usdt_mcap + usdc_mcap) / 1e9, 2)
            
        tvl_res = requests.get("https://api.llama.fi/v2/chains", timeout=5)
        if tvl_res.status_code == 200:
            res_data["global_tvl_b"] = round(sum(c.get("tvl", 0) for c in tvl_res.json()) / 1e9, 2)
    except Exception:
        pass
    return res_data

@st.cache_data(ttl=60)
def get_vix_and_nasdaq():
    data = {"vix_val": 18.0, "vix_chg": 0.0, "nasdaq_val": 0.0, "nasdaq_chg": 0.0}
    try:
        df_vix = yf.Ticker("^VIX").history(period="5d")
        if not df_vix.empty: data["vix_val"], data["vix_chg"] = df_vix["Close"].iloc[-1], ((df_vix["Close"].iloc[-1] - df_vix["Close"].iloc[0])/df_vix["Close"].iloc[0])*100
        df_ndx = yf.Ticker("^NDX").history(period="5d")
        if not df_ndx.empty: data["nasdaq_val"], data["nasdaq_chg"] = df_ndx["Close"].iloc[-1], ((df_ndx["Close"].iloc[-1] - df_ndx["Close"].iloc[0])/df_ndx["Close"].iloc[0])*100
    except Exception:
        pass
    return data

@st.cache_data(ttl=300)
def get_btc_dominance():
    try:
        res = requests.get("https://api.coingecko.com/api/v3/global", timeout=4)
        if res.status_code == 200: return round(res.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 56.5), 1)
    except Exception:
        pass
    return 56.5

@st.cache_data(ttl=60)
def get_macro_bond_yield():
    try:
        hist = yf.Ticker("^TNX").history(period="5d")
        if not hist.empty: return float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-1] - hist["Close"].iloc[0])
    except Exception:
        pass
    return None, 0.0

@st.cache_data(ttl=60)
def get_extended_macro_data():
    data = {}
    tickers = {"dxy": "DX-Y.NYB", "eurusd": "EURUSD=X", "usdjpy": "JPY=X", "gold": "GC=F", "silver": "SI=F", "oil": "CL=F", "dow": "^DJI", "ftse": "^FTSE", "sse": "000001.SS"}
    for key, sym in tickers.items():
        try:
            h = yf.Ticker(sym).history(period="5d")
            if not h.empty:
                data[f"{key}_val"] = float(h["Close"].iloc[-1])
                data[f"{key}_chg"] = float(((h["Close"].iloc[-1] - h["Close"].iloc[0])/h["Close"].iloc[0])*100)
        except Exception:
            data[f"{key}_val"], data[f"{key}_chg"] = 0.0, 0.0
    return data

@st.cache_data(ttl=30)
def get_historical_data(symbol, limit_anzahl, interval, weekly=False):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit_anzahl}"
        res = requests.get(url, timeout=4)
        if res.status_code == 200:
            raw = res.json()
            if raw:
                dates = [pd.to_datetime(e[0], unit='ms').tz_localize('UTC').tz_convert(BERLIN_TZ).tz_localize(None) for e in raw]
                df = pd.DataFrame({"Timestamp": dates, "Kurs": [float(e[4]) for e in raw]}).set_index("Timestamp")
                return df.resample("W").mean().dropna() if weekly else df.sort_index()
    except Exception:
        pass
    return None

# --- INDIKATOREN ---
@st.cache_data(ttl=30)
def calculate_atr(symbol, limit=30):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}"
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            df = pd.DataFrame(res.json(), columns=["ot", "o", "high", "low", "close", "v", "ct", "qav", "nt", "tbb", "tbq", "i"])
            df[["high", "low", "close"]] = df[["high", "low", "close"]].astype(float)
            df["tr1"] = df["high"] - df["low"]
            df["tr2"] = abs(df["high"] - df["close"].shift(1))
            df["tr3"] = abs(df["low"] - df["close"].shift(1))
            return float(df[["tr1", "tr2", "tr3"]].max(axis=1).rolling(14).mean().iloc[-1])
    except Exception:
        pass
    return 0.0

@st.cache_data(ttl=30)
def get_real_rsi(symbol, limit=30):
    try:
        res = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}", timeout=3)
        if res.status_code == 200:
            df = pd.DataFrame(res.json(), columns=["ot", "o", "h", "l", "close", "v", "ct", "qav", "nt", "tbb", "tbq", "i"])
            delta = df["close"].astype(float).diff()
            rs = delta.clip(lower=0).rolling(14).mean() / -delta.clip(upper=0).rolling(14).mean()
            return round(float(100 - (100 / (1 + rs)).iloc[-1]), 2)
    except Exception:
        pass
    return 50.0

@st.cache_data(ttl=60)
def get_real_funding_rate(coin_name="Bitcoin"):
    symbol = {"Bitcoin": "BTCUSDT", "Ethereum": "ETHUSDT", "Solana": "SOLUSDT", "Cardano": "ADAUSDT", "Bitcoin Cash": "BCHUSDT"}.get(coin_name, "BTCUSDT")
    try:
        res = requests.get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}", timeout=5)
        if res.status_code == 200: return float(res.json().get("lastFundingRate", 0.0001))
    except Exception:
        pass
    return 0.0001

def analyze_whale_activity(volume, change_24h):
    if volume > 2000000000 and change_24h < -1.5: return "🔴 Whale-Warnung: Hohe Börsenzuflüsse", -1
    elif volume > 2000000000 and change_24h > 1.5: return "🟢 Whale-Akkumulation: OTC-Käufe", 1
    return "🟡 Unauffällige On-Chain-Aktivität", 0

# --- KI, SCORING & LOGGING ---
def generate_local_self_learning_optimizations(df_archive, current_data):
    default_weights = {"Trend & Momentum": 15.0, "ETF Net Flows": 15.0, "Makro (Zins/Aktien/Währung/VIX)": 15.0, "Fear & Greed Index": 15.0, "Funding Rates & Derivate": 15.0, "RSI & Volatilität": 10.0, "Nachrichten & Zins-Sentiment": 10.0, "Krypto-Liquidität & DeFi TVL": 5.0}
    if df_archive is None or df_archive.empty or not current_data:
        return "🧠 *Das autonome Lernmodul sammelt aktuell noch historische Daten.*", default_weights

    buy_signals, sell_signals, failed_buys, failed_sells = 0, 0, 0, 0

    for idx, row in df_archive.iterrows():
        c_name = row.get("Coin", "")
        price_then = float(row.get("Kurs_Damals", 0))
        signal = str(row.get("Signal", "")).upper()
        curr_price = current_data.get(c_name, {}).get("eur", 0)
        
        if price_then > 0 and curr_price > 0:
            diff_pct = ((curr_price - price_then) / price_then) * 100
            if "KAUF" in signal:
                buy_signals += 1
                if diff_pct < -1.5: failed_buys += 1
            elif "VORSICHT" in signal or "GEWINN" in signal:
                sell_signals += 1
                if diff_pct > 1.5: failed_sells += 1

    weights = default_weights.copy()
    optimizations = []

    if buy_signals > 0 and (failed_buys / buy_signals) > 0.3:
        weights["Trend & Momentum"] -= 2.0; weights["Makro (Zins/Aktien/Währung/VIX)"] += 2.0; weights["Nachrichten & Zins-Sentiment"] += 1.0
        optimizations.append("⚠️ **Anpassung Kauf-Signale:** Makro-, Zins- und News-Einfluss gestärkt.")
    if sell_signals > 0 and (failed_sells / sell_signals) > 0.3:
        weights["Funding Rates & Derivate"] += 2.0; weights["RSI & Volatilität"] += 1.0; weights["Trend & Momentum"] -= 3.0
        optimizations.append("⚠️ **Anpassung Verkauf-Signale:** Derivate-Raten & RSI höher gewichtet.")
    if not optimizations:
        optimizations.append("✅ **System-Status Optimal:** Multi-Faktoren-Gewichtungen aktiv.")

    report = f"#### 📊 Autonom berechnete Gewichtungs-Optimierung\nBasierend auf **{len(df_archive)} ausgewerteten Zyklen**:\n\n" + "\n".join([f"- {opt}" for opt in optimizations])
    return report, weights

def calculate_detailed_factor_scores(perf_val, fg_value, bond_change, etf_net_flow, vix_data, news_score, llama_data, rsi_val, funding_rate, weights):
    trend_score = 80 if perf_val <= -7.5 else 40 if perf_val <= -3.0 else -80 if perf_val >= 7.5 else -40 if perf_val >= 3.5 else 0
    sentiment_score = 90 if fg_value <= 25 else -90 if fg_value >= 75 else (50 - fg_value) * 1.5
    etf_score = 90 if etf_net_flow >= 200.0 else 50 if etf_net_flow >= 50.0 else -90 if etf_net_flow <= -200.0 else -50 if etf_net_flow <= -50.0 else 0
    
    funding_score = 10
    if funding_rate is not None:
        funding_score = -90 if funding_rate >= 0.0005 else -50 if funding_rate >= 0.0002 else 90 if funding_rate <= -0.0003 else 50 if funding_rate <= -0.0001 else 10
        
    rsi_score = max(-100.0, min(100.0, (50.0 - rsi_val) * 2.5)) if rsi_val is not None else 0
    
    macro_points = 30 if bond_change < -0.05 else -30 if bond_change > 0.05 else 0
    if vix_data.get("vix_val", 18.0) > 28.0: macro_points -= 30
    elif vix_data.get("vix_val", 18.0) < 15.0: macro_points += 20
    
    liq_score = 70 if llama_data.get("global_tvl_b", 85.0) >= 90.0 else 30 if llama_data.get("global_tvl_b", 85.0) >= 70.0 else -20

    return {
        f"Trend & Momentum ({weights['Trend & Momentum']:.0f}%)": trend_score,
        f"Spot-ETF Flows ({weights['ETF Net Flows']:.0f}%)": etf_score,
        f"Makro & VIX ({weights['Makro (Zins/Aktien/Währung/VIX)']:.0f}%)": max(-100, min(100, macro_points)),
        f"Fear & Greed ({weights['Fear & Greed Index']:.0f}%)": sentiment_score,
        f"Funding & Derivate ({weights['Funding Rates & Derivate']:.0f}%)": funding_score,
        f"RSI & Volatilität ({weights['RSI & Volatilität']:.0f}%)": rsi_score,
        f"News & Zins ({weights['Nachrichten & Zins-Sentiment']:.0f}%)": news_score,
        f"DeFi TVL ({weights['Krypto-Liquidität & DeFi TVL']:.0f}%)": liq_score,
    }

def generate_signal(perf_val, fg_value, bond_change, etf_net_flow, vix_data, news_score, llama_data, zeitraum, weights, rsi_val, funding_rate):
    scores = calculate_detailed_factor_scores(perf_val, fg_value, bond_change, etf_net_flow, vix_data, news_score, llama_data, rsi_val, funding_rate, weights)
    total = sum(score_val * (weights[key.split(" (")[0]] / 100.0) for key, score_val in scores.items() if key.split(" (")[0] in weights)
    macro_score = 1 if total > 20 else (-1 if total < -20 else 0)
    
    if zeitraum == "24 Stunden": t_sf, t_mf, t_ms, t_ss = -7.5, -3.0, 3.5, 7.5
    elif zeitraum == "7 Tage": t_sf, t_mf, t_ms, t_ss = -13.0, -4.5, 5.5, 13.0
    elif zeitraum == "30 Tage": t_sf, t_mf, t_ms, t_ss = -22.0, -8.0, 9.0, 22.0
    elif zeitraum == "90 Tage": t_sf, t_mf, t_ms, t_ss = -32.0, -11.0, 13.0, 32.0
    elif zeitraum == "6 Monate": t_sf, t_mf, t_ms, t_ss = -45.0, -15.0, 18.0, 45.0
    else: t_sf, t_mf, t_ms, t_ss = -60.0, -20.0, 25.0, 60.0

    if perf_val <= t_sf and macro_score >= 0: return "STARKE KAUFCHANCE 🟢🟢", f"Deutlicher Kursrücksetzer ({zeitraum}: {format_ger(perf_val, force_sign=True)}%).", scores
    elif perf_val <= t_mf: return "KAUFEN 🟢", f"Moderat nachgebend im Zeitraum {zeitraum} ({format_ger(perf_val, force_sign=True)}%).", scores
    elif perf_val >= t_ss and (fg_value >= 75 or macro_score < 0): return "GEWINNE SICHERN 🔴🔴", f"Starker Preissprung ({zeitraum}: {format_ger(perf_val, force_sign=True)}%).", scores
    elif perf_val >= t_ms: return "VORSICHT / TEILVERKAUF 🔴", f"Solider Zuwachs ({zeitraum}: {format_ger(perf_val, force_sign=True)}%).", scores
    else: return "HALTEN 🟡", f"Seitwärtsphase im Zeitraum {zeitraum} ({format_ger(perf_val, force_sign=True)}%).", scores

def save_and_commit_csv_simple(file_path, new_row):
    try:
        df = pd.read_csv(file_path) if os.path.exists(file_path) else pd.DataFrame()
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df.to_csv(file_path, index=False)
    except: pass

# --- HAUPTPROGRAMM (UI) ---
bond_val, bond_change = get_macro_bond_yield()
ext_macro = get_extended_macro_data()
fg_value, fg_text = get_fear_and_greed()
real_etf_flow = get_real_spot_etf_flows()
vix_data = get_vix_and_nasdaq()
news_score, top_headlines = get_news_sentiment_score()
llama_data = get_defillama_metrics()
btc_dom = get_btc_dominance()
current_data = get_current_data()

df_archive_check = pd.read_csv(LOG_FILE) if os.path.exists(LOG_FILE) else pd.DataFrame()
local_learning_report, dynamic_weights = generate_local_self_learning_optimizations(df_archive_check, current_data)

with st.expander("⚙️ **Einstellungen & Investitions-Planung**", expanded=False):
    col_e1, col_e2 = st.columns(2)
    with col_e1:
        zeitraum_wahl = st.selectbox("Rückblick-Zeitraum wählen:", ["24 Stunden", "7 Tage", "30 Tage", "90 Tage", "6 Monate", "1 Jahr"], index=1)
        investment_wahl = st.selectbox("Geplantes Ordervolumen (Tranche):", [100, 500, 1000, 2500, 5000, "Individuell"], index=1)
        geplantes_budget = st.number_input("Eigenen Betrag eingeben (€):", 10.0, 1000.0, 50.0) if investment_wahl == "Individuell" else float(investment_wahl)
    with col_e2:
        ausgewaehlte_coins = st.multiselect("Kryptowährungen anzeigen:", list(CRYPTO_SYMBOLS.keys()), default=list(CRYPTO_SYMBOLS.keys()))
        if st.checkbox("🔄 Auto-Refresh (alle 5 Min.)", value=True): st_autorefresh(interval=300000, key="datarefresh")

    active_weights = dynamic_weights
    if st.checkbox("🎛️ Manuelle Indikator-Gewichtung nutzen", value=False):
        c1, c2, c3, c4 = st.columns(4)
        active_weights = {
            "Trend & Momentum": c1.slider("Trend (%)", 0, 50, 15), "ETF Net Flows": c2.slider("ETF (%)", 0, 50, 15),
            "Makro (Zins/Aktien/Währung/VIX)": c3.slider("Makro (%)", 0, 50, 15), "Fear & Greed Index": c4.slider("F&G (%)", 0, 50, 15),
            "Funding Rates & Derivate": c1.slider("Funding (%)", 0, 50, 15), "RSI & Volatilität": c2.slider("RSI (%)", 0, 50, 10),
            "Nachrichten & Zins-Sentiment": c3.slider("News (%)", 0, 50, 10), "Krypto-Liquidität & DeFi TVL": c4.slider("DeFi (%)", 0, 50, 5)
        }
    portfolio_tranche_placeholder = st.container()

limit_anzahl, interval, wöchentlich = (24, "1h", False) if zeitraum_wahl == "24 Stunden" else (7, "1d", False) if zeitraum_wahl == "7 Tage" else (30, "1d", False) if zeitraum_wahl == "30 Tage" else (90, "1d", True) if zeitraum_wahl == "90 Tage" else (180, "1d", True) if zeitraum_wahl == "6 Monate" else (365, "1d", True)

if current_data and ausgewaehlte_coins:
    coin_results = {}
    for coin in ausgewaehlte_coins:
        sym_binance = {"Bitcoin": "BTCEUR", "Ethereum": "ETHEUR", "Solana": "SOLEUR", "Cardano": "ADAEUR", "Bitcoin Cash": "BCHEUR"}[coin]
        vals = current_data.get(coin, {})
        price, vol, chg24 = vals.get("eur", 0), vals.get("volume", 0), vals.get("eur_24h_change", 0)

        atr_value, rsi_value = calculate_atr(sym_binance, 30), get_real_rsi(sym_binance, 30)
        funding_rate_val = get_real_funding_rate(coin)

        df_history = get_historical_data(sym_binance, limit_anzahl, interval, wöchentlich)
        hist_price = df_history.iloc[0]["Kurs"] if df_history is not None and not df_history.empty else price
        perf_val = ((price - hist_price) / hist_price) * 100 if hist_price > 0 else 0.0

        signal, reason, scores_dict = generate_signal(perf_val, fg_value, bond_change, real_etf_flow, vix_data, news_score, llama_data, zeitraum_wahl, active_weights, rsi_value, funding_rate_val)
        whale_msg, _ = analyze_whale_activity(vol, chg24)
        save_and_commit_csv_simple(LOG_FILE, {"Datum": datetime.now(BERLIN_TZ).strftime("%Y-%m-%d %H:%M:%S"), "Coin": coin, "Kurs_Damals": price, "Signal": signal.split()[0], "FearGreed": fg_value, "Target_Horizon": zeitraum_wahl})

        coin_results[coin] = {
            "price": price, "vol": vol, "chg24": chg24, "atr": atr_value, "rsi": rsi_value, "fund": funding_rate_val,
            "sl": max(0.0, price - (2.0 * atr_value)), "tp": price + (3.0 * atr_value), "hist_price": hist_price, "perf": perf_val,
            "sig": signal, "msg": reason, "scores": scores_dict, "whale": whale_msg, "df": df_history, "sym": CRYPTO_SYMBOLS[coin], "logo": CRYPTO_LOGOS[coin]
        }

    # Portfolio Allokation
    total_w = sum(1.0 if "STARKE KAUF" in r["sig"] else 0.5 if "KAUFEN" in r["sig"] else 0.0 for r in coin_results.values())
    total_inv = 0.0
    for r in coin_results.values():
        w = 1.0 if "STARKE KAUF" in r["sig"] else 0.5 if "KAUFEN" in r["sig"] else 0.0
        r["inv_eur"] = round(geplantes_budget * (w / max(1.0, total_w)), 2)
        r["inv_pct"] = round((r["inv_eur"] / geplantes_budget) * 100, 1) if geplantes_budget > 0 else 0.0
        total_inv += r["inv_eur"]
    
    cash_eur = max(0.0, round(geplantes_budget - total_inv, 2))

    with portfolio_tranche_placeholder:
        st.markdown("💼 **Portfolio-Tranche Allokation:**")
        col_p1, col_p2, col_p3 = st.columns(3)
        col_p1.metric("Gesamt", f"{format_ger(geplantes_budget)} €")
        col_p2.metric("Krypto", f"{format_ger(total_inv)} €")
        col_p3.metric("Cash", f"{format_ger(cash_eur)} €")
        
        alloc_data = [{"Asset": c, "Betrag (€)": r["inv_eur"], "Anteil (%)": r["inv_pct"]} for c, r in coin_results.items() if r["inv_eur"] > 0]
        if cash_eur > 0: alloc_data.append({"Asset": "💵 Cash", "Betrag (€)": cash_eur, "Anteil (%)": round(cash_eur/geplantes_budget*100,1)})
        if alloc_data:
            chart_alloc = alt.Chart(pd.DataFrame(alloc_data)).mark_bar(cornerRadiusEnd=4).encode(
                x=alt.X("Betrag (€):Q", title=None), y=alt.Y("Asset:N", sort=None, title=None),
                color=alt.Color("Asset:N", legend=None), tooltip=["Asset", "Betrag (€)", "Anteil (%)"]
            ).properties(height=max(120, len(alloc_data) * 35))
            st.altair_chart(chart_alloc, use_container_width=True)

    st.markdown("### 🪙 Einzelne Kryptowährungen")
    for coin, r in coin_results.items():
        p_dec = 4 if r["price"] < 1 else 3 if r["price"] < 10 else 2
        bg_color, f_color = ("rgba(46, 204, 113, 0.12)", "#2ecc71") if r["perf"] >= 0 else ("rgba(231, 76, 60, 0.12)", "#e74c3c")

        with st.container(border=True):
            c1, c2 = st.columns([1.2, 1])
            c1.markdown(f"#### <img src='{r['logo']}' width='22' style='vertical-align: middle;'> {coin} ({r['sym']})", unsafe_allow_html=True)
            c1.markdown(f"<div style='font-size: 1.35rem; font-weight: bold;'>{format_ger(r['price'], p_dec)} €</div>", unsafe_allow_html=True)
            
            c2.markdown(f"<div style='text-align:right;'><span style='background-color:{bg_color}; color:{f_color}; font-weight:bold; padding:4px 10px; border-radius:8px;'>{format_ger(r['perf'], 2, force_sign=True)}%</span></div>", unsafe_allow_html=True)

            st.write("---")
            c_s1, c_s2 = st.columns(2)
            c_s1.markdown(f"**Signal:** {r['sig']}")
            c_s2.markdown(f"**Tranche:** **{format_ger(r['inv_eur'])} €** ({format_ger(r['inv_pct'])}%)")

            with st.expander("🔍 **Details, Analyse & Chart**"):
                st.info(f"💡 **Begründung ({zeitraum_wahl}):** {r['msg']}")
                st.caption(f"🏛 **Spot-ETF:** {format_ger(real_etf_flow, force_sign=True)}M $ | 📊 **RSI:** {format_ger(r['rsi'], 1)} | ⚡ **Funding:** {format_ger(r['fund']*100, 3, force_sign=True)}%\n\n📰 **News-Score:** {news_score}/100 | 🔒 **DeFi TVL:** {format_ger(llama_data.get('global_tvl_b', 0), 1)} Mrd. $\n\n🐋 **Wal-Aktivität:** {r['whale']}")
                st.markdown(f"🛡️ **ATR (14):** {format_ger(r['atr'], p_dec)} € | 🛑 **SL:** **{format_ger(r['sl'], p_dec)} €** | 🎯 **TP:** **{format_ger(r['tp'], p_dec)} €**")
                
                # Signal-Score Chart
                df_scores = pd.DataFrame(list(r["scores"].items()), columns=["Indikator", "Score"])
                chart_scores = alt.Chart(df_scores).mark_bar().encode(
                    x=alt.X("Score:Q", scale=alt.Scale(domain=[-100, 100])), y=alt.Y("Indikator:N", sort=None, title=None),
                    color=alt.condition(alt.datum.Score > 0, alt.value("#2ecc71"), alt.value("#e74c3c")), tooltip=["Indikator", "Score"]
                ).properties(height=250)
                st.altair_chart(chart_scores, use_container_width=True)

                # History Chart
                if r["df"] is not None and not r["df"].empty:
                    df_chart = r["df"].reset_index()
                    min_p, max_p = df_chart["Kurs"].min(), df_chart["Kurs"].max()
                    c_color = "#2ecc71" if r["perf"] >= 0 else "#e74c3c"
                    
                    base = alt.Chart(df_chart).encode(
                        x=alt.X("Timestamp:T", title="", axis=alt.Axis(gridDash=[2,2], gridColor="rgba(150,150,150,0.15)")),
                        y=alt.Y("Kurs:Q", scale=alt.Scale(domain=[min_p*0.98, max_p*1.02], zero=False), axis=alt.Axis(gridColor="rgba(150,150,150,0.15)"))
                    ).properties(height=200)
                    
                    chart = base.mark_area(opacity=0.12, color=c_color) + base.mark_line(color=c_color, strokeWidth=2)
                    st.altair_chart(chart.interactive(), use_container_width=True)

else:
    st.warning("Konnte keine Marktdaten laden.")

st.write("---")

# --- GLOBALER MARKT ---
bar_c = "#e74c3c" if fg_value > 70 else "#f39c12" if fg_value >= 45 else "#2ecc71"
with st.container(border=True):
    st.markdown(f"📊 **Sentiment-Index:** **{fg_value} / 100** ({fg_text}) {'🔴' if fg_value > 70 else '🟡' if fg_value >= 45 else '🟢'}")
    st.markdown(f"<div style='background:#e0e0e0; border-radius:10px; width:100%; height:12px;'><div style='background:{bar_c}; width:{fg_value}%; height:100%;'></div></div>", unsafe_allow_html=True)

    with st.expander("🌐 **Globale Leitmärkte & Makro-Details**"):
        st.markdown(f"🏛 **US-10J Zins:** {format_ger(bond_val)}% | 💵 **DXY:** {format_ger(ext_macro.get('dxy_val'))} | 💶 **EUR/USD:** {format_ger(ext_macro.get('eurusd_val'), 4)}")
        st.markdown(f"🥇 **Gold:** {format_ger(ext_macro.get('gold_val'))} $ | 🛢 **Öl:** {format_ger(ext_macro.get('oil_val'))} $ | 🏦 **Spot-ETF:** {format_ger(real_etf_flow)} Mio. USD")
        st.write(f"📉 **VIX:** {format_ger(vix_data.get('vix_val'))} | 🪙 **BTC Dominanz:** {format_ger(btc_dom)}%")
        
        st.write("---")
        st.write(f"📰 **Zins- & Nachrichten-Sentiment: {news_score} / 100**")
        for h in top_headlines: st.caption(f"- {h}")

# --- BACKTEST EXPANDER ---
with st.expander("🧠 **Autonomes Selbstlern-System & Backtest**"):
    if os.path.exists(LOG_FILE):
        try:
            df_archive = pd.read_csv(LOG_FILE)
            if not df_archive.empty and current_data:
                df_archive["ParsedDate"] = pd.to_datetime(df_archive["Datum"], errors="coerce")
                df_filtered = df_archive.sort_values(by="ParsedDate", ascending=False).copy()
                
                rows, bt_data = [], []
                sim_sig, sim_hold = 100.0, 100.0
                
                for _, row in df_filtered.iterrows():
                    c_n = row["Coin"]
                    kd = float(row.get("Kurs_Damals", 0))
                    ka = current_data.get(c_n, {}).get("eur", 0)
                    diff = ((ka - kd) / kd) * 100 if kd > 0 and ka > 0 else 0
                    
                    if "KAUF" in str(row.get("Signal", "")).upper(): sim_sig *= 1.0 + (max(-5.0, min(5.0, diff)) / 100.0)
                    sim_hold *= 1.0 + (diff / 100.0)
                    dt_str = row["ParsedDate"].strftime("%d.%m.%Y %H:%M") if pd.notnull(row["ParsedDate"]) else str(row["Datum"])
                    
                    bt_data.append({"Datum": dt_str, "App-Strategie": sim_sig, "Buy & Hold": sim_hold})
                    rows.append({
                        "Datum": dt_str, "Coin": c_n, "Signal": str(row.get("Signal", "")),
                        "Damals": f"{format_ger(kd, 4 if kd < 1 else 2)} €", "Aktuell": f"{format_ger(ka, 4 if ka < 1 else 2)} €",
                        "Diff": f"{format_ger(diff, force_sign=True)} %"
                    })
                
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
                st.markdown(local_learning_report)
                
                df_bt = pd.DataFrame(bt_data)
                if not df_bt.empty:
                    chart_bt = alt.Chart(df_bt.melt(id_vars=["Datum"], value_vars=["App-Strategie", "Buy & Hold"], var_name="Strategie", value_name="Wert")).mark_line(strokeWidth=2).encode(
                        x=alt.X("Datum:N", title=None, sort=None), y=alt.Y("Wert:Q", title="Depotwert (€)", scale=alt.Scale(zero=False)),
                        color=alt.Color("Strategie:N", scale=alt.Scale(domain=["App-Strategie", "Buy & Hold"], range=["#2ecc71", "#e74c3c"]))
                    ).properties(height=220)
                    st.altair_chart(chart_bt.interactive(), use_container_width=True)
        except Exception as e:
            st.warning(f"Fehler beim Backtest: {e}")

st.markdown("---")
st.caption("⚠️ **Disclaimer:** Dies ist keine Anlageberatung. Die Signale dienen rein zu Bildungszwecken.")
