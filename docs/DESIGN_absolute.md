# ABSOLUTE-1: from "better than its peers" to "will it make money over 3 to 5 years"

Status: design for approval, 2026-11-03. Nothing here is built. No code, strategy file, backtest result or data file changes with this page.

## a. The question Aristos answers now, and why it is being replaced

Today Aristos answers one question: is this company better than the companies around it? Nine lenses rank a company inside an automatic peer group. Eight of them vote BUY, HOLD or SELL, and one only marks (Forensic), and the verdict of record is the strict majority of the votes, or MIXED when none holds one.

That is a fair question for choosing among forty names. It is the wrong question for an owner deciding whether to buy one. A company can rank first in an expensive group and still lose money. The Microsoft run of 2026-11-01 shows it. Earnings Power Value voted BUY because Microsoft's price sat closer to its no-growth value than ten of thirteen peers, yet the same page says every company in that group is priced above that value. The BUY meant "least expensive of an expensive group".

The new question is: if I buy this company at today's price and hold it for 3 to 5 years, will it make money, and more than a safe alternative would? The doctrine does not change. The maths judges and the language model writes. No model touches the verdict, the gate, the valuation or the return figure.

What stays true from the old design: every figure comes from a deterministic tool, a missing input is "not evaluated" and never a failure, and published strategy files are never overwritten.

## b. The gate: who is excluded before any valuation

The gate asks one thing: can the accounts and the balance sheet be trusted, and will the company survive the holding period? Only accounting and solvency failures exclude. A cheap or expensive price never excludes. Each rule returns excluded (with the reason), passed, or not evaluated.

