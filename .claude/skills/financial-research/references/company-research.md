# Company research

Three common requests: an earnings recap, a question about a filing or announcement, and a peer or valuation comparison. They share a habit: go to the primary document, quote it precisely, and say which period it covers.

## Earnings recap

**Collect**
1. **The release.** US: `fin.py filings SYM --forms 8-K` and find the item 2.02 filing. Exhibit 99.1 is the press release; 99.2/99.3 are usually the investor briefing or slides. Asia: the results announcement on the exchange (see `markets.md`) plus the slides on the company's investor-relations site.
2. **The full report.** The 10-Q (or 10-K for Q4) is filed the same day or within weeks; its MD&A explains the drivers. In Asia, the interim or annual report follows the results announcement.
3. **The call.** Free US transcripts: Motley Fool publishes most large caps (`tinyfish search query "<company> Q3 2026 earnings call transcript"`). Investor-relations sites carry webcasts and sometimes transcripts. Asian companies often post only Q&A summaries, but Futu often publishes full call transcripts for Hong Kong and China names (全文 … 業績會實錄): `fin.py news "<name> 業績會" --local`.
4. **Expectations.** `fin.py profile SYM` gives EPS actual vs estimate. For revenue and KPI consensus, use news recaps that name the source ("vs LSEG consensus"), and cite them.
5. **Reaction.** `fin.py move SYM --date <first session after the release>`. A release after the close trades the next session.

**Write it up**
- Headline numbers vs consensus and vs a year ago: revenue, EPS and the industry's key metric.
- Guidance: raised, held or cut, against both the previous guidance and consensus.
- Drivers, in management's words: quote a line or two from the release or call.
- What analysts pushed on in Q&A, and anything new (buyback, capex change, M&A, leadership).
- Market reaction, and whether it fits the numbers or something else (guidance, one KPI, the call's tone).
- Sources with links.

**Metrics that move stocks, by industry**

| Industry | Watch |
|---|---|
| Telecom | Postpaid phone net adds, churn, ARPU, fiber/fixed-wireless net adds, free cash flow, capex, leverage |
| Banks | Net interest income and margin (NIM), loan growth, fees, cost/income, credit costs and NPLs, CET1, ROE, payouts |
| Semiconductors, hardware | Revenue by segment or node, gross margin, next-quarter guidance, inventory days, capex |
| Software, internet | Revenue growth, ARR/RPO, net revenue retention, operating margin, users, take rate |
| Retail, consumer | Same-store sales (traffic vs ticket), gross margin, inventory |
| Energy, materials | Production, realized prices, unit cash costs, capex |
| REITs | Distribution per unit (DPU), occupancy, rental reversions, gearing, cost of debt |
| Insurers | Value of new business (Asian life insurers), combined ratio, solvency ratio |
| Pharma, biotech | Trial readouts, approvals, launch sales, patent cliffs |

**Reporting rhythms that trip people up**
- Hong Kong and Singapore require only half-year results. Many large caps add quarterly business updates, which are lighter.
- Japan's 決算短信 (tanshin) results come out on TDnet, often at 15:00–15:30 JST after the close. The English version can lag by days.
- Korea's large caps (e.g. Samsung) publish preliminary results about a week after quarter end and full results weeks later.
- Taiwan companies report monthly revenue (by the 10th); a monthly print can move a stock on its own.
- Japan's and India's fiscal years end in March, so "Q2 FY2027" for them is July–September 2026.

## Questions about filings and announcements

**Find the document**
- US, one company: `fin.py filings SYM --from D --to D --forms 10-K,10-Q,8-K,DEF 14A`.
- US, across all filers (who else mentions X?): EDGAR full-text search.
  ```
  curl -s -A "$SEC_USER_AGENT" 'https://efts.sec.gov/LATEST/search-index?q="exact phrase"&forms=10-K,8-K&dateRange=custom&startdt=2026-01-01&enddt=2026-10-25'
  ```
  Each hit's `_id` is `<accession>:<file>`. The document is at `https://www.sec.gov/Archives/edgar/data/<CIK>/<accession without dashes>/<file>`. Always pass both `startdt` and `enddt`; with only one, it silently searches every year. During outages it returns HTTP 200 with an `errorType` field, so check the body before concluding "no results".
- US, a number across periods: `https://data.sec.gov/api/xbrl/companyconcept/CIK<10-digit CIK>/us-gaap/<Concept>.json` (e.g. `NetIncomeLoss`, `EarningsPerShareDiluted`). Revenue tags vary by company (`Revenues` vs `RevenueFromContractWithCustomerExcludingAssessedTax`).
- Asia: each exchange's announcement search is in `markets.md`. Annual reports, circulars and results are usually PDFs.

**Where things live in US filings**
- **10-K:** Item 1 business, 1A risk factors, 7 MD&A, 7A market risk, 8 financial statements. The notes hold segments, debt maturities, leases and litigation.
- **10-Q:** Part I Item 2 MD&A, Part II Item 1A risk-factor updates.
- **DEF 14A:** executive pay, board, shareholder proposals.
- **Form 4:** insider trades. Code P is an open-market buy, S is a sale. Check the 10b5-1 plan flag before reading meaning into a sale.
- **SC 13D / 13G:** holders above 5%. 13D signals intent to influence.
- **S-1, S-3, 424B:** share and debt offerings, which can explain a drop through dilution.
- **6-K / 20-F:** the foreign-issuer equivalents of 8-K and 10-K (e.g. TSMC's ADR).

**Answer** by quoting the exact passage with the document, section and page. Give the filing date and the period covered. Keep what the filing says apart from your interpretation. For amendments (`/A`), say what changed.

## Peer and valuation comparisons

1. **Choose peers deliberately and say why:** similar business mix, geography and size. Three good peers beat eight loose ones, and mixing in unrelated large caps makes every ratio meaningless.
2. **Pull the table:** `fin.py profile A B C`.
3. **Use the metrics that fit the industry:**
   - Banks: P/B against ROE, dividend yield, CET1. EV/EBITDA means nothing for a bank.
   - REITs: yield, price to NAV, gearing.
   - Growth tech: EV/sales against growth and margins.
   - Telecom, utilities, industrials: EV/EBITDA, free-cash-flow yield, leverage.
   - Insurers: price to embedded value, growth in value of new business.
4. **Check comparability:**
   - Currency: market caps are in local currency.
   - Fiscal years: the "Latest quarter" row shows when peers are a quarter apart.
   - Accounting: US GAAP vs IFRS vs JGAAP vs China's CAS.
   - Share classes and dual listings: A/H premiums, ADR ratios.
   - One-offs that distort trailing P/E or growth.
5. **Verify the two or three numbers** your conclusion rests on in the latest results release or filing.
6. **Present** the table, what stands out, the business reasons behind the gaps (cheap for a reason?), and the caveats. When asked which looks best, compare on the metrics and lay out the trade-offs rather than making a buy/sell call.
