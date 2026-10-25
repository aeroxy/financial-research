# Markets

Recipes below were verified in October 2026. Sites change: if one breaks, say so in the answer and fall back to news and the company's investor-relations site. Don't present a failed lookup as "no announcements".

| Market | Yahoo ticker | `move` benchmark | Session (local time) | Daily limit | `filings` source |
|---|---|---|---|---|---|
| US | `T`, `BRK-B` | ^GSPC | 09:30–16:00 ET; pre 04:00, after-hours to 20:00 | none (5-minute volatility halts) | SEC EDGAR |
| Singapore | `D05.SI` | ^STI | 09:00–12:00, 13:00–17:00 | — | none; use `news` |
| Hong Kong | `0700.HK` | ^HSI | 09:30–12:00, 13:00–16:00, closing auction to ~16:10 | none | HKEXnews |
| Japan | `7203.T` | ^N225 | 09:00–11:30, 12:30–15:30 | yen bands by price | TDnet (last 31 days) |
| Korea | `005930.KS`, `247540.KQ` | ^KS11 / ^KQ11 | 09:00–15:30; after-hours 16:00–20:00 | ±30% | KRX KIND |
| Taiwan | `2330.TW`, `6488.TWO` | ^TWII / 006201.TWO | 09:00–13:30 | ±10% | MOPS |
| China A | `600519.SS`, `300750.SZ` | 000001.SS / 399001.SZ | 09:30–11:30, 13:00–15:00 | ±10% main board; ±20% ChiNext (300xxx) and STAR (688xxx) | CNINFO |
| India | `RELIANCE.NS` | ^NSEI | 09:15–15:30 | bands 2–20%; F&O stocks dynamic | NSE |
| Crypto (Binance) | `BTCUSDT`, `BNB/USDT`, `ETH/BTC` | BTCUSDT (ETHUSDT for BTC) | 24/7, days in UTC | none | Binance announcements |

## Across Asia

- **Moomoo / Futu coverage** (the first block of `fin.py news`) varies by market:
  - Strong: the US, plus Hong Kong and China through Futu in Chinese (`--local`).
  - Good: Singapore.
  - Partial: Japan, Korea and Taiwan.
  - Weak: India, where the other-outlets block does the work.
- **News language.** Search the local edition with the company's local name plus "share price" (股價 / 股价, 株価, 주가) via `fin.py news "<local name> 股價" --local`. Local outlets explain mid-cap moves that English wires skip. Use English for background and for wire coverage (Reuters, Bloomberg).
- **Dual listings.** Compare the other line with `--vs`: A+H shares (`300750.SZ` / `3750.HK`), ADRs (`TSM` / `2330.TW`, `BABA` / `9988.HK`). A US line trades overnight, so it often moves first.
- **Overnight read-through.** An Asian session follows the previous US session. Chip names track the US semiconductor ETF `SMH` and Nvidia, and exporters track the yen or won.
- **Lunch breaks.** Singapore, Hong Kong, Japan and China pause at midday, and Yahoo's 5-minute data shows an empty gap. A filing at 12:00 (common in Hong Kong) moves the afternoon session.
- **Google News.** It often gives only a date for Asian stories (`--:--` in `fin.py news`). Open the article for the real time. When tinyfish on a Google News link returns only a headline, fetch the `final_url` it reports.

## US

- **Filings:** `fin.py filings`; more detail in `company-research.md`. Earnings 8-Ks usually land before 09:30 or after 16:00 ET.
- **Press releases** that aren't filed appear on the company's newsroom and on Business Wire, PR Newswire and GlobeNewswire.
- **Readable with tinyfish:** Yahoo Finance, Motley Fool (also free call transcripts), CNBC, Investor's Business Daily, Forbes, Morningstar (Dow Jones wire).
- **Mostly paywalled:** Bloomberg, WSJ, Barron's and FT. Their scoops get rewritten on the readable sites within hours.
- **Calendars without a key:** the Nasdaq API has `https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD` and `/api/analyst/<T>/ratings`. Send a browser User-Agent plus `Origin: https://www.nasdaq.com` and `Referer: https://www.nasdaq.com/`. Rating data is often sparse.

## Singapore (SGX)

