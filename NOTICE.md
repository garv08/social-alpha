# Notices

- `social_alpha/made.py` is a pure-Python port of Andrej Karpathy's
  [pytorch-made](https://github.com/karpathy/pytorch-made) (MIT License,
  Copyright (c) 2017 Andrej Karpathy), implementing MADE from Germain, Gregor,
  Murray & Larochelle, "MADE: Masked Autoencoder for Distribution Estimation",
  ICML 2015 (arXiv:1502.03509). The mask construction and ensemble-of-orderings
  scheme follow that code.
- The `top` package is vendored from
  [TOP — Trade Observability Platform](https://github.com/garv08/Trade-Observability-Platform)
  (MIT, Copyright garv08). It is the DuckDB store, OHLC sync, indicators,
  strategies, and chart desk. Its license is [top/LICENSE](top/LICENSE).
- Chart drawing uses TradingView Lightweight Charts™ (Apache-2.0), unmodified,
  in `top/web/vendor/` and `social_alpha/monitor_web/vendor/`. The chart shows
  the "Charting by TradingView" link. This project is not affiliated with
  TradingView and does not use TradingView's private chart protocol.
- Posts are collected with [social-archiver](https://github.com/garv08) outputs.
  Scraped posts and media are personal research data and are **not** part of this
  repository. Scraping X/Twitter and Telegram may violate their terms of service;
  keep volumes low and do not republish other people's posts.
- The conviction blend in `social_alpha/conviction.py` follows the published
  scoring shape of [renee-jia/trading-bot](https://github.com/renee-jia/trading-bot)
  (MIT harness): four lenses, a 0–100 score, letter grades, a 25% name cap,
  a bear-market cash reserve, and a volatility target that never levers.
  Their private `core/` engine is not in that repository and is not used.
  Their US equity results are not this system's results. There is no Alpaca
  executor here.
- Nothing here is investment advice. Backtests describe the past, and the paper
  books trade imaginary money.
