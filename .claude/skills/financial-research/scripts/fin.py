#!/usr/bin/env python3
"""Market data for the financial-research skill. Python 3.10+, standard library only.

  fin.py move SYMBOL [--date YYYY-MM-DD] [--vs SYM,SYM] [--intraday]
  fin.py news "QUERY" [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--market us|sg|hk|jp|kr|tw|cn|in|crypto] [--local] [--symbol SYM]
  fin.py filings SYMBOL [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--forms 8-K,10-Q]
  fin.py profile SYMBOL [SYMBOL ...]
  fin.py binance-ai PAIR

Symbols are Yahoo Finance tickers: T, D05.SI, 0700.HK, 7203.T, 005930.KS, 2330.TW, 600519.SS, RELIANCE.NS.
Crypto pairs come from Binance: BTCUSDT or BTC/USDT (USDT, USDC and FDUSD quotes; others need the slash, ETH/BTC).
Behind a TLS-inspecting proxy, set SSL_CERT_FILE to the proxy's CA certificate.
SEC asks API users to identify themselves: set SEC_USER_AGENT="Your Name you@example.com".
"""
import argparse
import gzip
import html
import http.cookiejar
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
SEC_UA = os.environ.get("SEC_USER_AGENT", "financial-research-skill research@example.com")

# Google News locales are (hl, gl, ceid).
Market = namedtuple("Market", "benchmark tz news_en news_local")
MARKETS = {
    "us": Market("^GSPC", "America/New_York", ("en-US", "US", "US:en"), None),
    "sg": Market("^STI", "Asia/Singapore", ("en-SG", "SG", "SG:en"), None),
    "hk": Market("^HSI", "Asia/Hong_Kong", ("en-HK", "HK", "HK:en"), ("zh-HK", "HK", "HK:zh-Hant")),
    "jp": Market("^N225", "Asia/Tokyo", ("en-US", "US", "US:en"), ("ja", "JP", "JP:ja")),
    "kr": Market("^KS11", "Asia/Seoul", ("en-US", "US", "US:en"), ("ko", "KR", "KR:ko")),
    "tw": Market("^TWII", "Asia/Taipei", ("en-US", "US", "US:en"), ("zh-TW", "TW", "TW:zh-Hant")),
    "cn": Market("000001.SS", "Asia/Shanghai", ("en-US", "US", "US:en"), ("zh-CN", "CN", "CN:zh-Hans")),
    "in": Market("^NSEI", "Asia/Kolkata", ("en-IN", "IN", "IN:en"), None),
    "crypto": Market("BTCUSDT", "UTC", ("en-US", "US", "US:en"), None),
}
CRYPTO_QUOTES = ("USDT", "USDC", "FDUSD")
BINANCE_CATALOGS = {48: "listing", 161: "delisting", 157: "maintenance", 49: "news", 128: "airdrop"}
AI_SECTIONS = {"tldr": "TL;DR", "key_drivers": "Key drivers", "risk_assessment": "Risks"}
SUFFIX_MARKET = {"SI": "sg", "HK": "hk", "T": "jp", "KS": "kr", "KQ": "kr", "TW": "tw", "TWO": "tw",
                 "SS": "cn", "SZ": "cn", "NS": "in", "BO": "in"}
# Yahoo has no daily history for the CSI 300, ChiNext or TPEx indices, so China uses the exchange composites
# and Taiwan's OTC market an index ETF.
SUFFIX_BENCHMARK = {"KQ": "^KQ11", "SZ": "399001.SZ", "TWO": "006201.TWO"}
# Google News marks stories it only has a date for as midnight US Pacific.
GOOGLE_DATE_ONLY_TZ = ZoneInfo("America/Los_Angeles")
# Moomoo and Futu (its Chinese-language twin) republish Dow Jones, MT Newswires, Cailian and exchange filings with
# exact timestamps, so they lead; other outlets follow for independent confirmation. Their forum posts ("$DBS
# (D05.SG)$ ...") are opinion and their quote pages aren't news.
MOOMOO_SITES = "(site:moomoo.com OR site:futunn.com)"
NOT_MOOMOO_SITES = "-site:moomoo.com -site:futunn.com"
MOOMOO_NOT_NEWS = re.compile(r"^\$[^$]+\$|Share Price, News, Quotes")

ITEMS_8K = {
    "1.01": "Material agreement", "1.02": "Agreement terminated", "1.03": "Bankruptcy",
    "1.05": "Cybersecurity incident", "2.01": "Acquisition/disposal completed", "2.02": "Results of operations",
    "2.03": "New debt obligation", "2.05": "Exit/restructuring costs", "2.06": "Material impairment",
    "3.01": "Delisting notice", "3.02": "Unregistered equity sale", "4.01": "Auditor change",
    "4.02": "Prior financials unreliable", "5.01": "Change in control", "5.02": "Director/officer change",
    "5.03": "Bylaws/fiscal year change", "5.07": "Shareholder vote", "7.01": "Reg FD disclosure",
    "8.01": "Other events", "9.01": "Exhibits",
}
INSIDER_FORMS = {"3", "4", "5", "144", "3/A", "4/A", "5/A"}
ANALYST_ACTIONS = {"up": "upgrade", "down": "downgrade", "init": "initiate", "main": "maintain", "reit": "reiterate"}

Bar = namedtuple("Bar", "t o h l c v")
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
_crumb = None


