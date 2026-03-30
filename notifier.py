"""
notifier.py — Dispatcher Telegram completo em Português BR

Mensagens:
  1. Sinal de trade — com IA, entrada/stop/alvos, notícias, macro
  2. Relatório de mercado a cada hora
  3. Alerta de movimento brusco do BTC
  4. Resumo diário com performance

UPGRADE: Integração Claude AI
  - Cada sinal passa pelo ai_analyst antes de ser enviado
  - Análise em PT-BR: motivo + risco + tags contextuais
  - Sinal com approve=False da IA → enviado com aviso ⚠️ (nunca bloqueado)

UPGRADE: Notícias linkáveis
  - Títulos de notícias em inglês são exibidos com link clicável
  - Tradução resumida do contexto em PT-BR

UPGRADE: Macro totalmente em PT-BR
  - Títulos de eventos RSS traduzidos por mapeamento de palavras-chave
"""

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp

from ai_analyst import AIAnalyst, AIAnalysis
from alerts_dedup import AlertDedupStore
from database import write_system_event
from logger import get_logger
from news_engine import NewsContext
from ranking import RankedSignal, RankingResult
from scoring import ScoreBand
from sector_rotation import get_sector_label

log = get_logger("notifier")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
SEND_TIMEOUT = 10

BTC_SPIKE_PCT      = 3.0
BTC_ALERT_COOLDOWN = 1800   # 30 min entre alertas de BTC


class _NotifierState:
    """Estado mutável do notifier encapsulado — sem globais soltos."""
    __slots__ = ("last_btc_price", "last_btc_alert", "daily_signals")

    def __init__(self) -> None:
        self.last_btc_price: float      = 0.0
        self.last_btc_alert: float      = 0.0
        self.daily_signals:  List[dict] = []

    def reset_daily(self) -> None:
        self.daily_signals.clear()


_state = _NotifierState()


# ---------------------------------------------------------------------------
# Helpers gerais
# ---------------------------------------------------------------------------

def _now_br() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def _get_price(symbol: str) -> float:
    try:
        from websocket_client import ws_price_cache
        p = ws_price_cache.get(symbol, 0.0)
        if p > 0:
            return p
    except Exception:
        pass
    try:
        from scanner import _meta_cache
        meta = _meta_cache.get(symbol)
        if meta and meta.mark_price > 0:
            return meta.mark_price
    except Exception:
        pass
    return 0.0


def _calc_levels(price: float, direction: str) -> Tuple[float, float, float, float]:
    if direction == "LONG":
        sl, tp1, tp2 = price * 0.97, price * 1.03, price * 1.06
    else:
        sl, tp1, tp2 = price * 1.03, price * 0.97, price * 0.94
    return price, round(sl, 6), round(tp1, 6), round(tp2, 6)


def _direction_emoji(d: str) -> str:
    return "📈" if d.upper() == "LONG" else "📉"


def _band_label(band) -> str:
    if "HIGH_CONVICTION" in str(band):
        return "🔥 ALTA CONVICÇÃO"
    return "✅ VÁLIDO"


def _sentiment_pt(s: str) -> str:
    return {"positive": "Positivo 🟢", "negative": "Negativo 🔴"}.get(s, "Neutro ⚪")


# ---------------------------------------------------------------------------
# UPGRADE: Tradução de títulos de notícias (inglês → contexto PT-BR)
#
# Notícias da CryptoPanic/RSS chegam em inglês.
# Não fazemos tradução automática (cara e lenta), mas:
#   1. Exibimos o título original com link clicável
#   2. Adicionamos uma tag de contexto em PT-BR baseada em palavras-chave
# ---------------------------------------------------------------------------

