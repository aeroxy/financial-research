---
name: financial-research
description: Explain why a stock, ETF, index or crypto pair moved, and research listed companies, across the US, Asia (Singapore, Hong Kong, Japan, Korea, Taiwan, mainland China, India) and Binance crypto. Use this whenever someone asks why a share price or coin rose, fell, crashed, pumped, spiked or "is down X% today/on Friday", what happened to a ticker, token or company this week, what Binance's AI says about a pair, or wants company research such as an earnings recap, beat/miss against consensus, guidance changes, what a filing or exchange announcement says, analyst rating changes, or a valuation/peer comparison, even if they only give a company name or ticker and a vague time like "today" or "after earnings".
---

# Financial research

The goal is an answer a skeptical analyst would accept: numbers checked against market data, causes traced to the source that broke them, and timestamps that line up with the price action. Most of the craft is in not over-claiming. Headlines name a reason for every move, and many of those reasons are guesses written after the fact.

## Tools

`scripts/fin.py` in this skill's directory fetches market data with the Python standard library. Run `python3 <skill-dir>/scripts/fin.py <command> --help` for every option. Symbols are Yahoo Finance tickers (`T`, `D05.SI`, `0700.HK`, `7203.T`, `005930.KS`, `2330.TW`, `600519.SS`, `RELIANCE.NS`). Crypto pairs use Binance symbols (`BTCUSDT`, `BNB/USDT`). For other markets' suffixes, benchmarks, trading hours and disclosure sources, and the crypto specifics, see `references/markets.md`.

| Command | What it gives you |
|---|---|
| `move SYM --date D --vs P1,P2 [--intraday]` | The session in numbers: % change, gap at the open against the move during trading, volume against its 20-day average, size in standard deviations, a market vs stock-specific split, peers on the same date, surrounding sessions, ex-dividend flags and that week's analyst actions. `--intraday` adds the 5-minute path, including US pre-market (last ~60 days only). Crypto pairs: UTC days, BTC as the benchmark (ETH for BTC itself), and the Binance perpetual's funding and open-interest change. |
| `news "QUERY" --from D --to D [--symbol SYM] [--local]` | Headlines in exchange-local time, oldest first, in two blocks. **Moomoo / Futu first: the main news source.** It republishes Dow Jones, MT Newswires, Cailian and exchange filings with exact timestamps. **Other outlets** follow for independent confirmation, plus Yahoo's headlines when `--symbol` is given. `--local` switches to the local-language edition, where Futu's Chinese coverage is the richest for Hong Kong and China. |
| `filings SYM --from D --to D` | US: SEC filings with exact acceptance times, 8-K item names and exhibit links. Asia: exchange announcements where a feed is wired up (see `references/markets.md`). Crypto: Binance listing, delisting, maintenance, news and airdrop notices that mention the token. |
| `profile SYM [SYM ...]` | Valuation, growth, margins and consensus as a side-by-side table. With one symbol it adds EPS actual vs estimate and the latest analyst actions. Companies only. |
| `binance-ai PAIR` | Binance's own AI report on a crypto pair (TL;DR, key drivers, risks) with its timestamp, the same as the "Insights" panel on the pair's binance.com trade page. It covers only the latest window, and Binance says it may be inaccurate, so use its drivers as leads to verify, never as the answer on their own. |

Other tools for reading and searching:
- **Read an article or page:** `tinyfish fetch content get "<url>"` returns clean markdown with title, author and publish time, and it follows Google News redirect links. Moomoo and Futu articles (`moomoo.com/news/post/...`) read in full this way. Moomoo's stock and quote pages block scripts, so don't try those. If a page comes back empty or blocked, try `lightpanda fetch --dump markdown "<url>"`.
- **Paywalled originals (Bloomberg, WSJ, FT, Nikkei):** syndicated rewrites on Yahoo Finance, Morningstar (Dow Jones), Investing.com or MarketWatch usually carry the key facts. The chrome-devtools skill can read pages in the user's own logged-in Chrome, but ask before driving their browser.
- **Find the original report:** `tinyfish search query "<keywords>"`. Prefer named outlets over social posts and SEO content farms.
- **PDFs** (most Asian announcements): `curl -sL -o <scratch>/doc.pdf "<url>"`, then open with the Read tool, using `pages` for long documents.

If `fin.py` reports a TLS certificate error, the machine is behind a TLS-inspecting proxy. Set `SSL_CERT_FILE` to that proxy's CA certificate and rerun. The SEC asks callers to identify themselves via `SEC_USER_AGENT="Name email"`. The default value works, but a real contact is more polite.

## Explaining a move

**1. Pin down the session.** Turn "Friday" or "yesterday" into a date using today's date, in the exchange's local time. An Asian session runs while the US is asleep, so news from the previous US trading day lands before the Asian open. A US company's after-hours earnings show up in the next day's session. Crypto trades round the clock and Binance's days are UTC, so a Singapore "Friday" (UTC+8) spans two UTC candles; use `--intraday` to see the hours that matter.

