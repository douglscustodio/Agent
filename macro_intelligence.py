"""
macro_intelligence.py — Inteligência macroeconômica
Monitora: FED/juros, CPI/PIB/empregos, S&P500/Nasdaq, DXY, geopolítica, ETF BTC
Fontes: Yahoo Finance (via yfinance), NewsAPI/RSS, FRED
Produz: MacroSnapshot com score de risco 0-100 e explicação em português
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp

from retry_utils import retry_async

from database import write_system_event
from logger import get_logger

log = get_logger("macro_intelligence")

# ---------------------------------------------------------------------------
# Tradução de termos em inglês para PT-BR (eventos macro RSS)
# ---------------------------------------------------------------------------

_MACRO_TRANSLATE_MAP = {
    "interest rate": "taxa de juros",
    "federal reserve": "Federal Reserve (Fed)",
    "inflation": "inflação",
    "cpi": "CPI (inflação)",
    "gdp": "PIB",
    "nonfarm payroll": "folha de pagamentos EUA",
    "unemployment": "desemprego",
    "fomc": "reunião Fed (FOMC)",
    "rate hike": "aumento de juros",
    "rate cut": "corte de juros",
    "bitcoin etf": "ETF de Bitcoin",
    "crypto regulation": "regulação de crypto",
    "sec": "SEC (regulador EUA)",
    "war": "conflito geopolítico",
    "sanctions": "sanções",
    "recession": "recessão",
}

def _translate_event_title(title: str) -> str:
    """Traduz termos-chave de títulos de eventos macro do inglês para PT-BR."""
    import re
    result = title
    t_lower = title.lower()
    for en, pt in _MACRO_TRANSLATE_MAP.items():
        if en in t_lower:
            pattern = re.compile(re.escape(en), re.IGNORECASE)
            result = pattern.sub(pt, result, count=1)
            break
    return result


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CACHE_TTL_S   = 1800    # 30 min cache
REQUEST_TIMEOUT = 10

# Yahoo Finance quote URLs (no API key needed)
YF_QUOTE_URL  = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"

# Economic calendar RSS (investing.com public feed)
ECON_CALENDAR_RSS = "https://www.investing.com/rss/news_301.rss"

# Market symbols to track
MARKET_SYMBOLS = {
    "SP500":  "^GSPC",
    "NASDAQ": "^IXIC",
    "DXY":    "DX-Y.NYB",
    "VIX":    "^VIX",
    "GOLD":   "GC=F",
    "BTC_ETF": "IBIT",    # BlackRock BTC ETF
}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class MarketData:
    symbol:      str
    name:        str
    price:       float
    change_pct:  float      # 24h % change
    trend:       str        # "UP" | "DOWN" | "FLAT"


@dataclass
class MacroEvent:
    title:       str
    impact:      str        # "HIGH" | "MEDIUM" | "LOW"
    sentiment:   str        # "positive" | "negative" | "neutral"
    source:      str
    published_at: float


@dataclass
class MacroSnapshot:
    # Market data
    sp500:       Optional[MarketData] = None
    nasdaq:      Optional[MarketData] = None
    dxy:         Optional[MarketData] = None
    vix:         Optional[MarketData] = None
    gold:        Optional[MarketData] = None
    btc_etf:     Optional[MarketData] = None

    # Events
    events:      List[MacroEvent] = field(default_factory=list)

    # Risk assessment
    risk_score:  float = 50.0     # 0=baixo risco, 100=alto risco
    risk_label:  str   = "NEUTRO"
    crypto_bias: str   = "NEUTRO" # "BULLISH" | "BEARISH" | "NEUTRO"
    explanation: List[str] = field(default_factory=list)

    # Meta
    updated_at:  float = field(default_factory=time.time)

    @property
    def is_fresh(self) -> bool:
        return (time.time() - self.updated_at) < CACHE_TTL_S


# ---------------------------------------------------------------------------
# Yahoo Finance fetcher
# ---------------------------------------------------------------------------

async def _fetch_yf_once(
    session: aiohttp.ClientSession,
    ticker:  str,
    name:    str,
) -> Optional[MarketData]:
    url = YF_QUOTE_URL.format(symbol=ticker)
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            headers={"User-Agent": "Mozilla/5.0"},
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            result = data["chart"]["result"][0]
            meta   = result["meta"]
            price  = float(meta.get("regularMarketPrice", 0))
            prev   = float(meta.get("previousClose", price))
            change = ((price - prev) / prev * 100) if prev > 0 else 0.0
            trend  = "UP" if change > 0.3 else ("DOWN" if change < -0.3 else "FLAT")
            return MarketData(
                symbol=ticker, name=name,
                price=price, change_pct=round(change, 2), trend=trend,
            )
    except Exception as exc:
        log.warning("MACRO_REFRESH_DONE", f"YF fetch failed {ticker}: {exc}")
        return None


# ---------------------------------------------------------------------------
# News event fetcher (RSS)
# ---------------------------------------------------------------------------

def _parse_macro_rss(xml_text: str) -> List[MacroEvent]:
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    HIGH_KEYWORDS   = {"fed", "fomc", "interest rate", "cpi", "inflation", "gdp", "nonfarm",
                       "payroll", "recession", "sanctions", "war", "ban", "etf", "approval"}
    NEGATIVE_WORDS  = {"crash", "recession", "ban", "sanction", "war", "inflation", "selloff",
                       "fear", "panic", "default", "crisis"}
    POSITIVE_WORDS  = {"approval", "etf", "bullish", "growth", "cut", "stimulus", "inflow",
                       "record", "rally", "surge"}

    events = []
    try:
        root    = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            return events
        for item in channel.findall("item")[:20]:
            title_el = item.find("title")
            pub_el   = item.find("pubDate")
            title    = title_el.text if title_el is not None else ""
            t_lower  = title.lower()

            try:
                ts = parsedate_to_datetime(pub_el.text).timestamp() if pub_el is not None else time.time()
            except Exception:
                ts = time.time()

            words = set(t_lower.split())
            impact = "HIGH" if words & HIGH_KEYWORDS else "LOW"
            sentiment = (
                "positive" if words & POSITIVE_WORDS
                else "negative" if words & NEGATIVE_WORDS
                else "neutral"
            )
            events.append(MacroEvent(
                title=title, impact=impact,
                sentiment=sentiment, source="macro_rss",
                published_at=ts,
            ))
    except Exception as exc:
        log.error("NEWS_PRIMARY_FAIL", f"macro RSS parse error: {exc}")
    return events


# ---------------------------------------------------------------------------
# Risk scorer
# ---------------------------------------------------------------------------

def _compute_risk(snap: MacroSnapshot) -> tuple:
    """Returns (risk_score 0-100, risk_label, crypto_bias, explanations)."""
    score        = 50.0
    explanations = []
    bullish_pts  = 0
    bearish_pts  = 0

    # VIX — fear index
    if snap.vix:
        if snap.vix.price > 30:
            score += 20; bearish_pts += 2
            explanations.append(f"⚠️ VIX alto ({snap.vix.price:.1f}) — medo no mercado")
        elif snap.vix.price > 20:
            score += 10; bearish_pts += 1
            explanations.append(f"⚠️ VIX elevado ({snap.vix.price:.1f}) — cautela recomendada")
        else:
            score -= 10; bullish_pts += 1
            explanations.append(f"✅ VIX baixo ({snap.vix.price:.1f}) — mercado calmo")

    # DXY — dólar forte = ruim para crypto
    if snap.dxy:
        if snap.dxy.change_pct > 0.5:
            score += 15; bearish_pts += 2
            explanations.append(f"⚠️ Dólar subindo ({snap.dxy.change_pct:+.1f}%) — pressão em crypto")
        elif snap.dxy.change_pct < -0.5:
            score -= 15; bullish_pts += 2
            explanations.append(f"✅ Dólar caindo ({snap.dxy.change_pct:+.1f}%) — favorável para crypto")
        else:
            explanations.append(f"➖ Dólar (DXY) estável ({snap.dxy.change_pct:+.1f}%)")

    # S&P500 — correlação com crypto
    if snap.sp500:
        if snap.sp500.change_pct > 1.0:
            score -= 10; bullish_pts += 1
            explanations.append(f"✅ S&P500 subindo ({snap.sp500.change_pct:+.1f}%) — risco on")
        elif snap.sp500.change_pct < -1.0:
            score += 10; bearish_pts += 1
            explanations.append(f"⚠️ S&P500 caindo ({snap.sp500.change_pct:+.1f}%) — risco off")
        else:
            explanations.append(f"➖ S&P500 estável ({snap.sp500.change_pct:+.1f}%)")

    # BTC ETF flows
    if snap.btc_etf:
        if snap.btc_etf.change_pct > 1.0:
            score -= 10; bullish_pts += 2
            explanations.append(f"✅ ETF BTC (IBIT) subindo {snap.btc_etf.change_pct:+.1f}% — entrada institucional")
        elif snap.btc_etf.change_pct < -1.0:
            score += 10; bearish_pts += 1
            explanations.append(f"⚠️ ETF BTC (IBIT) caindo {snap.btc_etf.change_pct:+.1f}%")

    # High impact news events
    high_neg = [e for e in snap.events if e.impact == "HIGH" and e.sentiment == "negative"]
    high_pos = [e for e in snap.events if e.impact == "HIGH" and e.sentiment == "positive"]
    if high_neg:
        score += 15; bearish_pts += 2
        explanations.append(f"🔴 Evento macro negativo: {_translate_event_title(high_neg[0].title)[:60]}")
    if high_pos:
        score -= 10; bullish_pts += 2
        explanations.append(f"🟢 Evento macro positivo: {_translate_event_title(high_pos[0].title)[:60]}")

    # Clamp
    score = max(0, min(100, score))

    # Risk label
    if score >= 75:
        label = "ALTO RISCO 🔴"
    elif score >= 55:
        label = "RISCO MODERADO 🟡"
    else:
        label = "BAIXO RISCO 🟢"

    # Crypto bias
    if bullish_pts > bearish_pts + 1:
        bias = "BULLISH"
    elif bearish_pts > bullish_pts + 1:
        bias = "BEARISH"
    else:
        bias = "NEUTRO"

    return round(score, 1), label, bias, explanations


# ---------------------------------------------------------------------------
# MacroEngine
# ---------------------------------------------------------------------------

class MacroEngine:
    def __init__(self) -> None:
        self._snapshot: Optional[MacroSnapshot] = None

    def get_snapshot(self) -> Optional[MacroSnapshot]:
        return self._snapshot

    def get_risk_score(self) -> float:
        return self._snapshot.risk_score if self._snapshot else 50.0

    def get_crypto_bias(self) -> str:
        return self._snapshot.crypto_bias if self._snapshot else "NEUTRO"

    async def refresh(self) -> MacroSnapshot:
        """Fetch all macro data and compute risk. Called every 30 min by scheduler."""
        snap = MacroSnapshot()
        t0   = time.monotonic()

        async with aiohttp.ClientSession() as session:
            # Fetch market data concurrently
            tasks = {
                "sp500":   _fetch_yf(session, MARKET_SYMBOLS["SP500"],   "S&P 500"),
                "nasdaq":  _fetch_yf(session, MARKET_SYMBOLS["NASDAQ"],  "Nasdaq"),
                "dxy":     _fetch_yf(session, MARKET_SYMBOLS["DXY"],     "Dólar (DXY)"),
                "vix":     _fetch_yf(session, MARKET_SYMBOLS["VIX"],     "VIX (Medo)"),
                "gold":    _fetch_yf(session, MARKET_SYMBOLS["GOLD"],    "Ouro"),
                "btc_etf": _fetch_yf(session, MARKET_SYMBOLS["BTC_ETF"], "ETF BTC (IBIT)"),
            }
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for key, result in zip(tasks.keys(), results):
                if not isinstance(result, Exception) and result:
                    setattr(snap, key, result)

            # Fetch macro news
            try:
                async with session.get(
                    ECON_CALENDAR_RSS,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as resp:
                    if resp.status == 200:
                        xml = await resp.text()
                        snap.events = _parse_macro_rss(xml)
            except Exception as exc:
                log.warning("NEWS_PRIMARY_FAIL", f"macro RSS fetch failed: {exc}")

        # Score risk
        snap.risk_score, snap.risk_label, snap.crypto_bias, snap.explanation = _compute_risk(snap)
        snap.updated_at = time.time()
        self._snapshot  = snap

        elapsed = round((time.monotonic() - t0) * 1000, 2)
        log.info(
            "MACRO_REFRESH_DONE",
            f"macro refresh: risk={snap.risk_score} bias={snap.crypto_bias} events={len(snap.events)}",
            latency_ms=elapsed,
        )
        await write_system_event(
            "MACRO_REFRESH_DONE",
            f"macro snapshot: risk={snap.risk_score} bias={snap.crypto_bias}",
            level="INFO", module="macro_intelligence",
            score=snap.risk_score, latency_ms=elapsed,
        )
        return snap

    def format_report(self) -> str:
        """Format macro snapshot as Portuguese Telegram message."""
        snap = self._snapshot
        if not snap:
            return "📊 *Dados macro ainda carregando...*"

        now_str = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        lines   = [
            "🌍 *CONTEXTO MACROECONÔMICO*",
            f"_{now_str}_",
            "",
            f"*Risco de mercado: {snap.risk_label}*",
            f"*Viés para crypto: {'🟢 ALTISTA' if snap.crypto_bias == 'BULLISH' else ('🔴 BAIXISTA' if snap.crypto_bias == 'BEARISH' else '⚪ NEUTRO')}*",  # PT-BR
            "",
            "*📈 Mercados tradicionais:*",
        ]

        for attr, label in [
            ("sp500",   "S&P 500"),
            ("nasdaq",  "Nasdaq"),
            ("dxy",     "Dólar (DXY)"),
            ("vix",     "VIX (Medo)"),
            ("gold",    "Ouro"),
            ("btc_etf", "ETF BTC (IBIT)"),
        ]:
            md: Optional[MarketData] = getattr(snap, attr, None)
            if md:
                arrow = "⬆️" if md.trend == "UP" else ("⬇️" if md.trend == "DOWN" else "➡️")
                lines.append(f"• {label}: `{md.price:,.2f}` {arrow} ({md.change_pct:+.2f}%)")

        lines.append("")
        lines.append("*🧠 O que isso significa para crypto:*")
        for exp in snap.explanation[:5]:
            lines.append(f"• {exp}")

        if snap.events:
            high_events = [e for e in snap.events if e.impact == "HIGH"][:3]
            if high_events:
                lines.append("")
                lines.append("*📰 Eventos de alto impacto:*")
                for e in high_events:
                    emoji = "🔴" if e.sentiment == "negative" else ("🟢" if e.sentiment == "positive" else "⚪")
                    title_pt = _translate_event_title(e.title)
                    lines.append(f"• {emoji} {title_pt[:80]}")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("_Jarvis AI Trading Monitor_")
        return "\n".join(lines)