_NEWS_KEYWORD_MAP = {
    # Positivos
    "partnership": "parceria anunciada",
    "listing": "nova listagem",
    "launch": "lançamento",
    "upgrade": "atualização",
    "etf": "ETF / entrada institucional",
    "adoption": "adoção crescente",
    "bullish": "viés altista",
    "rally": "rali em andamento",
    "surge": "forte valorização",
    "all-time high": "máxima histórica",
    "ath": "máxima histórica",
    "institutional": "compra institucional",
    "approval": "aprovação regulatória",
    "record": "recorde histórico",
    # Negativos
    "hack": "⚠️ hack / ataque",
    "exploit": "⚠️ exploit detectado",
    "breach": "⚠️ brecha de segurança",
    "scam": "⚠️ golpe / fraude",
    "crash": "⚠️ queda acentuada",
    "ban": "⚠️ proibição regulatória",
    "lawsuit": "⚠️ processo judicial",
    "sec": "⚠️ ação regulatória SEC",
    "investigation": "⚠️ investigação",
    "bearish": "viés baixista",
    "dump": "⚠️ venda massiva",
    "liquidation": "⚠️ liquidações",
    "warning": "⚠️ alerta emitido",
    # Neutros
    "update": "atualização de protocolo",
    "fee": "mudança de taxas",
    "vote": "votação de governança",
    "proposal": "proposta de melhoria",
    "mainnet": "mainnet / rede principal",
    "testnet": "testnet em andamento",
    "staking": "oportunidade de staking",
    "airdrop": "airdrop anunciado",
}


def _news_context_pt(title: str) -> str:
    """Gera tag de contexto em PT-BR baseada no título em inglês."""
    title_lower = title.lower()
    for kw, label in _NEWS_KEYWORD_MAP.items():
        if kw in title_lower:
            return label
    return "notícia relevante"


def _format_news_line(news_ctx: NewsContext) -> str:
    """
    Formata notícia como linha linkável no Telegram (formato Markdown).
    Exibe: emoji + contexto PT-BR + link clicável para o título original.
    """
    if not news_ctx or not news_ctx.articles:
        return ""

    article = news_ctx.articles[0]
    sentiment_emoji = {"positive": "🟢", "negative": "🔴"}.get(
        news_ctx.aggregate_sentiment, "⚪"
    )
    context_pt = _news_context_pt(news_ctx.top_headline)
    age_min    = round(news_ctx.freshness_minutes)
    title_short = news_ctx.top_headline[:70]

    # Se tiver URL, exibe como link clicável
    url = getattr(article, "url", None) or getattr(article, "link", None)
    if url:
        return f"• {sentiment_emoji} [{title_short}...]({url}) — _{context_pt}_ ({age_min}min)"
    else:
        return f"• {sentiment_emoji} {title_short}… — _{context_pt}_ ({age_min}min)"


# ---------------------------------------------------------------------------
# UPGRADE: Tradução de eventos macro do inglês para PT-BR
# ---------------------------------------------------------------------------

_MACRO_KEYWORD_TRANSLATE = {
    "federal reserve": "Federal Reserve (Fed)",
    "interest rate": "taxa de juros",
    "inflation": "inflação",
    "cpi": "CPI (inflação ao consumidor)",
    "gdp": "PIB",
    "nonfarm payroll": "folha de pagamentos (EUA)",
    "unemployment": "desemprego",
    "fomc": "reunião do Fed (FOMC)",
    "treasury": "títulos do Tesouro dos EUA",
    "yield": "rendimento de títulos",
    "recession": "recessão",
    "rate hike": "aumento de juros",
    "rate cut": "corte de juros",
    "quantitative": "política monetária",
    "bitcoin etf": "ETF de Bitcoin",
    "crypto regulation": "regulação de crypto",
    "sec": "SEC (regulador EUA)",
    "sanctions": "sanções",
    "war": "conflito geopolítico",
    "geopolitical": "tensão geopolítica",
}


def _translate_macro_title(title: str) -> str:
    """Traduz termos-chave de títulos de eventos macro (inglês → PT-BR)."""
    result = title
    title_lower = title.lower()
    for en, pt in _MACRO_KEYWORD_TRANSLATE.items():
        if en in title_lower:
            # Substituição case-insensitive
            pattern = re.compile(re.escape(en), re.IGNORECASE)
            result = pattern.sub(pt, result, count=1)
            break   # Um por vez para não duplicar
    return result


