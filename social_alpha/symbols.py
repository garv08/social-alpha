"""Resolve stock mentions in free text to TOP symbols.

TOP ids: Nifty 50 stocks are bare (RELIANCE), other NSE listings 'NSE:HFCL', BSE-only 'BSE:XYZ',
indices 'IDX:NIFTY50' / 'IDX:BSE:SENSEX'. A mention resolves to the NSE id when one exists.

Matching, most to least reliable:
  cashtag / NSE:XYZ prefix   $HFCL, NSE:HFCL, BSE:HFCL
  TradingView link           tradingview.com/chart/HFCL/..., /symbols/NSE-HFCL/
  hashtag                    #HFCL, #tbotek (company name without spaces), #muthootfinance
  company name               "Muthoot Finance Ltd", "TBO Tek"
  bare ticker                HFCL, IRCTC (uppercase, not an English word, NSE-listed, 3+ chars)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

INDEX_ALIASES = {
    "NIFTY": "IDX:NIFTY50", "NIFTY50": "IDX:NIFTY50", "NIFTY 50": "IDX:NIFTY50",
    "BANKNIFTY": "IDX:NIFTYBANK", "BANK NIFTY": "IDX:NIFTYBANK", "NIFTYBANK": "IDX:NIFTYBANK",
    "FINNIFTY": "IDX:NIFTYFINANCIALSERVICES", "MIDCPNIFTY": "IDX:NIFTYMIDCAPSELECT",
    "SENSEX": "IDX:BSE:SENSEX", "BANKEX": "IDX:BSE:BANKEX",
}
# Tickers that are also everyday words, finance jargon or common abbreviations.
STOP = set("""
A I AI API LLM GPT USA USD INR CEO CFO IPO FPO OFS QIP ETF ATH ATL RSI EMA SMA MACD ADX VWAP ATR SL TP CMP
LTP BUY SELL LONG SHORT FII DII FPI GDP CPI RBI SEBI NSE BSE MCX IT PE PB EPS ROE ROCE ROA OPM NPM EBITDA
FOMO DCA BTC ETH SOL NFT DAO DEX CEX OTC PNL ROI ML NLP UI UX OK US UK EU PR TV OI PCR CE PM AM IST
NEW ONE TOP BIG BEST GOOD HIGH LOW OPEN CLOSE DAY WEEK MONTH YEAR LIVE NOW ALL FOR THE AND ARE YOU
NOT BUT CAN GET HAS WAS ITS OUT OUR WHY HOW WHO MAY INDIA BHARAT TATA ADANI JUST ONLY THIS THAT WITH
FROM INTO OVER YOUR THEM THEY WILL ALSO VERY MORE MOST MUCH SOME SUCH THAN THEN WHEN WHAT BEEN HAVE
BULL BEAR GAIN LOSS RISK HOLD STOP ENTRY EXIT CALL PUT DEMAT MTF SIP NAV AMC GST UPI FY Q1 Q2 Q3 Q4
H1 H2 YOY QOQ MOM NII NIM NPA GNPA NNPA CASA EV AC DC KYC PAN NRI HNI QIB MF EMI CAGR NA DM RT CR
AGI ESG OMNI IPL STAR ACE RAIN ROUTE GLOBAL META AVAX
""".split())
NAME_DROP = re.compile(r"\b(ltd|limited|the|co|company|corporation|corp|inc|plc)\b")
WORD = re.compile(r"[a-z0-9&]+")


def _norm_name(name: str) -> str:
    s = (name or "").lower().replace("&", " and ").replace(".", " ").replace("-", " ").replace("'", "")
    s = NAME_DROP.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


def _english_words() -> set:
    for path in ("/usr/share/dict/words", "/usr/share/dict/american-english"):
        p = Path(path)
        if p.exists():
            return {w.strip().upper() for w in p.read_text(errors="ignore").splitlines() if w.strip().isalpha()}
    return set()


STRONG = {"prefix", "tradingview", "hashtag_name", "name", "index"}
WEAK = {"ticker", "cashtag", "hashtag", "name1"}


class SymbolIndex:
    def __init__(self, symbols: list[dict]):
        self.info = {s["symbol"]: s for s in symbols}
        self.by_code: dict[str, str] = {}
        self.nse_codes: set[str] = set()
        for s in symbols:
            sym, kind = s["symbol"], s.get("kind")
            if kind == "index":
                continue
            code = sym.split(":", 1)[1] if ":" in sym else sym
            prev = self.by_code.get(code)
            if prev is None or self._rank(sym) < self._rank(prev):
                self.by_code[code] = sym
            if not sym.startswith("BSE:"):
                self.nse_codes.add(code)
        self.names: dict[str, str] = {}
        self.hashtags: dict[str, str] = {}
        for s in symbols:
            if s.get("kind") != "stock" or not s.get("name"):
                continue
            sym = self.by_code.get(s["symbol"].split(":", 1)[-1], s["symbol"])
            key = _norm_name(s["name"])
            if not key or len(key) < 4:
                continue
            # BSE names are often truncated ("Amara Raja Energy & Mobility L"); NSE names win.
            if key not in self.names or self._rank(sym) < self._rank(self.names[key]):
                self.names[key] = sym
                self.hashtags[key.replace(" ", "")] = sym
        self.words = _english_words()
        self.max_name_words = max((len(k.split()) for k in self.names), default=1)

    @staticmethod
    def _rank(sym: str) -> int:
        return 2 if sym.startswith("BSE:") else (1 if sym.startswith("NSE:") else 0)

    @classmethod
    def from_json(cls, path: Path) -> "SymbolIndex":
        return cls(json.loads(Path(path).read_text()))

    def is_index(self, sym: str) -> bool:
        return sym.startswith("IDX:")

    def _single_word_name_ok(self, key: str) -> bool:
        return len(key) >= 6 and key.upper() not in self.words and key.upper() not in STOP

    def resolve_code(self, code: str, strict: bool, shouting: bool = False) -> str | None:
        code = code.upper().strip("$#.,:;!?()[]")
        if code in INDEX_ALIASES:
            return INDEX_ALIASES[code]
        if code in STOP or len(code) < 2:
            return None
        sym = self.by_code.get(code)
        if sym is None:
            return None
        if strict:
            # bare uppercase token: NSE-listed, 3+ chars; English words (RELIANCE, TITAN) only when the
            # post is not written in capitals, where every word looks like a ticker
            if code not in self.nse_codes or len(code) < 3 or (shouting and code in self.words):
                return None
        return sym

    def find(self, text: str, links=()) -> list[tuple[str, str]]:
        """Return [(symbol, matched_by)] in first-seen order, one entry per symbol.

        matched_by in STRONG is reliable on its own; WEAK matches (bare tickers, cashtags, uppercase
        hashtags, one-word names) need Indian-market context, which extract.py checks.
        """
        found: dict[str, str] = {}

        def add(sym, how):
            if sym and sym not in found:
                found[sym] = how

        for url in links or ():
            m = re.search(r"tradingview\.com/(?:chart|symbols)/(?:(NSE|BSE)[-:])?([A-Z0-9&_.-]+)", url or "", re.I)
            if m:
                add(self.resolve_code(m.group(2), strict=False), "tradingview")
        for m in re.finditer(r"\b(NSE|BSE)\s*:\s*([A-Za-z0-9&-]{2,20})", text):
            add(self.resolve_code(m.group(2), strict=False), "prefix")
        for m in re.finditer(r"\$([A-Za-z][A-Za-z0-9&-]{1,19})\b", text):
            add(self.resolve_code(m.group(1), strict=False), "cashtag")
        for m in re.finditer(r"#([A-Za-z][A-Za-z0-9&_]{1,40})", text):
            tag = m.group(1)
            sym = self.resolve_code(tag, strict=False) if (tag.isupper() or tag.upper() in INDEX_ALIASES) else None
            if sym:
                add(sym, "index" if sym.startswith("IDX:") else "hashtag")
            else:
                add(self.hashtags.get(tag.lower().replace("_", "")), "hashtag_name")
        for alias in ("BANK NIFTY", "NIFTY 50"):
            if alias.lower() in text.lower():
                add(INDEX_ALIASES[alias], "index")
        # company names: longest n-gram first over normalised words
        words = _norm_name(text).split()
        i = 0
        while i < len(words):
            hit = None
            for n in range(min(self.max_name_words, len(words) - i), 0, -1):
                key = " ".join(words[i:i + n])
                sym = self.names.get(key)
                if sym and (n > 1 or self._single_word_name_ok(key)):
                    hit = (sym, n)
                    break
            if hit:
                add(hit[0], "name" if hit[1] > 1 else "name1")
                i += hit[1]
            else:
                i += 1
        letters = [ch for ch in text if ch.isalpha()]
        shouting = len(letters) > 20 and sum(ch.isupper() for ch in letters) / len(letters) > 0.5
        for m in re.finditer(r"(?<![$#:\w])([A-Z][A-Z0-9&-]{1,19})\b", text):
            sym = self.resolve_code(m.group(1), strict=True, shouting=shouting)
            add(sym, "index" if sym and sym.startswith("IDX:") else "ticker")
        return list(found.items())
