"""Point-in-time data vendors.

TradingAgents keeps a data-access contract: a decision dated D may read only
records published on or before D. This module is that contract for the inputs
this machine actually has.

- social: posts and extracted calls timestamped on or before the end of D (IST)
- market: daily bars dated on or before D
- news: themes already stored on those visible calls, not a separate feed
- fundamentals: always empty. TOP has no point-in-time filings.

Nothing here calls a network API or a broker.
"""

from __future__ import annotations

import datetime as dt

from social_alpha.market import IST

VENDORS = ("social", "market", "news", "fundamentals")


def published_by(day: str) -> int:
    """Epoch milliseconds at the end of an IST calendar day."""
    end = dt.datetime.combine(dt.date.fromisoformat(day), dt.time(23, 59, 59), tzinfo=IST)
    return int(end.timestamp() * 1000)


def posts_visible(con, as_of: str) -> list:
    """Posts a desk running after the close of as_of would already have ingested."""
    return con.execute(
        "SELECT * FROM posts WHERE ts_utc > 0 AND ts_utc <= ? ORDER BY ts_utc",
        (published_by(as_of),),
    ).fetchall()


def calls_visible(con, as_of: str) -> list:
    """Calls whose source post was published by the end of as_of."""
    return con.execute(
        """SELECT c.* FROM calls c
           JOIN posts p ON p.uid = c.post_uid
           WHERE p.ts_utc > 0 AND p.ts_utc <= ?
           ORDER BY p.ts_utc, c.id""",
        (published_by(as_of),),
    ).fetchall()


def bars_visible(bars, as_of: str):
    """Bars dated on or before as_of. Later sessions are not returned."""
    if bars is None:
        return None
    end = bars.last_index_on_or_before(as_of)
    if end < 0:
        return type(bars)(bars.symbol, [])
    rows = [
        {"time": bars.dates[i], "open": bars.o[i], "high": bars.h[i], "low": bars.l[i],
         "close": bars.c[i], "volume": bars.v[i]}
        for i in range(end + 1)
    ]
    return type(bars)(bars.symbol, rows)


def fundamentals_visible(as_of: str) -> dict:
    return {
        "as_of": as_of,
        "filings": [],
        "status": "abstain",
        "reason": "no point-in-time filings in TOP; fundamentals are not a vote",
    }


def snapshot(con, as_of: str | None = None) -> dict:
    """What the pipeline is allowed to see. as_of defaults to the latest stored post day."""
    row = con.execute("SELECT COUNT(*), MAX(ts_utc) FROM posts WHERE ts_utc > 0").fetchone()
    latest_ms = row[1]
    if as_of is None and latest_ms:
        as_of = dt.datetime.fromtimestamp(latest_ms / 1000, IST).date().isoformat()
    visible = len(posts_visible(con, as_of)) if as_of else 0
    return {
        "as_of": as_of,
        "posts": row[0] or 0,
        "posts_visible": visible,
        "posts_after_as_of": (row[0] or 0) - visible,
        "vendors": {
            "social": "X and Telegram archives already ingested",
            "market": "TOP daily OHLC on or before the as-of date",
            "news": "themes extracted from visible posts",
            "fundamentals": "abstain",
        },
        "deployment": "paper",
    }