- **Announcements.** Not covered: SGX's site blocks scripts, and its announcement feed adds little beyond what moomoo, Business Times and the company's investor-relations page carry.
- **Reporting.** Half-year results are mandatory; many large caps add quarterly business updates. DBS releases results around 06:30, before the open. Most other filings arrive after 17:00 and move the next session.
- **Ex-dividend days** are frequent price movers for banks and REITs.
- **News:** `--market sg`. Query "DBS Group" or "DBS shares" rather than bare "DBS".
  - Business Times: free wraps read in full; premium stories come back truncated.
  - The Edge Singapore blocks fetchers.
  - Straits Times and CNA are untested.

## Hong Kong (HKEX)

- **Announcements.** `fin.py filings` (HKEXnews, English titles, PDF links). Hong Kong runs on a different filing clock:
  - Most filings land 16:15–23:00 and move the next session; some come at 12:00, during lunch.
  - "Date of Board Meeting" notices pre-announce results dates.
  - "Trading halt" notices appear intraday.
- **Half-day sessions** (morning only) on the eves of Christmas, New Year and Lunar New Year. Holidays differ from Singapore's and the mainland's.
- **News.** Chinese coverage is much better for move explanations. `fin.py news "騰訊 股價" --market hk --local` leads with Futu (富途牛牛), which covers southbound flows, results-call transcripts and move write-ups, then reaches HKET, HKEJ, Now 財經 and 星島. The Standard reads in full; SCMP is often truncated.

## Japan

- **Announcements.** `fin.py filings` uses TDnet, which keeps about 31 days.
  - Older disclosures: `https://kabutan.jp/stock/news?code=<code>` (readable with curl and tinyfish) or the company's investor-relations site.
  - English TDnet: POST `https://www.release.tdnet.info/onsf/TDJFSearch_e/TDJFSearch_e` with `t0`, `t1`, `q`, `p=1`.
  - EDINET's API needs a free key, and its web search needs a browser.
- **Results timing.** 決算短信 (tanshin) mostly come out at 15:30, after the close, but 13:00–15:00 releases land during the session. Most fiscal years end in March.
- **当社に関する一部報道について** ("regarding certain press reports about the company") is the company responding to a news story. It is a strong clue when explaining a move.
- **Benchmarks.** Yahoo has no TOPIX index; add the TOPIX ETF `1306.T` with `--vs`.
- **Price limits** are yen bands by price (e.g. ±¥700 for stocks priced ¥3,000–5,000) and widen after consecutive limit days.
- **News:** `fin.py news "トヨタ 株価" --market jp --local`. Kabutan (株探) reads in full; Nikkei Asia is paywalled.

## Korea

- **Announcements.**
  - `fin.py filings` uses KRX KIND, with timestamps.
  - Full text: open the KIND viewer link, or use DART (`dart.fss.or.kr`).
  - English filings: `englishdart.fss.or.kr`.
  - 잠정실적 = preliminary results. Samsung publishes them about a week after quarter end, before the open.
- **Extended trading.** KRX after-hours runs 16:00–20:00, and the Nextrade venue trades in the evening. Evening news moves prices before the next regular open, so the "gap" may have happened the night before.
- **Yahoo's intraday bars** for `.KS`/`.KQ` stop at 15:00. The daily close is correct.
- **Market-wide stress:** look for KRX "sidecar" notices and widened futures limits on big index drops.
- **News:** `fin.py news "삼성전자 주가" --market kr --local`. Yonhap and Korea Herald read in full from direct article URLs. Maeil Business, Hankyung and KED Global (English) are worth checking.

## Taiwan

- **Announcements.** `fin.py filings` uses MOPS material information. Full text: POST the JSON parameters it prints to `https://mops.twse.com.tw/mops/api/t05st01_detail`. Financial-report PDFs come from `https://doc.twse.com.tw/server-java/t57sb01` (step=1, then step=9).
- **Monthly revenue** comes out by the 10th of each month, and a revenue surprise can move a stock on its own. TSMC reports after the close, so its ADR trades first in New York.
- **Holidays:** TWSE's calendar is `https://www.twse.com.tw/rwd/en/holidaySchedule/holidaySchedule?response=json`. Closed Mon 2026-10-26.
- **News:** `fin.py news "台積電 股價" --market tw --local`. Focus Taiwan reads in full; DigiTimes gives teasers only. English "TSMC" searches are flooded with ADR filler.

## China A-shares

