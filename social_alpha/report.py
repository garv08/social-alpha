"""Static HTML report + CSV exports."""

from __future__ import annotations

import csv
import datetime as dt
import html
import json
import math

from social_alpha import db, model, paper
from social_alpha.evaluate import group_stats


def _f(v, pct=False, nd=2):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if pct:
        return f"{100 * v:+.{nd - 1}f}%"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return html.escape(str(v))


def _table(rows, cols, title="", note=""):
    if not rows:
        return f"<h3>{title}</h3><p class=muted>none yet</p>"
    head = "".join(f"<th>{html.escape(c[1])}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{c[2](r) if len(c) > 2 else _f(r.get(c[0]))}</td>" for c in cols) + "</tr>" for r in rows)
    return f"<h3>{title}</h3>{f'<p class=muted>{note}</p>' if note else ''}<table><tr>{head}</tr>{body}</table>"


def _write_csv(path, rows):
    if not rows:
        path.write_text("")
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in r.items()})


def _spearman(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 10:
        return None, n

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        for rank, i in enumerate(order):
            r[i] = rank
        return r

    rx, ry = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return (cov / (sx * sy) if sx and sy else None), n


def sources_summary(con):
    return [dict(r) for r in con.execute(
        """SELECT p.author, p.platform, COUNT(*) posts, MIN(date(p.ts_utc / 1000, 'unixepoch')) first,
                  MAX(date(p.ts_utc / 1000, 'unixepoch')) last, SUM(p.n_photos) photos, SUM(p.n_videos) videos,
                  SUM(p.ocr_text IS NOT NULL AND p.ocr_text != '') ocr_done,
                  (SELECT COUNT(DISTINCT c.post_uid) FROM calls c WHERE c.author = p.author) posts_with_stocks,
                  (SELECT COUNT(*) FROM calls c WHERE c.author = p.author AND c.kind = 'call') calls,
                  (SELECT COUNT(*) FROM calls c WHERE c.author = p.author AND c.kind = 'mention') mentions,
                  (SELECT COUNT(DISTINCT c.symbol) FROM calls c WHERE c.author = p.author) symbols
           FROM posts p GROUP BY p.author, p.platform ORDER BY posts_with_stocks DESC""")]


def build(con, cfg, market_date=None) -> dict:
    out = cfg.output_dir
    ids = json.loads(db.get_meta(con, "feature_ids", "[]") or "[]")
    authors = group_stats(con, "author", min_n=1)
    setups = group_stats(con, "setups", min_n=3)
    themes = group_stats(con, "themes", min_n=3)
    symbols = group_stats(con, "symbol", min_n=3)[:60]
    sources = sources_summary(con)
    calls = [dict(r) for r in con.execute(
        """SELECT c.id, c.author, c.platform, datetime(c.ts_utc / 1000, 'unixepoch') posted_utc, c.symbol, c.matched_by, c.stance,
                  c.kind, c.setups, c.themes, c.entry, c.entry_type, c.stop, c.targets, c.horizon, c.confidence,
                  o.signal_date, o.fill_date, o.fill_price, o.exit_date, o.exit_price, o.exit_reason, o.trade_ret,
                  o.r5, o.r20, o.r60, o.x5, o.x20, o.x60, o.n_long, o.top_best, o.top_agree, o.made_score, p.url
           FROM calls c LEFT JOIN outcomes o ON o.call_id = c.id LEFT JOIN posts p ON p.uid = c.post_uid
           ORDER BY c.ts_utc DESC""")]
    scan = json.loads(db.get_meta(con, "scan_latest", "{}") or "{}")
    made_info = json.loads(db.get_meta(con, "made_info", "{}") or "{}")
    lift = model.lifts(con, ids)[:15] if ids else []
    prints = model.author_fingerprints(con, ids) if ids else {}
    books = paper.summary(con, cfg)
    rho, n_rho = _spearman([c["made_score"] for c in calls if c["stance"] == 1], [c["x20"] for c in calls if c["stance"] == 1])
    agree = [c["x20"] for c in calls if c["top_agree"] == 1 and c["x20"] is not None and c["stance"] != 0]
    disagree = [c["x20"] for c in calls if c["top_agree"] == 0 and c["x20"] is not None and c["stance"] != 0]

    for name, rows in (("creators", authors), ("setups", setups), ("themes", themes), ("symbols", symbols),
                       ("calls", calls), ("sources", sources), ("scan", scan.get("rows", [])),
                       ("paper_trades", books["copy"]["trades"] + books["ours"]["trades"])):
        _write_csv(out / f"{name}.csv", rows)

    pct = lambda k: (lambda r: _f(r.get(k), pct=True))  # noqa: E731
    num = lambda k, nd=2: (lambda r: _f(r.get(k), nd=nd))  # noqa: E731
    stat_cols = [("n20", "calls with 20d"), ("x20_mean", "mean excess 20d", pct("x20_mean")), ("x20_median", "median", pct("x20_median")),
                 ("x20_hit", "beat Nifty", pct("x20_hit")), ("x20_t", "t-stat", num("x20_t")), ("score", "shrunk score", pct("score")),
                 ("trade_n", "trades"), ("trade_mean", "avg trade", pct("trade_mean")), ("trade_hit", "trade win", pct("trade_hit")),
                 ("top_agree_x20", "x20 when TOP agreed", pct("top_agree_x20")), ("top_disagree_x20", "x20 when TOP disagreed", pct("top_disagree_x20"))]
    parts = [f"""<!doctype html><meta charset=utf-8><title>social-alpha</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;max-width:1400px;color:#222}}table{{border-collapse:collapse;margin:8px 0 24px}}
td,th{{border:1px solid #ddd;padding:3px 7px;text-align:right;font-variant-numeric:tabular-nums}}th{{background:#f4f4f4}}
td:first-child,th:first-child{{text-align:left}}.muted{{color:#777}}h2{{margin-top:36px;border-bottom:2px solid #eee}}
.pos{{color:#080}}.neg{{color:#b00}}</style>
<h1>social-alpha</h1>
<p class=muted>Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} · market data through {market_date or '?'} ·
{sum(s['posts'] for s in sources):,} posts from {len(sources)} sources · {len(calls):,} stock references
({sum(1 for c in calls if c['kind'] == 'call'):,} calls with a direction/levels) · research only, not advice.</p>"""]
    # paper books
    parts.append("<h2>Paper trading (₹{:,.0f} per book)</h2>".format(cfg.paper_capital))
    for book in paper.BOOKS:
        b = books[book]
        last = b["equity"][-1] if b["equity"] else {"equity": cfg.paper_capital, "cash": cfg.paper_capital, "date": "—"}
        parts.append(f"<h3>{book} · equity ₹{last['equity']:,.0f} ({100 * b['return']:+.2f}%) · cash ₹{last['cash']:,.0f} · "
                     f"{b['n_trades']} closed trades, {b['wins']} wins · as of {last['date']}</h3>")
        parts.append(_table(b["positions"], [("symbol", "symbol"), ("qty", "qty"), ("entry_date", "entry"), ("entry_price", "price"),
                                            ("stop", "stop"), ("target", "target"), ("reason", "why")], "open positions"))
        parts.append(_table(b["orders"], [("symbol", "symbol"), ("side", "side"), ("created", "created"), ("reason", "why")], "orders for next open"))
        parts.append(_table(b["trades"][:20], [("symbol", "symbol"), ("entry_date", "in"), ("exit_date", "out"), ("ret", "return", pct("ret")),
                                              ("pnl", "P&L ₹"), ("exit_reason", "exit"), ("reason", "why")], "recent closed trades"))
    parts.append("<h2>Creators</h2>")
    parts.append(_table(authors, [("author", "creator")] + stat_cols, "",
                        "Excess = stock return minus Nifty 50 over 20 sessions from the fill, sign-flipped for bearish calls. "
                        "Shrunk score = mean × n/(n+10), so a few lucky calls do not top the table. "
                        "Trades use the post's entry/stop/target, else an 8% stop and a 20-session exit."))
    parts.append(_table(setups, [("setups", "setup")] + stat_cols, "Technical setups named in posts"))
    parts.append(_table(themes, [("themes", "theme")] + stat_cols, "Fundamental / flow themes"))
    parts.append(_table(symbols, [("symbol", "symbol"), ("calls", "refs")] + stat_cols, "Most-discussed stocks (3+ refs)"))
    parts.append("<h2>TOP strategies vs. social calls</h2>")
    parts.append(f"<p>Calls where the stock's best TOP strategy (chosen walk-forward on its own history) agreed: "
                 f"n={len(agree)}, mean 20d excess {_f(sum(agree) / len(agree) if agree else None, pct=True)}; "
                 f"disagreed: n={len(disagree)}, {_f(sum(disagree) / len(disagree) if disagree else None, pct=True)}.</p>")
    parts.append(_table(lift, [("strategy", "TOP strategy"), ("p_call", "long at calls", pct("p_call")), ("p_background", "long on random days", pct("p_background")),
                               ("lift", "lift"), ("support", "calls")], "Which TOP strategies are long when people post bullish calls",
                        "Lift > 1: this strategy's state is over-represented at social calls versus random liquid stock-days — the closest "
                        "systematic description of what the crowd is trading."))
    if prints:
        rows = [{"author": a, "fingerprint": ", ".join(f"{x['strategy']} ×{x['lift']:.1f}" for x in v)} for a, v in prints.items()]
        parts.append(_table(rows, [("author", "creator"), ("fingerprint", "top strategies by lift")], "Creator fingerprints"))
    parts.append("<h2>MADE (masked autoencoder) model</h2>")
    parts.append(f"<pre>{html.escape(json.dumps({k: v for k, v in made_info.items() if k not in ('background', 'call')}, indent=1))}</pre>")
    for k in ("background", "call"):
        if k in made_info:
            i = made_info[k]
            parts.append(f"<p>{k}: best validation NLL {i['best_val_nll']} nats vs independent-bits baseline {i['independent_nll']} "
                         f"(train n={i['n_train']}, val n={i['n_val']}, epochs {len(i['history'])}).</p>")
    parts.append(f"<p>Spearman correlation of MADE score at call time with the call's 20d excess return: "
                 f"{_f(rho, nd=3)} (n={n_rho}). Near zero means 'looks like a social call' does not by itself predict returns.</p>")
    parts.append(_table(scan.get("rows", [])[:30], [("symbol", "symbol"), ("close", "close"), ("made_score", "MADE score", num("made_score")),
                        ("n_long", "TOP long"), ("long_strategies", "long strategies", lambda r: html.escape(", ".join(r["long_strategies"][:8])))],
                        f"Today's most 'call-like' stocks ({scan.get('date', '—')})",
                        "Stocks whose TOP strategy state resembles the states at which social traders posted bullish calls."))
    parts.append("<h2>Recent stock references</h2>")
    parts.append(_table(calls[:80], [("posted_utc", "posted (UTC)"), ("author", "creator"), ("symbol", "symbol"), ("kind", "kind"),
                                     ("stance", "stance"), ("setups", "setups"), ("entry", "entry"), ("stop", "stop"), ("targets", "targets"),
                                     ("exit_reason", "exit"), ("trade_ret", "trade", pct("trade_ret")), ("x20", "20d excess", pct("x20")),
                                     ("top_best", "best TOP strategy"), ("top_agree", "agree"),
                                     ("url", "post", lambda r: f"<a href='{html.escape(r['url'] or '')}'>link</a>")]))
    parts.append("<h2>Sources</h2>")
    parts.append(_table(sources, [("author", "source"), ("platform", "platform"), ("posts", "posts"), ("first", "first"), ("last", "last"),
                                  ("photos", "photos"), ("videos", "videos"), ("ocr_done", "OCR'd"), ("posts_with_stocks", "posts naming stocks"),
                                  ("calls", "calls"), ("mentions", "mentions"), ("symbols", "stocks")]))
    (out / "report.html").write_text("\n".join(parts), encoding="utf-8")
    return {"report": str(out / "report.html"), "creators": len(authors), "calls": len(calls)}
