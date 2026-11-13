# I built an AI stock council, measured it, demoted it, and published the null

*A case study on Aristos, a stock-analysis project, and what five months of building taught me about proving a signal exists before engineering around it.*

## The goal

I wanted a tool that helps me judge a single company before I buy it: are the accounts sound, what growth does the price already assume, and how does the company compare with its rivals? The original idea went further. A council of AI specialists would debate each company and produce a call: BUY, HOLD or SELL.

## What I built

Over five months I built roughly 69,000 lines of code, 75,000 lines of tests and 7,500 lines of documentation, for a single user. The main pieces:

- A company page with an accounts-quality check, a reverse-valuation view (what growth the current price needs) and a comparison with rivals.
- An exclusion gate that flags companies with warning signs before they reach the ranking.
- A ranking and valuation core, with its rules written in two frozen definition files.
- A backtest harness (a backtest replays the rules on past data to see how they would have done) and two forward tests, a scoreboard and a judge panel, that score live picks on dates fixed in advance.
- Careful data handling. Missing figures abstain instead of becoming false failures, and currencies, duplicates and stock splits are handled explicitly.

## What I measured

I treated the project as a hypothesis, not a product: do these rules pick companies that beat their industry?

1. I measured the AI council, and demoted it. In a controlled experiment across 17 councils, the council did not earn its place. One audited model call now writes the narrative, and code checks every claim it makes.
2. I wrote the rules down before running the tests. This is called pre-registration: fixing the rules in advance so they can't be tuned to fit the answer. The backtest definitions were frozen first.
3. I published a null result, meaning the test found no evidence of an advantage. Over ten years, the headline verdict ("clears the hurdle") finished about half a point behind a simple "cheapest third" screen and added no steady return over it.

## Why the result is a null, not a failure

The rules are published, well-known ideas applied to large companies using data everyone has. My own design document expected an edge of one to three points a year at best.

The test cannot see an edge that small. Ten years of today's surviving companies gives error bars of about plus or minus 1.7 points. Error bars are the range the true answer could plausibly sit in, and that range is wider than the edge I was looking for.

This is the real lesson. No further engineering fixes it. Every new variant (the fourth form was the latest) is another draw on the same ten years, which makes the test weaker, not stronger. The effort went into trust (careful data, frozen rules, audited claims) around a signal that was never shown to exist.

## What I got wrong

- I kept building after the question was already answered. The honest reading of the evidence arrived well before the last tens of thousands of lines.
- I measured the wrong thing for the gate. The exclusion gate's real job is avoiding companies that fail and vanish, but those companies are not in the data, so the backtest is blind to exactly what the gate does well.
- I let a verdict outrun its evidence. BUY, HOLD, SELL and "clears the hurdle" read like calls. The data supports them only as readings.

## The case against my conclusion

The strongest objection is fair: "no edge shown" is not "no edge", and the test cannot rule out a one-point edge. A checklist that stops one bad purchase may also be worth more than any edge, and no backtest here measured that. The fourth form's +2.0 points since 2022 is a lead, but one seen after the fact, so I treat it as a question for the forward tests, not a result.

## What I'm doing next

- Letting the forward tests report before building anything more. The scoreboard's first scoring is in January 2027. I plan to reopen active development only if it shows something.
- Keeping the company page as my own checklist and the exclusion gate as a warning list, along with the company table, the peer groups and the backtest harness.
- Considering what to retire: BUY, HOLD and SELL as verdicts, new lenses and versions, the ETF lenses (they only sort funds by fee and size), and the four-specialist narration machinery.

## Patterns worth reusing

Three habits came out of this, and they travel well beyond investing:

1. A model writes, code checks every claim. Language models draft; plain code verifies.
2. Abstain rather than guess. A missing input should produce "unknown", never a made-up pass or fail.
3. Fix the rules before the run. Deciding the test in advance is the only defence against fooling yourself with your own data.

The next domain I apply these to should be one where being right can be checked in weeks, not decades.

## What this means for teams building with AI

Most AI projects I see are built in the same order I built this one: system first, proof later. Teams invest in pipelines, agents and dashboards before checking that the AI actually beats the simple alternative. The cheaper order is the reverse. Define what success looks like, test whether you can even measure it, and compare against a plain baseline before building anything around it. A week spent on that question can save months of engineering.

## Takeaway

Before building the infrastructure to trust a signal, check that the test could detect the signal at all. Mine could not, and the most useful thing the project produced was knowing that, with the evidence written down.
