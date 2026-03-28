"""
news_engine.py — News intelligence engine
Primary:  CryptoPanic API
Fallback: CoinDesk RSS → The Block RSS → cached last-known
Impact scoring: 0–100 per article, aggregated per symbol/sector
"""

import asyncio
import hashlib
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp

from database import write_system_event
from logger import get_logger

log = get_logger("news_engine")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CRYPTOPANIC_URL   = "https://cryptopanic.com/api/v1/posts/"
COINDESK_RSS      = "https://www.coindesk.com/arc/outboundfeeds/rss/"
THEBLOCK_RSS      = "https://www.theblock.co/rss.xml"
REQUEST_TIMEOUT   = 10          # seconds per HTTP call
MAX_ARTICLES      = 50          # articles to fetch per cycle
CACHE_TTL_S       = 300         # 5-minute in-memory cache

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class NewsArticle:
    id:           str
    title:        str
    source:       str
    published_at: float         # unix epoch
    url:          str
    symbols:      List[str]     # coins mentioned e.g. ["BTC", "ETH"]
    sentiment:    str           # "positive" | "negative" | "neutral"
    impact_score: float         # 0–100
    categories:   List[str]     # e.g. ["regulation", "hack", "partnership"]


@dataclass
class NewsContext:
    symbol:              str
    articles:            List[NewsArticle]
    aggregate_sentiment: str    # "positive" | "negative" | "neutral"
    impact_score:        float  # 0–100, highest single article impact for symbol
    top_headline:        str
    freshness_minutes:   float  # minutes since most recent article


# ---------------------------------------------------------------------------
# Impact keyword scoring
# ---------------------------------------------------------------------------

_NEGATIVE_HIGH = {
    "hack", "exploit", "breach", "stolen", "scam", "fraud", "rug",
    "sec", "lawsuit", "ban", "crash", "bankrupt", "insolvent", "freeze",
    "arrest", "sanction", "delist", "investigation",
}
_NEGATIVE_MED = {
    "warning", "risk", "concern", "delay", "suspend", "halt",
    "bearish", "sell-off", "dump", "exit", "outflow",
}
_POSITIVE_HIGH = {
    "etf", "approval", "launch", "partnership", "adoption", "integration",
    "upgrade", "bullish", "record", "all-time", "institutional", "acquire",
    "listing", "mainstream", "regulatory clarity", "breakthrough",
}
_POSITIVE_MED = {
    "growth", "expand", "rally", "inflow", "accumulate", "support",
    "positive", "gain", "recover", "milestone",
}


def _score_title(title: str) -> Tuple[float, str]:
    """Return (impact_score 0–100, sentiment)."""
    t = title.lower()
    words = set(t.replace(",", "").replace(".", "").split())

    neg_high = bool(words & _NEGATIVE_HIGH)
    neg_med  = bool(words & _NEGATIVE_MED)
    pos_high = bool(words & _POSITIVE_HIGH)
    pos_med  = bool(words & _POSITIVE_MED)

    if neg_high:
        return 90.0, "negative"
    if pos_high:
        return 85.0, "positive"
    if neg_med:
        return 60.0, "negative"
    if pos_med:
        return 55.0, "positive"
    return 30.0, "neutral"


def _extract_categories(title: str) -> List[str]:
    t = title.lower()
    cats = []
    if any(w in t for w in ("sec", "regulation", "ban", "law", "sanction")): cats.append("regulation")
    if any(w in t for w in ("hack", "exploit", "breach", "stolen")):          cats.append("security")
    if any(w in t for w in ("etf", "fund", "institutional")):                 cats.append("institutional")
    if any(w in t for w in ("partnership", "integration", "adopt")):          cats.append("adoption")
    if any(w in t for w in ("upgrade", "launch", "protocol")):                cats.append("technology")
    if any(w in t for w in ("market", "price", "rally", "dump")):             cats.append("market")
    return cats or ["general"]


