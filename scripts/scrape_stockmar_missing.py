"""Scrape StockMar list members that have no archive yet, India-focused handles first.

Credentials come from an existing credentials.env (X_CRED_FILE); nothing is copied.
Runs social-archiver's x-handle (which also downloads media) per handle, skipping ones already done.
"""
import json, os, subprocess, sys, time
from pathlib import Path

ARCH = Path(os.environ.get("ARCHIVER_HOME", "/home/dell/.cline/data/workspaces/chat/social-archiver"))
CRED = Path(os.environ.get("X_CRED_FILE", "/home/dell/.cline/data/workspaces/chat/x-scraper-rohonchain/credentials.env"))
DAYS = os.environ.get("DAYS", "365")
PRIORITY = ["jitu_stock", "nooreshtech", "adi408b", "stageanalysis", "subi_sa", "ItsAshishKSingh",
            "QuantifiedStrat", "nomadascalper"]

env = dict(os.environ)
for line in CRED.read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        env.setdefault(k.strip(), v.strip().strip("'\""))

handles = [h.lstrip("@") for h in sys.argv[1:]] or PRIORITY
for h in handles:
    out = ARCH / "output" / h
    if (out / "tweets.html").exists():
        print(f"skip {h} (done)", flush=True)
        continue
    print(f"=== {h} {time.ctime()}", flush=True)
    py = str(ARCH / ".venv/bin/python")
    # x-handle downloads media and builds tweets.html itself; finish again only if that step failed
    r = subprocess.run([py, "-m", "archiver", "x-handle", "@" + h, DAYS, "--passes", "1"], cwd=ARCH, env=env)
    if not (out / "tweets.html").exists() and (out / "tweets.json").exists():
        subprocess.run([py, "-m", "archiver", "finish", h], cwd=ARCH, env=env)
    print(f"=== {h} rc={r.returncode} {time.ctime()}", flush=True)
