#!/usr/bin/env python3
"""Market data for the financial-research skill. Python 3.10+, standard library only.

  fin.py move SYMBOL [--date YYYY-MM-DD] [--vs SYM,SYM] [--intraday]
  fin.py news "QUERY" [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--market us|sg|hk|jp|kr|tw|cn|in|crypto] [--local] [--symbol SYM]
  fin.py filings SYMBOL [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--forms 8-K,10-Q]
  fin.py profile SYMBOL [SYMBOL ...]
  fin.py binance-ai PAIR [--at "YYYY-MM-DD HH:MM"]
  fin.py odds "QUERY" [--date YYYY-MM-DD] [--market us|sg|...] [-n 4]

Symbols are Yahoo Finance tickers: T, D05.SI, 0700.HK, 7203.T, 005930.KS, 2330.TW, 600519.SS, RELIANCE.NS, ^GSPC.
Crypto pairs come from Binance: BTCUSDT or BTC/USDT (USDT, USDC and FDUSD quotes; others need the slash, ETH/BTC).
Stock prices come from moomoo (US, Hong Kong and Singapore, with an AppKey in MOOMOO_CONFIG), then CNBC, then Yahoo.
Behind a TLS-inspecting proxy, set SSL_CERT_FILE to the proxy's CA certificate.
SEC asks API users to identify themselves: set SEC_USER_AGENT="Your Name you@example.com".
"""
import argparse
import base64
import functools
import gzip
import hashlib
import html
import http.cookiejar
import json
import os
import re
import secrets
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

# Every request looks like Firefox opening a page, the same for every source: sites can be stricter with clients
# that don't look like a browser. Only gzip is offered because only gzip is decoded here.
BROWSER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:160.0) Gecko/20100101 Firefox/160.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Priority": "u=0, i",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}
SEC_UA = os.environ.get("SEC_USER_AGENT", "financial-research-skill research@example.com")

# Google News locales are (hl, gl, ceid). Hours are the regular session in local time, closing auction included.
Market = namedtuple("Market", "benchmark tz hours news_en news_local")
MARKETS = {
    "us": Market("^GSPC", "America/New_York", ("09:30", "16:00"), ("en-US", "US", "US:en"), None),
    "sg": Market("^STI", "Asia/Singapore", ("09:00", "17:06"), ("en-SG", "SG", "SG:en"), None),
    "hk": Market("^HSI", "Asia/Hong_Kong", ("09:30", "16:10"), ("en-HK", "HK", "HK:en"),
                 ("zh-HK", "HK", "HK:zh-Hant")),
    "jp": Market("^N225", "Asia/Tokyo", ("09:00", "15:30"), ("en-US", "US", "US:en"), ("ja", "JP", "JP:ja")),
    "kr": Market("^KS11", "Asia/Seoul", ("09:00", "15:30"), ("en-US", "US", "US:en"), ("ko", "KR", "KR:ko")),
    "tw": Market("^TWII", "Asia/Taipei", ("09:00", "13:30"), ("en-US", "US", "US:en"),
                 ("zh-TW", "TW", "TW:zh-Hant")),
    "cn": Market("000001.SS", "Asia/Shanghai", ("09:30", "15:00"), ("en-US", "US", "US:en"),
                 ("zh-CN", "CN", "CN:zh-Hans")),
    "in": Market("^NSEI", "Asia/Kolkata", ("09:15", "15:30"), ("en-IN", "IN", "IN:en"), None),
    "crypto": Market("BTCUSDT", "UTC", ("00:00", "24:00"), ("en-US", "US", "US:en"), None),
}
# Indices without a suffix that belong to a market other than the US.
INDEX_MARKET = {"^STI": "sg", "^HSI": "hk", "^N225": "jp", "^TOPX": "jp", "^KS11": "kr", "^KQ11": "kr", "^TWII": "tw",
                "^NSEI": "in"}
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
    req = urllib.request.Request(url, data=data, headers={**BROWSER, **(headers or {})})
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
    if symbol in INDEX_MARKET:
        return INDEX_MARKET[symbol]
    return SUFFIX_MARKET.get(symbol.rsplit(".", 1)[-1] if "." in symbol else "", "us")


def benchmark_of(symbol):
    pair = crypto_pair(symbol)
    if pair:
        return "ETHUSDT" if pair[0] == "BTC" else "BTCUSDT"
    bench = SUFFIX_BENCHMARK.get(symbol.rsplit(".", 1)[-1], MARKETS[market_of(symbol)].benchmark)
    return "^GSPC" if bench == symbol else bench  # an Asian index follows the US session before it


# --- prices ----------------------------------------------------------------------------------
# Bars stamped in the exchange's own timezone, from the first source that has them: Binance for crypto pairs;
# otherwise moomoo, CNBC, then Yahoo. A source returns (meta, bars, dividends), None when it doesn't cover the
# symbol, or raises with its reason.

def prices(symbol, start, end, interval="1d", prepost=False):
    """meta["source"] names the source that answered, with why any before it failed."""
    pair = crypto_pair(symbol)
    if pair:
        return binance_klines(*pair, start, end, interval)
    failed = []
    for name, source in (("moomoo", moomoo_bars), ("CNBC", cnbc_bars), ("Yahoo", chart)):
        try:
            got = source(symbol, start, end, interval, prepost)
        except (LookupError, OSError, ValueError, KeyError) as e:
            failed.append(f"{name}: {e}")
            continue
        if got:
            got[0]["source"] = name + (f" ({'; '.join(failed)})" if failed else "")
            return got
    raise LookupError(f"No price data for {symbol}: {'; '.join(failed) or 'no source covers it'}.")


# --- moomoo OpenAPI --------------------------------------------------------------------------
# moomoo's REST API (open.moomoo.com) gives international accounts US, Hong Kong and Singapore stock data for free:
# bars going back years (5-minute ones too, with US pre-market, after-hours and overnight trading), dividends and
# splits, US analyst actions, consensus, valuation and estimates. It needs an AppKey, set up as
# references/markets.md says, and every request is signed with the AppKey's Ed25519 key. The first time a symbol's
# history is read it takes one of the account's history slots (about 100 on a small account) for 7 days; when they
# run out, prices come from CNBC.

MOOMOO_CONFIG = os.environ.get("MOOMOO_CONFIG", os.path.expanduser("~/.config/financial-research/moomoo.json"))
MOOMOO_MARKETS = {"us": "US", "hk": "HK", "sg": "SG"}
MOOMOO_ERRORS = {-7: "unknown symbol", -8: "not supported", -10: "no data",
                 -12: "the history quota is used up (each new symbol holds a slot for 7 days)",
                 -13: "its quota service is down", -12006: "it refused the signature (an expired AppKey, or this"
                 " machine's clock more than 5 seconds off)", -12009: "the AppKey lacks permission for this"}
ED25519_PKCS8 = bytes.fromhex("302e020100300506032b657004220420")  # a PKCS#8 Ed25519 key is this, then the seed

# Ed25519 signing (RFC 8032), which the standard library lacks: point arithmetic in extended coordinates. Its
# signatures are deterministic, and these match OpenSSL's byte for byte.
ED_P = 2**255 - 19
ED_L = 2**252 + 27742317777372353535851937790883648493
ED_D = -121665 * pow(121666, ED_P - 2, ED_P) % ED_P
ED_X = 15112221349535400772501151409588531511454012693041857206046113283949847762202
ED_Y = 46316835694926478169428394003475163141307993866256225615783033603165251855960


