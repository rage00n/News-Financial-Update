
import json
import re
import logging
import requests
import feedparser
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone

# ============================================================
#  CONFIG
# ============================================================

WATCHLIST_FILE = "watchlist.json"
import os
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
    
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
#  Safe JSON parsing – kills control characters
# ============================================================
def safe_parse_json(response_text):
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        cleaned = re.sub(r'[\x00-\x1f]', '', response_text)
        logger.warning("Stripped control characters from JSON response")
        return json.loads(cleaned)

# ============================================================
#  Watchlist loading (tickers + name map)
# ============================================================
def load_watchlist():
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        raw_text = f.read()
    # Remove control characters that crash JSON
    cleaned = re.sub(r'[\x00-\x1f]', '', raw_text)
    raw = json.loads(cleaned)

    tickers = []
    name_map = {}
    for item in raw:
        if isinstance(item, dict):
            ticker = item.get("ticker")
            name = item.get("name", None)
        else:
            ticker = item
            name = None
        tickers.append(ticker)
        if name:
            name_map[ticker] = name
    return tickers, name_map



# ============================================================
#  Yahoo Finance helpers
# ============================================================
def get_stock_data(ticker, period="6mo"):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        if hist.empty:
            return None
        close = hist["Close"]
        price = close.iloc[-1]
        ma20 = close.rolling(window=20).mean().iloc[-1]
        ma50 = close.rolling(window=50).mean().iloc[-1] if len(close) >= 50 else None
        low6m, high6m = close.min(), close.max()
        range_pct = (price - low6m) / (high6m - low6m) if high6m != low6m else 0.5
        return {
            "ticker": ticker,
            "price": round(price, 2),
            "ma20": round(ma20, 2) if not pd.isna(ma20) else None,
            "ma50": round(ma50, 2) if ma50 and not pd.isna(ma50) else None,
            "range_percentile": round(range_pct * 100, 1),
            "price_vs_ma20": round((price / ma20 - 1) * 100, 1) if ma20 else None,
        }
    except:
        return None

def filter_extreme_tickers(all_data, thresholds):
    extremas = []
    for d in all_data:
        reasons = []
        if d["range_percentile"] is not None:
            if d["range_percentile"] <= thresholds["range_bottom"]:
                reasons.append(f"RangePtl {d['range_percentile']}% (bottom)")
            elif d["range_percentile"] >= thresholds["range_top"]:
                reasons.append(f"RangePtl {d['range_percentile']}% (top)")
        if d["price_vs_ma20"] is not None:
            if d["price_vs_ma20"] <= thresholds["ma20_below"]:
                reasons.append(f"vsMA20 {d['price_vs_ma20']}%")
            elif d["price_vs_ma20"] >= thresholds["ma20_above"]:
                reasons.append(f"vsMA20 +{d['price_vs_ma20']}%")
        if reasons:
            d["why_extreme"] = "; ".join(reasons)
            extremas.append(d)
    return extremas

def build_market_table(rows):
    if not rows:
        return None
    header = f"{'Ticker':<8} {'Price':>8} {'MA20':>8} {'MA50':>8} {'%vs20':>8} {'RangePtl':>10}  Signal"
    lines = [header, "-" * len(header)]
    for r in rows:
        signal = r.get("why_extreme", "")
        lines.append(
            f"{r['ticker']:<8} {r['price']:>8} "
            f"{r['ma20'] if r['ma20'] else 'N/A':>8} "
            f"{r['ma50'] if r['ma50'] else 'N/A':>8} "
            f"{r['price_vs_ma20'] if r['price_vs_ma20'] is not None else 'N/A':>7}% "
            f"{r['range_percentile']:>9}%  {signal}"
        )
    return "\n".join(lines)

# ============================================================
#  Ticker extraction (regex, uses watchlist name map)
# ============================================================
def extract_tickers_from_headlines(raw_items, name_map=None):
    if name_map is None:
        name_map = {}
    NON_TICKER = {
        "AI", "IPO", "CEO", "CFO", "GDP", "ETF", "USA", "US", "EU", "UK",
        "API", "IOS", "MAC", "PC", "TV", "USD", "SGD", "A", "I", "AM", "PM",
        "THE", "FOR", "AND", "NOT", "BUT", "WAS", "ARE", "IS", "IT", "AN",
        "AT", "BY", "IN", "ON", "OF", "TO", "OR", "BE", "MY", "WE", "NO"
    }
    pattern = re.compile(r'\b[A-Z]{2,5}\b')
    result = {}

    for item in raw_items:
        title = item.get("title", "")
        if not title:
            continue
        potential = pattern.findall(title)
        valid_symbols = list(dict.fromkeys(t for t in potential if t not in NON_TICKER))
        if valid_symbols:
            named = []
            for sym in valid_symbols:
                full = name_map.get(sym)
                named.append((sym, full))
            result[title] = named
    return result

