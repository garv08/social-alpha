# social-alpha

**Backtest what people post about Indian stocks on X and Telegram, find which strategies they are really trading, and paper-trade the ideas that hold up — on your own machine.**

social-alpha joins two local projects:

- **[social-archiver](https://github.com/garv08)** — downloads X handles, X lists and Telegram channels (posts + media).
- **[TOP — Trade Observability Platform](https://github.com/garv08/Trade-Observability-Platform)** — daily OHLC for every NSE/BSE stock, ETF and index in DuckDB, plus 95 backtestable strategies.

and adds a **MADE** model (Karpathy's [pytorch-made](https://github.com/karpathy/pytorch-made) masked autoencoder, ported to pure Python) that learns what the technical picture looks like when people post a bullish call.

<p align="center">
  <img src="assets/pipeline.svg" alt="Cron pulls new social posts, joins them to the latest OHLC for every script, backtests, and sorts a top 20 paper list" width="100%">
</p>

Each cron run pulls social posts since the last run and joins them to the latest OHLC already stored for every NSE/BSE script. That pair goes through the backtester. The output is a sorted top 20 paper list. It is not an order.

> [!IMPORTANT]
> Research tool, not advice. Social posts are noisy, archives are incomplete, and backtests describe the past. The paper books trade imaginary money.

## Framework

The picture above is the whole loop: cron, new posts, latest OHLC, backtest, top 20. The desk inside the backtester still follows the [TradingAgents](https://github.com/TauricResearch/TradingAgents) roles (Tauric Research, arXiv 2412.20138), as rules rather than language-model calls. It does not install that project. The portfolio manager can approve a paper name. It cannot reach a broker.

| TradingAgents role | This repo |
|---|---|
| Technical analyst | TOP's 95 strategies on DuckDB bars the cron has already written, dated on or before D |
| Sentiment analyst | X and Telegram calls published by the end of D |
| News analyst | Themes extracted from those posts. There is no separate news wire |
| Fundamentals analyst | Abstains. TOP has no point-in-time filings |
| Bull / bear researchers | The two sides of `decide()` |
| Trader | A paper proposal. Not an order router |
| Risk team | Aggressive, neutral, and conservative. Only the conservative vote can open |
| Portfolio manager | BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL. Only BUY can open a paper book |

```
INGEST  →  PROCESS AS OF DATE D  →  RATE  →  SCORE ON LATER CLOSES  →  PAPER
```

| Stage | Command | What it may see |
|---|---|---|
| Ingest | `ingest`, `fetch` | X handles, X lists, and Telegram channels already downloaded by social-archiver. Each post keeps its timestamp. |
| Process | `extract`, `evaluate`, `desk` | Posts published by the end of the as-of day (IST), TOP bars dated on or before that day, and themes from those posts. No filings. Fundamentals abstain. |
| Backtest | `backtest` | For each bullish call, the desk is rebuilt from creator scores whose 20-session window had already closed. The rating is then scored on the 5/20/60-session excess versus the Nifty 50. |
| Paper | `paper` | Two ₹10,000 books. An entry needs a sanitized desk BUY. Fills are the next open. |

```bash
.venv/bin/python -m social_alpha backtest
.venv/bin/python -m social_alpha backtest --start 2024-01-01 --end 2025-12-31
.venv/bin/python -m social_alpha validate-desk
```

`backtest` rewrites the decision grid. It does not place an order. A BUY that looks good on that grid is still only a paper permission. The technical veto is not applied backwards: confirming it uses later windows, so the historical rating records the opposition and does not pretend the veto was already known.

## What it answers

| Question | Where |
|---|---|
| Which creators' stock calls actually beat the Nifty 50 afterwards? | Creators table (20-session excess return, shrunk for small samples) |
| Which setups (breakout, VCP, RSI…) and themes (FII buying, promoter buying, results…) work? | Setups / Themes tables |
| What were they *really* trading? | Strategy lift: which of TOP's 95 strategies are long at social calls far more often than on random stock-days; per-creator fingerprints |
| Did the call work better when TOP's best strategy for that stock agreed? | "x20 when TOP agreed / disagreed" columns |
| Which stocks look like the next social call today? | MADE scan (log p_calls − log p_background) |
| Would following them make money from here on? | Two paper books, ₹10,000 each, stepped every trading day |

## How it works

1. **Fetch** (optional) — runs social-archiver for each source in `config.toml` with a short trailing window. Archives are resumable and merge.
2. **Ingest** — reads every archive folder under `archive_roots`: X `tweets.json` (handles and lists; list posts are attributed to their real author), Telegram `posts.jsonl` (Telethon) or `messages.jsonl` (bridge). Runs that stopped early are read from `_state.json`.
3. **TOP data** — if TOP's store is behind the latest weekday, runs `top-sync --auto` (which goes through a running TOP server when there is one). Reads the store directly, or through TOP's HTTP API when the server holds the file.
4. **OCR** (optional) — `tesseract`, when installed, reads chart screenshots; the text is a low-confidence extra source.
5. **Extract** — matches TOP's ~8,000 symbols by `NSE:`/`BSE:` prefix, TradingView link, `$cashtag`, `#hashtag` (also `#muthootfinance`-style company hashtags), company name ("TBO Tek Ltd"), or bare ticker. Bare tickers, cashtags and one-word names are trusted only from creators who mostly post about Indian markets, or in posts with Indian-market words (₹, NSE, Nifty, FII, crore…). Then stance, entry (`buy above 112` → stop-entry), stop loss, targets, setups, themes and horizon. A post naming one stock with a direction or levels is a **call**; list posts are **mentions**. An OpenAI-compatible LLM can handle posts the rules cannot settle (off unless configured).
6. **Evaluate** — with no look-ahead:
   - posted before 09:15 IST → fill at that day's open; during market hours → that day's close; after 15:30 or on a holiday → next open;
   - `above`/`below` entries wait up to 5 sessions for the level;
   - stop and target on daily highs/lows (stop first when both fall in one bar; gaps fill at the open); 8% default stop, 20-session time exit;
   - returns 5/20/60 sessions after the fill, and the same minus the Nifty 50 (excess); bearish calls are scored as shorts.
   - TOP state at the last close before the post: which of the 95 strategies were long, and the stock's best strategy by CAGR ÷ |max drawdown| on history before that month.
7. **MADE** — two masked autoencoders over the 95-bit strategy-state vectors: one on bullish calls, one on random liquid stock-days. Retrained weekly; validation loss is reported against an independent-bits baseline so you can see whether it learned anything.
8. **Scan** — today's state of the ~400 most liquid stocks, ranked by the MADE score.
9. **Desk** — the same shape as a research firm ([TradingAgents](https://github.com/TauricResearch/TradingAgents)), without that project's language-model calls or US data vendors. Four rule reports (TOP technical state, social calls, post themes, and an explicit "no filings" fundamentals note) go to a bull/bear read and a risk vote. The portfolio manager rates BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, or SELL. Only BUY can open a paper order. SELL is a note; the existing paper risk rule still places the exit. Nothing in this step calls a broker.
10. **Paper books** (₹10,000 each, whole shares, long only, orders fill at next open, 10 bps per side):
   - **copy** — bullish calls from creators with ≥ 8 closed 20-day calls and a positive shrunk score, counting only calls that had closed before the day (walk-forward). Uses the post's stop and target, otherwise 8% and 20 sessions.
   - **ours** — TOP strategies with social attention as the filter: stocks with bullish mentions in the last 10 days plus the top MADE scan names; for each, the TOP strategy with the best CAGR ÷ drawdown on prior data; buy when it has just turned long, sell when it turns flat or hits the stop.
11. **Improve** — the self-improving loop (see [docs/architecture.md](docs/architecture.md)). Failures in expectancy are turned into proposals. A proposal becomes a paper-book gate only when the same failure repeats on later, unseen calls. Live broker execution is refused.
12. **Report** — `output/report.html` and CSVs (creators, setups, themes, symbols, calls, scan, paper trades, sources).

## Install

Requires Python 3.11–3.13 and a TOP checkout with its data store (`data/nse.duckdb`; see TOP's README for the snapshot).

```bash
git clone https://github.com/garv08/social-alpha.git && cd social-alpha
python3 -m venv .venv
.venv/bin/pip install -e /path/to/Trade-Observability-Platform   # or let pip fetch TOP from GitHub
.venv/bin/pip install -e . --no-deps
cp config.example.toml config.toml                                # set paths, sources, capital
```

No NumPy or PyTorch: everything, including MADE, is plain Python, so it runs on the same old CPUs TOP supports.

## Use

```bash
.venv/bin/python -m social_alpha daily --force   # whole pipeline now
.venv/bin/python -m social_alpha install-cron    # every 30 min; acts once per weekday after 19:30 IST,
                                                 # waits for TOP's update, runs anyway on holidays after 23:30
```

Single steps: `ingest`, `fetch`, `ocr`, `extract [--redo]`, `evaluate`, `train [--rebuild-background]`, `scan`, `desk`, `validate-desk`, `backtest`, `paper`, `improve`, `report`.

`validate-desk` strips markup from stored ratings, cancels any open paper buy that does not have a sanitized desk BUY, and checks the technical veto on calls whose 20-day window closed after the cut date. A veto that does not repeat there is recorded and is not allowed to block a paper entry.

The feedback loop is [docs/architecture.md](docs/architecture.md). Notes on the StockMar list, including what was not OCR'd, are in [docs/stockmar-analysis.md](docs/stockmar-analysis.md).

`scripts/scrape_stockmar_missing.py` back-fills a year of posts for list members that have no archive yet (India-focused handles first).

## Configuration

See [config.example.toml](config.example.toml). Main keys: `archive_roots`, `sources` (what `daily` fetches), `authors` (restrict analysis), `paper_capital`, `paper_max_positions`, `copy_min_calls`, `cost_bps`, `max_hold_days`, `default_stop_pct`, `scan_universe`/`scan_limit`, `llm_url`/`llm_model` (key in the `SOCIAL_ALPHA_LLM_KEY` env var), MADE size and epochs.

## Limitations

- **Levels are mostly in images.** The Indian accounts analysed so far post charts and stock lists; very few posts carry entry/SL/target in text. Without OCR those become *mentions*, scored on 20-day excess return. Install `tesseract-ocr` to read chart headers and levels.
- **Daily bars only.** Intraday calls (Nifty/Bank Nifty option levels) cannot be evaluated properly.
- **Survivorship and coverage.** TOP lists today's scripts; archives cover what was scraped; deleted posts are missing; X search is rate-limited.
- **Small samples.** Scores are shrunk toward zero (n / (n + 10)) and t-stats are shown; treat creators with few calls as unknown.
- **A ₹10,000 book** cannot buy one share of many stocks; those orders are skipped and logged.

## Tests

```bash
python -m unittest discover -s tests -v
```

Covers the MADE port (autoregressive masks, exact gradients, normalised probabilities, learning correlations an independent model cannot), symbol matching, call extraction, post timing and the trade simulator.

## License

MIT. MADE port credits in [NOTICE.md](NOTICE.md).
