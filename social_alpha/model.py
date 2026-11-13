"""TOP strategy-state features, MADE training/scoring, strategy fingerprints, and the daily scan.

Feature vector for (stock, date): bit k = 1 when TOP strategy k is long at that close.
  call vectors        stock/date of each social call (signal_date: last close before the post)
  background vectors  random liquid stock-days (what the market looks like in general)
Two MADE models are trained, p_call and p_bg. made_score = log p_call(x) - log p_bg(x) is high
for stocks whose current technical state resembles what social traders tend to post about.
"""

from __future__ import annotations

import json
import math
import random

from social_alpha import db
from social_alpha.made import MADE, Independent

WARMUP = 260
SCAN_BARS = 400


def check_feature_ids(con, market, log=print) -> list[str]:
    ids = market.strategy_ids()
    old = db.get_meta(con, "feature_ids")
    if old and json.loads(old) != ids:
        log("  TOP strategy list changed: clearing stored features")
        con.execute("DELETE FROM features")
        con.execute("UPDATE outcomes SET n_long = NULL, top_best = NULL, top_agree = NULL, made_score = NULL")
    db.set_meta(con, "feature_ids", json.dumps(ids))
    con.commit()
    return ids


def make_features_cb(con, market, cfg):
    """Callback for evaluate.run: adds TOP state, walk-forward best strategy and stores the call vector."""
    ids = market.strategy_ids()

    def cb(symbol, bars, results):
        todo = [(c, r) for c, r in results if r.get("n_long") is None and r.get("signal_date")]
        if not todo or len(bars) < 60:
            return
        dates, sig = market.signals(bars)
        metrics_cache = {}
        for c, r in todo:
            i = bars.last_index_on_or_before(r["signal_date"])
            if i < 0:
                continue
            bits = market.bits_at(sig, ids, i)
            r["n_long"] = bits.count("1")
            con.execute("INSERT OR REPLACE INTO features(symbol, date, origin, bits) VALUES (?, ?, 'call', ?)",
                        (symbol, bars.dates[i], bits))
            month = bars.dates[i][:7]
            if month not in metrics_cache:
                # history before the month starts: the choice could have been made before any call in it
                end = bars.last_index_on_or_before(month + "-00")
                metrics_cache[month] = market.strategy_metrics(bars, sig, end, cfg.cost_bps) if end >= WARMUP else {}
            ms = metrics_cache[month]
            if ms:
                best = max(ms, key=lambda s: ms[s]["score"])
                r["top_best"] = best
                r["top_agree"] = int(sig[best][i] == 1.0) if c.get("stance", 0) >= 0 else int(sig[best][i] == 0.0)
    return cb


def build_background(con, market, cfg, n_symbols=120, per_symbol=30, seed=7, log=print) -> int:
    ids = market.strategy_ids()
    rng = random.Random(seed)
    universe = market.liquid_symbols(max(n_symbols * 3, 300), cfg.min_traded_value_cr)
    rng.shuffle(universe)
    added = 0
    for n, sym in enumerate(universe[:n_symbols], 1):
        bars = market.bars(sym)
        if bars is None or len(bars) < WARMUP + 30:
            continue
        _, sig = market.signals(bars)
        for i in rng.sample(range(WARMUP, len(bars)), min(per_symbol, len(bars) - WARMUP)):
            con.execute("INSERT OR REPLACE INTO features(symbol, date, origin, bits) VALUES (?, ?, 'background', ?)",
                        (sym, bars.dates[i], market.bits_at(sig, ids, i)))
            added += 1
        con.commit()
        if n % 20 == 0:
            log(f"    background {n}/{n_symbols} symbols")
    db.set_meta(con, "background_built", market.latest_date())
    con.commit()
    return added


def _vectors(con, origin, where="", params=()):
    rows = con.execute(f"SELECT DISTINCT f.symbol, f.date, f.bits FROM features f {where} "
                       f"{'AND' if where else 'WHERE'} f.origin = ?", (*params, origin)).fetchall()
    return [(r["symbol"], r["date"], [int(ch) for ch in r["bits"]]) for r in rows]


def call_vectors(con):
    """Feature vectors of bullish calls (deduplicated by stock/date)."""
    return _vectors(con, "call", """JOIN calls c ON c.symbol = f.symbol
                                     JOIN outcomes o ON o.call_id = c.id AND o.signal_date IS NOT NULL
                                     WHERE c.stance = 1 AND f.date <= o.signal_date
                                       AND f.date = (SELECT MAX(date) FROM features g WHERE g.symbol = f.symbol
                                                     AND g.origin = 'call' AND g.date <= o.signal_date)""")