def _article_id(title: str, source: str) -> str:
    return hashlib.md5(f"{title}{source}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# CryptoPanic parser
# ---------------------------------------------------------------------------

def _parse_cryptopanic(data: dict) -> List[NewsArticle]:
    articles = []
    for item in data.get("results", [])[:MAX_ARTICLES]:
        title = item.get("title", "")
        score, sentiment = _score_title(title)

        # CryptoPanic vote boost
        votes = item.get("votes", {})
        positive = votes.get("positive", 0)
        negative = votes.get("negative", 0)
        if positive > 10:
            score = min(score + 10, 100)
            sentiment = "positive"
        if negative > 10:
            score = min(score + 10, 100)
            sentiment = "negative"

        currencies = [c["code"] for c in item.get("currencies", [])]
        published = item.get("published_at", "")
        try:
            ts = datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = time.time()

        articles.append(NewsArticle(
            id=_article_id(title, "cryptopanic"),
            title=title,
            source="cryptopanic",
            published_at=ts,
            url=item.get("url", ""),
            symbols=currencies,
            sentiment=sentiment,
            impact_score=score,
            categories=_extract_categories(title),
        ))
    return articles


# ---------------------------------------------------------------------------
# RSS parser (CoinDesk + The Block)
# ---------------------------------------------------------------------------

def _parse_rss(xml_text: str, source_name: str) -> List[NewsArticle]:
    articles = []
    try:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            return articles
        items = channel.findall("item")[:MAX_ARTICLES]
        for item in items:
            title_el = item.find("title")
            link_el  = item.find("link")
            pub_el   = item.find("pubDate")

            title = title_el.text if title_el is not None else ""
            url   = link_el.text  if link_el  is not None else ""

            try:
                from email.utils import parsedate_to_datetime
                ts = parsedate_to_datetime(pub_el.text).timestamp() if pub_el is not None else time.time()
            except Exception:
                ts = time.time()

            score, sentiment = _score_title(title)
            articles.append(NewsArticle(
                id=_article_id(title, source_name),
                title=title,
                source=source_name,
                published_at=ts,
                url=url,
                symbols=[],          # RSS has no per-coin tagging
                sentiment=sentiment,
                impact_score=score,
                categories=_extract_categories(title),
            ))
    except ET.ParseError as exc:
        log.error("NEWS_PRIMARY_FAIL", f"RSS parse error ({source_name}): {exc}")
    return articles


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _fetch_json(session: aiohttp.ClientSession, url: str, params: dict = None) -> Optional[dict]:
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            log.warning("NEWS_PRIMARY_FAIL", f"HTTP {resp.status} from {url}")
            return None
    except Exception as exc:
        log.error("NEWS_PRIMARY_FAIL", f"request failed {url}: {exc}")
        return None


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
            if resp.status == 200:
                return await resp.text()
            log.warning("NEWS_PRIMARY_FAIL", f"HTTP {resp.status} from {url}")
            return None
    except Exception as exc:
        log.error("NEWS_PRIMARY_FAIL", f"request failed {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# NewsEngine
# ---------------------------------------------------------------------------

class NewsEngine:
    """
    Fetch and score news from CryptoPanic (primary) with RSS fallback chain.
    Thread-safe in-memory cache keyed by source.
    """

    def __init__(self, cryptopanic_token: Optional[str] = None):
        self._token    = cryptopanic_token
        self._cache:   List[NewsArticle] = []
        self._cache_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_all(self) -> List[NewsArticle]:
        """
        Fetch articles using primary → fallback chain.
        Returns cached result if within CACHE_TTL_S.
        """
        now = time.time()
        if self._cache and (now - self._cache_ts) < CACHE_TTL_S:
            log.debug("NEWS_FALLBACK_USED", "returning cached news articles")
            return self._cache

        articles: List[NewsArticle] = []

        async with aiohttp.ClientSession() as session:
            # 1. CryptoPanic
            articles = await self._fetch_cryptopanic(session)

            # 2. CoinDesk RSS fallback
            if not articles:
                log.warning("NEWS_PRIMARY_FAIL", "CryptoPanic returned no articles — trying CoinDesk RSS")
                await write_system_event("NEWS_PRIMARY_FAIL", "CryptoPanic empty, falling back", level="WARNING", module="news_engine")
                articles = await self._fetch_rss(session, COINDESK_RSS, "coindesk")

            # 3. The Block RSS fallback
            if not articles:
                log.warning("NEWS_FALLBACK_USED", "CoinDesk RSS failed — trying The Block RSS")
                await write_system_event("NEWS_FALLBACK_USED", "CoinDesk failed, trying TheBlock", level="WARNING", module="news_engine")
                articles = await self._fetch_rss(session, THEBLOCK_RSS, "theblock")

        # 4. Last-known cache
        if not articles and self._cache:
            log.warning("NEWS_FALLBACK_USED", "all news sources failed — using stale cache")
            await write_system_event("NEWS_FALLBACK_USED", "all sources failed, using stale cache", level="WARNING", module="news_engine")
            return self._cache

        if articles:
            self._cache    = articles
            self._cache_ts = now
            log.info(
                "PERFORMANCE_LOGGED",
                f"news fetch complete: {len(articles)} articles from {articles[0].source if articles else 'none'}",
            )

        return articles

    def get_context_for_symbol(
        self,
        symbol: str,
        articles: List[NewsArticle],
        window_minutes: int = 120,
    ) -> NewsContext:
        """
        Build NewsContext for a symbol from a pre-fetched article list.
        Considers articles published within `window_minutes`.
        """
        cutoff = time.time() - window_minutes * 60
        sym_upper = symbol.upper().replace("USDT", "").replace("-PERP", "")

        relevant = [
            a for a in articles
            if a.published_at >= cutoff and (
                sym_upper in [s.upper() for s in a.symbols]
                or sym_upper in a.title.upper()
            )
        ]

        if not relevant:
            return NewsContext(
                symbol=symbol,
                articles=[],
                aggregate_sentiment="neutral",
                impact_score=0.0,
                top_headline="No recent news",
                freshness_minutes=999.0,
            )

        relevant.sort(key=lambda a: a.impact_score, reverse=True)
        top = relevant[0]
        freshness = (time.time() - top.published_at) / 60

        # Aggregate sentiment by vote
        pos = sum(1 for a in relevant if a.sentiment == "positive")
        neg = sum(1 for a in relevant if a.sentiment == "negative")
        agg = "positive" if pos > neg else ("negative" if neg > pos else "neutral")

        return NewsContext(
            symbol=symbol,
            articles=relevant[:5],
            aggregate_sentiment=agg,
            impact_score=round(top.impact_score, 2),
            top_headline=top.title[:120],
            freshness_minutes=round(freshness, 1),
        )

    # ------------------------------------------------------------------
    # Private fetchers
    # ------------------------------------------------------------------

    async def _fetch_cryptopanic(self, session: aiohttp.ClientSession) -> List[NewsArticle]:
        if not self._token:
            log.warning("NEWS_PRIMARY_FAIL", "CryptoPanic token not configured")
            return []
        params = {
            "auth_token": self._token,
            "public":     "true",
            "filter":     "hot",
            "kind":       "news",
        }
        data = await _fetch_json(session, CRYPTOPANIC_URL, params)
        if data is None:
            return []
        articles = _parse_cryptopanic(data)
        if articles:
            log.info("PERFORMANCE_LOGGED", f"CryptoPanic: {len(articles)} articles fetched")
        return articles

    async def _fetch_rss(
        self,
        session: aiohttp.ClientSession,
        url: str,
        source_name: str,
    ) -> List[NewsArticle]:
        text = await _fetch_text(session, url)
        if not text:
            log.error("NEWS_PRIMARY_FAIL", f"{source_name} RSS fetch failed")
            await write_system_event("NEWS_PRIMARY_FAIL", f"{source_name} RSS failed", level="ERROR", module="news_engine")
            return []
        articles = _parse_rss(text, source_name)
        if articles:
            log.info("NEWS_FALLBACK_USED", f"{source_name} RSS: {len(articles)} articles fetched")
            await write_system_event("NEWS_FALLBACK_USED", f"using {source_name} RSS ({len(articles)} articles)", level="WARNING", module="news_engine")
        return articles