def ed_add(a, b):
    (x1, y1, z1, t1), (x2, y2, z2, t2) = a, b
    p, q = (y1 - x1) * (y2 - x2) % ED_P, (y1 + x1) * (y2 + x2) % ED_P
    r, s = 2 * t1 * t2 * ED_D % ED_P, 2 * z1 * z2 % ED_P
    e, f, g, h = q - p, s - r, s + r, q + p
    return e * f % ED_P, g * h % ED_P, f * g % ED_P, e * h % ED_P


def ed_mul(n, point):
    acc = (0, 1, 1, 0)
    while n:
        if n & 1:
            acc = ed_add(acc, point)
        point, n = ed_add(point, point), n >> 1
    return acc


def ed_encode(point):
    zi = pow(point[2], ED_P - 2, ED_P)
    x, y = point[0] * zi % ED_P, point[1] * zi % ED_P
    return (y | (x & 1) << 255).to_bytes(32, "little")


def ed25519_sign(seed, msg):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little") & (2**254 - 8) | 2**254
    base = (ED_X, ED_Y, 1, ED_X * ED_Y % ED_P)
    public = ed_encode(ed_mul(a, base))
    r = int.from_bytes(hashlib.sha512(h[32:] + msg).digest(), "little") % ED_L
    big_r = ed_encode(ed_mul(r, base))
    k = int.from_bytes(hashlib.sha512(big_r + public + msg).digest(), "little") % ED_L
    return big_r + ((r + k * a) % ED_L).to_bytes(32, "little")


@functools.cache
def moomoo_key():
    """(AppKey, Ed25519 seed) from MOOMOO_CONFIG, None when there is no config, or why it can't be used."""
    try:
        with open(MOOMOO_CONFIG) as f:
            cfg = json.load(f)
        with open(os.path.join(os.path.dirname(MOOMOO_CONFIG), cfg["private_key"])) as f:
            der = base64.b64decode("".join(x for x in f.read().splitlines() if not x.startswith("-----")))
        expires = datetime.fromisoformat(cfg.get("expires") or "9999-12-31")
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError) as e:
        return f"can't read the AppKey config ({e})"
    if (expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)) < datetime.now(timezone.utc):
        return f"the AppKey expired on {expires:%Y-%m-%d}; make a new one at open.moomoo.com/dashboard"
    if len(der) != 48 or der[:16] != ED25519_PKCS8:
        return "the private key isn't an Ed25519 key in PKCS#8 PEM"
    return cfg["app_key"], der[16:]


def moomoo(path, query=None, body=None):
    """One signed moomoo OpenAPI call: (data, pagination). Rate limits are waited out twice; any other refusal is a
    LookupError with moomoo's reason."""
    key = moomoo_key()
    if not isinstance(key, tuple):
        raise LookupError(key or "no AppKey set up (references/markets.md says how)")
    qs = urllib.parse.urlencode(query or {})
    raw = b"" if body is None else json.dumps(body).encode()
    for attempt in range(3):
        stamp = str(int(time.time() * 1000))
        signed = f"{stamp}\n{'POST' if raw else 'GET'}\n{path}\n{qs}\n{hashlib.sha256(raw).hexdigest() if raw else ''}"
        headers = {"X-Api-Key": key[0], "X-Timestamp": stamp, "X-Nonce": secrets.token_hex(8),
                   "Authorization": base64.b64encode(ed25519_sign(key[1], signed.encode())).decode(),
                   "Accept": "application/json", "Content-Type": "application/json"}
        try:
            d = get_json(f"https://webapi.moomoo.com{path}{'?' + qs if qs else ''}", headers=headers, data=raw or None)
        except urllib.error.HTTPError as e:
            text = e.read()
            try:
                d = json.loads(gzip.decompress(text) if e.headers.get("Content-Encoding") == "gzip" else text)
            except ValueError:
                raise LookupError(f"HTTP {e.code}") from None
        code = d.get("ret_code", d.get("code"))
        if code == 0:
            return d.get("data") or {}, d.get("pagination") or {}
        if code in (-11, -12011) and attempt < 2:  # rate-limited, or this symbol's slot is still being reserved
            time.sleep(2)
            continue
        raise LookupError(MOOMOO_ERRORS.get(code) or f"{d.get('ret_msg') or d.get('message')} ({code})")


def moomoo_symbol(symbol):
    """US.NVDA, HK.00700 or SG.D05 for a Yahoo ticker; None elsewhere, and for indices (moomoo has no US ones)."""
    mkt = market_of(symbol)
    if mkt not in MOOMOO_MARKETS or symbol.startswith("^"):
        return None
    code = symbol.replace("-", ".") if mkt == "us" else symbol.rsplit(".", 1)[0]
    return f"{MOOMOO_MARKETS[mkt]}.{code.zfill(5) if mkt == 'hk' else code}"


def moomoo_klines(code, start, end, ktype, extended=0):
    """Unadjusted bars between two dates in the market's timezone, oldest first. Minute bars stop before `end`."""
    query = {"start": str(start), "end": str(end), "ktype": ktype, "autype": 0, "num": 370, "extended_time": extended}
    rows = []
    for _ in range(20):  # 20 pages of 370 bars; a request needing more is a mistake
        data, page = moomoo(f"/api/v1.0/quote/{code}/history-kline", query)
        # Each page reaches further back. A session in progress also lists its later bars, empty.
        rows = [r for r in data.get("kline_list") or [] if r.get("close") is not None] + rows
        if not page.get("has_more"):
            break
        token = page.get("next_page_token")
        query = {**query, "page_token": token} if token else {**query, "next_time": data["next_time"]}
    return rows