# ---------------------------------------------------------------------------
# 1. Sinal de trade — com análise de IA
# ---------------------------------------------------------------------------

def _build_signal_message(
    signals:      List[RankedSignal],
    news_map:     Dict[str, Optional[NewsContext]],
    ai_map:       Dict[str, AIAnalysis],
    macro_snap    = None,
    memory        = None,
) -> str:
    lines = [
        "🚨 *SINAL DE TRADE DETECTADO*",
        f"_{_now_br()}_",
        "",
    ]

    for sig in signals[:3]:
        sym       = sig.symbol
        direction = sig.direction.upper()
        score     = sig.score
        band      = sig.band
        sector    = get_sector_label(sym)
        news_ctx  = news_map.get(sym)
        price     = _get_price(sym)
        comp      = sig.components
        ai        = ai_map.get(sym)

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"{_direction_emoji(direction)} *{sym}/USDT — {direction}*")
        lines.append(f"Força do sinal: `{score:.0f}/100` {_band_label(band)}")
        if ai and ai.used_ai:
            conf_emoji = "🟢" if ai.confidence >= 70 else ("🟡" if ai.confidence >= 55 else "🔴")
            lines.append(f"Confiança IA: `{ai.confidence}/100` {conf_emoji}")
            if not ai.approved:
                lines.append("⚠️ *IA identificou sinal de cautela — leia o risco abaixo*")
        lines.append("")

        # Níveis de trade
        if price > 0:
            _, sl, tp1, tp2 = _calc_levels(price, direction)
            sl_pct  = abs(price - sl)  / price * 100
            tp1_pct = abs(tp1 - price) / price * 100
            tp2_pct = abs(tp2 - price) / price * 100
            lines.append("*📌 COMO OPERAR:*")
            lines.append(f"• Entrada:   `${price:,.5f}`")
            lines.append(f"• Stop Loss: `${sl:,.5f}` ⛔ ({sl_pct:.1f}% de risco)")
            lines.append(f"• Alvo 1:   `${tp1:,.5f}` 🎯 (+{tp1_pct:.1f}%)")
            lines.append(f"• Alvo 2:   `${tp2:,.5f}` 🎯 (+{tp2_pct:.1f}%)")
            lines.append(f"• Setor: {sector}")
            lines.append("")

        # UPGRADE: Explicação da IA em PT-BR
        if ai and ai.used_ai and ai.reason:
            lines.append("*🤖 ANÁLISE IA (PT-BR):*")
            lines.append(f"• {ai.reason}")
            if ai.risk_note:
                lines.append(f"• ⚠️ Risco: {ai.risk_note}")
            if ai.context_tags:
                tags_str = " · ".join(f"`{t}`" for t in ai.context_tags[:3])
                lines.append(f"• Tags: {tags_str}")
            lines.append("")
        else:
            # Fallback: análise baseada em regras em PT-BR
            lines.append("*🧠 POR QUE ESTE SINAL?*")
            rs   = comp.get("relative_strength", 0)
            adx  = comp.get("adx_regime", 0)
            oi   = comp.get("oi_acceleration", 0)
            bb   = comp.get("bb_squeeze", 0)
            atr  = comp.get("atr_quality", 0)
            fund = comp.get("funding", 0)

            if rs >= 70 and adx >= 70:
                lines.append("• 📊 Tendência forte — superando o BTC com força")
            elif rs >= 65:
                lines.append("• 📊 Mais forte que o BTC no momento")
            elif adx >= 65:
                lines.append("• 📊 Tendência clara no gráfico")
            else:
                lines.append("• 📊 Movimento moderado — confirme no gráfico")

            if oi >= 85:
                lines.append("• 💰 Muito dinheiro entrando agora — alta convicção")
            elif oi >= 65:
                lines.append("• 💰 Interesse crescente de traders no ativo")
            elif oi < 45:
                lines.append("• ⚠️ Interesse dos traders em queda — cautela")

            if fund >= 75:
                lines.append("• 💸 Financiamento favorável para esta direção")
            elif fund <= 35:
                lines.append("• ⚠️ Financiamento desfavorável — aumento do risco")

            if bb >= 75 and atr >= 75:
                lines.append("• ⏱ Entrada no início do movimento — ótimo timing")
            elif bb >= 60:
                lines.append("• ⏱ Bom momento para entrada")
            else:
                lines.append("• ⏱ Movimento já iniciado — entre com mais cautela")
            lines.append("")

        # UPGRADE: Notícias linkáveis em PT-BR
        if news_ctx and news_ctx.articles:
            news_line = _format_news_line(news_ctx)
            if news_line:
                lines.append("*📰 NOTÍCIA RECENTE:*")
                lines.append(news_line)
                lines.append("")
        elif not news_ctx or not news_ctx.articles:
            lines.append("• 📰 _Configure CRYPTOPANIC\\_TOKEN para notícias em tempo real_")
            lines.append("")

        # Contexto macro
        if macro_snap:
            bias_emoji = "🟢" if macro_snap.crypto_bias == "BULLISH" else (
                "🔴" if macro_snap.crypto_bias == "BEARISH" else "⚪"
            )
            bias_pt = {"BULLISH": "ALTISTA", "BEARISH": "BAIXISTA"}.get(
                macro_snap.crypto_bias, "NEUTRO"
            )
            lines.append("*🌍 CONTEXTO MACRO:*")
            lines.append(f"• Risco de mercado: {macro_snap.risk_label}")
            lines.append(f"• Viés macro: {bias_emoji} {bias_pt}")
            if macro_snap.explanation:
                lines.append(f"• {macro_snap.explanation[0]}")
            if macro_snap.risk_score > 70:
                lines.append("• ⚠️ Risco macro alto — reduza o tamanho da posição")
            lines.append("")

        # Memória/aprendizado
        if memory:
            from sector_rotation import classify_symbol
            insight = memory.get_insight(
                direction, score,
                sig.result.regime_used or "TRENDING",
                classify_symbol(sym),
                macro_snap.risk_score if macro_snap else 50,
            )
            if insight.explanation:
                lines.append("*🧠 O QUE O AGENTE APRENDEU:*")
                for exp in insight.explanation[:2]:
                    lines.append(f"• {exp}")
                if insight.ignore_signal:
                    lines.append("• ⛔ ATENÇÃO: padrão histórico de baixa performance")
                lines.append("")

        lines.append("*⚠️ GESTÃO DE RISCO:*")
        lines.append("• Arrisque no máximo 1–2% do capital por operação")
        lines.append("• Respeite o Stop Loss — ele protege sua conta")
        lines.append("• Este é um sinal, não uma garantia de lucro")
        lines.append("")

        _state.daily_signals.append({
            "symbol": sym, "direction": direction,
            "score": score, "price": price,
            "sl": sl if price > 0 else 0,
            "tp1": tp1 if price > 0 else 0,
            "time": _now_br(),
        })

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Jarvis AI Trading Monitor_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. Relatório de mercado
# ---------------------------------------------------------------------------

