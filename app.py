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

# Zeitzone für Deutschland (MEZ / MESZ)
BERLIN_TZ = ZoneInfo("Europe/Berlin")

# Krypto-Währungssymbole und offizielle Logo-URLs
CRYPTO_SYMBOLS = {
    "Bitcoin": "₿",
    "Ethereum": "Ξ",
    "Solana": "◎",
    "Cardano": "₳",
    "Bitcoin Cash": "₿",
}

CRYPTO_LOGOS = {
    "Bitcoin": "https://assets.coingecko.com/coins/images/1/small/bitcoin.png",
    "Ethereum": "https://assets.coingecko.com/coins/images/279/small/ethereum.png",
    "Solana": "https://assets.coingecko.com/coins/images/4128/small/solana.png",
    "Cardano": "https://assets.coingecko.com/coins/images/975/small/cardano.png",
    "Bitcoin Cash": "https://assets.coingecko.com/coins/images/780/small/bitcoin-cash-circle.png",
}

# Seiten-Konfiguration
st.set_page_config(
    page_title="Krypto-Kompass",
    page_icon="🪙",
    layout="centered",
)

# --- HANDY-OPTIMIERTES CSS ---
hide_streamlit_style = """
    <style>
    /* Oberste Leiste, Admin-Menü und Footer unsichtbar machen */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    header { visibility: hidden; }
    .stAppDeployButton { display: none !important; }
    div[data-testid="stDecoration"] { display: none !important; }
    div[data-testid="stViewerBadge"] { display: none !important; }
    .stAppViewerBadge { display: none !important; }
    div[class*="viewerBadge"] { display: none !important; }
    [data-testid="manage-app-button"] { display: none !important; }
    
    /* Mobilfreundliche Abstände & Schriftgrößen */
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 2rem !important;
        padding-left: 0.8rem !important;
        padding-right: 0.8rem !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.2rem !important;
    }
    div[data-testid="stExpander"] {
        border-radius: 8px !important;
        margin-bottom: 0.5rem !important;
    }
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# --- PASSWORTSCHUTZ / LOGIN ---
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.markdown("## 🔒 Krypto-Kompass — Zugangsschutz")
        st.info("Bitte gib das Passwort ein, um auf den Krypto-Kompass zuzugreifen.")
        eingabe = st.text_input("Passwort:", type="password")
        if st.button("Anmelden", use_container_width=True):
            passwort_soll = st.secrets.get("APP_PASSWORD", "MeinGeheimesPasswort123")
            if eingabe == passwort_soll:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("❌ Falsches Passwort!")
        return False
    return True

if not check_password():
    st.stop()

# KOPFBEREICH
col_title, col_btn = st.columns([2, 1])
with col_title:
    st.markdown("### 🪙 Krypto-Kompass")
with col_btn:
    if st.button("🔄 Aktualisieren", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

aktuelle_zeit = datetime.now(BERLIN_TZ).strftime("%d.%m.%Y %H:%M:%S")
st.caption(f"Letztes Update: {aktuelle_zeit}")

LOG_FILE = "prediction_log.csv"
IMP_FILE = "improvements_log.csv"
MARKET_LOG_FILE = "market_data_log.csv"

# --- AUTOMATISCHER GITHUB CLOUD-SPEICHER MIT DROSSELUNG & TOAST-FEEDBACK ---
def save_and_commit_csv(file_path, df, force_commit=False):
    df.to_csv(file_path, index=False)
    
    last_commit_key = f"last_commit_{file_path}"
    now = datetime.now(BERLIN_TZ)
    
    if not force_commit and last_commit_key in st.session_state:
        time_diff = (now - st.session_state[last_commit_key]).total_seconds()
        if time_diff < 1800:
            return

    github_token = st.secrets.get("GITHUB_TOKEN")
    repo = st.secrets.get("GITHUB_REPO")
    
    if github_token and repo:
        try:
            url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
            headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json",
            }
            res = requests.get(url, headers=headers, timeout=5)
            sha = res.json().get("sha") if res.status_code == 200 else None
            
            csv_str = df.to_csv(index=False)
            content_b64 = base64.b64encode(csv_str.encode("utf-8")).decode("utf-8")
            
            payload = {
                "message": f"🤖 Auto-Log Update: {file_path}",
                "content": content_b64,
            }
            if sha:
                payload["sha"] = sha
                
            put_res = requests.put(url, headers=headers, json=payload, timeout=5)
            if put_res.status_code in [200, 201]:
                st.session_state[last_commit_key] = now
                st.toast(f"☁️ {file_path} erfolgreich auf GitHub gesichert!", icon="✅")
        except Exception:
            pass

def clean_old_logs(file_path, days_limit=365):
    if os.path.exists(file_path):
        try:
            df = pd.read_csv(file_path)
            if "Datum" in df.columns:
                df["ParsedDate"] = pd.to_datetime(df["Datum"], errors="coerce")
                cutoff_date = datetime.now(BERLIN_TZ).replace(tzinfo=None) - timedelta(days=days_limit)
                df_filtered = df[df["ParsedDate"] >= cutoff_date]
                df_filtered = df_filtered.drop(columns=["ParsedDate"], errors="ignore")
                save_and_commit_csv(file_path, df_filtered)
        except Exception:
            pass

clean_old_logs(LOG_FILE, 365)
clean_old_logs(MARKET_LOG_FILE, 365)

# 1. BATCH-ABRUF DER BINANCE-DATEN (MIT YAHOO-FALLBACK)
@st.cache_data(ttl=10)
def get_current_data():
    coins = {"Bitcoin": "BTCEUR", "Ethereum": "ETHEUR", "Solana": "SOLEUR", "Cardano": "ADAEUR", "Bitcoin Cash": "BCHEUR"}
    yf_map = {"Bitcoin": "BTC-EUR", "Ethereum": "ETH-EUR", "Solana": "SOL-EUR", "Cardano": "ADA-EUR", "Bitcoin Cash": "BCH-EUR"}
    data = {}
    
    try:
        url = "https://api.binance.com/api/v3/ticker/24hr"
        response = requests.get(url, timeout=4)
        if response.status_code == 200:
            res_json = response.json()
            if isinstance(res_json, list):
                ticker_dict = {item["symbol"]: item for item in res_json if isinstance(item, dict) and "symbol" in item}
                for name, symbol in coins.items():
                    if symbol in ticker_dict:
                        item = ticker_dict[symbol]
                        data[name] = {
                            "eur": float(item["lastPrice"]),
                            "eur_24h_change": float(item["priceChangePercent"]),
                            "volume": float(item["quoteVolume"]),
                        }
    except Exception:
        pass

    if len(data) < len(coins):
        for name, yf_sym in yf_map.items():
            if name not in data:
                try:
                    t = yf.Ticker(yf_sym)
                    hist = t.history(period="5d")
                    if not hist.empty and len(hist) >= 1:
                        price = float(hist["Close"].iloc[-1])
                        prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else float(hist["Open"].iloc[-1])
                        chg = ((price - prev) / prev) * 100.0 if prev > 0 else 0.0
                        vol = float(hist["Volume"].iloc[-1]) * price
                        data[name] = {
                            "eur": price,
                            "eur_24h_change": chg,
                            "volume": vol,
                        }
                except Exception:
                    pass

    return data if data else None

# 2. Spot-ETF Net Flows (Neues, robustes System)
@st.cache_data(ttl=300)
def get_real_spot_etf_flows():
    # Wir tarnen die Anfrage als echter Webbrowser
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://coinglass.com",
        "Referer": "https://coinglass.com/"
    }
    
    # Versuch 1: Versteckte interne FAPI von CoinGlass
    try:
        url = "https://fapi.coinglass.com/api/etf/flow"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data and isinstance(data, list):
                # Holt den Net Flow des jüngsten Tages
                latest_flow = float(data[-1].get("totalNetInflow", data[-1].get("totalNetFlow", 0)))
                if latest_flow != 0:
                    return round(latest_flow, 2)
    except Exception:
        pass

    # Versuch 2: Alternative Farside CSV Direktauslese (sehr zuverlässiger Backup-Server)
    try:
        farside_url = "https://farside.co.uk/wp-content/uploads/ptool/US%20Spot%20Bitcoin%20ETF/flow.csv"
        csv_res = requests.get(farside_url, headers=headers, timeout=5)
        if csv_res.status_code == 200:
            lines = csv_res.text.strip().split("\n")
            if len(lines) > 2:
                # Wir nehmen die letzte Zeile, ignorieren leere Einträge und filtern das Total
                last_line_data = lines[-1].split(",")
                total_flow = float(last_line_data[-1].strip())
                if total_flow != 0:
                    return round(total_flow, 2)
    except Exception:
        pass

    # Versuch 3: Die alte Pro-API als absoluter Notnagel
    try:
        url_alt = "https://api.coinglass.com/api/pro/v1/futures/bitcoin_etf/flow"
        res_alt = requests.get(url_alt, headers=headers, timeout=5)
        if res_alt.status_code == 200:
            res_json = res_alt.json()
            if res_json.get("success") and res_json.get("data"):
                latest_flow = float(res_json["data"][-1].get("totalNetIngressUsd", 0)) / 1_000_000.0
                if latest_flow != 0:
                    return round(latest_flow, 2)
    except Exception:
        pass

    return 0.0

# 3. Fear & Greed Index
@st.cache_data(ttl=60)
def get_fear_and_greed():
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            res = response.json()
            if "data" in res and len(res["data"]) > 0:
                data = res["data"][0]
                return int(data["value"]), data["value_classification"]
    except Exception:
        pass
    return 50, "Neutral"

# 4. VOLATILITÄTSINDEX (VIX) & NASDAQ 100
@st.cache_data(ttl=60)
def get_vix_and_nasdaq():
    data = {"vix_val": 18.0, "vix_chg": 0.0, "nasdaq_val": 0.0, "nasdaq_chg": 0.0}
    try:
        vix = yf.Ticker("^VIX")
        v_hist = vix.history(period="5d")
        if not v_hist.empty:
            val = float(v_hist["Close"].iloc[-1])
            prev = float(v_hist["Close"].iloc[0])
            data["vix_val"] = val
            data["vix_chg"] = float(((val - prev) / prev) * 100) if prev > 0 else 0.0
    except Exception:
        pass

    try:
        ndx = yf.Ticker("^NDX")
        n_hist = ndx.history(period="5d")
        if not n_hist.empty:
            n_val = float(n_hist["Close"].iloc[-1])
            n_prev = float(n_hist["Close"].iloc[0])
            data["nasdaq_val"] = n_val
            data["nasdaq_chg"] = float(((n_val - n_prev) / n_prev) * 100) if n_prev > 0 else 0.0
    except Exception:
        pass

    return data

# 5. ULTRA-ROBUSTE NEWS- & SENTIMENT-FUNKTION (GARANTIERTER FALLBACK)
@st.cache_data(ttl=10)
def get_news_sentiment_score():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    headlines = []
    
    try:
        res = requests.get("https://min-api.cryptocompare.com/data/v2/news/?lang=EN", headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json().get("Data", [])
            for art in data[:5]:
                t = art.get("title", "")
                if t:
                    headlines.append(t)
    except Exception:
        pass

    if not headlines:
        try:
            res_cg = requests.get("https://api.coingecko.com/api/v3/search/trending", headers=headers, timeout=5)
            if res_cg.status_code == 200:
                coins = res_cg.json().get("coins", [])
                for c in coins[:5]:
                    item = c.get("item", {})
                    headlines.append(f"Trending Asset im Fokus: {item.get('name', '')} ({item.get('symbol', '')})")
        except Exception:
            pass

    if not headlines:
        headlines = [
            "Makro-Lage: EZB und Fed signalisieren geldpolitische Kursrichtung",
            "Institutional Inflows: Spot-ETF Volumina stabilisieren den Markt",
            "On-Chain Aktivität: Langfristige Halter zeigen konstante Akkumulation",
            "Liquiditäts-Update: Globale Stablecoin-Märkte in Seitwärtsphase"
        ]

    sentiment_points = 0
    for h in headlines:
        t_low = h.lower()
        if any(w in t_low for w in ["bull", "cut", "rally", "gain", "up", "high", "surge", "approval", "growth"]):
            sentiment_points += 20
        if any(w in t_low for w in ["bear", "hike", "drop", "fall", "down", "low", "crash", "ban", "inflation"]):
            sentiment_points -= 20

    if sentiment_points == 0:
        sentiment_points = 25

    return max(-100, min(100, sentiment_points)), headlines[:5]

# 6. STABLECOIN LIQUIDITÄT & DEFI TVL
@st.cache_data(ttl=600)
def get_defillama_metrics():
    res_data = {"stablecoin_mcap_b": 135.0, "global_tvl_b": 85.0}
    try:
        url = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json().get("peggedAssets", []) if isinstance(res.json(), dict) else []
            usdt = next((item for item in data if item.get("symbol") == "USDT"), None)
            usdc = next((item for item in data if item.get("symbol") == "USDC"), None)
            
            usdt_mcap = usdt.get("circulating", {}).get("peggedUSD", 0) if usdt else 0
            usdc_mcap = usdc.get("circulating", {}).get("peggedUSD", 0) if usdc else 0
            
            res_data["stablecoin_mcap_b"] = round((usdt_mcap + usdc_mcap) / 1_000_000_000.0, 2)
    except Exception:
        pass

    try:
        tvl_url = "https://api.llama.fi/v2/chains"
        tvl_res = requests.get(tvl_url, timeout=5)
        if tvl_res.status_code == 200:
            chains = tvl_res.json()
            if isinstance(chains, list):
                total_tvl = sum(c.get("tvl", 0) for c in chains if isinstance(c, dict))
                res_data["global_tvl_b"] = round(total_tvl / 1_000_000_000.0, 2)
    except Exception:
        pass

    return res_data

# 7. BITCOIN DOMINANZ
@st.cache_data(ttl=300)
def get_btc_dominance():
    try:
        res = requests.get("https://api.coingecko.com/api/v3/global", timeout=4)
        if res.status_code == 200:
            dom = res.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 56.5)
            return round(dom, 1)
    except Exception:
        pass
    return 56.5

# 8. MAKRO-DATEN & LEITMÄRKTE
@st.cache_data(ttl=60)
def get_macro_bond_yield():
    try:
        tnx = yf.Ticker("^TNX")
        hist = tnx.history(period="5d")
        if not hist.empty:
            current_yield = float(hist["Close"].iloc[-1])
            prev_yield = float(hist["Close"].iloc[0])
            return current_yield, (current_yield - prev_yield)
    except Exception:
        pass
    return None, 0.0

@st.cache_data(ttl=60)
def get_extended_macro_data():
    data = {
        "dxy_val": 0.0, "dxy_chg": 0.0,
        "eurusd_val": 0.0, "eurusd_chg": 0.0,
        "usdjpy_val": 0.0, "usdjpy_chg": 0.0,
        "gold_val": 0.0, "gold_chg": 0.0,
        "silver_val": 0.0, "silver_chg": 0.0,
        "oil_val": 0.0, "oil_chg": 0.0,
        "copper_val": 0.0, "copper_chg": 0.0,
        "dow_val": 0.0, "dow_chg": 0.0,
        "ftse_val": 0.0, "ftse_chg": 0.0,
        "sse_val": 0.0, "sse_chg": 0.0,
    }
    
    tickers_map = {
        "dxy": "DX-Y.NYB",
        "eurusd": "EURUSD=X",
        "usdjpy": "JPY=X",
        "gold": "GC=F",
        "silver": "SI=F",
        "oil": "CL=F",
        "copper": "HG=F",
        "dow": "^DJI",
        "ftse": "^FTSE",
        "sse": "000001.SS",
    }

    try:
        symbols = list(tickers_map.values())
        batch_df = yf.download(symbols, period="5d", interval="1d", progress=False)
        
        if not batch_df.empty and "Close" in batch_df:
            close_df = batch_df["Close"]
            for key, sym in tickers_map.items():
                if sym in close_df.columns:
                    series = close_df[sym].dropna()
                    if len(series) >= 1:
                        val = float(series.iloc[-1])
                        prev = float(series.iloc[0]) if len(series) > 1 else val
                        chg = ((val - prev) / prev) * 100 if prev > 0 else 0.0
                        data[f"{key}_val"] = val
                        data[f"{key}_chg"] = chg
    except Exception:
        for key, sym in tickers_map.items():
            try:
                t = yf.Ticker(sym)
                h = t.history(period="5d")
                if not h.empty and len(h) >= 1:
                    val = float(h["Close"].iloc[-1])
                    prev = float(h["Close"].iloc[0])
                    chg = ((val - prev) / prev) * 100 if prev > 0 else 0.0
                    data[f"{key}_val"] = val
                    data[f"{key}_chg"] = chg
            except Exception:
                continue

    return data

@st.cache_data(ttl=60)
def get_stock_indices():
    indices = {"SP500": "^GSPC", "DAX": "^GDAXI"}
    results = {}
    for name, ticker in indices.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if not hist.empty:
                val = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[0])
                chg = ((val - prev) / prev) * 100 if prev > 0 else 0.0
                results[name] = {"val": val, "chg": chg}
        except Exception:
            pass
    return results

@st.cache_data(ttl=60)
def get_asian_markets():
    data = {}
    try:
        nikkei = yf.Ticker("^N225")
        nikkei_hist = nikkei.history(period="5d")
        if not nikkei_hist.empty:
            n_close = float(nikkei_hist["Close"].iloc[-1])
            n_prev = float(nikkei_hist["Close"].iloc[0])
            data["Nikkei_225"] = {
                "value": n_close,
                "change": ((n_close - n_prev) / n_prev) * 100 if n_prev > 0 else 0.0,
            }
    except Exception:
        pass

    try:
        hsi = yf.Ticker("^HSI")
        hsi_hist = hsi.history(period="5d")
        if not hsi_hist.empty:
            h_close = float(hsi_hist["Close"].iloc[-1])
            h_prev = float(hsi_hist["Close"].iloc[0])
            data["Hang_Seng"] = {
                "value": h_close,
                "change": ((h_close - h_prev) / h_prev) * 100 if h_prev > 0 else 0.0,
            }
    except Exception:
        pass

    return data if data else None

@st.cache_data(ttl=60)
def get_eurozone_market():
    try:
        stoxx = yf.Ticker("^STOXX50E")
        hist = stoxx.history(period="5d")
        if not hist.empty:
            close_val = float(hist["Close"].iloc[-1])
            prev_val = float(hist["Close"].iloc[0])
            change_val = ((close_val - prev_val) / prev_val) * 100 if prev_val > 0 else 0.0
            return {"value": close_val, "change": change_val}
    except Exception:
        pass
    return None

def analyze_whale_activity(volume, change_24h):
    if volume > 2000000000 and change_24h < -1.5:
        return ("🔴 Whale-Warnung: Hohe Börsenzuflüsse", -1)
    elif volume > 2000000000 and change_24h > 1.5:
        return ("🟢 Whale-Akkumulation: OTC-Käufe", 1)
    else:
        return ("🟡 Unauffällige On-Chain-Aktivität", 0)

@st.cache_data(ttl=30)
def calculate_atr(symbol, limit=30):
    symbol_yf_map = {"BTCEUR": "BTC-EUR", "ETHEUR": "ETH-EUR", "SOLEUR": "SOL-EUR", "ADAEUR": "ADA-EUR", "BCHEUR": "BCH-EUR"}
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            raw = response.json()
            if isinstance(raw, list) and len(raw) > 0:
                df = pd.DataFrame(
                    raw,
                    columns=[
                        "open_time", "open", "high", "low", "close", "volume",
                        "close_time", "quote_asset_volume", "num_trades",
                        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore",
                    ],
                )
                df["high"] = df["high"].astype(float)
                df["low"] = df["low"].astype(float)
                df["close"] = df["close"].astype(float)

                df["tr1"] = df["high"] - df["low"]
                df["tr2"] = abs(df["high"] - df["close"].shift(1))
                df["tr3"] = abs(df["low"] - df["close"].shift(1))
                df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)

                atr = df["tr"].rolling(window=14).mean().iloc[-1]
                return float(atr)
    except Exception:
        pass

    try:
        yf_sym = symbol_yf_map.get(symbol, "BTC-EUR")
        t = yf.Ticker(yf_sym)
        df = t.history(period="60d")
        if not df.empty and len(df) >= 15:
            df["high"] = df["High"].astype(float)
            df["low"] = df["Low"].astype(float)
            df["close"] = df["Close"].astype(float)

            df["tr1"] = df["high"] - df["low"]
            df["tr2"] = abs(df["high"] - df["close"].shift(1))
            df["tr3"] = abs(df["low"] - df["close"].shift(1))
            df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)

            atr = df["tr"].rolling(window=14).mean().iloc[-1]
            return float(atr)
    except Exception:
        pass
    return 0.0

@st.cache_data(ttl=30)
def get_real_rsi(symbol, limit=30):
    symbol_yf_map = {"BTCEUR": "BTC-EUR", "ETHEUR": "ETH-EUR", "SOLEUR": "SOL-EUR", "ADAEUR": "ADA-EUR", "BCHEUR": "BCH-EUR"}
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            raw = response.json()
            if isinstance(raw, list) and len(raw) > 0:
                df = pd.DataFrame(
                    raw,
                    columns=[
                        "open_time", "open", "high", "low", "close", "volume",
                        "close_time", "quote_asset_volume", "num_trades",
                        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore",
                    ],
                )
                df["close"] = df["close"].astype(float)
                delta = df["close"].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                return round(float(rsi.iloc[-1]), 2)
    except Exception:
        pass

    try:
        yf_sym = symbol_yf_map.get(symbol, "BTC-EUR")
        t = yf.Ticker(yf_sym)
        df = t.history(period="60d")
        if not df.empty and len(df) >= 15:
            df["close"] = df["Close"].astype(float)
            delta = df["close"].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return round(float(rsi.iloc[-1]), 2)
    except Exception:
        pass
    return 50.0

@st.cache_data(ttl=60)
def get_real_funding_rate(coin_name="Bitcoin"):
    futures_map = {
        "Bitcoin": "BTCUSDT",
        "Ethereum": "ETHUSDT",
        "Solana": "SOLUSDT",
        "Cardano": "ADAUSDT",
        "Bitcoin Cash": "BCHUSDT",
    }
    symbol = futures_map.get(coin_name, "BTCUSDT")
    try:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict):
                return float(data.get("lastFundingRate", 0.0001))
    except Exception:
        pass
    return 0.0001

# --- AUTONOMES SELBSTLERN-SYSTEM ---
def generate_local_self_learning_optimizations(df_archive, df_market, current_data):
    default_weights = {
        "Trend & Momentum": 15.0,
        "ETF Net Flows": 15.0,
        "Makro (Zins/Aktien/Währung/VIX)": 15.0,
        "Fear & Greed Index": 15.0,
        "Funding Rates & Derivate": 15.0,
        "RSI & Volatilität": 10.0,
        "Nachrichten & Zins-Sentiment": 10.0,
        "Krypto-Liquidität & DeFi TVL": 5.0,
    }

    if df_archive is None or df_archive.empty or not current_data:
        return (
            "🧠 *Das autonome Lernmodul sammelt aktuell noch historische Daten.*",
            default_weights,
        )

    total_predictions = len(df_archive)
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
                if diff_pct < -1.5:
                    failed_buys += 1
            elif "VORSICHT" in signal or "GEWINN" in signal:
                sell_signals += 1
                if diff_pct > 1.5:
                    failed_sells += 1

    weights = default_weights.copy()
    optimizations = []

    if buy_signals > 0 and (failed_buys / buy_signals) > 0.3:
        weights["Trend & Momentum"] -= 2.0
        weights["Makro (Zins/Aktien/Währung/VIX)"] += 2.0
        weights["Nachrichten & Zins-Sentiment"] += 1.0
        optimizations.append("⚠️ **Anpassung Kauf-Signale:** Makro-, Zins- und News-Einfluss gestärkt.")

    if sell_signals > 0 and (failed_sells / sell_signals) > 0.3:
        weights["Funding Rates & Derivate"] += 2.0
        weights["RSI & Volatilität"] += 1.0
        weights["Trend & Momentum"] -= 3.0
        optimizations.append("⚠️ **Anpassung Verkauf-Signale:** Derivate-Raten & RSI höher gewichtet.")

    if not optimizations:
        optimizations.append("✅ **System-Status Optimal:** Multi-Faktoren-Gewichtungen aktiv.")

    report = (
        f"""