def moomoo_bars(symbol, start, end, interval, prepost):
    code = moomoo_symbol(symbol)
    if not code or moomoo_key() is None:
        return None
    tz = ZoneInfo(MARKETS[market_of(symbol)].tz)
    meta = {"exchangeTimezoneName": tz.key}
    if interval != "1d":
        rows = moomoo_klines(code, start, end, 6, 1 if code.startswith("US.") else 0)
        if code.startswith("US."):
            # extended_time=1 adds pre-market and after-hours, 2 the overnight session instead, so the bars both
            # return are the regular session, early closes included.
            night = moomoo_klines(code, start, end, 6, 2)
            meta["regular"] = {k // 1000 - 300 for k in {r["time_key"] for r in rows} & {r["time_key"] for r in night}}
            rows = sorted({r["time_key"]: r for r in rows + night}.values(), key=lambda r: r["time_key"])
        if not rows:
            raise LookupError(f"no 5-minute bars in {start}..{end}")
        # moomoo stamps a bar with its end, the other sources with its start.
        return meta, [Bar(datetime.fromtimestamp(r["time_key"] / 1000 - 300, tz), r["open"], r["high"], r["low"],
                          r["close"], r["volume"]) for r in rows], {}
    rows = moomoo_klines(code, start, end, 2)
    if not rows:
        raise LookupError(f"no bars in {start}..{end}")
    bars = [Bar(datetime.strptime(str(r["date"]), "%Y%m%d").replace(tzinfo=tz), r["open"], r["high"], r["low"],
                r["close"], r["volume"]) for r in rows]
    # Raw prices put on today's share basis across splits, as Yahoo's are. Dividends stay in the prices, as on the
    # exchange, and are listed for move to flag.
    try:
        events = moomoo(f"/api/v1.0/quote/{code}/corporate-actions/rehab", {"divi_mode": "exclude_divi"})[0]
    except LookupError:
        events = {}
    divs = {}
    for e in sorted(events.get("rehabs") or [], key=lambda e: e["ex_div_date"]):
        ex = date.fromisoformat(e["ex_div_date"])
        a, b = e.get("forward_adj_factorA", 1), e.get("forward_adj_factorB", 0)
        if (a, b) != (1, 0):
            bars = [Bar(x.t, *(a * p + b for p in x[1:5]), x.v / a) if x.t.date() < ex else x for x in bars]
            divs = {d: v * a for d, v in divs.items()}
        cash = (e.get("per_cash_div") or 0) + (e.get("special_dividend") or 0)
        if cash:
            divs[ex] = divs.get(ex, 0) + cash
    return {**meta, "longName": rows[-1].get("name"), **quote_meta(cnbc_quote(symbol))}, bars, divs


# --- CNBC ------------------------------------------------------------------------------------
# The chart and quote APIs behind cnbc.com (undocumented, keyless): daily and 5-minute bars for indices and for
# stocks in most markets, and a quote that names the security. US 5-minute bars include pre-market and after-hours.
# Closes are split-adjusted only, like Yahoo's. Daily bars for Asian stocks lag a session behind the quote, so the
# latest session comes from the quote. Not covered: most Japanese stocks, KOSDAQ and Taiwan's OTC market.

CNBC_INDEX = {"^GSPC": ".SPX", "^IXIC": ".IXIC", "^DJI": ".DJI", "^RUT": ".RUT", "^VIX": ".VIX", "^SOX": ".SOX",
              "^STI": ".STI", "^HSI": ".HSI", "^N225": ".N225", "^TOPX": ".TOPX", "^KS11": ".KS11", "^KQ11": ".KQ11",
              "^TWII": ".TWII",
              "000001.SS": ".SSEC", "399001.SZ": ".SZI", "^NSEI": ".NSEI"}
CNBC_SUFFIX = {"SI": "SG", "HK": "HK", "T": "JP", "KS": "KR", "TW": "TW", "SS": "CN", "SZ": "CN", "NS": "IN"}


def cnbc_symbol(symbol):
    if symbol.startswith("^") or symbol in CNBC_INDEX:
        return CNBC_INDEX.get(symbol)
    if market_of(symbol) == "us":
        return symbol.replace("-", ".")
    code, _, suffix = symbol.rpartition(".")
    return f"{code.lstrip('0') if suffix == 'HK' else code}-{CNBC_SUFFIX[suffix]}" if suffix in CNBC_SUFFIX else None


def cnbc_quote(symbol):
    """CNBC's quote for a ticker, {} when it has none."""
    sym = cnbc_symbol(symbol)
    try:
        q = get_json("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?"
                     + urllib.parse.urlencode({"symbols": sym, "requestMethod": "itv", "noform": 1, "partnerId": 2,
                                               "fund": 1, "exthrs": 1, "output": "json"})
                     )["FormattedQuoteResult"]["FormattedQuote"] if sym else {}
    except (OSError, ValueError, KeyError):
        return {}
    q = q[0] if isinstance(q, list) else q
    return q if q.get("code") == 0 else {}


def quote_meta(q):
    return {k: q[f] for k, f in (("longName", "name"), ("fullExchangeName", "exchange"), ("currency", "currencyCode"))
            if q.get(f)}


def cnbc_number(s):
    return float(str(s).replace(",", ""))


def cnbc_bars(symbol, start, end, interval, prepost):
    sym = cnbc_symbol(symbol)
    if not sym:
        return None
    d = get_json(f"https://ts-api.cnbc.com/harmony/app/bars/{urllib.parse.quote(sym)}"
                 f"/{'1D' if interval == '1d' else '5M'}/{start:%Y%m%d}000000/{end:%Y%m%d}000000/adjusted/EST5EDT.json")
    rows = (d.get("barData") or {}).get("priceBars") or []
    if not rows:
        raise LookupError(f"no bars for {sym}")
    tz = ZoneInfo(MARKETS[market_of(symbol)].tz)
    stamp = ((lambda r: datetime.strptime(r["tradeTime"][:8], "%Y%m%d").replace(tzinfo=tz)) if interval == "1d"
             else (lambda r: datetime.fromtimestamp(r["tradeTimeinMills"] / 1000, tz)))
    bars = [Bar(stamp(r), *(cnbc_number(r[k]) for k in ("open", "high", "low", "close")), cnbc_number(r["volume"] or 0))
            for r in rows]
    q = cnbc_quote(symbol)
    if interval == "1d" and q.get("last_time"):
        day = datetime.strptime(q["last_time"], "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(tz).date()
        try:
            latest = [cnbc_number(q[k]) for k in ("open", "high", "low", "last")] + [cnbc_number(q.get("volume") or 0)]
        except (KeyError, ValueError):
            latest = None
        if latest and bars[-1].t.date() < day <= end:
            bars.append(Bar(datetime.combine(day, datetime.min.time(), tz), *latest))
    return {"exchangeTimezoneName": tz.key, **quote_meta(q)}, bars, {}


# --- Yahoo Finance ---------------------------------------------------------------------------
# Yahoo answers most scripted requests with HTTP 429 now, so it comes last.

def yahoo_error(e):
    return {404: "doesn't know the ticker (HTTP 404); check its suffix",
            429: "refuses this machine's requests (HTTP 429), which says nothing about the ticker"}.get(
        e.code, f"answered HTTP {e.code}")


def chart(symbol, start, end, interval="1d", prepost=False):
    """Bars between two dates (UTC midnights), stamped in the exchange's own timezone."""
    p1, p2 = (int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) for d in (start, end))
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?period1={p1}"
           f"&period2={p2}&interval={interval}&events=div&includePrePost={str(prepost).lower()}")
    try:
        res = get_json(url)["chart"]
    except urllib.error.HTTPError as e:
        raise LookupError(yahoo_error(e))
    if not res.get("result") or not res["result"][0].get("timestamp"):
        raise LookupError(f"no bars in {start}..{end}")
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
    try:
        res = get_json(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(symbol)}"
                       f"?modules={','.join(modules)}&crumb={urllib.parse.quote(yahoo_crumb())}")["quoteSummary"]
    except urllib.error.HTTPError as e:
        raise LookupError(yahoo_error(e))
    if not res.get("result"):
        raise LookupError(f"no company data: {res.get('error')}")
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
    meta = {"exchangeTimezoneName": "UTC", "longName": f"{base}/{quote}", "fullExchangeName": "Binance spot",
            "currency": quote, "source": "Binance"}
    return meta, bars, {}


def futures(path):
    """Rows from a Binance USDⓈ-M futures endpoint, [] when there are none. Binance refuses some requests with
    HTTP 400 and others with an error object where the rows should be."""
    try:
        rows = get_json("https://fapi.binance.com" + path)
    except (urllib.error.URLError, ValueError):
        return []
    return rows if isinstance(rows, list) else []


