"""Rules-only trading desk, shaped like a research firm. No language-model calls.

The shape follows TradingAgents (Tauric Research, arXiv 2412.20138): analysts report
in parallel, a bull and a bear argue, a trader proposes, a risk team votes, and a
portfolio manager issues a five-tier rating. The difference is the data and the
deployment. Analysts read TOP bars, TOP strategy state, and the social calls already
in this database. The portfolio manager can approve a paper-book order. It cannot
reach a broker. `engine.request_live()` stays refused.

Ratings: BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL.
Only BUY creates an entry. SELL is a note when a held name has turned against the
technical report; the existing paper risk rule still places that exit.
"""

from __future__ import annotations

import json
import re

import datetime as dt

from social_alpha import db, engine
from social_alpha.evaluate import group_stats

FRESH_BARS = 3
RECENT_DAYS = 10

RATINGS = ("BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL")
_SYMBOL = re.compile(r"^(?:NSE:|BSE:|IDX:(?:BSE:)?)?[A-Z0-9][A-Z0-9.&-]{0,24}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GOOD_THEMES = ("results", "growth_screen", "order_win", "fii_buying", "dii_buying", "promoter_buying")
BAD_THEMES = ("promoter_selling",)

SCHEMA = """
CREATE TABLE IF NOT EXISTS desk_runs (
    date TEXT, book TEXT, symbol TEXT,
    rating TEXT, action TEXT, reason TEXT,
    reports_json TEXT,
    PRIMARY KEY (date, book, symbol)
);
"""


def _ensure(con):
    con.executescript(SCHEMA)


def _days_before(day: str, n: int) -> str:
    return (dt.date.fromisoformat(day) - dt.timedelta(days=n)).isoformat()


def _prev(market, today):
    cal = market.calendar()
    i = cal.index(today) if today in cal else len(cal) - 1
    return cal[i - 1] if i > 0 else today


def _positions(con, book):
    return {r["symbol"] for r in con.execute("SELECT symbol FROM paper_positions WHERE book = ?", (book,))}


def _stance(points_for, points_against, missing=""):
    if points_for and not points_against:
        stance = 1
    elif points_against and not points_for:
        stance = -1
    else:
        stance = 0
    summary = "; ".join(points_for + points_against) or missing or "no view"
    return {"stance": stance, "summary": summary, "for": points_for, "against": points_against}


def technical_report(market, cfg, symbol, today) -> dict:
    bars = market.bars(symbol) if market is not None else None
    if bars is None or not bars.dates or bars.dates[-1] != today:
        return {**_stance([], [], "no bar for this session"), "just_on": False, "ref": None, "score": None, "close": None}
    close = bars.c[-1]
    try:
        _, sig = market.signals(bars, last_n=600)
        end = max(0, len(bars) - 2)
        metrics = market.strategy_metrics(bars, sig, end, cfg.cost_bps) if sig else {}
    except Exception:
        sig, metrics = {}, {}
    if not metrics:
        return {**_stance([], [], "no TOP strategy state on history before today"), "just_on": False, "ref": None, "score": None, "close": close}
    ref = max(metrics, key=lambda s: metrics[s]["score"])
    score = metrics[ref]["score"]
    pos = sig.get(ref) or []
    just_on = bool(pos) and pos[-1] == 1.0 and any(v == 0.0 for v in pos[-1 - FRESH_BARS:-1])
    turned_flat = bool(pos) and pos[-1] == 0.0 and any(v == 1.0 for v in pos[-1 - FRESH_BARS:-1])
    pro, con = [], []
    if just_on and score > 0:
        pro.append(f"{ref} just turned long (CAGR/DD {score:.2f})")
    elif turned_flat:
        con.append(f"{ref} just turned flat")
    elif score < 0:
        con.append(f"best prior strategy {ref} has CAGR/DD {score:.2f}")
    else:
        pro.append(f"{ref} is the best prior strategy (CAGR/DD {score:.2f}) but it is not a fresh long")
    report = _stance(pro, con)
    if not just_on:
        report["stance"] = -1 if con and not pro else 0
    report.update(just_on=just_on, ref=ref, score=score, close=close)
    return report


def sentiment_report(con, symbol, today, authors) -> dict:
    since = _days_before(today, RECENT_DAYS)
    rows = con.execute(
        """SELECT c.author, c.stance, c.kind, c.setups FROM calls c
           JOIN outcomes o ON o.call_id = c.id
           WHERE c.symbol = ? AND o.signal_date >= ? AND o.signal_date <= ? AND c.stance != 0
           ORDER BY c.ts_utc DESC""",
        (symbol, since, today)).fetchall()
    pro, con = [], []
    for r in rows[:8]:
        g = authors.get(r["author"])
        score = f", prior score {g['score']:+.1%}" if g and g.get("score") is not None else ", no closed track record yet"
        line = f"@{r['author']} {r['kind']}{score}"
        if r["stance"] > 0:
            pro.append(line)
        else:
            con.append(line)
    if not rows:
        return _stance([], [], "no recent social call")
    return _stance(pro, con)


def news_report(con, symbol, today) -> dict:
    since = _days_before(today, 20)
    rows = con.execute(
        """SELECT c.themes FROM calls c JOIN outcomes o ON o.call_id = c.id
           WHERE c.symbol = ? AND o.signal_date >= ? AND o.signal_date <= ?""",
        (symbol, since, today)).fetchall()
    themes = []
    for r in rows:
        try:
            themes.extend(json.loads(r["themes"] or "[]"))
        except json.JSONDecodeError:
            continue
    pro = sorted({t for t in themes if t in GOOD_THEMES})
    con = sorted({t for t in themes if t in BAD_THEMES})
    if not themes:
        return _stance([], [], "no news feed; posts named no fundamental theme")
    return _stance([f"post theme {t}" for t in pro], [f"post theme {t}" for t in con], "themes named, none scored as good or bad")


def fundamentals_report() -> dict:
    return _stance([], [], "no point-in-time filings in TOP; fundamentals are not a vote")


def decide(packet: dict) -> dict:
    """Portfolio-manager rating from analyst packets. BUY is the only entry."""
    technical = packet["technical"]
    sentiment = packet["sentiment"]
    t, s = technical["stance"], sentiment["stance"]
    gated = bool(packet.get("gated"))
    held = bool(packet.get("held"))
    slots = packet.get("slots_left", 1)
    book = packet["book"]
    analysts = (technical, sentiment, packet["news"], packet["fundamentals"])
    bull = [a["summary"] for a in analysts if a["stance"] > 0]
    bear = [a["summary"] for a in analysts if a["stance"] < 0]
    if book == "copy":
        research_buy = s > 0 and not gated and not held
    else:
        research_buy = bool(technical.get("just_on")) and t > 0 and not held
    # The technical veto is a block only after validate-desk has seen it lose on a later window.
    # Until then it is written into the reason and does not stop a paper entry.
    veto_on = bool(packet.get("technical_veto"))
    technical_blocks = t == -1 and veto_on
    aggressive = bool(bull)
    neutral = research_buy
    conservative = research_buy and not technical_blocks and s != -1 and not gated and slots > 0
    if conservative:
        rating, action = "BUY", "buy"
        why = "risk team approved a paper entry"
        if t == -1:
            why += "; technical opposition was not used as a block because it is not confirmed on unseen calls"
    elif research_buy and technical_blocks:
        rating, action = "OVERWEIGHT", "hold"
        why = "research leaned in, but the technical report opposed and that veto is confirmed on unseen calls"
    elif research_buy and s == -1:
        rating, action = "OVERWEIGHT", "hold"
        why = "a strategy turned long, but recent social calls are bearish"
    elif research_buy and slots <= 0:
        rating, action = "OVERWEIGHT", "hold"
        why = "the book is already full"
    elif t < 0 and s < 0:
        rating, action = "UNDERWEIGHT", "hold"
        why = "technical and social reports both lean against"
    elif t < 0 and held:
        rating, action = "SELL", "hold"
        why = "technical report turned against a held name; the paper risk rule places the exit, not a broker"
    else:
        rating, action = "HOLD", "hold"
        why = "no fresh entry the risk team will approve"
    reason = f"{rating}: {why}"
    return {
        "rating": rating, "action": action, "reason": reason,
        "bull": bull, "bear": bear,
        "risk": {"aggressive": aggressive, "neutral": neutral, "conservative": conservative},
        "ref": technical.get("ref"), "score": technical.get("score"),
    }


def _authors(con, today, cfg) -> dict:
    return {g["author"]: g for g in group_stats(con, "author", as_of=today, min_n=1)}


def _copy_rows(con, market, today, last):
    return con.execute(
        """SELECT c.*, o.signal_date, o.top_agree FROM calls c JOIN outcomes o ON o.call_id = c.id
           WHERE c.stance = 1 AND c.symbol NOT LIKE 'IDX:%' AND o.signal_date > ? AND o.signal_date <= ?
           ORDER BY c.ts_utc DESC""",
        (last or _prev(market, today), today)).fetchall()


def _ours_symbols(con, today) -> list[str]:
    since = _days_before(today, RECENT_DAYS)
    mentioned = [r[0] for r in con.execute(
        """SELECT c.symbol FROM calls c JOIN outcomes o ON o.call_id = c.id
           WHERE c.stance = 1 AND c.symbol NOT LIKE 'IDX:%' AND o.signal_date >= ?
           GROUP BY c.symbol ORDER BY COUNT(*) DESC LIMIT 40""", (since,))]
    scan = json.loads(db.get_meta(con, "scan_latest", "{}") or "{}")
    scanned = [r["symbol"] for r in scan.get("rows", [])[:20]
               if scan.get("date") == today and r.get("made_score") is not None]
    return list(dict.fromkeys(mentioned + scanned))


def _packet(con, market, cfg, today, book, symbol, authors, gates, held, slots_left, call=None) -> dict:
    technical = technical_report(market, cfg, symbol, today)
    sentiment = sentiment_report(con, symbol, today, authors)
    gated = None
    if call is not None:
        gated = engine.skip_reason(gates, call["author"], call["setups"], call["top_agree"])
    elif book == "ours":
        row = con.execute(
            """SELECT author, setups FROM calls WHERE symbol = ? AND stance = 1 ORDER BY ts_utc DESC LIMIT 1""",
            (symbol,)).fetchone()
        if row and row["author"] in gates.get("blocked_authors", []):
            gated = f"engine blocked @{row['author']}"
    return {
        "book": book, "symbol": symbol, "held": symbol in held, "slots_left": slots_left,
        "gated": gated, "technical": technical, "sentiment": sentiment,
        "news": news_report(con, symbol, today), "fundamentals": fundamentals_report(),
        "call": call,
    }


def run(con, market, cfg, log=None) -> list[dict]:
    """Review today's paper candidates and replace that date's desk rows."""
    _ensure(con)
    today = market.latest_date()
    last = db.get_meta(con, "paper_last_date")
    authors = _authors(con, today, cfg)
    gates = engine.load_gates(con)
    technical_veto = bool(rule_evidence(con).get("veto_confirmed"))
    good = {a: g for a, g in authors.items()
            if (g.get("n20") or 0) >= cfg.copy_min_calls and (g.get("score") or 0) > cfg.copy_min_score}
    out = []
    for book in ("copy", "ours"):
        held = set(_positions(con, book))
        slots = cfg.paper_max_positions - len(held)
        packets = []
        if book == "copy":
            seen = set()
            for call in _copy_rows(con, market, today, last):
                if call["symbol"] in seen or call["author"] not in good:
                    continue
                seen.add(call["symbol"])
                packet = _packet(con, market, cfg, today, book, call["symbol"], authors, gates, held, slots, call)
                packet["technical_veto"] = technical_veto
                packets.append(packet)
        else:
            for symbol in _ours_symbols(con, today):
                if symbol in held:
                    continue
                packet = _packet(con, market, cfg, today, book, symbol, authors, gates, held, slots)
                packet["technical_veto"] = technical_veto
                packets.append(packet)
        ranked = []
        for packet in packets:
            decision = decide(packet)
            decision["packet"] = packet
            ranked.append(decision)
        ranked.sort(key=lambda d: (d["action"] == "buy", d.get("score") or 0), reverse=True)
        buys_left = max(0, slots)
        for decision in ranked:
            packet = decision.pop("packet")
            if decision["action"] == "buy":
                if buys_left <= 0:
                    decision["rating"] = "OVERWEIGHT"
                    decision["action"] = "hold"
                    decision["reason"] = "OVERWEIGHT: approved, but the book has no free slot"
                else:
                    buys_left -= 1
            call = packet.get("call")
            if decision["action"] == "buy" and call is not None:
                decision["stop"] = call["stop"]
                targets = json.loads(call["targets"] or "[]")
                decision["target"] = targets[0] if targets else None
                decision["hold_days"] = cfg.max_hold_days
                decision["ref"] = call["post_uid"]
                decision["reason"] = f"desk BUY @{call['author']}: {decision['reason']}"
            elif decision["action"] == "buy":
                decision["stop"] = None
                decision["target"] = None
                decision["hold_days"] = 60
                decision["reason"] = f"desk BUY {decision.get('ref') or packet['symbol']}: {decision['reason']}"
            reports = {
                "technical": packet["technical"]["summary"],
                "sentiment": packet["sentiment"]["summary"],
                "news": packet["news"]["summary"],
                "fundamentals": packet["fundamentals"]["summary"],
                "bull": decision.get("bull"),
                "bear": decision.get("bear"),
                "risk": decision.get("risk"),
                "deployment": "paper",
            }
            row = sanitize_decision({
                "date": today, "book": book, "symbol": packet["symbol"],
                "rating": decision["rating"], "action": decision["action"], "reason": decision["reason"],
                "stop": decision.get("stop"), "target": decision.get("target"),
                "hold_days": decision.get("hold_days"), "ref": decision.get("ref"),
                "reports_json": json.dumps(reports),
            })
            if row is None:
                continue
            out.append(row)
            if log and decision["rating"] == "BUY":
                log(f"  desk {book} {packet['symbol']} BUY")
    con.execute("DELETE FROM desk_runs WHERE date = ?", (today,))
    for row in out:
        con.execute(
            """INSERT INTO desk_runs(date, book, symbol, rating, action, reason, reports_json)
               VALUES (:date, :book, :symbol, :rating, :action, :reason, :reports_json)""", row)
    db.set_meta(con, "desk_date", today)
    con.commit()
    return out


def load(con, today) -> dict:
    _ensure(con)
    rows = con.execute("SELECT * FROM desk_runs WHERE date = ?", (today,)).fetchall()
    out = {}
    for r in rows:
        clean = sanitize_decision(dict(r))
        if clean:
            out[(clean["book"], clean["symbol"])] = clean
    return out


def sanitize_text(value, limit=240) -> str:
    """Drop markup and control characters from anything that came out of a post or a report."""
    text = "" if value is None else str(value)
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def sanitize_decision(row: dict) -> dict | None:
    """A row may be stored or traded only if the rating, book, symbol, and deployment are in the allowed set.

    BUY is the only action that can open an order. A mismatched or unreadable row becomes no decision.
    """
    if not isinstance(row, dict):
        return None
    symbol = sanitize_text(row.get("symbol"), 32).upper()
    book = row.get("book")
    date = sanitize_text(row.get("date"), 10)
    if book not in ("copy", "ours") or not _SYMBOL.match(symbol) or not _DATE.match(date):
        return None
    rating = row.get("rating") if row.get("rating") in RATINGS else "HOLD"
    action = "buy" if rating == "BUY" and row.get("action") == "buy" else "hold"
    if rating == "BUY" and action != "buy":
        rating = "HOLD"
    reports = row.get("reports_json") or row.get("reports") or {}
    if isinstance(reports, str):
        try:
            reports = json.loads(reports)
        except json.JSONDecodeError:
            reports = {}
    if not isinstance(reports, dict):
        reports = {}
    clean_reports = {"deployment": "paper"}
    for key, value in reports.items():
        if key in ("live_trading", "broker", "api_key"):
            continue
        if isinstance(value, (dict, list)):
            clean_reports[key] = value
        else:
            clean_reports[key] = sanitize_text(value, 500)
    clean_reports["deployment"] = "paper"
    return {
        "date": date, "book": book, "symbol": symbol, "rating": rating, "action": action,
        "reason": sanitize_text(row.get("reason")),
        "stop": row.get("stop"), "target": row.get("target"),
        "hold_days": row.get("hold_days"), "ref": sanitize_text(row.get("ref"), 80) or None,
        "reports_json": json.dumps(clean_reports),
    }


def _mean(vals) -> float | None:
    return sum(vals) / len(vals) if vals else None


def rule_evidence(con) -> dict:
    """Unseen-window check of the one veto already stored on each call.

    A bullish call the desk would refuse is proxied by top_agree == 0: TOP's walk-forward
    strategy disagreed before the post. This is not a full replay of the four analysts.
    """
    rows = con.execute(
        """SELECT o.signal_date, o.top_agree, o.x20
           FROM calls c JOIN outcomes o ON o.call_id = c.id
           WHERE c.stance = 1 AND o.filled = 1 AND o.x20 IS NOT NULL AND o.top_agree IS NOT NULL
             AND o.signal_date IS NOT NULL
           ORDER BY o.signal_date"""
    ).fetchall()
    dates = sorted({r["signal_date"] for r in rows})
    if len(dates) < 4:
        return {"status": "insufficient", "n": len(rows)}
    cut = dates[max(1, int(len(dates) * 0.70))]
    later = [r for r in rows if r["signal_date"] >= cut]
    kept = [r["x20"] for r in later if r["top_agree"] != 0]
    vetoed = [r["x20"] for r in later if r["top_agree"] == 0]

    def side(vals):
        return {"n": len(vals), "x20_mean": _mean(vals),
                "hit": (sum(v > 0 for v in vals) / len(vals)) if vals else None}

    confirmed = bool(kept and vetoed and len(vetoed) >= 8 and len(kept) >= 8 and _mean(vetoed) < 0 and _mean(kept) > _mean(vetoed))
    return {"status": "ok", "cut": cut, "kept": side(kept), "vetoed": side(vetoed), "veto_confirmed": confirmed,
            "note": "confirmed" if confirmed else "the technical veto is not confirmed on the later window; do not treat BUY as an edge"}


def sanitize_open_orders(con) -> list[str]:
    """Cancel open buys that no sanitized desk BUY covers. Fills must not happen on a pre-desk order."""
    _ensure(con)
    approved = set()
    for r in con.execute("SELECT book, symbol, date, rating, action, reason, reports_json FROM desk_runs"):
        clean = sanitize_decision(dict(r))
        if clean and clean["action"] == "buy":
            approved.add((clean["book"], clean["symbol"], clean["date"]))
    cancelled = []
    for o in con.execute("SELECT id, book, symbol, created, reason FROM paper_orders WHERE status = 'open' AND side = 'buy'"):
        if (o["book"], o["symbol"], o["created"]) in approved:
            continue
        note = " | cancelled: no sanitized desk BUY for this order"
        con.execute("UPDATE paper_orders SET status = 'cancelled', reason = reason || ? WHERE id = ?", (note, o["id"]))
        cancelled.append(f"{o['book']} {o['symbol']} {o['created']}")
    con.commit()
    return cancelled


def validate(con) -> dict:
    """Rewrite stored desk rows through the sanitizer, then cancel uncovered open buys."""
    _ensure(con)
    raw = [dict(r) for r in con.execute("SELECT * FROM desk_runs")]
    clean = []
    dropped = 0
    for row in raw:
        item = sanitize_decision(row)
        if item is None:
            dropped += 1
            continue
        clean.append(item)
    con.execute("DELETE FROM desk_runs")
    for item in clean:
        con.execute(
            """INSERT OR REPLACE INTO desk_runs(date, book, symbol, rating, action, reason, reports_json)
               VALUES (:date, :book, :symbol, :rating, :action, :reason, :reports_json)""", item)
    cancelled = sanitize_open_orders(con)
    evidence = rule_evidence(con)
    result = {"desk_rows": len(clean), "dropped": dropped, "cancelled_orders": cancelled, "evidence": evidence,
              "live": engine.request_live(con)}
    db.set_meta(con, "desk_validation", json.dumps(result))
    con.commit()
    return result


def latest(con, limit=40) -> list[dict]:
    _ensure(con)
    return [dict(r) for r in con.execute(
        "SELECT date, book, symbol, rating, action, reason FROM desk_runs ORDER BY date DESC, rating, symbol LIMIT ?",
        (limit,))]
