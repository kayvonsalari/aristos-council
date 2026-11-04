# ABSOLUTE-1: from "better than its peers" to "what does the price require, and what does the record show"

Status: design for approval, revision 3, 2026-11-03. Phase 1 (the gate, section b) is built as ABSOLUTE-GATE-1; nothing else here is built. No code, strategy file, backtest result or data file changes with this page.

How to read the sources on this page. A figure is either cited to a source by name, in brackets, or marked "judgment". A figure the owner's brief attributed to a source that could not be read in the verification pass is carried only where the owner's earlier pass fetched it with the quotation given. It is then cited as "fetched 3 Nov 2026 in the owner's earlier pass; this pass's retry was refused", and the Sources section says so. Any other such figure is marked "not confirmed" and listed in the Sources section. Where the source says something different from the brief, the source wins and the difference is said.

## a. The question Aristos answers now, and why it is being replaced

Today Aristos answers one question: is this company better than the companies around it? Nine lenses rank a company inside an automatic peer group. Eight of them vote BUY, HOLD or SELL, and one only marks (Forensic). The verdict of record is the strict majority of the votes, or MIXED when none holds one.

That is a fair question for choosing among forty names. It is the wrong question for an owner deciding whether to buy one. A company can rank first in an expensive group and still lose money. The Microsoft run of 2026-11-01 shows it. Earnings Power Value voted BUY because Microsoft's price sat closer to its no-growth value than ten of thirteen peers. Yet the same page says every company in that group is priced above that value. The BUY meant "least expensive of an expensive group".

The new question is: if I buy this company at today's price and own it for the long run, what does the price require the business to do, and does the record show it can? The doctrine does not change. The maths judges and the language model writes. No model touches the verdict, the gate, the valuation or the return figure.

What this cannot promise. No method guarantees that qualifying companies make money. The page shows what the price requires and what the record shows, and nothing more.

What stays true from the old design: every figure comes from a deterministic tool, a missing input is "not evaluated" and never a failure, and published strategy files are never overwritten.

## b. The gate: who is excluded before any valuation

Three standing principles, in the owner's words:

- **(a)** An exclusion rule that removes more than one name in ten at calibration is the wrong instrument and becomes a mark until re-specified.
- **(b)** The gate excludes only for broken accounts or solvency. Everything else is a mark the reader sees.
- **(c)** Size is not a gate question. The absolute path runs for any company, and one below the backtested range is labelled "outside the tested range", never blocked.

The gate has four exclusion rules and four warnings. Each returns excluded (with the figure), passed, or not evaluated. A ratio whose bottom number is zero or negative is never a pass. The case is written out for every rule. A cheap or expensive price never excludes. A cut dividend, a heavy borrower, a weak distress score or a big spender is a cost to a shareholder, not a failure, so those are warnings.

### Exclusion 1: earnings quality (accrual ratio)

- What it is: the share of profit that has not arrived as cash. Accrual ratio = (net income minus operating cash flow) divided by average total assets. Average total assets is the mean of this year's and last year's.
- Why this form: Hribar and Collins (2002) show that building accruals from changes in balance sheet accounts, instead of reading them from the statement of cash flows, adds measurement error. Dechow and Schrand (2004) define accruals as earnings less cash from operations and rank companies in tenths by their size. Lev and Nissim divide by the average of opening and closing total assets, and note the cash-flow measure exists only from 1988.
- Rule: exclude above 0.10. Sloan's design [Sloan 1996] sorts companies into tenths by accruals, and in his Table 6 the highest tenth earned minus 5.5% size-adjusted in the following year and the lowest tenth plus 4.9%. He builds accruals from the balance sheet, so the form here differs from his.
- Why 0.10 and not the top tenth of our names: gate version 1 measured it. On 2,018 cohort names the cash-flow form is mostly negative (median minus 0.049), because depreciation is a non-cash expense that the cash-flow form adds back. The top-tenth boundary on our names was therefore 0.0054, a line near zero that says little about how poor the earnings are, and it excluded one name in ten by construction. The owner set the fixed line 0.10 for version 2. For orientation only, Ball et al. (2016, Table 1) report a 75th percentile of 0.016 and a 99th of 0.235 for the balance-sheet measure over opening assets, a different form.
- Calibration result at 0.10: 1% of names are excluded by this rule.
- Zero or negative case: average total assets at or below zero, or either input missing, is not evaluated. Negative accruals pass.
- A conflict in the evidence, stated plainly. Lev and Nissim conclude that the accruals anomaly persists and its size has not declined. Mohanram's 2011 draft finds that it produced excess returns until 2002 and has apparently disappeared since. The rule stays as an accounting-quality check, not as a source of return (section o).

### Exclusion 2: solvency (cash flow over debt)

- What it is: whether the cash the business makes before working-capital swings is a safe multiple of what it owes. Solvency = cash from operations before working-capital changes divided by total debt.
- Cash from operations before working-capital changes = operating cash flow minus the cash flow statement's change in working capital (a positive change means cash was released). It is the measure Moody's calls CFO pre-WC. Total debt is gross, not net, as Moody's measures it, and is counted as the tool counts it: the balance sheet's own Total Debt row (borrowings plus the long-term operating lease line).
- Rule: exclude below 5%. In Moody's utilities method the grid factor "CFO pre-WC / Debt" puts 5% to 13% in the Ba band and 1% to 5% in the B band in the standard grid [Moody's, Regulated Electric and Gas Utilities methodology; the exhibit text was garbled and its rows were rebuilt from the band edges]. Below 5% is therefore the B band and below.
- Sign cases, in order. Net cash passes and debt at or below zero passes, both before any ratio is formed. Cash flow before working capital at or below zero reads "not evaluated: loss-maker, see runway", and the runway rule decides. A negative ratio never excludes on its own. A missing debt figure, or a missing cash-flow line, is not evaluated; operating cash flow never stands in for the figure before working capital.
- Calibration result: 1% of names are excluded by this rule.
- This rule replaced Altman's distress score as exclusion 2. The score is now warning B.
- Not for banks and insurers (section c).

### Exclusion 3: accounting health (F-score)

- What it is: nine yes/no checks on profit, borrowing and efficiency, scored 0 to 9 [Piotroski 2000].
- Rule: exclude a score of 0 or 1. Piotroski's own groups: "The Low F-SCORE portfolio consists of firms with an aggregate score of 0 or 1; the High F-SCORE portfolio consists of firms with a score of 8 or 9." It counts only when all nine checks can be computed, as in his sample design. With fewer, the page shows "x of 9 computable" and the rule is not evaluated. This replaces the earlier rule, which scored from five checks.
- Evidence: one-year market-adjusted returns of 0.134 for the high group and minus 0.096 for the low group. "The mean return difference of 0.230 is significant at the 1% level." [Piotroski 2000, Table 3; fetched 3 Nov 2026 in the owner's earlier pass; this pass's retry was refused]. Walkshäusl (2020) finds high-score firms beat low-score firms by about 10% a year in non-US markets over 2000 to 2018 (section o).
- Disclosure: his sample is "firms in the highest BM quintile with sufficient financial statement data to calculate the various performance signals" [same source and note]. He tested the score on high book-to-market stocks only, and we apply it to every company.
- Calibration result: 0% of names are excluded; 3% are not evaluated (fewer than nine checks).

### Exclusion 4: cash runway

- What it is: how many months the cash lasts. Runway = (cash plus short-term investments) divided by the yearly burn. Burn is the negative free cash flow of the latest year.
- It triggers on cash from operations before working-capital changes at or below zero, the same measure the solvency rule uses. So every company falls under exactly one of the two rules: positive goes to solvency, at or below zero comes here. If free cash flow is not negative in such a year, working capital funded the business, and the runway passes with the note "funded by working capital this year". There is no "not enough data" outcome for that case.
- Rule: exclude under 12 months. This is the going-concern horizon. US accounting asks management whether there is substantial doubt about the entity's ability to continue "within one year after the date that the financial statements are issued" [FASB ASU 2014-15]. IAS 1 paragraph 26 is the international counterpart; the page could not be read, so it is not cited.
- 12 to 24 months passes, and warning D fires. The 24 is a judgment. BiopharmaWatch: "Many investors look for at least 18 to 24 months", and under 12 months "usually means a raise is close". RA Capital: a "convention that public biotech companies should plan to raise money by the time they get down to one year of cash left", which it then argues is not always a concern.
- Zero or negative case: if a cash line is missing, it is not evaluated.
- Calibration result: 1% of names are excluded by this rule.

### Warning A: highly leveraged (two readings)

- What it is: how heavy the borrowing is, read two ways. Net debt divided by EBITDA says how many years of operating cash profit it would take to repay the borrowing (EBITDA is profit before interest, tax, depreciation and amortisation; it needs the depreciation field, section l). Cash flow over debt is the solvency ratio above. Both readings are printed.
- Rule: the warning fires when net debt over EBITDA is above 6 times or cash flow over debt is below 12%. Source: S&P Global Ratings. Its financial risk scale has six bands, from minimal to highly leveraged. In the 2009 criteria, Table 2 (Financial Risk Indicative Ratios, Corporates), the bands for FFO/Debt, Debt/EBITDA and Debt/Capital are: minimal above 60% | under 1.5 times | under 25%; modest 45-60% | 1.5-2 | 25-35%; intermediate 30-45% | 2-3 | 35-45%; significant 20-30% | 3-4 | 45-50%; aggressive 12-20% | 4-5 | 50-60%; highly leveraged under 12% | above 5 | above 60% [S&P, Criteria Methodology: Business Risk/Financial Risk Matrix Expanded, 27 May 2009; read 3 Nov 2026]. So 12% is S&P's highly leveraged line for cash flow over debt. The 2024 Corporate Methodology keeps the same scale in its standard table, tables 17 to 19, and adds two more lenient tables: highly leveraged above 5.5 times (medial volatility) and above 6 times (low volatility, used for companies with the most stable cash flows, paragraph 123) [S&P, Corporate Methodology 2024; fetched 3 Nov 2026, supplied from the owner's earlier fetch; this pass's retry was refused with HTTP 403]. The EBITDA line uses 6 times, the most lenient of the three, so a flagged company is highly leveraged on every S&P table. The standard table says 5 times.
- Why a warning and not an exclusion: heavy borrowing is normal in some industries. Damodaran (January 2026) lists debt to EBITDA of 6.77 for general utilities, 6.04 for water utilities, 3.85 for telecoms, 1.71 for software and 2.43 for pharmaceuticals, against 6.52 for the whole market and 3.01 without financials. His page does not say whether the debt includes leases. Moody's utilities method sets the Baa band for cash flow before working capital over debt at 13% to 22% in its standard grid and 11% to 19% in its low-business-risk grid.
- Net cash passes on both readings. EBITDA at or below zero makes that reading not evaluated, and the other reading can still decide.
- Calibration result: 9% of names fire it.