def get(url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": BROWSER_UA, "Accept-Encoding": "gzip",
                                                          **(headers or {})})
    try:
        with _opener.open(req, timeout=30) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return body.decode(r.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e.reason):
            sys.exit("TLS certificate check failed. Behind a TLS-inspecting proxy? "
                     "Set SSL_CERT_FILE to the proxy's CA certificate and rerun.")
        raise


def get_json(url, **kw):
    return json.loads(get(url, **kw))


def sec_json(url):
    return get_json(url, headers={"User-Agent": SEC_UA})


def pct(x):
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def human(n):
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= size:
            return f"{n / size:.1f}{unit}"
    return f"{n:.0f}"


def num(x):
    """Prices: two decimals, or significant digits for sub-dollar crypto."""
    return f"{x:.2f}" if abs(x) >= 1 else f"{x:.4g}"


def crypto_pair(symbol):
    """BTCUSDT or BTC/USDT -> ("BTC", "USDT"); None for anything that isn't a Binance pair."""
    s = symbol.upper()
    if "/" in s:
        return tuple(s.split("/", 1))
    quote = next((q for q in CRYPTO_QUOTES if s.endswith(q) and len(s) > len(q) + 1), None)
    return (s[:-len(quote)], quote) if quote else None


def market_of(symbol):
    if crypto_pair(symbol):
        return "crypto"
    return SUFFIX_MARKET.get(symbol.rsplit(".", 1)[-1] if "." in symbol else "", "us")


def benchmark_of(symbol):
    pair = crypto_pair(symbol)
    if pair:
        return "ETHUSDT" if pair[0] == "BTC" else "BTCUSDT"
    return SUFFIX_BENCHMARK.get(symbol.rsplit(".", 1)[-1], MARKETS[market_of(symbol)].benchmark)


# --- Yahoo Finance ---------------------------------------------------------------------------

def chart(symbol, start, end, interval="1d", prepost=False):
    """Bars between two dates (UTC midnights), stamped in the exchange's own timezone."""
    p1, p2 = (int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) for d in (start, end))
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?period1={p1}"
           f"&period2={p2}&interval={interval}&events=div&includePrePost={str(prepost).lower()}")
    try:
        res = get_json(url)["chart"]
    except urllib.error.HTTPError as e:
        raise LookupError(f"Yahoo has no price data for {symbol} (HTTP {e.code}); check the ticker suffix.")
    if not res.get("result") or not res["result"][0].get("timestamp"):
        raise LookupError(f"Yahoo has no price data for {symbol} in {start}..{end}.")
    r = res["result"][0]
    tz = ZoneInfo(r["meta"]["exchangeTimezoneName"])
    q = r["indicators"]["quote"][0]
    bars = [Bar(datetime.fromtimestamp(t, tz), o, h, l, c, v or 0)
            for t, o, h, l, c, v in zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"], q["volume"])
            if c is not None]
    divs = {datetime.fromtimestamp(d["date"], tz).date(): d["amount"]
            for d in r.get("events", {}).get("dividends", {}).values()}
    return r["meta"], bars, divs


def daily(bars):
    """One bar per local date. Yahoo sometimes repeats the latest session, and for days a market was shut (a
    typhoon closure, say) it can insert a flat zero-volume bar that is not a real session."""
    return list({b.t.date(): b for b in bars if not (b.v == 0 and b.o == b.h == b.l == b.c)}.values())


def daily_returns(bars):
    return {b.t.date(): b.c / a.c - 1 for a, b in zip(bars, bars[1:])}


def yahoo_crumb():
    global _crumb
    if _crumb is None:
        try:
            get("https://fc.yahoo.com")  # answers 404 but sets the session cookie the crumb is tied to
        except urllib.error.HTTPError:
            pass
        _crumb = get("https://query1.finance.yahoo.com/v1/test/getcrumb")
    return _crumb


def quote_summary(symbol, modules):
    url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(symbol)}"
           f"?modules={','.join(modules)}&crumb={urllib.parse.quote(yahoo_crumb())}")
    try:
        res = get_json(url)["quoteSummary"]
    except urllib.error.HTTPError as e:
        raise LookupError(f"Yahoo has no company data for {symbol} (HTTP {e.code}).")
    if not res.get("result"):
        raise LookupError(f"Yahoo has no company data for {symbol}: {res.get('error')}")
    return res["result"][0]


# --- Binance -----------------------------------------------------------------------------------

def binance_klines(base, quote, start, end, interval="1d"):
    """Binance spot candles between two dates, shaped like chart()'s output: UTC days, volume in the quote
    currency. The market-data-only host also works where api.binance.com is geo-blocked."""
    symbol = base + quote
    t0, t1 = (int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000) for d in (start, end))
    rows = []
    while t0 < t1:
        try:
            batch = get_json(f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}"
                             f"&startTime={t0}&endTime={t1}&limit=1000")
        except urllib.error.HTTPError as e:
            raise LookupError(f"Binance has no spot pair {symbol} (HTTP {e.code}).")
        rows += batch
        if len(batch) < 1000:
            break
        t0 = batch[-1][6] + 1
    if not rows:
        raise LookupError(f"Binance has no {symbol} candles in {start}..{end}.")
    bars = [Bar(datetime.fromtimestamp(r[0] / 1000, timezone.utc), *map(float, r[1:5]), float(r[7])) for r in rows]
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    meta = {"exchangeTimezoneName": "UTC", "longName": f"{base}/{quote}", "fullExchangeName": "Binance spot",
            "currency": quote, "currentTradingPeriod": {"regular": {
                "start": today.timestamp(), "end": (today + timedelta(days=1)).timestamp()}}}
    return meta, bars, {}


