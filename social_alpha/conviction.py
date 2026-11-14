"""Conviction blend for the paper list.

The published scoring table of https://github.com/renee-jia/trading-bot
(MIT harness) is the shape: four lenses, a 0–100 score, letter grades,
a 25% name cap, a bear-market cash reserve, and a volatility target that
never levers. Their private ``core/`` scorer is not in that repo and is
not used here. Inputs are our own: TOP strategy stance, excess versus the
Nifty, social calls, and the MADE log-probability gap.

A score ranks the top 20. It does not open a paper order. Only a sanitized
desk BUY can do that.
"""

from __future__ import annotations

WEIGHTS = {"technical": 0.35, "trend": 0.30, "sentiment": 0.15, "made": 0.20}
NAME_CAP = 0.25
CASH_RESERVE_BEAR = 0.20
VOL_TARGET = 0.40


def grade(score: float) -> str:
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 45:
        return "C"
    if score >= 30:
        return "D"
    return "F"


def _stance(value) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, 50.0 + 50.0 * float(value)))


def fuse(*, technical=None, trend=None, sentiment=None, made=None, regime="neutral") -> dict:
    """Blend four lenses. A missing lens is 50 and is listed, not hidden.

    ``technical``, ``trend``, and ``sentiment`` are stances in [-1, 1].
    ``made`` is already on 0–100 (use ``made_to_unit`` for a raw log-prob gap).
    ``regime`` is ``bull``, ``bear``, or ``neutral``. Bear shrinks the score.
    Bull may lift it, never past 100.
    """
    raw = {
        "technical": _stance(technical),
        "trend": _stance(trend),
        "sentiment": _stance(sentiment),
        "made": None if made is None else max(0.0, min(100.0, float(made))),
    }
    missing = [name for name, value in raw.items() if value is None]
    parts = {name: 50.0 if value is None else value for name, value in raw.items()}
    score = sum(parts[name] * WEIGHTS[name] for name in WEIGHTS)
    if regime == "bear":
        score *= 0.85
    elif regime == "bull":
        score = min(100.0, score * 1.05)
    else:
        regime = "neutral"
    score = round(max(0.0, min(100.0, score)), 1)
    return {
        "score": score,
        "grade": grade(score),
        "parts": {name: round(value, 1) for name, value in parts.items()},
        "missing": missing,
        "regime": regime,
        "weights": dict(WEIGHTS),
    }


def made_to_unit(scores: list) -> list:
    """Rank a batch of MADE gaps onto 0–100. ``None`` stays missing."""
    known = [float(score) for score in scores if score is not None]
    if not known:
        return [None for _ in scores]
    ordered = sorted(known)

    def pct(score: float) -> float:
        below = sum(1 for item in ordered if item < score)
        equal = sum(1 for item in ordered if item == score)
        return 100.0 * (below + 0.5 * equal) / len(ordered)

    return [None if score is None else pct(float(score)) for score in scores]


def exposure(daily_returns, target: float = VOL_TARGET) -> float:
    """``min(1, target / annualized vol)``. Never above 1. Too few days stays 1."""
    series = [float(item) for item in daily_returns if item is not None]
    if len(series) < 2:
        return 1.0
    mean = sum(series) / len(series)
    var = sum((item - mean) ** 2 for item in series) / (len(series) - 1)
    vol = var ** 0.5
    if vol <= 0:
        return 1.0
    annual = vol * (252 ** 0.5)
    return round(min(1.0, target / annual), 4)


def allocate(rows: list[dict], n: int = 20, cap: float = NAME_CAP, regime: str = "neutral") -> list[dict]:
    """Ladder-weight the top ``n``. Each name is at most ``cap``. Leftover is cash.

    Bear keeps a 20% cash reserve before the cap. Weights are not orders.
    """
    ranked = sorted(rows, key=lambda row: (-float(row["score"]), str(row.get("symbol", ""))))[:n]
    if not ranked:
        return []
    size = len(ranked)
    ladder = [size - index for index in range(size)]
    budget = 1.0 - (CASH_RESERVE_BEAR if regime == "bear" else 0.0)
    weights = [item / sum(ladder) * budget for item in ladder]
    weights = _cap(weights, cap)
    cash = round(1.0 - sum(weights), 4)
    out = []
    for row, weight in zip(ranked, weights):
        item = dict(row)
        item["weight"] = round(weight, 4)
        item["cash"] = cash
        out.append(item)
    return out


def _cap(weights: list[float], cap: float) -> list[float]:
    weights = list(weights)
    for _ in range(len(weights) + 1):
        over = [i for i, weight in enumerate(weights) if weight > cap + 1e-12]
        if not over:
            break
        excess = sum(weights[i] - cap for i in over)
        for i in over:
            weights[i] = cap
        free = [i for i, weight in enumerate(weights) if i not in over and weight < cap - 1e-12]
        if not free or excess <= 0:
            break
        room = sum(cap - weights[i] for i in free)
        give = min(excess, room)
        base = sum(weights[i] for i in free) or float(len(free))
        for i in free:
            weights[i] += give * ((weights[i] or 1.0) / base)
    return weights