**2. Measure it.**
```
fin.py move SYM --date D --vs PEER1,PEER2,SECTOR_ETF --intraday
```
Pick 2–4 comparisons yourself, because Yahoo's "similar stocks" are often unrelated. Use direct competitors listed in the same market and, in the US, the sector ETF (XLK tech, XLC communications, XLF financials, XLE energy, XLV health care, XLY consumer discretionary, XLP staples, XLI industrials, XLU utilities, XLB materials, XLRE real estate, SMH semiconductors). For dual-listed companies, add the other line (e.g. TSM vs 2330.TW, BABA vs 9988.HK). Then read the output as evidence:
- **Premise.** Does the move match what the user said? If it was 3% rather than 8%, or Thursday rather than Friday, say so up front and explain the real move.
- **Ex-dividend.** If the session was an ex-dividend date, that part of the drop is mechanical. Asian banks and REITs can go ex by 3–6% at once.
- **Scope.** Is it market-wide (benchmark moved about as much), sector-wide (peers moved too) or stock-specific? This decides which news to look for. The market/stock split relies on beta, so when the correlation line says the link is weak, lean on peers instead.
- **Timing.** A gap at the open means the news came out while the market was closed. A slide during trading means news during the session, and the 5-minute path shows roughly when.
- **Conviction.** Volume above about 2× its average signals real news. Big moves on thin volume are often drift, rebalancing or sympathy moves.
- **Context.** Earlier sessions often explain a later one (a drop after earnings two days earlier, a streak of losses, a bounce). So do the week's analyst target cuts.

**3. Gather candidate causes** from the previous close to the end of the session.
- Company: `fin.py filings`. Results releases, profit warnings, guidance, executive departures, deals, offerings and trading halts usually come out here before anywhere else.
- News: `fin.py news "<company name>" --from <previous session> --to <D> --symbol SYM`. Start with the moomoo/Futu block and confirm with the other block. Moomoo republishes other publishers, so when judging independence count the source it credits (Dow Jones, Cailian, MT Newswires). For Asian stocks, run it again with `--local` and the local name. Also search the ticker and the CEO's name when the company name is generic.
- Broker actions outside the US: Yahoo tracks US analyst actions only. For Asian stocks, run `fin.py news "<company> downgrade OR upgrade OR target price"` over the two sessions before the move as well as the day itself. A downgrade the day before often explains a weak open.
- Sector: if peers moved too, search the competitor, regulator or industry story (`fin.py news "<industry or rival>"`).
- Macro: if the benchmark moved more than ~1%, look at that day's macro news: central banks, inflation, jobs data, tariffs, geopolitics, and for Asia, the overnight US session.
- Crypto: compare the coin with BTC first; most altcoin days are BTC days. Then check:
  - Binance's notices (`fin.py filings PAIR`).
  - The futures line: funding and open interest show whether leverage drove the move.
  - `fin.py binance-ai PAIR` for Binance's own reading.
  - Coin-specific catalysts: token unlocks, listings on other exchanges, hacks or exploits, ETF flows, and protocol upgrades.
- "Why X stock fell today" explainers (Motley Fool, Barron's, Investopedia, CNBC's premarket and midday movers) are good leads because they name the catalyst. Trace each claim back to the original report, filing or analyst note before relying on it.

**4. Read the 2–4 most informative sources in full** with tinyfish. Note who reported what and when, and the numbers involved (deal size, guidance range, subscriber counts, price target).

**5. Attribute.** A cause is credible when:
1. it was public before the move (compare timestamps with the gap and the intraday path),
2. its scope matches the move's scope (a sector story should hit peers),
3. its size is plausible, and
4. at least two independent sources tie the move to it.

Keep three labels apart: **confirmed** (filing, company or regulator statement), **reported** ("according to Bloomberg", not confirmed by the parties) and **commentary** (analysts' or writers' interpretation). If nothing convincing turns up, say so. Then offer the remaining hypotheses (sympathy move, index rebalance, options expiry, follow-through from an earlier event) as hypotheses.

## Company research

For earnings recaps, questions about filings and announcements, and peer or valuation comparisons, read `references/company-research.md`. It covers where each kind of document lives in each market and which metrics matter by industry.

## Answer format

Start with the answer and give the evidence after it. For a move:

```
**<Company> (<TICKER>) <fell/rose> <x.x%> on <Day, Mon D> (<close> <ccy>, <n>× normal volume): <the cause in one clause>.**

<2–4 sentences: what happened and when, how the stock reacted (gap or slide), how much was market or sector
versus company-specific, and why investors cared.>

Timeline (<exchange> time)
- <Day HH:MM>: <event> ([source](url))
- ...

Market and peers: <benchmark ±x.x%; peers ±y.y%; one line on what that implies>
Confidence: <high / medium / low>: <timing, scope, independent sources; what is reported but unconfirmed>
```

Match the length to the question. A quick "why is X down today" during trading hours deserves a short answer, with the figures marked as live and the story marked as still developing. Write prices in their own currency, times in the exchange's timezone with a label, and dates with the weekday. Link every source you relied on.

## Pitfalls

- **Live data.** During a session, Yahoo's latest daily bar is live, and `move` says so.
- **Data vintage.** Fundamentals (`profile`) reflect the last reported quarter, which the table shows. Growth rates there can be distorted by one-off items, so check any number you lean on against the filing.
- **Mismatched sources.** A recap that quotes numbers from a different quarter or share class is a common error. Check the period and the security.
- **Date mismatches.** Aggregators sometimes stamp syndicated stories with the re-publish time. When timing matters, use the original outlet's timestamp.
- **Missing data.** A source that failed, timed out or sat behind a paywall is not evidence that nothing happened. Say which source you couldn't check.
- **Insider filings.** Form 4s are filed up to two business days after the trade, so a sale just before a drop may only show up later.
- **Citations.** Cite only sources you actually read and relied on, not every headline the search returned.
- **Recommendations.** Don't give one unless asked. If asked which looks cheaper or better, give the analytical comparison and its caveats, not a call.