def prices(symbol, start, end, interval="1d", prepost=False):
    pair = crypto_pair(symbol)
    return binance_klines(*pair, start, end, interval) if pair else chart(symbol, start, end, interval, prepost)


def show_derivatives(base, quote, day):
    """Funding and open interest on the matching Binance USDⓈ-M perpetual, when there is one."""
    symbol = base + quote
    t0 = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
    t1 = t0 + 86_400_000
    try:
        funding = get_json(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&startTime={t0}&endTime={t1}")
    except urllib.error.HTTPError:
        funding = []
    if not funding:
        print(f"Perpetual futures: no {symbol} perpetual on Binance (or it is unreachable from here).")
        return
    line = "Perpetual futures: funding per 8h " + " → ".join(f"{float(f['fundingRate']) * 100:+.4f}%" for f in funding)
    try:
        oi = get_json(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=1h"
                      f"&startTime={t0}&endTime={t1}&limit=30")
    except urllib.error.HTTPError:
        oi = []
    if len(oi) > 1:
        first, last = float(oi[0]["sumOpenInterestValue"]), float(oi[-1]["sumOpenInterestValue"])
        line += f" · open interest {human(first)} → {human(last)} {quote} ({pct(last / first - 1)})"
    else:
        line += " · open interest n/a (Binance keeps 30 days of history)"
    print(line)


def cmd_binance_ai(a):
    base, quote = crypto_pair(a.symbol) or (a.symbol.upper(), "USDT")
    # Undocumented endpoint behind the "<token> Insights" panel on binance.com trade pages. It answers 200 with
    # data=null when a pair has no report, so check the body rather than the status.
    d = get_json("https://www.binance.com/bapi/bigdata/v1/friendly/bigdata/search/ai-report/report",
                 headers={"Content-Type": "application/json"},
                 data=json.dumps({"token": base, "symbol": base + quote, "product": "web-spot", "lang": "en",
                                  "quoteToken": ""}).encode()).get("data")
    if not d or not d.get("modules"):
        raise LookupError(f"Binance has no AI report for {base}/{quote}.")
    at = datetime.fromtimestamp(d["reportMeta"]["reportAt"] / 1000, timezone.utc)
    print(f"Binance AI report for {base}/{quote} as of {at:%Y-%m-%d %H:%M} UTC. It describes that moment, and Binance"
          " says it could be inaccurate, so treat its claims as leads to check.")
    for m in d["modules"]:
        print(f"\n{AI_SECTIONS.get(m['type'], m['type'].replace('_', ' ').capitalize())}:")
        for p in m["points"]:
            print(p["content"])


# --- move ------------------------------------------------------------------------------------

def cmd_move(a):
    sym = a.symbol.upper()
    anchor = date.fromisoformat(a.date) if a.date else date.today()
    start, end = anchor - timedelta(days=400), anchor + timedelta(days=8)
    pair = crypto_pair(sym)
    meta, bars, divs = prices(sym, start, end)
    bars = daily(bars)
    days = [b.t.date() for b in bars]
    i = max((k for k, d in enumerate(days) if d <= anchor), default=0)
    if i == 0:
        raise LookupError(f"Not enough {sym} price history before {anchor}.")
    b, p, day = bars[i], bars[i - 1], days[i]
    tz = ZoneInfo(meta["exchangeTimezoneName"])
    rets = daily_returns(bars)
    chg = rets[day]
    reg = meta.get("currentTradingPeriod", {}).get("regular")
    live = bool(reg) and i == len(bars) - 1 and reg["start"] <= datetime.now(timezone.utc).timestamp() < reg["end"]

    print(f"{sym}  {meta.get('longName') or meta.get('shortName', '')}  {meta.get('fullExchangeName', '')}  "
          f"{meta.get('currency', '')}  (times {tz.key})")
    if a.date and day != anchor:
        print(f"No {sym} session on {anchor:%a %Y-%m-%d} (weekend, holiday, weather closure or halt);"
              " showing the previous session.")
    print(f"Session {day:%a %Y-%m-%d}: close {num(b.c)}  {pct(chg)} vs previous close {num(p.c)}"
          f"{'  (session in progress: live numbers)' if live else ''}")
    if pair:
        print(f"  range {num(b.l)}–{num(b.h)} (trades round the clock, so there is no opening gap)")
    elif b.o:
        print(f"  gap at open {pct(b.o / p.c - 1)} (open {num(b.o)}) · open→close {pct(b.c / b.o - 1)}"
              f" · range {num(b.l)}–{num(b.h)}")
    avg_v = statistics.mean(x.v for x in bars[max(0, i - 20):i]) or 1
    year = bars[max(0, i - 251):i + 1]
    print(f"  volume {human(b.v)}{' ' + meta['currency'] if pair else ''} = {b.v / avg_v:.1f}× its 20-session average"
          f" · 52-week range {num(min(x.l for x in year if x.l))}–{num(max(x.h for x in year if x.h))}")
    window = days[max(1, i - 120):i]
    typical = statistics.pstdev(rets[d] for d in window)
    print(f"  typical daily move (σ over {len(window)} sessions) {typical * 100:.2f}% → this move is "
          f"{abs(chg) / typical:.1f}σ")
    if day in divs:
        print(f"  EX-DIVIDEND today: {divs[day]:.4g} per share = {divs[day] / p.c * 100:.2f}% of the previous close "
              f"(a mechanical drop, not news)")

    bench = benchmark_of(sym)
    print(f"Same session date elsewhere ({day}):")
    for c in [bench] + [s.strip().upper() for s in a.vs.split(",") if s.strip()]:
        try:
            cr = daily_returns(daily(prices(c, start, end)[1]))
        except LookupError as e:
            print(f"  {c:<11} {e}")
            continue
        if day not in cr:
            print(f"  {c:<11} no session that day")
            continue
        line = f"  {c:<11} {pct(cr[day]):>8}"
        common = [d for d in window if d in cr]
        if c == bench and len(common) >= 30:
            xs, ys = [cr[d] for d in common], [rets[d] for d in common]
            beta, corr = statistics.covariance(xs, ys) / statistics.variance(xs), statistics.correlation(xs, ys)
            line += (f"   benchmark · {sym} beta {beta:.2f}, correlation {corr:.2f} → market-explained"
                     f" {pct(beta * cr[day])}, {'coin' if pair else 'stock'}-specific {pct(chg - beta * cr[day])}"
                     f"{' (weak link to the benchmark, so this split is rough)' if abs(corr) < 0.3 else ''}")
        print(line)

    print("Sessions around it:")
    for k in range(max(1, i - 5), min(len(bars), i + 4)):
        d = days[k]
        print(f" {'>' if k == i else ' '} {d:%a %Y-%m-%d}  {num(bars[k].c):>10}  {pct(rets[d]):>8}"
              f"  vol {bars[k].v / avg_v:.1f}×{'  ex-dividend' if d in divs else ''}")

    if pair:
        show_derivatives(*pair, day)
    else:
        show_analyst_actions(sym, day, tz)

    if a.intraday:
        show_intraday(sym, day, p.c, prepost=market_of(sym) == "us")


def show_analyst_actions(sym, day, tz):
    try:
        hist = quote_summary(sym, ["upgradeDowngradeHistory"]).get("upgradeDowngradeHistory", {}).get("history", [])
    except LookupError:
        hist = []
    week = [h for h in hist if day - timedelta(days=7) <= datetime.fromtimestamp(h["epochGradeDate"], tz).date() <= day]
    print(f"Analyst rating actions {day - timedelta(days=7)}..{day} (Yahoo; thin outside the US):"
          f"{'' if week else ' none found'}")
    for h in sorted(week, key=lambda h: h["epochGradeDate"]):
        target = f"  target {h.get('priorPriceTarget') or '?'}→{h['currentPriceTarget']}" \
            if h.get("currentPriceTarget") else ""
        print(f"  {datetime.fromtimestamp(h['epochGradeDate'], tz):%Y-%m-%d}  {h['firm']}: "
              f"{ANALYST_ACTIONS.get(h['action'], h['action'])} {h.get('fromGrade') or ''}→{h['toGrade']}{target}")


def show_intraday(sym, day, prev_close, prepost):
    if not crypto_pair(sym) and (date.today() - day).days > 58:
        print("Intraday: Yahoo only keeps 5-minute bars for about 60 days.")
        return
    meta, bars, _ = prices(sym, day - timedelta(days=1), day + timedelta(days=2), "5m", prepost)
    bars = [b for b in bars if b.t.date() == day]
    periods = meta.get("tradingPeriods") or []
    periods = [p for ps in (periods["regular"] if isinstance(periods, dict) else periods) for p in ps]
    regular = [b for b in bars if any(p["start"] <= b.t.timestamp() < p["end"] for p in periods)] or bars
    if not regular:
        print("Intraday: no 5-minute bars for that session.")
        return
    print(f"Intraday {day} (5-minute bars, {regular[0].t.tzname()}):")
    pre = [b for b in bars if b.t < regular[0].t]
    if pre:
        print(f"  pre-market {pre[0].t:%H:%M}–{pre[-1].t:%H:%M}: {num(pre[0].c)} → {num(pre[-1].c)}"
              f" ({pct(pre[-1].c / prev_close - 1)} vs previous close)")
    step = 3 if len(regular) > 150 else 1  # round-the-clock markets: every third hour
    path = [b for k, b in enumerate(regular) if k == 0 or (b.t.minute == 0 and b.t.hour % step == 0)] + [regular[-1]]
    print("  path " + " · ".join(f"{b.t:%H:%M} {num(b.c)}" for b in path))
    if len(regular) > 6:
        moves = [(regular[k + 6].c / regular[k].c - 1, regular[k].t, regular[k + 6].t) for k in range(len(regular) - 6)]
        lo, hi = min(moves), max(moves)
        print(f"  steepest 30-min fall {lo[1]:%H:%M}→{lo[2]:%H:%M} {pct(lo[0])}"
              f" · steepest 30-min rise {hi[1]:%H:%M}→{hi[2]:%H:%M} {pct(hi[0])}")
    post = [b for b in bars if b.t > regular[-1].t]
    if post:
        print(f"  after hours to {post[-1].t:%H:%M}: {num(post[-1].c)} ({pct(post[-1].c / regular[-1].c - 1)} vs close)")


# --- news ------------------------------------------------------------------------------------

def google_news(query, edition, tz):
    hl, gl, ceid = edition
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": hl, "gl": gl, "ceid": ceid})
    items = []
    for it in ET.fromstring(get(url)).iter("item"):
        src, title = it.findtext("source") or "", it.findtext("title") or ""
        if src and title.endswith(" - " + src):
            title = title[:-len(src) - 3]
        t = parsedate_to_datetime(it.findtext("pubDate"))
        exact = t.astimezone(GOOGLE_DATE_ONLY_TZ).time() != datetime.min.time()
        items.append((t.astimezone(tz), src, title, it.findtext("link"), exact))
    return items  # in Google's relevance order


def cmd_news(a):
    mkt = a.market or (market_of(a.symbol.upper()) if a.symbol else "us")
    m = MARKETS[mkt]
    edition = m.news_local if a.local and m.news_local else m.news_en
    tz = ZoneInfo(m.tz)
    since = until = None
    if a.since or a.until:
        until = date.fromisoformat(a.until) if a.until else date.today()
        since = date.fromisoformat(a.since) if a.since else until - timedelta(days=7)
        window = f" after:{since - timedelta(days=1)} before:{until + timedelta(days=1)}"
    else:
        window = " when:7d"
    # Keep the most relevant of each, then show them in time order.
    moomoo = [x for x in google_news(f"{a.query} {MOOMOO_SITES}{window}", edition, tz)
              if not MOOMOO_NOT_NEWS.search(x[2])][:a.n]
    others = google_news(f"{a.query} {NOT_MOOMOO_SITES}{window}", edition, tz)[:a.n]
    if a.symbol:
        pair = crypto_pair(a.symbol)
        others += [x for x in yahoo_news(f"{pair[0]}-USD" if pair else a.symbol.upper(), tz)
                   if not since or since - timedelta(days=1) <= x[0].date() <= until + timedelta(days=1)]
    span = f"{since}..{until}" if since else "last 7 days"
    print(f'News for "{a.query}" ({span}, Google News {edition[0]}{" + Yahoo" if a.symbol else ""}),'
          f" times {tz.key}, oldest first")
    if not all(x[4] for x in moomoo + others):
        print("--:-- means Google only gave a date (±1 day); read the article for its real publish time.")
    seen = set()
    for label, items in (("Moomoo / Futu", moomoo), ("Other outlets", others)):
        rows = []
        for x in sorted(items):
            key = re.sub(r"\W+", "", x[2].lower())
            if key not in seen:
                seen.add(key)
                rows.append(x)
        print(f"-- {label}: {len(rows)} headlines")
        for t, src, title, link, exact in rows:
            stamp = f"{t:%Y-%m-%d %H:%M}" if exact else f"{t.astimezone(GOOGLE_DATE_ONLY_TZ):%Y-%m-%d} --:--"
            print(f"{stamp}  {src}: {title}\n    {link}")


def yahoo_news(symbol, tz):
    data = get_json("https://query1.finance.yahoo.com/v1/finance/search?"
                    + urllib.parse.urlencode({"q": symbol, "quotesCount": 0, "newsCount": 20}))
    return [(datetime.fromtimestamp(n["providerPublishTime"], tz), n.get("publisher", ""), n["title"], n["link"], True)
            for n in data.get("news", []) if symbol in n.get("relatedTickers", [])]


# --- filings ---------------------------------------------------------------------------------

def cmd_filings(a):
    sym = a.symbol.upper()
    until = date.fromisoformat(a.until) if a.until else date.today()
    since = date.fromisoformat(a.since) if a.since else until - timedelta(days=30)
    mkt = market_of(sym)
    if mkt == "us":
        us_filings(sym, since, until, a.forms)
    elif mkt in ANNOUNCEMENTS:
        ANNOUNCEMENTS[mkt](sym, since, until)
    else:
        raise LookupError(f"No announcement feed for {sym}'s market; references/markets.md says where to look.")


def hkex_filings(sym, since, until):
    code = sym.split(".")[0].zfill(5)
    jsonp = get("https://www1.hkexnews.hk/search/prefix.do?callback=callback&lang=EN&type=A&market=SEHK&name=" + code)
    stocks = json.loads(jsonp[jsonp.index("(") + 1:jsonp.rindex(")")])["stockInfo"]
    stock_id = next((s["stockId"] for s in stocks if s["code"] == code), None)
    if stock_id is None:
        raise LookupError(f"HKEXnews has no listed stock {code}.")
    rows = json.loads(get_json(
        "https://www1.hkexnews.hk/search/titleSearchServlet.do?sortDir=0&sortByOptions=DateTime&category=0"
        f"&market=SEHK&stockId={stock_id}&documentType=-1&fromDate={since:%Y%m%d}&toDate={until:%Y%m%d}"
        "&title=&searchType=0&t1code=-2&t2Gcode=-2&t2code=-2&rowRange=3000&lang=E")["result"] or "null") or []
    print(f"HKEXnews announcements for {code}, {since}..{until}, times Asia/Hong_Kong"
          " (filings after the 16:00 close move the next session)")
    for r in sorted(rows, key=lambda r: datetime.strptime(r["DATE_TIME"], "%d/%m/%Y %H:%M")):
        kind = html.unescape(re.sub(r"<[^>]+>", " ", r["SHORT_TEXT"])).strip()
        print(f"{datetime.strptime(r['DATE_TIME'], '%d/%m/%Y %H:%M'):%Y-%m-%d %H:%M}  {html.unescape(r['TITLE'])}"
              f"  [{kind}]\n    https://www1.hkexnews.hk{r['FILE_LINK']}")


def sgx_filings(sym, since, until):
    raise LookupError(f"SGX announcements aren't covered. Use fin.py news (moomoo carries SGX-listed companies) and"
                      f" the company's investor-relations page for {sym}.")


def cninfo_filings(sym, since, until):
    code = sym.split(".")[0]
    hits = json.loads(get("https://www.cninfo.com.cn/new/information/topSearch/query",
                          data=urllib.parse.urlencode({"keyWord": code, "maxNum": 5}).encode()))
    org = next((h["orgId"] for h in hits if h.get("code") == code), None)
    if org is None:
        raise LookupError(f"CNINFO has no A-share code {code}.")
    rows, page = [], 1
    while True:
        d = json.loads(get("https://www.cninfo.com.cn/new/hisAnnouncement/query", data=urllib.parse.urlencode(
            {"stock": f"{code},{org}", "tabName": "fulltext", "pageSize": 30, "pageNum": page,
             "seDate": f"{since}~{until}"}).encode()))
        rows += d.get("announcements") or []
        if not d.get("hasMore"):
            break
        page += 1
    cst = ZoneInfo("Asia/Shanghai")
    print(f"CNINFO announcements for {code} ({hits[0].get('zwjc', '')}), {since}..{until}, times Asia/Shanghai."
          " After-close filings are usually stamped 00:00 the next day; the market reacts in the first session on or"
          " after that date.")
    for r in sorted(rows, key=lambda r: r["announcementTime"]):
        title = re.sub(r"<[^>]+>", "", r["announcementTitle"])
        print(f"{datetime.fromtimestamp(r['announcementTime'] / 1000, cst):%Y-%m-%d %H:%M}  {title}\n"
              f"    https://static.cninfo.com.cn/{r['adjunctUrl']}")


def nse_filings(sym, since, until):
    symbol = sym.split(".")[0]
    if symbol.isdigit():
        raise LookupError("That is a BSE scrip code; use the NSE symbol (e.g. RELIANCE.NS). BSE's own API blocks"
                          " scripts, but its corp-announcements page reads with tinyfish.")
    rows = get_json("https://www.nseindia.com/api/corporate-announcements?index=equities&" + urllib.parse.urlencode(
        {"symbol": symbol, "from_date": f"{since:%d-%m-%Y}", "to_date": f"{until:%d-%m-%Y}"}),
        headers={"Accept": "application/json"})
    print(f"NSE announcements for {symbol}, {since}..{until}, times Asia/Kolkata (session 09:15–15:30;"
          " results often land during trading hours)")
    for r in sorted(rows, key=lambda r: r["sort_date"]):
        print(f"{r['sort_date'][:16]}  {r['desc']}: {r['attchmntText']}\n    {r['attchmntFile']}")


def tdnet_filings(sym, since, until):
    code = sym.split(".")[0]
    oldest = date.today() - timedelta(days=30)
    if since < oldest:
        print(f"(TDnet keeps only about 31 days, so this starts at {oldest}. For older disclosures use"
              f" https://kabutan.jp/stock/news?code={code} or the company's investor-relations site.)")
        since = oldest
    page = get("https://www.release.tdnet.info/onsf/TDJFSearch/TDJFSearch", data=urllib.parse.urlencode(
        {"t0": f"{since:%Y%m%d}", "t1": f"{until:%Y%m%d}", "q": code, "m": "0"}).encode())
    rows = re.findall(r'class="time"[^>]*>([^<]+)<.*?class="code"[^>]*>([^<]+)<.*?class="title"[^>]*>'
                      r'<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>', page, re.S)
    print(f"TDnet disclosures for {code}, {since}..{until}, times Asia/Tokyo (results at 15:00 land during"
          " trading; 15:30 and later move the next session)")
    for t, c, href, title in sorted(rows):
        if c.startswith(code):
            print(f"{t.replace('/', '-')}  {html.unescape(title.strip())}\n    https://www.release.tdnet.info{href}")


def kind_filings(sym, since, until):
    code = sym.split(".")[0]
    page = get("https://kind.krx.co.kr/disclosure/details.do", data=urllib.parse.urlencode(
        {"method": "searchDetailsSub", "forward": "details_sub", "searchCodeType": "number",
         "repIsuSrtCd": "A" + code, "searchCorpName": code, "fromDate": str(since), "toDate": str(until),
         "currentPageSize": 100, "pageIndex": 1, "orderMode": 0, "orderStat": "D"}).encode())
    rows = re.findall(r'<td class="txc">(\d{4}-\d\d-\d\d \d\d:\d\d)</td>.*?openDisclsViewer\(\'(\d+)\'[^>]*?'
                      r'title=\'([^\']+)\'', page, re.S)
    print(f"KRX KIND disclosures for {code}, {since}..{until}, times Asia/Seoul (regular session 09:00–15:30;"
          f" after-hours trading runs to 20:00){'; 100-row cap hit, narrow the dates' if len(rows) >= 100 else ''}")
    for t, acpt, title in sorted(rows):
        print(f"{t}  {html.unescape(title)}\n    https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={acpt}")


def mops_filings(sym, since, until):
    code = sym.split(".")[0]
    rows, (y, m) = [], (since.year, since.month)
    while (y, m) <= (until.year, until.month):
        d = json.loads(get("https://mops.twse.com.tw/mops/api/t05st01", headers={"Content-Type": "application/json"},
                           data=json.dumps({"companyId": code, "year": str(y - 1911), "month": str(m),
                                            "firstDay": "", "lastDay": ""}).encode()))
        rows += (d.get("result") or {}).get("data") or []
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    print(f"MOPS material information for {code}, {since}..{until}, times Asia/Taipei (session 09:00–13:30)."
          " Full text: POST https://mops.twse.com.tw/mops/api/t05st01_detail with the JSON shown.")
    for _, _, roc_day, clock, title, detail in rows:
        ry, rm, rd = (int(x) for x in roc_day.split("/"))
        day = date(ry + 1911, rm, rd)
        if since <= day <= until:
            print(f"{day} {clock[:5]}  {title}\n    {json.dumps(detail['parameters'])}")


def binance_announcements(sym, since, until):
    base = crypto_pair(sym)[0]
    # Futures notices name the contract ("PREDICTUSDT"), so allow a quote-currency suffix.
    mentions = re.compile(rf"(?<![A-Z0-9]){re.escape(base)}(?:{'|'.join(CRYPTO_QUOTES)})?(?![A-Z0-9])")
    first = int(datetime(since.year, since.month, since.day, tzinfo=timezone.utc).timestamp() * 1000)
    last = first + ((until - since).days + 1) * 86_400_000
    rows, capped = [], []
    for catalog, kind in BINANCE_CATALOGS.items():
        for page in range(1, 6):
            articles = get_json("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1"
                                f"&catalogId={catalog}&pageNo={page}&pageSize=50")["data"]["catalogs"][0]["articles"]
            rows += [(x["releaseDate"], kind, x["title"], x["code"]) for x in articles
                     if first <= x["releaseDate"] < last and mentions.search(x["title"])]
            if len(articles) < 50 or articles[-1]["releaseDate"] < first:
                break
        else:
            capped.append(kind)
    print(f"Binance announcements mentioning {base}, {since}..{until}, times UTC"
          f"{' (only the latest 250 checked for: ' + ', '.join(capped) + ')' if capped else ''}")
    for ms, kind, title, code in sorted(rows):
        print(f"{datetime.fromtimestamp(ms / 1000, timezone.utc):%Y-%m-%d %H:%M}  [{kind}] {title}\n"
              f"    https://www.binance.com/en/support/announcement/detail/{code}")


ANNOUNCEMENTS = {"hk": hkex_filings, "sg": sgx_filings, "cn": cninfo_filings, "in": nse_filings,
                 "jp": tdnet_filings, "kr": kind_filings, "tw": mops_filings, "crypto": binance_announcements}


def sec_cik(ticker):
    for v in sec_json("https://www.sec.gov/files/company_tickers.json").values():
        if v["ticker"] == ticker:
            return v["cik_str"], v["title"]
    raise LookupError(f"{ticker} is not in the SEC ticker list (not SEC-registered, or delisted).")


def us_filings(sym, since, until, forms):
    cik, name = sec_cik(sym)
    sub = sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    blocks = [sub["filings"]["recent"]] + [
        sec_json(f"https://data.sec.gov/submissions/{f['name']}") for f in sub["filings"].get("files", [])
        if f["filingFrom"] <= str(until) and f["filingTo"] >= str(since)]
    et = ZoneInfo("America/New_York")
    rows, insider = [], 0
    for r in blocks:
        for k, form in enumerate(r["form"]):
            if not str(since) <= r["filingDate"][k] <= str(until) or (forms and form not in forms):
                continue
            if not forms and form in INSIDER_FORMS:
                insider += 1
                continue
            accepted = datetime.fromisoformat(r["acceptanceDateTime"][k].replace("Z", "+00:00")).astimezone(et)
            rows.append((accepted, form, r["items"][k], r["accessionNumber"][k].replace("-", ""),
                         r["primaryDocument"][k], r["primaryDocDescription"][k]))
    print(f"SEC filings for {sym}, {name} (CIK {cik}), {since}..{until}, times America/New_York")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}"
    for accepted, form, items, acc, doc, desc in sorted(rows):
        what = "; ".join(f"{x} {ITEMS_8K.get(x, '')}".strip() for x in items.split(",") if x) or desc
        print(f"{accepted:%Y-%m-%d %H:%M}  {form:<8} {what}\n    {base}/{acc}/{doc}")
        if form.startswith(("8-K", "6-K")):
            for f in sec_json(f"{base}/{acc}/index.json")["directory"]["item"]:
                if re.search(r"ex(hibit)?[-_]?99", f["name"], re.I):
                    print(f"    exhibit: {base}/{acc}/{f['name']}")
    if insider:
        print(f"({insider} insider filings on Forms 3/4/5/144 not shown; add --forms 4 to list them.)")