def perp_stats(symbol, quote, t0, t1):
    """Funding, open interest and positioning on a Binance perpetual between two times (ms), as one line."""
    span = f"symbol={symbol}&startTime={t0}&endTime={t1}"
    parts = []
    funding = futures(f"/fapi/v1/fundingRate?{span}&limit=1000")
    if funding:
        rates = [f"{float(f['fundingRate']) * 100:+.4f}%" for f in funding]
        every = round((funding[1]["fundingTime"] - funding[0]["fundingTime"]) / 3_600_000) if len(funding) > 1 else 0
        parts.append(f"funding{f' every {every}h' if every else ''} "
                     + " → ".join(rates if len(rates) <= 4 else [rates[0], "…", rates[-1]]))
    oi = futures(f"/futures/data/openInterestHist?{span}&period=1h&limit=500")
    if len(oi) > 1:
        a, b = float(oi[0]["sumOpenInterestValue"]), float(oi[-1]["sumOpenInterestValue"])
        parts.append(f"open interest {human(a)} → {human(b)} {quote} ({pct(b / a - 1)})")
    # Shares of accounts with a net long position, and of top traders' position size that is long.
    for path, label in (("globalLongShortAccountRatio", "accounts long"),
                        ("topLongShortPositionRatio", "top traders' positions long")):
        rows = futures(f"/futures/data/{path}?{span}&period=1h&limit=500")
        if rows:
            parts.append(f"{label} {float(rows[0]['longAccount']):.0%} → {float(rows[-1]['longAccount']):.0%}")
    taker = futures(f"/futures/data/takerlongshortRatio?{span}&period=1h&limit=500")
    if taker:
        buy, sell = (sum(float(r[k]) for r in taker) for k in ("buyVol", "sellVol"))
        parts.append(f"takers bought {buy / (buy + sell):.0%} of volume")
    if parts and t1 < (time.time() - 30 * 86_400) * 1000:
        parts.append("(Binance keeps 30 days of open interest and positioning)")
    return " · ".join(parts)


def show_derivatives(base, quote, day):
    """Funding, open interest and positioning on the matching Binance USDⓈ-M perpetual, when there is one."""
    t0 = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
    line = perp_stats(base + quote, quote, t0, t0 + 86_400_000)
    print(f"Perpetual futures, that UTC day: {line}" if line else
          f"Perpetual futures: no {base + quote} perpetual on Binance (or it is unreachable from here).")


EQUITY_PERPS = {"us": "EQUITY", "hk": "HK_EQUITY"}


def stock_perp(sym):
    """Binance's perpetual on a US or Hong Kong stock (NVDAUSDT, HK0700USDT), or None. A ticker can also be a coin's
    (TUSDT is the Threshold token, not AT&T), so the contract's underlying type decides."""
    mkt = market_of(sym)
    if mkt not in EQUITY_PERPS or sym.startswith("^"):
        return None
    base = re.sub(r"[-.]", "", sym) if mkt == "us" else "HK" + sym.split(".")[0].lstrip("0").zfill(4)
    try:
        listed = get_json("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"]
    except (urllib.error.URLError, ValueError, KeyError):
        return None
    return next((s["symbol"] for s in listed if s.get("underlyingType") == EQUITY_PERPS[mkt]
                 and s.get("baseAsset") == base and s.get("status") == "TRADING"), None)


def show_stock_perp(perp, days, i, tz, hours):
    """A stock's perpetual trades round the clock, so it shows when a gap formed and what came after the close."""
    at = lambda d, hm: datetime.combine(d, datetime.strptime(hm, "%H:%M").time(), tz)
    ms = lambda t: int(t.timestamp() * 1000)
    now = datetime.now(tz)
    legs = [("previous close → open", at(days[i - 1], hours[1]), at(days[i], hours[0]))]
    close, nxt = at(days[i], hours[1]), at(days[i + 1], hours[0]) if i + 1 < len(days) else now
    if close < now:
        legs.append(("close → next open" if nxt < now else "close → now", close, min(nxt, now)))
    t0, t1 = legs[0][1], min(legs[-1][2], now)
    rows = futures(f"/fapi/v1/klines?symbol={perp}&interval=5m&startTime={ms(t0) - 300_000}&endTime={ms(t1)}"
                   "&limit=1500")
    if not rows:
        print(f"Binance perpetual {perp}: no trades {t0:%a %H:%M}–{t1:%a %H:%M}.")
        return
    path = [(datetime.fromtimestamp((r[0] + 300_000) / 1000, tz), float(r[4])) for r in rows]  # (bar end, close)
    price_at = lambda t: next((p for e, p in reversed(path) if e <= t), path[0][1])
    print(f"Binance perpetual {perp} (trades round the clock; its price runs a little off the stock's):")
    for label, a, b in legs:
        b = min(b, now)
        span = [x for x in path if a <= x[0] <= b]
        hour = max(((x[0], y[0], y[1] / x[1] - 1) for x, y in zip(span, span[12:])), key=lambda m: abs(m[2]),
                   default=None)
        print(f"  {label} ({a:%a %H:%M} → {b:%a %H:%M}): {num(price_at(a))} → {num(price_at(b))}"
              f" ({pct(price_at(b) / price_at(a) - 1)})"
              + (f" · steepest hour {hour[0]:%a %H:%M}→{hour[1]:%H:%M} {pct(hour[2])}" if hour else ""))
    stats = perp_stats(perp, "USDT", ms(t0), ms(t1))
    if stats:
        print(f"  {stats}")


def cmd_binance_ai(a):
    base, quote = crypto_pair(a.symbol) or (a.symbol.upper(), "USDT")
    when = datetime.fromisoformat(a.at) if a.at else datetime.now(timezone.utc)
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    # Undocumented endpoint behind the "<token> Insights" panel on binance.com trade pages. The report is rebuilt
    # about hourly, and `timestamp` picks the one current then, back about 90 days. A pair or time without a report
    # answers 200 with no modules, so check the body rather than the status.
    d = get_json("https://www.binance.com/bapi/bigdata/v3/friendly/bigdata/search/ai-report/report",
                 headers={"Content-Type": "application/json"},
                 data=json.dumps({"lang": "en", "token": base, "symbol": base + quote, "product": "web-spot",
                                  "timestamp": str(int(when.timestamp() * 1000)), "quoteToken": "", "version": "v4",
                                  "translateToken": None, "extParams": {"markdown": True}}).encode())
    report = ((d.get("data") or {}).get("report") or {}).get("original") or {}
    if not report.get("modules"):
        raise LookupError(f"Binance has no AI report for {base}/{quote}"
                          + (f" as of {when:%Y-%m-%d %H:%M} UTC; it keeps about 90 days." if a.at else "."))
    made = datetime.fromtimestamp(report["reportMeta"]["reportAt"] / 1000, timezone.utc)
    print(f"Binance AI report for {base}/{quote} as of {made:%Y-%m-%d %H:%M} UTC. It describes that moment, and"
          " Binance says it could be inaccurate, so treat its claims as leads to check.")
    for m in report["modules"]:
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
    hours, now = MARKETS[market_of(sym)].hours, datetime.now(tz)
    live = i == len(bars) - 1 and day == now.date() and hours[0] <= f"{now:%H:%M}" < hours[1]

    print(f"{sym}  {meta.get('longName') or meta.get('shortName', '')}  {meta.get('fullExchangeName', '')}  "
          f"{meta.get('currency', '')}  (times {tz.key}; prices from {meta['source']})")
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

    def returns(c):
        try:
            return daily_returns(daily(prices(c, start, end)[1]))
        except LookupError as e:
            return e

    others = [bench] + [s.strip().upper() for s in a.vs.split(",") if s.strip()]
    with ThreadPoolExecutor(4) as pool:
        fetched = list(pool.map(returns, others))
    for c, cr in zip(others, fetched):
        if isinstance(cr, LookupError):
            print(f"  {c:<11} {cr}")
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
        show_analyst_actions(sym, day)
        perp = stock_perp(sym)
        if perp:
            show_stock_perp(perp, days, i, tz, hours)

    if a.intraday:
        show_intraday(sym, day, p.c)


