"""Backtest every extracted call on TOP's daily bars, and score creators / setups / themes."""

from __future__ import annotations

import json
import math
import statistics

from social_alpha.market import BENCHMARK, timing

FILL_WINDOW = 5  # sessions a conditional entry (buy above / buy near) may wait


def _valid_levels(call, ref, stance):
    """Drop levels that cannot belong to this stock at this date (wrong scale or wrong side)."""
    entry, stop = call.get("entry"), call.get("stop")
    targets = list(call.get("targets") or [])
    if entry is not None and not (0.7 * ref <= entry <= 1.3 * ref):
        entry = None
    base = entry or ref
    if stop is not None and not (0.6 * base <= stop <= 1.4 * base and (stop - base) * stance < 0):
        stop = None
    targets = [t for t in targets if 0.5 * base <= t <= 2.0 * base and (t - base) * stance > 0]
    targets.sort(key=lambda t: abs(t - base))
    return entry, stop, targets


def simulate(bars, bench, call: dict, signal_date, action_date, fill_mode, cost_bps=10.0, max_hold=20,
             default_stop_pct=8.0, horizons=(5, 20, 60)) -> dict:
    """One call → fill, exit and fixed-horizon returns. Daily bars, no look-ahead:

    - market entries fill at the action day's open (posted outside market hours) or close (posted during)
    - 'above' / 'below' entries wait up to FILL_WINDOW sessions for the level to trade
    - if stop and target fall in the same bar, the stop is assumed to come first
    - bearish calls are scored as shorts (sign-flipped returns), for analysis only
    """
    st = call.get("stance") or 1
    stance = 1 if st >= 0 else -1
    last = bars.dates[-1]
    out = {"signal_date": signal_date, "action_date": action_date, "filled": 0, "resolved": 0, "data_through": last}
    s = bars.last_index_on_or_before(signal_date) if signal_date else -1
    if s < 0:
        out.update(exit_reason="nodata", resolved=1)
        return out
    ref = bars.c[s]
    if action_date is None or action_date > last:
        out["exit_reason"] = "pending"
        return out
    a = bars.last_index_on_or_before(action_date)
    if a < 0 or bars.dates[a] < action_date:
        a += 1
    if a >= len(bars):
        out["exit_reason"] = "pending"
        return out
    entry, stop, targets = _valid_levels(call, ref, stance)
    etype = call.get("entry_type") or "market"
    fill_i = fill_px = None
    check_from = None
    if entry is None or etype == "market":
        if fill_mode == "close":
            fill_i, fill_px, check_from = a, bars.c[a], a + 1
        else:
            fill_i, fill_px, check_from = a, bars.o[a], a
    else:
        start = a + 1 if fill_mode == "close" else a
        for k in range(start, min(start + FILL_WINDOW, len(bars))):
            o, h, l = bars.o[k], bars.h[k], bars.l[k]
            if etype == "above":
                if o >= entry:
                    fill_i, fill_px, check_from = k, o, k
                elif h >= entry:
                    fill_i, fill_px, check_from = k, entry, k + 1
            else:
                if o <= entry:
                    fill_i, fill_px, check_from = k, o, k
                elif l <= entry:
                    fill_i, fill_px, check_from = k, entry, k + 1
            if fill_i is not None:
                break
        if fill_i is None:
            done = start + FILL_WINDOW <= len(bars)
            out.update(exit_reason="nofill" if done else "pending", resolved=int(done))
            return out
    if stop is None:
        stop = fill_px * (1 - stance * default_stop_pct / 100.0)
    target = targets[0] if targets else None
    out.update(filled=1, fill_date=bars.dates[fill_i], fill_price=fill_px)

    exit_i = exit_px = reason = None
    end_i = fill_i + max_hold
    mfe = mae = 0.0
    for k in range(check_from, min(end_i, len(bars) - 1) + 1):
        o, h, l, c = bars.o[k], bars.h[k], bars.l[k], bars.c[k]
        fav, adv = (h, l) if stance == 1 else (l, h)
        mfe = max(mfe, stance * (fav / fill_px - 1))
        mae = min(mae, stance * (adv / fill_px - 1))
        hit_stop_open = (o - stop) * stance <= 0
        hit_tgt_open = target is not None and (o - target) * stance >= 0
        if hit_stop_open or hit_tgt_open:
            exit_i, exit_px, reason = k, o, "stop" if hit_stop_open else "target"
            break
        if (adv - stop) * stance <= 0:
            exit_i, exit_px, reason = k, stop, "stop"
            break
        if target is not None and (fav - target) * stance >= 0:
            exit_i, exit_px, reason = k, target, "target"
            break
        if k == end_i:
            exit_i, exit_px, reason = k, c, "time"
            break
    cost = 2 * cost_bps / 10_000
    if exit_i is None:
        mark = bars.c[-1]
        out.update(exit_reason="open", exit_price=mark, trade_ret=stance * (mark / fill_px - 1) - cost)
    else:
        out.update(exit_date=bars.dates[exit_i], exit_price=exit_px, exit_reason=reason,
                   trade_ret=stance * (exit_px / fill_px - 1) - cost)
    out.update(mfe=mfe, mae=mae)

    bench_ref = None
    if bench is not None:
        bi = bench.index.get(bars.dates[fill_i])
        if bi is not None:
            bench_ref = bench.o[bi] if (fill_mode == "open" and check_from == fill_i) else bench.c[bi]
    complete = True
    for h in horizons:
        k = fill_i + h
        if k >= len(bars):
            complete = False
            continue
        r = bars.c[k] / fill_px - 1
        out[f"r{h}"] = stance * r
        if bench_ref and bench is not None and bars.dates[k] in bench.index:
            rb = bench.c[bench.index[bars.dates[k]]] / bench_ref - 1
            out[f"x{h}"] = stance * (r - rb)
    out["resolved"] = int(complete and exit_i is not None)
    return out


