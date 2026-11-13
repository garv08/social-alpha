"""Turn a post into trade calls / stock mentions with rules (and an optional LLM fallback)."""

from __future__ import annotations

import json
import re

NUM = r"(?:rs\.?|inr|₹)?\s*(\d{1,6}(?:,\d{2,3})*(?:\.\d+)?)"

SETUPS = {
    "breakout": r"break ?out|breaking out|bo\b|range break|consolidation break",
    "breakdown": r"break ?down|breaking down",
    "retest": r"re-?test",
    "vcp": r"\bvcp\b|volatility contraction",
    "cup_handle": r"cup (?:and|&|n) handle|cup ?& ?handle|\bc&h\b",
    "darvas": r"darvas",
    "harmonic": r"harmonic|gartley|butterfly|\bcrab\b|\bbat pattern|cypher|\bshark pattern|\bprz\b",
    "flag": r"\bflag\b|pennant",
    "triangle": r"triangle",
    "wedge": r"wedge",
    "head_shoulders": r"(?<!inverse )(?<!inverted )head (?:and|&) shoulders?|\bh&s\b",
    "inverse_hs": r"(?:inverse|inverted|reverse) head (?:and|&) shoulders?|\bihs\b|inverse h&s",
    "double_bottom": r"double bottom|\bw pattern",
    "double_top": r"double top|\bm pattern",
    "trendline": r"trend ?line|\btl\b break",
    "support": r"\bsupport\b|demand zone",
    "resistance": r"\bresistance\b|supply zone",
    "fibonacci": r"fibonacci|\bfib\b|0\.618|61\.8%",
    "rsi": r"\brsi\b",
    "macd": r"\bmacd\b",
    "adx": r"\badx\b|\bdmi\b",
    "moving_average": r"\b(?:ema|sma|dma)\s*\d*\b|moving average|\b(?:50|100|200)[- ]?(?:day|d)\b",
    "bollinger": r"bollinger|\bbb squeeze",
    "supertrend": r"super ?trend",
    "vwap": r"\bvwap\b",
    "volume": r"\bvolume\b|\bvol\b spike|high volume",
    "high_52w": r"52[- ]?(?:week|wk|w)[- ]?high|all[- ]?time[- ]?high|\bath\b|lifetime high",
    "stage2": r"stage ?2|stage analysis|weinstein",
    "gap": r"gap ?up|gap ?down|gap fill",
    "pullback": r"pull ?back|\bdip\b|buy on dips?",
    "divergence": r"divergence",
    "ichimoku": r"ichimoku|kumo cloud",
    "price_action": r"price action|\bpa\b setup|candlestick|engulfing|hammer|doji|inside bar",
}
THEMES = {
    "fii_buying": r"fii\w*\s*(?:stake|holding)?\s*(?:jump|up|increase|buying|bought|addition|added|hike)|foreign(?:ers)?\s*(?:just\s*)?(?:flood|bought|buying)",
    "dii_buying": r"\bdii\w*\s*(?:stake|holding)?\s*(?:jump|up|increase|buying|bought|addition)|mutual funds? (?:bought|buying|added)",
    "promoter_buying": r"promoter\w*\s*(?:bought|buying|increase|stake (?:up|hike|increase)|creeping)",
    "promoter_selling": r"promoter\w*\s*(?:sold|selling|pledge|stake (?:cut|sale|down))",
    "superstar": r"vijay kedia|kacholia|dolly khanna|madhusudan kela|jhunjhunwala|damani|mukul agrawal|porinju|singhania|ashish dhawan|ace investor|big ?bull|superstar investor",
    "results": r"\bq[1-4]\b|quarterly results?|earnings|results? (?:out|today|update)|\bfy\d{2}\b",
    "growth_screen": r"sales growth|profit growth|revenue growth|\bpat growth|earnings growth",
    "quality_screen": r"\broe\b|\broce\b|\bopm\b|debt[- ]free|zero debt",
    "valuation": r"undervalued|\blow p/?e\b|\bp/e\b|\bpe ratio|\bpe\s*[~:=]|cheap valuation|discount to|\bpeg\b",
    "order_win": r"order (?:win|book|inflow|worth|from)|bags? (?:an? )?order|contract (?:win|worth|from)",
    "deal": r"bulk deal|block deal",
    "corporate_action": r"buy ?back|bonus|stock split|dividend|rights issue",
    "ipo": r"\bipo\b|listing gain|\bgmp\b",
    "sector_theme": r"\bpsu\b|railway|defen[cs]e stocks?|capex|infra(?:structure)? stocks?|ev stocks?|renewable",
}
BULL = r"\bbuy\b|\bbuying\b|\bbought\b|\blong\b|bullish|accumulate|\badd(?:ed)?\b|uptrend|\brally\b|upside|outperform|\bstrong\b|🚀|📈|\bmultibagger\b|\bpositive\b|\bbreak ?out\b|\bbuy above\b"
BEAR = r"\bsell\b|\bselling\b|\bsold\b|\bshort\b|bearish|\bavoid\b|\bexit\b|downtrend|\bweak\b|📉|\bcrash\b|\bdump\b|\bbreak ?down\b|\bred flag\b|\bnegative\b"
# Indian-market context: needed for any match from authors who rarely post about Indian stocks.
INDIA = (r"₹|\brs\.?\s?\d|\binr\b|\bnse\b|\bbse\b|nifty|sensex|\bsebi\b|dalal street|#stockmarketindia|#indianstockmarket|"
         r"#sharemarket|#stocksinfocus|\bfiis?\b|\bdiis?\b|promoter|\bgmp\b|multibagger|\bmcap\b|market cap|\bcr\b|crore")
