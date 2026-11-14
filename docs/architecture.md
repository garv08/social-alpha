# Self-improving trading engine

Saved from the public post by Arwaz Az ([@ArwazAz_](https://www.instagram.com/ArwazAz_/), [Instagram, 4 Nov 2026](https://www.instagram.com/p/DfWdUerE2lp/)). The carousel is two images: a poster of the seven stages, and a blueprint of the loop. Instagram would not serve the image bytes without a login, so this document follows the caption, which is the same text supplied with the link.

The rule in that post is the rule here: a model may propose an improvement, but independent validation and risk controls decide what is allowed to run. In this repo that decision never reaches a broker.

```
TRADE → RECORD → EVALUATE → IMPROVE → VALIDATE → REPEAT
```

## Where each stage lives

| Stage | In social-alpha | What it is not |
|---|---|---|
| 1. Market data | TOP daily OHLC and volume, plus ingested X and Telegram posts | A live tick feed |
| 2. Strategy engine | A rules-only desk: technical (TOP strategies), social, news themes, and a fundamentals note that we have no filings. Bull/bear read, then a risk vote. See `desk.py`. | An order router, or a language-model debate |
| 3. Backtesting | `evaluate.py`: next-bar fills, stops, targets, excess vs Nifty | A fit on the same days the signal was chosen |
| 4. Evaluation | Creator/setup scores, t-stat, shrunk score, paper-book expectancy and drawdown | A reason to go live |
| 5. Research | `engine.py` proposes gates from failures. An optional LLM can narrate extraction elsewhere; it cannot approve a gate | An agent that edits live parameters |
| 6. Validation | Later signal dates are unseen. A gate is approved only if the failure repeats there on at least 8 calls, and also failed on at least 8 earlier calls | In-sample leaderboard rank |
| 7. Controlled deployment | The desk's portfolio manager may rate a name BUY. That rating can open a paper order. Approved engine gates can still block the copy book. | A broker, an API key, or an auto-deploy flag |

`engine.request_live()` always returns `refused`. Loading gates forces `live_trading` to false even if that key is written into the database by hand.

## What can be approved

- **Block a creator** when their bullish calls lagged the Nifty 50 both before the cut and on the later window.
- **Block a setup** on the same two-window test.
- **Require TOP agreement** for a creator when calls that disagreed with that stock's walk-forward TOP strategy lost on both windows, and agreeing calls did not lose on the unseen window.

A loss that shows up only in the early window is recorded and rejected. If there are not enough dated calls to hold out a later window, existing gates are left unchanged.

Gates are recomputed every `social-alpha improve` (and at the end of `daily`). A creator who later stops failing the unseen window is unblocked. Open paper positions are not force-closed; the gate only stops new copy-book entries.

## What is only a note

Paper-book expectancy and max drawdown are recorded on each run. A negative paper book is not by itself a parameter change. Changing stop width, hold length, or capital still needs its own unseen test, which this version does not invent.

The **ours** book (TOP strategies, social names as the filter) is not gated by creator blocks. Its trades are evaluated and written into the report so the next change can be tested instead of assumed.

## Data contract

The ingest, process, and backtest loop follows the TradingAgents shape, not its language-model graph or its US vendors.

| TradingAgents stage | Here | Rule |
|---|---|---|
| Vendor fetch as of date D | `vendors.py` | Posts after the end of D (IST) are withheld. Bars after D are withheld. Filings are not invented. |
| Analysts, debate, portfolio manager | `desk.py` | Rules only. BUY is the only paper entry. |
| Decision log | `desk_runs` for today, `desk_replay` for history | A rating is stored with the date it was allowed to see. |
| Backtest on later closes | `replay.py`, command `backtest` | Creator scores use only windows that had already closed. The call is scored on its stored 5/20/60-session excess, not on a bar from before the decision. |
| Portfolio simulation | `paper.py` | Two ₹10,000 books, next-open fills. Not a broker. |

`social-alpha backtest` does not run inside `daily`. It rewrites `desk_replay` from the calls already evaluated. A BUY in that table is a paper permission. It is not evidence of an edge.

## Commands

```bash
.venv/bin/python -m social_alpha improve
.venv/bin/python -m social_alpha daily
```

`daily` runs improve after the paper books step and before the report. The report's "Self-improving loop" section shows the cut date, the gates, and the paper-book notes.
