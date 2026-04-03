"""
sector_rotation.py — Sector classification and rotation intelligence
Maps symbols to sectors, detects hot/cold sectors from news + price action,
and adjusts signal priority based on sector momentum.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from logger import get_logger

log = get_logger("sector_rotation")

# ---------------------------------------------------------------------------
# Sector taxonomy
# ---------------------------------------------------------------------------

SECTOR_MAP: Dict[str, str] = {
    # Layer 1
    "BTC":   "L1", "ETH":   "L1", "SOL":   "L1", "AVAX":  "L1",
    "ADA":   "L1", "DOT":   "L1", "NEAR":  "L1", "APT":   "L1",
    "SUI":   "L1", "TON":   "L1", "TRX":   "L1", "ATOM":  "L1",
    # Layer 2
    "MATIC": "L2", "ARB":   "L2", "OP":    "L2", "IMX":   "L2",
    "STRK":  "L2", "ZK":    "L2", "MANTA": "L2", "METIS": "L2",
    # DeFi
    "UNI":   "DEFI", "AAVE": "DEFI", "MKR":  "DEFI", "CRV":  "DEFI",
    "SNX":   "DEFI", "COMP": "DEFI", "BAL":  "DEFI", "YFI":  "DEFI",
    "LDO":   "DEFI", "RPL":  "DEFI", "GMX":  "DEFI", "DYDX": "DEFI",
    # AI / Data
    "FET":   "AI", "AGIX":  "AI", "OCEAN": "AI", "RNDR":  "AI",
    "WLD":   "AI", "TAO":   "AI", "GRT":   "AI", "NMR":   "AI",
    # Gaming / Metaverse
    "AXS":   "GAMING", "SAND": "GAMING", "MANA": "GAMING", "ENJ": "GAMING",
    "GALA":  "GAMING", "ILV":  "GAMING", "BEAM": "GAMING",
    # RWA / Infrastructure
    "LINK":  "INFRA", "BAND": "INFRA", "API3": "INFRA", "TIA": "INFRA",
    "PYTH":  "INFRA", "JTO":  "INFRA",
    # Meme
    "DOGE":  "MEME", "SHIB": "MEME", "PEPE": "MEME", "FLOKI": "MEME",
    "BONK":  "MEME", "WIF":  "MEME", "BOME": "MEME",
    # Stablecoins (excluded from signals)
    "USDT":  "STABLE", "USDC": "STABLE", "BUSD": "STABLE", "DAI": "STABLE",
}

SECTOR_LABELS: Dict[str, str] = {
    "L1":     "Layer 1",
    "L2":     "Layer 2 / Scaling",
    "DEFI":   "DeFi",
    "AI":     "AI / Data",
    "GAMING": "Gaming / Metaverse",
    "INFRA":  "Infrastructure",
    "MEME":   "Meme",
    "STABLE": "Stablecoin",
    "OTHER":  "Other",
}


def classify_symbol(symbol: str) -> str:
    """Return sector key for a symbol, stripping common suffixes."""
    clean = (
        symbol.upper()
        .replace("-PERP", "")
        .replace("USDT", "")
        .replace("USD", "")
        .replace("/", "")
        .strip()
    )
    return SECTOR_MAP.get(clean, "OTHER")


# ---------------------------------------------------------------------------
# Sector momentum snapshot
# ---------------------------------------------------------------------------

@dataclass
class SectorSnapshot:
    sector:           str
    label:            str
    symbols:          List[str]
    avg_score:        float          # mean composite signal score
    news_impact:      float          # mean news impact for sector
    hot:              bool           # True if sector is leading
    momentum_rank:    int            # 1 = hottest sector this cycle


@dataclass
class SectorRotationResult:
    snapshots:        List[SectorSnapshot]
    hot_sectors:      List[str]      # sector keys ranked by momentum
    cold_sectors:     List[str]
    sector_bonus:     Dict[str, float]   # symbol → score bonus (0–10)


# ---------------------------------------------------------------------------
# Sector rotation engine
# ---------------------------------------------------------------------------

def compute_sector_rotation(
    symbol_scores:      Dict[str, float],       # symbol → composite score (0–100)
    symbol_news_impact: Dict[str, float],       # symbol → news impact score
) -> SectorRotationResult:
    """
    Aggregate per-symbol scores into sector momentum.
    Returns sector rankings and per-symbol bonus for being in a hot sector.
    """
    sector_scores:  Dict[str, List[float]] = {}
    sector_news:    Dict[str, List[float]] = {}
    sector_symbols: Dict[str, List[str]]   = {}

    for sym, score in symbol_scores.items():
        sec = classify_symbol(sym)
        if sec == "STABLE":
            continue
        sector_scores .setdefault(sec, []).append(score)
        sector_news   .setdefault(sec, []).append(symbol_news_impact.get(sym, 0.0))
        sector_symbols.setdefault(sec, []).append(sym)

    snapshots: List[SectorSnapshot] = []
    for sec, scores in sector_scores.items():
        avg_score    = sum(scores) / len(scores)
        avg_news     = sum(sector_news.get(sec, [0.0])) / max(len(sector_news.get(sec, [1])), 1)
        momentum     = avg_score * 0.7 + avg_news * 0.3
        snapshots.append(SectorSnapshot(
            sector=sec,
            label=SECTOR_LABELS.get(sec, "Other"),
            symbols=sector_symbols[sec],
            avg_score=round(avg_score, 2),
            news_impact=round(avg_news, 2),
            hot=False,
            momentum_rank=0,
        ))

    # Rank by momentum
    snapshots.sort(key=lambda s: s.avg_score * 0.7 + s.news_impact * 0.3, reverse=True)
    hot_sectors  = []
    cold_sectors = []

    for rank, snap in enumerate(snapshots, 1):
        snap.momentum_rank = rank
        snap.hot = rank <= max(1, len(snapshots) // 3)   # top third = hot
        if snap.hot:
            hot_sectors.append(snap.sector)
        else:
            cold_sectors.append(snap.sector)

    # Bonus: symbols in hot sectors get +5, top sector gets +10
    sector_bonus: Dict[str, float] = {}
    top_sector = snapshots[0].sector if snapshots else None
    for sym in symbol_scores:
        sec = classify_symbol(sym)
        if sec == top_sector:
            sector_bonus[sym] = 10.0
        elif sec in hot_sectors:
            sector_bonus[sym] = 5.0
        else:
            sector_bonus[sym] = 0.0

    log.info(
        "PERFORMANCE_LOGGED",
        f"sector rotation: hot={hot_sectors} cold={cold_sectors[:3]}",
    )
    return SectorRotationResult(
        snapshots=snapshots,
        hot_sectors=hot_sectors,
        cold_sectors=cold_sectors,
        sector_bonus=sector_bonus,
    )


def get_sector_label(symbol: str) -> str:
    sec = classify_symbol(symbol)
    return SECTOR_LABELS.get(sec, "Other")