RATINGS = {1: "Sell", 2: "Hold", 3: "Buy"}


def analyst_actions(sym, since, until):
    """US analysts' notes between two dates from moomoo, oldest first, each with the same firm's note before it:
    (date, firm, rating, target, previous rating, previous target). Firms come most recently active first, so
    paging stops at a page whose last firm has been quiet since before `since`."""
    code = moomoo_symbol(sym)
    if not code or not code.startswith("US."):
        raise LookupError("moomoo tracks US analysts only; search the news for broker notes")
    acts, key = [], ""
    while True:
        data, page = moomoo(f"/api/v1.0/quote/{code}/research/rating-summary",
                            {"rating_dimension_type": 1, "limit": 20, "next_key": key})
        firms = [f for f in data.get("inst_rating_summary_list") or [] if f.get("rating_item_list")]
        for f in firms:
            info, notes = f.get("institution_info") or {}, f["rating_item_list"]  # newest first
            firm = info.get("institution_en_name") or info.get("institution_name") or "?"
            for n, before in zip(notes, notes[1:] + [None]):
                d = date.fromisoformat(n["recommendation_date_str"][:10])
                if since <= d <= until:
                    acts.append((d, firm, int(n["rating"]), n.get("target_price"),
                                 before and int(before["rating"]), before and before.get("target_price")))
        if (not page.get("has_more") or not page.get("next_key") or not firms
                or date.fromisoformat(firms[-1]["rating_item_list"][0]["recommendation_date_str"][:10]) < since):
            return sorted(acts)
        key = page["next_key"]


def rating_change(r, t, r0, t0):
    """'downgrade Buy→Hold, target 240→220' and the like."""
    if r0 is None:
        what = f"rates {RATINGS.get(r, r)} (its first note on record)"
    elif r != r0:
        what = f"{'upgrade' if r > r0 else 'downgrade'} {RATINGS.get(r0, r0)}→{RATINGS.get(r, r)}"
    else:
        what = f"keeps {RATINGS.get(r, r)}"
    if t and t0 and t != t0:
        return f"{what}, target {t0:g}→{t:g}"
    return f"{what}, target {t:g}" if t else what


def show_analyst_actions(sym, day):
    since = day - timedelta(days=7)
    try:
        acts = analyst_actions(sym, since, day)
    except LookupError as e:
        print(f"Analyst rating actions: {e}")
        return
    changed = [x for x in acts if x[4] is None or (x[2], x[3]) != (x[4], x[5])]
    kept = len(acts) - len(changed)
    notes = f"{kept} note{'' if kept == 1 else 's'}"
    print(f"Analyst rating actions {since}..{day} (moomoo):" + (
        " none found" if not acts else f" {notes}, none changing rating or target" if not changed else
        f" {notes} kept rating and target; changes:" if kept else ""))
    for d, firm, *ratings in changed:
        print(f"  {d}  {firm}: {rating_change(*ratings)}")


def show_intraday(sym, day, prev_close):
    us = market_of(sym) == "us"
    try:
        meta, bars, _ = prices(sym, day - timedelta(days=1), day + timedelta(days=2), "5m", prepost=us)
    except LookupError as e:
        print(f"Intraday: {e}")
        return
    tz = ZoneInfo(meta["exchangeTimezoneName"])
    # A US trading day runs from the overnight session the evening before to the one after it.
    start = datetime.combine(day, datetime.min.time(), tz) - timedelta(hours=4 if us else 0)
    bars = [b for b in bars if start <= b.t < start + timedelta(hours=32 if us else 24)]
    hours, starts = MARKETS[market_of(sym)].hours, meta.get("regular")
    regular = [b for b in bars if (int(b.t.timestamp()) in starts if starts is not None
                                   else hours[0] <= f"{b.t:%H:%M}" < hours[1])]
    if not regular:
        print("Intraday: no 5-minute bars for that session.")
        return
    print(f"Intraday {day} (5-minute bars, {regular[0].t.tzname()}, from {meta['source']}):")
    open_, close = regular[0], regular[-1]

    def span(label, part, ref, versus):
        if part:
            print(f"  {label} {part[0].t:%H:%M}–{part[-1].t + timedelta(minutes=5):%H:%M}: {num(part[0].o)} →"
                  f" {num(part[-1].c)} ({pct(part[-1].c / ref - 1)} vs {versus})")

    early = [b for b in bars if b.t < open_.t] if us else []  # elsewhere these are auctions
    span("overnight", [b for b in early if b.t.date() < day or b.t.hour < 4], prev_close, "previous close")
    span("pre-market", [b for b in early if b.t.date() == day and b.t.hour >= 4], prev_close, "previous close")
    step = 3 if len(regular) > 150 else 1  # round-the-clock markets: every third hour
    path = [b for k, b in enumerate(regular) if k == 0 or (b.t.minute == 0 and b.t.hour % step == 0)] + [close]
    print("  path " + " · ".join(f"{b.t:%H:%M} {num(b.c)}" for b in path))
    if len(regular) > 6:
        moves = [(regular[k + 6].c / regular[k].c - 1, regular[k].t, regular[k + 6].t) for k in range(len(regular) - 6)]
        lo, hi = min(moves), max(moves)
        print(f"  steepest 30-min fall {lo[1]:%H:%M}→{lo[2]:%H:%M} {pct(lo[0])}"
              f" · steepest 30-min rise {hi[1]:%H:%M}→{hi[2]:%H:%M} {pct(hi[0])}")
    late = [b for b in bars if b.t > close.t] if us else []
    span("after hours", [b for b in late if b.t.date() == day and b.t.hour < 20], close.c, "close")
    span("overnight", [b for b in late if b.t.date() > day or b.t.hour >= 20], close.c, "close")


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
    yahoo = ""
    if a.symbol:
        pair = crypto_pair(a.symbol)
        try:
            others += [x for x in yahoo_news(f"{pair[0]}-USD" if pair else a.symbol.upper(), tz)
                       if not since or since - timedelta(days=1) <= x[0].date() <= until + timedelta(days=1)]
            yahoo = " + Yahoo"
        except urllib.error.HTTPError as e:
            yahoo = f"; Yahoo headlines unavailable, HTTP {e.code}"
    span = f"{since}..{until}" if since else "last 7 days"
    print(f'News for "{a.query}" ({span}, Google News {edition[0]}{yahoo}), times {tz.key}, oldest first')
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
# moomoo's key indicators for the latest reported quarter, by the name it gives them.
MOOMOO_RATIOS = [("Gross margin", "Gross Margin"), ("Operating margin", "Operating Margin"),
                 ("Net margin", "Net Margin"), ("LT debt/equity", "Long-Term Debt to Equity Ratio")]
CONSENSUS = {5: "Strong buy", 4: "Buy", 3: "Hold", 2: "Underperform", 1: "Sell"}
PROFILE_ORDER = ["Name", "Sector", "Industry", "Currency", "Price", "Market cap", "52-week range", "52-week change",
                 "P/E trailing", "P/E forward", "P/E, industry average", "P/E percentile, 1 year", "P/B", "EV/EBITDA",
                 "Dividend yield", "Revenue growth y/y", "Earnings growth y/y", "Gross margin", "Operating margin",
                 "Net margin", "Margins from", "ROE", "Debt/equity %", "LT debt/equity", "Beta", "Consensus",
                 "Analysts", "Analyst target, mean", "Latest quarter", "Ex-dividend date", "Next earnings"]