### Warning B: distress score low (Altman Z-double-prime)

- What it is: one number from four accounting ratios that measures how close a company is to failing. Altman's version for non-manufacturers, written here without its constant:
  Z'' = 6.56 X1 + 3.26 X2 + 6.72 X3 + 1.05 X4 [Altman 2000; Altman 2002]
  X1 = working capital / total assets. X2 = retained earnings / total assets. X3 = EBIT / total assets. X4 = book value of equity / total liabilities. (Working capital is current assets minus current liabilities. EBIT is profit before interest and tax.) The definitions are those on Altman's 2017 slides.
- Why this version: it uses book equity, not market value, and has no sales term. So a company whose shares are quoted in one currency and whose accounts are in another is no longer a problem. Every input is a statement figure and the ratios cancel the currency.
- Rule: the warning fires below 1.10. Altman (2002): "Z''-Scores below 1.10 indicate a distressed condition", in the form without a constant. The grey zone, 1.10 to 2.60, passes with a note. The 2.60 is derived, not quoted. Altman's 2017 slides give the zones for the version with a constant of 3.25 (distress below 4.35, safe above 5.85). The constant is stated in Altman (2000, 2002, 2018). Taking it off gives 1.10 and 2.60.
- Why a warning and not an exclusion: "the general Z-Score model works reasonably well for most countries (the prediction accuracy is approximately 0.75)", and "classification accuracy can be improved further (above 0.90) by using country-specific estimation" [Altman, Iwanicz-Drozdowska, Laitinen and Suvas 2017, abstract; read on the vlex copy on 3 Nov 2026; the SSRN page refused]. That accuracy is the authors' own measure, not a hit rate we have tested. Version 1 used it as an exclusion and it removed 20% of names, including whole industries of ordinary operating companies. By principle (a) it is a mark.
- Zero or negative case: total liabilities or total assets at or below zero is not evaluated. Negative equity makes X4 negative and the score low. That is real information and is allowed to count.
- Calibration result: 20% of names fire it.

### Warning C: dividend cover

- What it is: whether profit pays the dividend. Cover = three-year average net income divided by dividends paid.
- Rule: the warning fires below 1.0, where the dividend is not covered [Wall Street Prep: "Net income is insufficient to pay the dividend"]. Between 1.0 and 1.5 nothing fires. The page prints the cover figure with the line "below 1.0 the dividend is not covered; 1.5 to 2.0 is the range sources call thin to comfortable". That range is context from the sources: Stockopedia, "Dividend Cover of less than 1.5 may indicate a danger of a dividend cut while more than 2 is viewed as healthy" [Stockopedia, Dividend Cover; fetched 3 Nov 2026, supplied from the owner's earlier fetch; this pass's retry was refused with HTTP 403]. AJ Bell calls 2.00 times "a figure traditionally seen as one that offers comfort and protection in the event of any unforeseen economic setback". It puts FTSE 100 cover at 1.99 times and says the top ten yielders' 1.22 times "looks skinny" [AJ Bell Dividend Dashboard Q4 2025; fetched 3 Nov 2026, supplied from the owner's earlier fetch; this pass's retry returned HTTP 404]. Wall Street Prep adds that a cover above 2.0 is perceived as the minimum floor before shareholders should worry.
- Why net income and not free cash flow: payers whose free cash flow is negative are common in regulated industries and still pay. Damodaran (January 2026): general utilities pay out 65.40% of net income while their free cash flow to equity before borrowing is negative (minus $23,132m), though positive after new borrowing ($1,550m). EEI reports a payout of 60.9% for its index companies over the twelve months to 30 September 2025, a total of dividends over a total of earnings. Moody's does size dividends against cash flow before working capital, in a separate line of its grid.
- Why a warning: a dividend cut is a cost to the owner, not a failure of the business.
- Zero or negative case: average net income at or below zero with a dividend being paid reads "not covered", never a pass. No dividend means the warning does not apply.
- Calibration result: 11% of names fire it.

- Data guards, found by a cache check before the gate was frozen: the cash-flow dividends line counts only when the provider also records a per-share dividend or a yield, and only the latest fiscal year's line is used. Without them, companies that pay no dividend but whose line held other flows (a software company showed 1,022m, a chip maker 2,088m) read as payers, and 398 spurious payers lifted the warning from 11% to 20% of names.
- A limit that remains: on 165 of 1,419 payers (12%) the line is more than 1.5 times the common dividend that the per-share figure implies. It may include payouts to preferred, minority or hybrid holders, and 60 of the not-covered names are among them (without them the warning would fire on 8% of names). The raw lines are not kept, so this is reported, not corrected.

### Warning D: runway between 12 and 24 months

- It fires for a company under the runway rule whose cash lasts less than 24 months: "will need to raise money within the holding period". Under 12 months the exclusion takes it. See exclusion 4 for the sources and the trigger.
- Calibration result: 1% of names fire it.

### How the rules combine

- Any one failed exclusion excludes. The page names the rule and its figure.
- "Qualifies" needs the accrual ratio evaluated and solvency settled. Solvency is settled when it is evaluated, or when the company is a loss-maker before working capital and its runway is evaluated (the runway decides). The F-score counts when nine checks exist. Otherwise the result is "not enough data", with the missing inputs listed.
- Warnings never change the result. They print beside it.

### Context, not exclusion: worst-year return on capital

Worst-year return on capital and similar quality readings stay as context. Reason: a cyclical company has a trough. Oil producers earned well below their cost of capital in 2020, then recovered. A gate that read the worst of five years would remove them at the bottom of the cycle, when the price is lowest. The reading prints with its year, so a reader sees the trough.

### Calibration before any threshold is frozen

- Run every rule on the union of all cohort members, not on the whole index. Locally there are 58 built cohort folders holding 2,044 member rows and 2,020 distinct tickers (the README counts 57 defined cohorts, so one folder is older). Counted as aggregates only; no names are recorded here.
- Cost: one EODHD fundamentals request is 10 units. Calibrating on 2,020 names is about 20,200 units. The whole market index of about 25,000 listed companies would be about 250,000 units. This is why the cohort union is used.
- Report, per industry, the share excluded by each rule. If the rules exclude an industry wholesale (more than half of its names), the industry is marked "not covered by the gate in this release". The thresholds are never loosened to admit it.
- Version 2 was calibrated on 2026-11-03 (2020 names, 20200 units; docs/GATE_CALIBRATION.md). Share of names excluded, by rule: the accrual ratio 1%, solvency 1%, the F-score 0%, the cash runway 1%. Overall, 3% are excluded and 97% qualify. **No exclusion rule breaks the guardrail.** The largest share removed by one rule is 1%.
- Industries excluded wholesale by the version 2 rules: 0.
- The calibration changed no threshold. It re-measured the investment-intensity boundary (the top fifth, section d) and listed the industries above, and wrote both into the gate file.

## c. Banks and insurers

Not covered in this release. Earnings before interest and tax over enterprise value does not work for a bank, because interest is its cost of goods and its debt is raw material. The distress score, the accrual ratio and the leverage warning do not apply for the same reason. The page says so plainly, shows no return figure and no verdict, and prints the Financials readings (price to book, return on equity) as context. A path for banks would need its own design.

## d. The valuation basis: everything per share

The base is free cash flow per share. Free cash flow is the cash left after the business has paid for what it needs to stay in business and grow. Per share means it is what one share owns.

- Formula: free cash flow per share = (cash from operations before working-capital changes minus capital spending) divided by diluted weighted-average shares. (Diluted means counting shares that options and similar rights could add.) **Revised in definition version 3 (ABSOLUTE-PAGE-2; decision 29):** versions 1 and 2 used operating cash flow as reported. Working-capital swings reverse, so they are not cash the owner keeps: Korea Gas (036460.KS) read a 107% yield because receivables collected after years of under-recovered costs flowed through operating cash flow. The solvency rule (section b, decision 23) already uses cash from operations before working-capital changes, so the page now has one definition of cash from operations. The price test prints the latest year's working-capital effect as its own line, and marks a yield on the base above 25% as "unusually high; check the cash flow lines".
- Not valued in this release: companies with positive cash from operations but negative free cash flow after capital spending (regulated utilities in an investing phase, such as Duke Energy) read "not enough data: not valued, no positive free cash flow". A dividend-based path for them is a later design.
- Rule: use the lower of the latest fiscal year and the three-year average of per-share free cash flow. This is a judgment, and it is conservative.
- Price per share is in the reporting currency. A share quoted in another currency is converted at the current rate. The tool already fetches current exchange rates as price pairs from yfinance for this purpose (the same source the valuation band uses at month ends), so no new source is needed for this step.
- Why after-tax operating profit cannot be the base. It is profit before the money the business must put back to grow, so it is not cash to the owner. A company that must spend most of what it earns to keep growing would look cheap on it and be expensive on cash.

The verdict rests on the return range alone (section f). There are no separate "measures". The readings below are context printed beside it.

### Context 1: earnings yield (the Magic Formula figure, unchanged)

Operating profit (EBIT) divided by enterprise value, as today. Enterprise value is the market value of shares plus debt minus cash. The page prints it as context. It no longer feeds any verdict.

### Context 2: earnings power value (unchanged at 9%)

