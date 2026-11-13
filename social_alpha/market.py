"""Bridge to TOP (Trade Observability Platform): symbols, adjusted daily bars, and its 95 strategies.

TOP's DuckDB store lets one process hold it. Direct mode opens it through TOP's own
store.connect() (same settings, so TOP strategies that open the store themselves can share it).
When the TOP server holds the file, HTTP mode reads /api/symbols and /api/bars instead;
the few TOP strategies that read the store themselves then return no signal.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import subprocess
import urllib.request
from collections import OrderedDict

NAN = float("nan")
BENCHMARK = "IDX:NIFTY50"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class Bars:
    __slots__ = ("symbol", "dates", "o", "h", "l", "c", "v", "index")

    def __init__(self, symbol, rows):
        self.symbol = symbol
        self.dates = [r["time"] for r in rows]
        self.o = [float(r["open"]) for r in rows]
        self.h = [float(r["high"]) for r in rows]
        self.l = [float(r["low"]) for r in rows]
        self.c = [float(r["close"]) for r in rows]
        self.v = [float(r.get("volume") or 0) for r in rows]
        self.index = {d: i for i, d in enumerate(self.dates)}

    def __len__(self):
        return len(self.dates)

    def last_index_on_or_before(self, date: str) -> int:
        """Index of the last bar dated <= date, or -1."""
        lo, hi = 0, len(self.dates) - 1
        ans = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.dates[mid] <= date:
                ans, lo = mid, mid + 1
            else:
                hi = mid - 1
        return ans


class Market:
    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.mode = None
        self._con = None
        self._bars = OrderedDict()
        self._symbols = None
        self._strategy_ids = None

    # ---- connection -------------------------------------------------------
    def open(self):
        if self.mode:
            return self
        from top import store

        try:
            self._con = store.connect()
            store.ensure_schema(self._con)
            self.mode = "direct"
        except Exception as exc:  # duckdb.IOException when the TOP server holds the lock
            if self._api_ok():
                self.mode = "http"
                self.log(f"  TOP store busy ({str(exc)[:80]}); using TOP API at {self.cfg.top_api}")
            else:
                raise RuntimeError(f"cannot open TOP store {self.cfg.top_db} and TOP API is down: {exc}") from exc
        return self

    def close(self):
        if self._con is not None:
            self._con.close()
            self._con = None
        self.mode = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def _api(self, path):
        with urllib.request.urlopen(self.cfg.top_api.rstrip("/") + path, timeout=120) as resp:
            return json.loads(resp.read().decode())

    def _api_ok(self):
        try:
            return bool(self._api("/api/health").get("ok"))
        except Exception:
            return False

    # ---- data -------------------------------------------------------------
    def symbols(self) -> list[dict]:
        if self._symbols is None:
            if self.mode == "direct":
                rows = self._con.execute("SELECT symbol, name, exchange, kind, universe, sector FROM symbols").fetchall()
                self._symbols = [dict(zip(("symbol", "name", "exchange", "kind", "universe", "sector"), r)) for r in rows]
            else:
                self._symbols = [{k: s.get(k) for k in ("symbol", "name", "exchange", "kind", "universe", "sector")}
                                 for s in self._api("/api/symbols")["symbols"]]
        return self._symbols

    def bars(self, symbol: str) -> Bars | None:
        if symbol in self._bars:
            self._bars.move_to_end(symbol)
            return self._bars[symbol]
        if self.mode == "direct":
            from top import store

            rows = store.load_bars(self._con, symbol)
        else:
            try:
                rows = self._api("/api/bars?symbol=" + urllib.request.quote(symbol, safe=""))["bars"]
            except Exception:
                rows = []
        rows = [r for r in rows if r.get("close") and r.get("open")]
        bars = Bars(symbol, rows) if rows else None
        self._bars[symbol] = bars
        if len(self._bars) > 600:
            self._bars.popitem(last=False)
        return bars

    def calendar(self) -> list[str]:
        bars = self.bars(BENCHMARK) or self.bars("RELIANCE")
        return bars.dates if bars else []

    def latest_date(self) -> str | None:
        cal = self.calendar()
        return cal[-1] if cal else None

    def liquid_symbols(self, limit: int, min_value_cr: float) -> list[str]:
        """Stocks by median daily traded value over the last 60 sessions (direct mode)."""
        if self.mode != "direct":
            return [s["symbol"] for s in self.symbols() if s["universe"] == "nifty50"]
        rows = self._con.execute(
            """
            WITH recent AS (
                SELECT b.symbol, b.close * b.volume AS value
                FROM bars b JOIN symbols s ON s.symbol = b.symbol
                WHERE b.interval = '1d' AND s.kind = 'stock' AND s.universe IN ('nifty50', 'nse')
                  AND b.ts >= (SELECT MAX(ts) FROM bars) - INTERVAL 90 DAY
            )
            SELECT symbol, median(value) AS med FROM recent GROUP BY symbol
            HAVING med >= ? ORDER BY med DESC LIMIT ?
            """,
            [min_value_cr * 1e7, int(limit)],
        ).fetchall()
        return [r[0] for r in rows]

    # ---- freshness ----------------------------------------------------------
    @staticmethod
    def expected_latest(now=None) -> str:
        """Most recent weekday whose bhavcopy should be out (after 19:00 IST)."""
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(IST)
        day = now.date() if now.time() >= dt.time(19, 0) else now.date() - dt.timedelta(days=1)
        while day.weekday() >= 5:
            day -= dt.timedelta(days=1)
        return day.isoformat()

    def ensure_fresh(self, run_sync=True) -> dict:
        """Run TOP's own daily update when the store is behind; exchange holidays stay behind harmlessly."""
        latest, expected = self.latest_date(), self.expected_latest()
        info = {"latest": latest, "expected": expected, "synced": False}
        if run_sync and (latest is None or latest < expected):
            self.log(f"  TOP data at {latest}, expected {expected}: running top-sync --auto")
            was = self.mode
            self.close()
            proc = subprocess.run([self.cfg.top_python, "-m", "top.store", "--auto"], cwd=self.cfg.top_home,
                                  capture_output=True, text=True, timeout=4 * 3600)
            info["synced"] = proc.returncode == 0
            info["sync_tail"] = (proc.stdout + proc.stderr)[-600:]
            self._bars.clear()
            self._symbols = None
            if was:
                self.open()
            info["latest"] = self.latest_date()
        return info

    # ---- TOP strategies -------------------------------------------------------
    def strategy_ids(self) -> list[str]:
        if self._strategy_ids is None:
            from top import strategies

            self._strategy_ids = [s["id"] for s in strategies.catalog()]
        return self._strategy_ids

    def strategy_catalog(self) -> dict:
        from top import strategies

        return {s["id"]: s for s in strategies.catalog()}

    def signals(self, bars: Bars, last_n: int | None = None) -> tuple[list[str], dict]:
        """All TOP strategy positions (1 long / 0 flat / nan warm-up), known at each close."""
        from top import strategies
        from top.indicators import Frame

        start = max(0, len(bars) - last_n) if last_n else 0
        frame = Frame(bars.dates[start:], bars.o[start:], bars.h[start:], bars.l[start:], bars.c[start:], bars.v[start:])
        out = {}
        for sid in self.strategy_ids():
            try:
                out[sid] = [float(x) for x in strategies.signal(sid, frame)]
            except Exception:
                out[sid] = [NAN] * len(frame)
        return bars.dates[start:], out

    @staticmethod
    def bits_at(signals: dict, ids: list[str], i: int) -> str:
        return "".join("1" if (v := signals[s][i]) == 1.0 else "0" for s in ids)

    def strategy_metrics(self, bars: Bars, signals: dict, end: int, cost_bps: float) -> dict:
        """TOP backtest metrics per strategy on bars[:end+1] (walk-forward selection)."""
        from top import backtest

        out = {}
        for sid, pos in signals.items():
            p = pos[: end + 1]
            if sum(1 for x in p if x == 1.0) < 5:
                continue
            try:
                m = backtest.metrics(bars.o[: end + 1], bars.c[: end + 1], p, cost_bps=cost_bps)
            except Exception:
                continue
            cagr, dd = m.get("cagr_pct"), m.get("max_drawdown_pct")
            if cagr is None or dd is None or (m.get("closed_trades") or 0) < 3:
                continue
            m["score"] = cagr / max(abs(dd), 5.0)
            out[sid] = m
        return out


def ist(ts_ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts_ms / 1000, dt.timezone.utc).astimezone(IST)


def timing(ts_ms: int, calendar: list[str]):
    """When could a post at ts_ms be traded on daily bars?

    Returns (signal_date, action_date, fill_mode):
      signal_date  last session whose close was known before the post (strategy state / features)
      action_date  first session the call can be filled in
      fill_mode    'open' (posted before 09:15 or after 15:30 → next open) or 'close' (posted during
                   market hours → that day's close, since the open had already happened)
    """
    t = ist(ts_ms)
    day, tod = t.date().isoformat(), t.time()
    import bisect

    k = bisect.bisect_right(calendar, day)  # first session strictly after `day`
    is_session = k > 0 and calendar[k - 1] == day
    if is_session and tod < dt.time(9, 15):
        sig = calendar[k - 2] if k >= 2 else None
        return sig, day, "open"
    if is_session and tod < dt.time(15, 30):
        sig = calendar[k - 2] if k >= 2 else None
        return sig, day, "close"
    sig = calendar[k - 1] if k >= 1 else None
    act = calendar[k] if k < len(calendar) else None
    return sig, act, "open"


def finite(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))
