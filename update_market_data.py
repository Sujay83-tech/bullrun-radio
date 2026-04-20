"""
BullRun Radio — Daily Market Data Auto-Updater  v2.0
=====================================================
Fetches live NSE stock prices (yfinance) + crypto (CoinGecko)
then injects data into index.html via HTML comment markers.

HOW TO RUN:
  python update_market_data.py          # normal run
  python update_market_data.py --test   # test mode (uses fallback data, no file write)
  python update_market_data.py --force  # force-write even if prices are 0
"""

import json, re, os, sys, time
from datetime import datetime, timezone, timedelta
import urllib.request, urllib.error

# ── yfinance import with friendly error ──────────────────────────────────────
try:
    import yfinance as yf
except ImportError:
    print("❌ yfinance not installed.")
    print("   Run: pip install yfinance requests")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — Edit these to change which stocks/cryptos are tracked
# ─────────────────────────────────────────────────────────────────────────────

IST = timezone(timedelta(hours=5, minutes=30))

# Yahoo Finance uses .NS suffix for NSE stocks
NSE_STOCKS = {
    # Key (your label) : Yahoo ticker
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

CRYPTO_IDS = {
    # CoinGecko ID : display symbol
    "bitcoin":  "BTC",
    "ethereum": "ETH",
    "solana":   "SOL",
}

HTML_FILE = "index.html"

# ─────────────────────────────────────────────────────────────────────────────
# FALLBACK DATA — used when market is closed or API fails
# Update these numbers roughly every month
# ─────────────────────────────────────────────────────────────────────────────

FALLBACK_STOCKS = {
    "BAJFINANCE": {"price": 7134,  "change_pct": 0.0, "week52_high": 7830,  "week52_low": 6200,  "range_pos": 88},
    "RELIANCE":   {"price": 2891,  "change_pct": 0.0, "week52_high": 3030,  "week52_low": 2220,  "range_pos": 85},
    "LTIM":       {"price": 5480,  "change_pct": 0.0, "week52_high": 5900,  "week52_low": 4200,  "range_pos": 74},
    "TATAPOWER":  {"price": 412,   "change_pct": 0.0, "week52_high": 430,   "week52_low": 280,   "range_pos": 87},
    "DIXON":      {"price": 14720, "change_pct": 0.0, "week52_high": 15200, "week52_low": 7800,  "range_pos": 94},
    "PAYTM":      {"price": 336,   "change_pct": 0.0, "week52_high": 680,   "week52_low": 310,   "range_pos": 7},
    "ZOMATO":     {"price": 138,   "change_pct": 0.0, "week52_high": 220,   "week52_low": 115,   "range_pos": 22},
    "NYKAA":      {"price": 142,   "change_pct": 0.0, "week52_high": 210,   "week52_low": 130,   "range_pos": 15},
    "INDUSINDBK": {"price": 1018,  "change_pct": 0.0, "week52_high": 1700,  "week52_low": 960,   "range_pos": 8},
    "PBFINTECH":  {"price": 1180,  "change_pct": 0.0, "week52_high": 1700,  "week52_low": 900,   "range_pos": 35},
    "NIFTY50":    {"price": 24312, "change_pct": 0.0, "week52_high": 26277, "week52_low": 19800, "range_pos": 70},
    "SENSEX":     {"price": 79943, "change_pct": 0.0, "week52_high": 85978, "week52_low": 64484, "range_pos": 71},
    "BANKNIFTY":  {"price": 51876, "change_pct": 0.0, "week52_high": 57000, "week52_low": 42000, "range_pos": 66},
}

FALLBACK_CRYPTO = {
    "BTC": {"price_inr": 7142000, "change_24h": 0.0, "high_52w": 8400000, "low_52w": 3800000, "range_pos": 82, "market_cap_b": 1380},
    "ETH": {"price_inr": 318500,  "change_24h": 0.0, "high_52w": 490000,  "low_52w": 180000,  "range_pos": 45, "market_cap_b": 383},
    "SOL": {"price_inr": 13200,   "change_24h": 0.0, "high_52w": 22800,   "low_52w": 4900,    "range_pos": 46, "market_cap_b": 71},
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — FETCH NSE DATA  (with after-hours fallback)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_nse_data() -> dict:
    print("📈 Fetching NSE stock data via yfinance...")
    result = {}

    for name, ticker in NSE_STOCKS.items():
        data = _fetch_single_stock(name, ticker)
        result[name] = data

    # How many succeeded (non-zero price)?
    ok = sum(1 for v in result.values() if v and v["price"] > 0)
    print(f"\n   → {ok}/{len(NSE_STOCKS)} stocks fetched with valid prices")

    # If fewer than half returned data, NSE is likely closed — use fallbacks
    if ok < len(NSE_STOCKS) // 2:
        print("   ⚠ Market appears closed or API throttled.")
        print("   ↩ Merging with fallback data for stocks with price=0...")
        for name in result:
            if not result[name] or result[name]["price"] == 0:
                result[name] = FALLBACK_STOCKS.get(name)
                print(f"     Fallback used for {name}")

    return result


def _fetch_single_stock(name: str, ticker: str) -> dict:
    """Fetch one stock with retries and multiple field fallbacks."""
    for attempt in range(3):
        try:
            tkr  = yf.Ticker(ticker)
            info = tkr.info

            # yfinance field names vary — try multiple fallbacks
            price = (info.get("currentPrice")
                  or info.get("regularMarketPrice")
                  or info.get("previousClose")
                  or 0)

            prev  = (info.get("previousClose")
                  or info.get("regularMarketPreviousClose")
                  or price)

            h52   = info.get("fiftyTwoWeekHigh") or price * 1.2
            l52   = info.get("fiftyTwoWeekLow")  or price * 0.8

            # If all zeros, try fast_info (newer yfinance versions)
            if price == 0:
                fi = tkr.fast_info
                price = getattr(fi, "last_price", 0) or 0
                prev  = getattr(fi, "previous_close", price) or price
                h52   = getattr(fi, "year_high", price * 1.2) or price * 1.2
                l52   = getattr(fi, "year_low",  price * 0.8) or price * 0.8

            price = float(price or 0)
            prev  = float(prev  or price)
            h52   = float(h52   or price * 1.2)
            l52   = float(l52   or price * 0.8)

            change_pct = ((price - prev) / prev * 100) if prev > 0 else 0.0
            span       = h52 - l52
            range_pos  = int(((price - l52) / span * 100)) if span > 0 else 50
            range_pos  = max(0, min(100, range_pos))

            sym = "✓" if price > 0 else "⚠"
            print(f"  {sym} {name:15s}  ₹{price:>10,.2f}  {change_pct:+.2f}%")

            return {
                "price":       round(price, 2),
                "change_pct":  round(change_pct, 2),
                "week52_high": round(h52, 2),
                "week52_low":  round(l52, 2),
                "range_pos":   range_pos,
            }

        except Exception as e:
            if attempt < 2:
                time.sleep(2)   # wait 2 seconds then retry
            else:
                print(f"  ✗ {name}: {e}")
                return FALLBACK_STOCKS.get(name)  # use fallback after 3 fails

    return FALLBACK_STOCKS.get(name)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — FETCH CRYPTO DATA
# ─────────────────────────────────────────────────────────────────────────────

def fetch_crypto_data() -> dict:
    print("\n₿  Fetching crypto data via CoinGecko...")

    ids_param = ",".join(CRYPTO_IDS.keys())
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        f"?vs_currency=inr&ids={ids_param}"
        "&order=market_cap_desc&sparkline=false&price_change_percentage=24h"
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BullRunRadio/2.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ✗ CoinGecko failed: {e}")
        print("  ↩ Using fallback crypto data")
        return FALLBACK_CRYPTO.copy()

    result = {}
    for coin in raw:
        cid    = coin["id"]
        symbol = CRYPTO_IDS.get(cid, cid.upper())
        price  = coin.get("current_price", 0) or 0
        chg    = coin.get("price_change_percentage_24h", 0) or 0
        mcap   = (coin.get("market_cap", 0) or 0) / 1e9

        h52, l52 = _fetch_crypto_52w(cid, price)
        span     = h52 - l52
        rpos     = int(((price - l52) / span * 100)) if span > 0 else 50
        rpos     = max(0, min(100, rpos))

        result[symbol] = {
            "price_inr":    price,
            "change_24h":   round(chg, 2),
            "high_52w":     round(h52, 0),
            "low_52w":      round(l52, 0),
            "range_pos":    rpos,
            "market_cap_b": round(mcap, 1),
        }
        sym = "▲" if chg >= 0 else "▼"
        print(f"  ✓ {symbol:6s}  ₹{price:>14,.0f}  {sym}{abs(chg):.2f}%")

    # Fill in any missing coins from fallback
    for sym, fb in FALLBACK_CRYPTO.items():
        if sym not in result:
            result[sym] = fb
            print(f"  ↩ Fallback used for {sym}")

    return result


def _fetch_crypto_52w(coin_id: str, current_price: float):
    """Get 365-day price history for real 52W high/low."""
    url = (
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        "?vs_currency=inr&days=365&interval=daily"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BullRunRadio/2.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        prices = [p[1] for p in data.get("prices", []) if p[1] > 0]
        if prices:
            return max(prices), min(prices)
    except Exception:
        pass
    # Fallback 52W values
    fb = FALLBACK_CRYPTO.get(coin_id.upper(), {})
    return (fb.get("high_52w", current_price * 1.4),
            fb.get("low_52w",  current_price * 0.6))


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — COMPUTE SIGNALS
# ─────────────────────────────────────────────────────────────────────────────

QUALITY = {"ZOMATO","INDUSINDBK","RELIANCE","BAJFINANCE","ETH","BTC","LTIM","TCS"}

def compute_signal(name: str, data: dict) -> str:
    pos = data.get("range_pos", 50)
    chg = data.get("change_pct", data.get("change_24h", 0)) or 0
    if pos >= 90: return "HOLD"   if chg < 2 else "WATCH"
    if pos >= 75: return "HOLD"   if chg >= 0 else "WATCH"
    if pos >= 40: return "BUY SIP" if chg >= 0 else "HOLD"
    if pos >= 15: return "ACCUM." if name in QUALITY else "WAIT"
    return           "ACCUM."    if name in QUALITY and chg >= 0 else "WAIT"

SIGNAL_CSS = {
    "BUY SIP": "s-buy", "BUY": "s-buy", "ACCUM.": "s-buy",
    "HOLD": "s-hold", "WATCH": "s-watch", "WAIT": "s-watch",
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — BUILD HTML FRAGMENTS
# ─────────────────────────────────────────────────────────────────────────────

STOCK_META = {
    "BAJFINANCE": "NBFC · NIFTY50",   "RELIANCE":   "CONGLOMERATE",
    "LTIM":       "IT SERVICES",       "TATAPOWER":  "POWER · MIDCAP",
    "DIXON":      "ELECTRONICS",       "PAYTM":      "FINTECH · SMALLCAP",
    "ZOMATO":     "FOOD DELIVERY",     "NYKAA":      "BEAUTY ECOM",
    "INDUSINDBK": "PRIVATE BANK",      "PBFINTECH":  "INSURTECH",
}

def build_stock_row(key: str, data: dict, display: str = None) -> str:
    if not data or data["price"] == 0:
        return ""
    label   = display or key
    sector  = STOCK_META.get(key, "")
    price   = data["price"]
    chg     = data["change_pct"]
    rpos    = data["range_pos"]
    signal  = compute_signal(key, data)
    sig_cls = SIGNAL_CSS.get(signal, "s-watch")
    chg_cls = "cu" if chg >= 0 else "cd"
    arrow   = "&#x25B2;" if chg >= 0 else "&#x25BC;"

    return (
        f'<tr><td><div class="sn">{label}</div><div class="ss">{sector}</div></td>'
        f'<td><div>&#x20B9;{price:,.0f}</div><div class="{chg_cls}">{arrow} {abs(chg):.1f}%</div></td>'
        f'<td><div class="range-wrap"><span class="rl">L</span>'
        f'<div class="range-track"><div class="range-fill" style="width:{rpos}%"></div>'
        f'<div class="range-dot" style="left:{rpos}%"></div></div>'
        f'<span class="rl">H</span></div></td>'
        f'<td><span class="signal {sig_cls}">{signal}</span></td></tr>'
    )


def build_ticker_html(stocks: dict, crypto: dict) -> str:
    items = []
    SHOW = [
        ("NIFTY50",   "NIFTY 50",   False),
        ("SENSEX",    "SENSEX",     False),
        ("BANKNIFTY", "NIFTY BANK", False),
        ("RELIANCE",  "RELIANCE",   True),
        ("BAJFINANCE","BAJFINANCE", True),
    ]
    for key, label, rupee in SHOW:
        d = stocks.get(key)
        if not d or d["price"] == 0:
            continue
        p   = d["price"]
        chg = d["change_pct"]
        cls = "up" if chg >= 0 else "dn"
        sym = "+" if chg >= 0 else ""
        val = f"&#x20B9;{p:,.0f}" if rupee else f"{p:,.0f}"
        items.append(f'<span class="{cls}">{label} &mdash; {val} {sym}{chg:.2f}%</span>')

    for symbol, d in crypto.items():
        if not d:
            continue
        p   = d["price_inr"]
        chg = d["change_24h"]
        cls = "up" if chg >= 0 else "dn"
        sym = "+" if chg >= 0 else ""
        items.append(f'<span class="{cls}">{symbol}/INR &mdash; &#x20B9;{p:,.0f} {sym}{chg:.2f}%</span>')

    inner = "".join(items)
    return inner + inner   # duplicate for seamless scroll


def build_crypto_cards(crypto: dict) -> str:
    META = {
        "BTC": ("#f5a623", "BITCOIN &middot; KING",        "&#x20BF;"),
        "ETH": ("#00d4ff", "ETHEREUM &middot; LAYER 1",    "&#x25C6;"),
        "SOL": ("#9945ff", "SOLANA &middot; HIGH PERF L1", "&#x25CE;"),
    }
    html = ""
    for symbol in ("BTC", "ETH", "SOL"):
        d = crypto.get(symbol)
        if not d:
            continue
        color, name, icon = META[symbol]
        price  = d["price_inr"]
        chg    = d["change_24h"]
        rpos   = d["range_pos"]
        mcap   = d["market_cap_b"]
        h52    = d["high_52w"]
        l52    = d["low_52w"]
        signal = compute_signal(symbol, {"range_pos": rpos, "change_24h": chg})
        chg_cls= "cu" if chg >= 0 else "cd"
        arrow  = "&#x25B2;" if chg >= 0 else "&#x25BC;"

        def fmt_inr(v):
            if v >= 100000: return f"&#x20B9;{v/100000:.1f}L"
            if v >= 1000:   return f"&#x20B9;{v/1000:.0f}K"
            return f"&#x20B9;{v:,.0f}"

        html += (
            f'<div class="crypto-card" data-icon="{icon}">'
            f'<div class="crypto-symbol">{symbol}</div>'
            f'<div class="crypto-name">{name}</div>'
            f'<div class="crypto-price" style="color:{color}">{fmt_inr(price)}</div>'
            f'<div class="crypto-change {chg_cls}">{arrow} {abs(chg):.1f}% (24h)</div>'
            f'<div class="mini-chart"><svg viewBox="0 0 120 48" preserveAspectRatio="none">'
            f'<polyline points="0,40 30,32 60,20 90,{max(4,48-int(rpos*0.38))} 120,{max(4,48-int(rpos*0.44))}"'
            f' fill="none" stroke="{color}" stroke-width="2"/></svg></div>'
            f'<div class="range-wrap" style="margin-top:12px">'
            f'<span class="range-label" style="font-size:9px;color:var(--muted)">52W L {fmt_inr(l52)}</span>'
            f'<div class="range-track" style="flex:1">'
            f'<div class="range-fill" style="width:{rpos}%"></div>'
            f'<div class="range-dot" style="left:{rpos}%"></div></div>'
            f'<span class="range-label" style="font-size:9px;color:var(--muted)">52W H {fmt_inr(h52)}</span></div>'
            f'<div class="crypto-stats">'
            f'<div><div class="cstat-label">MKT CAP</div><div class="cstat-val">${mcap:.0f}B</div></div>'
            f'<div><div class="cstat-label">SIGNAL</div><div class="cstat-val" style="color:{color}">{signal}</div></div>'
            f'</div></div>'
        )
    return html


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — INJECT INTO HTML
# ─────────────────────────────────────────────────────────────────────────────

def inject_into_html(stocks: dict, crypto: dict):
    if not os.path.exists(HTML_FILE):
        print(f"\n❌ '{HTML_FILE}' not found in: {os.getcwd()}")
        print("   Make sure you run this script from the same folder as index.html")
        sys.exit(1)

    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    now_ist = datetime.now(IST).strftime("%B %d, %Y — %I:%M %p IST")

    def replace(pattern, replacement):
        return re.sub(pattern, replacement, html, flags=re.DOTALL)

    # 1. Ticker tape
    ticker_html = build_ticker_html(stocks, crypto)
    html = re.sub(
        r"<!-- TICKER_START -->.*?<!-- TICKER_END -->",
        f"<!-- TICKER_START -->{ticker_html}<!-- TICKER_END -->",
        html, flags=re.DOTALL
    )

    # 2. 52W HIGH stock rows
    high_rows = (
        build_stock_row("BAJFINANCE", stocks.get("BAJFINANCE"), "BAJFINANCE") +
        build_stock_row("RELIANCE",   stocks.get("RELIANCE"),   "RELIANCE")   +
        build_stock_row("DIXON",      stocks.get("DIXON"),      "DIXON TECH")
    )
    html = re.sub(
        r"<!-- 52W_HIGH_ROWS_START -->.*?<!-- 52W_HIGH_ROWS_END -->",
        f"<!-- 52W_HIGH_ROWS_START -->\n        {high_rows}\n      <!-- 52W_HIGH_ROWS_END -->",
        html, flags=re.DOTALL
    )

    # 3. 52W LOW stock rows
    low_rows = (
        build_stock_row("ZOMATO",     stocks.get("ZOMATO"),     "ZOMATO")     +
        build_stock_row("INDUSINDBK", stocks.get("INDUSINDBK"), "INDUSINDBK") +
        build_stock_row("PAYTM",      stocks.get("PAYTM"),      "PAYTM")
    )
    html = re.sub(
        r"<!-- 52W_LOW_ROWS_START -->.*?<!-- 52W_LOW_ROWS_END -->",
        f"<!-- 52W_LOW_ROWS_START -->\n        {low_rows}\n      <!-- 52W_LOW_ROWS_END -->",
        html, flags=re.DOTALL
    )

    # 4. Timestamp in ticker (if present)
    if "<!-- LAST_UPDATED -->" in html:
        html = re.sub(
            r"<!-- LAST_UPDATED -->.*?<!-- /LAST_UPDATED -->",
            f"<!-- LAST_UPDATED -->{now_ist}<!-- /LAST_UPDATED -->",
            html, flags=re.DOTALL
        )

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ {HTML_FILE} updated at {now_ist}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    TEST_MODE  = "--test"  in sys.argv
    FORCE_MODE = "--force" in sys.argv

    print("=" * 55)
    print("  BullRun Radio — Market Data Updater v2.0")
    if TEST_MODE:
        print("  MODE: TEST (using fallback data, no file write)")
    print("=" * 55)

    if TEST_MODE:
        stocks = FALLBACK_STOCKS.copy()
        crypto = FALLBACK_CRYPTO.copy()
        print("✓ Using fallback data (test mode)")
    else:
        stocks = fetch_nse_data()
        crypto = fetch_crypto_data()

    # ── Validation: warn if too many zeros ───────────────────────────────────
    zero_count = sum(1 for v in stocks.values() if v and v["price"] == 0)
    if zero_count > 5 and not FORCE_MODE:
        print(f"\n⚠ WARNING: {zero_count} stocks have price=0 (market closed?)")
        print("  The HTML will NOT be updated to avoid clearing all prices.")
        print("  To force-update anyway, run: python update_market_data.py --force")
        print("  The scheduled GitHub Actions job runs at 9 AM IST when market is open.")
        sys.exit(0)

    if not TEST_MODE:
        inject_into_html(stocks, crypto)

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n📊 Summary:")
    live   = sum(1 for v in stocks.values() if v and v["price"] > 0)
    fb_used= sum(1 for v in stocks.values() if v and v["price"] == 0)
    print(f"   Stocks  — Live: {live}  |  Fallback: {len(stocks)-live}")
    print(f"   Cryptos — {len(crypto)} fetched")
    print("=" * 55)