# --- profile ---------------------------------------------------------------------------------

PROFILE_ROWS = [
    ("Name", "price", "shortName"), ("Sector", "assetProfile", "sector"), ("Industry", "assetProfile", "industry"),
    ("Currency", "price", "currency"), ("Price", "price", "regularMarketPrice"), ("Market cap", "price", "marketCap"),
    ("52-week change", "defaultKeyStatistics", "52WeekChange"), ("P/E trailing", "summaryDetail", "trailingPE"),
    ("P/E forward", "summaryDetail", "forwardPE"), ("P/B", "defaultKeyStatistics", "priceToBook"),
    ("EV/EBITDA", "defaultKeyStatistics", "enterpriseToEbitda"), ("Dividend yield", "summaryDetail", "dividendYield"),
    ("Revenue growth y/y", "financialData", "revenueGrowth"),
    ("Earnings growth y/y", "financialData", "earningsGrowth"), ("Gross margin", "financialData", "grossMargins"),
    ("Operating margin", "financialData", "operatingMargins"), ("Net margin", "financialData", "profitMargins"),
    ("ROE", "financialData", "returnOnEquity"), ("Debt/equity %", "financialData", "debtToEquity"),
    ("Beta", "summaryDetail", "beta"), ("Analyst target, mean", "financialData", "targetMeanPrice"),
    ("Analysts", "financialData", "numberOfAnalystOpinions"), ("Consensus", "financialData", "recommendationKey"),
    ("Latest quarter", "defaultKeyStatistics", "mostRecentQuarter"),
    ("Ex-dividend date", "calendarEvents", "exDividendDate"),
]
PROFILE_MODULES = ["price", "assetProfile", "summaryDetail", "defaultKeyStatistics", "financialData",
                   "calendarEvents", "earningsHistory", "upgradeDowngradeHistory"]


