from __future__ import annotations

from pathlib import Path
from typing import List, Set


def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    txt = path.read_text(encoding="utf-8", errors="ignore")
    out: List[str] = []
    for line in txt.splitlines():
        s = line.strip().upper()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def load_universe_sp500_and_nasdaq100(
    *,
    repo_root: Path | None = None,
) -> List[str]:
    """
    Load the stable earnings-calendar universe from static text files:
      - data/universe/sp500.txt
      - data/universe/nasdaq100.txt

    Returns a unique, sorted list of tickers.
    """
    root = repo_root or Path(__file__).resolve().parent.parent
    base = root / "data" / "universe"
    sp = _read_lines(base / "sp500.txt")
    ndx = _read_lines(base / "nasdaq100.txt")

    # Normalize common punctuation (keep BRK.B style dots).
    out: Set[str] = set()
    for t in [*sp, *ndx]:
        s = str(t or "").strip().upper()
        if not s:
            continue
        out.add(s)
    return sorted(out)


