"""Local request-based chart monitor for this pipeline. Paper only.

The chart library is TradingView Lightweight Charts (Apache-2.0), the same
library TOP already vendors. Bars are our own OHLC, loaded by an HTTP request
to this process, which reads TOP's store or TOP's API. This is not a
TradingView session, and it does not speak TradingView's private chart protocol.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from social_alpha import conviction, db, desk
from social_alpha.market import Market

WEB = Path(__file__).resolve().parent / "monitor_web"
HOST = "127.0.0.1"


def _scan_ranked(raw_rows: list[dict]) -> list[dict]:
    """Fuse a scan into the paper list. Trend is missing unless the row has it."""
    picked = [row for row in raw_rows if row.get("symbol")][:20]
    made_units = conviction.made_to_unit([row.get("made_score") for row in picked])
    fused = []
    for row, made in zip(picked, made_units):
        bull = int(row.get("bullish") or 0)
        bear = int(row.get("bearish") or 0)
        technical = None if bull + bear == 0 else (bull - bear) / (bull + bear)
        if "sentiment" in row and row.get("sentiment") is not None:
            sentiment = row.get("sentiment")
        else:
            sentiment = None
        trend = row.get("trend")
        scored = conviction.fuse(
            technical=technical, trend=trend, sentiment=sentiment, made=made,
            regime=row.get("regime") or "neutral",
        )
        missing = ", ".join(scored["missing"]) or "none"
        fused.append({
            "symbol": row["symbol"],
            "score": scored["score"],
            "grade": scored["grade"],
            "made_score": row.get("made_score"),
            "detail": f"{scored['grade']} {scored['score']} missing {missing}",
            "source": "scan",
        })
    return conviction.allocate(fused, regime=(picked[0].get("regime") if picked else "neutral") or "neutral")


def top20(con) -> list[dict]:
    """The sorted paper list the pipeline last wrote. Scan first, then desk BUYs."""
    raw = json.loads(db.get_meta(con, "scan_latest", "{}") or "{}")
    rows = _scan_ranked(raw.get("rows") or [])
    if rows:
        return rows
    for row in desk.latest(con, 40):
        if row["rating"] != "BUY":
            continue
        rows.append({"symbol": row["symbol"], "score": None, "detail": row["reason"], "source": "desk"})
        if len(rows) == 20:
            break
    return rows


def status(con) -> dict:
    posts = con.execute("SELECT COUNT(*), MAX(ts_utc) FROM posts").fetchone()
    return {
        "deployment": "paper",
        "posts": posts[0] or 0,
        "ingest_latest_ts": posts[1],
        "market_date": db.get_meta(con, "market_date"),
        "desk_date": db.get_meta(con, "desk_date"),
        "top20": top20(con),
        "chart": "Lightweight Charts, request bars from this monitor. Not a TradingView login.",
    }


def fetch_bars(cfg, symbol: str) -> dict:
    clean = desk.sanitize_decision({
        "date": "2020-01-01", "book": "copy", "symbol": symbol,
        "rating": "HOLD", "action": "hold", "reason": "", "reports_json": "{}",
    })
    if clean is None:
        return {"error": "unknown symbol", "bars": []}
    symbol = clean["symbol"]
    url = cfg.top_api.rstrip("/") + "/api/bars?symbol=" + urllib.request.quote(symbol, safe="")
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
        payload["source"] = "top-api"
        return payload
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        pass
    try:
        with Market(cfg) as market:
            bars = market.bars(symbol)
    except Exception as exc:
        return {"symbol": symbol, "bars": [], "error": str(exc)[:160]}
    if bars is None:
        return {"symbol": symbol, "interval": "1d", "bars": [], "source": "top-store"}
    return {
        "symbol": symbol,
        "interval": "1d",
        "source": "top-store",
        "bars": [
            {"time": bars.dates[i], "open": bars.o[i], "high": bars.h[i], "low": bars.l[i],
             "close": bars.c[i], "volume": bars.v[i]}
            for i in range(len(bars))
        ],
    }


def _file(url_path: str) -> Path | None:
    rel = unquote(url_path).lstrip("/") or "index.html"
    if rel.startswith("/") or ".." in rel.split("/"):
        return None
    path = (WEB / rel).resolve()
    root = WEB.resolve()
    if path != root and root not in path.parents:
        return None
    return path if path.is_file() else None


def make_handler(cfg, con):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _json(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/status":
                self._json(200, status(con))
                return
            if parsed.path == "/api/bars":
                symbol = (parse_qs(parsed.query).get("symbol") or [""])[0]
                payload = fetch_bars(cfg, symbol)
                self._json(404 if payload.get("error") == "unknown symbol" else 200, payload)
                return
            path = _file(parsed.path)
            if path is None:
                self._json(404, {"error": "not found"})
                return
            kind = "text/html" if path.suffix == ".html" else "text/plain" if path.suffix == ".txt" else "application/javascript"
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(cfg, con, port=8765):
    httpd = ThreadingHTTPServer((HOST, port), make_handler(cfg, con))
    print(f"monitor http://{HOST}:{port}  (this machine only, paper data)", flush=True)
    httpd.serve_forever()
