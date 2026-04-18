"""
BullRun Radio — Daily Market Data Auto-Updater
=============================================
Fetches live NSE stock prices (via yfinance) and crypto prices (via CoinGecko API)
then injects the data into index.html.

Run locally:  python update_market_data.py
Run via CI:   GitHub Actions calls this every day at 9:00 AM IST
"""

import json
import re
import os
import sys
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

# ── Try importing yfinance, install hint if missing ──────────────────────────
try:
    import yfinance as yf
except ImportError:
    print("ERROR: yfinance not installed. Run: pip install yfinance")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

IST = timezone(timedelta(hours=5, minutes=30))

# NSE tickers on Yahoo Finance use the .NS suffix
NSE_STOCKS = {
    "BAJFINANCE": "BAJFINANCE.NS",
    "RELIANCE":   "RELIANCE.NS",
    "LTIM":       "LTIM.NS",
    "TATAPOWER":  "TATAPOWER.NS",
    "DIXON":      "DIXON.NS",
    "PAYTM":      "PAYTM.NS",
    "ZOMATO":     "ZOMATO.NS",
    "NYKAA":      "NYKAA.NS",
    "INDUSINDBK": "INDUSINDBK.NS",
    "PBFINTECH":  "PBFINTECH.NS",
    "NIFTY50":    "^NSEI",
    "SENSEX":     "^BSESN",
    "BANKNIFTY":  "^NSEBANK",
}

# CoinGecko IDs → maps to our display names
CRYPTO_IDS = {
    "bitcoin":  "BTC",
    "ethereum": "ETH",
    "solana":   "SOL",
}

HTML_FILE = "index.html"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — FETCH NSE DATA
# ─────────────────────────────────────────────────────────────────────────────

def fetch_nse_data() -> dict:
    """
    Returns a dict like:
    {
      "RELIANCE": {"price": 2891.5, "change_pct": 1.2, "week52_high": 3024.0, "week52_low": 2220.0},
      ...
    }
    """
    print("📈 Fetching NSE stock data via yfinance...")
    result = {}

    for name, ticker in NSE_STOCKS.items():
        try:
            tkr = yf.Ticker(ticker)
            info = tkr.info

            price       = info.get("currentPrice") or info.get("regularMarketPrice") or 0
            prev_close  = info.get("previousClose") or info.get("regularMarketPreviousClose") or price
            high_52w    = info.get("fiftyTwoWeekHigh") or price
            low_52w     = info.get("fiftyTwoWeekLow")  or price

            change_pct  = ((price - prev_close) / prev_close * 100) if prev_close else 0

            # 52W range position (0–100%)
            range_span  = high_52w - low_52w
            range_pos   = int(((price - low_52w) / range_span * 100)) if range_span > 0 else 50

            result[name] = {
                "price":        round(price, 2),
                "change_pct":   round(change_pct, 2),
                "week52_high":  round(high_52w, 2),
                "week52_low":   round(low_52w, 2),
                "range_pos":    range_pos,           # % position in 52W range
                "ticker":       ticker,
            }
            print(f"  ✓ {name:15s} ₹{price:>10,.2f}  {change_pct:+.2f}%")
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            result[name] = None

    return result


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — FETCH CRYPTO DATA
# ─────────────────────────────────────────────────────────────────────────────

def fetch_crypto_data() -> dict:
    """
    Uses CoinGecko free API — no key required.
    Returns prices in INR + USD, 24h change, 52W high/low.
    """
    print("\n₿  Fetching crypto data via CoinGecko...")

    ids_param = ",".join(CRYPTO_IDS.keys())
    url = (
        f"https://api.coingecko.com/api/v3/coins/markets"
        f"?vs_currency=inr"
        f"&ids={ids_param}"
        f"&order=market_cap_desc"
        f"&sparkline=false"
        f"&price_change_percentage=24h"
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BullRunRadio/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"  ✗ CoinGecko API error: {e}")
        return {}

    result = {}
    for coin in data:
        cid    = coin["id"]
        symbol = CRYPTO_IDS.get(cid, cid.upper())

        price_inr   = coin.get("current_price", 0)
        change_24h  = coin.get("price_change_percentage_24h", 0) or 0
        high_52w    = coin.get("high_24h", price_inr) * 365   # CoinGecko free tier doesn't give 52W
        # ↑ For real 52W, we'd need a paid endpoint; use approximate heuristic or historical fetch
        # Better approach: fetch 365-day history
        high_52w, low_52w = fetch_crypto_52w(cid, price_inr)

        range_span = high_52w - low_52w
        range_pos  = int(((price_inr - low_52w) / range_span * 100)) if range_span > 0 else 50

        market_cap_b = coin.get("market_cap", 0) / 1e9

        result[symbol] = {
            "price_inr":   price_inr,
            "change_24h":  round(change_24h, 2),
            "high_52w":    round(high_52w, 0),
            "low_52w":     round(low_52w, 0),
            "range_pos":   max(0, min(100, range_pos)),
            "market_cap_b": round(market_cap_b, 1),
        }
        print(f"  ✓ {symbol:6s} ₹{price_inr:>14,.0f}  {change_24h:+.2f}%")

    return result


