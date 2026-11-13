"""SQLite state: posts, extracted calls, outcomes, feature vectors, paper books.

SQLite (not DuckDB) on purpose: TOP's DuckDB store allows one writer process, and this
project must keep working while TOP's server or cron sync holds that file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    uid TEXT PRIMARY KEY,           -- platform:author:id
    platform TEXT, author TEXT, post_id TEXT,
    ts_utc INTEGER,                 -- epoch ms
    text TEXT, url TEXT,
    n_photos INTEGER DEFAULT 0, n_videos INTEGER DEFAULT 0,
    media_paths TEXT,               -- JSON list of local image files
    links TEXT,                     -- JSON list of expanded urls
    ocr_text TEXT,
    views INTEGER, likes INTEGER,
    extracted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS posts_author ON posts(author, ts_utc);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_uid TEXT, author TEXT, platform TEXT, ts_utc INTEGER,
    symbol TEXT, matched_by TEXT,   -- ticker | cashtag | hashtag | name | tradingview | llm
    stance INTEGER,                 -- +1 bullish, -1 bearish, 0 neutral mention
    kind TEXT,                      -- call (has levels or explicit buy/sell) | mention
    setups TEXT, themes TEXT,       -- JSON lists
    entry REAL, entry_type TEXT,    -- market | above | below
    stop REAL, targets TEXT,        -- JSON list
    horizon TEXT,                   -- intraday | swing | positional | long
    confidence REAL,
    UNIQUE(post_uid, symbol)
);
CREATE INDEX IF NOT EXISTS calls_symbol ON calls(symbol, ts_utc);

CREATE TABLE IF NOT EXISTS outcomes (
    call_id INTEGER PRIMARY KEY,
    signal_date TEXT,               -- last close known when the call was made
    action_date TEXT,               -- session whose open is the first possible fill
    filled INTEGER, fill_date TEXT, fill_price REAL,
    exit_date TEXT, exit_price REAL, exit_reason TEXT,   -- target | stop | time | open | nofill
    trade_ret REAL,                 -- stance-signed, after costs
    r5 REAL, r20 REAL, r60 REAL,    -- stance-signed close-to-close from fill
    x5 REAL, x20 REAL, x60 REAL,    -- minus Nifty 50 over the same window
    mfe REAL, mae REAL,
    resolved INTEGER,               -- 1 when every horizon / exit is known
    data_through TEXT,
    n_long INTEGER,                 -- TOP strategies long at signal_date
    top_best TEXT,                  -- best TOP strategy on this stock's history up to signal_date
    top_agree INTEGER,              -- 1 when that strategy was long at signal_date
    made_score REAL                 -- log p_calls(x) - log p_background(x)
);

CREATE TABLE IF NOT EXISTS features (
    symbol TEXT, date TEXT, origin TEXT,  -- call | background | scan
    bits TEXT,                           -- '0101...' in strategy-id order (meta: feature_ids)
    PRIMARY KEY (symbol, date, origin)
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book TEXT, symbol TEXT, side TEXT,   -- buy | sell
    created TEXT, reason TEXT, stop REAL, target REAL, hold_days INTEGER,
    status TEXT DEFAULT 'open',          -- open | filled | cancelled
    fill_date TEXT, fill_price REAL, qty INTEGER, ref TEXT
);
CREATE TABLE IF NOT EXISTS paper_positions (
    book TEXT, symbol TEXT, qty INTEGER, entry_date TEXT, entry_price REAL,
    stop REAL, target REAL, hold_days INTEGER, reason TEXT, ref TEXT,
    PRIMARY KEY (book, symbol)
);
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book TEXT, symbol TEXT, qty INTEGER, entry_date TEXT, entry_price REAL,
    exit_date TEXT, exit_price REAL, pnl REAL, ret REAL, reason TEXT, exit_reason TEXT, ref TEXT
);
CREATE TABLE IF NOT EXISTS paper_equity (
    book TEXT, date TEXT, cash REAL, equity REAL, PRIMARY KEY (book, date)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))
