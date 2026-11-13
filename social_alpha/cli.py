"""social-alpha command line. `social-alpha daily` runs the whole pipeline once per trading day."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import time

from social_alpha import config, db, engine, evaluate, extract, model, ocr, paper, report, sources
from social_alpha.llm import LLM
from social_alpha.market import IST, Market
from social_alpha.symbols import SymbolIndex

DAILY_AT = dt.time(19, 30)  # IST; TOP's own update starts at 19:00
GIVE_UP_AT = dt.time(23, 30)  # holidays: no new bar ever comes, run anyway
CRON_TAG = "# social-alpha daily"


def log(msg):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _symbols(cfg, market=None) -> SymbolIndex:
    path = cfg.data_dir / "symbols.json"
    if market is not None:
        path.write_text(json.dumps(market.symbols()))
    if not path.exists():
        with Market(cfg, log) as m:
            path.write_text(json.dumps(m.symbols()))
    return SymbolIndex.from_json(path)


def cmd_ingest(cfg, con, args):
    stats = sources.ingest(con, cfg.archive_roots, cfg.authors, log)
    for name, s in stats.items():
        log(f"  {name:<22} {s['kind']:<9} {s['posts']:>7} posts ({s['new']} new)")
    return stats


def cmd_extract(cfg, con, args, index=None):
    if getattr(args, "redo", False):
        con.execute("UPDATE posts SET extracted = 0")
    res = extract.run(con, index or _symbols(cfg), LLM.maybe(cfg), cfg.authors, log)
    log(f"  extracted {res}")
    return res


def cmd_evaluate(cfg, con, args, market=None):
    own = market is None
    market = market or Market(cfg, log).open()
    try:
        model.check_feature_ids(con, market, log)
        res = evaluate.run(con, market, cfg, log, features_cb=model.make_features_cb(con, market, cfg))
        log(f"  evaluated {res}")
        return res
    finally:
        if own:
            market.close()


def cmd_train(cfg, con, args, market=None):
    own = market is None
    market = market or Market(cfg, log).open()
    try:
        model.check_feature_ids(con, market, log)
        bg_built = db.get_meta(con, "background_built")
        if getattr(args, "rebuild_background", False) or not bg_built:
            log("  building background feature vectors (random liquid stock-days)")
            n = model.build_background(con, market, cfg, cfg.background_symbols, cfg.background_per_symbol, log=log)
            log(f"  {n} background vectors")
        info = model.train(con, cfg, cfg.data_dir, log)
        log(f"  MADE: {info.get('status')}")
        n = model.score_calls(con, cfg.data_dir)
        log(f"  scored {n} calls")
        db.set_meta(con, "made_trained", dt.date.today().isoformat())
        con.commit()
        return info
    finally:
        if own:
            market.close()


def cmd_scan(cfg, con, args, market=None):
    own = market is None
    market = market or Market(cfg, log).open()
    try:
        rows = model.scan(con, market, cfg, log)
        for r in rows[:15]:
            log(f"  {r['symbol']:<18} score {r.get('made_score', float('nan')):7.2f}  long {r['n_long']}")
        return rows
    finally:
        if own:
            market.close()


def cmd_paper(cfg, con, args, market=None):
    own = market is None
    market = market or Market(cfg, log).open()
    try:
        res = paper.step(con, market, cfg, log)
        log(f"  paper: {json.dumps(res)}")
        return res
    finally:
        if own:
            market.close()


def cmd_improve(cfg, con, args):
    """Propose gates from failures and approve only those that repeat on unseen calls."""
    res = engine.run(con, cfg, db.get_meta(con, "market_date"))
    gates = res.get("gates") or {}
    log(f"  improve: {res.get('status')} cut={res.get('cut')} "
        f"approved={res.get('approved', 0)} rejected={res.get('rejected', 0)} "
        f"blocked_authors={gates.get('blocked_authors')} blocked_setups={gates.get('blocked_setups')} "
        f"require_top_agree={gates.get('require_top_agree')}")
    log(f"  live deployment: {res.get('live', engine.request_live())['status']}")
    return res


def cmd_report(cfg, con, args, market_date=None):
    res = report.build(con, cfg, market_date or db.get_meta(con, "market_date"))
    log(f"  report: {res['report']}")
    return res


def cmd_daily(cfg, con, args):
    now = dt.datetime.now(dt.timezone.utc).astimezone(IST)
    today = now.date().isoformat()
    if not args.force:
        if now.weekday() >= 5:
            return log("weekend: nothing to do")
        if now.time() < DAILY_AT:
            return log(f"before {DAILY_AT:%H:%M} IST: waiting for the exchange files")
        if db.get_meta(con, "daily_done") == today:
            return log(f"already ran for {today}")
    lock = open(cfg.data_dir / "daily.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return log("another daily run is in progress")
    t0 = time.time()
    if cfg.sources and not args.no_fetch:
        log("1/8 fetch social posts (social-archiver)")
        sources.fetch(cfg, log)
    log("2/8 ingest archives")
    cmd_ingest(cfg, con, args)
    market = Market(cfg, log).open()
    try:
        log("3/8 TOP OHLC freshness")
        info = market.ensure_fresh(run_sync=not args.no_sync)
        log(f"  TOP data through {info['latest']} (expected {info['expected']})")
        if info["latest"] and info["latest"] < info["expected"] and now.time() < GIVE_UP_AT and not args.force:
            return log("TOP data not updated yet; cron will retry")
        db.set_meta(con, "market_date", info["latest"])
        index = _symbols(cfg, market)
        if cfg.ocr and ocr.available():
            log("4/8 OCR chart images")
            log(f"  {ocr.run(con, cfg.authors, log=log)}")
        log("5/8 extract calls")
        cmd_extract(cfg, con, args, index)
        log("6/8 backtest calls on TOP bars")
        cmd_evaluate(cfg, con, args, market)
        trained = db.get_meta(con, "made_trained")
        if not trained or (dt.date.today() - dt.date.fromisoformat(trained)).days >= 7:
            log("7/8 train MADE (weekly)")
            cmd_train(cfg, con, args, market)
        else:
            model.score_calls(con, cfg.data_dir)
        log("7/8 MADE scan")
        cmd_scan(cfg, con, args, market)
        log("8/9 paper books")
        cmd_paper(cfg, con, args, market)
        log("9/9 improve: evaluate failures, validate proposals, update paper gates")
        cmd_improve(cfg, con, args)
    finally:
        market.close()
    cmd_report(cfg, con, args, info["latest"])
    db.set_meta(con, "daily_done", today)
    con.commit()
    log(f"daily done in {time.time() - t0:.0f}s")


def cmd_install_cron(cfg, con, args):
    root = config.ROOT
    line = (f"*/30 * * * * cd {root} && {sys.executable} -m social_alpha daily "
            f">> {cfg.data_dir / 'daily.log'} 2>&1  {CRON_TAG}")
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = [l for l in (cur.stdout.splitlines() if cur.returncode == 0 else []) if CRON_TAG not in l]
    lines.append(line)
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
    log(f"installed: {line}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="social-alpha", description=__doc__)
    ap.add_argument("--config", help="config.toml path (default: ./config.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest", help="load social-archiver outputs into the local DB")
    sub.add_parser("fetch", help="run social-archiver for the configured sources")
    sub.add_parser("ocr", help="OCR chart images (needs tesseract)")
    p = sub.add_parser("extract", help="extract stock calls/mentions from posts")
    p.add_argument("--redo", action="store_true", help="re-extract every post")
    sub.add_parser("evaluate", help="backtest calls on TOP bars + TOP strategy state")
    p = sub.add_parser("train", help="build background vectors and train the MADE models")
    p.add_argument("--rebuild-background", action="store_true")
    sub.add_parser("scan", help="score today's universe with MADE")
    sub.add_parser("paper", help="step the paper books for the latest trading day")
    sub.add_parser("improve", help="propose gates and approve only those that repeat out of sample")
    sub.add_parser("report", help="write output/report.html and CSVs")
    p = sub.add_parser("daily", help="fetch → ingest → TOP sync → extract → evaluate → MADE → paper → improve → report")
    p.add_argument("--force", action="store_true", help="ignore time/once-a-day checks")
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--no-sync", action="store_true", help="do not run top-sync when data is behind")
    sub.add_parser("install-cron", help="run `daily` from cron (every 30 min; it decides when to act)")
    args = ap.parse_args(argv)
    cfg = config.load(args.config)
    con = db.connect(cfg.db_path)
    try:
        fn = {"ingest": cmd_ingest, "extract": cmd_extract, "evaluate": cmd_evaluate, "train": cmd_train,
              "scan": cmd_scan, "paper": cmd_paper, "improve": cmd_improve, "report": cmd_report, "daily": cmd_daily,
              "install-cron": cmd_install_cron,
              "fetch": lambda c, k, a: sources.fetch(c, log),
              "ocr": lambda c, k, a: log(str(ocr.run(k, c.authors, log=log)))}[args.cmd]
        fn(cfg, con, args)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