FOCUS_SHARE = 0.12  # author counts as India-market-focused above this share of posts with INDIA context
EXPLICIT = r"\bbuy\b|\bsell\b|\blong\b|\bshort\b|\bentry\b|\bsl\b|stop ?loss|\btgt\b|\btarget"
HORIZON = [("intraday", r"intraday|\bscalp|\bce\b|\bpe\b levels"), ("btst", r"\bbtst\b|\bstbt\b"),
           ("swing", r"\bswing\b|short[- ]term"), ("positional", r"positional|medium[- ]term"),
           ("long", r"long[- ]term|investment|invest\b|multibagger|wealth")]

_SETUP_RE = {k: re.compile(v, re.I) for k, v in SETUPS.items()}
_THEME_RE = {k: re.compile(v, re.I) for k, v in THEMES.items()}
_BULL, _BEAR, _EXPLICIT = re.compile(BULL, re.I), re.compile(BEAR, re.I), re.compile(EXPLICIT, re.I)
_INDIA = re.compile(INDIA, re.I)
_STOP = re.compile(r"(?:\bsl\b|stop ?-?loss|\bstop\b)\s*[:\-@=]?\s*(?:below|at|of|is|near|closing basis|cb)?\s*" + NUM, re.I)
_TGT = re.compile(r"(?:\btgts?\b|\btargets?\b|\btp\d?\b|\bt[1-3]\b)\s*[:\-@=]?\s*(?:of|is|at|upto|up to)?\s*" + NUM
                  + r"((?:\s*(?:[/,&-]|and|\bt[2-3]\b\s*[:\-]?)\s*(?:rs\.?|₹)?\s*\d{1,6}(?:,\d{2,3})*(?:\.\d+)?)*)", re.I)
_ENTRY = re.compile(r"(buy|entry|enter|long|accumulate|add|cmp|sell|short)\s*(?:(?-i:[A-Z][A-Z0-9&-]{1,19})\s*)?(?:it|now|only)?\s*"
                    r"(above|over|below|near|around|at|@|:|-|=|between|range|zone|on dips? (?:near|to|till)?)?\s*" + NUM, re.I)


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except (TypeError, ValueError):
        return None


def tags(text: str) -> tuple[list[str], list[str]]:
    return ([k for k, r in _SETUP_RE.items() if r.search(text)],
            [k for k, r in _THEME_RE.items() if r.search(text)])


def levels(text: str) -> dict:
    out = {"entry": None, "entry_type": "market", "stop": None, "targets": [], "side": None}
    m = _ENTRY.search(text)
    if m:
        verb, prep, value = m.group(1).lower(), (m.group(2) or "").lower(), _num(m.group(3))
        out["side"] = -1 if verb in ("sell", "short") else 1
        if verb != "cmp" or prep in ("", ":", "-", "=", "@", "at"):
            out["entry"] = value
        # entry_type is the price condition for the fill: trade at/above X, or at/below X
        if prep in ("above", "over"):
            out["entry_type"] = "above"
        elif prep == "below":
            out["entry_type"] = "below"
        elif prep.startswith("on dip") or prep in ("near", "around"):
            out["entry_type"] = "below" if out["side"] == 1 else "above"
    m = _STOP.search(text)
    if m:
        out["stop"] = _num(m.group(1))
    for m in _TGT.finditer(text):
        first = _num(m.group(1))
        if first is not None:
            out["targets"].append(first)
        for extra in re.findall(r"\d{1,6}(?:,\d{2,3})*(?:\.\d+)?", m.group(2) or ""):
            v = _num(extra)
            if v is not None and v not in out["targets"] and v > 3:  # skip "T2"/"T3" labels
                out["targets"].append(v)
    return out


def stance(text: str, lv: dict, setups: list[str], themes: list[str]) -> int:
    if lv.get("side"):
        return lv["side"]
    score = len(_BULL.findall(text)) - len(_BEAR.findall(text))
    score += sum(1 for t in themes if t in ("fii_buying", "dii_buying", "promoter_buying", "superstar", "order_win"))
    score -= sum(1 for t in themes if t == "promoter_selling")
    score += sum(1 for s in setups if s in ("breakout", "inverse_hs", "double_bottom", "high_52w", "stage2", "vcp", "cup_handle"))
    score -= sum(1 for s in setups if s in ("breakdown", "head_shoulders", "double_top"))
    return (score > 0) - (score < 0)