PROFILE_NOTES = {
    "moomoo": "moomoo data, with CNBC's quote: price, valuation and dividend yield are current; ROE and debt/equity"
              " are CNBC's trailing twelve months and latest quarter; margins are the quarter shown.",
    "Yahoo": "Yahoo data: ratios use the latest reported quarter or trailing twelve months.",
}


def analysts(estimate):
    n = estimate.get("num_analysts")
    return f"{n or '?'} analyst{'' if n == 1 else 's'}"


def year_on_year(history):
    """The latest actual in moomoo's estimate history against the same period a year earlier."""
    if not history:
        return None
    now = history[0]
    then = next((h for h in history if h.get("fiscal_year") == now.get("fiscal_year", 0) - 1
                 and h.get("fiscal_quarter") == now.get("fiscal_quarter")), None)
    try:
        a, b = float(now["actual_value"]), float(then["actual_value"])
    except (TypeError, KeyError, ValueError):
        return None
    return pct(a / b - 1) if b > 0 else None


def moomoo_profile(sym):
    """Rows (label → value) and detail lines for one US, Hong Kong or Singapore stock. moomoo's snapshot leaves out
    Singapore, so CNBC's quote fills in the price and the basic ratios. A part that fails is left out."""
    code, key = moomoo_symbol(sym), moomoo_key()
    if not isinstance(key, tuple):
        raise LookupError(key or "no AppKey set up (references/markets.md says how)")
    if not code:
        raise LookupError("covers US, Hong Kong and Singapore stocks only")

    def part(path, query=None, body=None):
        try:
            return moomoo(f"/api/v1.0/quote/{path}", query, body)[0]
        except LookupError:
            return {}

    snap = (part("snapshot", body={"code_list": [code]}).get("snapshot_list") or [{}])[0]
    q = cnbc_quote(sym)
    if not snap.get("last_price") and not q.get("last"):
        raise LookupError(f"no quote for {code}")
    val = part(f"{code}/valuation/detail", {"valuation_type": 1, "interval_type": 3})
    trend, plate = val.get("trend") or {}, val.get("plate_distribution") or {}
    book = snap.get("pb_ratio") or (part(f"{code}/valuation/detail", {"valuation_type": 2, "interval_type": 3})
                                    .get("trend") or {}).get("current_value")
    eps, revenue = (part(f"{code}/financials/estimate", {"estimate_type": k, "hist_count": 5, "fut_count": 1})
                    for k in ("EPS_GAAP", "REVENUE"))
    report = (part(f"{code}/financials/statements", {"statement_type": 4, "limit": 1}).get("report_list") or [{}])[0]
    ratios = {x.get("display_name"): x.get("data") for x in report.get("item_list") or []}
    cons = part(f"{code}/research/analyst-consensus")
    rows = {
        "Name": snap.get("name") or q.get("name"), "Industry": plate.get("plate_name"),
        "Currency": q.get("currencyCode"),
        "Price": num(snap["last_price"]) if snap.get("last_price") else q.get("last"),
        "Market cap": human(snap["total_market_val"]) if snap.get("total_market_val") else q.get("mktcapView"),
        "52-week range": f"{num(snap['lowest52weeks_price'])}–{num(snap['highest52weeks_price'])}"
        if snap.get("highest52weeks_price") else f"{q['yrloprice']}–{q['yrhiprice']}" if q.get("yrhiprice") else None,
        "P/E trailing": snap.get("pe_ttm_ratio") or q.get("pe"), "P/E forward": trend.get("forward_value"),
        "P/E, industry average": plate.get("plate_average_value"),
        "P/E percentile, 1 year": f"{trend['valuation_percentile']:.0f}%" if "valuation_percentile" in trend else None,
        "P/B": book,
        "Dividend yield": f"{snap['dividend_ratio_ttm']:.2f}%" if snap.get("dividend_ratio_ttm") is not None
        else q.get("dividendyield"),
        "Revenue growth y/y": year_on_year(revenue.get("historical_estimates")),
        "Earnings growth y/y": year_on_year(eps.get("historical_estimates")),
        **{label: f"{ratios[name]:.1f}%" for label, name in MOOMOO_RATIOS
           if isinstance(ratios.get(name), (int, float))},
        "ROE": q.get("ROETTM") or (f"{ratios['ROE']:.1f}% (that quarter)" if "ROE" in ratios else None),
        "Debt/equity %": q.get("DEBTEQTYQ"), "Margins from": report.get("period_text"), "Beta": q.get("beta"),
        "Latest quarter": (eps.get("historical_estimates") or [{}])[0].get("period_str") or report.get("period_text"),
        "Consensus": CONSENSUS.get(cons.get("rating")) if cons.get("total") else None, "Analysts": cons.get("total"),
        "Analyst target, mean": f"{num(cons['average'])} (range {num(cons['lowest'])}–{num(cons['highest'])})"
        if cons.get("average") else None,
    }
    details = []
    for h in (eps.get("historical_estimates") or [])[:4]:
        try:
            surprise = f", surprise {pct(float(h['actual_value']) / float(h['consensus_mean']) - 1)}"
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            surprise = ""
        details.append(f"  {h.get('period_str')}: actual {h.get('actual_value') or '–'} vs estimate"
                       f" {h.get('consensus_mean') or '–'} ({analysts(h)}{surprise})")
    for h in (eps.get("future_estimates") or [])[:1]:
        details.append(f"  next, {h.get('period_str')}: estimate {h.get('consensus_mean') or '–'}"
                       f" ({analysts(h)})")
    if details:
        details.insert(0, f"EPS vs consensus ({eps.get('currency') or 'reporting currency'}):")
    if code.startswith("US."):
        try:
            changed = [x for x in analyst_actions(sym, date.today() - timedelta(days=30), date.today())
                       if x[4] is None or (x[2], x[3]) != (x[4], x[5])][-8:]
        except LookupError:
            changed = []
        details += ["Latest analyst changes, last 30 days:"] * bool(changed)
        details += [f"  {d}  {firm}: {rating_change(*ratings)}" for d, firm, *ratings in changed]
    return rows, details


def yahoo_profile(sym):
    d = quote_summary(sym, PROFILE_MODULES)

    def val(module, field):
        v = d.get(module, {}).get(field)
        return str(v.get("fmt", "") if isinstance(v, dict) else v or "") or None

    rows = {label: val(m, f) for label, m, f in PROFILE_ROWS}
    rows["Consensus"] = (rows["Consensus"] or "").replace("_", " ").capitalize() or None  # strong_buy
    rows["Next earnings"] = " / ".join(e.get("fmt", "") for e in d.get("calendarEvents", {}).get("earnings", {})
                                       .get("earningsDate", [])) or None
    details = []
    for e in d.get("earningsHistory", {}).get("history", []):
        details.append(f"  quarter ending {e.get('quarter', {}).get('fmt', '?')}: actual"
                       f" {e.get('epsActual', {}).get('fmt', '–')} vs estimate {e.get('epsEstimate', {}).get('fmt', '–')}"
                       f" (surprise {e.get('surprisePercent', {}).get('fmt', '–')})")
    if details:
        details.insert(0, "EPS vs consensus:")
    acts = d.get("upgradeDowngradeHistory", {}).get("history", [])[:8]
    details += ["Latest analyst actions:"] * bool(acts)
    for h in acts:
        details.append(f"  {datetime.fromtimestamp(h['epochGradeDate'], timezone.utc):%Y-%m-%d}  {h['firm']}:"
                       f" {ANALYST_ACTIONS.get(h['action'], h['action'])} {h.get('fromGrade') or ''}→{h['toGrade']}"
                       f"{'  target ' + str(h['currentPriceTarget']) if h.get('currentPriceTarget') else ''}")
    return rows, details