# ============================================================
#  RSS‑only news fetcher
# ============================================================
GLOBAL_TECH_FEEDS = [
    "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en",
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/index",
]

SG_FEEDS = [
    "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB?hl=en-SG&gl=SG&ceid=SG:en",
    "https://www.channelnewsasia.com/rss/latestnews",
    "https://www.businesstimes.com.sg/api/rss/sgx-news",
]

import requests as req

def fetch_feed_with_timeout(url, timeout=15):
    """Download an RSS feed with a timeout, return parsed entries or empty list."""
    try:
        resp = req.get(url, timeout=timeout)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        return feed.entries if feed.entries else []
    except Exception as e:
        print(f"RSS timeout/error for {url}: {e}")
        return []

def fetch_news():
    raw_global = []
    raw_sg = []


    # --- Global tech ---
    for url in GLOBAL_TECH_FEEDS:
        try:
            entries = fetch_feed_with_timeout(url, timeout=10)
            if entries:
                for entry in entries[:6]:
                    title = entry.get("title", "").strip()
                    link = entry.get("link", "")
                    if len(title) >= 20:
                        title = re.sub(r'\s*[-|]\s*[^-|]+$', '', title).strip()
                        raw_global.append({"title": title, "url": link})
        except Exception as e:
            logger.warning("Global RSS error (%s): %s", url, e)

    seen = set()
    deduped_global = []
    for item in raw_global:
        if item["title"] not in seen:
            seen.add(item["title"])
            deduped_global.append(item)
    raw_global = deduped_global[:12]

    # --- Singapore ---
    for url in SG_FEEDS:
        try:
            entries = fetch_feed_with_timeout(url, timeout=10)
            if entries:
                for entry in entries[:4]:
                    title = entry.get("title", "").strip()
                    link = entry.get("link", "")
                    if len(title) >= 20:
                        title = re.sub(r'\s*[-|]\s*[^-|]+$', '', title).strip()
                        raw_sg.append({"title": title, "url": link})
        except Exception as e:
            logger.warning("SG RSS error (%s): %s", url, e)

    seen = set()
    deduped_sg = []
    for item in raw_sg:
        if item["title"] not in seen:
            seen.add(item["title"])
            deduped_sg.append(item)
    raw_sg = deduped_sg[:10]

# If no Singapore headlines were fetched, try a fallback feed
    if not raw_sg:
        fallback_url = "https://www.channelnewsasia.com/rss/latestnews"
        entries = fetch_feed_with_timeout(fallback_url, timeout=15)
        for entry in entries[:3]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            if len(title) >= 20:
                title = re.sub(r'\s*[-|]\s*[^-|]+$', '', title).strip()
                raw_sg.append({"title": title, "url": link})

    if not raw_global and not raw_sg:
        return "No noteworthy news available today."

    # Ticker detection
    _, name_map = load_watchlist()
    ticker_map = extract_tickers_from_headlines(raw_global + raw_sg, name_map)

    return curate_headlines(raw_global, raw_sg, ticker_map)