Earnings power value is what the business would be worth if its current normalised profit never grew. The method is Greenwald's. Wall Street Prep: "Earnings Power Value (EPV) = Adjusted Earnings / Cost of Capital (WACC)", with adjusted earnings taken as operating income at the average margin of three to five years, taxed. The Kellogg lecture notes label it "Enterprise Value" at "Cost of Capital = WACC", and subtract debt to reach equity. So it is a value of the whole business at the weighted cost of capital. It is therefore not re-based to the equity hurdle in section g. It stays at 9% and is labelled an enterprise value. It is context.

### Context 3: investment intensity

- Capital spending divided by depreciation and amortisation is printed. Wall Street Prep writes the ratio the other way up (depreciation over capital spending) and says mature companies with little growth investment sit "near 1.0, or 100%". Its next sentence, about ratios far from 1.0, reads backwards, so it is a weak source. Near 1.0 means spending only to stand still, which is our reading too. The source gives no threshold above that.
- Abnormal investment is also printed, following Titman, Wei and Xie (NBER w9951): capital spending over sales this year, divided by the average of the same ratio over the previous three years, minus 1. They find that firms that substantially increase capital investment subsequently earn negative benchmark-adjusted returns (an investment-spread return of 0.168% a month, about 2.02% a year, in the paper).
- Warning when the company is in the top fifth of our names on this measure. The boundary comes from calibration. The top-fifth cut is a judgment.
- Dropped: the earlier rule "capital spending above 1.5 times depreciation for two years". It had no citable basis. Depreciation is also movable. Microsoft extends the estimated useful life of its data centers and office buildings from 15 to 25 years from the start of fiscal 2027 [CFO Dive, 30 July 2026]. The article calls the effect on operating income a minimal benefit and says the larger effect is on capital spending, because more future data center leases will be operating leases, which are not counted as capital spending. A depreciation-based flag could be moved by an accounting estimate. Printing both measures lets a reader see it.

## e. Implied growth: the exact calculation

The question: what yearly growth in free cash flow per share must this company deliver for ten years to justify today's price, if the buyer earns exactly the hurdle (section g)?

- Base `B`: free cash flow per share, by the rule in section d.
- Price: price per share.
- Horizon: ten years of growth at a constant rate `g`.
- After year 10: the cash flow stays flat forever, valued as a perpetuity at the hurdle.
- Equation: price = sum for t = 1 to 10 of [ B (1+g)^t / (1+h)^t ] + [ B (1+g)^10 / h ] / (1+h)^10, where `h` is the hurdle.
- Solved by bisection between minus 10% and plus 40% a year. The right side rises steadily with `g`, so there is one answer. If none exists in that range, the page says "not solvable".

Compared with three records of growth, per share:
1. Own 5-year growth in diluted earnings per share, from the statements.
2. Own 10-year growth in diluted earnings per share. A record is unavailable when the earnings per share in the start year or the end year is at or below zero.
3. Consensus: the analysts' earnings per share growth, compounded from the last reported fiscal year to the second forecast year (two steps), with both years named on the page. Marked "not testable" (section j).

Mark any rate above 18% a year: "few companies have grown faster than 18% a year for ten years". Source: Chan, Karceski and Lakonishok (2003), Table I, ten-year growth, 1951 to 1998. The 90th percentile is 18.0% for sales, 19.4% for operating income before depreciation and 20.4% for income before extraordinary items (the "average" rows), with medians of about 10%. 18% is the lowest of the three, so the mark errs toward flagging. Their abstract says there is no persistence in long-term earnings growth beyond chance. The body finds persistence in sales growth, so the finding is about earnings, not every measure.

### Worked example: Microsoft

Inputs from Microsoft's 10-K for the year to June 2026 (SEC filing; the saved run of 2026-11-01 agrees on cash flow), in millions of dollars:

| Line | FY2026 | FY2025 | FY2024 |
|---|---|---|---|
| Net cash from operations (as reported) | 182,935 | 136,162 | 118,548 |
| Change in working capital (positive = cash released) | -4,895 | -1,000 | -2,000 |
| Cash from operations before working-capital changes | 187,830 | 137,162 | 120,548 |
| Additions to property and equipment | 115,948 | 64,551 | 44,477 |
| Free cash flow (before working-capital changes) | 71,882 | 72,611 | 76,071 |
| Diluted weighted-average shares (millions) | 7,453 | 7,465 | 7,469 |
| Per share | 9.64 | 9.73 | 10.18 |

(Re-anchored by ABSOLUTE-PAGE-2. FY2026's working-capital change is the figure that gives the owner's 187,830; the FY2025 and FY2024 changes are round fixture values used by the tests, not read from the 10-K. On operating cash flow as reported the three years were 8.99, 9.59 and 9.92.)

- Lower-of rule: the three-year average is 9.85 and the latest year is 9.64. The latest year (FY2026) wins. The base is 9.64 a share. The latest year's working-capital effect is a use of 0.66 a share, which the base leaves out.
- Price: $521.91 (saved run). Yield on the base: 9.64 / 521.91 = 1.8%.
- Bond: 4.93%, the ten-year US yield on 30 October 2026 (EODHD series, close 4.934; section g). FRED shows 4.94 for the same day. Hurdle = 4.93 + 5.0 = 9.93%.
- Implied growth: the price needs **24.0% a year** for ten years (25.0% on the old base of 8.99). Computed with a throwaway script, not committed.
- Records: own 5-year diluted earnings per share growth 17.2%, own 10-year 21.2% (saved run). Consensus 16.0% a year: two steps from the last reported year (FY2026 diluted earnings per share $17.95, from the 10-K) to the consensus for the year to June 2028 ($24.14). The page used to show 20.6%, a single step between the two forecast years ($20.02 to $24.14). The change is because the definition now starts from the last reported year, which is the only year with an actual figure, and spreads the growth over the two steps it needs. The first step alone (17.95 to 20.02) is 11.5%. All the records sit below the 25.0% the price needs. The own 10-year rate of 21.2% is above 18% and carries the mark.
- Investment intensity: capital spending of $115.9bn against depreciation, amortisation and other of $38.5bn is 3.0 times. The cash flow statement line also includes "other", so the ratio is approximate. The three-year history of capital spending is 44.5, 64.6 and 115.9 ($bn).

## f. The return range and the verdict of record

### The return

The long-run owner's return is the yearly rate `r` at which the ten-year path of free cash flow per share, flat after year 10, is worth exactly today's price. It is the same equation as section e, solved for `r` instead of `g`, for each growth case. A shorter holding also depends on the price the shares sell for, which this data cannot predict.

Two conventions it rests on. A ten-year horizon sits inside the range of years that markets typically price in. Mauboussin and Callahan (2014), citing Olsen, say the market-implied competitive advantage period averaged about 8 years from 1976 to 2007, from about 5 years for very competitive industries to 15 for stable ones. Zero growth after year 10 is below Damodaran's rule of thumb that the stable growth rate "should not exceed the riskless rate used in the valuation".

### Four growth cases

1. Own 5-year diluted earnings per share growth.
2. Own 10-year diluted earnings per share growth.
3. Consensus (marked "not testable").
4. The downside case: years 1 and 2 at the consensus rate (own 5-year rate if there is no consensus), then a straight line to the bond yield by year 10, flat after. **Revised in definition version 2 (decision 17): years 1 and 2 use the lower of the consensus rate and the own 5-year rate.** The reasons are Chan et al.'s result that growth does not persist beyond chance, Damodaran's stable-growth rule, and Mauboussin's horizon. The straight-line shape is a judgment.

The range runs from the lowest to the highest of the available cases. Fewer than two available means "not enough data".

### Microsoft, on the new basis

Hurdle 9.93%. Base 9.64 a share (cash from operations before working-capital changes; ABSOLUTE-PAGE-2). On the old base of 8.99 the four returns were 6.5%, 6.0%, 8.1% and 4.3%.

| Growth case | Growth | Return |
|---|---|---|
| Own 5-year | 17.2% | 6.8% |
| Consensus (two steps, 16.0%) | 16.0% | 6.3% |
| Own 10-year | 21.2% | 8.6% |
| Downside | 16.0% for two years (the lower of the consensus and own 5-year rates), then falling to 4.93% | 4.6% |

The range is 4.6% to 8.6% a year, against a hurdle of 9.93%. Verdict: **qualifies, below hurdle, 0 of 4 cases clear.** Beside it, for the change in the consensus definition: the old one-step 20.6% would have returned 7.9% for the consensus case and 5.2% for the downside case. The verdict is the same either way.

### Verdict of record

| State | Rule |
|---|---|
| Excluded | At least one exclusion rule failed. The rule and figure are named. |
| Not enough data | The gate could not be completed, or fewer than two growth cases exist, or no hurdle, or no free cash flow per share. The missing items are listed. |
| Qualifies, below hurdle | Passed the gate, and at least one available case returns less than the hurdle. |
| Qualifies, clears hurdle | Passed the gate, and every available case, the downside case included, returns at least the hurdle, **and at least one own-record case (own 5-year or own 10-year growth) is available and clears.** |
| Not enough data (no own record) | Passed the gate, but neither own-record case exists: "no record of the company's own growth". The price test and the forecast-based cases stay on the page as information. (ABSOLUTE-PAGE-2; decision 28.) |

The page always shows "n of 4 cases clear" (or n of the cases that exist). Forecast-based cases (consensus, and a downside that starts from it) cannot make a company clear on their own: Ford read "clears hurdle" on two of them, with no record of its own growth, three warnings and net debt of 38.6 times EBITDA.

The backtest-form verdict is computed and shown on every live page beside the full one. It uses the cases the backtest can test: own 5-year, own 10-year and the downside case with its fallback, with no consensus. For Microsoft the downside case with the own 5-year rate in years 1 and 2 returns 4.6%, own 5-year 6.5%, own 10-year 8.1%. That is below hurdle, 0 of 3.

### How it is shown, so it does not read as a forecast promise

- The figure is labelled "long-run owner's return if the growth holds". The words "expected return", "target" and "proven" are never used.
- It is always a range with named cases, never one number.
- The line under it: these are what the price returns if the stated growth repeats. They are not forecasts. Past growth may not repeat. Forecasts can be wrong. The model's narration cannot change them.
- Any growth case above 18% a year is marked, and still shown.
- This figure shows what today's price requires. Studies of company-level implied returns find they rank future returns only partly: where 1 would be perfect, the weakest model scores about 0.2 and the better ones 0.5 to 0.65, and four of five measures do no better than a fixed constant in the cross section (section o, Implied returns). So the figure is a question put to the reader and not an answer (section o, "Implied returns"). The gate and the cash yield carry the evidence; this figure carries the question.

