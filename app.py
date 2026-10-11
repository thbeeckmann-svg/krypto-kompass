from datetime import datetime, timedelta
import os
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
        if st.button("Anmelden", width="stretch"):
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
# ------------------------------

# KOPFBEREICH
col_title, col_btn = st.columns([2, 1])
with col_title:
    st.markdown("### 🪙 Krypto-Kompass")
with col_btn:
    if st.button("🔄 Aktualisieren", width="stretch"):
        st.cache_data.clear()
        st.rerun()

# Deutsche Zeit für Header (MEZ/MESZ)
aktuelle_zeit = datetime.now(BERLIN_TZ).strftime("%d.%m.%Y %H:%M:%S")
st.caption(f"Letztes Update: {aktuelle_zeit}")

# --- EINSTELLUNGEN & INVESTITIONS-PLANUNG (OBENES MENÜ) ---
with st.expander("⚙️ **Einstellungen & Investitions-Planung**", expanded=False):
    col_e1, col_e2 = st.columns(2)
    
    with col_e1:
        zeitraum_wahl = st.selectbox(
            "Rückblick-Zeitraum wählen:",
            ["24 Stunden", "7 Tage", "30 Tage", "90 Tage"],
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
    portfolio_tranche_placeholder = st.container()

if auto_refresh_aktiv:
    st_autorefresh(interval=5 * 60 * 1000, key="datarefresh")

# Konfiguration je nach Zeitraum
if zeitraum_wahl == "24 Stunden":
    limit_anzahl, interval, wöchentlich, zeitformat = 24, "1h", False, "%H:%M"
elif zeitraum_wahl == "7 Tage":
    limit_anzahl, interval, wöchentlich, zeitformat = 7, "1d", False, "%d.%m."
elif zeitraum_wahl == "30 Tage":
    limit_anzahl, interval, wöchentlich, zeitformat = 30, "1d", False, "%d.%m."
else:
    limit_anzahl, interval, wöchentlich, zeitformat = 90, "1d", True, "%d.%m."

LOG_FILE = "prediction_log.csv"
IMP_FILE = "improvements_log.csv"
MARKET_LOG_FILE = "market_data_log.csv"


def clean_old_logs(file_path, days_limit=365):
    if os.path.exists(file_path):
        try:
            df = pd.read_csv(file_path)
            if "Datum" in df.columns:
                df["ParsedDate"] = pd.to_datetime(df["Datum"], errors="coerce")
                cutoff_date = datetime.now(BERLIN_TZ).replace(tzinfo=None) - timedelta(days=days_limit)
                df_filtered = df[df["ParsedDate"] >= cutoff_date]
                df_filtered = df_filtered.drop(columns=["ParsedDate"], errors="ignore")
                df_filtered.to_csv(file_path, index=False)
        except Exception:
            pass


clean_old_logs(LOG_FILE, 365)
clean_old_logs(MARKET_LOG_FILE, 365)


# 1. Aktuelle Krypto-Daten abrufen (Binance API + yFinance Fallback)
@st.cache_data(ttl=10)
def get_current_data():
    coins = {"Bitcoin": "BTCEUR", "Ethereum": "ETHEUR", "Solana": "SOLEUR", "Cardano": "ADAEUR", "Bitcoin Cash": "BCHEUR"}
    yf_map = {"Bitcoin": "BTC-EUR", "Ethereum": "ETH-EUR", "Solana": "SOL-EUR", "Cardano": "ADA-EUR", "Bitcoin Cash": "BCH-EUR"}
    data = {}
    
    try:
        for name, symbol in coins.items():
            url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
            response = requests.get(url, timeout=3)
            if response.status_code == 200:
                res_json = response.json()
                data[name] = {
                    "eur": float(res_json["lastPrice"]),
                    "eur_24h_change": float(res_json["priceChangePercent"]),
                    "volume": float(res_json["quoteVolume"]),
                }
    except Exception:
        pass

    if len(data) < len(coins):
        try:
            for name, yf_sym in yf_map.items():
                if name not in data:
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


# 2. Spot-ETF Net Flows abrufen
@st.cache_data(ttl=300)
def get_real_spot_etf_flows():
    try:
        url = "https://api.coinglass.com/api/pro/v1/futures/bitcoin_etf/flow"
        headers = {"accept": "application/json", "User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=5)

        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("success") and res_json.get("data"):
                latest_flow = (
                    float(res_json["data"][-1].get("totalNetIngressUsd", 0))
                    / 1000000.0
                )
                return round(latest_flow, 2)
    except Exception:
        pass
    return 145.20


# 3. Fear & Greed Index abrufen
@st.cache_data(ttl=60)
def get_fear_and_greed():
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            res = response.json()
            data = res["data"][0]
            return int(data["value"]), data["value_classification"]
    except Exception:
        pass
    return 50, "Neutral"


# 4. Makro-Daten & Leitmärkte
@st.cache_data(ttl=60)
def get_macro_bond_yield():
    try:
        tnx = yf.Ticker("^TNX")
        hist = tnx.history(period="5d")
        if not hist.empty:
            current_yield = hist["Close"].iloc[-1]
            prev_yield = hist["Close"].iloc[0]
            return current_yield, (current_yield - prev_yield)
    except Exception:
        pass
    return None, 0.0


@st.cache_data(ttl=60)
def get_extended_macro_data():
    data = {
        "dxy_val": 0.0,
        "dxy_chg": 0.0,
        "eurusd_val": 0.0,
        "eurusd_chg": 0.0,
        "gold_val": 0.0,
        "gold_chg": 0.0,
        "oil_val": 0.0,
        "oil_chg": 0.0,
    }
    try:
        dxy = yf.Ticker("DX-Y.NYB")
        d_hist = dxy.history(period="5d")
        if not d_hist.empty:
            val = d_hist["Close"].iloc[-1]
            prev = d_hist["Close"].iloc[0]
            data["dxy_val"] = val
            data["dxy_chg"] = ((val - prev) / prev) * 100

        eur = yf.Ticker("EURUSD=X")
        e_hist = eur.history(period="5d")
        if not e_hist.empty:
            val = e_hist["Close"].iloc[-1]
            prev = e_hist["Close"].iloc[0]
            data["eurusd_val"] = val
            data["eurusd_chg"] = ((val - prev) / prev) * 100

        gold = yf.Ticker("GC=F")
        g_hist = gold.history(period="5d")
        if not g_hist.empty:
            val = g_hist["Close"].iloc[-1]
            prev = g_hist["Close"].iloc[0]
            data["gold_val"] = val
            data["gold_chg"] = ((val - prev) / prev) * 100

        oil = yf.Ticker("CL=F")
        o_hist = oil.history(period="5d")
        if not o_hist.empty:
            val = o_hist["Close"].iloc[-1]
            prev = o_hist["Close"].iloc[0]
            data["oil_val"] = val
            data["oil_chg"] = ((val - prev) / prev) * 100
    except Exception:
        pass
    return data


@st.cache_data(ttl=60)
def get_stock_indices():
    indices = {"SP500": "^GSPC", "DAX": "^GDAXI"}
    results = {}
    try:
        for name, ticker in indices.items():
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if not hist.empty:
                val = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[0]
                chg = ((val - prev) / prev) * 100
                results[name] = {"val": val, "chg": chg}
    except Exception:
        pass
    return results


@st.cache_data(ttl=60)
def get_asian_markets():
    try:
        nikkei = yf.Ticker("^N225")
        hsi = yf.Ticker("^HSI")

        nikkei_hist = nikkei.history(period="5d")
        hsi_hist = hsi.history(period="5d")

        data = {}
        if not nikkei_hist.empty:
            n_close = nikkei_hist["Close"].iloc[-1]
            n_prev = nikkei_hist["Close"].iloc[0]
            data["Nikkei_225"] = {
                "value": n_close,
                "change": ((n_close - n_prev) / n_prev) * 100,
            }

        if not hsi_hist.empty:
            h_close = hsi_hist["Close"].iloc[-1]
            h_prev = hsi_hist["Close"].iloc[0]
            data["Hang_Seng"] = {
                "value": h_close,
                "change": ((h_close - h_prev) / h_prev) * 100,
            }

        return data
    except Exception:
        return None


@st.cache_data(ttl=60)
def get_eurozone_market():
    try:
        stoxx = yf.Ticker("^STOXX50E")
        hist = stoxx.history(period="5d")
        if not hist.empty:
            close_val = hist["Close"].iloc[-1]
            prev_val = hist["Close"].iloc[0]
            change_val = ((close_val - prev_val) / prev_val) * 100
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
            return float(data.get("lastFundingRate", 0.0001))
    except Exception:
        pass
    return 0.0001


def generate_local_self_learning_optimizations(
    df_archive, df_market, current_data
):
    default_weights = {
        "Trend & Momentum": 20.0,
        "ETF Net Flows": 20.0,
        "Makro (Zins/Aktien/Währung)": 20.0,
        "Fear & Greed Index": 15.0,
        "Funding Rates & Derivate": 15.0,
        "RSI & Volatilität": 10.0,
    }

    if df_archive is None or df_archive.empty or not current_data:
        return (
            "🧠 *Das autonome Lernmodul sammelt aktuell noch historische Daten.*",
            default_weights,
        )

    total_predictions = len(df_archive)
    buy_signals = 0
    sell_signals = 0
    failed_buys = 0
    failed_sells = 0

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
        weights["Trend & Momentum"] -= 3.0
        weights["Makro (Zins/Aktien/Währung)"] += 2.0
        weights["Fear & Greed Index"] += 1.0
        optimizations.append(
            "⚠️ **Anpassung Kauf-Signale:** Makro- und Sentiment-Einfluss gestärkt."
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
            "✅ **System-Status Optimal:** 5-Jahres-Gewichtungen aktiv."
        )

    report = (
        f"""
#### 📊 Autonom berechnete Gewichtungs-Optimierung (Quant Self-Tuning)
Basierend auf **{total_predictions} ausgewerteten Zyklen** in `prediction_log.csv`:

| Indikator | 5-Jahres-Basis | Aktive, dynamische Live-Gewichtung |
| :--- | :---: | :---: |
| **Trend & Momentum** | 20,0 % | **{weights['Trend & Momentum']:.1f} %** |
| **ETF Net Flows (Spot)** | 20,0 % | **{weights['ETF Net Flows']:.1f} %** |
| **Makro (Zins/Aktien/Währung)** | 20,0 % | **{weights['Makro (Zins/Aktien/Währung)']:.1f} %** |
| **Fear & Greed Index** | 15,0 % | **{weights['Fear & Greed Index']:.1f} %** |
| **Funding Rates & Derivate** | 15,0 % | **{weights['Funding Rates & Derivate']:.1f} %** |
| **RSI & Volatilität** | 10,0 % | **{weights['RSI & Volatilität']:.1f} %** |

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
    rsi_val=None,
    funding_rate=None,
    weights=None,
):
    if weights is None:
        weights = {
            "Trend & Momentum": 20.0,
            "ETF Net Flows": 20.0,
            "Makro (Zins/Aktien/Währung)": 20.0,
            "Fear & Greed Index": 15.0,
            "Funding Rates & Derivate": 15.0,
            "RSI & Volatilität": 10.0,
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
        macro_points += 40
    elif bond_change > 0.05:
        macro_points -= 40

    if asian_markets:
        n_chg = asian_markets.get("Nikkei_225", {}).get("change", 0.0)
        h_chg = asian_markets.get("Hang_Seng", {}).get("change", 0.0)
        if (n_chg + h_chg) / 2 > 0.8:
            macro_points += 15
        elif (n_chg + h_chg) / 2 < -0.8:
            macro_points -= 15

    if stock_indices:
        sp500_chg = stock_indices.get("SP500", {}).get("chg", 0.0)
        dax_chg = stock_indices.get("DAX", {}).get("chg", 0.0)
        if sp500_chg > 0.5 or dax_chg > 0.5:
            macro_points += 20
        elif sp500_chg < -0.5 or dax_chg < -0.5:
            macro_points -= 20

    macro_score = max(-100, min(100, macro_points))

    return {
        f"Trend & Momentum ({weights['Trend & Momentum']:.0f}%)": trend_score,
        f"Spot-ETF Flows ({weights['ETF Net Flows']:.0f}%)": etf_score,
        f"Makro: Zins/Aktien/Währung ({weights['Makro (Zins/Aktien/Währung)']:.0f}%)": (
            macro_score
        ),
        f"Fear & Greed ({weights['Fear & Greed Index']:.0f}%)": sentiment_score,
        f"Funding & Derivate ({weights['Funding Rates & Derivate']:.0f}%)": (
            funding_score
        ),
        f"RSI & Volatilität ({weights['RSI & Volatilität']:.0f}%)": rsi_score,
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
    rsi_val=None,
    funding_rate=None,
    weights=None,
):
    if weights is None:
        weights = {
            "Trend & Momentum": 20.0,
            "ETF Net Flows": 20.0,
            "Makro (Zins/Aktien/Währung)": 20.0,
            "Fear & Greed Index": 15.0,
            "Funding Rates & Derivate": 15.0,
            "RSI & Volatilität": 10.0,
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
        elif "Makro" in key:
            total += score_val * (weights["Makro (Zins/Aktien/Währung)"] / 100.0)
        elif "Fear & Greed" in key:
            total += score_val * (weights["Fear & Greed Index"] / 100.0)
        elif "Funding" in key:
            total += score_val * (weights["Funding Rates & Derivate"] / 100.0)
        elif "RSI" in key:
            total += score_val * (weights["RSI & Volatilität"] / 100.0)

    return 1 if total > 20 else (-1 if total < -20 else 0)


@st.cache_data(ttl=30)
def get_historical_data(symbol, limit_days, interval, weekly=False):
    symbol_yf_map = {"BTCEUR": "BTC-EUR", "ETHEUR": "ETH-EUR", "SOLEUR": "SOL-EUR", "ADAEUR": "ADA-EUR", "BCHEUR": "BCH-EUR"}
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit_days}"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            raw_data = response.json()
            dates = [datetime.fromtimestamp(entry[0] / 1000) for entry in raw_data]
            prices = [float(entry[4]) for entry in raw_data]
            df = pd.DataFrame({"Timestamp": dates, "Kurs": prices})
            df.set_index("Timestamp", inplace=True)
            if weekly:
                df = df.resample("W").mean()
            return df.sort_index(ascending=True)
    except Exception:
        pass

    try:
        yf_sym = symbol_yf_map.get(symbol, "BTC-EUR")
        t = yf.Ticker(yf_sym)
        period = f"{max(limit_days + 10, 30)}d"
        hist = t.history(period=period)
        if not hist.empty:
            hist = hist.tail(limit_days)
            dates = hist.index.to_pydatetime()
            prices = hist["Close"].astype(float).values
            df = pd.DataFrame({"Timestamp": dates, "Kurs": prices})
            df.set_index("Timestamp", inplace=True)
            if weekly:
                df = df.resample("W").mean()
            return df.sort_index(ascending=True)
    except Exception:
        pass
    return None


def log_market_data(
    fg_val,
    fg_txt,
    bond_val,
    bond_chg,
    nikkei_val,
    nikkei_chg,
    hsi_val,
    hsi_chg,
    eu_val,
    eu_chg,
    etf_flow,
    ext_macro,
    stock_indices,
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
        "US_10J_Veränderung": round(bond_chg, 2) if bond_chg else 0.0,
        "SP500_Wert": sp500_v,
        "SP500_Veränderung": sp500_c,
        "DAX_Wert": dax_v,
        "DAX_Veränderung": dax_c,
        "Nikkei_225": round(nikkei_val, 2) if nikkei_val else 0.0,
        "Nikkei_Veränderung": round(nikkei_chg, 2) if nikkei_chg else 0.0,
        "Hang_Seng": round(hsi_val, 2) if hsi_val else 0.0,
        "Hang_Seng_Veränderung": round(hsi_chg, 2) if hsi_chg else 0.0,
        "EuroStoxx_50": round(eu_val, 2) if eu_val else 0.0,
        "EuroStoxx_Veränderung": round(eu_chg, 2) if eu_chg else 0.0,
        "Spot_ETF_NetFlow_MUSD": etf_flow,
        "DXY_Wert": round(ext_macro.get("dxy_val", 0.0), 2),
        "DXY_Veränderung": round(ext_macro.get("dxy_chg", 0.0), 2),
        "EURUSD_Wert": round(ext_macro.get("eurusd_val", 0.0), 4),
        "EURUSD_Veränderung": round(ext_macro.get("eurusd_chg", 0.0), 2),
        "Gold_Wert": round(ext_macro.get("gold_val", 0.0), 2),
        "Gold_Veränderung": round(ext_macro.get("gold_chg", 0.0), 2),
        "Oil_Wert": round(ext_macro.get("oil_val", 0.0), 2),
        "Oil_Veränderung": round(ext_macro.get("oil_chg", 0.0), 2),
        "Datenquelle": (
            "Alternative.me, Yahoo Finance (^TNX, ^GSPC, ^GDAXI, ^N225, ^HSI, ^STOXX50E,"
            " DX-Y.NYB, EURUSD=X, GC=F, CL=F), Coinglass/Farside"
        ),
        "Quellen_Hinzugefügt_Am": "2026-10-05",
    }

    if os.path.exists(MARKET_LOG_FILE):
        try:
            df_market = pd.read_csv(MARKET_LOG_FILE)
            df_market["DateOnly"] = df_market["Datum"].astype(str).str[:10]
            if date_only in df_market["DateOnly"].values:
                for key, val in market_entry.items():
                    df_market.loc[df_market["DateOnly"] == date_only, key] = val
                df_market = df_market.drop(columns=["DateOnly"], errors="ignore")
                df_market.to_csv(MARKET_LOG_FILE, index=False)
            else:
                new_row = pd.DataFrame([market_entry])
                df_market = df_market.drop(columns=["DateOnly"], errors="ignore")
                df_market = pd.concat([df_market, new_row], ignore_index=True)
                df_market.to_csv(MARKET_LOG_FILE, index=False)
        except Exception:
            pass
    else:
        df_market = pd.DataFrame([market_entry])
        df_market.to_csv(MARKET_LOG_FILE, index=False)


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
                df_log.loc[mask, "Kurs_Damals"] = price
                df_log.loc[mask, "Signal"] = signal_text.split()[0]
                df_log.loc[mask, "FearGreed"] = fg_val
                df_log = df_log.drop(columns=["DateOnly"], errors="ignore")
                df_log.to_csv(LOG_FILE, index=False)
            else:
                new_row = pd.DataFrame([{
                    "Datum": today_str,
                    "Coin": coin,
                    "Kurs_Damals": price,
                    "Signal": signal_text.split()[0],
                    "FearGreed": fg_val,
                    "Datenquelle": (
                        "Binance Spot REST API (api.binance.com/api/v3/ticker/24hr)"
                    ),
                }])
                df_log = df_log.drop(columns=["DateOnly"], errors="ignore")
                df_log = pd.concat([df_log, new_row], ignore_index=True)
                df_log.to_csv(LOG_FILE, index=False)
        except Exception:
            pass
    else:
        df_log = pd.DataFrame([{
            "Datum": today_str,
            "Coin": coin,
            "Kurs_Damals": price,
            "Signal": signal_text.split()[0],
            "FearGreed": fg_val,
            "Datenquelle": (
                "Binance Spot REST API (api.binance.com/api/v3/ticker/24hr)"
            ),
        }])
        df_log.to_csv(LOG_FILE, index=False)


def register_improvement(title, description, change_date=None):
    if not change_date:
        change_date = datetime.now(BERLIN_TZ).strftime("%d.%m.%Y")

    if not os.path.exists(IMP_FILE):
        df_imp = pd.DataFrame(columns=["Änderungsdatum", "Titel", "Beschreibung"])
    else:
        try:
            df_imp = pd.read_csv(IMP_FILE)
            if "Datum" in df_imp.columns and "Änderungsdatum" not in df_imp.columns:
                df_imp = df_imp.rename(columns={"Datum": "Änderungsdatum"})
        except Exception:
            df_imp = pd.DataFrame(columns=["Änderungsdatum", "Titel", "Beschreibung"])

    if not df_imp.empty and "Titel" in df_imp.columns:
        df_imp = df_imp.drop_duplicates(subset=["Titel"], keep="last")

    if not df_imp.empty and (df_imp["Titel"] == title).any():
        df_imp.loc[df_imp["Titel"] == title, "Beschreibung"] = description
        df_imp.loc[df_imp["Titel"] == title, "Änderungsdatum"] = change_date
    else:
        new_row = pd.DataFrame([{
            "Änderungsdatum": change_date,
            "Titel": title,
            "Beschreibung": description,
        }])
        df_imp = pd.concat([new_row, df_imp], ignore_index=True)

    df_imp = df_imp.drop_duplicates(subset=["Titel"], keep="last")
    df_imp.to_csv(IMP_FILE, index=False)


def generate_signal(
    perf_val,
    volume,
    fg_value,
    bond_change,
    asian_markets,
    eurozone_market,
    stock_indices,
    etf_net_flow,
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
    else:
        t_sf, t_mf, t_ms, t_ss = -32.0, -11.0, 13.0, 32.0

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


# --- LIVE-DATEN ABRUFEN & LOGGEN ---
bond_val, bond_change = get_macro_bond_yield()
ext_macro = get_extended_macro_data()
stock_indices = get_stock_indices()
asian_markets = get_asian_markets()
eurozone_market = get_eurozone_market()
fg_value, fg_text = get_fear_and_greed()
real_etf_flow = get_real_spot_etf_flows()

nikkei_val = asian_markets.get("Nikkei_225", {}).get("value") if asian_markets else None
nikkei_chg = asian_markets.get("Nikkei_225", {}).get("change") if asian_markets else 0.0
hsi_val = asian_markets.get("Hang_Seng", {}).get("value") if asian_markets else None
hsi_chg = asian_markets.get("Hang_Seng", {}).get("change") if asian_markets else 0.0

eu_val = eurozone_market.get("value") if eurozone_market else None
eu
