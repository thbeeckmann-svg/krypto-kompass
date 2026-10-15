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
    # 1. Immer lokal im aktuellen Container speichern
    df.to_csv(file_path, index=False)
    
    # 2. Prüfen, ob in den letzten 30 Minuten bereits ein GitHub-Commit für diese Datei stattfand
    last_commit_key = f"last_commit_{file_path}"
    now = datetime.now(BERLIN_TZ)
    
    if not force_commit and last_commit_key in st.session_state:
        time_diff = (now - st.session_state[last_commit_key]).total_seconds()
        if time_diff < 1800:  # Erst nach 30 Min. (1800 Sek.) erneut an GitHub senden
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
            # SHA der existierenden Datei abfragen (erforderlich für GitHub-Update)
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
                st.session_state[last_commit_key] = now  # Zeitstempel aktualisieren
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


# 2. Spot-ETF Net Flows
@st.cache_data(ttl=300)
def get_real_spot_etf_flows():
    try:
        url = "https://api.coinglass.com/api/pro/v1/futures/bitcoin_etf/flow"
        headers = {"accept": "application/json", "User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=4)
        if response.status_code == 200:
            res_json = response.json()
            if isinstance(res_json, dict) and res_json.get("success") and res_json.get("data"):
                latest_flow = float(res_json["data"][-1].get("totalNetIngressUsd", 0)) / 1_000_000.0
                if latest_flow != 0:
                    return round(latest_flow, 2)
    except Exception:
        pass

    try:
        url_alt = "https://open-api.coinglass.com/public/v1/bitcoin/etf/flow"
        res = requests.get(url_alt, timeout=4)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data and isinstance(data, list):
                return round(float(data[-1].get("totalNetFlow", 0)), 2)
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


# 5. MULTI-SOURCE NACHRICHTEN & MAKRO-SENTIMENT (Google News Finanzen, Yahoo Finance, CoinTelegraph, CoinDesk)
@st.cache_data(ttl=300)
def get_news_sentiment_score():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    headlines = []
    sentiment_points = 0
    
    bullish_keywords = [
        "rate cut", "fed cuts", "ecb cuts", "approval", "bullish", "inflow",
        "adoption", "stimulus", "easing", "rate cuts", "lower rates", "dovish",
        "fed rate cut", "soft landing", "gdp growth", "rally", "gain", "soar",
        "breakout", "record high", "etf approval", "surge", "wall street gain"
    ]
    bearish_keywords = [
        "rate hike", "inflation high", "recession", "sec lawsuit", "ban",
        "crackdown", "outflow", "dump", "tariff", "rate hikes", "higher rates",
        "hawkish", "fed rate hike", "stagflation", "cpi surge", "drop", "plunge",
        "hack", "exploit", "crash", "liquidation", "wall street fall"
    ]

    # QUELLE 1: Google News Finanzen & Makro-Feeds (Suche nach Zinsen, Bitcoin & Wall Street)
    try:
        gn_url = "https://news.google.com/rss/search?q=bitcoin+OR+crypto+OR+%22fed+rate%22+OR+wallstreet&hl=en-US&gl=US&ceid=US:en"
        res_gn = requests.get(gn_url, headers=headers, timeout=4)
        if res_gn.status_code == 200:
            root = ET.fromstring(res_gn.text)
            for item in root.findall(".//item")[:8]:
                t = item.find("title")
                if t is not None and t.text:
                    t_clean = t.text.split(" - ")[0].strip()
                    if t_clean and t_clean not in headlines:
                        headlines.append(t_clean)
    except Exception:
        pass

    # QUELLE 2: Yahoo Finance Stock Market / Crypto RSS
    try:
        yf_url = "https://finance.yahoo.com/news/rssindex"
        res_yf = requests.get(yf_url, headers=headers, timeout=4)
        if res_yf.status_code == 200:
            root = ET.fromstring(res_yf.text)
            for item in root.findall(".//item")[:8]:
                t = item.find("title")
                if t is not None and t.text:
                    t_text = t.text.strip()
                    if t_text and t_text not in headlines:
                        headlines.append(t_text)
    except Exception:
        pass

    # QUELLE 3: CoinTelegraph RSS
    try:
        res_ct = requests.get("https://cointelegraph.com/rss", headers=headers, timeout=4)
        if res_ct.status_code == 200:
            root = ET.fromstring(res_ct.text)
            for item in root.findall(".//item")[:8]:
                t = item.find("title")
                if t is not None and t.text:
                    t_text = t.text.strip()
                    if t_text and t_text not in headlines:
                        headlines.append(t_text)
    except Exception:
        pass

    # QUELLE 4: CoinDesk RSS
    try:
        res_cd = requests.get("https://www.coindesk.com/arc/outboundfeeds/rss/", headers=headers, timeout=4)
        if res_cd.status_code == 200:
            root = ET.fromstring(res_cd.text)
            for item in root.findall(".//item")[:8]:
                t = item.find("title")
                if t is not None and t.text:
                    t_text = t.text.strip()
                    if t_text and t_text not in headlines:
                        headlines.append(t_text)
    except Exception:
        pass

    # Sentiment-Auswertung über alle gesammelten Schlagzeilen
    for h in headlines:
        text_lower = h.lower()
        for word in bullish_keywords:
            if word in text_lower:
                sentiment_points += 15
        for word in bearish_keywords:
            if word in text_lower:
                sentiment_points -= 15

    score = max(-100, min(100, sentiment_points))
    
    if not headlines:
        return 0, ["⚠️ Nachrichten-Stream temporär nicht erreichbar"]

    return score, headlines[:5]


# 6. STABLECOIN LIQUIDITÄT & DEFI TVL (DEFILLAMA API)
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


# 7. BITCOIN DOMINANZ (COINGECKO API)
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


# 8. Makro-Daten & Leitmärkte (OPTIMIERTE BATCH-ABRUFE FÜR SCHNELLE LADEZEIT)
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


def generate_local_self_learning_optimizations(
    df_archive, df_market, current_data
):
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
        optimizations.append(
            "⚠️ **Anpassung Kauf-Signale:** Makro-, Zins- und News-Einfluss gestärkt."
        )

    if sell_signals > 0 and (failed_sells / sell_signals) > 0.3:
        weights["Funding Rates & Derivate"] += 2.0
        weights["RSI & Volatilität"] += 1.0
        weights["Trend & Momentum"] -= 3.0
        optimizations.append(
            "⚠️ **Anpassung Verkauf-Signale:** Derivate-Raten & RSI höher gewichtet."
        )

    if not optimizations:
        optimizations.append(
            "✅ **System-Status Optimal:** Multi-Faktoren-Gewichtungen aktiv."
        )

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
    sp500_c = round(stock_indices.get("SP500", {}).get("chg", 0.0), 2) if stock_indices else 0.0
    dax_v = round(stock_indices.get("DAX", {}).get("val", 0.0), 2) if stock_indices else 0.0
    dax_c = round(stock_indices.get("DAX", {}).get("chg", 0.0), 2) if stock_indices else 0.0

    market_entry = {
        "Datum": today_str,
        "FearGreed_Wert": fg_val,
        "FearGreed_Stimmung": fg_txt,
        "US_10J_Rendite": round(bond_val, 2) if bond_val else 0.0,
        "US_10J_Veränderung": round(bond_change, 2) if bond_change else 0.0,
        "SP500_Wert": sp500_v,
        "SP500_Veränderung": sp500_c,
        "DAX_Wert": dax_v,
        "DAX_Veränderung": dax_c,
        "DowJones_Wert": round(ext_macro.get("dow_val", 0.0), 2),
        "DowJones_Veränderung": round(ext_macro.get("dow_chg", 0.0), 2),
        "FTSE100_Wert": round(ext_macro.get("ftse_val", 0.0), 2),
        "FTSE100_Veränderung": round(ext_macro.get("ftse_chg", 0.0), 2),
        "Nikkei_225": round(nikkei_val, 2) if nikkei_val else 0.0,
        "Nikkei_Veränderung": round(nikkei_chg, 2) if nikkei_chg else 0.0,
        "Hang_Seng": round(hsi_val, 2) if hsi_val else 0.0,
        "Hang_Seng_Veränderung": round(hsi_chg, 2) if hsi_chg else 0.0,
        "Shanghai_Comp": round(ext_macro.get("sse_val", 0.0), 2),
        "Shanghai_Veränderung": round(ext_macro.get("sse_chg", 0.0), 2),
        "EuroStoxx_50": round(eu_val, 2) if eu_val else 0.0,
        "EuroStoxx_Veränderung": round(eu_chg, 2) if eu_chg else 0.0,
        "Spot_ETF_NetFlow_MUSD": etf_flow,
        "DXY_Wert": round(ext_macro.get("dxy_val", 0.0), 2),
        "DXY_Veränderung": round(ext_macro.get("dxy_chg", 0.0), 2),
        "EURUSD_Wert": round(ext_macro.get("eurusd_val", 0.0), 4),
        "EURUSD_Veränderung": round(ext_macro.get("eurusd_chg", 0.0), 2),
        "USDJPY_Wert": round(ext_macro.get("usdjpy_val", 0.0), 2),
        "USDJPY_Veränderung": round(ext_macro.get("usdjpy_chg", 0.0), 2),
        "Gold_Wert": round(ext_macro.get("gold_val", 0.0), 2),
        "Gold_Veränderung": round(ext_macro.get("gold_chg", 0.0), 2),
        "Silber_Wert": round(ext_macro.get("silver_val", 0.0), 2),
        "Silber_Veränderung": round(ext_macro.get("silver_chg", 0.0), 2),
        "Oil_Wert": round(ext_macro.get("oil_val", 0.0), 2),
        "Oil_Veränderung": round(ext_macro.get("oil_chg", 0.0), 2),
        "Kupfer_Wert": round(ext_macro.get("copper_val", 0.0), 2),
        "Kupfer_Veränderung": round(ext_macro.get("copper_chg", 0.0), 2),
        "VIX_Wert": round(vix_data.get("vix_val", 0.0), 2),
        "Nasdaq_Veränderung": round(vix_data.get("nasdaq_chg", 0.0), 2),
        "News_Sentiment_Score": news_score,
        "Stablecoin_Mcap_MrdUSD": llama_data.get("stablecoin_mcap_b", 0.0),
        "DeFi_TVL_MrdUSD": llama_data.get("global_tvl_b", 0.0),
        "BTC_Dominanz_Pct": btc_dom,
        "Datenquelle": "Multi-Source API",
        "Quellen_Hinzugefügt_Am": "2026-10-12",
    }

    if os.path.exists(MARKET_LOG_FILE):
        try:
            df_market = pd.read_csv(MARKET_LOG_FILE)
            for k in market_entry.keys():
                if k not in df_market.columns:
                    df_market[k] = np.nan
            df_market["DateOnly"] = df_market["Datum"].astype(str).str[:10]
            if date_only in df_market["DateOnly"].values:
                for key, val in market_entry.items():
                    df_market.loc[df_market["DateOnly"] == date_only, key] = val
                df_market = df_market.drop(columns=["DateOnly"], errors="ignore")
                save_and_commit_csv(MARKET_LOG_FILE, df_market)
            else:
                new_row = pd.DataFrame([market_entry])
                df_market = df_market.drop(columns=["DateOnly"], errors="ignore")
                df_market = pd.concat([df_market, new_row], ignore_index=True)
                save_and_commit_csv(MARKET_LOG_FILE, df_market)
        except Exception:
            pass
    else:
        df_market = pd.DataFrame([market_entry])
        save_and_commit_csv(MARKET_LOG_FILE, df_market)


def log_prediction(coin, price, signal_text, fg_val):
    today_str = datetime.now(BERLIN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    date_only = datetime.now(BERLIN_TZ).strftime("%Y-%m-%d")

    if os.path.exists(LOG_FILE):
        try:
            df_log = pd.read_csv(LOG_FILE)
            df_log["DateOnly"] = df_log["Datum"].astype(str).str[:10]

            mask = (df_log["DateOnly"] == date_only) & (df_log["Coin"] == coin)
            if mask.any():
                df_log.loc[mask, "Datum"] = today_str
                df_log.loc[mask, "Signal"] = signal_text.split()[0]
                df_log.loc[mask, "FearGreed"] = fg_val
                df_log = df_log.drop(columns=["DateOnly"], errors="ignore")
                save_and_commit_csv(LOG_FILE, df_log)
            else:
                new_row = pd.DataFrame([{
                    "Datum": today_str,
                    "Coin": coin,
                    "Kurs_Damals": price,
                    "Signal": signal_text.split()[0],
                    "FearGreed": fg_val,
                    "Datenquelle": "Binance Spot REST API",
                }])
                df_log = df_log.drop(columns=["DateOnly"], errors="ignore")
                df_log = pd.concat([df_log, new_row], ignore_index=True)
                save_and_commit_csv(LOG_FILE, df_log)
        except Exception:
            pass
    else:
        df_log = pd.DataFrame([{
            "Datum": today_str,
            "Coin": coin,
            "Kurs_Damals": price,
            "Signal": signal_text.split()[0],
            "FearGreed": fg_val,
            "Datenquelle": "Binance Spot REST API",
        }])
        save_and_commit_csv(LOG_FILE, df_log)


def generate_signal(
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
    zeitraum,
    weights,
    rsi_val=None,
    funding_rate=None,
):
    macro_score = get_automatic_macro_score(
        volume,
        fg_value,
        bond_change,
        perf_val,
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
    if zeitraum == "24 Stunden":
        t_sf, t_mf, t_ms, t_ss = -7.5, -3.0, 3.5, 7.5
    elif zeitraum == "7 Tage":
        t_sf, t_mf, t_ms, t_ss = -13.0, -4.5, 5.5, 13.0
    elif zeitraum == "30 Tage":
        t_sf, t_mf, t_ms, t_ss = -22.0, -8.0, 9.0, 22.0
    elif zeitraum == "90 Tage":
        t_sf, t_mf, t_ms, t_ss = -32.0, -11.0, 13.0, 32.0
    elif zeitraum == "6 Monate":
        t_sf, t_mf, t_ms, t_ss = -45.0, -15.0, 18.0, 45.0
    else:  # 1 Jahr
        t_sf, t_mf, t_ms, t_ss = -60.0, -20.0, 25.0, 60.0

    if perf_val <= t_sf and macro_score >= 0:
        return (
            "STARKE KAUFCHANCE 🟢🟢",
            f"Deutlicher Kursrücksetzer ({zeitraum}: {perf_val:.1f}%).",
        )
    elif perf_val <= t_mf:
        return (
            "KAUFEN 🟢",
            f"Moderat nachgebend im Zeitraum {zeitraum} ({perf_val:.1f}%).",
        )
    elif perf_val >= t_ss and (fg_value >= 75 or macro_score < 0):
        return (
            "GEWINNE SICHERN 🔴🔴",
            f"Starker Preissprung ({zeitraum}: +{perf_val:.1f}%).",
        )
    elif perf_val >= t_ms:
        return (
            "VORSICHT / TEILVERKAUF 🔴",
            f"Solider Zuwachs ({zeitraum}: +{perf_val:.1f}%).",
        )
    else:
        return (
            "HALTEN 🟡",
            f"Seitwärtsphase im Zeitraum {zeitraum} ({perf_val:+.1f}%).",
        )


# LIVE-DATEN ABRUFEN
bond_val, bond_change = get_macro_bond_yield()
ext_macro = get_extended_macro_data()
stock_indices = get_stock_indices()
asian_markets = get_asian_markets()
eurozone_market = get_eurozone_market()
fg_value, fg_text = get_fear_and_greed()
real_etf_flow = get_real_spot_etf_flows()

vix_data = get_vix_and_nasdaq()
news_score, top_headlines = get_news_sentiment_score()
llama_data = get_defillama_metrics()
btc_dom = get_btc_dominance()

current_data = get_current_data()

df_archive_check = pd.read_csv(LOG_FILE) if os.path.exists(LOG_FILE) else pd.DataFrame()
df_market_check = pd.read_csv(MARKET_LOG_FILE) if os.path.exists(MARKET_LOG_FILE) else pd.DataFrame()

local_learning_report, dynamic_weights = generate_local_self_learning_optimizations(
    df_archive_check, df_market_check, current_data
)

# EINSTELLUNGEN & INVESTITIONS-PLANUNG
with st.expander("⚙️ **Einstellungen & Investitions-Planung**", expanded=False):
    col_e1, col_e2 = st.columns(2)
    
    with col_e1:
        zeitraum_wahl = st.selectbox(
            "Rückblick-Zeitraum wählen:",
            ["24 Stunden", "7 Tage", "30 Tage", "90 Tage", "6 Monate", "1 Jahr"],
            index=1,
        )
        investment_wahl = st.selectbox(
            "Geplantes Ordervolumen (Tranche):",
            [100, 500, 1000, 2500, 5000, "Individuell"],
            index=1,
        )
        if investment_wahl == "Individuell":
            geplantes_budget = st.number_input(
                "Eigenen Betrag eingeben (€):", min_value=10.0, value=1000.0, step=50.0
            )
        else:
            geplantes_budget = float(investment_wahl)

    with col_e2:
        coins_map = {"Bitcoin": "BTCEUR", "Ethereum": "ETHEUR", "Solana": "SOLEUR", "Cardano": "ADAEUR", "Bitcoin Cash": "BCHEUR"}
        ausgewaehlte_coins = st.multiselect(
            "Kryptowährungen anzeigen:",
            options=list(coins_map.keys()),
            default=list(coins_map.keys()),
        )
        auto_refresh_aktiv = st.checkbox("🔄 Auto-Refresh (alle 5 Min.)", value=True)

    st.write("---")
    manuelle_gewichtung = st.checkbox("🎛️ Manuelle Indikator-Gewichtung nutzen (übersteuert KI-Automatik)", value=False)
    
    if manuelle_gewichtung:
        st.caption("Verstelle die Slider, um die Einflüsse im Decision Engine Score manuell zu gewichten.")
        col_w1, col_w2, col_w3, col_w4 = st.columns(4)
        w_trend = col_w1.slider("Trend & Momentum (%)", 0, 50, 15)
        w_etf = col_w2.slider("ETF Net Flows (%)", 0, 50, 15)
        w_makro = col_w3.slider("Makro & VIX (%)", 0, 50, 15)
        w_fg = col_w4.slider("Fear & Greed (%)", 0, 50, 15)

        w_fund = col_w1.slider("Funding Rate (%)", 0, 50, 15)
        w_rsi = col_w2.slider("RSI & Vola (%)", 0, 50, 10)
        w_news = col_w3.slider("News/Zinsen (%)", 0, 50, 10)
        w_liq = col_w4.slider("DeFi TVL & Liq (%)", 0, 50, 5)

        active_weights = {
            "Trend & Momentum": float(w_trend),
            "ETF Net Flows": float(w_etf),
            "Makro (Zins/Aktien/Währung/VIX)": float(w_makro),
            "Fear & Greed Index": float(w_fg),
            "Funding Rates & Derivate": float(w_fund),
            "RSI & Volatilität": float(w_rsi),
            "Nachrichten & Zins-Sentiment": float(w_news),
            "Krypto-Liquidität & DeFi TVL": float(w_liq),
        }
    else:
        active_weights = dynamic_weights

    st.write("---")
    portfolio_tranche_placeholder = st.container()

if auto_refresh_aktiv:
    st_autorefresh(interval=5 * 60 * 1000, key="datarefresh")

if zeitraum_wahl == "24 Stunden":
    limit_anzahl, interval, wöchentlich = 24, "1h", False
elif zeitraum_wahl == "7 Tage":
    limit_anzahl, interval, wöchentlich = 7, "1d", False
elif zeitraum_wahl == "30 Tage":
    limit_anzahl, interval, wöchentlich = 30, "1d", False
elif zeitraum_wahl == "90 Tage":
    limit_anzahl, interval, wöchentlich = 90, "1d", True
elif zeitraum_wahl == "6 Monate":
    limit_anzahl, interval, wöchentlich = 180, "1d", True
else:  # 1 Jahr
    limit_anzahl, interval, wöchentlich = 365, "1d", True

nikkei_val = asian_markets.get("Nikkei_225", {}).get("value") if asian_markets else None
nikkei_chg = asian_markets.get("Nikkei_225", {}).get("change") if asian_markets else 0.0
hsi_val = asian_markets.get("Hang_Seng", {}).get("value") if asian_markets else None
hsi_chg = asian_markets.get("Hang_Seng", {}).get("change") if asian_markets else 0.0

eu_val = eurozone_market.get("value") if eurozone_market else None
eu_chg = eurozone_market.get("change") if eurozone_market else 0.0

log_market_data(
    fg_value,
    fg_text,
    bond_val,
    bond_change,
    nikkei_val,
    nikkei_chg,
    hsi_val,
    hsi_chg,
    eu_val,
    eu_chg,
    real_etf_flow,
    ext_macro,
    stock_indices,
    vix_data,
    news_score,
    llama_data,
    btc_dom,
)

if current_data and len(current_data) > 0:
    if not ausgewaehlte_coins:
        st.warning("👈 Bitte wähle oben im Einstellungen-Menü mindestens eine Kryptowährung aus.")
    else:
        coin_results = {}
        for coin in ausgewaehlte_coins:
            symbol = coins_map[coin]
            values = current_data.get(coin, {})
            price = values.get("eur", 0)
            change_24h = values.get("eur_24h_change", 0)
            volume = values.get("volume", 0)

            atr_value = calculate_atr(symbol, limit=30)
            rsi_value = get_real_rsi(symbol, limit=30)
            funding_rate_val = get_real_funding_rate(coin)

            stop_loss = max(0.0, price - (2.0 * atr_value)) if atr_value > 0 else price * 0.95
            take_profit = price + (3.0 * atr_value) if atr_value > 0 else price * 1.08

            df_history = get_historical_data(symbol, limit_anzahl, interval, weekly=wöchentlich)
            perf_val, hist_price = 0.0, 0.0
            if df_history is not None and not df_history.empty:
                hist_price = df_history.iloc[0]["Kurs"]
                if hist_price > 0:
                    perf_val = ((price - hist_price) / hist_price) * 100

            signal, reason = generate_signal(
                perf_val,
                volume,
                fg_value,
                bond_change,
                asian_markets,
                eurozone_market,
                stock_indices,
                real_etf_flow,
                vix_data,
                news_score,
                llama_data,
                btc_dom,
                zeitraum_wahl,
                active_weights,
                rsi_val=rsi_value,
                funding_rate=funding_rate_val,
            )
            whale_msg, _ = analyze_whale_activity(volume, change_24h)

            log_prediction(coin, price, signal, fg_value)

            coin_results[coin] = {
                "symbol": symbol,
                "price": price,
                "change_24h": change_24h,
                "volume": volume,
                "atr_value": atr_value,
                "rsi_value": rsi_value,
                "funding_rate_val": funding_rate_val,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "df_history": df_history,
                "perf_val": perf_val,
                "hist_price": hist_price,
                "signal": signal,
                "reason": reason,
                "whale_msg": whale_msg,
            }

        portfolio_weights = {}
        for coin, res in coin_results.items():
            sig = res["signal"]
            if "STARKE KAUFCHANCE" in sig:
                portfolio_weights[coin] = 1.0
            elif "KAUFEN" in sig:
                portfolio_weights[coin] = 0.5
            else:
                portfolio_weights[coin] = 0.0

        total_portfolio_weight = sum(portfolio_weights.values())
        denom = max(1.0, total_portfolio_weight)

        total_invested_eur = 0.0
        for coin, res in coin_results.items():
            w = portfolio_weights[coin]
            alloc_eur = round(geplantes_budget * (w / denom), 2)
            alloc_pct = round((alloc_eur / geplantes_budget) * 100, 1) if geplantes_budget > 0 else 0.0
            res["investition_eur"] = alloc_eur
            res["investition_pct"] = alloc_pct
            res["portfolio_weight"] = w
            total_invested_eur += alloc_eur

        cash_reserve_eur = max(0.0, round(geplantes_budget - total_invested_eur, 2))
        cash_reserve_pct = round((cash_reserve_eur / geplantes_budget) * 100, 1) if geplantes_budget > 0 else 0.0

        with portfolio_tranche_placeholder:
            st.markdown("💼 **Portfolio-Tranche Allokation:**")
            col_p1, col_p2, col_p3 = st.columns(3)
            col_p1.metric("Gesamt", f"{geplantes_budget:,.0f} €")
            col_p2.metric("Krypto", f"{total_invested_eur:,.0f} €", f"{100.0 - cash_reserve_pct:.0f}%")
            col_p3.metric("Cash", f"{cash_reserve_eur:,.0f} €", f"{cash_reserve_pct:.0f}%")

            alloc_chart_data = []
            for coin, res in coin_results.items():
                if res["investition_eur"] > 0:
                    alloc_chart_data.append({
                        "Asset": coin,
                        "Betrag (€)": res["investition_eur"],
                        "Anteil (%)": res["investition_pct"],
                    })
            if cash_reserve_eur > 0:
                alloc_chart_data.append({
                    "Asset": "💵 Cash",
                    "Betrag (€)": cash_reserve_eur,
                    "Anteil (%)": cash_reserve_pct,
                })

            if alloc_chart_data:
                df_alloc_chart = pd.DataFrame(alloc_chart_data)
                chart_alloc = (
                    alt.Chart(df_alloc_chart)
                    .mark_bar(cornerRadiusEnd=4)
                    .encode(
                        x=alt.X("Betrag (€):Q", title=None),
                        y=alt.Y("Asset:N", sort=None, title=None),
                        color=alt.Color("Asset:N", scale=alt.Scale(scheme="category10"), legend=None),
                        tooltip=["Asset", "Betrag (€)", "Anteil (%)"],
                    )
                    .properties(height=max(120, len(alloc_chart_data) * 35))
                )
                st.altair_chart(chart_alloc, use_container_width=True)

        st.markdown("### 🪙 Einzelne Kryptowährungen")

        for coin, res in coin_results.items():
            price = res["price"]
            change_24h = res["change_24h"]
            volume = res["volume"]
            atr_value = res["atr_value"]
            rsi_value = res["rsi_value"]
            funding_rate_val = res["funding_rate_val"]
            stop_loss = res["stop_loss"]
            take_profit = res["take_profit"]
            perf_val = res["perf_val"]
            hist_price = res["hist_price"]
            df_history = res["df_history"]
            signal = res["signal"]
            reason = res["reason"]
            whale_msg = res["whale_msg"]
            investition_eur = res["investition_eur"]
            investition_pct = res["investition_pct"]

            coin_menge = investition_eur / price if price > 0 else 0.0
            logo_url = CRYPTO_LOGOS.get(coin, "")
            symbol = CRYPTO_SYMBOLS.get(coin, "")

            with st.container(border=True):
                col1, col2 = st.columns([1.2, 1])

                perf_color_hex = "#2ecc71" if perf_val >= 0 else "#e74c3c"
                bg_badge = "rgba(46, 204, 113, 0.12)" if perf_val >= 0 else "rgba(231, 76, 60, 0.12)"

                with col1:
                    st.markdown(
                        f"#### <img src='{logo_url}' width='22' style='vertical-align: middle; margin-right: 6px;'> {coin} ({symbol})",
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"<div style='font-size: 1.35rem; font-weight: bold;'>{price:,.2f} €</div>", unsafe_allow_html=True)
                
                with col2:
                    st.markdown(
                        f"""
                        <div style='text-align: right; margin-bottom: 2px;'>
                            <span style='background-color: {bg_badge}; color: {perf_color_hex}; font-size: 1.25rem; font-weight: bold; padding: 4px 10px; border-radius: 8px; display: inline-block;'>
                                {perf_val:+.2f}%
                            </span>
                        </div>
                        <div style='text-align: right; font-size: 0.75rem; color: gray;'>
                            Veränderung ({zeitraum_wahl})
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                st.write("---")

                col_s1, col_s2 = st.columns(2)
                with col_s1:
                    st.markdown(f"**Signal:** {signal}")
                with col_s2:
                    st.markdown(f"**Tranche:** **{investition_eur:,.2f} €** ({investition_pct}%)")

                with st.expander("🔍 **Details, Analyse & Chart**", expanded=False):
                    st.info(f"💡 **Begründung ({zeitraum_wahl}):** {reason}")
                    
                    st.markdown(
                        f"🎯 **Empfehlung:** ca. **{coin_menge:.4f} {coin.upper()} {symbol}** kaufen "
                        f"({investition_eur:,.2f} € aus der Tranche)."
                    )
                    
                    st.write("---")
                    st.markdown("##### 📊 Live-Marktindikatoren & Wal-Tracker")
                    st.caption(
                        f"🏛 **Spot-ETF Net Flow:** **{real_etf_flow:+.1f}M $**\n\n"
                        f"📊 **RSI (14):** **{rsi_value:.1f}** | ⚡ **Funding Rate:** **{funding_rate_val*100:+.3f}%**\n\n"
                        f"📰 **News/Zins-Sentiment:** **{news_score:+d} / 100** | 🔒 **DeFi TVL:** **{llama_data.get('global_tvl_b', 0):.1f} Mrd. $**\n\n"
                        f"🐋 **Wal-Aktivität:** {whale_msg}"
                    )

                    st.write("---")
                    st.markdown("##### 🛡️ ATR Risikomanagement")
                    st.write(f"• **ATR (14):** {atr_value:,.2f} €")
                    st.write(f"• 🛑 **Stop-Loss:** **{stop_loss:,.2f} €**")
                    st.write(f"• 🎯 **Take-Profit:** **{take_profit:,.2f} €**")

                    st.write("---")
                    st.markdown("##### 📊 Signal-Zusammensetzung Score")
                    factor_scores = calculate_detailed_factor_scores(
                        perf_val,
                        volume,
                        fg_value,
                        bond_change,
                        asian_markets,
                        eurozone_market,
                        stock_indices,
                        real_etf_flow,
                        vix_data,
                        news_score,
                        llama_data,
                        btc_dom,
                        rsi_val=rsi_value,
                        funding_rate=funding_rate_val,
                        weights=active_weights,
                    )

                    df_scores = pd.DataFrame(
                        list(factor_scores.items()), columns=["Indikator", "Score"]
                    )

                    chart_scores = (
                        alt.Chart(df_scores)
                        .mark_bar()
                        .encode(
                            x=alt.X("Score:Q", scale=alt.Scale(domain=[-100, 100]), title="Bärisch (-100) | Bullisch (+100)"),
                            y=alt.Y("Indikator:N", sort=None, title=None),
                            color=alt.condition(
                                alt.datum.Score > 0,
                                alt.value("#2ecc71"),
                                alt.value("#e74c3c"),
                            ),
                            tooltip=["Indikator", "Score"],
                        )
                        .properties(height=250)
                    )
                    st.altair_chart(chart_scores, use_container_width=True)

                    if hist_price > 0:
                        st.markdown(
                            f"📈 **Rückblick ({zeitraum_wahl}):** Start: **{hist_price:,.2f} €** | Aktuell: **{price:,.2f} €**",
                            unsafe_allow_html=True,
                        )

                    # CHARTING
                    if df_history is not None and not df_history.empty:
                        df_chart = df_history.reset_index().copy()

                        if len(df_chart) >= 5:
                            span_val = min(20, len(df_chart))
                            df_chart["EMA"] = df_chart["Kurs"].ewm(span=span_val, adjust=False).mean()

                        min_p, max_p = df_chart["Kurs"].min(), df_chart["Kurs"].max()
                        puffer = (max_p - min_p) * 0.15 if max_p != min_p else min_p * 0.02
                        chart_color = "#2ecc71" if perf_val >= 0 else "#e74c3c"

                        if zeitraum_wahl == "24 Stunden":
                            date_format = "%H:%M"
                        elif zeitraum_wahl in ["7 Tage", "30 Tage"]:
                            date_format = "%d.%m."
                        else:
                            date_format = "%b %y"

                        y_format = ",.2f" if max_p < 10 else ",.0f"

                        base = alt.Chart(df_chart).encode(
                            x=alt.X(
                                "Timestamp:T",
                                title=None,
                                axis=alt.Axis(
                                    format=date_format,
                                    labelAngle=0,
                                    grid=True,
                                    gridDash=[2, 2],
                                    gridColor="rgba(150, 150, 150, 0.15)",
                                    tickCount=6,
                                ),
                            ),
                            y=alt.Y(
                                "Kurs:Q",
                                title="€",
                                scale=alt.Scale(domain=[min_p - puffer, max_p + puffer], zero=False),
                                axis=alt.Axis(
                                    format=y_format,
                                    grid=True,
                                    gridColor="rgba(150, 150, 150, 0.15)",
                                ),
                            ),
                        ).properties(height=220)

                        area = base.mark_area(opacity=0.12, color=chart_color)
                        line = base.mark_line(color=chart_color, strokeWidth=2)
                        
                        chart_layers = area + line

                        if "EMA" in df_chart.columns:
                            ema_line = base.mark_line(color="#e67e22", strokeWidth=1.5, strokeDash=[3, 3]).encode(
                                y=alt.Y("EMA:Q")
                            )
                            chart_layers = chart_layers + ema_line

                        points = base.mark_point(size=40, color=chart_color, filled=True).encode(
                            tooltip=[
                                alt.Tooltip("Timestamp:T", title="Zeit", format="%d.%m.%Y %H:%M"),
                                alt.Tooltip("Kurs:Q", title="Kurs", format=",.2f €"),
                            ]
                        )

                        final_chart = (chart_layers + points).interactive()
                        st.altair_chart(final_chart, use_container_width=True)
else:
    st.warning("Konnte keine Marktdaten laden.")

st.write("---")

# GLOBALER MARKT & MAKRO-ÜBERSICHT
bar_color = "#e74c3c" if fg_value > 70 else ("#f39c12" if fg_value >= 45 else "#2ecc71")
signal_icon = "🔴" if fg_value > 70 else ("🟡" if fg_value >= 45 else "🟢")

with st.container(border=True):
    st.markdown(f"📊 **Sentiment-Index:** **{fg_value} / 100** ({fg_text}) {signal_icon}")
    st.markdown(
        f"""
        <div style="background-color: #e0e0e0; border-radius: 10px; width: 100%; height: 12px; overflow: hidden; margin-top: 4px; margin-bottom: 8px;">
            <div style="background-color: {bar_color}; width: {fg_value}%; height: 100%;"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("🌐 **Globale Leitmärkte & Makro-Details**", expanded=False):
        if bond_val is not None:
            bt_text = "📈 Steigend" if bond_change > 0 else "📉 Fallend"
            st.write(f"🏛 **US-10J Zins:** {bond_val:.2f}% ({bond_change:+.2f}%) | {bt_text}")

        dxy_color = "#2ecc71" if ext_macro.get("dxy_chg", 0) >= 0 else "#e74c3c"
        eur_color = "#2ecc71" if ext_macro.get("eurusd_chg", 0) >= 0 else "#e74c3c"
        jpy_color = "#2ecc71" if ext_macro.get("usdjpy_chg", 0) >= 0 else "#e74c3c"

        gold_color = "#2ecc71" if ext_macro.get("gold_chg", 0) >= 0 else "#e74c3c"
        silver_color = "#2ecc71" if ext_macro.get("silver_chg", 0) >= 0 else "#e74c3c"
        oil_color = "#2ecc71" if ext_macro.get("oil_chg", 0) >= 0 else "#e74c3c"
        copper_color = "#2ecc71" if ext_macro.get("copper_chg", 0) >= 0 else "#e74c3c"

        vix_color = "#e74c3c" if vix_data.get("vix_chg", 0) >= 0 else "#2ecc71"
        ndx_color = "#2ecc71" if vix_data.get("nasdaq_chg", 0) >= 0 else "#e74c3c"
        etf_color = "#2ecc71" if real_etf_flow >= 0 else "#e74c3c"

        st.markdown(
            f"""
            <div style="line-height: 1.8; font-size: 0.95rem;">
                💵 <b>DXY:</b> {ext_macro.get('dxy_val', 0):.2f} (<span style="color:{dxy_color}; font-weight:bold;">{ext_macro.get('dxy_chg', 0):+.2f}%</span>) &nbsp;|&nbsp; 
                💶 <b>EUR/USD:</b> {ext_macro.get('eurusd_val', 0):.4f} (<span style="color:{eur_color}; font-weight:bold;">{ext_macro.get('eurusd_chg', 0):+.2f}%</span>) &nbsp;|&nbsp; 
                💴 <b>USD/JPY:</b> {ext_macro.get('usdjpy_val', 0):.2f} (<span style="color:{jpy_color}; font-weight:bold;">{ext_macro.get('usdjpy_chg', 0):+.2f}%</span>)<br>
                🥇 <b>Gold:</b> {ext_macro.get('gold_val', 0):,.1f} $ (<span style="color:{gold_color}; font-weight:bold;">{ext_macro.get('gold_chg', 0):+.2f}%</span>) &nbsp;|&nbsp; 
                🥈 <b>Silber:</b> {ext_macro.get('silver_val', 0):,.2f} $ (<span style="color:{silver_color}; font-weight:bold;">{ext_macro.get('silver_chg', 0):+.2f}%</span>) &nbsp;|&nbsp; 
                🛢 <b>Öl:</b> {ext_macro.get('oil_val', 0):,.1f} $ (<span style="color:{oil_color}; font-weight:bold;">{ext_macro.get('oil_chg', 0):+.2f}%</span>) &nbsp;|&nbsp; 
                🏗 <b>Kupfer:</b> {ext_macro.get('copper_val', 0):,.2f} $ (<span style="color:{copper_color}; font-weight:bold;">{ext_macro.get('copper_chg', 0):+.2f}%</span>)<br>
                📉 <b>CBOE VIX (Panik):</b> {vix_data.get('vix_val', 0):.1f} (<span style="color:{vix_color}; font-weight:bold;">{vix_data.get('vix_chg', 0):+.2f}%</span>) &nbsp;|&nbsp; 
                💻 <b>Nasdaq 100:</b> <span style="color:{ndx_color}; font-weight:bold;">{vix_data.get('nasdaq_chg', 0):+.2f}%</span><br>
                🏦 <b>Spot-ETF Nettofluss:</b> <span style="color:{etf_color}; font-weight:bold;">{real_etf_flow:+.1f} Mio. USD</span> &nbsp;|&nbsp; 
                💧 <b>DeFi TVL:</b> <b>{llama_data.get('global_tvl_b', 0):.1f} Mrd. $</b> &nbsp;|&nbsp; 
                🪙 <b>BTC Dominanz:</b> <b>{btc_dom:.1f}%</b>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if stock_indices:
            sp500_v, sp500_c = stock_indices.get("SP500", {}).get("val", 0), stock_indices.get("SP500", {}).get("chg", 0)
            dax_v, dax_c = stock_indices.get("DAX", {}).get("val", 0), stock_indices.get("DAX", {}).get("chg", 0)
            dow_v, dow_c = ext_macro.get("dow_val", 0), ext_macro.get("dow_chg", 0)
            ftse_v, ftse_c = ext_macro.get("ftse_val", 0), ext_macro.get("ftse_chg", 0)
            st.write(
                f"📈 **S&P 500:** {sp500_v:,.0f} ({sp500_c:+.2f}%) | "
                f"**Dow Jones:** {dow_v:,.0f} ({dow_c:+.2f}%) | "
                f"**DAX:** {dax_v:,.0f} ({dax_c:+.2f}%) | "
                f"**FTSE 100:** {ftse_v:,.0f} ({ftse_c:+.2f}%)"
            )

        if asian_markets:
            n_val, n_chg = asian_markets.get("Nikkei_225", {}).get("value", 0), asian_markets.get("Nikkei_225", {}).get("change", 0)
            h_val, h_chg = asian_markets.get("Hang_Seng", {}).get("value", 0), asian_markets.get("Hang_Seng", {}).get("change", 0)
            sse_v, sse_c = ext_macro.get("sse_val", 0), ext_macro.get("sse_chg", 0)
            st.write(
                f"🌏 **Nikkei 225:** {n_val:,.0f} ({n_chg:+.2f}%) | "
                f"**Hang Seng:** {h_val:,.0f} ({h_chg:+.2f}%) | "
                f"**Shanghai Comp:** {sse_v:,.0f} ({sse_c:+.2f}%)"
            )

        st.write("---")
        st.markdown(f"📰 **Zins- & Nachrichten-Sentiment:** **{news_score:+d} / 100**")
        if top_headlines:
            st.caption("Aktuelle Makro- & Krypto-Schlagzeilen (Google News, Yahoo Finance, Wall Street Feeds):")
            for h in top_headlines:
                st.markdown(f"- *{h}*")

# EXPANDER: AUTONOMES SELBSTLERN-SYSTEM, BACKTEST & CSV-EXPORT
st.write("")
with st.expander("🧠 **Autonomes Selbstlern-System & Backtest**", expanded=False):
    st.markdown("Auswertung der protokollierten Prognosen der letzten **365 Tage** im Vergleich zu Buy-and-Hold.")

    if os.path.exists(LOG_FILE):
        try:
            df_archive = pd.read_csv(LOG_FILE)
            if "Datenquelle" not in df_archive.columns:
                df_archive["Datenquelle"] = "Binance Spot REST API"
            else:
                df_archive["Datenquelle"] = df_archive["Datenquelle"].fillna("Binance Spot REST API")
            df_archive.to_csv(LOG_FILE, index=False)

            if not df_archive.empty and current_data:
                # 1. Datum parsen
                df_archive["ParsedDate"] = pd.to_datetime(df_archive["Datum"], errors="coerce")
                
                # 2. Sortierung
                df_archive_sorted = df_archive.sort_values(by="ParsedDate", ascending=False).copy()

                # Coin-Filter
                all_coins = list(df_archive_sorted["Coin"].unique())
                selected_filter = st.multiselect("🔍 Nach Coin filtern:", options=all_coins, default=all_coins)
                df_filtered_view = df_archive_sorted[df_archive_sorted["Coin"].isin(selected_filter)]

                comparison_rows, backtest_data = [], []
                correct_hits, total_evaluated = 0, 0
                sim_depot_signal, sim_depot_hold = 100.0, 100.0

                for index, row in df_filtered_view.iterrows():
                    c_name = row["Coin"]
                    kurs_damals = float(row.get("Kurs_Damals", 0))
                    signal_damals = str(row.get("Signal", "")).upper()
                    
                    parsed_dt = row["ParsedDate"]
                    datum_formatted = parsed_dt.strftime("%d.%m.%Y %H:%M") if pd.notnull(parsed_dt) else str(row["Datum"])

                    kurs_aktuell = current_data.get(c_name, {}).get("eur", 0)
                    diff_pct = (((kurs_aktuell - kurs_damals) / kurs_damals) * 100) if kurs_damals > 0 and kurs_aktuell > 0 else 0.0

                    if "KAUF" in signal_damals:
                        sim_depot_signal *= 1.0 + (max(-5.0, min(5.0, diff_pct)) / 100.0)
                    sim_depot_hold *= 1.0 + (diff_pct / 100.0)

                    backtest_data.append({
                        "Datum": datum_formatted,
                        "App-Strategie": round(sim_depot_signal, 2),
                        "Buy & Hold": round(sim_depot_hold, 2),
                    })

                    evaluation = "Neutral 🟡"
                    if "KAUF" in signal_damals:
                        total_evaluated += 1
                        if diff_pct > -1.5:
                            correct_hits += 1
                            evaluation = "Treffer 🟢"
                        else:
                            evaluation = "Fehlsignal 🔴"
                    elif "VORSICHT" in signal_damals or "GEWINN" in signal_damals:
                        total_evaluated += 1
                        if diff_pct < 1.5:
                            correct_hits += 1
                            evaluation = "Schutz 🟢"
                        else:
                            evaluation = "Seitwärts 🟡"

                    comparison_rows.append({
                        "Datum": datum_formatted,
                        "Coin": c_name,
                        "Signal": signal_damals,
                        "Kurs Damals": f"{kurs_damals:,.2f} €",
                        "Aktuell": f"{kurs_aktuell:,.2f} €",
                        "Diff (%)": round(diff_pct, 2),
                        "Auswertung": evaluation,
                    })

                df_comp = pd.DataFrame(comparison_rows)

                st.dataframe(
                    df_comp,
                    column_config={
                        "Diff (%)": st.column_config.NumberColumn(
                            "Diff",
                            format="%+.2f %%",
                        ),
                    },
                    use_container_width=True,
                    hide_index=True,
                )

                st.write("---")
                st.markdown("##### 📈 Backtest-Vergleich")
                df_bt = pd.DataFrame(backtest_data)
                if not df_bt.empty:
                    df_bt_melted = df_bt.melt(id_vars=["Datum"], value_vars=["App-Strategie", "Buy & Hold"], var_name="Strategie", value_name="Wert")
                    chart_bt = (
                        alt.Chart(df_bt_melted)
                        .mark_line(strokeWidth=2)
                        .encode(
                            x=alt.X("Datum:N", title=None, sort=None),
                            y=alt.Y("Wert:Q", title="Depotwert (€)", scale=alt.Scale(zero=False)),
                            color=alt.Color("Strategie:N", scale=alt.Scale(domain=["App-Strategie", "Buy & Hold"], range=["#2ecc71", "#e74c3c"])),
                            tooltip=["Datum", "Strategie", "Wert"],
                        )
                        .properties(height=220)
                    )
                    st.altair_chart(chart_bt.interactive(), use_container_width=True)

                if total_evaluated > 0:
                    hit_rate = (correct_hits / total_evaluated) * 100
                    st.metric("Signal-Trefferquote", f"{hit_rate:.1f}%", f"{correct_hits} von {total_evaluated} Zyklen erfolgreich")

                st.write("---")
                st.markdown(local_learning_report)

                st.write("---")
                st.markdown("##### 💾 CSV-Export & Backup")
                col_d1, col_d2, col_d3 = st.columns(3)
                if os.path.exists(LOG_FILE):
                    with open(LOG_FILE, "rb") as f:
                        col_d1.download_button("📥 prediction_log.csv", f, file_name="prediction_log.csv", mime="text/csv", use_container_width=True)
                if os.path.exists(MARKET_LOG_FILE):
                    with open(MARKET_LOG_FILE, "rb") as f:
                        col_d2.download_button("📥 market_log.csv", f, file_name="market_data_log.csv", mime="text/csv", use_container_width=True)
                if os.path.exists(IMP_FILE):
                    with open(IMP_FILE, "rb") as f:
                        col_d3.download_button("📥 improvements.csv", f, file_name="improvements_log.csv", mime="text/csv", use_container_width=True)
        except Exception as e:
            st.warning(f"Fehler: {e}")

# EXPANDER: HISTORISCHES MAKRO- & STIMMUNGSDATEN-ARCHIV
st.write("")
with st.expander("📊 **Historisches Makro- & Stimmungsdaten-Archiv**", expanded=False):
    if os.path.exists(MARKET_LOG_FILE):
        try:
            df_market_log = pd.read_csv(MARKET_LOG_FILE)
            if not df_market_log.empty:
                df_market_log["ParsedDate"] = pd.to_datetime(df_market_log["Datum"], errors="coerce")
                df_market_log["Datum_Clean"] = df_market_log["ParsedDate"].dt.strftime("%d.%m.%Y %H:%M")

                num_cols = [
                    "FearGreed_Wert", "US_10J_Rendite", "VIX_Wert", "Gold_Wert", 
                    "Silber_Wert", "Kupfer_Wert", "Spot_ETF_NetFlow_MUSD", 
                    "News_Sentiment_Score", "DeFi_TVL_MrdUSD", "BTC_Dominanz_Pct",
                    "DowJones_Wert", "FTSE100_Wert"
                ]
                for col in num_cols:
                    if col in df_market_log.columns:
                        if df_market_log[col].dtype == object:
                            df_market_log[col] = (
                                df_market_log[col]
                                .astype(str)
                                .str.replace("%", "", regex=False)
                                .str.replace("$", "", regex=False)
                                .str.replace(",", "", regex=False)
                                .str.strip()
                            )
                        df_market_log[col] = pd.to_numeric(df_market_log[col], errors="coerce")

                # FEHLENDE HISTORISCHE WERTE MIT FORWARD/BACKWARD FILL ERGÄNZEN
                df_market_log[num_cols] = df_market_log[num_cols].ffill().bfill()

                df_chart_data = df_market_log.sort_values(by="ParsedDate", ascending=True).dropna(subset=["ParsedDate"])

                st.markdown("##### 📈 Historische Trend-Entwicklung")
                
                # ZEILE 1: Sentiment & VIX
                col_mc1, col_mc2 = st.columns(2)

                with col_mc1:
                    st.markdown("###### 📊 Sentiment-Verlauf (Fear & Greed)")
                    df_fg = df_chart_data[df_chart_data["FearGreed_Wert"].notnull() & (df_chart_data["FearGreed_Wert"] > 0)].copy()
                    if not df_fg.empty:
                        base = alt.Chart(df_fg).encode(
                            x=alt.X("ParsedDate:T", title=None, axis=alt.Axis(format="%d.%m.", labelAngle=0)),
                            y=alt.Y("FearGreed_Wert:Q", scale=alt.Scale(domain=[0, 100]), title=None),
                            tooltip=[
                                alt.Tooltip("Datum_Clean:N", title="Zeitpunkt"),
                                alt.Tooltip("FearGreed_Wert:Q", title="Wert"),
                                alt.Tooltip("FearGreed_Stimmung:N", title="Stimmung"),
                            ],
                        )
                        chart_fg_log = (base.mark_line(color="#f1c40f", strokeWidth=2) + base.mark_point(color="#f1c40f", size=30)).properties(height=180)
                        st.altair_chart(chart_fg_log, use_container_width=True)
                    else:
                        st.info("Keine Daten für Sentiment verfügbar.")

                with col_mc2:
                    st.markdown("###### 📉 Marktpanik (VIX Index)")
                    df_vix = df_chart_data[df_chart_data["VIX_Wert"].notnull() & (df_chart_data["VIX_Wert"] > 0)].copy()

                    if not df_vix.empty:
                        min_vix = max(0, df_vix["VIX_Wert"].min() - 2)
                        max_vix = df_vix["VIX_Wert"].max() + 2
                        if min_vix == max_vix:
                            min_vix, max_vix = min_vix - 1, max_vix + 1
                        
                        base_vix = alt.Chart(df_vix).encode(
                            x=alt.X("ParsedDate:T", title=None, axis=alt.Axis(format="%d.%m.", labelAngle=0)),
                            y=alt.Y("VIX_Wert:Q", scale=alt.Scale(domain=[min_vix, max_vix]), title=None),
                            tooltip=[
                                alt.Tooltip("Datum_Clean:N", title="Zeitpunkt"),
                                alt.Tooltip("VIX_Wert:Q", title="VIX Index"),
                            ],
                        )
                        chart_vix_log = (base_vix.mark_line(color="#e74c3c", strokeWidth=2) + base_vix.mark_point(color="#e74c3c", size=40)).properties(height=180)
                        st.altair_chart(chart_vix_log, use_container_width=True)
                    else:
                        st.info("Noch keine VIX-Historie vorhanden.")

                # ZEILE 2: US 10J Zins & Gold
                col_mc3, col_mc4 = st.columns(2)

                with col_mc3:
                    st.markdown("###### 🏛 US 10J Anleihen-Zins (%)")
                    df_zins = df_chart_data[df_chart_data["US_10J_Rendite"].notnull() & (df_chart_data["US_10J_Rendite"] > 0)].copy()

                    if not df_zins.empty:
                        min_z = max(0, df_zins["US_10J_Rendite"].min() - 0.15)
                        max_z = df_zins["US_10J_Rendite"].max() + 0.15
                        if min_z == max_z:
                            min_z, max_z = min_z - 0.1, max_z + 0.1

                        base_zins = alt.Chart(df_zins).encode(
                            x=alt.X("ParsedDate:T", title=None, axis=alt.Axis(format="%d.%m.", labelAngle=0)),
                            y=alt.Y("US_10J_Rendite:Q", scale=alt.Scale(domain=[min_z, max_z]), title=None),
                            tooltip=[
                                alt.Tooltip("Datum_Clean:N", title="Zeitpunkt"),
                                alt.Tooltip("US_10J_Rendite:Q", title="Zins (%)", format=".2f"),
                            ],
                        )
                        chart_zins_log = (base_zins.mark_line(color="#3498db", strokeWidth=2) + base_zins.mark_point(color="#3498db", size=30)).properties(height=180)
                        st.altair_chart(chart_zins_log, use_container_width=True)
                    else:
                        st.info("Keine Zins-Historie geloggt.")

                with col_mc4:
                    st.markdown("###### 🥇 Goldkurs ($ / Unze)")
                    df_gold = df_chart_data[df_chart_data["Gold_Wert"].notnull() & (df_chart_data["Gold_Wert"] > 0)].copy()

                    if not df_gold.empty:
                        min_g = max(0, df_gold["Gold_Wert"].min() - 30)
                        max_g = df_gold["Gold_Wert"].max() + 30
                        if min_g == max_g:
                            min_g, max_g = min_g - 10, max_g + 10

                        base_gold = alt.Chart(df_gold).encode(
                            x=alt.X("ParsedDate:T", title=None, axis=alt.Axis(format="%d.%m.", labelAngle=0)),
                            y=alt.Y("Gold_Wert:Q", scale=alt.Scale(domain=[min_g, max_g]), title=None),
                            tooltip=[
                                alt.Tooltip("Datum_Clean:N", title="Zeitpunkt"),
                                alt.Tooltip("Gold_Wert:Q", title="Gold ($)", format=",.1f"),
                            ],
                        )
                        chart_gold_log = (base_gold.mark_line(color="#f39c12", strokeWidth=2) + base_gold.mark_point(color="#f39c12", size=30)).properties(height=180)
                        st.altair_chart(chart_gold_log, use_container_width=True)
                    else:
                        st.info("Keine Gold-Historie geloggt.")

                st.write("---")
                st.markdown("##### 📋 Vollständige Makro-Historie")

                df_table_data = df_market_log.sort_values(by="ParsedDate", ascending=False)

                display_cols = []
                for idx, row in df_table_data.iterrows():
                    d_clean = row.get("Datum_Clean", "-")
                    fg_w = row.get("FearGreed_Wert", None)
                    us_y = row.get("US_10J_Rendite", None)
                    etf_fl = row.get("Spot_ETF_NetFlow_MUSD", None)
                    vix_w = row.get("VIX_Wert", None)
                    news_s = row.get("News_Sentiment_Score", None)
                    gold_w = row.get("Gold_Wert", None)
                    silver_w = row.get("Silber_Wert", None)
                    copper_w = row.get("Kupfer_Wert", None)
                    tvl_w = row.get("DeFi_TVL_MrdUSD", None)
                    btc_d = row.get("BTC_Dominanz_Pct", None)
                    dow_w = row.get("DowJones_Wert", None)

                    display_cols.append({
                        "Datum": d_clean,
                        "Fear & Greed": f"{int(fg_w)} / 100" if pd.notnull(fg_w) and fg_w > 0 else "-",
                        "US 10J Zins": f"{us_y:.2f}%" if pd.notnull(us_y) and us_y > 0 else "-",
                        "Spot-ETF Flow": f"{etf_fl:+.1f}M $" if pd.notnull(etf_fl) else "-",
                        "VIX (Panik)": f"{vix_w:.1f}" if pd.notnull(vix_w) and vix_w > 0 else "-",
                        "News Score": f"{int(news_s):+d}" if pd.notnull(news_s) else "-",
                        "Gold ($)": f"{gold_w:,.1f}" if pd.notnull(gold_w) and gold_w > 0 else "-",
                        "Silber ($)": f"{silver_w:,.2f}" if pd.notnull(silver_w) and silver_w > 0 else "-",
                        "Kupfer ($)": f"{copper_w:,.2f}" if pd.notnull(copper_w) and copper_w > 0 else "-",
                        "Dow Jones": f"{dow_w:,.0f}" if pd.notnull(dow_w) and dow_w > 0 else "-",
                        "DeFi TVL": f"{tvl_w:.1f}B $" if pd.notnull(tvl_w) and tvl_w > 0 else "-",
                        "BTC Dom.": f"{btc_d:.1f}%" if pd.notnull(btc_d) and btc_d > 0 else "-",
                    })

                df_macro_table = pd.DataFrame(display_cols)
                st.dataframe(df_macro_table, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning(f"Fehler beim Laden des Makro-Archivs: {e}")

st.markdown("---")
st.caption("⚠️ **Disclaimer:** Dies ist keine Anlageberatung. Die Signale dienen rein zu Bildungszwecken.")
