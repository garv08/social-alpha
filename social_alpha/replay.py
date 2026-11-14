"""Decision-grid backtest, scored on later closes.

This is the local shape of a TradingAgents backtest: for each bullish stock
call, rebuild the rules desk from data that had closed by that call's signal
date, record the rating, then score it on the 5/20/60-session excess returns
already stored by evaluate.py. Those returns are close-to-close from the fill,
minus the Nifty 50. They are not a second cash book. The paper books remain
the portfolio simulation.

Look-ahead rules:
- a creator's score uses only calls whose 20-session window had already closed
- the call being rated is not in its own score
- today's engine gates are not applied backwards
- the technical veto is not applied backwards; confirmation uses later windows
"""

from __future__ import annotations

import json

from social_alpha import db, desk
from social_alpha.evaluate import summarize

SCHEMA = """
CREATE TABLE IF NOT EXISTS desk_replay (
    call_id INTEGER PRIMARY KEY,
    as_of TEXT,
    symbol TEXT,
    author TEXT,
    rating TEXT,
    action TEXT,
    reason TEXT,
    creator_n INTEGER,
    creator_score REAL,
    x5 REAL,
    x20 REAL,
    x60 REAL
);
"""


def _ensure(con):
    con.executescript(SCHEMA)


class _Track:
    """Closed excess returns, added only after their window has ended."""

    def __init__(self):
        self._by_author: dict[str, list[float]] = {}

    def add(self, author: str, x20: float | None):
        if x20 is None:
            return
        self._by_author.setdefault(author, []).append(x20)

    def score(self, author: str) -> tuple[int, float]:
        stats = summarize(self._by_author.get(author, []))
        return stats.get("n", 0), stats.get("shrunk", 0.0) or 0.0


def _technical(row) -> dict:
    ref = row["top_best"] or "TOP"
    if row["top_agree"] == 0:
        report = desk._stance([], [f"{ref} was not long at the prior close"])
        report["stance"] = -1
    elif row["top_agree"] == 1:
        report = desk._stance([f"{ref} was already long at the prior close"], [])
        report["stance"] = 0
    else:
        report = desk._stance([], [], "no TOP state stored for this call")
    report.update(just_on=False, ref=row["top_best"], score=None)
    return report


def _news(row) -> dict:
    try:
        themes = json.loads(row["themes"] or "[]")
    except json.JSONDecodeError:
        themes = []
    pro = sorted({t for t in themes if t in desk.GOOD_THEMES})
    con = sorted({t for t in themes if t in desk.BAD_THEMES})
    if not themes:
        return desk._stance([], [], "no news feed; this post named no fundamental theme")
    return desk._stance([f"post theme {t}" for t in pro], [f"post theme {t}" for t in con],
                        "themes named, none scored as good or bad")


def _packet(row, qualified: bool) -> dict:
    sentiment = desk._stance(
        [f"@{row['author']} {row['kind']} visible on {row['signal_date']}"],
        [],
    )
    return {
        "book": "copy",
        "symbol": row["symbol"],
        "held": False,
        "slots_left": 1,
        "gated": None if qualified else "creator not yet qualified on closed calls",
        "technical_veto": False,
        "technical": _technical(row),
        "sentiment": sentiment,
        "news": _news(row),
        "fundamentals": desk.fundamentals_report(),
    }