async def build_market_report(news_engine=None) -> str:
    btc_price = _get_price("BTC")
    eth_price = _get_price("ETH")
    sol_price = _get_price("SOL")

    lines = [
        "📊 *RELATÓRIO DE MERCADO*",
        f"_{_now_br()}_",
        "",
        "*💹 Preços atuais:*",
    ]

    if btc_price > 0:
        lines.append(f"• BTC: `${btc_price:,.2f}`")
    if eth_price > 0:
        lines.append(f"• ETH: `${eth_price:,.2f}`")
    if sol_price > 0:
        lines.append(f"• SOL: `${sol_price:,.2f}`")
    lines.append("")

    # Regime BTC
    try:
        from hyperliquid_client import fetch_all_candles
        from btc_regime import compute_adx
        candle_map = await fetch_all_candles(["BTC"], interval="1h", count=50)
        candles    = candle_map.get("BTC", [])
        if len(candles) >= 30:
            highs  = [c.high  for c in candles]
            lows   = [c.low   for c in candles]
            closes = [c.close for c in candles]
            regime = compute_adx(highs, lows, closes)

            regime_pt = {
                "TRENDING": "📈 Em tendência",
                "RANGING":  "↔️ Lateral / sem direção clara",
                "WEAK":     "🔄 Tendência fraca",
            }.get(str(regime.regime).split(".")[-1], "Indefinido")

            dir_pt = {
                "UP": "Para cima ⬆️",
                "DOWN": "Para baixo ⬇️",
                "NEUTRAL": "Neutro ➡️",
            }.get(regime.trend_direction, "Neutro")

            lines.append("*📉 Regime do BTC (1h):*")
            lines.append(f"• Situação: {regime_pt}")
            lines.append(f"• Direção: {dir_pt}")
            lines.append(f"• Força da tendência (ADX): `{regime.adx:.1f}`")
            lines.append("")
    except Exception as exc:
        log.warning("NOTIFIER_EVENT", f"market report regime error: {exc}")

    # UPGRADE: Notícias recentes — com contexto PT-BR + link
    if news_engine:
        try:
            articles = news_engine._cache[:6] if news_engine._cache else []
            if articles:
                lines.append("*📰 Últimas notícias:*")
                for a in articles[:4]:
                    age_min    = round((time.time() - a.published_at) / 60)
                    emoji      = "🟢" if a.sentiment == "positive" else (
                        "🔴" if a.sentiment == "negative" else "⚪"
                    )
                    context_pt = _news_context_pt(a.title)
                    url        = getattr(a, "url", None) or getattr(a, "link", None)
                    title_short = a.title[:65]
                    if url:
                        lines.append(f"• {emoji} [{title_short}...]({url}) _{context_pt}_ ({age_min}min)")
                    else:
                        lines.append(f"• {emoji} {title_short}… _{context_pt}_ ({age_min}min)")
                lines.append("")
        except Exception:
            pass

    # Dica horária
    lines.append("*💡 Dica do momento:*")
    hour = datetime.now(timezone.utc).hour
    if 0 <= hour < 6:
        lines.append("• Madrugada UTC — liquidez menor, cuidado com fakeouts")
    elif 8 <= hour < 12:
        lines.append("• Manhã europeia — mercado começando a movimentar")
    elif 13 <= hour < 17:
        lines.append("• Tarde americana — maior volume do dia, bons sinais")
    else:
        lines.append("• Monitore os níveis de suporte e resistência")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Próximo relatório em 1 hora_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. Alerta BTC