## g. The hurdle

Hurdle = the ten-year government bond yield of the reporting currency + 5.0 percentage points.

**Why 5.0.** Kroll's recommended US equity risk premium is 5.0%, reaffirmed on 30 January 2026 and effective 2 September 2025. It is paired with the higher of a normalised risk-free rate of 3.5% or the spot 20-year Treasury yield [Kroll Cost of Capital Inputs, year-end 2025]. The 2025 survey of finance professionals by Fernandez et al. gives median market risk premia of 5.0 (USA), 5.2 (Germany), 5.1 (UK), 4.4 (Switzerland), 5.7 (Japan) and 7.6 (Brazil), with averages of 5.5, 5.4, 5.4, 4.2, 5.1 and 7.9. For contrast, Damodaran's home page shows an implied premium of 4.41% on 1 November 2026, with 4.20% listed as the previous month. His January 2026 country table gives 4.46% for the US and 4.23% for mature markets. His 1928 to 2025 arithmetic means are 11.85% for stocks, 3.41% for T-bills and 4.82% for T-bonds, with premiums of "8.44% for stocks over T. Bills and 7.03% for stocks over T. Bonds" [Damodaran, Equity Risk Premiums 2026 edition, Table 3; fetched 3 Nov 2026 in the owner's earlier pass; this pass's retry was refused].

The earlier proposal of 4.0 points is dropped. It was a judgment chosen so that more names would clear, which is the wrong reason.

**One hurdle for all companies.** There is no per-company risk adjustment. That is one honest assumption, stated, instead of many precise-looking guesses.

**Bond source.** EODHD's government bond series, read through the end-of-day endpoint (for example `/api/eod/UK10Y.GBOND`). EODHD's page describes 154 series across 34 countries, daily from 2000 or earlier depending on the country, one call each regardless of history length, and lists the endpoints as available on the Fundamentals Data Feed plan [EODHD Macroeconomic Data API]. Probed once for this page on 2026-11-03, with the key read from the local environment file and not printed: the US ten-year series answered HTTP 200 with daily rows. Closes: 4.938 (29 October), 4.934 (30 October), 4.909 (2 November), 4.927 (3 November). So the plan reaches it.