#### 📊 Autonom berechnete Gewichtungs-Optimierung (Quant Self-Tuning)
Basierend auf **{total_predictions} ausgewerteten Zyklen** in `prediction_log.csv`:

| Indikator | Basis-Gewichtung | Aktive Live-Gewichtung |
| :--- | :---: | :---: |
| **Trend & Momentum** | 15,0 % | **{weights['Trend & Momentum']:.1f} %** |
| **ETF Net Flows (Spot)** | 15,0 % | **{weights['ETF Net Flows']:.1f} %** |
| **Makro & VIX** | 15,0 % | **{weights['Makro (Zins/Aktien/Währung/VIX)']:.1f} %** |
| **Fear & Greed Index** | 15,0 % | **{weights['Fear & Greed Index']:.1f} %** |
| **Funding Rates & Derivate** | 15,0 % | **{weights['Funding Rates & Derivate']:.1f} %** |
| **RSI & Volatilität** | 10,0 % | **{weights['RSI & Volatilität']:.1f} %** |
| **Nachrichten & Zins-Sentiment** | 10,0 % | **{weights['Nachrichten & Zins-Sentiment']:.1f} %** |
| **DeFi TVL & Liq.** | 5,0 % | **{weights['Krypto-Liquidität & DeFi TVL']:.1f} %** |