# ---------------------------------------------------------------------------

async def check_btc_spike(notifier: "Notifier") -> None:

    current = _get_price("BTC")
    if current <= 0:
        return

    if _state.last_btc_price <= 0:
        _state.last_btc_price = current
        return

    pct_change = (current - _state.last_btc_price) / _state.last_btc_price * 100
    now = time.time()

    if abs(pct_change) >= BTC_SPIKE_PCT and (now - _state.last_btc_alert) > BTC_ALERT_COOLDOWN:
        direction = "SUBIU" if pct_change > 0 else "CAIU"
        emoji     = "🚀" if pct_change > 0 else "💥"
        impact    = "pode abrir oportunidades de LONG" if pct_change > 0 else (
            "cuidado — pode arrastar altcoins para baixo"
        )

        msg = (
            f"{emoji} *ALERTA BTC — MOVIMENTO BRUSCO!*\n"
            f"_{_now_br()}_\n\n"
            f"• BTC *{direction}* `{abs(pct_change):.1f}%` rapidamente\n"
            f"• Preço atual: `${current:,.2f}`\n"
            f"• Preço anterior: `${_state.last_btc_price:,.2f}`\n\n"
            f"*O que isso significa?*\n"
            f"• {impact}\n"
            f"• Aguarde confirmação antes de entrar em novas posições\n"
            f"• Se você tiver posição aberta, verifique seu stop loss\n\n"
            f"_Jarvis AI Trading Monitor_"
        )
        await notifier.send_system_alert(msg)
        _state.last_btc_alert = now
        _state.last_btc_price = current

        log.info("ALERT_SENT", f"BTC spike alert enviado: {pct_change:.1f}%")
        await write_system_event(
            "ALERT_SENT", f"BTC spike {pct_change:.1f}%",
            level="INFO", module="notifier", symbol="BTC", score=abs(pct_change),
        )
    else:
        _state.last_btc_price = current * 0.9 + _state.last_btc_price * 0.1