- A currency-to-series table is kept as data. For example, the euro maps to the German ten-year. Currencies pegged to another, such as the Hong Kong dollar, map to the US series, and the mapping is documented. The list of available series needs a token and was not read, so only the two series named above are verified.
- No series for a currency: the result is "not enough data: no hurdle".
- The last good yield is kept for 7 days, then lapses.
- US fallbacks: ^TNX on Yahoo Finance. Its history page with a 1962 window (https://finance.yahoo.com/quote/%5ETNX/history/?period1=-252460800&period2=-157766400&interval=1mo) shows rows from January 1962 and includes Jun 22, 1962 at 3.9000 (read 3 Nov 2026). ^IRX, with %5EIRX in the same address, also shows rows from January 1962 (its first row as read is 2 January 1962, not June). FRED's DGS10 series gave 4.94 for 30 October 2026 and needs a free API key.
- EODHD also has a US Treasury rates API (beta, all plans, one call per request).
- Cash comparison in the backtest: the US 3-month bill, FRED DTB3 (3.99 on 30 October 2026), labelled a benchmark and not a hurdle. ^IRX was not checked.

A local bond of a risky country may already carry country risk, so adding 5.0 points can double count it. That is accepted as conservative.

## h. What happens to each existing lens

| Lens | Disposition | Reason |
|---|---|---|
| Forensic | Kept as gate input | Its accrual reading and its F-score become exclusions 1 and 3; its distress reading becomes warning B. The mark-only lens retires in its ranked form. |
| Quality | Kept as context | Worst-year return on capital and gross profitability print as quality readings. Its debt-to-profit leg becomes warning A. |
| Earnings Power Value | Kept as context | Section d, context 2. As a voting lens it retires. |
| Magic Formula RAW | Retired as a vote | Its earnings yield is context 1. Its return on capital part is context. |
| Value + Momentum | Retired as a vote | Earnings yield is context 1. Momentum becomes context with no vote. |
| Growth | Retired as a vote | The growth record feeds the three growth cases. The rank is context. |
| Defensive Income | Retired as a vote | Its dividend cover becomes warning B. Yield and streak are context. |
| Cyclical Income | Retired as a vote | Same. |
| Financials | Kept as context | Section c. |
| ETF lenses | Unchanged | The new question is about companies. |
| Legacy lenses already hidden | Unchanged | |

Strategy files are not edited. The new gate and valuation live in new, versioned files.

## i. The company page, in the order a reader meets it

1. **The answer** (written by code, two lines): the verdict of record in plain words, then the one reason that mattered most.
2. **The story** (written by code, five short paragraphs): what was asked, what the gate found, what the price requires, what to doubt, what this cannot tell you. The optional model summary still replaces it when ticked, and cannot change a number.
3. **The gate**: a table of four exclusion rules and four warnings, each with its reading, its limit and its result.
4. **The price test**: earnings yield, implied growth against the three records, and investment intensity.
5. **The return range**: the four growth cases, the return for each, the hurdle, the verdict, "n of 4 cases clear", and the backtest-form verdict beside it.
6. **Context, no vote**: quality readings (with the trough year named), momentum, where the company sits among rivals, the valuation band, earnings power value, analysts' readings.
7. **The judge panel**, when ticked, asked the same question (section k).
8. **The workings**, folded: every source, every input with its date, the old relative-lens table labelled "baseline: relative view, no longer votes".

The text download keeps every section.

## j. The new backtest

**Question.** Did names that qualified beat (a) a broad index, (b) cash, and (c) an equal-weight basket of companies in the same industry, over 1 and 3 years? The third comparison stops a sector boom being mistaken for skill. Tested by industry, as today.

**Order of reporting.** Gate alone first, then gate plus valuation, reported separately so the result says which part works.

**Three forms.**
1. Gate alone: every name that passes the gate.
2. Gate plus relative cheapness: within each industry, the cheapest third of the qualifying names on free cash flow yield (free cash flow per share divided by price). Ranked inside the industry, never across industries, so no bet is placed on cheap industries. This is the form with published support (section o).
3. Gate plus the absolute verdict: only names that read "clears hurdle" on the backtest form (own 5-year, own 10-year and the downside case with its fallback; no consensus). This answers the owner's question. It has no published evidence of its own, and the page shows the gap between forms 2 and 3.

**Method, reusing what exists.**
- Monthly rounds on accounts already published, using the as-of reader. Each form is held equal-weight for 12 and for 36 months, with the existing 0.5% round-trip cost.
- Qualifying counts are reported per month and per industry, so a result built on three names is visible as such.
- Market value at past dates is the historical share count (outstanding shares from the provider; diluted where it carries them) times the price on that date, using EODHD's outstanding-shares history. It is not today's value scaled by price change, which is what the backtest does now.
- Three comparisons, kept: a broad index, cash (the US 3-month bill) and the equal-weight same-industry basket. Excess is measured against each separately.
- Luck test: the existing one, 500 random baskets of the same size and turnover drawn from the same industry pool. Drop-best check: the existing one.
- Pass bar: at least 2 points a year over the equal-weight industry basket across the full window, with the luck score under 5%. The count of winning years is reported, not required, because cheapness works with long droughts. Swedroe (2018): the value premium averaged 4.8% a year over 1927 to 2017 and was negative in 22% of five-year periods, 14% of ten-year periods and 6% of twenty-year periods [Swedroe 2018; fetched 3 Nov 2026 in the owner's earlier pass; this pass's retry was refused]. One fetched study measures a drawdown of 55% in value against growth from 2007 to mid-2020 [Arnott et al. 2021].
- The 3-year hold is reported without pass or fail, since it has far fewer independent rounds.

**What can and cannot be tested.**
- Testable: the gate, free cash flow yield, and implied growth against the company's own records. All use accounts only.
- Not testable: everything that rests on analysts. The data holds no analyst forecasts as they stood on a past date. Consensus growth, forward earnings yield and the consensus case are labelled "not tested". The downside case uses its fallback form (own 5-year rate in years 1 and 2).
- The hurdle needs a bond yield on each past date. The EODHD series goes back to 2000 or earlier by country, so the US series is reachable. The backtest uses the reporting currency's series where one is mapped.

**Look-ahead and survivorship limits, said plainly.**
- Accounts are the provider's restated record, not the figures first published. The filing-date mode limits which years are visible by when they were filed. It works only for companies whose accounts come from EODHD with filing dates. Others fall back to period end plus 90 days, counted and reported.
- The company list is today's market index. Companies that were delisted, merged or went bankrupt are not in it. So the gate can never be called "proven": the companies that failed and vanished are not in the data, and a test that removes failing companies is flattered most by their absence. The page says "no edge shown in a test that cannot see failures", not "proven".
- Prices come from yfinance, adjusted for splits and dividends.

**Baseline kept.** The existing results (`backtests/`, the track record column and the badges) stay unchanged and are labelled the relative baseline. The new results go in a separate directory and a separate summary file.

## k. The judge panel

The judges today are asked for BUY, HOLD or SELL from a pack with no lens opinions. They keep the same pack and the same stability rule (ask twice, keep both if they differ). Only the question changes, so that they are asked what the maths is asked. Proposed wording:

"Using only the evidence pack and anything you know about the company, which you must label, would a person who buys this company at today's price and holds it for ten years more likely than not earn more than the ten-year government bond yield of the company's currency plus 5 points a year? Answer YES, NO or ABSTAIN, with a confidence and the one fact that decided it."

The ten years match the return in section f. The pack stays free of the code's verdict, hurdle result and return range. After the code has finished, the page shows whether the panel agrees with the verdict (YES against "clears hurdle", NO against "below hurdle").

Old judge records stay as they are, labelled as answers to the old question, and are not mixed into the new agreement figure. The bar in the judge panel document (12 months, 30 names) restarts for the new question.

## l. Build plan, and what stays byte-identical

### Inputs: what exists, what needs a new field, what needs a new fetch

| Input | Status | Notes |
|---|---|---|
| Net income, operating cash flow, total assets (accrual ratio) | Exists | Annual, period-matched. |
| Z'' inputs (current assets and liabilities, retained earnings, EBIT, total assets, total liabilities, book equity) | Exists | All annual statement lines are read. The score uses book equity, so the price-currency mismatch goes away. It is warning B. |
| F-score and its nine checks | Exists | Rule changes from "5 of 9 checks" to "all 9". |
| Operating profit and EBIT, annual | Exists | Newest first, dated in the as-of reader. |
| Free cash flow, operating cash flow, capital spending | Exists | Annual lists. |
| Depreciation and amortisation | Added (ABSOLUTE-GATE-1) | Read on both providers: yfinance row "Depreciation And Amortization", EODHD `Cash_Flow` key `depreciation` (both 38,534m for Microsoft FY2026). Needed for EBITDA and for capital spending over depreciation. |
| Change in working capital | Added (ABSOLUTE-GATE-2) | Read on both providers: yfinance row "Change In Working Capital", EODHD `Cash_Flow` key `changeInWorkingCapital` (both minus 4,895m for Microsoft FY2026). Cash from operations before working-capital changes = operating cash flow minus this line. |
| Diluted weighted-average shares, history | New field | The yfinance path already stores diluted earnings per share; the share line itself is not stored. The EODHD income statement block does not carry diluted shares (probed on KO.US, 2026-09-21), so that path would use the outstanding-shares history, which is a basic count. State which was used. |
| Cash runway | Exists as a reading | Triggers on cash flow before working capital at or below zero; gross cash over burn; 12 months excludes, 24 months warns. |
| Dividends paid, net income | Exists | Cover on net income is new arithmetic on existing lines. |
| Earnings power value | Exists | Unchanged. |
| Tax provision, pretax income | Exists | Used by earnings power value only. |
| 5- and 10-year earnings per share growth | Exists | 10-year from the EODHD long history; the yfinance fallback gives about 4 years. |
| Consensus earnings per share, this and next fiscal year | Exists | EODHD analyst data. Gaps outside the US. |
| Consensus "year ago" earnings per share | New field | The probed payload lists it; the parser does not keep it. Needed for the two-step consensus rate. |
| Consensus operating profit, revenue or long-term growth | Cannot be had | The probe lists only earnings per share fields. |
| Ten-year government bond yield, daily history | New fetch | EODHD bond series, probed: reachable on the current plan. Needs a currency-to-series table. |
| Exchange rates | Exists | Price pairs through the market data adapter, month-end series and current rate. |
| US 3-month bill history | New fetch | FRED DTB3 (needs a free key) or ^IRX (not checked). |
| Broad index total return | New fetch | A broad index fund through yfinance. |
| Historical share counts for past market value | New fetch | EODHD outstanding shares history. |
| Analyst forecasts as they stood on past dates | Cannot be had | Not stored. |

### Reconciliation: Microsoft's net cash

The saved run says net cash of $7.3bn (cash and short-term investments $64.5bn, total debt $57.2bn). Microsoft's 10-K for the year to June 2026 shows cash, cash equivalents and short-term investments of $76.8bn (76,843m) and borrowings of $40.3bn (current portion of long-term debt 9,227m plus long-term debt 31,067m).

- The tool counts debt as the balance sheet's own "Total Debt" row, not as borrowings alone. For June 2026 that row is $56.8bn (56,826m), which equals borrowings of 40,294m plus the long-term operating lease liability line of 16,532m. So the tool's debt includes the long-term operating lease line, and excludes the larger lease total the vendor's headline adds, which the page shows separately.
- The saved run's $64.5bn and $57.2bn come from a later balance sheet. The same run states a trailing P/E for the twelve months to September 2026, so the September 2026 quarter was already reported. That date is inferred, not read from a filing.
- On the 10-K at June 2026 the net position is $76.8bn minus $56.8bn = $20.0bn net cash on the tool's definition, or $76.8bn minus $40.3bn = $36.5bn counting borrowings only.
- The page will state the definition: "debt as the tool counts it: borrowings plus the long-term operating lease line, from the latest balance sheet", with its date.

### Phase 1: the gate

- Built as ABSOLUTE-GATE-1 and amended by ABSOLUTE-GATE-2 and ABSOLUTE-GATE-3: a versioned gate definition (`gates/absolute_gate_v2.yaml`; version 1 stays on disk as the record), a gate result type, the page sections, the calibration tool. Four exclusions (accrual ratio, solvency, F-score, runway) and four warnings (highly leveraged, distress score, dividend cover, runway between 12 and 24 months).
- Calibrated on the cohort union (section b). No change to any vote.

### Phase 2: valuation, hurdle and return

- Step 1: the bond series probe. Done for this page (section g): the US ten-year answers HTTP 200 on the current plan. The probe should be repeated for each mapped series before it is relied on.
- Depreciation field, diluted shares field, year-ago consensus field, bond series fetch with storage, free cash flow per share, the implied growth solver, the four cases, the return range and the verdict.

### Status, 4 Nov 2026: phases 1 to 3 are built

- **Phase 1** (gate): ABSOLUTE-GATE-1 to 3, merged (PRs #199, #200).
- **Phase 2** (valuation, hurdle, return): ABSOLUTE-VALUE-1 and 2, merged (PR #201). The bond series are in `data/bond_series.yaml`; the Swiss
  ten-year is `SW10Y` (`CH10Y` is Chile).
- **Phase 3** (report and UI): ABSOLUTE-PAGE-1. The company page is rebuilt in the section i order; the absolute verdict is the verdict of record
  on that page only; the lens majority and agreement table moved into the workings as "Relative view (peer ranks): no longer votes"; the judges
  answer the section k question (YES, NO or ABSTAIN) and the old judge records are kept apart; the summary's facts pack and checker are re-pointed;
  the sweep gained the section 3 rules. The Analyse tab's cohort and list runs are unchanged.
- **Phase 4** (backtest): not started. Nothing yet shows that a company reading "clears hurdle" went on to do better, and the page says so.
- **Decision 17 revised** by ABSOLUTE-PAGE-1's last commit: see the note under decision 17.
- **ABSOLUTE-PAGE-2 (4 Nov 2026):** own-record rule for "clears" (decision 28), pre-working-capital base in definition version 3 (decision 29), judge scoring (decision 30).

### Phase 3: report and UI

- The page order in section i, code-written answer and story in new templates, the gate table, the price test, the return range. Judge question change (section k). Old lens table moved into the folded workings.
- The sweep gains rules: no lens id or column name in the first three sections, and none of the words "expected return", "target" or "proven".

### Phase 4: backtest

- New runner beside the existing one, separate output directory, the three forms, the three comparisons, the 1- and 3-year holds, and the existing luck and drop-best code.

### Stays byte-identical in every phase

- Every file under `backtests/` and the track record table.
- Every strategy file, and the outputs of the existing lenses (saved ranks and votes).
- Every saved verdict (`verdicts/`) and saved report (`reports/`).
- The results of the Microsoft evidence runs under `runs/`, except for files written by the new sections, which are additive.
- The full suite stays green after each phase. New behaviour ships with tests anchored to the worked example in sections e and f.

## m. Decisions

Each decision with its source or "judgment". The former open questions 1 to 13 are answered first.

1. Worst-year return on capital: context, not a gate. Judgment (the cyclical reasoning in section b).
2. Banks and insurers: not covered in this release. Decided by the owner.
3. Distress score: Z'' without constant, a warning below 1.10, never an exclusion; grey to 2.60. [Altman 2000; Altman 2002; Altman 2017 slides for the 2.60 derivation.] Owner's decision in ABSOLUTE-GATE-2, by principle (a).
4. Bond and margin: the reporting currency's ten-year yield plus 5.0 points. [Kroll 2026; Fernandez et al. 2025; EODHD bond series.]
5. Return formula: the ten-year equation, flat after year 10. [Mauboussin and Callahan 2014; Damodaran terminal value rule.] Yield-plus-growth is not used, because it assumes growth continues for ever.
6. A middle verdict state: none. Four states, with "n of 4 cases clear". Judgment.
7. Cost of capital in earnings power value: unchanged at 9%, labelled an enterprise value at the weighted cost of capital, context only. [Greenwald via Kellogg lecture notes; Wall Street Prep.]
8. Capital spending: no base switch. The base is free cash flow per share (lower of latest and three-year average); investment intensity prints as context with a top-fifth warning. [Titman, Wei and Xie.] The 1.5 times rule is dropped.
9. Judges: they do not see the hurdle or the growth rates. Judgment.
10. Thresholds: each is listed under "Judgment calls" below or carries a source in section b.
11. Growth above 15%: replaced by a mark above 18% a year. [Chan, Karceski and Lakonishok 2003, Table I.]
12. Accrual rule: kept, excluded above 0.10. The owner's fixed line replaces the measured top-tenth boundary, which on the cash-flow form was 0.0054 (median minus 0.049, depreciation added back). [Sloan 1996 design; Hribar and Collins 2002; gate version 1 calibration.] The evidence is mixed (Lev and Nissim against Mohanram), so it is a check on accounts and not a source of return.
13. Backtest bar: 2 points a year over the equal-weight industry basket, luck under 5%, winning years reported. Judgment, with the drought reason from Arnott et al. 2021.

New decisions.

14. Valuation base: free cash flow per share, the lower of the latest year and the three-year average. Judgment.
15. Hurdle margin: 5.0 points over the bond. [Kroll 2026; Fernandez et al. 2025.]
16. Bond source: EODHD bond series through the end-of-day endpoint, probed; US fallbacks ^TNX and FRED DGS10. [EODHD Macroeconomic Data API; FRED.]
17. Downside case: two years at consensus then a straight line to the bond yield. Shape is judgment; reasons from Chan et al., Damodaran and Mauboussin.
    **Revised in `valuation/absolute_value_v2.yaml` (ABSOLUTE-PAGE-1, last commit):** years 1 and 2 use the LOWER of the consensus rate and the own 5-year
    rate (whichever exists if one is missing). When analysts forecast 50% a year, two years at that rate is not a downside. Version 1 stays on disk as the
    record; Microsoft's downside case then starts at 17.2% (its own 5-year rate) and returns 4.6%, the same as the backtest form.
18. Capital spending measure: capital spending over depreciation printed, plus abnormal investment. [Titman, Wei and Xie; Wall Street Prep.]
19. Leverage: one warning, "highly leveraged", with two readings: net debt over EBITDA above 6 times, or cash flow over debt below 12% [S&P 2009 Table 2 and 2024 tables 17 to 19; Damodaran January 2026; Moody's utilities method]. Dividend cover: a warning only below 1.0 (not covered); 1.5 to 2.0 is printed context for thin to comfortable [Wall Street Prep; Stockopedia; AJ Bell]. Both are warnings, never exclusions.
20. F-score: excludes 0 or 1, and only when all nine checks can be computed. [Piotroski 2000, Table 3; figures from the owner's earlier fetch, this pass's retry was refused.]
21. Cash runway: under 12 months excludes; 12 to 24 passes and its own warning fires. [FASB ASU 2014-15 for the 12; BiopharmaWatch and RA Capital for the context.]
22. Industry not-covered rule: an industry excluded wholesale (more than half of its names) is marked "not covered by the gate in this release", and thresholds are never loosened. Judgment.
23. Solvency rule: exclusion 2, cash from operations before working-capital changes over gross total debt, excluded below 5% (Moody's B band and below); net cash and debt at or below zero pass first; cash flow at or below zero is "not evaluated: loss-maker, see runway"; a negative ratio never excludes on its own. [Moody's utilities method, CFO pre-WC / Debt grid.] Owner's decision in ABSOLUTE-GATE-2.
24. Runway trigger: cash from operations before working-capital changes at or below zero, the same measure as solvency, so every company falls under exactly one of the two rules; a year with free cash flow not negative passes as "funded by working capital this year"; the old "not enough data" outcome for that case is removed. Owner's ruling in ABSOLUTE-GATE-3.
25. Principle (a): an exclusion rule that removes more than one name in ten at calibration is the wrong instrument and becomes a mark until re-specified. The calibration reports a breach and never changes a rule. Owner's principle.
26. Principle (b): the gate excludes only for broken accounts or solvency; everything else is a mark the reader sees. Owner's principle.
27. Principle (c): size is not a gate question. The absolute path runs for any company, and one below the backtested range is labelled "outside the tested range", never blocked. Owner's principle.

28. Own record required (ABSOLUTE-PAGE-2): "clears hurdle" needs at least one own-record case (own 5-year or own 10-year growth) available and clearing; with none the verdict is "not enough data: no record of the company's own growth". Owner's ruling after the Ford and Korea Gas pages of 2026-11-04.
29. Base before working-capital changes (ABSOLUTE-PAGE-2, `valuation/absolute_value_v3.yaml`): cash from operations before working-capital changes minus capital spending, per share, lower of latest and three-year average. Same measure as decision 23. A yield above 25% is marked. Owner's ruling (the Korea Gas 107% yield).
30. Judge scoring (ABSOLUTE-PAGE-2): a YES is right when the share's total return over the window exceeded the bond yield of its currency plus 5 points a year, pro-rated to the window; a NO when it did not; ABSTAIN is not scored. Old BUY/HOLD/SELL rows keep the old scoring.

### Judgment calls, listed together

- The lower-of base rule (latest year or three-year average).
- Three-year averages for dividend cover and the free cash flow base.
- The 24-month line in the runway warning, and the 5% solvency line (the edge of Moody's B band, applied to every industry).
- The top-fifth cut for investment intensity. (The 0.10 accrual line is the owner's.)
- The downside case shape.
- One hurdle for all companies.
- The pegged-currency mapping of bond series.
- The ten-year horizon of the judges' question.

## n. Where this design disagrees with the plan

- **The verdict rests on free cash flow per share and the price, not on earnings yield against a bond yield.** The plan's three measures are now context or are replaced. After-tax operating profit is not cash to the owner when growth needs reinvestment.
- **Expected return is not "starting yield plus growth".** The simple sum assumes growth continues for ever and contradicts the implied-growth calculation. The design uses one equation for both.
- **The holding period is not 3 to 5 years.** The return is a long-run ten-year owner's return. A shorter holding depends on the selling price, which this data cannot predict.
- **Worst-year return on capital is context, not a gate.** Cyclicals would be excluded at the bottom of the cycle.
- **Only four exclusions.** Leverage and dividend cover are warnings, because heavy borrowing is normal in some industries and a dividend cut is a cost, not a failure.
- **The hurdle margin is 5.0 points, not 4.0, and it is sourced.**
- **There is a fourth growth case, the downside case,** and "clears hurdle" means all available cases clear.
- **The distress score changes to the version with book equity, and is a warning.** The one in use abstains for any company whose price and accounts are in different currencies.
- **Banks and insurers are not covered.**
- **No fixed fallback hurdle.** No bond series means "not enough data".
- **The consensus case is untestable and labelled so.**
- **The backtest has three forms and the pass bar changes.** Winning years are reported, not required. The gate is never called "proven".
- **Brief figures that did not hold as written** are listed in the Sources section.
- **ETF lenses stay.** The plan does not mention funds.

## o. What the evidence says to expect

Every figure in this section was read on the page or in the paper named beside it. Where a figure from the brief could not be confirmed, it is left out and listed in the Sources section.

### Published rules fade, and some never held

- McLean and Pontiff (2016) studied 97 variables shown to predict stock returns. Portfolio returns are 26% lower out of sample and 58% lower after publication.
- Hou, Xue and Zhang (Review of Financial Studies, 2020): 65% of the 452 anomalies in their library fail the ordinary significance hurdle of an absolute t-value of 1.96, with microcaps mitigated by large-exchange breakpoints and value-weighted returns. At the stricter hurdle of 2.78 the failure rate is 82%. Their earlier 2017 NBER draft gives different counts (447 anomalies, 64%), so the published numbers are used.
- The other side: Jensen, Kelly and Pedersen (2023) find a baseline replication rate of 56.9% on US returns, rising to 84.9% under their own method, across 153 factors in 93 countries. They say part of the gap to Hou, Xue and Zhang comes from weighting: capped value weights add 8.5 points to their rate. The two papers disagree on how much survives, so this page does not pick a single share.
- Microcaps and equal weighting inflate published results. Hou, Xue and Zhang write that microcaps are on average only 3% of the market value of the US universe but about 60% of the number of stocks, and that "anomalies in microcaps are more apparent than real". Our cohort size floors of $1bn to $10bn and the $5bn tested-range limit on the company page keep the smallest companies out. Our backtest is still equal-weight, so this lowers the risk and does not remove it.
- Accruals: Mohanram's 2011 draft (the file fetched is dated September 2011) reports that the accruals anomaly produced excess returns consistently for over four decades until 2002 and since then has apparently disappeared. It credits part of the decline to analysts' cash flow forecasts. Lev and Nissim reach the opposite conclusion: the anomaly persists and its magnitude has not declined. Both are in the sources. The accrual rule stays as an accounting-quality check.

### Quality and profitability hold up better

- Asness, Frazzini and Pedersen (2019), quality minus junk: the factor delivers positive returns in 23 out of 24 countries studied, in a sample of 54,616 stocks from June 1957 to December 2016.
- Walkshäusl (2020), Journal of Asset Management: over 2000 to 2018, high-FSCORE firms outperform low-FSCORE firms by about 10% per year across developed non-US and emerging countries (9.9% developed, 12.0% emerging in the body). The count of 35 markets in the brief was not found and is not used. The paper also cites Piotroski and So (2012, Table 1) for 10.03% a year for the US F-Score premium.
- Novy-Marx (2013): profitability, measured as gross profits over assets, has roughly the same power as book-to-market in predicting the cross-section of average returns, and controlling for it raises the performance of value strategies, especially among the largest, most liquid stocks.
- Fama and French (2013 draft): profitability and investment add to the description of average returns, and the value factor is redundant in the five-factor model. In plain terms, cheapness alone explains less than its reputation suggests, which is one reason the gate comes first.

### Cheapness works, with long droughts

- Arnott, Harvey, Kalesnik and Linnainmaa (2021): value, as measured by the Fama-French book-to-market factor, has underperformed growth investing since 2007, producing a drawdown of 55% as of mid-2020.
- Swedroe (2018): the value premium averaged 4.8% a year over 1927 to 2017 and was negative in 22% of five-year periods, 14% of ten-year periods and 6% of twenty-year periods [Swedroe 2018; fetched 3 Nov 2026 in the owner's earlier pass; this pass's retry was refused]. This is why the backtest reports winning years and does not require them.

### Cheap plus quality, in backtests

- Piotroski and So (2012) is the base paper for combining cheapness with strength of fundamentals. Its abstract carries no return figures. As reported by Alpha Architect (secondary), for 1972 to 2010: plain value against glamour earned 10.5% a year and was positive in 27 of 39 years; the F-score-conditioned version earned 20.8% a year and was positive in 35 of 39 years [Alpha Architect, Value premium, risk and mispricing; fetched 3 Nov 2026 in the owner's earlier pass; this pass's retry was refused].
- Gray and Carlisle, Quantitative Value, reviewed by the CFA Institute (16 May 2013): the full model generates a return of 17.68% a year. The review says the authors claim it outperformed the Magic Formula by 3.74% a year from 1974 through 2011. The comparison is the Magic Formula, not an index.
- Carlisle, Latticework interview, 18 December 2024: "The way we tested, it was about 16.2% compound over the full dataset (1972 to 2017)", against an equal-weight S&P 500 total return of about 10.5%. The visible text does not say these are Magic Formula figures. The second retest in the brief (11.4% against 8.7%, 2003 to 2015) was not found there and is not used.

### Live results

- QVAL (Alpha Architect), factsheet through 30 September 2026, return at net asset value: 1 year 26.84%, 3 years 18.12% a year, 5 years 13.33% a year, 10 years 11.56% a year, since the fund began on 21 October 2014 9.09% a year. The market-price figures for 10 years and since inception are 11.54% and 9.11%.
- iShares pages, 30 September 2026, 10-year annual return on the benchmark row: S&P 500 15.32%, and 11.42% for the Russell 1000 Value fund's benchmark row (the page fetched does not name the index).
- So over ten years to 30 September 2026 the fund returned 11.56% against 15.32% for the S&P 500 and 11.42% for the value benchmark. It matched the value benchmark and lagged the market by about 3.8 points a year. That arithmetic is ours.
- QVAL's process, from its investment case: start from the 1,500 largest US stocks, remove low-volume, red-flag, bad-price-action and extreme-valuation names, rank on EBIT over total enterprise value, then remove low financial-strength scores. That is close to form 2 in section j. The document is dated 31 March 2025 and shows a different start date, so none of its returns are used.
- AAII's Magic Formula screen, as listed on 30 October 2026: 2022 minus 21.3%, 2023 13.5%, 2024 17.2%, 2025 17.9%, 2026 to date minus 13.5%, "Annualized Total" 6.4% (the window is not stated on the page). One screen as followers run it, not a fund.
- Greenblatt, Morningstar column, reprinted by Validea on 16 January 2012, for May 2009 to April 2011 (the period is from a secondary page): self-managed accounts had "a cumulative return of 59.4% after all expenses", professionally managed accounts "earned 84.1% after all expenses", and the S&P 500 was "up 62.7%" [Validea reprint; fetched 3 Nov 2026 in the owner's earlier pass; this pass's retry was refused; the address now redirects to the Validea home page, and an archive copy read in the earlier verification pass did not contain these figures]. Reading: the edge was discipline, not the formula.

### Tilts in practice

- Fitzgibbons and co-authors, "Long-Only Style Investing: Don't Just Mix, Integrate", as summarised by CXO Advisory (the AQR affiliation is not stated on the page): against the value-weighted MSCI World, February 1993 to December 2015, standalone value and momentum portfolios outperform by an average annualised net 1.7%, with tracking errors just under 8%. The mixed and integrated combinations outperform by an average net 2.5%, with tracking errors about 4%. The integrated combination is reported at 3.6%.
- Research Affiliates (2017), over 1991 to 2016 in live funds: "Well over half of the factor return for market beta and for value (defined as HML) disappears" [Research Affiliates 2017 via Advisor Perspectives; fetched 3 Nov 2026 in the owner's earlier pass; this pass's retry was refused].

### Implied returns

- Li and Mohanram (the file fetched is dated 2013), regression of future realised returns on an implied-return measure, where 1 would be perfect: the HVZ model 0.209, 0.192 and 0.087 for years one, two and three ahead (the last not significant); the AR model 0.584, 0.498 and 0.576; the RIV model 0.652, 0.574 and 0.594. So "about 0.2" holds for the weakest of three models; the other two are about 0.5 to 0.65.
- Lee, So and Wang (the June 2017 working paper; the published 2021 version was not fetched): among the measures tested, only a composite and one other consistently perform better than a trivial estimate, that is, a fixed constant. Four of the five individual implied-cost-of-capital measures underperform a constant in the cross section, and three of the five outperform every non-implied measure in time series.
- Li, Ng and Swaminathan (2013): aggregate implied cost of capital strongly predicts future excess market returns at horizons from one month to four years. That is a market-level, time-series result, and not evidence for ranking stocks.

### Sector and industry

- AAII (June 2022): Greenblatt also excludes financials and utilities because of their unique financial structures. This design treats banks and insurers as "not covered" (section c). Utilities are not excluded here. The leverage warning will flag many of them; check at calibration.
- Novy-Marx (2013) uses variables demeaned by the 49 Fama-French industries and finds industry-adjusted gross profitability has more power than the straight measure. So comparisons are made within an industry, and no bet is placed on cheap industries.
- Morningstar 2017, Bryan and McCullough, "The Impact of Industry Tilts on Factor Performance", December 1966 to November 2016: stock-level value "returned 2.36% annualized" and industry-level value was "negative 0.32% annualized" [Morningstar 2017; fetched 3 Nov 2026 in the owner's earlier pass; this pass's retry was refused; the address now redirects to a home page].

### The realistic target

One to three points a year over the equal-weight industry basket, over ten years, with droughts of several years. That range is a judgment built on the fetched figures above: net edges of 1.7 and 2.5 points for tilts against a market index, after a published rule has typically lost 26% of its return out of sample and 58% after publication. It is not itself a cited result.

A backtest of ours showing far more than that is to be treated as an error until it is explained: look first for look-ahead, survivorship, microcaps, a data fault, or a single stock.

## p. Sources

Status is what happened in this pass. "Fetched" means the page or paper was read and the figures used were checked against it. Several documents were PDFs that the page-reading tool returned as raw bytes; their text was extracted from saved copies.

### Sources for sections b to n

Fetched and used:

- Sloan 1996, The Accounting Review: https://www.cuhk.edu.hk/acy2/workshop/June2009Wasley/1996TAR).pdf
- Hribar and Collins 2002 (abstract; it says balance-sheet accruals add measurement error, and does not use the words "preferred"): https://iro.uiowa.edu/esploro/outputs/journalArticle/Errors-in-Estimating-Accruals-Implications-for/9984380479802771
- Dechow and Schrand 2004, CFA Institute (accruals as earnings less cash from operations, in deciles; no single formula or threshold): https://rpc.cfainstitute.org/-/media/documents/book/rf-publication/2004/rf-v2004-n3-3927-pdf.pdf
- Lev and Nissim, The Persistence of the Accruals Anomaly: http://www.columbia.edu/~dn75/The%20Persistence%20of%20the%20Accruals%20Anomaly.pdf
- Ball, Gerakos, Linnainmaa, Nikolaev 2016 (Table 1: accruals 75th percentile 0.016, 99th 0.235; balance-sheet measure over lagged assets): https://www.anderson.ucla.edu/documents/sites/about/clubs/asam/6-Accruals-cash-flows-and-operating-profitability.pdf
- Altman 2000 (Z'' coefficients; constant +3.25 for the emerging market model; no Z'' zones): https://moodle2.units.it/pluginfile.php/322514/mod_resource/content/0/Zscores.pdf
- Altman 2002 ("Z''-Scores below 1.10 indicate a distressed condition", form without constant): https://archive.nyu.edu/bitstream/2451/26485/2/02-41.pdf
- Altman 2017 Hofstra slides (formula with constant 3.25; zones 4.35 and 5.85; original Z zones 1.8 and 2.99): https://www.hofstra.edu/pdf/community/bdc/breslin/breslin-evolution-of-altman-z-score.pdf
- Altman 2018, A fifty-year retrospective (formula with constant; applies to non-manufacturers and non-US firms; no Z'' zones): https://mebfaber.com/wp-content/uploads/2020/11/Altman_Z_score_models_final.pdf
- Moody's, Regulated Electric and Gas Utilities methodology, exhibit copy (Baa band 13% to 22% in the standard grid; garbled text, rows rebuilt from band edges): https://puc.sd.gov/commission/dockets/electric/2026/EL26-003/Vol2Section6/Exh1TStevens.pdf
- Damodaran, Debt to EBITDA by sector, January 2026: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/dbtfund.html
- Damodaran, Margins by sector, January 2026 (fetched; not used for any figure): https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/margin.html
- Damodaran, Dividend fundamentals by sector, January 2026: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/divfund.html
- Damodaran, Country risk premiums, January 2026: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html
- Damodaran home page (implied premium 4.41% on 1 November 2026; 4.20% as the previous month): https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm
- Damodaran, terminal value rule: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/terminalvalue.htm
- Fernandez, Garcia de la Garza, Fernandez Acin 2025 survey: https://breesept.tulane.edu/wp-content/uploads/sites/18/2026/03/Rf-and-Rm-Rf-2025.pdf
- Kroll Cost of Capital Inputs at Year-End 2025 (30 January 2026): https://edge.sitecorecloud.io/krollllc17bf0-kroll6fee-proda464-0e9b/media/Cost-of-Capital/kroll-cost-of-capital-updates-jan26-executive-summary.pdf
- EEI Q4 2025 Dividends Summary: https://www.eei.org/-/media/Project/EEI/Documents/Issues-and-Policy/Finance-And-Tax/Quarterly-Financial-Updates/2025_Q4_Dividends_Summary.pdf
- Wall Street Prep, Dividend Coverage Ratio (below 1.0 insufficient; above 2.0 the minimum floor): https://wallstreetprep.com/knowledge/dividend-coverage-ratio
- S&P, Criteria Methodology: Business Risk/Financial Risk Matrix Expanded, 27 May 2009 (Table 2, the six bands; read from this copy on 3 Nov 2026): https://www.efis.psc.mo.gov/Document/Display/44676
- FASB ASU 2014-15 ("within one year after the date that the financial statements are issued"): https://storage.fasb.org/ASU%202014-15.pdf
- RA Capital on going-concern clauses: https://www.racap.com/rapport/going-concern-clauses-not-always-concerning
- BiopharmaWatch, cash runway (18 to 24 months; the brief's 12 to 24 is not in the text): https://www.biopharmawatch.com/learn/biotech-cash-runway-and-dilution
- EODHD Macroeconomic Data API (GBOND): https://eodhd.com/financial-apis/macroeconomic-data-api
- EODHD US Treasury rates API (beta): https://eodhd.com/financial-apis/us-treasury-ust-interest-rates-api-beta
- FRED DGS10: https://fred.stlouisfed.org/series/DGS10 (4.94 for 30 October 2026, not 4.93) ; DTB3: https://fred.stlouisfed.org/series/DTB3 (3.99) ; API: https://fred.stlouisfed.org/docs/api/fred/series_observations.html (key required)
- Yahoo Finance ^TNX history, 1962 window (rows from January 1962, includes Jun 22, 1962 at 3.9000): https://finance.yahoo.com/quote/%5ETNX/history/?period1=-252460800&period2=-157766400&interval=1mo ; ^IRX, same window (rows from January 1962): https://finance.yahoo.com/quote/%5EIRX/history/?period1=-252460800&period2=-157766400&interval=1mo
- Chan, Karceski, Lakonishok 2003 (Table I; the median reading depends on a column alignment that had to be reconstructed from extracted text): https://www.lsvasset.com/pdf/research-papers/Level+Persistence_of_Growth_Rates_FINAL.pdf
- Mauboussin and Callahan 2014 (about 8 years, 5 to 15 across industries, attributed to Olsen and called a competitive advantage period): https://strawman.com/member/uploads/objects/a9/41159492407c5bf57c2e67861056bed2decf7d.pdf
- Titman, Wei, Xie, Capital Investments and Stock Returns (NBER w9951): https://www.nber.org/papers/w9951.pdf
- Cooper, Gulen, Schill 2008 (secondary summary; fetched, not used for any figure): https://quantpedia.com/strategies/asset-growth-effect
- Wall Street Prep, Growth vs Maintenance Capex (writes depreciation over capital spending; weak): https://www.wallstreetprep.com/knowledge/growth-capex-vs-maintenance-capex/
- Greenwald EPV at WACC, Kellogg lecture notes: https://kellogg.northwestern.edu/faculty/jagannathan/teaching/practicum/2006/files/Week3/VI_Lecture_3.pdf ; Wall Street Prep EPV: https://www.wallstreetprep.com/knowledge/earnings-power-value-epv
- Microsoft 10-K FY2026, EDGAR (R2, R4, R6; all briefed lines matched): https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/R2.htm ; press release: https://www.microsoft.com/en-us/investor/earnings/fy-2026-q4/press-release-webcast
- CFO Dive, 30 July 2026 (data centers and office buildings, 15 to 25 years from the start of fiscal 2027; not server and network equipment as the brief said): https://www.cfodive.com/news/microsoft-holds-line-ai-spending-plans/826648/

Fetched 3 Nov 2026 in the owner's earlier pass, figures used; this pass's retry was refused:

- Piotroski 2000, Table 3 (low group 0 or 1, high group 8 or 9; returns 0.134 and minus 0.096; sample the highest book-to-market quintile): https://test2.ivey.uwo.ca/media/3775523/value_investing_the_use_of_historical_financial_statement_information.pdf (HTTP 403)
- Damodaran, Equity Risk Premiums 2026 edition, Table 3 (1928 to 2025 arithmetic means 11.85%, 3.41%, 4.82%; premiums 8.44% and 7.03%): https://ripuc.ri.gov/sites/g/files/xkgbur841/files/2026-05/DPUC%20Attachment%202%20to%20RIE%202-1(e).pdf (HTTP 403)

Fetched in this pass:

- Altman, Iwanicz-Drozdowska, Laitinen, Suvas 2017, abstract (prediction accuracy approximately 0.75; above 0.90 with country-specific estimation), vlex copy: https://international.vlex.com/vid/financial-distress-prediction-in-855618057 ; the SSRN page also listed refused: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2536340

Listed in the brief but not usable, and why:

- S&P Global Ratings, Corporate Methodology 2024 (tables 17 to 19: highly leveraged above 5, 5.5 and 6 times; paragraph 123): fetched 3 Nov 2026 per the owner, whose figures are used; this pass's retry was refused (Akamai access denied, then HTTP 403): https://www.maalot.co.il/Publications/MT20240412122958.PDF
- Stockopedia, Dividend Cover (less than 1.5 a danger of a cut; more than 2 healthy): fetched 3 Nov 2026 per the owner, whose quotation is used; this pass's retries were refused with HTTP 403: https://www.stockopedia.com/ratios/dividend-cover-rolling-5130
- AJ Bell Dividend Dashboard Q4 2025 (2.00 times comfort; FTSE 100 1.99 times; top ten yielders 1.22 times "looks skinny"): fetched 3 Nov 2026 per the owner, whose quotations are used; this pass's retries returned HTTP 404: https://www.ajbell.co.uk/group/sites/ajbell.co.uk/files/AJ%20Bell%20Dividend%20Dashboard%20Q4%202025.pdf
- IAS 1 paragraph 26: a subscription page was returned: https://www.ifrs.org/content/dam/ifrs/publications/pdf-standards/english/2021/issued/part-a/ias-1-presentation-of-financial-statements.pdf
- EODHD GBOND symbol list: answers "Unauthenticated" without a token, so it was not read: https://eodhd.com/api/exchange-symbol-list/GBOND

Differences between the brief and the sources, for the record:

- FRED DGS10 for 30 October 2026 is 4.94, not 4.93. EODHD's close is 4.934, which rounds to the briefed 4.93. This page uses the EODHD figure.
- Kroll's risk-free rate is the higher of 3.5% or the spot 20-year Treasury yield, not simply "normalised".
- The Damodaran implied premium of 4.20% is the "previous month" line; the current line is 4.41% on 1 November 2026.
- FASB says "one year", not "12 months". BiopharmaWatch says 18 to 24 months, not 12 to 24.
- Wall Street Prep's capital spending ratio is written depreciation over capital spending.
- The Microsoft useful-life change is for data centers and office buildings, not servers.
- Chan et al.: the abstract's "no persistence" refers to earnings growth; the "past five years" wording was not verified.
- Mauboussin and Callahan: an average of about 8 years for the competitive advantage period (Olsen), and the 5 to 15 year spread is across industries.
- The Microsoft saved run's net cash of $7.3bn: see section l for the reconciliation.

### Sources for section o (from the earlier brief; fetched in the previous pass)

Fetched and used:

- Hou, Xue, Zhang 2020, Replicating Anomalies (abstract): https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html ; 2017 NBER draft: https://www.nber.org/papers/w23394.pdf
- McLean and Pontiff 2016: https://Www.Gwern.net/doc/economics/2016-mclean.pdf
- Jensen, Kelly, Pedersen 2023 (NBER WP 28432): https://nber.org/system/files/working_papers/w28432/w28432.pdf
- Fama and French five-factor (2013 draft): https://static1.squarespace.com/static/5e6033a4ea02d801f37e15bb/t/5f5a8f4912595c0d5f5af0f4/1599770442385/FF_Five_Factor.pdf
- Novy-Marx 2013 (the file is dated June 2012): https://static1.squarespace.com/static/5e6033a4ea02d801f37e15bb/t/5f5a8f2dd418ba7246912fa0/1599770413829/Novy_marx_OSoV.pdf
- Asness, Frazzini, Pedersen 2019, Quality Minus Junk: https://research-api.cbs.dk/ws/portalfiles/portal/60211462/lasse_heje_pedersen_et_al_quality_minus_junk_publishersversion.pdf
- Mohanram, accruals (a September 2011 draft, "The Role of Analysts' Cash Flow Forecasts in the Decline of the Accruals Anomaly"): https://www.stern.nyu.edu/sites/default/files/assets/documents/con_033123.pdf
- Arnott, Harvey, Kalesnik, Linnainmaa 2021 (abstract): https://ideas.repec.org/a/taf/ufajxx/v77y2021i1p44-67.html
- Piotroski and So 2012 (abstract only, no return figures): https://ideas.repec.org/a/oup/rfinst/v25y2012i9p2841-2875.html
- Walkshäusl 2020: https://epub.uni-regensburg.de/44019/1/Walksh%C3%A4usl2020_Article_PiotroskiSFSCOREInternationalE.pdf
- Li and Mohanram, "Evaluating Cross-Sectional Forecasting Models for Implied Cost of Capital" (file dated 2013): https://accounting.unibocconi.eu/sites/default/files/files/media/attachments/Li%2520and%2520Mohanram%2520201320130717161038.pdf
- Lee, So, Wang (June 2017 working paper, "Evaluating Firm-Level Expected-Return Proxies"): https://www.abfer.org/component/edocman/?task=document.viewdoc&id=256&Itemid=
- Li, Ng, Swaminathan 2013, "Predicting Market Returns Using Aggregate Implied Cost of Capital": https://www.lsvasset.com/pdf/research-papers/LNS_agg_March2013.pdf
- CFA Institute review of Quantitative Value (16 May 2013): https://blogs.cfainstitute.org/insideinvesting/2013/05/16/quant-magic-a-book-review-of-quantitative-value-by-wesley-gray-and-tobias-carlisle
- QVAL factsheet (through 30 September 2026): https://funds.alphaarchitect.com/wp-content/uploads/compliance/etf/education/factsheet_qval.pdf ; QVAL process: https://funds.alphaarchitect.com/wp-content/uploads/2026/02/investment_case_qval.pdf
- iShares S&P 500 (IVV) and Russell 1000 Value (IWD), benchmark rows, 30 September 2026: https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf ; https://www.ishares.com/us/products/239708/ishares-russell-1000-value-etf
- Carlisle interview, Latticework, 18 December 2024 (partly paywalled): https://www.latticework.com/p/how-the-billionaire-contrarians-of
- AAII on Greenblatt's exclusions (29 June 2022): https://www.aaii.com/stockideas/article/17812-greenblatts-magic-formula-for-beating-the-market ; AAII screen performance (30 October 2026, secondary): https://www.aaii.com/stock-screens/performance
- AQR long-only value and momentum, via CXO Advisory (2016, secondary; the summary tool, not the raw page, was read): https://www.cxoadvisory.com/value-premium/integrating-momentum-and-value-stock-exposures

Fetched 3 Nov 2026 in the owner's earlier pass, figures used; this pass's retry was refused:

- Alpha Architect on Piotroski and So (secondary): https://alphaarchitect.com/value-premium-risk-mispricing/ (HTTP 403)
- Greenblatt column, Validea reprint of 16 January 2012: https://blog.validea.com/greenblatt-less-is-more-in-portfolio-management/ (now redirects to the Validea home page; an archive copy read earlier did not contain the figures)
- Research Affiliates 2017 via Advisor Perspectives: https://www.advisorperspectives.com/commentaries/2017/04/13/the-incredible-shrinking-factor-return (HTTP 403)
- Swedroe 2018: https://www.advisorperspectives.com/articles/2018/06/14/in-defense-of-the-value-premium (HTTP 403)
- Morningstar 2017, Bryan and McCullough: https://www.morningstar.co.uk/static/UploadManager/Assets/The_Impact_of_Industry_Tilts_on_Factor_Performance.pdf (redirects to a home page)

