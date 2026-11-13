"""Two paper-trading books, stepped once per new trading day (idempotent).

copy  buy bullish calls from creators whose earlier calls beat the Nifty 50 (walk-forward: only
      calls whose 20-session window had closed count); stop/target from the post or defaults.
ours  TOP's own strategies, with social attention as the stock filter: candidates are stocks with
      recent bullish mentions plus the top MADE "call-like" scan names; for each, pick the TOP
      strategy with the best CAGR / max-drawdown on its history, and buy when that strategy has
      just turned long. Exit when it turns flat, on the stop, or after the hold limit.

Mechanics: orders are created after the close of day D and fill at the open of D+1. Whole shares
only (a ₹10,000 book cannot buy one share of some stocks; those are skipped and logged).
Long only, since short delivery is not possible in Indian cash equities.
"""

from __future__ import annotations

import json

from social_alpha import db
from social_alpha.evaluate import group_stats

BOOKS = ("copy", "ours")
RECENT_DAYS = 10
FRESH_BARS = 3


def _cash(con, book, capital):
    row = con.execute("SELECT cash FROM paper_equity WHERE book = ? ORDER BY date DESC LIMIT 1", (book,)).fetchone()
    return capital if row is None else row["cash"]


def _positions(con, book):
    return {r["symbol"]: dict(r) for r in con.execute("SELECT * FROM paper_positions WHERE book = ?", (book,))}


def _sessions_between(bars, d0, d1):
    i0, i1 = bars.last_index_on_or_before(d0), bars.last_index_on_or_before(d1)
    return max(0, i1 - i0)


