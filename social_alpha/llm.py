"""Optional LLM fallback for posts the rules cannot parse (OpenAI-compatible chat API).

Off unless config llm_url + llm_model are set and the key is in the env var named by llm_key_env.
Only the post text is sent. Symbols the model returns are re-validated against TOP's list.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

from social_alpha import extract as ex

PROMPT = """You extract Indian stock-market trade ideas from a social media post.
Return JSON only: {"calls": [{"ticker": "NSE ticker or company name", "stance": 1 | -1 | 0,
"entry": number|null, "entry_type": "market"|"above"|"below", "stop": number|null,
"targets": [numbers], "setup": "short setup name or null"}]}
stance 1 = bullish/buy, -1 = bearish/sell, 0 = neutral mention. Use [] when the post names no
Indian listed stock or index. Do not guess tickers that are not in the post.
Post:
"""


class LLM:
    def __init__(self, cfg):
        self.url = cfg.llm_url.rstrip("/")
        self.model = cfg.llm_model
        self.key = os.environ.get(cfg.llm_key_env, "")
        self.calls = 0

    @classmethod
    def maybe(cls, cfg):
        if cfg.llm_url and cfg.llm_model and os.environ.get(cfg.llm_key_env):
            return cls(cfg)
        return None

    def wants(self, post: dict, calls: list) -> bool:
        """Only trading-looking posts the rules could not settle."""
        text = post.get("text") or ""
        if len(text) < 25 or not ex._EXPLICIT.search(text):
            return False
        return not calls or all(c["stance"] == 0 for c in calls)

    def extract(self, post: dict, index):
        body = {"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": PROMPT + (post.get("text") or "")[:2000]}]}
        req = urllib.request.Request(self.url + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                content = json.loads(resp.read().decode())["choices"][0]["message"]["content"]
            data = json.loads(re.sub(r"^```(?:json)?|```$", "", content.strip()))
        except Exception:
            return None
        self.calls += 1
        setups, themes = ex.tags(post.get("text") or "")
        out = []
        for c in data.get("calls") or []:
            found = index.find(str(c.get("ticker") or "")) or index.find("$" + str(c.get("ticker") or ""))
            if not found:
                continue
            st = int(c.get("stance") or 0)
            has_levels = bool(c.get("entry") or c.get("stop") or c.get("targets"))
            out.append({"symbol": found[0][0], "matched_by": "llm", "stance": max(-1, min(1, st)),
                        "kind": "call" if st and has_levels else "mention",
                        "setups": setups + ([c["setup"]] if c.get("setup") else []), "themes": themes,
                        "entry": c.get("entry"), "entry_type": c.get("entry_type") or "market",
                        "stop": c.get("stop"), "targets": [t for t in (c.get("targets") or []) if isinstance(t, (int, float))],
                        "horizon": ex.horizon(post.get("text") or ""), "confidence": 0.6})
        return out