def horizon(text: str) -> str | None:
    for name, pat in HORIZON:
        if re.search(pat, text, re.I):
            return name
    return None


def extract(post: dict, index, ocr_text: str = "", focused: bool = False) -> list[dict]:
    """Calls for one post. A single-stock post keeps its levels; multi-stock lists become mentions.

    focused: the author mostly posts about Indian markets, so weak matches (bare tickers etc.) are
    trusted without Indian-market words in this particular post.
    """
    text = post.get("text") or ""
    links = json.loads(post.get("links") or "[]") if isinstance(post.get("links"), str) else (post.get("links") or [])
    found = index.find(text, links)
    if ocr_text:
        known = {s for s, _ in found}
        found += [(s, "ocr") for s, how in index.find(ocr_text) if s not in known and how in ("prefix", "tradingview", "cashtag", "ticker")]
    if not found:
        return []
    if not focused and not _INDIA.search(text + " " + ocr_text):
        return []
    full = text + ("\n" + ocr_text if ocr_text else "")
    setups, themes = tags(full)
    lv = levels(text)
    st = stance(text, lv, setups, themes)
    single = len(found) == 1
    explicit = bool(_EXPLICIT.search(text))
    hz = horizon(full)
    out = []
    for sym, how in found[:25]:
        keep_levels = single and (lv["entry"] or lv["stop"] or lv["targets"])
        kind = "call" if single and (explicit or keep_levels) and st != 0 else "mention"
        conf = {"prefix": .9, "tradingview": .85, "hashtag_name": .8, "hashtag": .75, "cashtag": .75, "name": .75,
                "index": .8, "name1": .6, "ticker": .6, "ocr": .5, "llm": .6}.get(how, .5)
        conf += .1 if kind == "call" else 0
        out.append({
            "symbol": sym, "matched_by": how, "stance": st, "kind": kind,
            "setups": setups, "themes": themes,
            "entry": lv["entry"] if keep_levels else None,
            "entry_type": lv["entry_type"] if keep_levels else "market",
            "stop": lv["stop"] if keep_levels else None,
            "targets": lv["targets"] if keep_levels else [],
            "horizon": hz, "confidence": round(min(conf, 1.0), 2),
        })
    return out


def author_focus(con) -> dict:
    """Share of each author's posts with Indian-market context."""
    tot, hit = {}, {}
    for r in con.execute("SELECT author, text FROM posts"):
        tot[r[0]] = tot.get(r[0], 0) + 1
        if _INDIA.search(r[1] or ""):
            hit[r[0]] = hit.get(r[0], 0) + 1
    return {a: hit.get(a, 0) / n for a, n in tot.items()}


def run(con, index, llm=None, authors=None, log=print, batch=2000) -> dict:
    """Extract calls from posts not yet processed. Re-extraction replaces a post's calls."""
    focus = author_focus(con)
    wanted = [a.lower().lstrip("@") for a in (authors or [])]
    where = "extracted = 0" + (f" AND lower(author) IN ({','.join('?' * len(wanted))})" if wanted else "")
    n_posts = n_calls = n_llm = 0
    while True:
        rows = con.execute(f"SELECT * FROM posts WHERE {where} LIMIT {batch}", wanted).fetchall()
        if not rows:
            break
        for row in rows:
            post = dict(row)
            calls = extract(post, index, post.get("ocr_text") or "", focus.get(post["author"], 0) >= FOCUS_SHARE)
            if llm is not None and llm.wants(post, calls):
                got = llm.extract(post, index)
                if got is not None:
                    calls, n_llm = got, n_llm + 1
            con.execute("DELETE FROM outcomes WHERE call_id IN (SELECT id FROM calls WHERE post_uid = ?)", (post["uid"],))
            con.execute("DELETE FROM calls WHERE post_uid = ?", (post["uid"],))
            for c in calls:
                con.execute(
                    """INSERT OR IGNORE INTO calls(post_uid, author, platform, ts_utc, symbol, matched_by, stance, kind,
                       setups, themes, entry, entry_type, stop, targets, horizon, confidence)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (post["uid"], post["author"], post["platform"], post["ts_utc"], c["symbol"], c["matched_by"],
                     c["stance"], c["kind"], json.dumps(c["setups"]), json.dumps(c["themes"]), c["entry"],
                     c["entry_type"], c["stop"], json.dumps(c["targets"]), c["horizon"], c["confidence"]))
            con.execute("UPDATE posts SET extracted = 1 WHERE uid = ?", (post["uid"],))
            n_posts += 1
            n_calls += len(calls)
        con.commit()
    return {"posts": n_posts, "calls": n_calls, "llm_posts": n_llm}
