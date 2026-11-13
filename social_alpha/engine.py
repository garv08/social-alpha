"""Self-improving loop. Paper only.

Mapped from the public architecture posted by Arwaz Az (@ArwazAz_, Instagram
https://www.instagram.com/p/DfWdUerE2lp/, 4 Nov 2026). The carousel is a poster
of the seven stages plus a blueprint of the feedback loop. Instagram hid the
image bytes behind a login wall; the caption and the text supplied with that
link are the same specification:

1. Market data — TOP daily OHLC, volume, and social events already ingested.
2. Strategy engine — TOP's 95 strategies plus extracted social calls.
3. Backtesting — evaluate.py (no look-ahead fills).
4. Evaluation — expectancy, hit rate, t-stat, and paper-book drawdown.
5. Research — a rule agent proposes gates from failures. An optional LLM may
   narrate; it cannot approve, reject, or place an order.
6. Validation — the later slice of signal dates is unseen. A gate is approved
   only when the same failure shows up there with enough calls.
7. Controlled deployment — approved gates change the paper books only.

TRADE → RECORD → EVALUATE → IMPROVE → VALIDATE → REPEAT.

Live broker execution is refused in code. Nothing in this module can turn it on.
"""

from __future__ import annotations

import json

from social_alpha import db

MIN_SIDE = 8
HOLDOUT = 0.30
GATES_KEY = "engine_gates"
LAST_KEY = "engine_last"

SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT,
    kind TEXT,
    target TEXT,
    reason TEXT,
    train_json TEXT,
    test_json TEXT,
    status TEXT,          -- approved | rejected | noted
    note TEXT
);
"""


def empty_gates() -> dict:
    return {
        "blocked_authors": [],
        "blocked_setups": [],
        "require_top_agree": [],
        "live_trading": False,
    }


def load_gates(con) -> dict:
    gates = empty_gates()
    raw = db.get_meta(con, GATES_KEY, "") or ""
    if raw:
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            for key in ("blocked_authors", "blocked_setups", "require_top_agree"):
                vals = loaded.get(key) or []
                if isinstance(vals, list):
                    gates[key] = [str(v) for v in vals]
    gates["live_trading"] = False
    return gates


def save_gates(con, gates: dict) -> dict:
    clean = empty_gates()
    for key in ("blocked_authors", "blocked_setups", "require_top_agree"):
        clean[key] = sorted(set(gates.get(key) or []))
    db.set_meta(con, GATES_KEY, json.dumps(clean))
    return clean


def skip_reason(gates: dict, author: str, setups, top_agree) -> str | None:
    """Why a bullish call must not enter the copy book, or None if it may."""
    if author in gates.get("blocked_authors", []):
        return f"engine blocked @{author} after walk-forward validation"
    names = setups if isinstance(setups, list) else json.loads(setups or "[]")
    blocked = [s for s in names if s in gates.get("blocked_setups", [])]
    if blocked:
        return f"engine blocked setup {blocked[0]} after walk-forward validation"
    if author in gates.get("require_top_agree", []) and top_agree != 1:
        return f"engine requires TOP agreement for @{author}"
    return None


def request_live(_con=None) -> dict:
    """The only deployment path. It always refuses a broker."""
    return {
        "status": "refused",
        "reason": "live execution is out of scope; validation may update paper gates only",
    }


def _ensure(con):
    con.executescript(SCHEMA)


def _rows(con):
    return con.execute(
        """SELECT c.author, c.setups, o.signal_date, o.x20, o.top_agree, o.trade_ret
           FROM calls c JOIN outcomes o ON o.call_id = c.id
           WHERE o.filled = 1 AND c.stance = 1 AND o.x20 IS NOT NULL AND o.signal_date IS NOT NULL
           ORDER BY o.signal_date, c.id"""
    ).fetchall()


def _split(rows):
    dates = sorted({r["signal_date"] for r in rows})
    if len(dates) < 4:
        return rows, [], None
    cut = dates[max(1, int(len(dates) * (1 - HOLDOUT)))]
    train = [r for r in rows if r["signal_date"] < cut]
    test = [r for r in rows if r["signal_date"] >= cut]
    return train, test, cut


def _mean(rows) -> float | None:
    if not rows:
        return None
    return sum(r["x20"] for r in rows) / len(rows)


def _side(rows, pred) -> dict:
    picked = [r for r in rows if pred(r)]
    return {"n": len(picked), "x20_mean": _mean(picked)}


def _setups(row) -> list[str]:
    try:
        return json.loads(row["setups"] or "[]")
    except json.JSONDecodeError:
        return []


def _propose(train, test) -> list[dict]:
    out = []
    authors = sorted({r["author"] for r in train} | {r["author"] for r in test})
    for author in authors:
        tr = _side(train, lambda r, a=author: r["author"] == a)
        te = _side(test, lambda r, a=author: r["author"] == a)
        if tr["n"] >= MIN_SIDE and te["n"] >= MIN_SIDE and tr["x20_mean"] < 0 and te["x20_mean"] < 0:
            out.append(_prop("block_author", author, "approved",
                              "20-day excess vs Nifty was negative before the cut and again on later unseen calls", tr, te))
        elif tr["n"] >= MIN_SIDE and tr["x20_mean"] < 0:
            out.append(_prop("block_author", author, "rejected",
                              "earlier calls lagged Nifty, but the unseen window is too small or did not repeat the loss", tr, te,
                              "need the same negative result on at least 8 later calls"))
        out.extend(_agree_proposal(author, train, test))
    names = sorted({s for r in train + test for s in _setups(r)})
    for name in names:
        tr = _side(train, lambda r, n=name: n in _setups(r))
        te = _side(test, lambda r, n=name: n in _setups(r))
        if tr["n"] >= MIN_SIDE and te["n"] >= MIN_SIDE and tr["x20_mean"] < 0 and te["x20_mean"] < 0:
            out.append(_prop("block_setup", name, "approved",
                              "this setup lagged Nifty in both the earlier window and the unseen later window", tr, te))
        elif tr["n"] >= MIN_SIDE and tr["x20_mean"] < 0:
            out.append(_prop("block_setup", name, "rejected",
                              "setup lagged earlier, but that did not repeat on enough unseen calls", tr, te,
                              "need the same negative result on at least 8 later calls"))
    return out


def _agree_proposal(author, train, test) -> list[dict]:
    """Require TOP agreement only when disagreement lost on both sides and agreement did not."""
    def sides(rows):
        agree = [r for r in rows if r["author"] == author and r["top_agree"] == 1]
        disagree = [r for r in rows if r["author"] == author and r["top_agree"] == 0]
        return agree, disagree

    tr_a, tr_d = sides(train)
    te_a, te_d = sides(test)
    tr = {"n_agree": len(tr_a), "agree": _mean(tr_a), "n_disagree": len(tr_d), "disagree": _mean(tr_d)}
    te = {"n_agree": len(te_a), "agree": _mean(te_a), "n_disagree": len(te_d), "disagree": _mean(te_d)}
    enough = min(len(tr_d), len(te_d), len(tr_a), len(te_a)) >= MIN_SIDE
    failed = enough and tr["disagree"] < 0 and te["disagree"] < 0 and tr["agree"] > tr["disagree"] and te["agree"] > 0
    if not (len(tr_d) >= MIN_SIDE and tr["disagree"] is not None and tr["disagree"] < 0):
        return []
    return [_prop("require_top_agree", author, "approved" if failed else "rejected",
                   "calls where TOP disagreed lagged Nifty; agreement is required only if that repeats out of sample",
                   tr, te, None if failed else "unseen window did not confirm that TOP disagreement is the failure")]


def _prop(kind, target, status, reason, train, test, note=None) -> dict:
    return {"kind": kind, "target": target, "status": status, "reason": reason,
            "train": train, "test": test, "note": note or ""}


def _apply(proposals) -> dict:
    gates = empty_gates()
    for p in proposals:
        if p["status"] != "approved":
            continue
        key = {"block_author": "blocked_authors", "block_setup": "blocked_setups",
               "require_top_agree": "require_top_agree"}[p["kind"]]
        gates[key].append(p["target"])
    return gates


def _paper_findings(con, capital: float) -> list[dict]:
    findings = []
    for book in ("copy", "ours"):
        eq = [r["equity"] for r in con.execute(
            "SELECT equity FROM paper_equity WHERE book = ? ORDER BY date", (book,))]
        trades = [r["ret"] for r in con.execute(
            "SELECT ret FROM paper_trades WHERE book = ? AND ret IS NOT NULL", (book,))]
        finding = {"book": book, "n_trades": len(trades), "expectancy": None, "max_drawdown": None, "status": "noted"}
        if trades:
            finding["expectancy"] = sum(trades) / len(trades)
        if eq:
            peak = eq[0]
            worst = 0.0
            for e in eq:
                peak = max(peak, e)
                if peak:
                    worst = min(worst, e / peak - 1)
            finding["max_drawdown"] = worst
        if len(trades) >= MIN_SIDE and finding["expectancy"] is not None and finding["expectancy"] <= 0:
            finding["note"] = (f"{book} book expectancy is {finding['expectancy']:+.2%} over {len(trades)} closed trades; "
                               "left as a note because a parameter change still needs its own unseen test")
        elif len(trades) < MIN_SIDE:
            finding["note"] = f"{book} book has {len(trades)} closed trades; too few to judge robustness"
        else:
            finding["note"] = f"{book} book expectancy is positive on {len(trades)} closed trades"
        finding["equity"] = eq[-1] if eq else capital
        findings.append(finding)
    return findings


def run(con, cfg, today: str | None = None) -> dict:
    """Evaluate, propose, validate, and replace paper gates. Never deploys live."""
    _ensure(con)
    today = today or "unknown"
    rows = _rows(con)
    train, test, cut = _split(rows)
    findings = _paper_findings(con, cfg.paper_capital)
    if cut is None or len(train) < MIN_SIDE or len(test) < MIN_SIDE:
        result = {
            "status": "insufficient",
            "date": today,
            "cut": cut,
            "n_train": len(train),
            "n_test": len(test),
            "proposals": [],
            "gates": load_gates(con),
            "findings": findings,
            "live": request_live(con),
            "note": "not enough dated calls to hold out an unseen window; paper gates were left unchanged",
        }
        db.set_meta(con, LAST_KEY, json.dumps(result))
        con.commit()
        return result
    proposals = _propose(train, test)
    gates = save_gates(con, _apply(proposals))
    con.execute("DELETE FROM engine_proposals WHERE created = ?", (today,))
    for p in proposals:
        con.execute(
            """INSERT INTO engine_proposals(created, kind, target, reason, train_json, test_json, status, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (today, p["kind"], p["target"], p["reason"], json.dumps(p["train"]), json.dumps(p["test"]), p["status"], p["note"]))
    result = {
        "status": "validated",
        "date": today,
        "cut": cut,
        "n_train": len(train),
        "n_test": len(test),
        "approved": sum(p["status"] == "approved" for p in proposals),
        "rejected": sum(p["status"] == "rejected" for p in proposals),
        "proposals": proposals,
        "gates": gates,
        "findings": findings,
        "live": request_live(con),
    }
    db.set_meta(con, LAST_KEY, json.dumps(result))
    con.commit()
    return result


def summary_rows(con) -> list[dict]:
    _ensure(con)
    return [dict(r) for r in con.execute(
        "SELECT created, kind, target, status, reason, note, train_json, test_json FROM engine_proposals ORDER BY id DESC LIMIT 40")]
