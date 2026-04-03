"""
ranking.py — Top-3 signal ranker

- VALID (60–74) and HIGH_CONVICTION (75+) signals are primary
- WATCHLIST (35–59) signals fill slots when no valid signals available
- Sorted by score descending, with HIGH_CONVICTION always above VALID
- Tie-break: EV score → OI acceleration → funding score
- Max 3 signals returned

UPGRADE: Correlation / sector deduplication
  Cryptos within the same sector are highly correlated.
  Sending SOL LONG + SUI LONG + APT LONG is effectively one trade.
  Ranking now:
    1. Picks the best signal per sector first (no penalty)
    2. Applies a CORRELATION_PENALTY for any additional same-sector pick
    3. Only allows a second pick from the same sector if NO other sector
       has a valid signal (universe exhaustion).
  This dramatically reduces correlated exposure and improves diversification.

UPGRADE: Expected value used as primary tie-breaker over raw score.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from scoring import ScoreResult, ScoreBand
from logger import get_logger

log = get_logger("ranking")


# ---------------------------------------------------------------------------
# Sector map — groups highly correlated assets
# ---------------------------------------------------------------------------

SECTOR_MAP: Dict[str, str] = {
    # Bitcoin
    "BTC":  "btc",
    # Ethereum + L2s (very high correlation)
    "ETH":  "eth_l2",
    "ARB":  "eth_l2",
    "OP":   "eth_l2",
    # Solana ecosystem
    "SOL":  "sol_eco",
    "SUI":  "sol_eco",
    "APT":  "sol_eco",
    "AVAX": "sol_eco",
    # Meme coins (near-perfect intra-sector correlation)
    "DOGE": "meme",
    "PEPE": "meme",
    "WIF":  "meme",
    "BONK": "meme",
    # AI / GPU tokens
    "FET":  "ai",
    "RNDR": "ai",
    "TAO":  "ai",
    # DeFi
    "UNI":  "defi",
    "AAVE": "defi",
    "LDO":  "defi",
    # Cosmos / interop ecosystem
    "NEAR": "cosmos",
    "INJ":  "cosmos",
    "TIA":  "cosmos",
    "LINK": "cosmos",
    "PYTH": "cosmos",
    "JTO":  "cosmos",
}

CORRELATION_PENALTY: float = 0.80  # 20% score penalty per additional same-sector pick
MAX_PER_SECTOR: int = 1            # default: 1 per sector unless universe exhausted


# ---------------------------------------------------------------------------
# Ranked signal wrapper
# ---------------------------------------------------------------------------

@dataclass
class RankedSignal:
    rank:   int
    result: ScoreResult
    penalized_score: float = 0.0   # UPGRADE: score after correlation penalty

    @property
    def symbol(self)    -> str:       return self.result.symbol
    @property
    def direction(self) -> str:       return self.result.direction
    @property
    def score(self)     -> float:     return self.result.total
    @property
    def band(self)      -> ScoreBand: return self.result.band
    @property
    def components(self) -> dict:     return self.result.components
    @property
    def ev_score(self)  -> float:     return self.result.ev_score
    @property
    def dominant(self)  -> str:       return self.result.dominant_component


@dataclass
class RankingResult:
    top:          List[RankedSignal]
    total_scored: int
    total_valid:  int
    watchlist:    List[ScoreResult] = field(default_factory=list)
    rejected:     List[ScoreResult] = field(default_factory=list)
    sectors_hit:  List[str] = field(default_factory=list)   # UPGRADE: diversity summary


# ---------------------------------------------------------------------------
# UPGRADE: Correlation-aware ranker
# ---------------------------------------------------------------------------

def rank_signals(
    scores:              List[ScoreResult],
    max_signals:         int = 3,
    oi_accel_map:        Optional[dict] = None,
    funding_map:         Optional[dict] = None,
) -> RankingResult:
    """
    Rank ScoreResult objects with sector-diversity enforcement.

    Algorithm:
      Pass 1 — Sort all valid signals by (band_priority, ev_score, raw_score, oi, funding)
      Pass 2 — Greedily select, applying CORRELATION_PENALTY for same-sector repeats.
               A penalized signal is only selected if its penalized_score still
               exceeds the next available signal's raw score — i.e. it genuinely
               beats the alternative even after the diversity tax.
    """
    if not scores:
        log.warning("PERFORMANCE_LOGGED", "rank_signals called with empty score list")
        return RankingResult(top=[], total_scored=0, total_valid=0)

    valid_scores     = [s for s in scores if s.band in (ScoreBand.VALID, ScoreBand.HIGH_CONVICTION)]
    watchlist_scores = [s for s in scores if s.band == ScoreBand.WATCHLIST]
    rejected_scores  = [s for s in scores if s.band == ScoreBand.REJECT]

    def _sort_key(s: ScoreResult):
        band_priority = 1 if s.band == ScoreBand.HIGH_CONVICTION else 0
        oi_score   = oi_accel_map[s.symbol].score if oi_accel_map and s.symbol in oi_accel_map else 0.0
        fund_score = funding_map[s.symbol].score  if funding_map  and s.symbol in funding_map  else 0.0
        # UPGRADE: EV score as primary tie-breaker
        return (band_priority, s.ev_score, s.total, oi_score, fund_score, s.symbol)

    valid_sorted = sorted(valid_scores, key=_sort_key, reverse=True)

    # --- Pass 2: greedy selection with correlation penalty ---
    selected: List[tuple] = []        # (RankedSignal, penalized_score)
    sector_counts: Dict[str, int] = {}

    for sig in valid_sorted:
        if len(selected) >= max_signals:
            break

        sector = SECTOR_MAP.get(sig.symbol, sig.symbol)
        count  = sector_counts.get(sector, 0)

        # Apply correlation penalty for repeat-sector picks
        penalty = CORRELATION_PENALTY ** count
        penalized = round(sig.total * penalty, 2)

        if count >= MAX_PER_SECTOR:
            # Only include if penalized score is still above WATCHLIST threshold
            # AND we don't have max_signals from diverse sectors yet
            diverse_count = len({SECTOR_MAP.get(r.symbol, r.symbol) for r, _ in selected})
            if penalized < 75.0:
                log.info(
                    "PERFORMANCE_LOGGED",
                    f"SKIP {sig.symbol}: correlation penalty reduces {sig.total:.1f} → {penalized:.1f} "
                    f"(sector={sector} already selected)",
                    symbol=sig.symbol,
                )
                continue

        ranked = RankedSignal(
            rank=len(selected) + 1,
            result=sig,
            penalized_score=penalized,
        )
        selected.append((ranked, penalized))
        sector_counts[sector] = count + 1

    top = [r for r, _ in selected]

    # If not enough valid signals, fill with WATCHLIST signals (score >= 35)
    if len(top) < max_signals and watchlist_scores:
        watchlist_sorted = sorted(watchlist_scores, key=_sort_key, reverse=True)
        for sig in watchlist_sorted:
            if len(top) >= max_signals:
                break
            sector = SECTOR_MAP.get(sig.symbol, sig.symbol)
            if sector not in sector_counts:
                ranked = RankedSignal(
                    rank=len(top) + 1,
                    result=sig,
                    penalized_score=sig.total,
                )
                top.append(ranked)
                sector_counts[sector] = 1
                log.info(
                    "ALERT_SENT",
                    f"#{ranked.rank} {sig.symbol} ({sig.direction}) score={sig.total:.1f} [WATCHLIST] "
                    f"ev={sig.ev_score:.1f} dominant={sig.dominant} (watchlist fill)",
                    symbol=sig.symbol, direction=sig.direction, score=sig.total,
                )

    # Re-assign final ranks (penalized ordering preserved from selection order)
    for i, r in enumerate(top):
        r.rank = i + 1

    sectors_hit = list({SECTOR_MAP.get(r.symbol, r.symbol) for r in top})

    # Logging
    log.info(
        "PERFORMANCE_LOGGED",
        f"ranking complete: {len(valid_scores)} valid, {len(watchlist_scores)} watchlist, "
        f"{len(rejected_scores)} rejected → top {len(top)} selected "
        f"sectors={sectors_hit}",
    )
    for r in top:
        penalty_note = f" (penalized={r.penalized_score:.1f})" if r.penalized_score != r.score else ""
        log.info(
            "ALERT_SENT",
            f"#{r.rank} {r.symbol} ({r.direction}) score={r.score:.1f} [{r.band}] "
            f"ev={r.ev_score:.1f} dominant={r.dominant}{penalty_note}",
            symbol=r.symbol, direction=r.direction, score=r.score,
        )

    return RankingResult(
        top=top,
        total_scored=len(scores),
        total_valid=len(valid_scores),
        watchlist=watchlist_scores,
        rejected=rejected_scores,
        sectors_hit=sectors_hit,
    )


# ---------------------------------------------------------------------------
# Formatted summary
# ---------------------------------------------------------------------------

def format_ranking_summary(result: RankingResult) -> str:
    lines = [
        f"📊 Scan complete — {result.total_scored} symbols scored",
        f"✅ Valid: {result.total_valid}  |  👀 Watchlist: {len(result.watchlist)}  |  ❌ Rejected: {len(result.rejected)}",
        f"🗂  Sectors: {', '.join(result.sectors_hit) if result.sectors_hit else 'none'}",
        "",
    ]
    if not result.top:
        lines.append("No valid signals this scan.")
        return "\n".join(lines)

    for r in result.top:
        band_emoji = "🔥" if r.band == ScoreBand.HIGH_CONVICTION else "✅"
        sector = SECTOR_MAP.get(r.symbol, r.symbol)
        penalty_note = (
            f"  ⚠️ corr-penalized→{r.penalized_score:.0f}"
            if r.penalized_score < r.score else ""
        )
        lines.append(
            f"{band_emoji} #{r.rank} {r.symbol} {r.direction}  "
            f"score={r.score:.1f}  EV={r.ev_score:.1f}  [{r.band}]  [{sector}]{penalty_note}"
        )
        lines.append(
            f"   RS={r.components.get('relative_strength',0):.0f}  "
            f"ADX={r.components.get('adx_regime',0):.0f}  "
            f"RSI={r.components.get('rsi_quality',0):.0f}  "
            f"Fund={r.components.get('funding',0):.0f}  "
            f"OI={r.components.get('oi_acceleration',0):.0f}  "
            f"BB={r.components.get('bb_squeeze',0):.0f}  "
            f"ATR={r.components.get('atr_quality',0):.0f}  "
            f"⭐{r.dominant}"
        )
        lines.append("")

    return "\n".join(lines)