OUT_COLS = ["signal_date", "action_date", "filled", "fill_date", "fill_price", "exit_date", "exit_price", "exit_reason",
            "trade_ret", "r5", "r20", "r60", "x5", "x20", "x60", "mfe", "mae", "resolved", "data_through",
            "n_long", "top_best", "top_agree", "made_score"]


def run(con, market, cfg, log=print, features_cb=None) -> dict:
    """(Re)evaluate calls that are new or not yet resolved. features_cb(symbol, bars, rows) adds TOP/MADE columns."""
    cal = market.calendar()
    bench = market.bars(BENCHMARK)
    rows = con.execute(
        """SELECT c.*, o.n_long AS prev_n_long, o.top_best AS prev_top_best, o.top_agree AS prev_top_agree,
                  o.made_score AS prev_made_score
           FROM calls c LEFT JOIN outcomes o ON o.call_id = c.id
           WHERE o.call_id IS NULL OR (o.resolved = 0 AND (o.data_through IS NULL OR o.data_through < ?))
           ORDER BY c.symbol, c.ts_utc""", (cal[-1] if cal else "",)).fetchall()
    by_symbol: dict[str, list] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(dict(r))
    done = 0
    for n, (sym, calls) in enumerate(by_symbol.items(), 1):
        bars = market.bars(sym)
        if bars is None or len(bars) < 2:
            for c in calls:
                _save(con, c["id"], {"exit_reason": "nodata", "resolved": 1, "data_through": cal[-1] if cal else None})
            continue
        results = []
        for c in calls:
            if c.get("targets") and isinstance(c["targets"], str):
                c["targets"] = json.loads(c["targets"])
            sig, act, mode = timing(c["ts_utc"], cal)
            res = simulate(bars, bench, c, sig, act, mode, cfg.cost_bps, cfg.max_hold_days, cfg.default_stop_pct, cfg.horizons)
            for k in ("n_long", "top_best", "top_agree", "made_score"):
                res[k] = c.get("prev_" + k)
            results.append((c, res))
        if features_cb is not None:
            features_cb(sym, bars, results)
        for c, res in results:
            _save(con, c["id"], res)
            done += 1
        con.commit()
        if n % 50 == 0:
            log(f"    evaluated {n}/{len(by_symbol)} symbols")
    return {"calls": done, "symbols": len(by_symbol)}


def _save(con, call_id, res):
    vals = [res.get(k) for k in OUT_COLS]
    con.execute(f"INSERT OR REPLACE INTO outcomes(call_id, {', '.join(OUT_COLS)}) VALUES (?{', ?' * len(OUT_COLS)})",
                (call_id, *vals))


# ---- scoring -------------------------------------------------------------------
SHRINK = 10  # pseudo-calls pulling small samples toward zero edge


def summarize(values: list[float]) -> dict:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    n = len(vals)
    if not n:
        return {"n": 0}
    mean = sum(vals) / n
    sd = statistics.pstdev(vals) if n > 1 else 0.0
    return {"n": n, "mean": mean, "median": statistics.median(vals), "hit": sum(v > 0 for v in vals) / n,
            "t": mean / (sd / math.sqrt(n)) if sd > 0 and n > 1 else 0.0, "shrunk": mean * n / (n + SHRINK)}


def group_stats(con, key: str, as_of: str | None = None, min_n=1, where="", params=()) -> list[dict]:
    """Stats per author / symbol / setup / theme on x20 (excess vs Nifty 50) and trade_ret.

    as_of keeps only calls whose 20-session window ended on or before that date (walk-forward use).
    """
    rows = con.execute(
        f"""SELECT c.author, c.symbol, c.kind, c.stance, c.setups, c.themes, c.horizon, o.*
            FROM calls c JOIN outcomes o ON o.call_id = c.id
            WHERE o.filled = 1 AND c.stance != 0 {where}""", params).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        if as_of is not None and (r["exit_date"] is None or r["exit_date"] > as_of or r["x20"] is None):
            continue
        if key in ("setups", "themes"):
            keys = json.loads(r[key] or "[]") or ["(none)"]
        else:
            keys = [r[key] or "(none)"]
        for k in keys:
            g = groups.setdefault(k, {"x20": [], "trade": [], "calls": 0, "agree": [], "disagree": []})
            g["calls"] += 1
            g["x20"].append(r["x20"])
            g["trade"].append(r["trade_ret"])
            if r["top_agree"] == 1:
                g["agree"].append(r["x20"])
            elif r["top_agree"] == 0:
                g["disagree"].append(r["x20"])
    out = []
    for k, g in groups.items():
        sx, st = summarize(g["x20"]), summarize(g["trade"])
        if sx.get("n", 0) < min_n and st.get("n", 0) < min_n:
            continue
        out.append({key: k, "calls": g["calls"], "n20": sx.get("n", 0), "x20_mean": sx.get("mean"), "x20_median": sx.get("median"),
                    "x20_hit": sx.get("hit"), "x20_t": sx.get("t"), "score": sx.get("shrunk", 0.0),
                    "trade_n": st.get("n", 0), "trade_mean": st.get("mean"), "trade_hit": st.get("hit"),
                    "top_agree_x20": summarize(g["agree"]).get("mean"), "top_agree_n": len([v for v in g["agree"] if v is not None]),
                    "top_disagree_x20": summarize(g["disagree"]).get("mean")})
    out.sort(key=lambda d: d["score"] or 0, reverse=True)
    return out
