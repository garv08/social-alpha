"""Read social-archiver outputs (X and Telegram) into the posts table, and run fresh fetches.

Archive layouts (one folder per handle/channel under each archive root):
  X handle/list        tweets.json    {"handle", "tweets": [{id, created_at_ms, text, url, media[{type, local}], links}]}
                       (or _state.json {"tweets": {id: {...}}} when the run never reached "finish")
  Telegram (Telethon)  posts.jsonl    {id, date_ms, text, media[{type, local}], url}
  Telegram (bridge)    messages.jsonl {id, date_ms, text, media_type, media_url, url}
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
from pathlib import Path

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
URL_AUTHOR = re.compile(r"https?://(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/")


def _clean(text: str) -> str:
    return html.unescape((text or "").replace("<br>", "\n"))


def _x_posts(folder: Path):
    """X handle or list archive. List feeds mix authors, so the author comes from each tweet's URL.

    Falls back to _state.json when a run stopped before writing tweets.json.
    """
    if (folder / "tweets.json").exists():
        data = json.loads((folder / "tweets.json").read_text(encoding="utf-8"))
        tweets = data.get("tweets") or []
    else:
        data = json.loads((folder / "_state.json").read_text(encoding="utf-8"))
        tweets = list((data.get("tweets") or {}).values())
    default = data.get("handle") or folder.name
    for t in tweets:
        m = URL_AUTHOR.match(t.get("url") or "")
        author = m.group(1) if m else default
        media = t.get("media") or []
        images = [str(folder / m["local"]) for m in media
                  if m.get("local") and m["local"].lower().endswith(IMAGE_EXT)]
        yield {
            "uid": f"x:{author}:{t['id']}", "platform": "x", "author": author, "post_id": str(t["id"]),
            "ts_utc": int(t.get("created_at_ms") or 0), "text": _clean(t.get("text")), "url": t.get("url"),
            "n_photos": sum(1 for m in media if m.get("type") in ("photo", "animated_gif")),
            "n_videos": sum(1 for m in media if m.get("type") == "video"),
            "media_paths": json.dumps(images),
            "links": json.dumps([l.get("expanded_url") for l in (t.get("links") or []) if l.get("expanded_url")]),
            "views": t.get("views"), "likes": t.get("favorite_count"),
        }


def _tg_posts(folder: Path, name: str):
    author = folder.name
    seen = set()
    for line in (folder / name).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("id") in seen:
            continue
        seen.add(m.get("id"))
        media = m.get("media") or []
        images = [str(folder / x["local"]) for x in media if x.get("local") and x["local"].lower().endswith(IMAGE_EXT)]
        kinds = [x.get("type") or "" for x in media] or [m.get("media_type") or ""]
        yield {
            "uid": f"tg:{author}:{m['id']}", "platform": "tg", "author": author, "post_id": str(m["id"]),
            "ts_utc": int(m.get("date_ms") or 0), "text": _clean(m.get("text")), "url": m.get("url"),
            "n_photos": sum(1 for k in kinds if "photo" in k.lower()),
            "n_videos": sum(1 for k in kinds if "video" in k.lower() or "document" in k.lower()),
            "media_paths": json.dumps(images), "links": "[]",
            "views": m.get("views"), "likes": None,
        }


def discover(roots):
    """Yield (kind, folder) for every archive folder under the roots."""
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            if (folder / "tweets.json").exists():
                yield "x", folder
            elif (folder / "_state.json").exists() and '"tweets": {' in (folder / "_state.json").read_text(errors="ignore")[:200_000]:
                yield "x", folder
            elif (folder / "posts.jsonl").exists():
                yield "tg", folder
            elif (folder / "messages.jsonl").exists():
                yield "tg-bridge", folder


def ingest(con, roots, authors=None, log=print) -> dict:
    """Upsert posts from every archive. Text edits are picked up; OCR/extraction flags survive."""
    wanted = {a.lower().lstrip("@") for a in (authors or [])}
    stats = {}
    for kind, folder in discover(roots):
        if wanted and folder.name.lower() not in wanted:
            continue
        try:
            rows = list(_x_posts(folder) if kind == "x" else
                        _tg_posts(folder, "posts.jsonl" if kind == "tg" else "messages.jsonl"))
        except (OSError, ValueError, KeyError) as exc:
            log(f"  skip {folder}: {exc}")
            continue
        if not rows:
            stats[folder.name] = {"kind": kind, "posts": 0, "new": 0}
            continue
        before = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        con.executemany(
            """INSERT INTO posts(uid, platform, author, post_id, ts_utc, text, url, n_photos, n_videos,
                                 media_paths, links, views, likes)
               VALUES (:uid, :platform, :author, :post_id, :ts_utc, :text, :url, :n_photos, :n_videos,
                       :media_paths, :links, :views, :likes)
               ON CONFLICT(uid) DO UPDATE SET
                 text = excluded.text, media_paths = excluded.media_paths, links = excluded.links,
                 views = excluded.views, likes = excluded.likes,
                 extracted = CASE WHEN posts.text = excluded.text AND posts.media_paths = excluded.media_paths
                                  THEN posts.extracted ELSE 0 END""",
            rows,
        )
        con.commit()
        after = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        stats[folder.name] = {"kind": kind, "posts": len(rows), "new": after - before}
    return stats


def _cred_env(cfg) -> dict:
    env = dict(os.environ)
    if cfg.x_cred_file and Path(cfg.x_cred_file).exists():
        for line in Path(cfg.x_cred_file).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                # the archiver also reads USERNAME/PASSWORD, which the shell sets to the login user
                env[key.strip()] = value.strip().strip("'\"")
    return env


def fetch(cfg, log=print) -> list:
    """Run social-archiver for every configured source over a short trailing window (resumable)."""
    results = []
    env = _cred_env(cfg)
    for src in cfg.sources:
        kind, name, days = src["kind"], src["name"], str(src.get("days", 7))
        if kind == "x-handle":
            args = ["x-handle", "@" + name.lstrip("@"), days, "--passes", "1", "--bucket-days", "7"]
        elif kind == "x-list":
            args = ["x-list", name, days, "--slug", src["slug"], "--passes", "1", "--bucket-days", "7", "--no-members"]
        elif kind in ("tg-channel", "tg-bridge"):
            args = [kind, name, days]
        else:
            raise ValueError(f"unknown source kind {kind!r}")
        log(f"  fetch {kind} {name} ({days}d)")
        # x-handle / x-list also download media and rebuild the HTML/CSV ("finish") themselves
        rc = subprocess.run([cfg.archiver_python, "-m", "archiver", *args], cwd=cfg.archiver_home, env=env).returncode
        results.append({"source": name, "kind": kind, "rc": rc})
    return results