| Rule | What it measures | Proposed threshold | Reason and source | When data is missing |
|---|---|---|---|---|
| Earnings quality | Accrual ratio: (net income minus operating cash flow) divided by average total assets. The share of profit that has not arrived as cash. | Excluded above 0.10 (10% of assets). Set from the data before build: the top tenth of the market index. | Sloan (1996) showed the highest-accrual tenth of companies earn lower returns later. The 0.10 is a placeholder until the distribution is measured. No published fixed cut exists. | Not evaluated. Already computed today. |
| Distress | Altman Z-Score: five accounting ratios combined into one number. | Excluded below 1.81. Grey zone 1.81 to 2.99 passes with a note. | Altman (1968): below 1.81 is the distress zone, above 2.99 the safe zone. | Not evaluated. Today it abstains for any company whose share price and accounts use different currencies (ADRs, most non-US reporters). See open question 3. |
| Accounting health | Piotroski F-Score: nine yes/no checks on profit, leverage and efficiency, 0 to 9. | Excluded at 0 or 1. | Piotroski (2000) called 0 to 1 the low group and 8 to 9 the high group. | Not evaluated when fewer than 5 of the 9 checks can be run (today's rule). |
| Debt burden | Net debt divided by operating profit (EBIT). | Excluded above 5 times. Net cash always passes. | No single published cut. Rating agencies treat roughly 4 to 5 times operating profit before depreciation as aggressive borrowing. Operating profit after depreciation is smaller, so 5 times it is already the stricter line. | Not evaluated when debt or cash is missing. |
| Cash runway | For a company with negative operating cash flow: how long net cash lasts at last year's spending. | Excluded under 24 months. | The holding period is 3 to 5 years. A company that cannot fund two years of losses must raise money on someone else's terms. Judgment, not a published figure. | Applies only to cash-burning companies. Not evaluated when cash flow lines are missing. |
| Dividend cover | Dividends paid divided by the average of three years of free cash flow. | Excluded above 100% for a company that pays a dividend. | A dividend the business does not cover is paid from debt or savings. Convention, not a published cut. | Applies only where a dividend is paid. Not evaluated when free cash flow is missing. |

Source status: the author and year named in this table (Sloan 1996, Altman 1968, Piotroski 2000, and the rating agency remark) were written from memory in the first draft. None of those four papers was fetched for this page, and they are not in the Sources section. Verify each cut-off against the paper before phase 1 fixes it. Evidence that was fetched, about accruals and the F-Score, is in section o.

How the rules combine:

- Any one confirmed failure excludes the company, and the page names the rule and its figure.
- If nothing fails, the company qualifies only if at least three of the four always-applicable rules (earnings quality, distress, accounting health, debt burden) were evaluated, and at least one of distress or debt burden was among them. Otherwise the result is "not enough data", with the missing inputs listed.
- Cash runway and dividend cover add to the count only when they apply.
- A company with no operating profit and enough runway is not excluded. It passes the gate but cannot be valued by profit, so section d returns "not valued: no operating profit" and the result is "not enough data".

Worst-year return on capital and similar quality readings are shown as context, not as exclusions. Reason: a cyclical company has a trough. Oil producers earned well below their cost of capital in 2020, then recovered. A gate that reads the worst of five years would remove them at the bottom of the cycle, which is when the price is lowest and the future return highest. The reading is still printed, with its year, so a reader sees the trough. The same goes for gross profitability and the other Quality readings.

Open question 1 asks whether worst-year return on capital should be a hard gate instead.

## c. Banks and insurers

Earnings before interest and tax over enterprise value does not work for a bank. Interest is the bank's cost of goods, and its debt is raw material. The Altman Z-Score and the debt burden rule do not apply for the same reason. Accruals are also unreliable for a lender.

Two options:

1. A separate path built on the inputs the Financials lens already reads: price against book value and return on equity. The absolute question for a bank is whether its return on equity beats the return investors require, and what price-to-book that justifies. The three measures in section d would be replaced by two: earnings yield on equity (net income over market value) and a growth-implied version of it.
2. "Not covered", stated plainly on the page, with the Financials readings shown as context and no return figure.

Recommendation: option 2 in the first release. A bank path doubles the validation work and has no backtest yet. Option 1 can follow as its own design. Open question 2.

## d. The three valuation measures

All three compare the price with what the business earns. None uses a peer group. Inputs are in section l's input table.

**Hurdle.** The return the company must beat. Ten-year government bond yield plus a margin (section g). Written `h` below.

### Measure 1: earnings yield against the hurdle

Earnings yield is operating profit divided by what it costs to buy the whole business.

- Enterprise value (EV) = market value of shares + total debt minus cash and short-term investments. This is the existing calculation. Lease obligations are not in debt, and the page says so.
- Trailing earnings yield = latest-year operating profit (EBIT) / EV.
- Forward earnings yield = next-year consensus earnings per share / share price. This is on an equity basis, after interest and tax, so it is shown beside the trailing figure and never mixed with it. It is shown where analysts' forecasts exist, with its fiscal year named.
- Reading: trailing earnings yield minus the hurdle, in percentage points. Operating profit is before tax, so the comparison uses after-tax operating profit: yield after tax = EBIT x (1 minus tax rate) / EV, with the tax rate rule the Earnings Power Value already uses (the company's own average rate if it falls between 5% and 40%, otherwise a flat 25%).

### Measure 2: earnings power value against price

Earnings power value (EPV) is what the business is worth if its current profit never grows.

- Existing formula: normalised after-tax operating profit divided by a flat 9% cost of capital. Normalised profit is the average operating margin of up to five fiscal years times the latest year's revenue, taxed as above. At least three years are needed.
- Reading: EPV / EV minus 1. A negative number means the price already includes growth. A company at minus 64% is priced for a lot of it.
- The 9% is a fixed assumption for the whole universe, as today. In the new design the cost of capital in this measure should equal the hurdle `h`, so measure 2 and measure 3 use one rate. That is a change to the existing formula (open question 7); until it is approved the page shows EPV at 9% as it does today.
- Its assumption, in one line: no growth at all, ever. That makes it a floor, not a forecast.

### Measure 3: implied growth

Covered in section e.

### Warning: capital spending far above depreciation

Trailing profit and trailing cash flow both mislead when a company is spending much more on assets than it is writing off. Depreciation lags spending. As new assets age, depreciation rises and operating profit falls unless revenue keeps up. Free cash flow is already lower than profit. Microsoft is the case: operating cash flow was $182.9bn in the year to June 2026 and capital spending was $115.9bn, so free cash flow was $67.0bn against operating profit of $155.2bn.

Rule: show a warning when capital spending is above 1.5 times depreciation and amortisation in each of the last two fiscal years. The 1.5 is a judgment. A steady grower runs near 1.0 to 1.2. The threshold is checked against the market index distribution before build. When the warning is on:

- the page says in one sentence that trailing profit may overstate future profit;
- implied growth and the return range are computed on both bases, after-tax operating profit and free cash flow, and shown side by side;
- the headline uses the lower of the two (open question 8).

Depreciation is not read by the tool today (section l). The warning cannot be shown for the saved Microsoft run, so the worked example below uses only the capital spending and free cash flow already on the page.

## e. Implied growth: the exact calculation

The question: what yearly growth in earnings must this company deliver for ten years to pay a buyer today's price and earn exactly the hurdle?

**Base.** `B` = after-tax operating profit, the same normalised figure the EPV uses. Reason: it is before the financing mix, it matches the earnings yield, and it does not swing with one year's capital spending. When the capital spending warning is on, a second run uses latest-year free cash flow as `B`. Free cash flow is already after interest, so it is compared with the market value of shares, not EV; for a company near net-zero debt such as Microsoft the two are within 1%, and the page says which was used.

**Horizon.** Ten years of growth at a constant rate `g`.

**Terminal assumption.** After year 10, profit stays flat forever, valued as a perpetuity at the hurdle. This is the same no-growth assumption as the EPV, so measures 2 and 3 agree at `g = 0`. It is conservative: any growth beyond year 10 is a bonus not counted.

**The equation.**

EV = sum for t = 1 to 10 of [ B x (1+g)^t / (1+h)^t ] + [ B x (1+g)^10 / h ] / (1+h)^10

**Solved** by bisection between minus 10% and plus 40% a year. The right side rises steadily with `g`, so there is one answer. If no answer exists in that range the page says "not solvable" and shows no number.

**Compared with** three records, each a plain number:
1. the company's own earnings per share growth over 5 years (existing growth record);
2. the same over 10 years, from the long annual history (EODHD, with the shorter yfinance history as fallback);
3. two-year consensus growth: the compound yearly growth from last reported earnings per share to next fiscal year's consensus, where analysts' forecasts exist.

Earnings per share growth, not operating profit growth, is used for all three, because the 10-year history and the consensus both come as earnings per share and no operating profit forecast is available. This mixes a per-share figure with a company-level base. It is acceptable only while buybacks and share issuance are small, and the page says it is an approximation.

### Worked example: Microsoft, run of 2026-11-01

Inputs from `runs/2026-11-01T15-15-08Z_company_check_MSFT`:

- Operating profit, year to June 2026: $155.2bn.
- Enterprise value: about $3.62tn. It is not printed on the page; it is the value that reproduces the page's earnings power value reading of minus 64.3%. Market cap on the page is $3.67tn and net cash is $7.3bn.
- Tax rate: flat 25% (illustrative). After-tax operating profit `B` = 155.2 x 0.75 = $116.4bn.
- Hurdle `h` = 8.5%, made of an illustrative 4.5% ten-year bond plus a 4.0 point margin. The bond yield is not in the saved run.
- Free cash flow: $67.0bn.

Measure 1: after-tax earnings yield = 116.4 / 3,620 = 3.2%. That is 5.3 points below the hurdle. The page's printed pre-tax figure is 4.4% (the saved run's own EBIT line; 155.2 / 3,620 gives 4.3%). Forward earnings yield: 20.02 / 521.91 = 3.8% for the year to June 2027.

Measure 2: EPV = 116.4 / 0.09 = $1.29tn. EPV / EV minus 1 = minus 64.3%, matching the page.

Measure 3: with `B` = $116.4bn the price needs growth of **13.4% a year for ten years**. With `B` = $67.0bn free cash flow it needs **21.0% a year**.

The records: own 5-year earnings per share growth 17.2%, own 10-year 21.2%, consensus growth from $20.02 (year to June 2027) to $24.14 (year to June 2028) is 20.6% in a single step. That is one year of growth, not two, because last reported earnings per share is not in the saved run. The build reads it from the same analyst data (it carries a "year ago" figure).

Reading: on operating profit, Microsoft's record exceeds the 13.4% the price needs. On free cash flow the requirement (21.0%) equals the best of its records. The two bases disagree because of the capital spending. That disagreement is the point of the warning.

## f. Expected return, and the verdict of record

**Definition.** For each of three named growth assumptions `g`, the return is the yearly rate `r` that makes the ten-year equation in section e equal today's price: the same equation, solved for `r` instead of `g`. In words, it is the yearly return a buyer earns if profit grows at `g` for ten years and then stays flat, with nothing else assumed. A buyer who holds 3 to 5 years and sells at the same valuation earns about the same.

The three assumptions, and nothing else, make the range:
1. own 5-year earnings per share growth;
2. own 10-year earnings per share growth;
3. two-year consensus growth.

Low end of the range is the lowest of the three returns, high end the highest. No band is invented. If only two of the three exist, the range uses two. If fewer than two exist, the result is "not enough data". Where the consensus case is one of the three, it is marked "not testable" (section j).

**Worked figures, Microsoft, operating profit base, hurdle 8.5%:**

| Growth assumption | Growth | Return |
|---|---|---|
| Own 5-year | 17.2% | 10.5% |
| Consensus | 20.6% | 12.5% |
| Own 10-year | 21.2% | 12.8% |

On the free cash flow base the same three give 6.8%, 8.3% and 8.6%. Because the capital spending warning would be on, the headline range is the lower pair: **6.8% to 8.6% a year against a hurdle of 8.5%**.

**Why not "starting yield plus growth".** The plan asks for starting yield plus growth. For Microsoft that sums to 3.2% + 17.2% = 20.4% a year. That figure assumes today's price keeps buying growth of 17% forever. It is not consistent with the implied-growth calculation, which assumes growth stops after ten years. The two would give different answers to the same question. This design therefore uses the ten-year equation, and still prints the starting yield and the growth next to every figure, so a reader sees both parts. Open question 5.

**Valuation change.** No change in valuation (re-rating) is included. A forecast of what multiple investors will pay in five years is not something this data can support. The valuation band is shown as context only.

**Verdict of record.** Four states, set by code:

| State | Rule |
|---|---|
| Excluded | At least one gate rule failed. The reason is named. |
| Not enough data | The gate could not be completed, or fewer than two growth assumptions exist, or no operating profit, or no hurdle. The missing items are listed. |
| Qualifies, below hurdle | Passed the gate, and at least one of the growth cases returns less than the hurdle. The page also says how many of the cases clear it. |
| Qualifies, clears hurdle | Passed the gate, and every available growth case returns at least the hurdle. |

Using the lowest case makes "clears" a strict test. Microsoft in the example above would read "Qualifies, below hurdle (1 of 3 cases clear on free cash flow)". Open question 6 asks whether a middle state is wanted.

**How it is shown, so that it does not read as a forecast promise.**
- The figure is labelled "return if the growth holds", never "expected return" or "target".
- It is always a range with the three named cases, never a single number.
- The line under it says: these are what the price returns if the stated growth repeats. They are not forecasts. Past growth may not repeat. Forecasts can be wrong. The model's narration cannot change them.
- A growth case above 15% a year for ten years is marked "few companies have done this" and still shown.
- No colour implies certainty: the two qualifying states use neutral and positive tones, never a "BUY" word.

**What the figure is worth.** This figure shows what today's price requires. It is a question put to the reader, not an answer. Studies of company-level implied returns find that they rank future returns weakly, and the weakest measures are about as good as a constant (section o, "Implied returns"). The gate and the cash yield carry the evidence; this figure carries the question. Two details of the studies, so the line is not read as stronger than it is: the measure that came out at a slope of about 0.2 against realised returns, where 1.0 would be perfect, is one of three tested (the other two came out at about 0.6), and one study of 2017 finds the same measures useful for tracking expected returns through time.

## g. The hurdle

Hurdle = ten-year government bond yield + a margin.

**Margin.** Proposed 4.0 percentage points. This is a judgment, not a sourced figure: no source for the stock-over-bond premium was fetched for this page, so no study is cited. At build, set it from a published estimate of the premium (fetched and cited then), and fix it in the strategy file, not in code.

**Which bond.** Options: the bond of the company's own currency, or one reference for all. Own-currency is more correct (a Brazilian real return should clear a real-denominated bond) but needs a bond yield series for every currency. One reference (the US ten-year) is simple but wrong for currencies with very different inflation.

**What is reachable.** Section l lists it in detail. In short: a US ten-year yield history is expected from yfinance and was not confirmed from this machine on the day of writing (the probe timed out). Other countries' yields are not confirmed from either provider. EODHD publishes government bond yields through its end-of-day price service, and the current plan has no end-of-day prices, so those are expected to be unreachable.

**When no series is reachable.** The company's result is "not enough data: no hurdle". There is no fixed fallback rate, because a made-up bond yield would put an invented number into the verdict. The last good yield is stored with its date and used for up to 7 days, then it lapses.

Recommendation: one reference, the US ten-year, for every company in the first release, with a visible line saying so, and a flag for companies whose reporting currency has very different inflation. Open question 4.

## h. What happens to each existing lens

| Lens | Disposition | Reason |
|---|---|---|
| Forensic | Kept as gate input | Its three readings (accruals, distress, accounting health) become the first three gate rules. The mark-only lens retires in its ranked form. |
| Quality | Kept as context | Worst-year return on capital and gross profitability print as quality readings. Its debt-to-profit leg moves into the gate. |
| Earnings Power Value | Kept as a measure | It becomes measure 2. As a voting lens it retires. |
| Magic Formula RAW | Retired as a vote | Its earnings yield is measure 1. Its return on capital part is context. |
| Value + Momentum | Retired as a vote | Earnings yield is measure 1. Momentum becomes context with no vote. |
| Growth | Retired as a vote | The growth record feeds the three growth cases. The rank is context. |
| Defensive Income | Retired as a vote | Dividend cover moves into the gate. Yield and streak are context. |
| Cyclical Income | Retired as a vote | Same reason. |
| Financials | Kept as context, and as the base for a later bank path | Section c. |
| ETF lenses | Unchanged | The new question is about companies. ETFs keep their current lenses. |
| Legacy lenses already hidden | Unchanged | |

Overlaps, stated plainly: Magic Formula's earnings yield and measure 1 are the same ratio. Earnings Power Value and measure 2 are the same formula, with the one open change in the cost of capital. Measure 3 has no counterpart today.

Strategy files are not edited. The new gate and valuation live in new, versioned files.

## i. The company page, in the order a reader meets it

1. **The answer** (written by code, two lines). The verdict of record in plain words, then the one reason that mattered most. Example for an excluded name: "Excluded: debt is 7.2 times operating profit, above the 5 times limit." Example for a qualifying name: "Qualifies, below hurdle: the price returns 6.8% to 8.6% a year if growth holds, against a hurdle of 8.5%."
2. **The story** (written by code, five short paragraphs, same shape as today): what was asked, what the gate found, what the price needs, what to doubt, what this cannot tell you. The optional model summary still replaces it when ticked, and still cannot change a number.
3. **The gate** as a six-row table: rule, reading, limit, result.
4. **The price test**: earnings yield against hurdle, EPV against price, implied growth against the three records, side by side. The capital spending warning sits here when on.
5. **The return range**: the three growth cases, the return for each, the hurdle, the verdict.
6. **Context, no vote**: quality readings (with the trough year named), momentum, where the company sits among rivals, the valuation band, analysts' readings.
7. **The judge panel**, when ticked, asked the same question (section k).
8. **The workings**, folded: every source, every input with its date, the old relative-lens table labelled "baseline: relative view, no longer votes".

The text download keeps every section.

## j. The new backtest

**Question.** Did names that qualified beat (a) a broad index, (b) cash, and (c) an equal-weight basket of companies in the same industry, over 1 and 3 years? The third comparison stops a sector boom being mistaken for skill. Tested by industry, as today.

**Method, reusing what exists.**
- Monthly rounds. At each month end, the gate and the trailing parts of the valuation run on accounts the company had already published, using the existing as-of reader.
- The names held depend on the form being tested (next block). Each form is held equal-weight for 12 and for 36 months, with the existing 0.5% round-trip cost.
- Excess return is measured against each of the three comparisons separately.
- The luck test is the existing one: 500 random baskets of the same size and turnover, drawn from the same industry pool, scored the same way. The drop-best check is the existing one: remove the best pick each round, only rounds with 4 or more names.
- Pass bar: at least 2 points a year over the equal-weight industry basket over the full window, with the luck score under 5%. The count of winning years is reported, not required. Reason: cheapness works with long droughts. One fetched study measures a drawdown of 55% in value against growth from 2007 to mid-2020 (Arnott et al. 2021, section o), so a rule that demanded most years to win would reject a real effect. A figure for how often five-year windows lose was cited to a Swedroe article that could not be fetched; it is not used. This replaces the earlier bar of 6 winning years in 10.
- The 3-year hold has far fewer independent rounds and is reported without a pass or fail.

**Three forms, tested and reported separately.**

1. **Gate alone.** Every name that passes the gate, equal-weight, against the three comparisons.
2. **Gate plus relative cheapness.** Within each industry group, the cheapest third of the qualifying names on free cash flow yield (free cash flow divided by enterprise value). Cheapness is ranked inside the industry, never across industries, so no bet is placed on cheap industries.
3. **Gate plus the absolute verdict.** Only names that read "qualifies, clears hurdle" (section f), using the two own-record growth cases, because consensus cannot be tested.

What each form is for, stated plainly:

- Form 2 is the form with published evidence: cheap-and-sound selections in Piotroski and So (2012), the cheap-plus-quality screen of Gray and Carlisle (reviewed by the CFA Institute), and the industry adjustment in Novy-Marx (2013). What was fetched on each is in section o. Piotroski and So's own return numbers could not be fetched and are not quoted.
- Form 3 is the form that answers the owner's question, "will it make money over my holding period". It has no published evidence of its own: it is built from the implied-growth measure, which section o shows is weak at ranking stocks.
- The page will show the gap between form 2 and form 3 in the same table. If form 3 does not beat form 2, the absolute verdict is adding a question, not evidence.

**What can and cannot be tested.**
- Testable: the gate, the trailing earnings yield, the EPV, and implied growth against the company's own 5- and 10-year records. All use accounts only.
- Not testable: everything that rests on analysts. The data holds no analyst forecasts as they stood on a past date, only today's. Consensus growth, forward earnings yield and the consensus return case therefore have no backtest, and the page labels them "not tested". In the backtest, the return range uses the two own-record cases.
- The hurdle needs a ten-year yield on each past date. A US series is expected to be reachable; other countries are not confirmed. The backtest uses the US ten-year for all names, as the live design does.
- Cash needs a short-term rate history (US three-month bill from yfinance, expected, unconfirmed).

**Look-ahead and survivorship limits, said plainly.**
- Accounts are the provider's restated record, not the figures first published. The filing-date mode limits which years are visible by when they were filed. It works only for companies whose accounts come from EODHD with filing dates; a company without them falls back to period end plus 90 days, counted and reported.
- The company list is today's market index. Companies that were delisted, merged or went bankrupt are not in it. This flatters any method, and flatters a gate that removes failing companies the most, because it never sees the ones that failed and vanished.
- Market value at past dates is rebuilt from today's value scaled by price change. Split-sized jumps in share count withhold the value.
- Prices come from yfinance, adjusted for splits and dividends.

**Baseline kept.** The existing results (`backtests/`, the track record column and the proof-or-not badges) stay unchanged and are labelled the relative baseline. The new results go in a separate directory and a separate summary file. Neither overwrites the other.

## k. The judge panel

The judges today are asked for BUY, HOLD or SELL from a pack with no lens opinions. They keep the same pack and the same stability rule (ask twice, keep both if they differ). Only the question changes. Proposed wording:

"Using only the evidence pack and anything you know about the company, which you must label, would a person who buys this company at today's price and holds it for 3 to 5 years more likely than not earn more than a ten-year government bond yields plus 4 points a year? Answer YES, NO or ABSTAIN, with a confidence and the one fact that decided it."

The answers are YES, NO and ABSTAIN. This is the same question the maths answers. The pack stays free of the code's verdict, hurdle result and return range, so the panel is independent of the maths. After the code has finished, the page shows whether the panel agrees with the verdict (YES against "clears hurdle", NO against "below hurdle").

Old judge records stay as they are, separate, labelled as answers to the old question, and are not mixed into the new agreement figure. The bar in the judge panel document (12 months, 30 names) restarts for the new question.

## l. Build plan, and what stays byte-identical

### Inputs: what exists, what needs a new fetch, what cannot be had

| Input | Status today | Notes |
|---|---|---|
| Net income, operating cash flow, total assets (accrual ratio) | Exists | Annual, period-matched. |
| Altman Z inputs (current assets and liabilities, retained earnings, EBIT, total assets, total liabilities, revenue, market cap) | Exists | Abstains when price and accounts currencies differ. Coverage gap. |
| Piotroski F-Score and its nine checks | Exists | Needs 5 of 9 checks. |
| Operating profit and EBIT, annual series | Exists | Newest first, dated in the as-of reader. |
| Enterprise value | Exists | Market value + total debt minus cash; leases not included, noted in the report. |
| Net debt over operating profit | Exists | Shown as "net cash" below zero. |
| Worst-year return on capital | Exists | Last 5 years. |
| Cash runway | Exists as a reading | Narrow rule: applies only when operating cash flow is negative. |
| Dividend cover | Exists | Dividends over mean free cash flow. |
| Earnings power value | Exists | 9% and 25% are fixed constants. |
| Free cash flow, operating cash flow, capital spending | Exists | Annual lists, newest first. |
| Depreciation and amortisation | **New field** | Not read anywhere. Both providers carry it on the cash flow statement; it needs a new field and a mapper line, and no extra request. To be confirmed on a live payload at build. |
| Tax provision, pretax income | Exists | Used by the EPV tax rule. |
| 5- and 10-year earnings per share growth | Exists | 10-year from the EODHD long history; yfinance fallback gives about 4 years. |
| Consensus earnings per share, this and next fiscal year, and 90 days ago | Exists | EODHD analyst data. Gaps outside the US. |
| Consensus "year ago" earnings per share | **Exists in the payload, not kept** | The probe lists it. Needs one extra field kept for the two-year growth. |
| Consensus operating profit, revenue or long-term growth | **Cannot be had** | The probe lists only earnings per share fields. Not verified for others. The design does not rely on them. |
| Ten-year government bond yield history, US | **New fetch, unconfirmed** | Expected from yfinance. The probe timed out on 2026-11-03 and is not confirmed. |
| Ten-year yields, other countries | **Unconfirmed, probably cannot be had** | Not on yfinance in a form probed. EODHD serves bond yields through its end-of-day price service, which the current plan does not include. A single one-unit probe is the first step of phase 2. |
| Short-term cash rate history | **New fetch, unconfirmed** | Same source as the bond. |
| Broad index total return | **New fetch** | A broad index fund via yfinance. |
| Analyst forecasts as they stood on past dates | **Cannot be had** | Not stored. Limits section j. |

### Phase 1: the gate

- A new, versioned gate definition, new gate result type, new company page section. Reuses the existing accrual, distress, F-Score, debt, runway and cover readings.
- Calibration step before thresholds are fixed: measure each reading across the market index and write the distribution into this page.
- No change to any vote.

### Phase 2: absolute valuation and expected return

- Depreciation field, "year ago" consensus field, bond series fetch with storage, the three measures, the capital spending warning, implied growth solver, the return range and the verdict of record.
- Probe the bond sources first, and report before building anything on them.
- Open question 7 decided first (cost of capital in the EPV).

### Phase 3: report and UI

- The page order in section i, code-written answer and story in new templates, the gate table, the price test, the return range. Judge question change (section k). Old lens table moved into the folded workings.
- The sweep gains rules: no lens id or column name in the first three sections, and no "expected return" or "forecast" wording.

### Phase 4: backtest

- New runner beside the existing one, separate output directory, the three comparisons, the 1- and 3-year holds, the existing luck and drop-best code.

### Stays byte-identical in every phase

- Every file under `backtests/` and the track record table.
- Every strategy file, and the rank engine's outputs for the existing lenses (saved ranks and votes).
- Every saved verdict (`verdicts/`) and saved report (`reports/`).
- The results JSON of the Microsoft evidence runs under `runs/`, except for files written by the new section, which are additive.
- The full suite stays green after each phase. New behaviour ships with tests anchored to this page's worked example.

## m. Open questions for the owner

1. **Worst-year return on capital: context or hard gate?** Recommended: context, for the cyclical reason in section b. If hard, apply it with a lower floor (for example, positive and above the hurdle's bond rate) and exempt cyclical industries by a stated rule.
2. **Banks and insurers: separate path, or "not covered"?** Recommended: "not covered" with the Financials readings as context, and a later design for a separate path.
3. **Which distress score?** The current Altman Z uses market cap and sales, so it abstains for most non-US companies. The non-manufacturer version (Altman 1995) uses book equity and drops the sales term, so it works across currencies. Recommended: switch to the non-manufacturer version, with its published zones (distress below 1.1, safe above 2.6), and check the cut points against the paper at build.
4. **Which bond and which margin?** Recommended: US ten-year for all in the first release, margin 4.0 points, both set in the strategy file.
5. **Return formula: ten-year equation or "yield plus growth"?** Recommended: the ten-year equation, printing yield and growth beside it.
6. **A middle verdict state?** Recommended: no. Keep four states, with a line saying how many growth cases clear.
7. **Should the EPV cost of capital become the hurdle?** Recommended: yes in the new measure; the old lens keeps its 9% in its published file.
8. **When the capital spending warning is on, headline on the lower base?** Recommended: yes.
9. **Judge pack:** should the judges see the hurdle and the three growth rates? Recommended: no; they see the same pack as now, so they stay independent of the maths.
10. **Thresholds in section b that are judgment, not published** (accrual 0.10, debt 5 times, runway 24 months, cover 100%, capital spending 1.5 times): approve them as proposals, to be re-set from the measured distribution in phase 1, then frozen in a versioned file before any backtest. Recommended: yes.
11. **Growth above 15% a year:** mark only, or cap? Recommended: mark only.
12. **Keep the accrual rule in the gate?** The fetched evidence (section o) says the accruals anomaly stopped paying after 2002. Recommended: keep it as an accounting-quality check only, and drop it if the phase 1 calibration shows it removes companies without a matching rise in later losses.
13. **Backtest bar:** 2 points a year over the equal-weight industry basket across the full window, luck under 5%, winning years reported not required. Recommended: yes (section j).

## n. Where this design disagrees with the plan

- **Expected return is not "starting yield plus growth".** The simple sum assumes growth continues forever and contradicts the implied-growth calculation. The design uses one equation for both, and still prints the parts. (Section f.)
- **Worst-year return on capital is context, not a gate.** The plan lists it in the exclusion gate. Cyclicals on a trough year would be excluded at the point of highest future return. Moved to context; hard gate left as open question 1.
- **Cash runway applies only to cash-burning companies, and an unprofitable company with a runway is "not enough data", not excluded.** The plan states runway for loss-makers; this fixes the boundary and avoids calling a profitless but funded company a failure.
- **Altman Z needs a different version for most non-US names.** The plan assumes the current Z-Score. It abstains for any company whose price and accounts are in different currencies, so the gate would return "not enough data" for much of the world. (Open question 3.)
- **The range is the three named growth cases, and "clears hurdle" means all available cases clear.** The plan asks for a verdict of "clears hurdle" without defining it. The design picks the strict reading.
- **Implied growth is compared against earnings per share records, not operating profit records.** The plan says earnings or free cash flow. The long history and consensus are only per share. The mismatch is stated, not hidden.
- **No fixed fallback hurdle.** The plan asks what happens without a bond series. The design returns "not enough data" rather than an invented rate.
- **The consensus case is untestable and labelled so.** The plan already says consensus parts are untested; the design adds that the backtest range uses only the two own-record cases.
- **Banks are "not covered" in the first release.** The plan allows either; this picks the cheaper and says so.
- **The backtest tests three forms, not one, and the pass bar changes** (section j). Form 3 is the owner's question; form 2 has the published support; the page shows the gap. The 6-years-in-10 requirement is dropped and winning years are reported only.
- **Four of the brief's evidence figures are corrected or left out** (section o and p): the 3.6 point tilt result is paired with about 4% tracking error, not 8%; the 0.2 implied-return slope is one of three models; the Lee, So and Wang finding holds for most individual measures in the cross section, not all; and the Swedroe, Research Affiliates, Morningstar, Piotroski and So and Greenblatt figures could not be fetched.
- **ETF lenses stay.** The plan does not mention funds. They are left alone.

## o. What the evidence says to expect

Every figure in this section was read on the page or in the paper named beside it, in this pass. Where a figure from the brief could not be confirmed, it is left out and listed in section p. Where the brief's wording differed from the source, the source's wording is used and the difference is said.

### Published rules fade, and some never held

- McLean and Pontiff (2016) studied 97 variables shown to predict stock returns. Portfolio returns are 26% lower out of sample and 58% lower after publication.
- Hou, Xue and Zhang (Review of Financial Studies, 2020): 65% of the 452 anomalies in their library fail the ordinary significance hurdle of an absolute t-value of 1.96, with microcaps mitigated by large-exchange breakpoints and value-weighted returns. At the stricter multiple-testing hurdle of 2.78 the failure rate is 82%. Their earlier 2017 NBER draft gives different counts (447 anomalies, 64%), so the published numbers are used.
- The other side: Jensen, Kelly and Pedersen (2023) find a baseline replication rate of 56.9% on US returns, rising to 84.9% under their own method, across 153 factors in 93 countries. They say part of the gap to Hou, Xue and Zhang comes from weighting: capped value weights add 8.5 points to their rate. The two papers disagree on how much survives, so this page does not pick a single share.
- Microcaps and equal weighting inflate published results. Hou, Xue and Zhang write that microcaps are on average only 3% of the market value of the US universe but about 60% of the number of stocks, and that "anomalies in microcaps are more apparent than real". Our cohort size floors of $1bn to $10bn and the $5bn tested-range limit on the company page keep the smallest companies out. Our backtest is still equal-weight, so this lowers the risk and does not remove it.
- Accruals: Mohanram's 2011 draft (the file fetched is dated September 2011, not 2014) reports that the accruals anomaly produced excess returns consistently for over four decades until 2002 and since then has apparently disappeared. It credits part of the decline to analysts' cash flow forecasts. This weakens the case for the accrual rule as a source of return. It stays in the gate as an accounting-quality check, and open question 12 asks whether to keep it.

### Quality and profitability hold up better

- Asness, Frazzini and Pedersen (2019), quality minus junk: the factor delivers positive returns in 23 out of 24 countries studied, in a sample of 54,616 stocks from June 1957 to December 2016.
- Walkshäusl (2020), Journal of Asset Management: over 2000 to 2018, high-FSCORE firms outperform low-FSCORE firms by about 10% per year across developed non-US and emerging countries (9.9% developed, 12.0% emerging in the body). The count of 35 markets in the brief was not found in the parts read and is not used. The paper also cites Piotroski and So (2012, Table 1) for 10.03% a year for the US F-Score premium.
- Novy-Marx (2013): profitability, measured as gross profits over assets, has roughly the same power as book-to-market in predicting the cross-section of average returns, and controlling for it raises the performance of value strategies, especially among the largest, most liquid stocks.
- Fama and French (2013 draft): profitability and investment add to the description of average returns, and the value factor is redundant in the five-factor model. In plain terms, cheapness alone explains less than its reputation suggests, which is one reason the gate comes first.

### Cheapness works, with long droughts

- Arnott, Harvey, Kalesnik and Linnainmaa (2021): value, as measured by the Fama-French book-to-market factor, has underperformed growth investing since 2007, producing a drawdown of 55% as of mid-2020. The brief said "lost 55% against growth"; the source says a drawdown of the factor.

### Cheap plus quality, in backtests

- Piotroski and So (2012) is the base paper for combining cheapness with strength of fundamentals. The abstract fetched carries no return figures. The 20.8% against 10.5% a year in the brief came from a page that could not be fetched, and is not used.
- Gray and Carlisle, Quantitative Value, reviewed by the CFA Institute (16 May 2013): the full model generates a return of 17.68% a year. The review says the authors claim it outperformed the Magic Formula by 3.74% a year from 1974 through 2011. The comparison is the Magic Formula, not an index.
- Carlisle, Latticework interview, 18 December 2024: "The way we tested, it was about 16.2% compound over the full dataset (1972 to 2017)", against an equal-weight S&P 500 total return of about 10.5%. The visible text does not say these are Magic Formula figures. The second retest in the brief (11.4% against 8.7%, 2003 to 2015) was not found there and is not used.

### Live results

- QVAL (Alpha Architect), factsheet through 30 September 2026, return at net asset value: 1 year 26.84%, 3 years 18.12% a year, 5 years 13.33% a year, 10 years 11.56% a year, since the fund began on 21 October 2014 9.09% a year. The market-price figures for 10 years and since inception are 11.54% and 9.11%.
- iShares pages, 30 September 2026, 10-year annual return on the benchmark row: S&P 500 15.32%, and 11.42% for the Russell 1000 Value fund's benchmark row (the page fetched does not name the index).
- So over ten years to 30 September 2026 the fund returned 11.56% against 15.32% for the S&P 500 and 11.42% for the value benchmark. It matched the value benchmark and lagged the market by about 3.8 points a year. That arithmetic is ours.
- QVAL's process, from its investment case: start from the 1,500 largest US stocks, remove low-volume, red-flag, bad-price-action and extreme-valuation names, rank on EBIT over total enterprise value, then remove low financial-strength scores. That is close to form 2 in section j. The document is dated 31 March 2025 and shows a different start date, so none of its returns are used.
- AAII's Magic Formula screen, as listed on 30 October 2026: 2022 minus 21.3%, 2023 13.5%, 2024 17.2%, 2025 17.9%, 2026 to date minus 13.5%, "Annualized Total" 6.4% (the window is not stated on the page). One screen as followers run it, not a fund.
- Greenblatt's comparison of managed and self-managed accounts (84.1%, 59.4%, 62.7% in the brief) is not on the Validea page fetched and is not used.

### Tilts in practice

- Fitzgibbons and co-authors, "Long-Only Style Investing: Don't Just Mix, Integrate", as summarised by CXO Advisory (the AQR affiliation is not stated on the page): against the value-weighted MSCI World, February 1993 to December 2015, standalone value and momentum portfolios outperform by an average annualised net 1.7%, with tracking errors just under 8%. The mixed and integrated combinations outperform by an average net 2.5%, with tracking errors about 4%. The integrated combination is reported at 3.6%. The brief paired 3.6 with a tracking error near 8%; the page pairs 8% with the standalone portfolios and about 4% with the combinations.
- The Research Affiliates 2017 finding that over half of the paper value return disappears in live funds could not be fetched. It is not used.

### Implied returns

- Li and Mohanram (the file fetched is dated 2013), regression of future realised returns on an implied-return measure, where 1 would be perfect: the HVZ model 0.209, 0.192 and 0.087 for years one, two and three ahead (the last not significant); the AR model 0.584, 0.498 and 0.576; the RIV model 0.652, 0.574 and 0.594. So "about 0.2" holds for the weakest of three models; the other two are about 0.5 to 0.65.
- Lee, So and Wang (the June 2017 working paper; the published 2021 version in the Review of Financial Studies was not fetched): among the measures tested, only a composite and one other consistently perform better than a trivial estimate, that is, a fixed constant. Four of the five individual implied-cost-of-capital measures underperform a constant in the cross section, and three of the five outperform every non-implied measure in time series. The abstract says only that these measures are particularly useful in tracking time-series variations in expected returns. The wording "no better than a constant at ranking stocks" is supported for most individual measures in the cross section, not for all of them and not in time series.
- Li, Ng and Swaminathan (2013): aggregate implied cost of capital strongly predicts future excess market returns at horizons ranging from one month to four years. That is a market-level, time-series result. It does not contradict the two papers above, and it is not evidence for ranking stocks.

### Sector and industry

- AAII (June 2022): Greenblatt also excludes financials and utilities because of their unique financial structures. This design already treats banks and insurers as "not covered" (section c). Utilities are not excluded here. The debt rule may remove many of them; check at calibration.
- Novy-Marx (2013) uses variables demeaned by the 49 Fama-French industries and finds industry-adjusted gross profitability has more power than the straight measure. So comparisons are made within an industry, and no bet is placed on cheap industries.
- The Morningstar 2017 figures on stock-level against industry-level value (2.36% and minus 0.32% in the brief) could not be fetched and are not used.

### The realistic target

One to three points a year over the equal-weight industry basket, over ten years, with droughts of several years. That range is a judgment built on the fetched figures above: net edges of 1.7 and 2.5 points for tilts against a market index, after a published rule has typically lost 26% of its return out of sample and 58% after publication. It is not itself a cited result.

A backtest of ours showing far more than that is to be treated as an error until it is explained: look first for look-ahead, survivorship, microcaps, a data fault, or a single stock.

## p. Sources

Status is what happened in this pass. "Fetched" means the page or paper was read and the figures above were checked against it. Several documents were PDFs that the page-reading tool returned as raw bytes; their text was extracted from saved copies.

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

Listed in the brief but not usable, and why:

- Alpha Architect, value premium, risk and mispricing (https://alphaarchitect.com/value-premium-risk-mispricing/): returned HTTP 403. The 20.8% and 10.5% figures are not used.
- Validea reprint of Greenblatt (https://blog.validea.com/greenblatt-less-is-more-in-portfolio-management/): read through an archive copy, dated 31 January 2012 in its metadata. The figures 84.1, 59.4 and 62.7 are not on it.
- Research Affiliates 2017 via Advisor Perspectives (https://www.advisorperspectives.com/commentaries/2017/04/13/the-incredible-shrinking-factor-return): HTTP 403.
- Swedroe 2018 (https://www.advisorperspectives.com/articles/2018/06/14/in-defense-of-the-value-premium): HTTP 403. The 22% of five-year windows is not confirmed.
- Morningstar 2017 (https://www.morningstar.co.uk/static/UploadManager/Assets/The_Impact_of_Industry_Tilts_on_Factor_Performance.pdf): redirected to a home page.

Named in section b and written from memory in the first draft, not fetched: Sloan 1996, Altman 1968, Piotroski 2000, and the rating agency remark on borrowing.