def fetch_crypto_52w(coin_id: str, current_price: float):
    """Fetch 365-day price history from CoinGecko to calculate real 52W high/low."""
    url = (
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        f"?vs_currency=inr&days=365&interval=daily"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BullRunRadio/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        prices = [p[1] for p in data.get("prices", [])]
        if prices:
            return max(prices), min(prices)
    except Exception:
        pass
    # Fallback estimates if API fails
    fallbacks = {
        "bitcoin":  (8400000, 3800000),
        "ethereum": (490000,  180000),
        "solana":   (22800,   4900),
    }
    return fallbacks.get(coin_id, (current_price * 1.5, current_price * 0.5))


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — COMPUTE BUY/HOLD/WATCH SIGNALS
# ─────────────────────────────────────────────────────────────────────────────

def compute_signal(name: str, data: dict) -> str:
    """
    Simple rule-based signal engine:
    - Near 52W HIGH (range_pos > 85%) + positive change → HOLD (avoid chasing)
    - Near 52W HIGH + strong momentum (change > 2%) → WATCH (breakout potential)
    - Near 52W LOW (range_pos < 20%) + known quality stock → ACCUMULATE
    - Middle range with positive change → BUY SIP
    - Negative change + low range → WAIT (no reversal yet)
    """
    pos = data.get("range_pos", 50)
    chg = data.get("change_pct", data.get("change_24h", 0))

    # Stocks we consider "quality" for accumulation at lows
    quality_stocks = {"ZOMATO", "INDUSINDBK", "RELIANCE", "BAJFINANCE", "TCS", "ETH", "BTC"}

    if pos >= 90:
        return "HOLD" if chg < 2 else "WATCH"
    elif pos >= 75:
        return "HOLD" if chg >= 0 else "WATCH"
    elif pos >= 40:
        return "BUY SIP" if chg >= 0 else "HOLD"
    elif pos >= 15:
        return "ACCUM." if name in quality_stocks else "WAIT"
    else:
        return "ACCUM." if name in quality_stocks and chg >= 0 else "WAIT"


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — BUILD TICKER TAPE STRING
# ─────────────────────────────────────────────────────────────────────────────

def build_ticker_html(stocks: dict, crypto: dict) -> str:
    items = []

    ticker_map = {
        "NIFTY50":    ("NIFTY 50", "NSEI"),
        "SENSEX":     ("SENSEX",   "BSE"),
        "BANKNIFTY":  ("NIFTY BANK", "BANK"),
        "RELIANCE":   ("RELIANCE", "₹"),
        "BAJFINANCE": ("BAJFINANCE", "₹"),
        "LTIM":       ("LTIM", "₹"),
        "TATAPOWER":  ("TATA POWER", "₹"),
    }

    for key, (label, prefix) in ticker_map.items():
        d = stocks.get(key)
        if not d:
            continue
        p   = d["price"]
        chg = d["change_pct"]
        cls = "up" if chg >= 0 else "down"
        sym = "+" if chg >= 0 else ""
        val = f"₹{p:,.0f}" if prefix == "₹" else f"{p:,.2f}"
        items.append(f'<span class="{cls}">{label} — {val} {sym}{chg:.2f}%</span>')

    for symbol, d in crypto.items():
        p   = d["price_inr"]
        chg = d["change_24h"]
        cls = "up" if chg >= 0 else "down"
        sym = "+" if chg >= 0 else ""
        items.append(f'<span class="{cls}">{symbol}/INR — ₹{p:,.0f} {sym}{chg:.2f}%</span>')

    # Duplicate for seamless scroll
    all_items = "".join(items)
    return all_items + all_items


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — BUILD STOCK TABLE ROWS
# ─────────────────────────────────────────────────────────────────────────────

SIGNAL_CLASS = {
    "BUY":     "signal-buy",
    "BUY SIP": "signal-buy",
    "ACCUM.":  "signal-buy",
    "HOLD":    "signal-hold",
    "WATCH":   "signal-watch",
    "WAIT":    "signal-watch",
}

STOCK_META = {
    "BAJFINANCE": ("NBFC · NIFTY50",           "HIGH"),
    "RELIANCE":   ("CONGLOMERATE · NIFTY50",   "HIGH"),
    "LTIM":       ("IT SERVICES · NIFTY50",    "HIGH"),
    "TATAPOWER":  ("POWER · MIDCAP",           "HIGH"),
    "DIXON":      ("ELECTRONICS · SMALLCAP",   "HIGH"),
    "PAYTM":      ("FINTECH · SMALLCAP",       "LOW"),
    "ZOMATO":     ("FOOD DELIVERY · NIFTY200", "LOW"),
    "NYKAA":      ("BEAUTY ECOM · SMALLCAP",   "LOW"),
    "INDUSINDBK": ("PRIVATE BANK · NIFTY50",   "LOW"),
    "PBFINTECH":  ("INSURTECH · MIDCAP",       "LOW"),
}

def build_stock_row(name: str, data: dict, display_name: str = None) -> str:
    if not data:
        return ""
    label    = display_name or name
    sector   = STOCK_META.get(name, ("", ""))[0]
    price    = data["price"]
    chg      = data["change_pct"]
    rpos     = data["range_pos"]
    signal   = compute_signal(name, data)
    sig_cls  = SIGNAL_CLASS.get(signal, "signal-watch")
    chg_cls  = "change-up" if chg >= 0 else "change-down"
    chg_sym  = "▲" if chg >= 0 else "▼"

    return f"""
          <tr>
            <td>
              <div class="stock-name">{label}</div>
              <div class="stock-sector">{sector}</div>
            </td>
            <td>
              <div class="price-val">₹{price:,.0f}</div>
              <div class="{chg_cls}">{chg_sym} {abs(chg):.1f}%</div>
            </td>
            <td>
              <div class="range-wrap">
                <span class="range-label">L</span>
                <div class="range-track">
                  <div class="range-fill" style="width:{rpos}%"></div>
                  <div class="range-dot" style="left:{rpos}%"></div>
                </div>
                <span class="range-label">H</span>
              </div>
            </td>
            <td><span class="signal {sig_cls}">{signal}</span></td>
          </tr>"""


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — BUILD CRYPTO CARD HTML
# ─────────────────────────────────────────────────────────────────────────────

CRYPTO_META = {
    "BTC": {"color": "#f5a623", "name": "BITCOIN · KING",         "icon": "₿",  "id": "bitcoin"},
    "ETH": {"color": "#00d4ff", "name": "ETHEREUM · LAYER 1",     "icon": "◆",  "id": "ethereum"},
    "SOL": {"color": "#9945ff", "name": "SOLANA · HIGH PERF L1",  "icon": "◎",  "id": "solana"},
}

def build_crypto_card(symbol: str, data: dict) -> str:
    if not data:
        return ""
    meta     = CRYPTO_META.get(symbol, {})
    color    = meta.get("color", "#ffffff")
    cname    = meta.get("name", symbol)
    icon     = meta.get("icon", "●")
    price    = data["price_inr"]
    chg      = data["change_24h"]
    rpos     = data["range_pos"]
    mcap     = data["market_cap_b"]
    h52      = data["high_52w"]
    l52      = data["low_52w"]
    signal   = compute_signal(symbol, {"range_pos": rpos, "change_24h": chg})
    sig_cls  = SIGNAL_CLASS.get(signal, "signal-watch")
    chg_cls  = "change-up" if chg >= 0 else "change-down"
    chg_sym  = "▲" if chg >= 0 else "▼"

    # Format large INR values nicely
    def fmt_inr(v):
        if v >= 100000:
            return f"₹{v/100000:.1f}L"
        elif v >= 1000:
            return f"₹{v/1000:.0f}K"
        return f"₹{v:,.0f}"

    return f"""
    <div class="crypto-card" data-icon="{icon}">
      <div class="crypto-symbol">{symbol}</div>
      <div class="crypto-name">{cname}</div>
      <div class="crypto-price" style="color:{color}">{fmt_inr(price)}</div>
      <div class="crypto-change {chg_cls}">{chg_sym} {abs(chg):.1f}% (24h)</div>
      <div class="mini-chart">
        <svg viewBox="0 0 120 48" preserveAspectRatio="none">
          <polyline points="0,40 20,32 40,36 60,20 80,24 100,10 120,{max(4, 48 - int(rpos*0.4))}"
            fill="none" stroke="{color}" stroke-width="2"/>
        </svg>
      </div>
      <div class="range-wrap" style="margin-top:12px">
        <span class="range-label" style="font-size:9px;color:var(--muted)">52W L {fmt_inr(l52)}</span>
        <div class="range-track" style="flex:1">
          <div class="range-fill" style="width:{rpos}%"></div>
          <div class="range-dot" style="left:{rpos}%"></div>
        </div>
        <span class="range-label" style="font-size:9px;color:var(--muted)">52W H {fmt_inr(h52)}</span>
      </div>
      <div class="crypto-stats">
        <div><div class="cstat-label">MKT CAP</div><div class="cstat-val">${mcap:.0f}B</div></div>
        <div><div class="cstat-label">SIGNAL</div><div class="cstat-val" style="color:{color}">{signal}</div></div>
      </div>
    </div>"""


# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — INJECT INTO HTML
# ─────────────────────────────────────────────────────────────────────────────

def inject_into_html(stocks: dict, crypto: dict):
    if not os.path.exists(HTML_FILE):
        print(f"✗ {HTML_FILE} not found in current directory!")
        sys.exit(1)

    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    now_ist = datetime.now(IST).strftime("%B %d, %Y — %I:%M %p IST")

    # ── 1. Update ticker tape ───────────────────────────────────────────────
    ticker_html = build_ticker_html(stocks, crypto)
    html = re.sub(
        r'<!-- TICKER_START -->.*?<!-- TICKER_END -->',
        f'<!-- TICKER_START -->{ticker_html}<!-- TICKER_END -->',
        html, flags=re.DOTALL
    )

    # ── 2. Update 52W HIGH table ────────────────────────────────────────────
    high_rows = "".join([
        build_stock_row("BAJFINANCE", stocks.get("BAJFINANCE"), "BAJFINANCE"),
        build_stock_row("RELIANCE",   stocks.get("RELIANCE"),   "RELIANCE"),
        build_stock_row("LTIM",       stocks.get("LTIM"),       "LTIM"),
        build_stock_row("TATAPOWER",  stocks.get("TATAPOWER"),  "TATAPOWER"),
        build_stock_row("DIXON",      stocks.get("DIXON"),      "DIXON TECH"),
    ])
    html = re.sub(
        r'<!-- 52W_HIGH_ROWS_START -->.*?<!-- 52W_HIGH_ROWS_END -->',
        f'<!-- 52W_HIGH_ROWS_START -->{high_rows}<!-- 52W_HIGH_ROWS_END -->',
        html, flags=re.DOTALL
    )

    # ── 3. Update 52W LOW table ─────────────────────────────────────────────
    low_rows = "".join([
        build_stock_row("PAYTM",      stocks.get("PAYTM"),      "PAYTM"),
        build_stock_row("ZOMATO",     stocks.get("ZOMATO"),     "ZOMATO"),
        build_stock_row("NYKAA",      stocks.get("NYKAA"),      "NYKAA"),
        build_stock_row("INDUSINDBK", stocks.get("INDUSINDBK"), "INDUSIND BANK"),
        build_stock_row("PBFINTECH",  stocks.get("PBFINTECH"),  "PB FINTECH"),
    ])
    html = re.sub(
        r'<!-- 52W_LOW_ROWS_START -->.*?<!-- 52W_LOW_ROWS_END -->',
        f'<!-- 52W_LOW_ROWS_START -->{low_rows}<!-- 52W_LOW_ROWS_END -->',
        html, flags=re.DOTALL
    )

    # ── 4. Update crypto cards ──────────────────────────────────────────────
    crypto_cards_html = "".join([
        build_crypto_card("BTC", crypto.get("BTC")),
        build_crypto_card("ETH", crypto.get("ETH")),
        build_crypto_card("SOL", crypto.get("SOL")),
    ])
    html = re.sub(
        r'<!-- CRYPTO_CARDS_START -->.*?<!-- CRYPTO_CARDS_END -->',
        f'<!-- CRYPTO_CARDS_START -->{crypto_cards_html}<!-- CRYPTO_CARDS_END -->',
        html, flags=re.DOTALL
    )

    # ── 5. Update "Last updated" timestamp ─────────────────────────────────
    html = re.sub(
        r'<!-- LAST_UPDATED -->.*?<!-- /LAST_UPDATED -->',
        f'<!-- LAST_UPDATED -->{now_ist}<!-- /LAST_UPDATED -->',
        html, flags=re.DOTALL
    )

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ {HTML_FILE} updated successfully at {now_ist}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  BullRun Radio — Daily Market Data Updater")
    print("=" * 55)

    stocks = fetch_nse_data()
    crypto = fetch_crypto_data()
    inject_into_html(stocks, crypto)

    print("\n📊 Summary:")
    print(f"   Stocks fetched : {sum(1 for v in stocks.values() if v)}/{len(NSE_STOCKS)}")
    print(f"   Cryptos fetched: {len(crypto)}/{len(CRYPTO_IDS)}")
    print("=" * 55)