# ---------------------------------------------------------------------------
# 4. Resumo diário
# ---------------------------------------------------------------------------

async def send_daily_summary(notifier: "Notifier", tracker=None) -> None:

    lines = [
        "📋 *RESUMO DO DIA*",
        f"_{_now_br()}_",
        "",
    ]

    total = len(_state.daily_signals)
    if total == 0:
        lines.append("Nenhum sinal enviado hoje.")
    else:
        lines.append(f"*📊 Sinais enviados hoje: {total}*")
        for s in _state.daily_signals[-10:]:
            dir_emoji = "📈" if s["direction"] == "LONG" else "📉"
            lines.append(
                f"• {dir_emoji} {s['symbol']} {s['direction']} — "
                f"Score `{s['score']:.0f}` às {s['time'][-8:]}"
            )
        lines.append("")

    if tracker:
        try:
            stats   = await tracker.get_recent_stats(days=1)
            tp1     = stats.get("tp1", 0)
            sl_hit  = stats.get("sl", 0)
            neutral = stats.get("neutral", 0)
            total_r = stats.get("total", 0)
            win_r   = stats.get("win_rate", 0.0)
            avg_pnl = stats.get("avg_pnl", 0.0)

            if total_r > 0:
                lines.append("*🏆 Performance de hoje (24h):*")
                lines.append(f"• ✅ Acertou (TP1): {tp1}")
                lines.append(f"• ❌ Stop Loss: {sl_hit}")
                lines.append(f"• ➖ Neutro: {neutral}")
                lines.append(f"• Taxa de acerto: `{win_r:.1f}%`")
                lines.append(f"• PnL médio: `{avg_pnl:+.2f}%`")
                lines.append("")
        except Exception as exc:
            log.warning("NOTIFIER_EVENT", f"daily summary stats error: {exc}")

    lines.append("*💡 Lembre-se:*")
    lines.append("• Gerencie bem o risco — 1 a 2% por operação")
    lines.append("• Sinais são probabilidades, não certezas")
    lines.append("• Amanhã é um novo dia de oportunidades 🚀")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Jarvis AI Trading Monitor_")

    await notifier.send_system_alert("\n".join(lines))
    _state.daily_signals.clear()
    log.info("ALERT_SENT", "resumo diário enviado")


# ---------------------------------------------------------------------------
# Telegram sender
# ---------------------------------------------------------------------------