# ============================================================
#  Claude curation of headlines (with safe JSON)
# ============================================================
def curate_headlines(raw_global, raw_sg, ticker_map=None):
    if not raw_global and not raw_sg:
        return "No noteworthy news today."

    global_str = "\n".join(f"- {h['title']}  [URL: {h['url']}]" for h in raw_global) if raw_global else "(none)"
    sg_str = "\n".join(f"- {h['title']}  [URL: {h['url']}]" for h in raw_sg) if raw_sg else "(none)"

    ticker_text = ""
    if ticker_map:
        lines = []
        for title, tickers in ticker_map.items():
            desc = ", ".join(f"{sym} ({name or 'unknown'})" for sym, name in tickers)
            lines.append(f"- {title[:60]}... → {desc}")
        if lines:
            ticker_text = "Detected ticker mentions:\n" + "\n".join(lines)

    prompt = f"""
You are a senior investment editor. From the lists below, select up to **5 global tech** and up to **3 Singapore** headlines that are truly relevant to a personal investor. Ignore pop culture, gadgets, puzzles, etc. — only market‑moving or company‑impacting stories.

For each selected headline, output exactly this format (including the emoji, link, and action line):

🟢/🟡/🔴 • Headline text — [Read more](URL)
  _Why it matters: one short sentence explaining the investment impact._
  _Action: a one‑sentence suggestion an investor could consider._
  📌 Watch: For ANY selected headline where ticker mentions are detected, you MUST include the line: 📌 Watch: SYMBOL1 (Full Name), SYMBOL2 (Full Name), … using the names provided in the "Detected ticker mentions" list exactly as shown. If no names are available, use SYMBOL alone. If no tickers are detected for a headline, omit this line completely.
Use these sentiment tags:
- 🟢 if the news is market‑positive or bullish for the assets mentioned.
- 🟡 if the impact is neutral, uncertain, or mixed.
- 🔴 if the news is negative or bearish.

Return EXACTLY:

GLOBAL:
🟢/... • ...
  _Why it matters: ..._
  _Action: ..._
  📌 Watch: ...

SINGAPORE:
🟢/... • ...
  _Why it matters: ..._
  _Action: ..._
  📌 Watch: ...

If no relevant headlines in a category, write "(none)".

Global headlines:
{global_str}

Singapore headlines:
{sg_str}

{ticker_text}
"""

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    data = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 550,
        "system": "You are a concise financial editor. Filter fluff, assign sentiment, and suggest a one‑line action.",
        "messages": [{"role": "user", "content": prompt}]
    }

    try:
        resp = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=data, timeout=20)
        if resp.status_code == 200:
            result = safe_parse_json(resp.text)
            curated = result["content"][0]["text"]
            return re.sub(r'[^\x20-\x7E\n•_🟢🔴🟡]', '', curated)
        else:
            return f"❌ Claude error: {resp.status_code}"
    except Exception as e:
        logger.exception("curate_headlines failed")
        # Fallback: raw headlines
        fallback = ""
        if raw_global:
            fallback += "🌐 **Global Tech / Markets**\n" + "\n".join(f"• {h['title']}" for h in raw_global[:5])
        if raw_sg:
            fallback += "\n🇸🇬 **Singapore / Local**\n" + "\n".join(f"• {h['title']}" for h in raw_sg[:3])
        return fallback

# ============================================================
#  Extreme‑ticker scan
# ============================================================
def run_full_scan():
    watchlist, _ = load_watchlist()
    all_data = []
    failed = []
    for t in watchlist:
        d = get_stock_data(t)
        if d:
            all_data.append(d)
        else:
            failed.append(t)

    thresholds = {"range_bottom": 20, "range_top": 80, "ma20_below": -3, "ma20_above": 3}
    extreme = filter_extreme_tickers(all_data, thresholds)

    if not extreme and not failed:
        return "✅ No extreme tickers today. All holdings within normal ranges."

    table = build_market_table(extreme)
    briefing = get_briefing_from_claude(table, failed)
    return briefing

# ============================================================
#  Combined scan + news
# ============================================================
def run_combined():
    scan_result = run_full_scan()
    news_result = fetch_news()
    return f"{scan_result}\n\n───\n\n{news_result}"

# ============================================================
#  Claude call for extreme‑ticker analysis (safe JSON)
# ============================================================
def get_briefing_from_claude(market_data, failed_tickers, news_text=""):
    if not market_data:
        return "⚠️ No market data available today."

    failure_text = ""
    if failed_tickers:
        failure_text = "⚠️ The following tickers could not be fetched: " + ", ".join(failed_tickers)

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    prompt = f"""
You are a sharp, impartial financial analyst. Today's date is {datetime.now(timezone.utc).strftime('%B %d, %Y')}.

📊 **Watchlist Extremes**:
{market_data}

{failure_text}

For each extreme ticker, start the line with 🟢🟡🔴 according to its setup:
- 🟢 if it looks like a dip‑buy opportunity (oversold / strong reversal possible)
- 🟡 if signals are mixed or unclear
- 🔴 if it appears overheated or in a downtrend

Then write ONE sentence per ticker describing the situation and main risk.
After covering all tickers, ask ONE contrarian question.
Keep the whole response under 150 words. No explicit buy/sell orders.
"""

    data = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 600,
        "system": "You are a data-driven financial assistant. Provide factual observations, not recommendations.",
        "messages": [{"role": "user", "content": prompt}]
    }

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=data,
            timeout=30
        )
        if response.status_code == 200:
            result = safe_parse_json(response.text)
            logger.info("Claude stop_reason: %s", result.get('stop_reason', 'unknown'))
            return result["content"][0]["text"]
        else:
            return f"❌ Claude error: {response.status_code}"
    except Exception as e:
        logger.exception("get_briefing_from_claude failed")
        return f"❌ Claude API call failed: {str(e)}"