def cmd_profile(a):
    syms = [s.upper() for s in a.symbols]
    data = {}
    for s in syms:
        if crypto_pair(s):
            print(f"{s} is a crypto pair; profile covers companies (use move, news and binance-ai).")
            data[s] = {}
            continue
        try:
            data[s] = quote_summary(s, PROFILE_MODULES)
        except LookupError as e:
            print(e)
            data[s] = {}

    def val(d, module, field):
        v = d.get(module, {}).get(field)
        return str(v.get("fmt", "") if isinstance(v, dict) else v or "") or "–"

    table = [["", *syms]] + [[label, *(val(data[s], m, f) for s in syms)] for label, m, f in PROFILE_ROWS]
    table.append(["Next earnings", *(" / ".join(e.get("fmt", "") for e in data[s].get("calendarEvents", {})
                                                 .get("earnings", {}).get("earningsDate", [])) or "–" for s in syms)])
    widths = [max(len(row[c][:28]) for row in table) for c in range(len(table[0]))]
    for row in table:
        print("  ".join(cell[:28].ljust(w) for cell, w in zip(row, widths)).rstrip())
    print("Yahoo data: ratios use the latest reported quarter or trailing twelve months; check key numbers"
          " against filings.")
    if len(syms) == 1 and data[syms[0]]:
        d = data[syms[0]]
        eps = d.get("earningsHistory", {}).get("history", [])
        if eps:
            print("EPS vs consensus:")
            for e in eps:
                print(f"  quarter ending {e.get('quarter', {}).get('fmt', '?')}: actual {e.get('epsActual', {}).get('fmt', '–')} vs estimate"
                      f" {e.get('epsEstimate', {}).get('fmt', '–')} (surprise {e.get('surprisePercent', {}).get('fmt', '–')})")
        acts = d.get("upgradeDowngradeHistory", {}).get("history", [])[:8]
        if acts:
            print("Latest analyst actions:")
            for h in acts:
                print(f"  {datetime.fromtimestamp(h['epochGradeDate'], timezone.utc):%Y-%m-%d}  {h['firm']}:"
                      f" {ANALYST_ACTIONS.get(h['action'], h['action'])} {h.get('fromGrade') or ''}→{h['toGrade']}"
                      f"{'  target ' + str(h['currentPriceTarget']) if h.get('currentPriceTarget') else ''}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("move", help="a session's move vs its market, peers and recent history")
    m.add_argument("symbol")
    m.add_argument("--date", help="session date YYYY-MM-DD in the exchange's local time, UTC for crypto"
                                  " (default: latest)")
    m.add_argument("--vs", default="", help="comma-separated peers, sector ETFs or ADRs to compare")
    m.add_argument("--intraday", action="store_true", help="5-minute path of the session (last ~60 days only)")
    n = sub.add_parser("news", help="headlines: moomoo/Futu first, then other outlets (Yahoo too with --symbol)")
    n.add_argument("query")
    n.add_argument("--from", dest="since", help="YYYY-MM-DD (default: last 7 days)")
    n.add_argument("--to", dest="until", help="YYYY-MM-DD")
    n.add_argument("--market", choices=MARKETS, help="news edition and timezone (default: from --symbol, else us)")
    n.add_argument("--local", action="store_true", help="local-language edition instead of English")
    n.add_argument("--symbol", help="also pull Yahoo Finance headlines tagged with this ticker")
    n.add_argument("-n", type=int, default=15, help="max Google headlines per block (default 15)")
    f = sub.add_parser("filings", help="SEC filings, exchange announcements, or Binance announcements for crypto")
    f.add_argument("symbol")
    f.add_argument("--from", dest="since", help="YYYY-MM-DD (default: 30 days before --to)")
    f.add_argument("--to", dest="until", help="YYYY-MM-DD (default: today)")
    f.add_argument("--forms", type=lambda s: set(s.upper().split(",")), help="SEC form types, e.g. 8-K,10-Q,4")
    p = sub.add_parser("profile", help="valuation, margins, estimates and analyst actions; several symbols compare")
    p.add_argument("symbols", nargs="+")
    b = sub.add_parser("binance-ai", help="Binance's AI report (TL;DR, drivers, risks) for a crypto pair")
    b.add_argument("symbol", help="BNBUSDT, BNB/USDT or just BNB")
    a = ap.parse_args()
    try:
        {"move": cmd_move, "news": cmd_news, "filings": cmd_filings, "profile": cmd_profile,
         "binance-ai": cmd_binance_ai}[a.cmd](a)
    except LookupError as e:
        sys.exit(str(e))
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} from {e.url}")


if __name__ == "__main__":
    main()