def cmd_profile(a):
    syms = [s.upper() for s in a.symbols]
    data, used = {}, {}
    for s in syms:
        if crypto_pair(s):
            print(f"{s} is a crypto pair; profile covers companies (use move, news and binance-ai).")
            continue
        failed = []
        for name, source in (("moomoo", moomoo_profile), ("Yahoo", yahoo_profile)):
            try:
                data[s], used[name] = source(s), True
                break
            except (LookupError, OSError, ValueError, KeyError) as e:
                failed.append(f"{name}: {e}")
        else:
            print(f"No company data for {s}: {'; '.join(failed)}.")
    cols = [s for s in syms if s in data]
    if not cols:
        return
    fmt = lambda v: "–" if v in (None, "") else f"{v:,.2f}" if isinstance(v, float) else str(v)
    labels = [label for label in PROFILE_ORDER if any(data[s][0].get(label) not in (None, "") for s in cols)]
    table = [["", *cols]] + [[label, *(fmt(data[s][0].get(label)) for s in cols)] for label in labels]
    widths = [max(len(row[c][:28]) for row in table) for c in range(len(table[0]))]
    for row in table:
        print("  ".join(cell[:28].ljust(w) for cell, w in zip(row, widths)).rstrip())
    print(" ".join(PROFILE_NOTES[name] for name in used) + " Check key numbers against filings.")
    if len(cols) == 1:
        print("\n".join(data[cols[0]][1]))


# --- prediction markets ----------------------------------------------------------------------
# Each venue becomes a list of events (title, url, contracts). A contract is one yes/no market as a dict whose prices
# are dollars per $1 payout, i.e. implied probabilities. depth is Kalshi open interest (contracts) or Polymarket
# order-book liquidity (USD); vol24 and vol (lifetime) pick the markets worth showing.

KALSHI = "https://api.elections.kalshi.com"
MAX_CONTRACTS = 8


def fnum(x):
    return None if x in (None, "") else float(x)


def iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def implied(bid, ask, last):
    """The mid-quote, or the last trade when the quote is one-sided or wider than 10¢ (Polymarket's own display rule).
    Last trades go stale: Kalshi once showed 83% for 'Fed above 5.50%' beside 23% for 'above 5.00%'."""
    if bid and ask and bid < ask < 1 and ask - bid <= 0.10:  # Kalshi shows a missing bid as 0 and ask as 1
        return (bid + ask) / 2
    return None if last is None else min(max(last, bid or 0), ask or 1)  # nobody pays above the ask or sells below the bid


def open_during(c, window):
    return (c["opened"] is None or c["opened"] < window[1]) and (c["closed"] is None or c["closed"] >= window[0])


def kalshi_history(series, ticker, t0, t1):
    d = get_json(f"{KALSHI}/trade-api/v2/series/{series}/markets/{ticker}/candlesticks"
                 f"?start_ts={t0}&end_ts={t1}&period_interval=60")
    path = [(c["end_period_ts"], implied(fnum(c["yes_bid"].get("close_dollars")), fnum(c["yes_ask"].get("close_dollars")),
                                         fnum(c["price"].get("close_dollars")))) for c in d.get("candlesticks") or []]
    return [(t, p) for t, p in path if p is not None]


def kalshi_contract(series, m):
    bid, ask, last = (fnum(m.get(k + "_dollars")) for k in ("yes_bid", "yes_ask", "last_price"))
    p = implied(bid, ask, last)
    prev = implied(*(fnum(m.get(k + "_dollars")) for k in ("previous_yes_bid", "previous_yes_ask", "previous_price")))
    return {"label": m.get("yes_sub_title") or m["title"], "order": fnum(m.get("floor_strike")), "p": p, "bid": bid,
            "ask": ask, "chg1d": None if p is None or prev is None else p - prev,
            "vol24": fnum(m.get("volume_24h_fp")), "depth": fnum(m.get("open_interest_fp")),
            "vol": fnum(m.get("volume_fp")) or 0, "opened": iso(m.get("open_time")), "closed": iso(m.get("close_time")),
            "result": m.get("result") or None,
            "history": lambda t0, t1: kalshi_history(series, m["ticker"], t0, t1)}


def kalshi_contracts(event_ticker):
    d = get_json(f"{KALSHI}/trade-api/v2/events/{event_ticker}?with_nested_markets=true")
    ev = d["event"]
    return [kalshi_contract(ev["series_ticker"], m) for m in d.get("markets") or ev.get("markets") or []]


def kalshi_events(query, window):
    """(title, url, first close, loader) per event. The full market data costs a call per event, so it loads later."""
    # The trade API has no text search, so use the one behind kalshi.com (undocumented). Without a status it returns
    # open events only; a past date also needs settled ones. (status=closed, for the hours between closing and
    # settling, returns mostly unrelated events.)
    found = {}
    for status in [None] + (["settled"] if window else []):
        q = {"query": query, "page_size": 20, **({"status": status} if status else {})}
        for e in get_json(f"{KALSHI}/v1/search/series?" + urllib.parse.urlencode(q))["current_page"]:
            if e.get("event_ticker"):
                found.setdefault(e["event_ticker"], e)
    for ticker, e in found.items():
        closes = [iso(m["close_ts"]) for m in e.get("markets") or [] if not window or
                  open_during({"opened": iso(m.get("open_ts")), "closed": iso(m.get("close_ts"))}, window)]
        if closes:
            yield (e["event_title"] + (f" ({e['event_subtitle']})" if e.get("event_subtitle") else ""),
                   f"https://kalshi.com/markets/{e['series_ticker'].lower()}", min(closes),
                   lambda t=ticker: kalshi_contracts(t))


def poly_history(token, t0, t1):
    d = get_json(f"https://clob.polymarket.com/prices-history?market={token}&startTs={t0}&endTs={t1}&fidelity=60")
    return [(x["t"], x["p"]) for x in d.get("history") or []]


def poly_contract(m):
    bid, ask, last = fnum(m.get("bestBid")), fnum(m.get("bestAsk")), fnum(m.get("lastTradePrice"))
    outcomes, payouts = json.loads(m.get("outcomes") or "[]"), json.loads(m.get("outcomePrices") or "[]")
    tokens = json.loads(m.get("clobTokenIds") or "[]")
    label = m.get("groupItemTitle") or m["question"]
    if outcomes and outcomes[0] != "Yes":  # e.g. Up/Down or two team names: the price is for the first outcome
        label += f" ({outcomes[0]})"
    return {"label": label, "order": fnum(m.get("groupItemThreshold")), "p": implied(bid, ask, last), "bid": bid,
            "ask": ask, "chg1d": fnum(m.get("oneDayPriceChange")), "vol24": fnum(m.get("volume24hr")),
            "depth": fnum(m.get("liquidityNum")), "vol": fnum(m.get("volumeNum")) or 0,
            "opened": iso(m.get("startDate")), "closed": iso(m.get("closedTime") or m.get("endDate")),
            "result": outcomes[payouts.index("1")] if m.get("closed") and "1" in payouts else None,
            "history": lambda t0, t1: poly_history(tokens[0], t0, t1) if tokens else []}


