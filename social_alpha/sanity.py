"""Synthetic sanity checks for every backtestable strategy.

The bars are generated, not read from the market database, so CI can run
without the 515 MB store. A passing check means the strategy is well-formed
on this path. It is not a claim that the strategy makes money.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

from top import market
from top.backtest import run as run_backtest
from top.indicators import Frame
from top.strategies import catalog, signal

CHECKS = (
    "params_in_bounds",
    "signal_length",
    "signal_domain",
    "deterministic",
    "causal",
    "backtest_finite",
    "equity_positive",
    "cost_does_not_raise_return",
    "vol_target_capped",
    "long_or_flat",
    "metrics_present",
)
BARS = 420
CUT = 300


def synthetic_frame(n: int = BARS, seed: int = 7) -> Frame:
    rng = random.Random(seed)
    price = 250.0
    ts, opens, highs, lows, closes, volume = [], [], [], [], [], []
    day = date(2022, 1, 3)
    i = 0
    while len(ts) < n:
        if day.weekday() < 5:
            wave = 0.01 * math.sin(2 * math.pi * i / 48)
            change = 0.0003 + wave + rng.gauss(0, 0.012)
            open_ = price * (1 + rng.gauss(0, 0.002))
            close = max(5.0, price * (1 + change))
            high = max(open_, close) * (1 + abs(rng.gauss(0, 0.004)))
            low = min(open_, close) * (1 - abs(rng.gauss(0, 0.004)))
            ts.append(day.isoformat())
            opens.append(open_)
            highs.append(high)
            lows.append(low)
            closes.append(close)
            volume.append(1_000_000 + rng.random() * 2_000_000)
            price = close
            i += 1
        day += timedelta(days=1)
    return Frame(ts, opens, highs, lows, closes, volume)


def _perturb(frame: Frame, cut: int) -> Frame:
    opens = frame.o.copy()
    highs = frame.h.copy()
    lows = frame.l.copy()
    closes = frame.c.copy()
    volume = frame.v.copy()
    closes[cut:] *= 1.35
    highs[cut:] *= 1.40
    lows[cut:] *= 1.20
    opens[cut:] *= 1.25
    volume[cut:] *= 3
    return Frame(frame.ts, opens, highs, lows, closes, volume)


def _same_prefix(left, right, cut: int) -> bool:
    for a, b in zip(list(left)[:cut], list(right)[:cut]):
        a_nan = a != a
        b_nan = b != b
        if a_nan or b_nan:
            if a_nan != b_nan:
                return False
            continue
        if float(a) != float(b):
            return False
    return True


def _finite_metrics(metrics: dict) -> bool:
    for key in ("total_return_pct", "cagr_pct", "max_drawdown_pct", "ending_equity"):
        value = metrics.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return False
    return True


def check_strategy(item: dict, frame: Frame, later: Frame) -> dict:
    failures = []
    params = item.get("params") or []
    if not all(p["min"] <= p["default"] <= p["max"] for p in params):
        failures.append("params_in_bounds")
    position = signal(item["id"], frame, {})
    again = signal(item["id"], frame, {})
    values = [float(v) for v in position]
    if len(values) != len(frame):
        failures.append("signal_length")
    if any(v == v and v not in (0.0, 1.0) for v in values):
        failures.append("signal_domain")
    if any(v == v and v < 0 for v in values):
        failures.append("long_or_flat")
    if not _same_prefix(position, again, len(frame)):
        failures.append("deterministic")
    causal = signal(item["id"], later, {})
    if not _same_prefix(position, causal, CUT):
        failures.append("causal")
    cheap = run_backtest(frame.ts, frame.o, frame.h, frame.l, frame.c, position, cost_bps=5)
    costly = run_backtest(frame.ts, frame.o, frame.h, frame.l, frame.c, position, cost_bps=50)
    targeted = run_backtest(
        frame.ts, frame.o, frame.h, frame.l, frame.c, position, cost_bps=5, sizing="vol_target", target_vol=40,
    )
    metrics = cheap["metrics"]
    if not _finite_metrics(metrics):
        failures.append("backtest_finite")
    if not (isinstance(metrics.get("ending_equity"), (int, float)) and metrics["ending_equity"] > 0):
        failures.append("equity_positive")
    if metrics.get("total_return_pct", 0) + 1e-9 < costly["metrics"].get("total_return_pct", 0):
        failures.append("cost_does_not_raise_return")
    exposure = targeted["metrics"].get("avg_position_pct")
    if exposure is not None and exposure > 100:
        failures.append("vol_target_capped")
    required = {"total_return_pct", "max_drawdown_pct", "trades", "ending_equity", "buy_hold_pct"}
    if not required <= set(metrics):
        failures.append("metrics_present")
    return {
        "id": item["id"],
        "name": item["name"],
        "section": item["section"],
        "checks": len(CHECKS),
        "passed": len(CHECKS) - len(failures),
        "failures": failures,
        "trades": metrics.get("trades"),
        "total_return_pct": metrics.get("total_return_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "ending_equity": metrics.get("ending_equity"),
        "buy_hold_pct": metrics.get("buy_hold_pct"),
    }


def run_sanity() -> dict:
    frame = synthetic_frame()
    later = _perturb(frame, CUT)
    # Strategies that compare a name with the market must not open the real store.
    previous = market._pinned
    market.pin({day: 0.0003 for day in frame.ts})
    try:
        rows = [check_strategy(item, frame, later) for item in catalog()]
    finally:
        market.pin(previous)
    case_count = len(rows) * len(CHECKS)
    failed_cases = sum(len(row["failures"]) for row in rows)
    return {
        "bars": BARS,
        "strategies": len(rows),
        "checks_per_strategy": len(CHECKS),
        "cases": case_count,
        "passed": case_count - failed_cases,
        "failed": failed_cases,
        "note": "Synthetic bars. These numbers are sanity results, not market returns.",
        "rows": rows,
    }


def markdown(report: dict) -> str:
    lines = [
        "# Strategy sanity",
        "",
        report["note"],
        "",
        f"{report['passed']} / {report['cases']} checks passed"
        f" across {report['strategies']} strategies"
        f" ({report['checks_per_strategy']} checks each) on {report['bars']} synthetic sessions.",
        "",
        "| Strategy | Section | Trades | Return % | Max DD % | Result |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in report["rows"]:
        result = "pass" if not row["failures"] else "fail: " + ", ".join(row["failures"])
        lines.append(
            f"| `{row['id']}` | {row['section']} | {row['trades']} | {row['total_return_pct']} | {row['max_drawdown_pct']} | {result} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(directory: Path, report: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sanity-metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    (directory / "sanity-metrics.md").write_text(markdown(report))


def main() -> int:
    report = run_sanity()
    root = Path(__file__).resolve().parents[1]
    write_report(root / "docs" / "ci", report)
    print(json.dumps({k: report[k] for k in ("strategies", "cases", "passed", "failed")}))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