async def _send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        log.warning("ALERT_SENT", "Telegram não configurado — TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID ausente")
        return False
    url     = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,   # UPGRADE: links funcionam
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload,
                timeout=aiohttp.ClientTimeout(total=SEND_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    log.info("ALERT_SENT", "Telegram: mensagem entregue")
                    return True
                body = await resp.text()
                log.error("ALERT_SENT", f"Telegram erro {resp.status}: {body[:200]}")
                return False
    except Exception as exc:
        log.error("ALERT_SENT", f"Telegram falhou: {exc}")
        return False


# ---------------------------------------------------------------------------
# Notifier class
# ---------------------------------------------------------------------------

class Notifier:
    def __init__(
        self,
        telegram_token:   Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
    ):
        self._token   = telegram_token   or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._dedup   = AlertDedupStore()
        self._news_engine = None
        self._ai      = AIAnalyst()    # UPGRADE: instância compartilhada

    async def startup(self) -> None:
        await self._dedup.ensure_table()
        log.info("SYSTEM_READY",
                 f"notifier pronto — AI={'habilitada' if self._ai.enabled else 'desabilitada'}")

    def set_news_engine(self, engine) -> None:
        self._news_engine = engine

    # ------------------------------------------------------------------
    # Sinais de trade — com análise AI
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        ranking:    RankingResult,
        news_map:   Dict[str, Optional[NewsContext]],
        macro_snap  = None,
        memory      = None,
    ) -> None:
        if not ranking.top:
            log.info("ALERT_SUPPRESSED", "nenhum sinal válido para despachar")
            return

        eligible = []
        for sig in ranking.top:
            should_send, reason = await self._dedup.should_send(
                sig.symbol, sig.direction, sig.score
            )
            if should_send:
                eligible.append(sig)
            else:
                log.info(
                    "ALERT_SUPPRESSED",
                    f"suprimido {sig.symbol} {sig.direction}: {reason}",
                    symbol=sig.symbol, direction=sig.direction, score=sig.score,
                )

        if not eligible:
            log.info("ALERT_SUPPRESSED", "todos os sinais suprimidos pelo dedup (aguarde cooldown)")
            return

        # UPGRADE: análise IA em paralelo para todos os candidatos elegíveis
        macro_bias = "NEUTRO"
        if macro_snap:
            macro_bias = macro_snap.crypto_bias

        try:
            from scanner import _meta_cache
            meta_map = _meta_cache
        except Exception:
            meta_map = {}

        ai_map = await self._ai.analyze_batch(
            candidates=eligible,
            meta_map=meta_map,
            news_map=news_map,
            macro_bias=macro_bias,
        )

        # Filtrar sinais que a IA reprovou (opcional — apenas avisa, não bloqueia)
        # Design: IA com approve=False → sinal enviado COM aviso, nunca descartado
        reproved = [sym for sym, ai in ai_map.items() if not ai.approved]
        if reproved:
            log.warning(
                "NOTIFIER_EVENT",
                f"IA marcou cautela em: {reproved} — sinais enviados com aviso",
            )

        message = _build_signal_message(
            eligible, news_map, ai_map,
            macro_snap=macro_snap, memory=memory,
        )
        sent = await _send_telegram(self._token, self._chat_id, message)

        if sent:
            for sig in eligible:
                await self._dedup.record_sent(sig.symbol, sig.direction, sig.score)
                ai_note = ""
                if ai_map.get(sig.symbol):
                    ai = ai_map[sig.symbol]
                    ai_note = f" IA={ai.confidence} aprovado={ai.approved}"
                await write_system_event(
                    "ALERT_SENT",
                    f"sinal enviado: {sig.symbol} {sig.direction}{ai_note}",
                    level="INFO", module="notifier",
                    symbol=sig.symbol, direction=sig.direction,
                    score=sig.score, alert_id=f"{sig.symbol}:{sig.direction}",
                )
        else:
            log.error("ALERT_SENT", "Telegram dispatch falhou — verifique token e chat_id")

    # ------------------------------------------------------------------
    # Relatório de mercado (a cada hora)
    # ------------------------------------------------------------------

    async def send_market_report(self) -> None:
        msg  = await build_market_report(self._news_engine)
        sent = await _send_telegram(self._token, self._chat_id, msg)
        if sent:
            log.info("ALERT_SENT", "relatório de mercado enviado")

    # ------------------------------------------------------------------
    # Alerta BTC spike (a cada 5 min)
    # ------------------------------------------------------------------

    async def check_btc_spike(self) -> None:
        await check_btc_spike(self)

    # ------------------------------------------------------------------
    # Resumo diário (meia-noite UTC)
    # ------------------------------------------------------------------

    async def send_daily_summary(self, tracker=None) -> None:
        await send_daily_summary(self, tracker)

    # ------------------------------------------------------------------
    # Alerta genérico de sistema
    # ------------------------------------------------------------------

    async def send_system_alert(self, text: str) -> None:
        if not self._token or not self._chat_id:
            return
        await _send_telegram(self._token, self._chat_id, text)
