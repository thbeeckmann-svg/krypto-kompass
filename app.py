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

LOG_FILE = "prediction_log.csv"
MARKET_LOG_FILE = "market_data_log.csv"

# --- DEUTSCHE ZAHLENFORMATIERUNG ---
def format_ger(value, decimals=2, force_sign=False):
    if value is None or pd.isna(value):
        return "0,00"
    try:
        val_float = float(value)
        if force_sign:
            fmt_str = f"{{:+,.{decimals}f}}".format(val_float)
        else:
            fmt_str = f"{{:,.{decimals}f}}".format(val_float)
        return fmt_str.replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return str(value)

# Seiten-Konfiguration
st.set_page_config(
    page_title="Krypto-Kompass",
    page_icon="🪙",
    layout="centered",
)

# --- HANDY-OPTIMIERTES CSS ---
hide_streamlit_style = """
    <style>
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    header { visibility: hidden; }
    .stAppDeployButton { display: none !important; }
    div[data-testid="stDecoration"] { display: none !important; }
    div[data-testid="stViewerBadge"] { display: none !important; }
    .stAppViewerBadge { display: none !important; }
    div[class*="viewerBadge"] { display: none !important; }
    [data-testid="manage-app-button"] { display: none !important; }
    
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

# --- AUTOMATISCHER GITHUB CLOUD-SPEICHER ---
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

def clean_old_logs(file_path, days_limit=1095):
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

clean_old_logs(LOG_FILE, 1095)
clean_old_logs(MARKET_LOG_FILE, 1095)

# --- FREIE LIVE-DATEN ABRUFE (MULTI-SOURCE API) ---
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

@st.cache_data(ttl=30)
def get_kraken_orderbook_pressure(coin_name="Bitcoin"):
    kraken_pairs = {"Bitcoin": "XXBTZEUR", "Ethereum": "XETHZEUR", "Solana": "SOLEUR", "Cardano": "ADAEUR", "Bitcoin Cash": "BCHEUR"}
    pair = kraken_pairs.get(coin_name, "XXBTZEUR")
    try:
        url = f"https://api.kraken.com/0/public/Depth?pair={pair}&count=25"
        res = requests.get(url, timeout=4)
        if res.status_code == 200:
            data = res.json().get("result", {})
            key = list(data.keys())[0] if data else None
            if key:
                bids = data[key].get("bids", [])
                asks = data[key].get("asks", [])
                bid_volume = sum([float(b[1]) for b in bids])
                ask_volume = sum([float(a[1]) for a in asks])
                total = bid_volume + ask_volume
                if total > 0:
                    pressure = ((bid_volume - ask_volume) / total) * 100
                    return round(pressure, 1)
    except:
        pass
    return 0.0

@st.cache_data(ttl=300)
def get_real_spot_etf_flows():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        r = requests.get("https://farside.co.uk/wp-content/uploads/ptool/US%20Spot%20Bitcoin%20ETF/flow.csv", headers=headers, timeout=4)
        if r.status_code == 200 and len(r.text.strip()) > 0:
            lines = [l.strip() for l in r.text.split("\n") if l.strip()]
            for line in reversed(lines):
                parts = [p.strip() for p in line.split(",") if p.strip()]
                if parts:
                    val_str = parts[-1].replace('$', '').replace(',', '').replace('(', '-').replace(')', '')
                    if val_str and val_str.lower() != 'date' and val_str != '-':
                        try:
                            val_float = float(val_str)
                            if val_float != 0.0 or len(parts) > 1:
                                return round(val_float, 2)
                        except ValueError:
                            pass
    except Exception:
        pass

    try:
        r2 = requests.get("https://open-api-v4.coinglass.com/api/etf/bitcoin/flow-history", headers=headers, timeout=4)
        if r2.status_code == 200:
            res_json = r2.json()
            data_list = res_json.get("data", [])
            if data_list and isinstance(data_list, list):
                latest = data_list[-1]
                flow_val = latest.get("flow_usd", latest.get("totalNetInflow", 0))
                if flow_val != 0:
                    return round(float(flow_val) / 1e6, 2)
    except Exception:
        pass

    return -45.5

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

@st.cache_data(ttl=60)
def get_vix_and_nasdaq():
    data = {"vix_val": 18.0, "vix_chg": 0.0, "nasdaq_val": 0.0, "nasdaq_chg": 0.0}
    try:
        v_hist = yf.Ticker("^VIX").history(period="5d")
        if not v_hist.empty:
            val = float(v_hist["Close"].iloc[-1])
            prev = float(v_hist["Close"].iloc[0])
            data["vix_val"] = val
            data["vix_chg"] = float(((val - prev) / prev) * 100) if prev > 0 else 0.0
    except: pass
    try:
        n_hist = yf.Ticker("^NDX").history(period="5d")
        if not n_hist.empty:
            n_val = float(n_hist["Close"].iloc[-1])
            n_prev = float(n_hist["Close"].iloc[0])
            data["nasdaq_val"] = n_val
            data["nasdaq_chg"] = float(((n_val - n_prev) / n_prev) * 100) if n_prev > 0 else 0.0
    except: pass
    return data

@st.cache_data(ttl=120)
def translate_to_german(text):
    if not text: return text
    t = text
    replacements = {
        "says": "sagt,", "reports": "berichtet,", "Report:": "Bericht:",
        "looking to enter": "plant den Einstieg in", "developing markets": "Schwellenländer",
        "euro-area economy is resilient": "Eurozonen-Wirtschaft ist widerstandsfähig",
        "digital euro imminent": "digitaler Euro steht bevor",
        "to discontinue services for some users": "stellt Dienste für einige Nutzer ein",
        "in one-off exercise": "im Rahmen einer einmaligen Maßnahme",
        "Here's what happened in crypto today": "Das ist heute im Krypto-Markt passiert",
        "Solana shows resilience, but some areas need continued observation": "Solana zeigt Widerstandskraft, aber einige Bereiche erfordern fortgesetzte Beobachtung",
        "escalate spending against": "verstärkt Ausgaben gegen",
        "Senate race": "Senatsrennen", "market update": "Markt-Update",
        "show steady institutional inflows": "zeigen stabile institutionelle Zuflüsse",
        "Macroeconomic outlook": "Makroökonomischer Ausblick",
        "Federal Reserve policy remains in focus": "Politik der US-Notenbank im Fokus",
        "DeFi liquidity expands": "DeFi-Liquidität wächst",
        "as total value locked reaches key resistance": "während der Gesamtwert (TVL) wichtige Widerstände erreicht",
        "Surge": "Anstieg", "Rally": "Rallye", "Gain": "Gewinn", "Drop": "Rückgang", "Fall": "Fall", "Crash": "Crash"
    }
    for eng, ger in replacements.items():
        t = t.replace(eng, ger)
    return t

@st.cache_data(ttl=120)
def get_news_sentiment_score():
    headers = {"User-Agent": "Mozilla/5.0"}
    news_items = []
    sentiment_points = 25

    cryptopanic_token = st.secrets.get("CRYPTOPANIC_API_KEY", "")
    if cryptopanic_token:
        try:
            cp_url = f"https://cryptopanic.com/api/v1/posts/?auth_token={cryptopanic_token}&public=true"
            res = requests.get(cp_url, timeout=4)
            if res.status_code == 200:
                results = res.json().get("results", [])
                for post in results[:5]:
                    title = post.get("title", "")
                    url = post.get("url", "https://cryptopanic.com")
                    if title:
                        news_items.append((title, url))
        except:
            pass

    if not news_items:
        try:
            res = requests.get("https://cointelegraph.com/rss", headers=headers, timeout=4)
            if res.status_code == 200:
                root = ET.fromstring(res.text)
                for item in root.findall(".//item")[:5]:
                    title_elem = item.find("title")
                    link_elem = item.find("link")
                    if title_elem is not None and title_elem.text:
                        title = title_elem.text.strip()
                        link = link_elem.text.strip() if link_elem is not None and link_elem.text else "https://cointelegraph.com"
                        news_items.append((title, link))
        except: pass

    if len(news_items) < 3:
        try:
            res = requests.get("https://www.coindesk.com/arc/outbound-feeds/rss/", headers=headers, timeout=4)
            if res.status_code == 200:
                root = ET.fromstring(res.text)
                for item in root.findall(".//item")[:5]:
                    title_elem = item.find("title")
                    link_elem = item.find("link")
                    if title_elem is not None and title_elem.text:
                        title = title_elem.text.strip()
                        link = link_elem.text.strip() if link_elem is not None and link_elem.text else "https://www.coindesk.com"
                        if not any(n[0] == title for n in news_items):
                            news_items.append((title, link))
        except: pass

    if not news_items:
        news_items = [
            ("Market Update: Bitcoin and Ethereum show steady institutional inflows", "https://cointelegraph.com"),
            ("Macroeconomic outlook: Federal Reserve policy remains in focus", "https://cointelegraph.com"),
            ("DeFi liquidity expands as total value locked reaches key resistance", "https://cointelegraph.com")
        ]
        sentiment_points = 20
    else:
        for title, _ in news_items:
            t_low = title.lower()
            if any(w in t_low for w in ["bull", "surge", "rally", "gain", "up", "high", "growth", "approval", "jump", "record"]):
                sentiment_points += 18
            if any(w in t_low for w in ["bear", "drop", "fall", "down", "low", "crash", "ban", "inflation", "loss", "risk"]):
                sentiment_points -= 18

    translated_news = [(translate_to_german(title), link) for title, link in news_items[:5]]
    return max(-100, min(100, sentiment_points)), translated_news

@st.cache_data(ttl=600)
def get_defillama_metrics():
    res_data = {"stablecoin_mcap_b": 135.0, "global_tvl_b": 85.0}
    try:
        res = requests.get("https://stablecoins.llama.fi/stablecoins?includePrices=true", timeout=5)
        if res.status_code == 200:
            data = res.json().get("peggedAssets", []) if isinstance(res.json(), dict) else []
            usdt_mcap = next((i for i in data if i.get("symbol") == "USDT"), {}).get("circulating", {}).get("peggedUSD", 0)
            usdc_mcap = next((i for i in data if i.get("symbol") == "USDC"), {}).get("circulating", {}).get("peggedUSD", 0)
            res_data["stablecoin_mcap_b"] = round((usdt_mcap + usdc_mcap) / 1e9, 2)
    except: pass
    try:
        tvl_res = requests.get("https://api.llama.fi/v2/chains", timeout=5)
        if tvl_res.status_code == 200:
            chains = tvl_res.json()
            if isinstance(chains, list):
                res_data["global_tvl_b"] = round(sum(c.get("tvl", 0) for c in chains if isinstance(c, dict)) / 1e9, 2)
    except: pass
    return res_data

@st.cache_data(ttl=300)
def get_btc_dominance():
    try:
        res = requests.get("https://api.coingecko.com/api/v3/global", timeout=4)
        if res.status_code == 200: return round(res.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 56.5), 1)
    except: pass
    return 56.5

@st.cache_data(ttl=60)
def get_macro_bond_yield():
    try:
        hist = yf.Ticker("^TNX").history(period="5d")
        if not hist.empty: return float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-1] - hist["Close"].iloc[0])
    except: pass
    return None, 0.0

@st.cache_data(ttl=60)
def get_extended_macro_data():
    data = {}
    tickers_map = {"dxy": "DX-Y.NYB", "eurusd": "EURUSD=X", "usdjpy": "JPY=X", "gold": "GC=F", "silver": "SI=F", "oil": "CL=F", "copper": "HG=F", "dow": "^DJI", "ftse": "^FTSE", "sse": "000001.SS"}
    for key, sym in tickers_map.items():
        try:
            h = yf.Ticker(sym).history(period="5d")
            if not h.empty and len(h) >= 1:
                val = float(h["Close"].iloc[-1])
                prev = float(h["Close"].iloc[0])
                data[f"{key}_val"] = val
                data[f"{key}_chg"] = ((val - prev) / prev) * 100 if prev > 0 else 0.0
            else:
                data[f"{key}_val"], data[f"{key}_chg"] = 0.0, 0.0
        except:
            data[f"{key}_val"], data[f"{key}_chg"] = 0.0, 0.0
    return data

@st.cache_data(ttl=60)
def get_stock_indices():
    results = {}
    for name, ticker in {"SP500": "^GSPC", "DAX": "^GDAXI"}.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if not hist.empty:
                val = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[0])
                results[name] = {"val": val, "chg": ((val - prev) / prev) * 100 if prev > 0 else 0.0}
        except: pass
    return results

@st.cache_data(ttl=60)
def get_asian_markets():
    data = {}
    try:
        nikkei_hist = yf.Ticker("^N225").history(period="5d")
        if not nikkei_hist.empty:
            data["Nikkei_225"] = {"value": float(nikkei_hist["Close"].iloc[-1]), "change": ((float(nikkei_hist["Close"].iloc[-1]) - float(nikkei_hist["Close"].iloc[0])) / float(nikkei_hist["Close"].iloc[0])) * 100}
    except: pass
    try:
        hsi_hist = yf.Ticker("^HSI").history(period="5d")
        if not hsi_hist.empty:
            data["Hang_Seng"] = {"value": float(hsi_hist["Close"].iloc[-1]), "change": ((float(hsi_hist["Close"].iloc[-1]) - float(hsi_hist["Close"].iloc[0])) / float(hsi_hist["Close"].iloc[0])) * 100}
    except: pass
    return data if data else None

@st.cache_data(ttl=60)
def get_eurozone_market():
    try:
        hist = yf.Ticker("^STOXX50E").history(period="5d")
        if not hist.empty: return {"value": float(hist["Close"].iloc[-1]), "change": ((float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[0])) / float(hist["Close"].iloc[0])) * 100}
    except: pass
    return None

def analyze_whale_activity(volume, change_24h):
    if volume > 2000000000 and change_24h < -1.5:
        return ("🔴 Whale-Warnung: Hohe Börsenzuflüsse", -1)
    elif volume > 2000000000 and change_24h > 1.5:
        return ("🟢 Whale-Akkumulation: OTC-Käufe", 1)
    else:
        return ("🟡 Unauffällige On-Chain-Aktivität", 0)

@st.cache_data(ttl=30)
def calculate_atr(symbol, current_price=0.0, limit=30):
    symbol_yf_map = {"BTCEUR": "BTC-EUR", "ETHEUR": "ETH-EUR", "SOLEUR": "SOL-EUR", "ADAEUR": "ADA-EUR", "BCHEUR": "BCH-EUR"}
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            raw = response.json()
            if isinstance(raw, list) and len(raw) > 5:
                df = pd.DataFrame(raw, columns=["ot", "o", "high", "low", "close", "v", "ct", "qav", "nt", "tbb", "tbq", "i"])
                df[["high", "low", "close"]] = df[["high", "low", "close"]].astype(float)
                df["tr"] = df[["high"]].sub(df["low"], axis=0).combine(df["high"].sub(df["close"].shift(1)).abs(), max).combine(df["low"].sub(df["close"].shift(1)).abs(), max)
                val = float(df["tr"].rolling(window=14).mean().iloc[-1])
                if val > 0: return val
    except: pass
    try:
        df = yf.Ticker(symbol_yf_map.get(symbol, "BTC-EUR")).history(period="60d")
        if not df.empty and len(df) >= 15:
            df[["high", "low", "close"]] = df[["High", "Low", "Close"]].astype(float)
            df["tr"] = df[["high"]].sub(df["low"], axis=0).combine(df["high"].sub(df["close"].shift(1)).abs(), max).combine(df["low"].sub(df["close"].shift(1)).abs(), max)
            val = float(df["tr"].rolling(window=14).mean().iloc[-1])
            if val > 0: return val
    except: pass
    if current_price > 0: return current_price * 0.03
    return 0.0

@st.cache_data(ttl=30)
def get_real_rsi(symbol, limit=30):
    symbol_yf_map = {"BTCEUR": "BTC-EUR", "ETHEUR": "ETH-EUR", "SOLEUR": "SOL-EUR", "ADAEUR": "ADA-EUR", "BCHEUR": "BCH-EUR"}
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            df = pd.DataFrame(response.json(), columns=["ot", "o", "h", "l", "close", "v", "ct", "qav", "nt", "tbb", "tbq", "i"])
            delta = df["close"].astype(float).diff()
            rs = (delta.where(delta > 0, 0)).rolling(window=14).mean() / (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            val = round(float(100 - (100 / (1 + rs)).iloc[-1]), 2)
            if not pd.isna(val): return val
    except: pass
    try:
        df = yf.Ticker(symbol_yf_map.get(symbol, "BTC-EUR")).history(period="60d")
        if not df.empty and len(df) >= 15:
            delta = df["Close"].astype(float).diff()
            rs = (delta.where(delta > 0, 0)).rolling(window=14).mean() / (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            val = round(float(100 - (100 / (1 + rs)).iloc[-1]), 2)
            if not pd.isna(val): return val
    except: pass
    return 50.0

@st.cache_data(ttl=60)
def get_real_funding_rate(coin_name="Bitcoin"):
    futures_map = {"Bitcoin": "BTCUSDT", "Ethereum": "ETHUSDT", "Solana": "SOLUSDT", "Cardano": "ADAUSDT", "Bitcoin Cash": "BCHUSDT"}
    try:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={futures_map.get(coin_name, 'BTCUSDT')}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200: return float(response.json().get("lastFundingRate", 0.0001))
    except: pass
    return 0.0001

# --- QUANT- & Z-SCORE PROGNOSE-LOGIK ---
def evaluate_learning_data(df_archive, current_data):
    stats = {"buy_wins": 0, "buy_fails": 0, "sell_wins": 0, "sell_fails": 0, "total_checked": 0}
    if df_archive is None or df_archive.empty or not current_data: return stats

    for _, row in df_archive.iterrows():
        parsed_dt = pd.to_datetime(row.get("Datum", ""), errors="coerce")
        if pd.isnull(parsed_dt): continue
        
        time_diff_hours = (datetime.now(BERLIN_TZ).replace(tzinfo=None) - parsed_dt).total_seconds() / 3600.0
        if time_diff_hours < 24: continue
        
        c_name = row.get("Coin", "")
        price_then = float(row.get("Kurs_Damals", 0))
        signal = str(row.get("Signal", "")).upper()
        curr_price = current_data.get(c_name, {}).get("eur", 0)
        
        if price_then > 0 and curr_price > 0:
            stats["total_checked"] += 1
            diff_pct = ((curr_price - price_then) / price_then) * 100
            if "KAUF" in signal:
                if diff_pct >= -1.5: stats["buy_wins"] += 1
                else: stats["buy_fails"] += 1
            elif "VORSICHT" in signal or "GEWINN" in signal:
                if diff_pct <= 1.5: stats["sell_wins"] += 1
                else: stats["sell_fails"] += 1
    return stats

def generate_local_self_learning_optimizations(df_archive, current_data):
    default_weights = {
        "Trend & Momentum": 15.0, "ETF Net Flows": 15.0, "Makro (Zins/Aktien/Währung/VIX)": 15.0,
        "Fear & Greed Index": 15.0, "Kraken Orderbuch-Druck": 15.0, "RSI & Volatilität": 10.0,
        "Nachrichten & Zins-Sentiment": 10.0, "Krypto-Liquidität & DeFi TVL": 5.0,
    }

    if df_archive is None or df_archive.empty or not current_data:
        return default_weights

    stats = evaluate_learning_data(df_archive, current_data)
    buy_signals = stats["buy_wins"] + stats["buy_fails"]
    sell_signals = stats["sell_wins"] + stats["sell_fails"]

    weights = default_weights.copy()
    if buy_signals > 0 and (stats["buy_fails"] / buy_signals) > 0.3:
        weights["Trend & Momentum"] -= 2.0
        weights["Makro (Zins/Aktien/Währung/VIX)"] += 2.0
        weights["Nachrichten & Zins-Sentiment"] += 1.0
    if sell_signals > 0 and (stats["sell_fails"] / sell_signals) > 0.3:
        weights["Kraken Orderbuch-Druck"] += 2.0
        weights["RSI & Volatilität"] += 1.0
        weights["Trend & Momentum"] -= 3.0

    return weights

def calculate_detailed_factor_scores(perf_val, volume, fg_value, bond_change, asian_markets, eurozone_market, stock_indices, etf_net_flow, vix_data, news_score, llama_data, btc_dom, kraken_pressure=0.0, rsi_val=None, weights=None):
    if weights is None:
        weights = {
            "Trend & Momentum": 15.0, 
            "ETF Net Flows": 15.0, 
            "Makro (Zins/Aktien/Währung/VIX)": 15.0, 
            "Fear & Greed Index": 15.0, 
            "Kraken Orderbuch-Druck": 15.0, 
            "RSI & Volatilität": 10.0, 
            "Nachrichten & Zins-Sentiment": 10.0, 
            "Krypto-Liquidität & DeFi TVL": 5.0
        }

    if perf_val <= -7.5: trend_score = 80
    elif perf_val <= -3.0: trend_score = 40
    elif perf_val >= 7.5: trend_score = -80
    elif perf_val >= 3.5: trend_score = -40
    else: trend_score = 5 if perf_val <= 0 else -5

    sentiment_score = 90 if fg_value <= 25 else -90 if fg_value >= 75 else (50 - fg_value) * 1.5

    if etf_net_flow >= 200.0: etf_score = 90
    elif etf_net_flow >= 50.0: etf_score = 50
    elif etf_net_flow <= -200.0: etf_score = -90
    elif etf_net_flow <= -50.0: etf_score = -50
    else: etf_score = 5 if etf_net_flow >= 0 else -5
    
    kraken_score = max(-100.0, min(100.0, kraken_pressure * 2.5))

    if rsi_val is not None:
        if rsi_val <= 28:
            rsi_score = 100.0
        elif rsi_val <= 35:
            rsi_score = 60.0
        elif rsi_val >= 72:
            rsi_score = -100.0
        else:
            rsi_score = max(-100.0, min(100.0, (50.0 - rsi_val) * 2.8))
    else:
        rsi_score = 70 if perf_val < -6.0 else (-70 if perf_val > 6.0 else 0)

    macro_points = 30 if bond_change < -0.05 else (-30 if bond_change > 0.05 else 0)
    if stock_indices:
        if stock_indices.get("SP500", {}).get("chg", 0.0) > 0.5 or stock_indices.get("DAX", {}).get("chg", 0.0) > 0.5: 
            macro_points += 15
        elif stock_indices.get("SP500", {}).get("chg", 0.0) < -0.5 or stock_indices.get("DAX", {}).get("chg", 0.0) < -0.5: 
            macro_points -= 15

    vix_val = vix_data.get("vix_val", 18.0)
    nasdaq_chg = vix_data.get("nasdaq_chg", 0.0)
    
    if vix_val > 25.0 and etf_net_flow < 0.0:
        macro_points -= 70
        etf_score -= 50

    if vix_val > 28.0: 
        macro_points -= 30
    elif vix_val < 15.0: 
        macro_points += 20
        
    if nasdaq_chg > 1.0: 
        macro_points += 20
    elif nasdaq_chg < -1.0: 
        macro_points -= 20

    tvl = llama_data.get("global_tvl_b", 85.0)
    liq_score = 70 if tvl >= 90.0 else 30 if tvl >= 70.0 else -20

    w_trend = weights.get('Trend & Momentum', 15.0)
    w_etf = weights.get('ETF Net Flows', 15.0)
    w_macro = weights.get('Makro (Zins/Aktien/Währung/VIX)', 15.0)
    w_fg = weights.get('Fear & Greed Index', 15.0)
    w_kraken = weights.get('Kraken Orderbuch-Druck', 15.0)
    w_rsi = weights.get('RSI & Volatilität', 10.0)
    w_news = weights.get('Nachrichten & Zins-Sentiment', 10.0)
    w_liq = weights.get('Krypto-Liquidität & DeFi TVL', 5.0)

    return {
        f"Trend & Momentum ({w_trend:.0f}%)": trend_score,
        f"ETF Net Flows ({w_etf:.0f}%)": etf_score,
        f"Makro & VIX ({w_macro:.0f}%)": max(-100, min(100, macro_points)),
        f"Fear & Greed ({w_fg:.0f}%)": sentiment_score,
        f"Kraken Orderbuch ({w_kraken:.0f}%)": kraken_score,
        f"RSI & Volatilität ({w_rsi:.0f}%)": rsi_score,
        f"News & Zins ({w_news:.0f}%)": max(-100, min(100, news_score)),
        f"DeFi TVL & Liq. ({w_liq:.0f}%)": liq_score,
    }

def get_automatic_macro_score(volume, fg_value, bond_change, change_24h, asian_markets, eurozone_market, stock_indices, etf_net_flow, vix_data, news_score, llama_data, btc_dom, kraken_pressure=0.0, rsi_val=None, weights=None):
    scores = calculate_detailed_factor_scores(change_24h, volume, fg_value, bond_change, asian_markets, eurozone_market, stock_indices, etf_net_flow, vix_data, news_score, llama_data, btc_dom, kraken_pressure=kraken_pressure, rsi_val=rsi_val, weights=weights)
    total = 0.0
    for key, score_val in scores.items():
        base_key = key.split(" (")[0].strip()
        matched_w = 0.0
        for wk, wv in weights.items():
            if wk.lower() in base_key.lower() or base_key.lower() in wk.lower() or ("etf" in wk.lower() and "etf" in base_key.lower()) or ("trend" in wk.lower() and "trend" in base_key.lower()) or ("kraken" in wk.lower() and "kraken" in base_key.lower()):
                matched_w = wv
                break
        total += score_val * (matched_w / 100.0)
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
                dates = [pd.to_datetime(entry[0], unit='ms').tz_localize('UTC').tz_convert(BERLIN_TZ).tz_localize(None) for entry in raw_data]
                df = pd.DataFrame({"Timestamp": dates, "Kurs": [float(entry[4]) for entry in raw_data]}).set_index("Timestamp")
                return df.resample("W").mean().dropna().sort_index(ascending=True) if weekly else df.sort_index(ascending=True)
    except: pass
    try:
        t = yf.Ticker(symbol_yf_map.get(symbol, "BTC-EUR"))
        hist = t.history(period="2d" if interval == "1h" else f"{limit_anzahl + 10}d", interval="1h" if interval == "1h" else ("1wk" if weekly else "1d"))
        if not hist.empty:
            hist = hist.tail(limit_anzahl)
            dates = [pd.to_datetime(d).tz_convert(BERLIN_TZ).tz_localize(None) if hasattr(d, "tzinfo") and d.tzinfo is not None else pd.to_datetime(d) for d in hist.index]
            df = pd.DataFrame({"Timestamp": dates, "Kurs": hist["Close"].astype(float).values}).set_index("Timestamp")
            return df.resample("W").mean().dropna().sort_index(ascending=True) if weekly else df.sort_index(ascending=True)
    except: pass
    return None

def log_market_data(fg_val, fg_txt, bond_val, bond_change, nikkei_val, nikkei_chg, hsi_val, hsi_chg, eu_val, eu_chg, etf_flow, ext_macro, stock_indices, vix_data, news_score, llama_data, btc_dom):
    today_str = datetime.now(BERLIN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    date_only = datetime.now(BERLIN_TZ).strftime("%Y-%m-%d")

    market_entry = {
        "Datum": today_str, "FearGreed_Wert": fg_val, "FearGreed_Stimmung": fg_txt,
        "US_10J_Rendite": round(bond_val, 2) if bond_val else 0.0, "US_10J_Veränderung": round(bond_change, 2) if bond_change else 0.0,
        "SP500_Wert": round(stock_indices.get("SP500", {}).get("val", 0.0), 2) if stock_indices else 0.0,
        "SP500_Veränderung": round(stock_indices.get("SP500", {}).get("chg", 0.0), 2) if stock_indices else 0.0,
        "DAX_Wert": round(stock_indices.get("DAX", {}).get("val", 0.0), 2) if stock_indices else 0.0,
        "DAX_Veränderung": round(stock_indices.get("DAX", {}).get("chg", 0.0), 2) if stock_indices else 0.0,
        "DowJones_Wert": round(ext_macro.get("dow_val", 0.0), 2), "DowJones_Veränderung": round(ext_macro.get("dow_chg", 0.0), 2),
        "FTSE100_Wert": round(ext_macro.get("ftse_val", 0.0), 2), "FTSE100_Veränderung": round(ext_macro.get("ftse_chg", 0.0), 2),
        "Nikkei_225": round(nikkei_val, 2) if nikkei_val else 0.0, "Nikkei_Veränderung": round(nikkei_chg, 2) if nikkei_chg else 0.0,
        "Hang_Seng": round(hsi_val, 2) if hsi_val else 0.0, "Hang_Seng_Veränderung": round(hsi_chg, 2) if hsi_chg else 0.0,
        "Shanghai_Comp": round(ext_macro.get("sse_val", 0.0), 2), "Shanghai_Veränderung": round(ext_macro.get("sse_chg", 0.0), 2),
        "EuroStoxx_50": round(eu_val, 2) if eu_val else 0.0, "EuroStoxx_Veränderung": round(eu_chg, 2) if eu_chg else 0.0,
        "Spot_ETF_NetFlow_MUSD": etf_flow, "DXY_Wert": round(ext_macro.get("dxy_val", 0.0), 2), "DXY_Veränderung": round(ext_macro.get("dxy_chg", 0.0), 2),
        "EURUSD_Wert": round(ext_macro.get("eurusd_val", 0.0), 4), "EURUSD_Veränderung": round(ext_macro.get("eurusd_chg", 0.0), 2),
        "USDJPY_Wert": round(ext_macro.get("usdjpy_val", 0.0), 2), "USDJPY_Veränderung": round(ext_macro.get("usdjpy_chg", 0.0), 2),
        "Gold_Wert": round(ext_macro.get("gold_val", 0.0), 2), "Gold_Veränderung": round(ext_macro.get("gold_chg", 0.0), 2),
        "Silber_Wert": round(ext_macro.get("silver_val", 0.0), 2), "Silber_Veränderung": round(ext_macro.get("silver_chg", 0.0), 2),
        "Oil_Wert": round(ext_macro.get("oil_val", 0.0), 2), "Oil_Veränderung": round(ext_macro.get("oil_chg", 0.0), 2),
        "Kupfer_Wert": round(ext_macro.get("copper_val", 0.0), 2), "Kupfer_Veränderung": round(ext_macro.get("copper_chg", 0.0), 2),
        "VIX_Wert": round(vix_data.get("vix_val", 0.0), 2), "Nasdaq_Veränderung": round(vix_data.get("nasdaq_chg", 0.0), 2),
        "News_Sentiment_Score": news_score, "Stablecoin_Mcap_MrdUSD": llama_data.get("stablecoin_mcap_b", 0.0),
        "DeFi_TVL_MrdUSD": llama_data.get("global_tvl_b", 0.0), "BTC_Dominanz_Pct": btc_dom, "Datenquelle": "Multi-Source API",
    }

    if os.path.exists(MARKET_LOG_FILE):
        try:
            df_market = pd.read_csv(MARKET_LOG_FILE)
            for k in market_entry.keys():
                if k not in df_market.columns: df_market[k] = np.nan
            df_market["DateOnly"] = df_market["Datum"].astype(str).str[:10]
            if date_only in df_market["DateOnly"].values:
                for key, val in market_entry.items():
                    df_market.loc[df_market["DateOnly"] == date_only, key] = val
                df_market = df_market.drop(columns=["DateOnly"], errors="ignore")
                save_and_commit_csv(MARKET_LOG_FILE, df_market)
            else:
                df_market = pd.concat([df_market.drop(columns=["DateOnly"], errors="ignore"), pd.DataFrame([market_entry])], ignore_index=True)
                save_and_commit_csv(MARKET_LOG_FILE, df_market)
        except: pass
    else: save_and_commit_csv(MARKET_LOG_FILE, pd.DataFrame([market_entry]))

def log_prediction(coin, price, signal_text, fg_val, target_horizon="7 Tage"):
    today_str = datetime.now(BERLIN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    date_only = datetime.now(BERLIN_TZ).strftime("%Y-%m-%d")

    new_row_data = {
        "Datum": today_str, 
        "Coin": coin, 
        "Kurs_Damals": float(price),
        "Signal": str(signal_text).split()[0], 
        "FearGreed": int(fg_val),
        "Target_Horizon": str(target_horizon), 
        "Datenquelle": "Binance Spot REST API"
    }

    if os.path.exists(LOG_FILE):
        try:
            df_log = pd.read_csv(LOG_FILE)
            for col in new_row_data.keys():
                if col not in df_log.columns:
                    df_log[col] = ""
            
            df_log["DateOnly"] = df_log["Datum"].astype(str).str[:10]
            mask = (df_log["DateOnly"] == date_only) & (df_log["Coin"] == coin)
            
            if mask.any():
                for key, val in new_row_data.items():
                    df_log.loc[mask, key] = val
            else:
                df_log = pd.concat([df_log, pd.DataFrame([new_row_data])], ignore_index=True)
            
            df_log = df_log.drop(columns=["DateOnly"], errors="ignore")
            save_and_commit_csv(LOG_FILE, df_log)
        except Exception:
            pd.DataFrame([new_row_data]).to_csv(LOG_FILE, index=False)
    else:
        save_and_commit_csv(LOG_FILE, pd.DataFrame([new_row_data]))

# SIGNAL-LOGIK (Z-SCORE & KRAKEN ORDERBUCH)
def generate_signal(price, hist_prices, volume, fg_value, bond_change, asian_markets, eurozone_market, stock_indices, etf_net_flow, vix_data, news_score, llama_data, btc_dom, kraken_pressure, zeitraum, weights, rsi_val=None):
    macro_score = get_automatic_macro_score(volume, fg_value, bond_change, 0.0, asian_markets, eurozone_market, stock_indices, etf_net_flow, vix_data, news_score, llama_data, btc_dom, kraken_pressure=kraken_pressure, rsi_val=rsi_val, weights=weights)
    vix_val = vix_data.get("vix_val", 18.0)

    if hist_prices is not None and len(hist_prices) >= 5:
        series = pd.Series(hist_prices)
        window_size = max(3, min(15, len(series) - 1))
        ma = series.rolling(window=window_size).mean().iloc[-1]
        std = series.rolling(window=window_size).std().iloc[-1]
        z_score = (price - ma) / std if std and std > 0 else 0.0
    else:
        z_score = 0.0

    risk_off = (vix_val > 25.0) and (etf_net_flow < 0)

    if risk_off and z_score < -1.2:
        return ("VORSICHT / KEIN KAUF 🔴", f"Liquidations-Schutz aktiv: VIX bei {vix_val:.1f} und ETF-Abflüsse trotz statistischem Rücksetzer (Z-Score: {z_score:.2f}).")

    if z_score <= -1.8 and macro_score >= 0 and (rsi_val is None or rsi_val <= 33) and not risk_off:
        return ("STARKE KAUFCHANCE 🟢🟢", f"Statistisch stark überverkauft (Z-Score: {z_score:.2f}, RSI: {rsi_val:.1f}) in gesundem Makro-Umfeld.")
    elif z_score <= -0.9 and macro_score >= 0 and not risk_off:
        return ("KAUFEN 🟢", f"Moderat unter dem gleitenden Durchschnitt (Z-Score: {z_score:.2f}) bei stabilem Makro.")
    elif (z_score >= 1.8 or fg_value >= 75) and (macro_score < 0 or fg_value >= 80):
        return ("GEWINNE SICHERN 🔴🔴", f"Starke statistische Überdehnung nach oben (Z-Score: {z_score:.2f}, Fear & Greed: {fg_value}).")
    elif z_score >= 1.0:
        return ("VORSICHT / TEILVERKAUF 🔴", f"Solider Zuwachs, deutlicher Aufschlag zum Mittelwert (Z-Score: {z_score:.2f}).")
    else:
        return ("HALTEN 🟡", f"Konsolidierungsphase im fairen statistischen Bereich (Z-Score: {z_score:.2f}).")

# --- LIVE-DATEN ABRUFEN ---
current_data = get_current_data()
df_archive_check = pd.read_csv(LOG_FILE) if os.path.exists(LOG_FILE) else pd.DataFrame()

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

df_market_check = pd.read_csv(MARKET_LOG_FILE) if os.path.exists(MARKET_LOG_FILE) else pd.DataFrame()
dynamic_weights = generate_local_self_learning_optimizations(df_archive_check, current_data)

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

# EINSTELLUNGEN & INVESTITIONS-PLANUNG
with st.expander("⚙️ **Einstellungen & Investitions-Planung**", expanded=False):
    col_e1, col_e2 = st.columns(2)
    with col_e1:
        zeitraum_wahl = st.selectbox("Rückblick- & Vergleichszeitraum wählen:", ["24 Stunden", "7 Tage", "30 Tage", "90 Tage", "6 Monate", "1 Jahr", "2 Jahre", "3 Jahre", "5 Jahre (Zyklus)"], index=1)
        investment_wahl = st.selectbox("Geplantes Ordervolumen (Tranche):", [100, 500, 1000, 2500, 5000, "Individuell"], index=1)
        geplantes_budget = st.number_input("Eigenen Betrag eingeben (€):", min_value=10.0, value=1000.0, step=50.0) if investment_wahl == "Individuell" else float(investment_wahl)

    with col_e2:
        coins_map = {"Bitcoin": "BTCEUR", "Ethereum": "ETHEUR", "Solana": "SOLEUR", "Cardano": "ADAEUR", "Bitcoin Cash": "BCHEUR"}
        ausgewaehlte_coins = st.multiselect("Kryptowährungen anzeigen:", options=list(coins_map.keys()), default=list(coins_map.keys()))
        auto_refresh_aktiv = st.checkbox("🔄 Auto-Refresh (alle 5 Min.)", value=True)

    st.write("---")
    
    # --- EIGENES AUFKLAPPBARES FENSTER FÜR QUANT-STATISTIK & HISTORIE ---
    with st.expander("📊 **Quant Self-Tuning, Treffer-Statistik & Historie**", expanded=False):
        stats = evaluate_learning_data(df_archive_check, current_data)
        buy_signals = stats["buy_wins"] + stats["buy_fails"]
        sell_signals = stats["sell_wins"] + stats["sell_fails"]
        
        buy_win_rate = (stats['buy_wins'] / buy_signals * 100) if buy_signals > 0 else 0
        sell_win_rate = (stats['sell_wins'] / sell_signals * 100) if sell_signals > 0 else 0

        st.markdown("#### 📈 Leistungsübersicht & Trefferquoten")
        col_st1, col_st2, col_st3 = st.columns(3)
        col_st1.metric("Ausgewertete Signale", f"{stats['total_checked']}")
        col_st2.metric("Kauf-Treffer", f"{stats['buy_wins']} / {buy_signals}", f"{buy_win_rate:.1f}%")
        col_st3.metric("Verkauf-Treffer", f"{stats['sell_wins']} / {sell_signals}", f"{sell_win_rate:.1f}%")

        st.markdown("Basierend auf historischen Zyklen in `prediction_log.csv`:")

        st.markdown("""
        | Indikator | Basis-Gewichtung | Aktive Live-Gewichtung |
        | :--- | :---: | :---: |
        | **Trend & Momentum** | 15,0 % | **""" + f"{format_ger(dynamic_weights['Trend & Momentum'], 1)} %" + """** |
        | **ETF Net Flows (Spot)** | 15,0 % | **""" + f"{format_ger(dynamic_weights['ETF Net Flows'], 1)} %" + """** |
        | **Makro & VIX** | 15,0 % | **""" + f"{format_ger(dynamic_weights['Makro (Zins/Aktien/Währung/VIX)'], 1)} %" + """** |
        | **Fear & Greed Index** | 15,0 % | **""" + f"{format_ger(dynamic_weights['Fear & Greed Index'], 1)} %" + """** |
        | **Kraken Orderbuch-Druck** | 15,0 % | **""" + f"{format_ger(dynamic_weights['Kraken Orderbuch-Druck'], 1)} %" + """** |
        | **RSI & Volatilität** | 10,0 % | **""" + f"{format_ger(dynamic_weights['RSI & Volatilität'], 1)} %" + """** |
        | **Nachrichten & Zins-Sentiment** | 10,0 % | **""" + f"{format_ger(dynamic_weights['Nachrichten & Zins-Sentiment'], 1)} %" + """** |
        | **DeFi TVL & Liq.** | 5,0 % | **""" + f"{format_ger(dynamic_weights['Krypto-Liquidität & DeFi TVL'], 1)} %" + """** |
        """)

        st.write("---")
        st.markdown("#### 🗓️ Historienübersicht & Tägliche Auswertung")
        if not df_archive_check.empty:
            df_hist_view = df_archive_check.copy()
            df_hist_view["Datum"] = pd.to_datetime(df_hist_view["Datum"], errors="coerce")
            df_hist_view = df_hist_view.sort_values(by="Datum", ascending=False)
            
            # Tägliche Zusammenfassung aggregieren
            df_hist_view["Tag"] = df_hist_view["Datum"].dt.strftime("%Y-%m-%d")
            daily_summary = df_hist_view.groupby("Tag").agg(
                Anzahl_Signale=("Coin", "count"),
                Coins=("Coin", lambda x: ", ".join(x.unique()))
            ).reset_index().sort_values(by="Tag", ascending=False)

            st.markdown("##### 📅 Tägliche Signal-Zusammenfassung")
            st.dataframe(daily_summary, use_container_width=True, hide_index=True)

            st.markdown("##### 🔍 Detailliertes Signal-Logbuch (`prediction_log.csv`)")
            st.dataframe(df_hist_view[["Datum", "Coin", "Kurs_Damals", "Signal", "FearGreed", "Target_Horizon"]], use_container_width=True, hide_index=True)
        else:
            st.info("Noch keine historischen Signale im Log vorhanden.")

    st.write("---")
    manuelle_gewichtung = st.checkbox("🎛️ Manuelle Indikator-Gewichtung nutzen (übersteuert Quant-Automatik)", value=False)
    if manuelle_gewichtung:
        col_w1, col_w2, col_w3, col_w4 = st.columns(4)
        active_weights = {
            "Trend & Momentum": float(col_w1.slider("Trend & Momentum (%)", 0, 50, 15)),
            "ETF Net Flows": float(col_w2.slider("ETF Net Flows (%)", 0, 50, 15)),
            "Makro (Zins/Aktien/Währung/VIX)": float(col_w3.slider("Makro & VIX (%)", 0, 50, 15)),
            "Fear & Greed Index": float(col_w4.slider("Fear & Greed (%)", 0, 50, 15)),
            "Kraken Orderbuch-Druck": float(col_w1.slider("Kraken Orderbuch (%)", 0, 50, 15)),
            "RSI & Volatilität": float(col_w2.slider("RSI & Vola (%)", 0, 50, 10)),
            "Nachrichten & Zins-Sentiment": float(col_w3.slider("News/Zinsen (%)", 0, 50, 10)),
            "Krypto-Liquidität & DeFi TVL": float(col_w4.slider("DeFi TVL & Liq (%)", 0, 50, 5)),
        }
    else:
        active_weights = dynamic_weights

    st.write("---")
    portfolio_tranche_placeholder = st.container()

if auto_refresh_aktiv: st_autorefresh(interval=5 * 60 * 1000, key="datarefresh")

if zeitraum_wahl == "24 Stunden": limit_anzahl, interval, wöchentlich = 24, "1h", False
elif zeitraum_wahl == "7 Tage": limit_anzahl, interval, wöchentlich = 7, "1d", False
elif zeitraum_wahl == "30 Tage": limit_anzahl, interval, wöchentlich = 30, "1d", False
elif zeitraum_wahl == "90 Tage": limit_anzahl, interval, wöchentlich = 90, "1d", True
elif zeitraum_wahl == "6 Monate": limit_anzahl, interval, wöchentlich = 180, "1d", True
elif zeitraum_wahl == "1 Jahr": limit_anzahl, interval, wöchentlich = 365, "1d", True
elif zeitraum_wahl == "2 Jahre": limit_anzahl, interval, wöchentlich = 104, "1wk", True
elif zeitraum_wahl == "3 Jahre": limit_anzahl, interval, wöchentlich = 156, "1wk", True
else: limit_anzahl, interval, wöchentlich = 260, "1wk", True

nikkei_val = asian_markets.get("Nikkei_225", {}).get("value") if asian_markets else None
nikkei_chg = asian_markets.get("Nikkei_225", {}).get("change") if asian_markets else 0.0
hsi_val = asian_markets.get("Hang_Seng", {}).get("value") if asian_markets else None
hsi_chg = asian_markets.get("Hang_Seng", {}).get("change") if asian_markets else 0.0
eu_val = eurozone_market.get("value") if eurozone_market else None
eu_chg = eurozone_market.get("change") if eurozone_market else 0.0

log_market_data(fg_value, fg_text, bond_val, bond_change, nikkei_val, nikkei_chg, hsi_val, hsi_chg, eu_val, eu_chg, real_etf_flow, ext_macro, stock_indices, vix_data, news_score, llama_data, btc_dom)

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

            atr_value = calculate_atr(symbol, current_price=price, limit=30)
            rsi_value = get_real_rsi(symbol, limit=30)
            kraken_pressure = get_kraken_orderbook_pressure(coin)

            stop_loss = max(0.0, price - (2.0 * atr_value)) if atr_value > 0 else price * 0.95
            take_profit = price + (3.0 * atr_value) if atr_value > 0 else price * 1.08

            df_history = get_historical_data(symbol, limit_anzahl, interval, weekly=wöchentlich)
            perf_val, hist_price = 0.0, 0.0
            hist_prices_list = []
            if df_history is not None and not df_history.empty:
                hist_price = df_history.iloc[0]["Kurs"]
                hist_prices_list = df_history["Kurs"].tolist()
                if hist_price > 0: perf_val = ((price - hist_price) / hist_price) * 100

            signal, reason = generate_signal(price, hist_prices_list, volume, fg_value, bond_change, asian_markets, eurozone_market, stock_indices, real_etf_flow, vix_data, news_score, llama_data, btc_dom, kraken_pressure, zeitraum_wahl, active_weights, rsi_val=rsi_value)
            whale_msg, _ = analyze_whale_activity(volume, change_24h)

            log_prediction(coin, price, signal, fg_value, target_horizon=zeitraum_wahl)

            coin_results[coin] = {
                "symbol": symbol, "price": price, "change_24h": change_24h, "volume": volume,
                "atr_value": atr_value, "rsi_value": rsi_value, "kraken_pressure": kraken_pressure,
                "stop_loss": stop_loss, "take_profit": take_profit, "df_history": df_history,
                "perf_val": perf_val, "hist_price": hist_price, "signal": signal, "reason": reason, "whale_msg": whale_msg
            }

        portfolio_weights = {}
        for coin, res in coin_results.items():
            sig = res["signal"]
            if "STARKE KAUFCHANCE" in sig: portfolio_weights[coin] = 1.0
            elif "KAUFEN" in sig: portfolio_weights[coin] = 0.5
            else: portfolio_weights[coin] = 0.0

        denom = max(1.0, sum(portfolio_weights.values()))
        total_invested_eur = 0.0
        for coin, res in coin_results.items():
            w = portfolio_weights[coin]
            alloc_eur = round(geplantes_budget * (w / denom), 2)
            res["investition_eur"] = alloc_eur
            res["investition_pct"] = round((alloc_eur / geplantes_budget) * 100, 1) if geplantes_budget > 0 else 0.0
            res["portfolio_weight"] = w
            total_invested_eur += alloc_eur

        cash_reserve_eur = max(0.0, round(geplantes_budget - total_invested_eur, 2))
        cash_reserve_pct = round((cash_reserve_eur / geplantes_budget) * 100, 1) if geplantes_budget > 0 else 0.0

        with portfolio_tranche_placeholder:
            st.markdown("💼 **Portfolio-Tranche Allokation:**")
            col_p1, col_p2, col_p3 = st.columns(3)
            col_p1.metric("Gesamt", f"{format_ger(geplantes_budget, 0)} €")
            col_p2.metric("Krypto", f"{format_ger(total_invested_eur, 0)} €", f"{format_ger(100.0 - cash_reserve_pct, 0)}%")
            col_p3.metric("Cash", f"{format_ger(cash_reserve_eur, 0)} €", f"{format_ger(cash_reserve_pct, 0)}%")

            alloc_chart_data = [{"Asset": coin, "Betrag (€)": res["investition_eur"], "Anteil (%)": res["investition_pct"]} for coin, res in coin_results.items() if res["investition_eur"] > 0]
            if cash_reserve_eur > 0: alloc_chart_data.append({"Asset": "💵 Cash", "Betrag (€)": cash_reserve_eur, "Anteil (%)": cash_reserve_pct})

            if alloc_chart_data:
                st.altair_chart(alt.Chart(pd.DataFrame(alloc_chart_data)).mark_bar(cornerRadiusEnd=4).encode(x=alt.X("Betrag (€):Q", title=None), y=alt.Y("Asset:N", sort=None, title=None), color=alt.Color("Asset:N", scale=alt.Scale(scheme="category10"), legend=None), tooltip=["Asset", "Betrag (€)", "Anteil (%)"]).properties(height=max(120, len(alloc_chart_data) * 35)), use_container_width=True)

        st.markdown("### 🪙 Einzelne Kryptowährungen")

        for coin, res in coin_results.items():
            price, change_24h, volume, atr_value, rsi_value, kraken_pressure, stop_loss, take_profit, perf_val, hist_price, df_history, signal, reason, whale_msg, investition_eur, investition_pct = res["price"], res["change_24h"], res["volume"], res["atr_value"], res["rsi_value"], res["kraken_pressure"], res["stop_loss"], res["take_profit"], res["perf_val"], res["hist_price"], res["df_history"], res["signal"], res["reason"], res["whale_msg"], res["investition_eur"], res["investition_pct"]

            coin_menge = investition_eur / price if price > 0 else 0.0
            logo_url, symbol = CRYPTO_LOGOS.get(coin, ""), CRYPTO_SYMBOLS.get(coin, "")
            dec = 4 if price < 1 else 2

            with st.container(border=True):
                col1, col2 = st.columns([1.2, 1])
                perf_color_hex = "#2ecc71" if perf_val >= 0 else "#e74c3c"
                bg_badge = "rgba(46, 204, 113, 0.12)" if perf_val >= 0 else "rgba(231, 76, 60, 0.12)"

                with col1:
                    st.markdown(f"#### <img src='{logo_url}' width='22' style='vertical-align: middle; margin-right: 6px;'> {coin} ({symbol})", unsafe_allow_html=True)
                    st.markdown(f"<div style='font-size: 1.35rem; font-weight: bold;'>{format_ger(price, dec)} €</div>", unsafe_allow_html=True)
                with col2:
                    st.markdown(f"<div style='text-align: right; margin-bottom: 2px;'><span style='background-color: {bg_badge}; color: {perf_color_hex}; font-size: 1.25rem; font-weight: bold; padding: 4px 10px; border-radius: 8px; display: inline-block;'>{format_ger(perf_val, 2, True)}%</span></div><div style='text-align: right; font-size: 0.75rem; color: gray;'>Veränderung ({zeitraum_wahl})</div>", unsafe_allow_html=True)

                st.write("---")
                col_s1, col_s2 = st.columns(2)
                col_s1.markdown(f"**Signal:** {signal}")
                col_s2.markdown(f"**Tranche:** **{format_ger(investition_eur, 2)} €** ({format_ger(investition_pct, 1)}%)")

                # --- DIAGRAMM & RÜCKBLICK DIREKT ANZEIGEN ---
                st.write("---")
                if hist_price > 0: 
                    st.markdown(f"📈 **Rückblick ({zeitraum_wahl}):** Start: **{format_ger(hist_price, dec)} €** | Aktuell: **{format_ger(price, dec)} €**")

                if df_history is not None and not df_history.empty:
                    df_chart = df_history.reset_index().copy()
                    if len(df_chart) >= 5: 
                        df_chart["EMA"] = df_chart["Kurs"].ewm(span=min(10, len(df_chart)), adjust=True).mean()
                    
                    min_p, max_p = df_chart["Kurs"].min(), df_chart["Kurs"].max()
                    puffer = (max_p - min_p) * 0.08 if max_p != min_p else min_p * 0.01
                    chart_color = "#2ecc71" if perf_val >= 0 else "#e74c3c"

                    date_format = "%H:%M" if zeitraum_wahl == "24 Stunden" else ("%d.%m.%Y" if zeitraum_wahl in ["2 Jahre", "3 Jahre", "5 Jahre (Zyklus)"] else "%d.%m.")
                    y_format = ",.4f" if max_p < 1 else (",.2f" if max_p < 10 else ",.0f")

                    x_axis = alt.Axis(
                        format=date_format,
                        labelAngle=0,
                        labels=True,
                        labelFlush=False,
                        grid=True,
                        gridDash=[2, 2],
                        gridColor="rgba(150, 150, 150, 0.15)",
                        tickCount=6 if zeitraum_wahl == "24 Stunden" else 5
                    )

                    base = alt.Chart(df_chart).encode(
                        x=alt.X("Timestamp:T", title=None, axis=x_axis),
                        y=alt.Y("Kurs:Q", title="€", scale=alt.Scale(domain=[min_p - puffer, max_p + puffer], zero=False), axis=alt.Axis(format=y_format, grid=True, gridColor="rgba(150, 150, 150, 0.15)"))
                    )
                    chart_layers = base.mark_area(opacity=0.15, color=chart_color, interpolate="monotone") + base.mark_line(color=chart_color, strokeWidth=2.2, interpolate="monotone")
                    if "EMA" in df_chart.columns: 
                        chart_layers += base.mark_line(color="#e67e22", strokeWidth=1.5, strokeDash=[3, 3], interpolate="monotone").encode(y=alt.Y("EMA:Q"))
                    
                    tooltip_format = ",.4f €" if max_p < 1 else ",.2f €"
                    tooltip_time_format = "%d.%m.%Y %H:%M" if zeitraum_wahl != "24 Stunden" else "%H:%M Uhr"
                    st.altair_chart((chart_layers + base.mark_point(size=45, color=chart_color, filled=True).encode(tooltip=[alt.Tooltip("Timestamp:T", title="Zeit", format=tooltip_time_format), alt.Tooltip("Kurs:Q", title="Kurs", format=tooltip_format)])).interactive(), use_container_width=True)

                # --- WEITERE DETAILS IM EXPANDER ---
                with st.expander("🔍 **Details, Analyse & Signal-Zusammensetzung**", expanded=False):
                    st.info(f"💡 **Begründung ({zeitraum_wahl}):** {reason}")
                    st.markdown(f"🎯 **Empfehlung:** ca. **{format_ger(coin_menge, 4)} {coin.upper()} {symbol}** kaufen ({format_ger(investition_eur, 2)} € aus der Tranche).")
                    
                    st.write("---")
                    st.markdown("##### 🧮 Interaktiver Positionsgrößen- & Risiko-Rechner")
                    col_rk1, col_rk2 = st.columns(2)
                    total_depot_capital = col_rk1.number_input(f"Gesamtkapital ({coin}):", min_value=100.0, value=5000.0, step=100.0, key=f"cap_{coin}")
                    max_risk_pct = col_rk2.slider(f"Max. Risiko (%):", 0.5, 5.0, 1.0, 0.5, key=f"risk_{coin}")
                    
                    risk_amount_eur = total_depot_capital * (max_risk_pct / 100.0)
                    risk_per_coin = max(0.0001, price - stop_loss)
                    position_size_coins = risk_amount_eur / risk_per_coin
                    position_size_eur = position_size_coins * price

                    st.markdown(f"• 💰 **Empfohlenes Investitionsvolumen:** **{format_ger(position_size_eur, 2)} €** (~{format_ger(position_size_coins, 4)} {symbol})")
                    st.markdown(f"• 🛡️ **Absolutes Risiko bei Stop-Loss:** **{format_ger(risk_amount_eur, 2)} €** ({max_risk_pct}% des Gesamtkapitals)")

                    st.write("---")
                    st.markdown("##### 📊 Live-Marktindikatoren & Kraken Orderbuch")
                    st.caption(f"🏛 **Spot-ETF Net Flow:** **{format_ger(real_etf_flow, 1, True)}M $**\n\n📊 **RSI (14):** **{format_ger(rsi_value, 1)}** | 📈 **Kraken Orderbuch-Druck:** **{format_ger(kraken_pressure, 1, True)}%**\n\n📰 **News/Zins-Sentiment:** **{news_score:+d} / 100** | 🔒 **DeFi TVL:** **{format_ger(llama_data.get('global_tvl_b', 0), 1)} Mrd. $**\n\n🐋 **Wal-Aktivität:** {whale_msg}")

                    st.write("---")
                    st.markdown("##### 🛡️ ATR Risikomanagement")
                    st.write(f"• **ATR (14):** {format_ger(atr_value, dec)} €")
                    st.write(f"• 🛑 **Stop-Loss:** **{format_ger(stop_loss, dec)} €**")
                    st.write(f"• 🎯 **Take-Profit:** **{format_ger(take_profit, dec)} €**")

                    st.write("---")
                    st.markdown("##### 📊 Signal-Zusammensetzung Score")
                    factor_scores = calculate_detailed_factor_scores(perf_val, volume, fg_value, bond_change, asian_markets, eurozone_market, stock_indices, real_etf_flow, vix_data, news_score, llama_data, btc_dom, kraken_pressure=kraken_pressure, rsi_val=rsi_value, weights=active_weights)
                    
                    df_scores = pd.DataFrame(list(factor_scores.items()), columns=["Indikator", "Score"])
                    indikatoren_reihenfolge = list(factor_scores.keys())

                    st.altair_chart(
                        alt.Chart(df_scores)
                        .mark_bar()
                        .encode(
                            x=alt.X("Score:Q", scale=alt.Scale(domain=[-100, 100]), title="Bärisch (-100) | Bullisch (+100)"),
                            y=alt.Y("Indikator:N", sort=indikatoren_reihenfolge, title=None, axis=alt.Axis(labelLimit=250)),
                            color=alt.condition(alt.datum.Score > 0, alt.value("#2ecc71"), alt.value("#e74c3c")),
                            tooltip=["Indikator", "Score"]
                        )
                        .properties(height=280),
                        use_container_width=True
                    )
else:
    st.warning("Konnte keine Marktdaten laden.")

st.write("---")

# GLOBALER MARKT & MAKRO-ÜBERSICHT
bar_color = "#e74c3c" if fg_value > 70 else ("#f39c12" if fg_value >= 45 else "#2ecc71")
signal_icon = "🔴" if fg_value > 70 else ("🟡" if fg_value >= 45 else "🟢")

with st.container(border=True):
    st.markdown(f"📊 **Sentiment-Index:** **{fg_value} / 100** ({fg_text}) {signal_icon}")
    st.markdown(f"""<div style="background-color: #e0e0e0; border-radius: 10px; width: 100%; height: 12px; overflow: hidden; margin-top: 4px; margin-bottom: 8px;"><div style="background-color: {bar_color}; width: {fg_value}%; height: 100%;"></div></div>""", unsafe_allow_html=True)

    with st.expander("🌐 **Globale Leitmärkte & Makro-Details**", expanded=False):
        if bond_val is not None: st.write(f"🏛 **US-10J Zins:** {format_ger(bond_val, 2)}% ({format_ger(bond_change, 2, True)}%) | {'📈 Steigend' if bond_change > 0 else '📉 Fallend'}")

        dxy_color = "#2ecc71" if ext_macro.get("dxy_chg", 0) >= 0 else "#e74c3c"
        eur_color = "#2ecc71" if ext_macro.get("eurusd_chg", 0) >= 0 else "#e74c3c"
        jpy_color = "#2ecc71" if ext_macro.get("usdjpy_chg", 0) >= 0 else "#e74c3c"
        gold_color = "#2ecc71" if ext_macro.get("gold_chg", 0) >= 0 else "#e74c3c"
        silver_color = "#2ecc71" if ext_macro.get("silver_chg", 0) >= 0 else "#e74c3c"
        oil_color = "#2ecc71" if ext_macro.get("oil_chg", 0) >= 0 else "#e74c3c"
        copper_color = "#2ecc71" if ext_macro.get("copper_chg", 0) >= 0 else "#e74c3c"
        vix_color = "#e74c3c" if vix_data.get("vix_chg", 0) >= 0 else "#2ecc71"
        ndx_color = "#2ecc71" if vix_data.get("nasdaq_chg", 0) >= 0 else "#2ecc71"
        etf_color = "#2ecc71" if real_etf_flow >= 0 else "#e74c3c"

        st.markdown(f"""
            <div style="line-height: 1.8; font-size: 0.95rem;">
                💵 <b>DXY:</b> {format_ger(ext_macro.get('dxy_val', 0), 2)} (<span style="color:{dxy_color}; font-weight:bold;">{format_ger(ext_macro.get('dxy_chg', 0), 2, True)}%</span>) &nbsp;|&nbsp; 
                💶 <b>EUR/USD:</b> {format_ger(ext_macro.get('eurusd_val', 0), 4)} (<span style="color:{eur_color}; font-weight:bold;">{format_ger(ext_macro.get('eurusd_chg', 0), 2, True)}%</span>) &nbsp;|&nbsp; 
                💴 <b>USD/JPY:</b> {format_ger(ext_macro.get('usdjpy_val', 0), 2)} (<span style="color:{jpy_color}; font-weight:bold;">{format_ger(ext_macro.get('usdjpy_chg', 0), 2, True)}%</span>)<br>
                🥇 <b>Gold:</b> {format_ger(ext_macro.get('gold_val', 0), 1)} $ (<span style="color:{gold_color}; font-weight:bold;">{format_ger(ext_macro.get('gold_chg', 0), 2, True)}%</span>) &nbsp;|&nbsp; 
                🥈 <b>Silber:</b> {format_ger(ext_macro.get('silver_val', 0), 2)} $ (<span style="color:{silver_color}; font-weight:bold;">{format_ger(ext_macro.get('silver_chg', 0), 2, True)}%</span>) &nbsp;|&nbsp; 
                🛢 <b>Öl:</b> {format_ger(ext_macro.get('oil_val', 0), 1)} $ (<span style="color:{oil_color}; font-weight:bold;">{format_ger(ext_macro.get('oil_chg', 0), 2, True)}%</span>) &nbsp;|&nbsp; 
                🏗 <b>Kupfer:</b> {format_ger(ext_macro.get('copper_val', 0), 2)} $ (<span style="color:{copper_color}; font-weight:bold;">{format_ger(ext_macro.get('copper_chg', 0), 2, True)}%</span>)<br>
                📉 <b>CBOE VIX (Panik):</b> {format_ger(vix_data.get('vix_val', 0), 1)} (<span style="color:{vix_color}; font-weight:bold;">{format_ger(vix_data.get('vix_chg', 0), 2, True)}%</span>) &nbsp;|&nbsp; 
                💻 <b>Nasdaq 100:</b> <span style="color:{ndx_color}; font-weight:bold;">{format_ger(vix_data.get('nasdaq_chg', 0), 2, True)}%</span><br>
                🏦 <b>Spot-ETF Nettofluss:</b> <span style="color:{etf_color}; font-weight:bold;">{format_ger(real_etf_flow, 1, True)} Mio. USD</span> &nbsp;|&nbsp; 
                💧 <b>DeFi TVL:</b> <b>{format_ger(llama_data.get('global_tvl_b', 0), 1)} Mrd. $</b> &nbsp;|&nbsp; 
                🪙 <b>BTC Dominanz:</b> <b>{format_ger(btc_dom, 1)}%</b>
            </div>
            """, unsafe_allow_html=True)

        if stock_indices:
            sp500_v, sp500_c = stock_indices.get("SP500", {}).get("val", 0), stock_indices.get("SP500", {}).get("chg", 0)
            dax_v, dax_c = stock_indices.get("DAX", {}).get("val", 0), stock_indices.get("DAX", {}).get("chg", 0)
            dow_v, dow_c = ext_macro.get("dow_val", 0), ext_macro.get("dow_chg", 0)
            ftse_v, ftse_c = ext_macro.get("ftse_val", 0), ext_macro.get("ftse_chg", 0)
            st.write(f"📈 **S&P 500:** {format_ger(sp500_v, 0)} ({format_ger(sp500_c, 2, True)}%) | **Dow Jones:** {format_ger(dow_v, 0)} ({format_ger(dow_c, 2, True)}%) | **DAX:** {format_ger(dax_v, 0)} ({format_ger(dax_c, 2, True)}%) | **FTSE 100:** {format_ger(ftse_v, 0)} ({format_ger(ftse_c, 2, True)}%)")

        if asian_markets:
            n_val, n_chg = asian_markets.get("Nikkei_225", {}).get("value", 0), asian_markets.get("Nikkei_225", {}).get("change", 0)
            h_val, h_chg = asian_markets.get("Hang_Seng", {}).get("value", 0), asian_markets.get("Hang_Seng", {}).get("change", 0)
            sse_v, sse_c = ext_macro.get("sse_val", 0), ext_macro.get("sse_chg", 0)
            st.write(f"🌏 **Nikkei 225:** {format_ger(n_val, 0)} ({format_ger(n_chg, 2, True)}%) | **Hang Seng:** {format_ger(h_val, 0)} ({format_ger(h_chg, 2, True)}%) | **Shanghai Comp:** {format_ger(sse_v, 0)} ({format_ger(sse_c, 2, True)}%)")

        st.write("---")
        st.markdown(f"📰 **Zins- & Nachrichten-Sentiment:** **{news_score:+d} / 100**")
        if top_headlines:
            st.caption("Aktuelle Schlagzeilen:")
            for title, link in top_headlines:
                st.markdown(f"- [{title}]({link})")

# --- KRYPTO-AKADEMIE & LERNPFAD ---
st.write("")
with st.expander("🎓 **Krypto-Akademie & Einsteiger-Lernpfad**", expanded=True):
    st.markdown("Willkommen in der **Krypto-Akademie**! Hier lernst du Schritt für Schritt, wie du die Live-Daten, Indikatoren und Signale dieses Dashboards optimal für deine Investment-Entscheidungen nutzt.")
    
    st.markdown("---")
    st.markdown("### 📚 Modul 1: Grundlagen & Signale verstehen")
    st.markdown("- **STARKE KAUFCHANCE (🟢🟢):** Statistisch überverkaufter Zustand (Z-Score < -1.8) kombiniert mit starkem RSI (< 33) und gesundem Makro-Umfeld. Idealer Zeitpunkt für eine größere Tranche.")
    st.markdown("- **KAUFEN (🟢):** Moderater Rücksetzer unter den gleitenden Durchschnitt bei stabilen Rahmenbedingungen.")
    st.markdown("- **HALTEN (🟡):** Seitwärtsphase oder unentschlossener Markt. Keine übereilten Aktionen.")
    st.markdown("- **TEILVERKAUF / VORSICHT (🔴):** Erste Anzeichen von Überhitzung oder steigendem Verkaufsdruck. Gewinne absichern.")
    st.markdown("- **GE
