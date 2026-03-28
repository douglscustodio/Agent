"""
ranking.py — Top-3 signal ranker
- Only VALID (75–84) and HIGH_CONVICTION (85+) signals are eligible
- Sorted by score descending, with HIGH_CONVICTION always above VALID
- Tie-break: OI acceleration, then funding score
- Max 3 signals returned
"""

from dataclasses import dataclass, field
from typing import List, Optional

from scoring import ScoreResult, ScoreBand
from logger import get_logger

log = get_logger("ranking")


# ---------------------------------------------------------------------------
# Ranked signal wrapper
# ---------------------------------------------------------------------------

@dataclass
class RankedSignal:
    rank:   int
    result: ScoreResult

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


@dataclass
class RankingResult:
    top:          List[RankedSignal]
    total_scored: int
    total_valid:  int
    watchlist:    List[ScoreResult] = field(default_factory=list)
    rejected:     List[ScoreResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Ranker
# ---------------------------------------------------------------------------

def rank_signals(
    scores:              List[ScoreResult],
    max_signals:         int = 3,
    oi_accel_map:        Optional[dict] = None,   # symbol → OIAccelerationResult
    funding_map:         Optional[dict] = None,   # symbol → FundingResult
) -> RankingResult:
    """
    Rank a list of ScoreResult objects.
    Returns top-N VALID/HIGH_CONVICTION signals plus watchlist and rejected buckets.

    Tie-break order:
      1. ScoreBand (HIGH_CONVICTION > VALID)
      2. Total score (descending)
      3. OI acceleration score (descending), if oi_accel_map provided
      4. Funding score (descending), if funding_map provided
      5. Symbol alphabetical (deterministic)
    """
    if not scores:
        log.warning("PERFORMANCE_LOGGED", "rank_signals called with empty score list")
        return RankingResult(top=[], total_scored=0, total_valid=0)

    # Bucket by band
    valid_scores     = [s for s in scores if s.band in (ScoreBand.VALID, ScoreBand.HIGH_CONVICTION)]
    watchlist_scores = [s for s in scores if s.band == ScoreBand.WATCHLIST]
    rejected_scores  = [s for s in scores if s.band == ScoreBand.REJECT]

    def _sort_key(s: ScoreResult):
        band_priority = 1 if s.band == ScoreBand.HIGH_CONVICTION else 0
        oi_score  = oi_accel_map[s.symbol].score  if oi_accel_map and s.symbol in oi_accel_map else 0.0
        fund_score= funding_map[s.symbol].score   if funding_map  and s.symbol in funding_map  else 0.0
        return (band_priority, s.total, oi_score, fund_score, s.symbol)

    valid_sorted = sorted(valid_scores, key=_sort_key, reverse=True)
    top_n        = valid_sorted[:max_signals]

    ranked = [RankedSignal(rank=i + 1, result=s) for i, s in enumerate(top_n)]

    # Logging
    log.info(
        "PERFORMANCE_LOGGED",
        f"ranking complete: {len(valid_scores)} valid, {len(watchlist_scores)} watchlist, "
        f"{len(rejected_scores)} rejected → top {len(ranked)} selected",
    )
    for r in ranked:
        log.info(
            "ALERT_SENT",
            f"#{r.rank} {r.symbol} ({r.direction}) score={r.score:.1f} [{r.band}]",
            symbol=r.symbol,
            direction=r.direction,
            score=r.score,
        )

    return RankingResult(
        top=ranked,
        total_scored=len(scores),
        total_valid=len(valid_scores),
        watchlist=watchlist_scores,
        rejected=rejected_scores,
    )


# ---------------------------------------------------------------------------
# Formatted summary (for Telegram / logging)
# ---------------------------------------------------------------------------

def format_ranking_summary(result: RankingResult) -> str:
    """Return a plain-text summary of the top signals."""
    lines = [
        f"📊 Scan complete — {result.total_scored} symbols scored",
        f"✅ Valid: {result.total_valid}  |  👀 Watchlist: {len(result.watchlist)}  |  ❌ Rejected: {len(result.rejected)}",
        "",
    ]
    if not result.top:
        lines.append("No valid signals this scan.")
        return "\n".join(lines)

    for r in result.top:
        band_emoji = "🔥" if r.band == ScoreBand.HIGH_CONVICTION else "✅"
        lines.append(
            f"{band_emoji} #{r.rank} {r.symbol} {r.direction}  score={r.score:.1f}  [{r.band}]"
        )
        lines.append(
            f"   RS={r.components.get('relative_strength',0):.0f}  "
            f"ADX={r.components.get('adx_regime',0):.0f}  "
            f"RSI={r.components.get('rsi_quality',0):.0f}  "
            f"Fund={r.components.get('funding',0):.0f}  "
            f"OI={r.components.get('oi_acceleration',0):.0f}  "
            f"BB={r.components.get('bb_squeeze',0):.0f}  "
            f"ATR={r.components.get('atr_quality',0):.0f}"
        )
        lines.append("")

    return "\n".join(lines)