- **Announcements.** `fin.py filings` uses CNINFO, which covers both exchanges and links PDFs.
  - After-close filings are stamped 00:00 the next day; the reaction comes in the first session on or after that date.
  - Exact submission times: Shenzhen's `https://www.szse.cn/api/disc/announcement/annList` (POST JSON with `seDate`, `stock`, `channelCode: ["listedNotice_disc"]`), or Shanghai's `query.sse.com.cn` bulletin search, which needs `Referer: https://www.sse.com.cn/`.
- **Benchmarks.** Yahoo's CSI 300, ChiNext and STAR indices lack daily history. Use the ETFs `510300.SS` (CSI 300), `159915.SZ` (ChiNext) and `588000.SS` (STAR 50) with `--vs`.
- **Hitting the limit.** A stock at its limit (涨停 / 跌停) can't trade further that day, so the move continues the next session.
- **Abnormal-volatility notices.** A cumulative ±20% move over three days (±30% on ChiNext and STAR) forces a 异常波动公告.
- **Deadlines:** Q1 and annual by 30 April, half-year by 31 August, Q3 by 31 October.
- **News:** `fin.py news "宁德时代 股价" --market cn --local` is much deeper than English, led by Futu. 财联社 (cls.cn) reads in full. Other outlets: 证券时报, 第一财经, 21世纪经济报道, plus Reuters, Bloomberg and SCMP in English. Caixin Global is paywalled; CnEVPost is good for EV and battery stocks.

## India

- **Prices.** Use `.NS` tickers. Yahoo's `.BO` history is broken for many large caps.
- **Announcements.** `fin.py filings` uses NSE.
  - BSE's API blocks scripts; its page `https://www.bseindia.com/stock-share-price/x/x/<scrip code>/corp-announcements/` reads with tinyfish.
  - Other NSE endpoints, same browser User-Agent: `/api/corporate-board-meetings?index=equities&symbol=` (results dates), `/api/corporates-corporateActions?index=equities&symbol=` (ex-dates), `/api/fiidiiTradeReact` (daily foreign and domestic institutional flows).
- **Results** often come out during trading hours, so compare the filing time with the intraday path. Fiscal years run April–March. With T+1 settlement, the ex-date equals the record date.
- **F&O ban list** (stocks barred from new derivative positions): `https://nsearchives.nseindia.com/content/fo/fo_secban.csv`.
- **News:** `--market in`. Moomoo is thin here, so rely on the other-outlets block. Economic Times reads in full.

## Crypto (Binance)

- **Prices.** Binance spot candles come from its market-data host (`data-api.binance.vision`, no key, not geo-blocked). Days are UTC and volume is in the quote currency. Pairs quoted in USDT, USDC or FDUSD can be written `BTCUSDT`; other quotes need a slash (`ETH/BTC`).
- **Leverage.** `move` adds the USDⓈ-M perpetual's funding rate (per 8 hours; positive means longs pay shorts) and the change in open interest over the day. Binance keeps 30 days of open-interest history. How to read the combinations:
  - Price down, open interest down: longs closed or were liquidated.
  - Price down, open interest up: new shorts.
  - Price up, open interest up, funding high: leveraged longs piling in, which is fragile.

  Liquidation totals aren't in Binance's public API. Aggregators such as CoinGlass report them, but may need a browser.
- **Announcements.** `filings` searches Binance's listing, delisting, maintenance, news and airdrop notices for the token, including futures contract names like `PREDICTUSDT`. For small coins, a listing or delisting notice is often the whole story.
- **AI report.** `binance-ai` calls the undocumented endpoint behind the trade page's "Insights" panel: `POST https://www.binance.com/bapi/bigdata/v1/friendly/bigdata/search/ai-report/report` with `{"token": "BNB", "symbol": "BNBUSDT", "product": "web-spot", "lang": "en"}`.
  - The report refreshes roughly hourly, and only the latest one is served.
  - For a move days ago, it describes later conditions.
  - When there's no report it returns `data: null` with HTTP 200.
  - If the endpoint stops working, the panel is still visible on `https://www.binance.com/en/trade/<BASE>_<QUOTE>?type=spot`.
- **News.** Moomoo covers the large coins. CoinDesk, The Block, Decrypt and Cointelegraph appear in the other-outlets block. On macro-driven days compare with the Nasdaq (`--vs ^IXIC`). Dates only line up roughly, because the US session ends at 20:00–21:00 UTC. Moneycontrol is partial through tinyfish; plain curl has the full text in the page's JSON-LD `articleBody`. Also Mint, Business Standard and Reuters.
