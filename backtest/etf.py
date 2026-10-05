"""ETF list from Nasdaq Trader's public symbol directory (`nasdaqlisted.txt`, `otherlisted.txt`).

Both files carry an ETF Y/N column. They are downloaded ONCE, read-only, into `data/reference/`
(gitignored); this module only parses what is there -- it never touches the network. A symbol is an
ETF if it appears with ETF = Y under ANY of its symbol columns (`Symbol`; `ACT Symbol`,
`CQS Symbol`, `NASDAQ Symbol`), matched exactly after upper-casing. A symbol in neither file is
treated as not an ETF, so an ETF delisted before the download is NOT flagged (pre-registration,
section 4).
"""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from .data.config import REPO_ROOT

DEFAULT_REFERENCE_DIR = REPO_ROOT / "data" / "reference"
NASDAQ_FILE = "nasdaqlisted.txt"
OTHER_FILE = "otherlisted.txt"
_NASDAQ_SYMBOL_COLS = ("Symbol",)
_OTHER_SYMBOL_COLS = ("ACT Symbol", "CQS Symbol", "NASDAQ Symbol")


class EtfListMissingError(RuntimeError):
    """The reference files are not in data/reference/ (they are a one-time manual download)."""


@dataclass(frozen=True)
class EtfList:
    symbols: frozenset[str]
    files: tuple[dict, ...]          # name, bytes, sha256, rows, etf_rows -- for run headers


def _rows(text: str) -> list[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text), delimiter="|"):
        first = next(iter(r.values()), "") or ""
        if first.startswith("File Creation Time"):
            continue
        out.append(r)
    return out


def parse_etf_symbols(text: str, symbol_cols: tuple[str, ...]) -> tuple[set[str], int, int]:
    """-> (ETF symbols found under any symbol column, total rows, ETF=Y rows)."""
    rows = _rows(text)
    syms: set[str] = set()
    etf_rows = 0
    for r in rows:
        if (r.get("ETF") or "").strip().upper() != "Y":
            continue
        etf_rows += 1
        for col in symbol_cols:
            v = (r.get(col) or "").strip().upper()
            if v:
                syms.add(v)
    return syms, len(rows), etf_rows


def load_etf_list(reference_dir: str | Path | None = None) -> EtfList:
    d = Path(reference_dir) if reference_dir else DEFAULT_REFERENCE_DIR
    symbols: set[str] = set()
    files = []
    for name, cols in ((NASDAQ_FILE, _NASDAQ_SYMBOL_COLS), (OTHER_FILE, _OTHER_SYMBOL_COLS)):
        path = d / name
        if not path.is_file():
            raise EtfListMissingError(
                f"{path} not found. Download Nasdaq Trader's {name} once (read-only) into "
                f"{d} -- see docs/specs/ORB_Phase2_Preregistration.md, section 4.")
        raw = path.read_bytes()
        syms, nrows, netf = parse_etf_symbols(raw.decode("utf-8", errors="replace"), cols)
        symbols |= syms
        files.append({"name": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                      "rows": nrows, "etf_rows": netf})
    return EtfList(frozenset(symbols), tuple(files))