def run(con, cfg, start: str | None = None, end: str | None = None, log=None) -> dict:
    """Rate every bullish stock call from data closed by its signal date."""
    _ensure(con)
    rows = [dict(r) for r in con.execute(
        """SELECT c.id, c.author, c.symbol, c.kind, c.themes, c.stance,
                  o.signal_date, o.exit_date, o.top_agree, o.top_best, o.x5, o.x20, o.x60
           FROM calls c JOIN outcomes o ON o.call_id = c.id
           WHERE c.stance = 1 AND c.symbol NOT LIKE 'IDX:%' AND o.signal_date IS NOT NULL
           ORDER BY o.signal_date, c.id""")]
    closings = sorted(
        (r for r in rows if r["exit_date"] and r["x20"] is not None),
        key=lambda r: (r["exit_date"], r["id"]),
    )
    track = _Track()
    close_i = 0
    written = []
    for row in rows:
        as_of = row["signal_date"]
        if start and as_of < start:
            while close_i < len(closings) and closings[close_i]["exit_date"] <= as_of:
                track.add(closings[close_i]["author"], closings[close_i]["x20"])
                close_i += 1
            continue
        if end and as_of > end:
            break
        while close_i < len(closings) and closings[close_i]["exit_date"] <= as_of:
            track.add(closings[close_i]["author"], closings[close_i]["x20"])
            close_i += 1
        n, score = track.score(row["author"])
        qualified = n >= cfg.copy_min_calls and score > cfg.copy_min_score
        if not qualified:
            decision = {
                "rating": "HOLD",
                "action": "hold",
                "reason": (f"HOLD: not reviewed; @{row['author']} had {n} closed calls "
                           f"and shrunk score {score:+.2%} as of {as_of}"),
            }
        else:
            decision = desk.decide(_packet(row, True))
        clean = desk.sanitize_decision({
            "date": as_of, "book": "copy", "symbol": row["symbol"],
            "rating": decision["rating"], "action": decision["action"], "reason": decision["reason"],
            "reports_json": "{}",
        })
        if clean is None:
            continue
        written.append({
            "call_id": row["id"], "as_of": as_of, "symbol": clean["symbol"], "author": row["author"],
            "rating": clean["rating"], "action": clean["action"], "reason": clean["reason"],
            "creator_n": n, "creator_score": score,
            "x5": row["x5"], "x20": row["x20"], "x60": row["x60"],
        })
    # The stored grid is the last run. A start/end window replaces the whole table
    # with that window, so a later report cannot mix two cuts.
    con.execute("DELETE FROM desk_replay")
    con.executemany(
        """INSERT INTO desk_replay(call_id, as_of, symbol, author, rating, action, reason,
                                   creator_n, creator_score, x5, x20, x60)
           VALUES (:call_id, :as_of, :symbol, :author, :rating, :action, :reason,
                   :creator_n, :creator_score, :x5, :x20, :x60)""",
        written,
    )
    db.set_meta(con, "replay_start", start or (written[0]["as_of"] if written else ""))
    db.set_meta(con, "replay_end", end or (written[-1]["as_of"] if written else ""))
    con.commit()
    out = summary(con)
    out["written"] = len(written)
    if log:
        log(f"  replay wrote {len(written)} decisions")
        for rating, block in out["by_rating"].items():
            log(f"  {rating:<11} n={block['n']:<5} mean excess 20d {block['x20_mean']}")
    return out


def summary(con) -> dict:
    _ensure(con)
    by_rating = {}
    for rating in desk.RATINGS:
        rows = con.execute(
            "SELECT x5, x20, x60 FROM desk_replay WHERE rating = ? AND x20 IS NOT NULL", (rating,)).fetchall()
        vals = {h: [r[h] for r in rows if r[h] is not None] for h in ("x5", "x20", "x60")}
        stats = summarize(vals["x20"])
        by_rating[rating] = {
            "n": stats.get("n", 0),
            "x20_mean": stats.get("mean"),
            "x20_hit": stats.get("hit"),
            "x5_mean": summarize(vals["x5"]).get("mean"),
            "x60_mean": summarize(vals["x60"]).get("mean"),
        }
    buys = by_rating["BUY"]["n"]
    holds = by_rating["HOLD"]["n"]
    return {
        "by_rating": by_rating,
        "note": ("BUY is a paper permission from data closed on the signal date, "
                 "scored on later closes. It is not evidence of an edge, and it is not a live order."),
        "compared": {"buy_n": buys, "hold_n": holds},
    }
