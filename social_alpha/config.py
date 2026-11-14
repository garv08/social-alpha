"""Settings from config.toml (see config.example.toml), with environment overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    top_home: Path = ROOT
    top_python: str = ""
    top_api: str = "http://127.0.0.1:8000"
    archiver_home: Path = Path("/home/dell/.cline/data/workspaces/chat/social-archiver")
    archiver_python: str = ""
    x_cred_file: str = ""
    archive_roots: list = field(default_factory=list)
    data_dir: Path = ROOT / "data"
    output_dir: Path = ROOT / "output"
    # fetch: [{"kind": "x-handle"|"x-list"|"tg-channel"|"tg-bridge", "name": ..., "slug": ..., "days": 7}]
    sources: list = field(default_factory=list)
    authors: list = field(default_factory=list)  # restrict analysis to these handles; empty = all
    scan_universe: str = "liquid"  # liquid | nifty50 | mentioned
    scan_limit: int = 400
    min_traded_value_cr: float = 5.0
    cost_bps: float = 10.0
    horizons: tuple = (5, 20, 60)
    max_hold_days: int = 20
    default_stop_pct: float = 8.0
    paper_capital: float = 10_000.0
    paper_max_positions: int = 4
    copy_min_calls: int = 8
    copy_min_score: float = 0.0
    desk: bool = True
    llm_url: str = ""
    llm_model: str = ""
    llm_key_env: str = "SOCIAL_ALPHA_LLM_KEY"
    ocr: bool = True
    background_symbols: int = 120
    background_per_symbol: int = 30
    made_hidden: int = 48
    made_epochs: int = 30
    made_masks: int = 4

    @property
    def db_path(self) -> Path:
        return self.data_dir / "social_alpha.sqlite"

    @property
    def top_db(self) -> Path:
        return Path(os.environ.get("TOP_DB") or self.top_home / "data" / "nse.duckdb")


def load(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("SOCIAL_ALPHA_CONFIG") or ROOT / "config.toml")
    cfg = Config()
    raw = tomllib.loads(path.read_text()) if path.exists() else {}
    for key, value in raw.items():
        if not hasattr(cfg, key):
            raise ValueError(f"unknown setting {key!r} in {path}")
        default = getattr(cfg, key)
        if isinstance(default, Path):
            value = Path(value).expanduser()
        elif isinstance(default, tuple):
            value = tuple(value)
        setattr(cfg, key, value)
    if not cfg.archive_roots:
        cfg.archive_roots = [str(cfg.archiver_home / "output")]
    cfg.top_python = cfg.top_python or str(cfg.top_home / ".venv" / "bin" / "python")
    cfg.archiver_python = cfg.archiver_python or str(cfg.archiver_home / ".venv" / "bin" / "python")
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TOP_DB", str(cfg.top_db))
    return cfg