def step(con, market, cfg, log=print) -> dict:
    today = market.latest_date()
    last = db.get_meta(con, "paper_last_date")
    if last and last >= today:
        return {"status": f"already stepped for {today}"}
    cost = cfg.cost_bps / 10_000
    report = {"date": today, "books": {}}
    for book in BOOKS:
        cash = _cash(con, book, cfg.paper_capital)
        pos = _positions(con, book)
        notes = []
        # 1. fill orders created before today at today's open
        for o in con.execute("SELECT * FROM paper_orders WHERE book = ? AND status = 'open' AND created < ?", (book, today)).fetchall():
            bars = market.bars(o["symbol"])
            i = bars.index.get(today) if bars else None
            if i is None:
                con.execute("UPDATE paper_orders SET status = 'cancelled', reason = reason || ' | no bar today' WHERE id = ?", (o["id"],))
                continue
            px = bars.o[i]
            if o["side"] == "buy":
                if o["symbol"] in pos or len(pos) >= cfg.paper_max_positions:
                    con.execute("UPDATE paper_orders SET status = 'cancelled' WHERE id = ?", (o["id"],))
                    continue
                slot = min(cash, (cash + sum(p["qty"] * p["entry_price"] for p in pos.values())) / cfg.paper_max_positions)
                qty = int(slot // (px * (1 + cost)))
                if qty < 1:
                    notes.append(f"skip {o['symbol']}: one share ₹{px:,.0f} > slot ₹{slot:,.0f}")
                    con.execute("UPDATE paper_orders SET status = 'cancelled', reason = reason || ' | unaffordable' WHERE id = ?", (o["id"],))
                    continue
                stop = o["stop"] if o["stop"] and o["stop"] < px else px * (1 - cfg.default_stop_pct / 100)
                target = o["target"] if o["target"] and o["target"] > px else None
                cash -= qty * px * (1 + cost)
                pos[o["symbol"]] = {"book": book, "symbol": o["symbol"], "qty": qty, "entry_date": today, "entry_price": px,
                                    "stop": stop, "target": target, "hold_days": o["hold_days"], "reason": o["reason"], "ref": o["ref"]}
                con.execute("INSERT OR REPLACE INTO paper_positions VALUES (:book, :symbol, :qty, :entry_date, :entry_price, :stop, :target, :hold_days, :reason, :ref)",
                            pos[o["symbol"]])
                con.execute("UPDATE paper_orders SET status = 'filled', fill_date = ?, fill_price = ?, qty = ? WHERE id = ?", (today, px, qty, o["id"]))
            else:  # sell order (strategy exit) at today's open
                p = pos.get(o["symbol"])
                if p:
                    cash += _close(con, book, p, today, px, "signal", cost)
                    del pos[o["symbol"]]
                con.execute("UPDATE paper_orders SET status = 'filled', fill_date = ?, fill_price = ? WHERE id = ?", (today, px, o["id"]))
        # 2. stops / targets / time exits on today's bar
        for sym, p in list(pos.items()):
            bars = market.bars(sym)
            i = bars.index.get(today) if bars else None
            if i is None:
                continue
            o, h, l, c = bars.o[i], bars.h[i], bars.l[i], bars.c[i]
            exit_px = reason = None
            if p["entry_date"] != today and o <= p["stop"]:
                exit_px, reason = o, "stop"
            elif p["target"] and p["entry_date"] != today and o >= p["target"]:
                exit_px, reason = o, "target"
            elif l <= p["stop"]:
                exit_px, reason = p["stop"], "stop"
            elif p["target"] and h >= p["target"]:
                exit_px, reason = p["target"], "target"
            elif p["hold_days"] and _sessions_between(bars, p["entry_date"], today) >= p["hold_days"]:
                exit_px, reason = c, "time"
            if exit_px is not None:
                cash += _close(con, book, p, today, exit_px, reason, cost)
                del pos[sym]
        # 3. mark to market
        equity = cash
        for sym, p in pos.items():
            bars = market.bars(sym)
            equity += p["qty"] * (bars.c[-1] if bars else p["entry_price"])
        con.execute("INSERT OR REPLACE INTO paper_equity(book, date, cash, equity) VALUES (?, ?, ?, ?)", (book, today, cash, equity))
        report["books"][book] = {"cash": round(cash, 2), "equity": round(equity, 2), "positions": len(pos), "notes": notes}
    # 4. new orders for tomorrow's open
    report["orders"] = _new_orders(con, market, cfg, today, last)
    db.set_meta(con, "paper_last_date", today)
    con.commit()
    return report


def _close(con, book, p, date, px, reason, cost):
    proceeds = p["qty"] * px * (1 - cost)
    basis = p["qty"] * p["entry_price"] * (1 + cost)
    con.execute("""INSERT INTO paper_trades(book, symbol, qty, entry_date, entry_price, exit_date, exit_price, pnl, ret, reason, exit_reason, ref)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (book, p["symbol"], p["qty"], p["entry_date"], p["entry_price"], date, px, proceeds - basis,
                 proceeds / basis - 1, p["reason"], reason, p["ref"]))
    con.execute("DELETE FROM paper_positions WHERE book = ? AND symbol = ?", (book, p["symbol"]))
    return proceeds


def _order(con, book, symbol, side, today, reason, stop=None, target=None, hold=None, ref=None):
    exists = con.execute("SELECT 1 FROM paper_orders WHERE book = ? AND symbol = ? AND status = 'open'", (book, symbol)).fetchone()
    if exists:
        return False
    con.execute("""INSERT INTO paper_orders(book, symbol, side, created, reason, stop, target, hold_days, ref)
                   VALUES (?,?,?,?,?,?,?,?,?)""", (book, symbol, side, today, reason, stop, target, hold, ref))
    return True


def _new_orders(con, market, cfg, today, last):
    made = {"copy": [], "ours": []}
    # copy book: calls posted since the previous step (first run: calls whose signal date is today)
    good = {g["author"]: g for g in group_stats(con, "author", as_of=today, min_n=cfg.copy_min_calls)
            if g["n20"] >= cfg.copy_min_calls and (g["score"] or 0) > cfg.copy_min_score}
    held = _positions(con, "copy")
    fresh = con.execute("""SELECT c.*, o.signal_date FROM calls c JOIN outcomes o ON o.call_id = c.id
                           WHERE c.stance = 1 AND c.symbol NOT LIKE 'IDX:%' AND o.signal_date > ? AND o.signal_date <= ?
                           ORDER BY c.ts_utc DESC""", (last or _prev(market, today), today)).fetchall()
    for c in fresh:
        g = good.get(c["author"])
        if g is None or c["symbol"] in held:
            continue
        targets = json.loads(c["targets"] or "[]")
        if _order(con, "copy", c["symbol"], "buy", today, f"@{c['author']} {c['kind']} (score {g['score']:+.3f})",
                  c["stop"], targets[0] if targets else None, cfg.max_hold_days, c["post_uid"]):
            made["copy"].append(c["symbol"])
    # ours: strategy exits for held names, then fresh entries
    held = _positions(con, "ours")
    for sym, p in held.items():
        bars = market.bars(sym)
        if bars is None or not p["ref"]:
            continue
        _, sig = market.signals(bars, last_n=600)
        if sig.get(p["ref"]) and sig[p["ref"]][-1] == 0.0:
            _order(con, "ours", sym, "sell", today, f"{p['ref']} turned flat")
    since = _days_before(today, RECENT_DAYS)
    mentioned = [r[0] for r in con.execute("""SELECT c.symbol FROM calls c JOIN outcomes o ON o.call_id = c.id
                 WHERE c.stance = 1 AND c.symbol NOT LIKE 'IDX:%' AND o.signal_date >= ? GROUP BY c.symbol
                 ORDER BY COUNT(*) DESC LIMIT 40""", (since,))]
    scan = json.loads(db.get_meta(con, "scan_latest", "{}") or "{}")
    scanned = [r["symbol"] for r in scan.get("rows", [])[:20] if scan.get("date") == today and r.get("made_score") is not None]
    cands = list(dict.fromkeys(mentioned + scanned))
    picks = []
    for sym in cands:
        if sym in held:
            continue
        bars = market.bars(sym)
        if bars is None or len(bars) < 300 or bars.dates[-1] != today:
            continue
        _, sig = market.signals(bars)
        ms = market.strategy_metrics(bars, sig, len(bars) - 2, cfg.cost_bps)  # chosen on data before today
        if not ms:
            continue
        best = max(ms, key=lambda s: ms[s]["score"])
        pos = sig[best]
        just_on = pos[-1] == 1.0 and any(v == 0.0 for v in pos[-1 - FRESH_BARS:-1])
        if just_on:
            picks.append((ms[best]["score"], sym, best, "mentioned" if sym in mentioned else "MADE scan"))
    for score, sym, best, why in sorted(picks, reverse=True)[: cfg.paper_max_positions]:
        if _order(con, "ours", sym, "buy", today, f"{best} turned long ({why}; CAGR/DD {score:.2f})", None, None, 60, best):
            made["ours"].append(sym)
    return made


def _prev(market, today):
    cal = market.calendar()
    i = cal.index(today) if today in cal else len(cal) - 1
    return cal[i - 1] if i > 0 else today


def _days_before(day: str, n: int) -> str:
    import datetime as dt

    return (dt.date.fromisoformat(day) - dt.timedelta(days=n)).isoformat()


def summary(con, cfg) -> dict:
    out = {}
    for book in BOOKS:
        eq = [dict(r) for r in con.execute("SELECT date, cash, equity FROM paper_equity WHERE book = ? ORDER BY date", (book,))]
        trades = [dict(r) for r in con.execute("SELECT * FROM paper_trades WHERE book = ? ORDER BY exit_date DESC", (book,))]
        pos = [dict(r) for r in con.execute("SELECT * FROM paper_positions WHERE book = ?", (book,))]
        orders = [dict(r) for r in con.execute("SELECT * FROM paper_orders WHERE book = ? AND status = 'open'", (book,))]
        last = eq[-1]["equity"] if eq else cfg.paper_capital
        out[book] = {"equity": eq, "trades": trades, "positions": pos, "orders": orders,
                     "return": last / cfg.paper_capital - 1, "wins": sum(t["pnl"] > 0 for t in trades), "n_trades": len(trades)}
    return out