def train(con, cfg, data_dir, log=print, min_calls=40) -> dict:
    calls = [v for _, _, v in call_vectors(con)]
    bg = [v for _, _, v in _vectors(con, "background")]
    info = {"call_vectors": len(calls), "background_vectors": len(bg)}
    if len(bg) < 200:
        info["status"] = "not enough background vectors"
        return info
    nin = len(bg[0])
    log(f"  MADE background model: {len(bg)} vectors x {nin} TOP strategies")
    m_bg = MADE(nin, [cfg.made_hidden], num_masks=cfg.made_masks, seed=1)
    fit_bg = m_bg.fit(bg, epochs=cfg.made_epochs, log=log)
    info["background"] = {**fit_bg, "independent_nll": round(Independent(bg).nll(bg), 4)}
    (data_dir / "made_background.json").write_text(json.dumps(m_bg.to_json()))
    if len(calls) < min_calls:
        info["status"] = f"background trained; only {len(calls)} bullish call vectors (need {min_calls}) for the call model"
        return info
    log(f"  MADE call model: {len(calls)} vectors")
    m_call = MADE(nin, [max(8, cfg.made_hidden // 2)], num_masks=cfg.made_masks, seed=2)
    fit_call = m_call.fit(calls, epochs=cfg.made_epochs, weight_decay=1e-3, log=log)
    info["call"] = {**fit_call, "independent_nll": round(Independent(calls).nll(calls), 4)}
    (data_dir / "made_calls.json").write_text(json.dumps(m_call.to_json()))
    info["status"] = "trained"
    db.set_meta(con, "made_info", json.dumps(info))
    con.commit()
    return info


def load_models(data_dir):
    try:
        bg = MADE.from_json(json.loads((data_dir / "made_background.json").read_text()))
        call = MADE.from_json(json.loads((data_dir / "made_calls.json").read_text()))
        return call, bg
    except (OSError, ValueError, KeyError):
        return None, None


def score_calls(con, data_dir) -> int:
    call_m, bg_m = load_models(data_dir)
    if call_m is None:
        return 0
    rows = con.execute("""SELECT o.call_id, f.bits FROM outcomes o JOIN calls c ON c.id = o.call_id
                          JOIN features f ON f.symbol = c.symbol AND f.origin = 'call'
                           AND f.date = (SELECT MAX(date) FROM features g WHERE g.symbol = c.symbol AND g.origin = 'call'
                                         AND g.date <= o.signal_date)""").fetchall()
    cache = {}
    for r in rows:
        if r["bits"] not in cache:
            x = [float(ch) for ch in r["bits"]]
            cache[r["bits"]] = call_m.log_prob(x) - bg_m.log_prob(x)
        con.execute("UPDATE outcomes SET made_score = ? WHERE call_id = ?", (cache[r["bits"]], r["call_id"]))
    con.commit()
    return len(rows)


def lifts(con, ids, where="", params=(), min_support=3) -> list[dict]:
    """Which TOP strategies are long far more often at social calls than on random stock-days."""
    bg = [v for _, _, v in _vectors(con, "background")]
    calls = [v for _, _, v in _vectors(con, "call", where, params)] if where else [v for _, _, v in call_vectors(con)]
    if not bg or not calls:
        return []
    out = []
    for k, sid in enumerate(ids):
        pc = (sum(v[k] for v in calls) + 1) / (len(calls) + 2)
        pb = (sum(v[k] for v in bg) + 1) / (len(bg) + 2)
        support = sum(v[k] for v in calls)
        if support >= min_support:
            out.append({"strategy": sid, "p_call": pc, "p_background": pb, "lift": pc / pb, "support": support})
    out.sort(key=lambda d: d["lift"], reverse=True)
    return out


def author_fingerprints(con, ids, min_calls=5, top=5) -> dict:
    out = {}
    authors = con.execute("""SELECT c.author, COUNT(DISTINCT c.symbol || o.signal_date) n FROM calls c
                             JOIN outcomes o ON o.call_id = c.id WHERE c.stance = 1 AND o.n_long IS NOT NULL
                             GROUP BY c.author HAVING n >= ?""", (min_calls,)).fetchall()
    for a in authors:
        where = """JOIN calls c ON c.symbol = f.symbol JOIN outcomes o ON o.call_id = c.id AND o.signal_date IS NOT NULL
                   WHERE c.stance = 1 AND c.author = ? AND f.date = (SELECT MAX(date) FROM features g WHERE g.symbol = f.symbol
                   AND g.origin = 'call' AND g.date <= o.signal_date)"""
        out[a["author"]] = lifts(con, ids, where, (a["author"],), min_support=2)[:top]
    return out


def scan(con, market, cfg, log=print) -> list[dict]:
    """Score today's state of the scan universe with both MADE models (top = most 'call-like')."""
    ids = market.strategy_ids()
    call_m, bg_m = load_models(cfg.data_dir)
    if cfg.scan_universe == "nifty50":
        universe = [s["symbol"] for s in market.symbols() if s["universe"] == "nifty50"]
    elif cfg.scan_universe == "mentioned":
        universe = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM calls WHERE symbol NOT LIKE 'IDX:%'")]
    else:
        universe = market.liquid_symbols(cfg.scan_limit, cfg.min_traded_value_cr)
    latest = market.latest_date()
    results = []
    for n, sym in enumerate(universe, 1):
        bars = market.bars(sym)
        if bars is None or len(bars) < WARMUP or bars.dates[-1] != latest:
            continue
        _, sig = market.signals(bars, last_n=SCAN_BARS)
        bits = market.bits_at(sig, ids, -1)
        con.execute("INSERT OR REPLACE INTO features(symbol, date, origin, bits) VALUES (?, ?, 'scan', ?)", (sym, latest, bits))
        x = [float(ch) for ch in bits]
        row = {"symbol": sym, "date": latest, "n_long": bits.count("1"), "close": bars.c[-1],
               "long_strategies": [s for s, b in zip(ids, bits) if b == "1"]}
        if call_m is not None:
            row["lp_call"], row["lp_bg"] = call_m.log_prob(x), bg_m.log_prob(x)
            row["made_score"] = row["lp_call"] - row["lp_bg"]
        results.append(row)
        if n % 50 == 0:
            log(f"    scanned {n}/{len(universe)}")
            con.commit()
    con.commit()
    results.sort(key=lambda r: r.get("made_score", -math.inf), reverse=True)
    db.set_meta(con, "scan_latest", json.dumps({"date": latest, "rows": results[:100]}))
    con.commit()
    return results