##### 💡 Autonome System-Erkenntnisse & Anpassungen:
"""
        + "\n".join([f"- {opt}" for opt in optimizations])
    )

    return report, weights

def calculate_detailed_factor_scores(
    perf_val,
    volume,
    fg_value,
    bond_change,
    asian_markets,
    eurozone_market,
    stock_indices,
    etf_net_flow,
    vix_data,
    news_score,
    llama_data,
    btc_dom,
    rsi_val=None,
    funding_rate=None,
    weights=None,
):
    if weights is None:
        weights = {
            "Trend & Momentum": 15.0,
            "ETF Net Flows": 15.0,
            "Makro (Zins/Aktien/Währung/VIX)": 15.0,
            "Fear & Greed Index": 15.0,
            "Funding Rates & Derivate": 15.0,
            "RSI & Volatilität": 10.0,
            "Nachrichten & Zins-Sentiment": 10.0,
            "Krypto-Liquidität & DeFi TVL": 5.0,
        }

    if perf_val <= -7.5:
        trend_score = 80
    elif perf_val <= -3.0:
        trend_score = 40
    elif perf_val >= 7.5:
        trend_score = -80
    elif perf_val >= 3.5:
        trend_score = -40
    else:
        trend_score = 0

    if fg_value <= 25:
        sentiment_score = 90
    elif fg_value >= 75:
        sentiment_score = -90
    else:
        sentiment_score = (50 - fg_value) * 1.5

    if etf_net_flow >= 200.0:
        etf_score = 90
    elif etf_net_flow >= 50.0:
        etf_score = 50
    elif etf_net_flow <= -200.0:
        etf_score = -90
    elif etf_net_flow <= -50.0:
        etf_score = -50
    else:
        etf_score = 0

    if funding_rate is not None:
        if funding_rate >= 0.0005:
            funding_score = -90
        elif funding_rate >= 0.0002:
            funding_score = -50
        elif funding_rate <= -0.0003:
            funding_score = 90
        elif funding_rate <= -0.0001:
            funding_score = 50
        else:
            funding_score = 10
    else:
        funding_score = -50 if perf_val > 5.0 else (50 if perf_val < -5.0 else 10)

    if rsi_val is not None:
        rsi_score = max(-100.0, min(100.0, (50.0 - rsi_val) * 2.5))
    else:
        rsi_score = 70 if perf_val < -6.0 else (-70 if perf_val > 6.0 else 0)

    macro_points = 0
    if bond_change < -0.05:
        macro_points += 30
    elif bond_change > 0.05:
        macro_points -= 30

    if stock_indices:
        sp500_chg = stock_indices.get("SP500", {}).get("chg", 0.0)
        dax_chg = stock_indices.get("DAX", {}).get("chg", 0.0)
        if sp500_chg > 0.5 or dax_chg > 0.5:
            macro_points += 15
        elif sp500_chg < -0.5 or dax_chg < -0.5:
            macro_points -= 15

    vix_val = vix_data.get("vix_val", 18.0)
    nasdaq_chg = vix_data.get("nasdaq_chg", 0.0)

    if vix_val > 28.0:
        macro_points -= 30
    elif vix_val < 15.0:
        macro_points += 20

    if nasdaq_chg > 1.0:
        macro_points += 20
    elif nasdaq_chg < -1.0:
        macro_points -= 20

    macro_score = max(-100, min(100, macro_points))
    news_sentiment_score = max(-100, min(100, news_score))

    tvl = llama_data.get("global_tvl_b", 85.0)
    if tvl >= 90.0:
        liq_score = 70
    elif tvl >= 70.0:
        liq_score = 30
    else:
        liq_score = -20

    return {
        f"Trend & Momentum ({weights['Trend & Momentum']:.0f}%)": trend_score,
        f"Spot-ETF Flows ({weights['ETF Net Flows']:.0f}%)": etf_score,
        f"Makro & VIX ({weights['Makro (Zins/Aktien/Währung/VIX)']:.0f}%)": macro_score,
        f"Fear & Greed ({weights['Fear & Greed Index']:.0f}%)": sentiment_score,
        f"Funding & Derivate ({weights['Funding Rates & Derivate']:.0f}%)": funding_score,
        f"RSI & Volatilität ({weights['RSI & Volatilität']:.0f}%)": rsi_score,
        f"News & Zins-Sentiment ({weights['Nachrichten & Zins-Sentiment']:.0f}%)": news_sentiment_score,
        f"DeFi TVL & Liq. ({weights['Krypto-Liquidität & DeFi TVL']:.0f}%)": liq_score,
    }

def get_automatic_macro_score(
    volume,
    fg_value,
    bond_change,
    change_24h,
    asian_markets,
    eurozone_market,
    stock_indices,
    etf_net_flow,
    vix_data,
    news_score,
    llama_data,
    btc_dom,
    rsi_val=None,
    funding_rate=None,
    weights=None,
):
    if weights is None:
        weights = {
            "Trend & Momentum": 15.0,
            "ETF Net Flows": 15.0,
            "Makro (Zins/Aktien/Währung/VIX)": 15.0,
            "Fear & Greed Index": 15.0,
            "Funding Rates & Derivate": 15.0,
            "RSI & Volatilität": 10.0,
            "Nachrichten & Zins-Sentiment": 10.0,
            "Krypto-Liquidität & DeFi TVL": 5.0,
        }

    scores = calculate_detailed_factor_scores(
        change_24h,
        volume,
        fg_value,
        bond_change,
        asian_markets,
        eurozone_market,
        stock_indices,
        etf_net_flow,
        vix_data,
        news_score,
        llama_data,
        btc_dom,
        rsi_val=rsi_val,
        funding_rate=funding_rate,
        weights=weights,
    )

    total = 0.0
    for key, score_val in scores.items():
        if "Trend & Momentum" in key:
            total += score_val * (weights["Trend & Momentum"] / 100.0)
        elif "Spot-ETF" in key:
            total += score_val * (weights["ETF Net Flows"] / 100.0)
        elif "Makro & VIX" in key:
            total += score_val * (weights["Makro (Zins/Aktien/Währung/VIX)"] / 100.0)
        elif "Fear & Greed" in key:
            total += score_val * (weights["Fear & Greed Index"] / 100.0)
        elif "Funding" in key:
            total += score_val * (weights["Funding Rates & Derivate"] / 100.0)
        elif "RSI" in key:
            total += score_val * (weights["RSI & Volatilität"] / 100.0)
        elif "News & Zins" in key:
            total += score_val * (weights["Nachrichten & Zins-Sentiment"] / 100.0)
        elif "DeFi TVL" in key:
            total += score_val * (weights["Krypto-Liquidität & DeFi TVL"] / 100.0)

    return 1 if total > 20 else (-1 if total < -20 else 0)

@st.cache_data(ttl=60)
def get_historical_data(symbol, limit_anzahl, interval, weekly=False):
    symbol_yf_map = {"BTCEUR": "BTC-EUR", "ETHEUR": "ETH-EUR", "SOLEUR": "SOL-EUR", "ADAEUR": "ADA-EUR", "BCHEUR": "BCH-EUR"}
    
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit_anzahl}"
        response = requests.get(url, timeout=4)
        if response.status_code == 200:
            raw_data = response.json()
            if isinstance(raw_data, list) and len(raw_data) > 0:
                dates = [
                    pd.to_datetime(entry[0], unit='ms')
                    .tz_localize('UTC')
                    .tz_convert(BERLIN_TZ)
                    .tz_localize(None)
                    for entry in raw_data
                ]
                prices = [float(entry[4]) for entry in raw_data]
                df = pd.DataFrame({"Timestamp": dates, "Kurs": prices})
                df.set_index("Timestamp", inplace=True)
                if weekly:
                    df = df.resample("W").mean().dropna()
                return df.sort_index(ascending=True)
    except Exception:
        pass

    try:
        yf_sym = symbol_yf_map.get(symbol, "BTC-EUR")
        t = yf.Ticker(yf_sym)
        yf_interval = "1h" if interval == "1h" else "1d"
        yf_period = "2d" if interval == "1h" else f"{limit_anzahl + 10}d"
        hist = t.history(period=yf_period, interval=yf_interval)
        if not hist.empty:
            hist = hist.tail(limit_anzahl)
            dates = [
                pd.to_datetime(d).tz_convert(BERLIN_TZ).tz_localize(None)
                if hasattr(d, "tzinfo") and d.tzinfo is not None
                else pd.to_datetime(d)
                for d in hist.index
            ]
            prices = hist["Close"].astype(float).values
            df = pd.DataFrame({"Timestamp": dates, "Kurs": prices})
            df.set_index("Timestamp", inplace=True)
            if weekly:
                df = df.resample("W").mean().dropna()
            return df.sort_index(ascending=True)
    except Exception:
        pass
    return None

def log_market_data(
    fg_val,
    fg_txt,
    bond_val,
    bond_change,
    nikkei_val,
    nikkei_chg,
    hsi_val,
    hsi_chg,
    eu_val,
    eu_chg,
    etf_flow,
    ext_macro,
    stock_indices,
    vix_data,
    news_score,
    llama_data,
    btc_dom,
):
    today_str = datetime.now(BERLIN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    date_only = datetime.now(BERLIN_TZ).strftime("%Y-%m-%d")

    sp500_v = round(stock_indices.get("SP500", {}).get("val", 0.0), 2) if stock_indices else 0.0
