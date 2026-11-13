"""Optional OCR of chart screenshots with the tesseract CLI (skipped when it is not installed).

Chart images usually carry the symbol in the header (e.g. "HFCL · 1D · NSE") and sometimes
levels. OCR text is stored on the post and fed to the extractor as a low-confidence source.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess


def available() -> str | None:
    return os.environ.get("TESSERACT") or shutil.which("tesseract")


def run(con, authors=None, limit=500, log=print) -> dict:
    exe = available()
    if not exe:
        return {"ocr": "tesseract not installed", "images": 0}
    wanted = [a.lower().lstrip("@") for a in (authors or [])]
    where = "ocr_text IS NULL AND media_paths NOT IN ('[]', '') AND n_photos > 0"
    if wanted:
        where += f" AND lower(author) IN ({','.join('?' * len(wanted))})"
    rows = con.execute(f"SELECT uid, media_paths FROM posts WHERE {where} ORDER BY ts_utc DESC LIMIT ?",
                       (*wanted, int(limit))).fetchall()
    done = 0
    for row in rows:
        texts = []
        for path in json.loads(row["media_paths"])[:4]:
            if not os.path.exists(path):
                continue
            try:
                proc = subprocess.run([exe, path, "-", "--psm", "11"], capture_output=True, text=True, timeout=60)
                texts.append(proc.stdout.strip())
                done += 1
            except subprocess.TimeoutExpired:
                continue
        con.execute("UPDATE posts SET ocr_text = ?, extracted = 0 WHERE uid = ?", ("\n".join(texts), row["uid"]))
        con.commit()
    return {"posts": len(rows), "images": done}