def poly_events(query, window):
    q = {"q": query, "limit_per_type": 20, **({} if window else {"events_status": "active"})}
    for e in get_json("https://gamma-api.polymarket.com/public-search?" + urllib.parse.urlencode(q)).get("events") or []:
        cs = [poly_contract(m) for m in e.get("markets") or [] if window or not m.get("closed")]
        yield e["title"], f"https://polymarket.com/event/{e['slug']}", iso(e.get("endDate")), lambda cs=cs: cs


def thin(c):
    """Why a price shouldn't be read as a forecast. A missing quote is normal near 0% or 100%."""
    bid, ask = c["bid"] or 0, c["ask"] or 1
    why = []
    if bid and ask < 1:
        if ask - bid > 0.05:
            why.append(f"{(ask - bid) * 100:.0f}¢ spread")
    elif c["p"] is None or 0.05 < c["p"] < 0.95:
        why.append("one-sided quote")
    if (c["depth"] or 0) < 1000:
        why.append("depth under 1K")
    return why


def day_move(c, window, tz):
    t0, t1 = (int(t.timestamp()) for t in window)
    now = int(datetime.now(timezone.utc).timestamp())
    day = f"{window[0]:%a %Y-%m-%d}"
    for attempt in (1, 2):
        try:
            hist = c["history"](t0 - 86_400, min(t1, now))
            break
        except urllib.error.URLError as e:
            if attempt == 1 and getattr(e, "code", None) == 429:  # Kalshi allows ~20 reads a second
                time.sleep(1)
                continue
            return f"{day}: no history ({getattr(e, 'reason', e)})"
    path = [x for x in hist if x[0] <= t0][-1:] + [x for x in hist if t0 < x[0] <= t1]
    if len(path) < 2:
        return f"{day}: no readings that day"
    (ta, pa), (tb, pb) = max(zip(path, path[1:]), key=lambda s: abs(s[1][1] - s[0][1]))
    hm = lambda t: f"{datetime.fromtimestamp(t, tz):%H:%M}"
    return (f"{day}: {path[0][1]:.1%} → {path[-1][1]:.1%} ({(path[-1][1] - path[0][1]) * 100:+.1f} pts)"
            f"{' so far' if t1 > now else ''}"
            f"{f' · largest step {hm(ta)}→{hm(tb)} {(pb - pa) * 100:+.1f} pts' if abs(pb - pa) >= 0.005 else ''}")


def cmd_odds(a):
    tz = ZoneInfo(MARKETS[a.market].tz)
    window = None
    if a.date:
        start = datetime.combine(date.fromisoformat(a.date), datetime.min.time(), tz)
        window = (start, start + timedelta(days=1))
    print(f'Prediction-market odds for "{a.query}", times {tz.key}. Prices are implied probabilities: the mid-quote,'
          " or the last trade when the quote is one-sided or wider than 10¢.\nTHIN marks prices to discount:"
          " a wide or one-sided quote, or little money behind it.")
    for venue, fetch, depth_name in (("Kalshi", kalshi_events, "open interest {}"),
                                     ("Polymarket", poly_events, "liquidity ${}")):
        events = []
        try:
            found = list(fetch(a.query, window))
            if window:  # the question resolving soonest after that day is usually the one being priced
                found.sort(key=lambda e: (e[2] or datetime.max.replace(tzinfo=timezone.utc)).astimezone(tz).date())
            for title, url, _, load in found:
                cs = [c for c in load() if c["vol"] and (not window or open_during(c, window))]
                if cs:
                    events.append((title, url, cs))
                if len(events) == a.n:
                    break
        except urllib.error.URLError as e:  # one venue down shouldn't hide the other
            print(f"\n-- {venue}: unreachable ({getattr(e, 'reason', e)})")
            continue
        print(f"\n-- {venue}: {len(events) or 'no'} matching event(s){' open that day' if window else ''}")
        for title, url, cs in events:
            print(f"{title}  {url}")
            # What trades today, then what traded most ever: lifetime volume alone surfaces long-dead outcomes.
            shown = sorted(sorted(cs, key=lambda c: (-(c["vol24"] or 0), -c["vol"]))[:MAX_CONTRACTS],
                           key=lambda c: (c["order"] is None, c["order"] or 0, -(c["p"] or 0)))
            with ThreadPoolExecutor(4) as pool:
                moves = list(pool.map(lambda c: day_move(c, window, tz), shown)) if window else []
            for k, c in enumerate(shown):
                if c["result"]:
                    line = f"settled: {c['result']}"
                elif c["p"] is None:
                    line = "no price"
                else:
                    quote = "/".join(f"{x * 100:g}" if x and x < 1 else "–" for x in (c["bid"], c["ask"]))
                    why = thin(c)
                    line = "  ".join(x for x in (
                        f"{'now ' if window else ''}{c['p']:>6.1%}", f"bid/ask {quote}¢",
                        f"1d {c['chg1d'] * 100:+.1f} pts" if c["chg1d"] is not None else "",
                        f"24h vol {human(c['vol24'])}" if c["vol24"] else "",
                        depth_name.format(human(c["depth"] or 0)), f"THIN: {', '.join(why)}" if why else "") if x)
                print(f"  {c['label']:<34} {line}")
                if window:
                    print(f"  {'':<34}   {moves[k]}")
            if len(cs) > len(shown):
                print(f"  (+{len(cs) - len(shown)} less-traded markets not shown)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("move", help="a session's move vs its market, peers and recent history")
    m.add_argument("symbol")
    m.add_argument("--date", help="session date YYYY-MM-DD in the exchange's local time, UTC for crypto"
                                  " (default: latest)")
    m.add_argument("--vs", default="", help="comma-separated peers, sector ETFs or ADRs to compare")
    m.add_argument("--intraday", action="store_true",
                   help="5-minute path of the session, with US pre-market, after-hours and overnight trading")
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
    b.add_argument("--at", help='"YYYY-MM-DD HH:MM" in UTC: the report current then (about 90 days back; default now)')
    o = sub.add_parser("odds", help="Kalshi and Polymarket odds on an event (Fed, CPI, tariffs, elections, a company)")
    o.add_argument("query", help='a few keywords, e.g. "fed rate", "cpi", "tariff", "shutdown", "tesla deliveries"')
    o.add_argument("--date", help="YYYY-MM-DD: how the odds moved that day, including markets since closed")
    o.add_argument("--market", choices=MARKETS, default="us", help="timezone for --date and times (default us)")
    o.add_argument("-n", type=int, default=4, help="max events per venue (default 4)")
    a = ap.parse_args()
    try:
        {"move": cmd_move, "news": cmd_news, "filings": cmd_filings, "profile": cmd_profile,
         "binance-ai": cmd_binance_ai, "odds": cmd_odds}[a.cmd](a)
    except LookupError as e:
        sys.exit(str(e))
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} from {e.url}")
    except (urllib.error.URLError, TimeoutError) as e:  # a source down or slow: say so, rerun later
        sys.exit(f"Network error: {getattr(e, 'reason', e)}")


if __name__ == "__main__":
    main()
