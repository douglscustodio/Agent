"""
sector_rotation.py — Sector labeling for crypto assets
Maps symbols to sectors for heat analysis.
"""

def classify_symbol(symbol: str) -> str:
    """Alias for get_sector_label."""
    return get_sector_label(symbol)


def get_sector_label(symbol: str) -> str:
    """Return sector label for a symbol."""
    sectors = {
        "BTC": "Layer 1",
        "ETH": "Layer 1",
        "SOL": "Layer 1",
        "AVAX": "Layer 1",
        "ARB": "Layer 2",
        "OP": "Layer 2",
        "MATIC": "Layer 2",
        "LINK": "DeFi",
        "UNI": "DeFi",
        "AAVE": "DeFi",
        "MKR": "DeFi",
        "CRV": "DeFi",
        "LDO": "DeFi",
        "SNX": "DeFi",
        "SUSHI": "DeFi",
        "GMX": "DeFi",
        "DOGE": "Meme",
        "SHIB": "Meme",
        "PEPE": "Meme",
        "ARB": "L2/Infra",
        "OP": "L2/Infra",
        "APT": "Layer 1",
        "SUI": "Layer 1",
        "SEI": "Layer 1",
        "TIA": "Layer 1",
        "INJ": "Layer 1",
        "TIA": "Modular",
        "RENDER": "AI/Compute",
        "FET": "AI/Compute",
        "RNDR": "AI/Compute",
        "AGIX": "AI/Compute",
        "WLD": "AI/Compute",
        "JTO": "DeFi",
        "PYTH": "Data",
        "GRT": "Data",
    }
    return sectors.get(symbol, "Other")


def compute_sector_rotation(symbol_scores: dict, symbol_news: dict):
    """Compute sector heat based on scores and news."""
    from dataclasses import dataclass
    
    @dataclass
    class SectorResult:
        hot_sectors: list
        cold_sectors: list
    
    sector_scores = {}
    for sym, score in symbol_scores.items():
        sector = get_sector_label(sym)
        if sector not in sector_scores:
            sector_scores[sector] = []
        sector_scores[sector].append(score)
    
    sector_avg = {s: sum(scores)/len(scores) for s, scores in sector_scores.items()}
    hot = sorted(sector_avg.items(), key=lambda x: -x[1])[:3]
    cold = sorted(sector_avg.items(), key=lambda x: x[1])[:3]
    
    return SectorResult(
        hot_sectors=[s for s, _ in hot],
        cold_sectors=[s for s, _ in cold],
    )
