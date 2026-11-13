# StockMar list: what was actually in the posts

List id `1518522655564918784` (slug `StockMar`). 44 members were stored with the list scrape. This note is the coverage and the India-market read before treating the list as a strategy source. It is not a claim that the list's ideas make money.

Numbers below are from the local social-alpha database after the second evaluation (4,144 filled calls across 1,021 symbols; report rewritten 14 Nov 2026 00:14 IST). Scores are 20-session excess return versus the Nifty 50, shrunk by `n / (n + 10)`. They describe the past. Many levels live in chart images, and tesseract is not installed, so OCR was not run.

## Coverage

A year of posts is not the same as "the handle appeared in the list feed".

| Handle | Posts in DB | Photos | India-market use |
|---|---:|---:|---|
| stockmarket8058 | 8,262 | 3,781 | Stock lists. Many names, few explicit levels in text. Trade mean is negative under the default 8% stop / 20-session exit. |
| adi408b | 1,850 | 1,027 | Large call sample. Small positive excess; trade mean about flat. |
| subi_sa | 1,310 | 666 | Negative excess on the calls that matched. |
| stageanalysis | 1,264 | 685 | Stage-analysis charts. Almost no Indian symbols matched in text (7 references). |
| TradZoo | 991 | 552 | Mostly Nifty / Bank Nifty chart posts. Excess about flat to slightly negative. |
| jitu_stock | 795 | 178 | India-focused. Small sample, positive shrunk score, t-stat about 1.5. Not enough to treat as a proven edge. |
| nooreshtech | 746 | 246 | India commentary. Excess negative; a few trades with levels were positive. Small n. |
| ItsAshishKSingh | 281 | 75 | Small sample, positive excess, t-stat about 1.5. TOP agreement did not explain it (agreeing calls were worse). |

The other members are mostly crypto, AI, or global quant accounts. Several have only the list-feed crumbs (1–21 posts), not a year: `Derzuschauer7` had none, and handles such as `RohOnChain`, `niraj_munot`, `Slonski_rt`, and `qwinsi0x` are not usable as strategy sources yet. `DGPIndia` is a Telegram channel ingested from another archive; it is politics with incidental market words, not a StockMar member, and its matched "calls" are weak.

Media was downloaded for the India-priority handles above. It was not read. Chart prices, trendlines, and handwritten levels are missing from the text extract. Do not treat a missing level as "no trade plan".

## What the text scores say

After the second ingest, creators with a closed 20-day window, best shrunk score first:

| Creator | n20 | mean excess | t | shrunk score | trade mean |
|---|---:|---:|---:|---:|---:|
| ItsAshishKSingh | 28 | +4.9% | 1.48 | +3.6% | +3.2% |
| jitu_stock | 34 | +3.7% | 1.48 | +2.9% | +1.5% |
| adi408b | 1,460 | +2.0% | 5.28 | +2.0% | ~0 |
| stockmarket8058 | 2,266 | +1.7% | 5.75 | +1.7% | −0.9% |
| TradZoo | 42 | −0.2% | −0.20 | −0.2% | −0.6% |
| nooreshtech | 39 | −0.7% | −0.28 | −0.6% | +1.3% |
| subi_sa | 112 | −1.0% | −0.96 | −0.9% | (negative excess) |

Reading this narrowly:

- A high t-stat on stockmarket8058 and adi408b is a large sample of small excess, not a large edge. stockmarket8058's actual stop/time-exit trades lost money. Mentions of a name going up is not the same as a trade you could have copied.
- jitu_stock and ItsAshishKSingh look better and are too small to clear a robustness bar. The engine will not block or promote them until an unseen window repeats the result with at least 8 calls on each side.
- Setups named in text that looked better than a plain mention: support, volume. Breakout calls did not beat the Nifty once costs and the default exit are applied. That is a candidate for the engine's setup gate, not a conclusion, until the unseen window agrees.
- TOP agreement did not uniformly help. For stockmarket8058, agreeing calls were not better than disagreeing ones.

## What this does not support

- It does not say the other 30+ handles have no Indian ideas. Their year archives were incomplete when this was written, and their charts were not OCR'd.
- It does not say a positive shrunk score should be copied with real money. The copy book still requires a walk-forward minimum and a positive score, and the improve step can block a creator only after the loss repeats out of sample.
- It does not add the 151 strategies from SSRN 3247865. Those were reviewed earlier and left out on purpose.

Next daily run can ingest whatever the background scrape still lands. That will not, by itself, move a gate.
