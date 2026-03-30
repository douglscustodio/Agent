"""
ai_analyst.py — Camada de inteligência Claude AI

Pipeline:
  scanner → scoring → ranking (top 5) → AI → alerta final

A IA recebe apenas os candidatos que já passaram pelo filtro quantitativo.
Ela não substitui o sistema — ela explica, valida e enriquece o sinal.

Responsabilidades:
  1. Validar o setup com linguagem natural em PT-BR
  2. Identificar riscos que regras fixas não capturam
  3. Gerar explicação humanizada para o Telegram
  4. Opcional: filtrar sinais com baixa confiança contextual

Custo estimado:
  3 candidatos × a cada 5 min = ~864 chamadas/dia
  Modelo: claude-haiku-4-5 (rápido + barato para este caso)
  Fallback: se API falhar, sinal segue sem análise de IA

Chave de ambiente: ANTHROPIC_API_KEY
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import aiohttp

from logger import get_logger

log = get_logger("ai_analyst")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
AI_MODEL          = "claude-haiku-4-5-20251001"   # rápido, barato, suficiente
AI_MAX_TOKENS     = 400
AI_TIMEOUT_S      = 12    # não bloqueia o ciclo de scan por mais que isso
AI_CALL_COOLDOWN  = 60    # segundos mínimos entre chamadas para o mesmo símbolo

_last_call: Dict[str, float] = {}   # symbol → last call unix ts


# ---------------------------------------------------------------------------
# Resultado da análise
# ---------------------------------------------------------------------------

@dataclass
class AIAnalysis:
    symbol:       str
    direction:    str
    approved:     bool         # IA aprova o sinal?
    confidence:   int          # 0–100 confiança contextual
    reason:       str          # motivo principal (PT-BR)
    risk_note:    str          # principal risco (PT-BR)
    context_tags: List[str]    # ["funding_trap", "momentum_real", etc]
    used_ai:      bool = True  # False se análise falhou/foi pulada
    latency_ms:   float = 0.0

    @classmethod
    def fallback(cls, symbol: str, direction: str, reason: str = "") -> "AIAnalysis":
        """Resultado neutro quando a IA não está disponível."""
        return cls(
            symbol=symbol,
            direction=direction,
            approved=True,        # não bloqueia o sinal se IA falhar
            confidence=50,
            reason=reason or "Análise de IA indisponível — sinal quantitativo aprovado",
            risk_note="Verifique o contexto de mercado manualmente",
            context_tags=[],
            used_ai=False,
        )


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(data: dict) -> str:
    """
    Constrói o prompt estruturado para o modelo.
    Dados passados: score, componentes, regime, funding, notícias, macro.
    """
    symbol    = data.get("symbol", "?")
    direction = data.get("direction", "LONG")
    score     = data.get("score", 0)
    regime    = data.get("btc_regime", "UNKNOWN")
    funding   = data.get("funding_rate_8h", 0)
    oi_change = data.get("oi_change_pct", 0)
    rsi       = data.get("rsi", 50)
    news      = data.get("news_headline", "sem notícias recentes")
    macro     = data.get("macro_bias", "NEUTRO")
    ev        = data.get("ev_score", 50)
    crowded   = data.get("is_crowded", False)
    dominant  = data.get("dominant_component", "")
    vol_label = data.get("volume_label", "QUIET")
    sector    = data.get("sector", "")

    direction_pt = "COMPRA (LONG)" if direction == "LONG" else "VENDA (SHORT)"
    crowded_note = "\n⚠️ ATENÇÃO: Setup identificado como 'crowded trade' (muito posicionamento na mesma direção)" if crowded else ""

    return f"""Você é um analista sênior de crypto. Analise este setup de trade e responda APENAS com JSON válido, sem texto adicional.

SETUP:
- Ativo: {symbol}/USDT — Direção: {direction_pt}
- Score quantitativo: {score:.0f}/100
- Componente dominante: {dominant}
- Regime BTC: {regime}
- RSI: {rsi:.0f}
- Funding rate (8h): {funding*100:.4f}%
- Variação de OI: {oi_change:+.1f}%
- Volume do candle: {vol_label}
- Valor esperado (EV): {ev:.0f}/100
- Setor: {sector}
- Notícia recente: {news[:120]}
- Contexto macro: {macro}{crowded_note}

Responda SOMENTE com este JSON (sem markdown, sem ```):
{{
  "approve": true,
  "confidence": 72,
  "reason": "explicação concisa em PT-BR do motivo principal do sinal",
  "risk_note": "principal risco ou armadilha para este setup em PT-BR",
  "context_tags": ["tag1", "tag2"]
}}

Tags possíveis: momentum_real, funding_trap, oi_divergencia, crowded_long, volume_confirmado, squeeze_risco, tendencia_forte, mean_reversion, breakout, narrativa_quente, macro_favoravel, macro_desfavoravel

Regras:
- approve=false se houver sinal claro de armadilha (funding trap + preço parado, crowded + sem volume)
- confidence entre 40-90 (nunca 0 ou 100)
- reason: máximo 2 frases, direto ao ponto, em português
- risk_note: máximo 1 frase, em português"""


# ---------------------------------------------------------------------------
# Chamada à API Anthropic
# ---------------------------------------------------------------------------

async def _call_anthropic(prompt: str, api_key: str) -> Optional[dict]:
    """
    Chama a API Anthropic e retorna o JSON parseado.
    Retorna None em caso de erro (falha silenciosa — sinal segue sem IA).
    """
    headers = {
        "Content-Type":         "application/json",
        "x-api-key":            api_key,
        "anthropic-version":    "2023-06-01",
    }
    body = {
        "model":      AI_MODEL,
        "max_tokens": AI_MAX_TOKENS,
        "messages":   [{"role": "user", "content": prompt}],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ANTHROPIC_API_URL,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=AI_TIMEOUT_S),
            ) as resp:
                if resp.status != 200:
                    body_text = await resp.text()
                    log.warning("PERFORMANCE_LOGGED",
                                f"Anthropic API erro {resp.status}: {body_text[:200]}")
                    return None

                data = await resp.json()
                raw  = data["content"][0]["text"].strip()

                # Remove markdown se o modelo insistir
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                    raw = raw.strip()

                return json.loads(raw)

    except json.JSONDecodeError as exc:
        log.warning("PERFORMANCE_LOGGED", f"AI resposta não é JSON válido: {exc}")
    except Exception as exc:
        log.warning("PERFORMANCE_LOGGED", f"Anthropic API falhou: {exc}")

    return None


# ---------------------------------------------------------------------------
# Analisador principal
# ---------------------------------------------------------------------------

class AIAnalyst:
    """
    Camada de análise contextual via Claude.
    Instanciada uma vez em main.py e injetada no job_scan_cycle.
    """

    def __init__(self) -> None:
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._enabled = bool(self._api_key)
        if self._enabled:
            log.info("SYSTEM_READY", f"AI Analyst pronto — modelo: {AI_MODEL}")
        else:
            log.warning("PERFORMANCE_LOGGED",
                        "ANTHROPIC_API_KEY não configurada — AI Analyst desabilitado")

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def analyze(
        self,
        symbol:    str,
        direction: str,
        score:     float,
        components: dict,
        regime:    str,
        funding_8h: float,
        oi_score:  float,
        ev_score:  float,
        is_crowded: bool,
        dominant_component: str,
        vol_label:  str,
        sector:     str,
        news_headline: str = "",
        macro_bias:    str = "NEUTRO",
    ) -> AIAnalysis:
        """
        Analisa um candidato de trade. Retorna AIAnalysis.
        Nunca lança exceção — falha silenciosamente com fallback.
        """
        if not self._enabled:
            return AIAnalysis.fallback(symbol, direction, "ANTHROPIC_API_KEY não configurada")

        # Cooldown por símbolo — não chama API no mesmo símbolo dentro de 60s
        last = _last_call.get(symbol, 0)
        if time.time() - last < AI_CALL_COOLDOWN:
            return AIAnalysis.fallback(symbol, direction, "Análise recente — usando cache")

        t0 = time.monotonic()

        # Estimar RSI a partir do score do componente rsi_quality
        rsi_score = components.get("rsi_quality", 50)
        rsi_est   = 55 if rsi_score >= 80 else (65 if rsi_score >= 60 else (45 if rsi_score >= 40 else 30))

        data = {
            "symbol":             symbol,
            "direction":          direction,
            "score":              score,
            "btc_regime":         regime,
            "funding_rate_8h":    funding_8h,
            "oi_change_pct":      (oi_score - 50) / 2,   # normalizar para ±25%
            "rsi":                rsi_est,
            "news_headline":      news_headline or "sem notícias recentes",
            "macro_bias":         macro_bias,
            "ev_score":           ev_score,
            "is_crowded":         is_crowded,
            "dominant_component": dominant_component,
            "volume_label":       vol_label,
            "sector":             sector,
        }

        prompt = _build_prompt(data)
        result = await _call_anthropic(prompt, self._api_key)

        latency = round((time.monotonic() - t0) * 1000, 1)
        _last_call[symbol] = time.time()

        if result is None:
            log.warning("PERFORMANCE_LOGGED", f"AI análise falhou para {symbol} — fallback")
            return AIAnalysis.fallback(symbol, direction)

        approved   = bool(result.get("approve", True))
        confidence = max(40, min(90, int(result.get("confidence", 60))))
        reason     = result.get("reason", "Análise indisponível")[:200]
        risk_note  = result.get("risk_note", "Gerencie o risco com stop loss")[:150]
        tags       = result.get("context_tags", [])[:5]

        log.info(
            "PERFORMANCE_LOGGED",
            f"AI análise {symbol} {direction}: aprovado={approved} "
            f"confiança={confidence} latência={latency}ms",
            symbol=symbol, direction=direction, score=confidence, latency_ms=latency,
        )

        return AIAnalysis(
            symbol=symbol,
            direction=direction,
            approved=approved,
            confidence=confidence,
            reason=reason,
            risk_note=risk_note,
            context_tags=tags,
            used_ai=True,
            latency_ms=latency,
        )

    async def analyze_batch(
        self,
        candidates: list,           # lista de RankedSignal
        meta_map:   dict = None,    # symbol → AssetMeta (para funding)
        news_map:   dict = None,    # symbol → NewsContext
        macro_bias: str  = "NEUTRO",
        crowd_map:  dict = None,    # symbol → CrowdedTradeResult
        vol_map:    dict = None,    # symbol → VolumeConfirmResult
        sector_map: dict = None,    # symbol → sector label
    ) -> Dict[str, AIAnalysis]:
        """
        Analisa um batch de candidatos concorrentemente.
        Retorna dict[symbol → AIAnalysis].
        """
        import asyncio

        tasks = {}
        for sig in candidates:
            sym       = sig.symbol
            meta      = (meta_map or {}).get(sym)
            news_ctx  = (news_map or {}).get(sym)
            crowd     = (crowd_map or {}).get(sym)
            vol       = (vol_map or {}).get(sym)

            tasks[sym] = self.analyze(
                symbol=sym,
                direction=sig.direction,
                score=sig.score,
                components=sig.components,
                regime=sig.result.regime_used,
                funding_8h=meta.funding_8h if meta else 0.0,
                oi_score=sig.components.get("oi_acceleration", 50),
                ev_score=sig.ev_score,
                is_crowded=crowd.is_crowded if crowd else False,
                dominant_component=sig.result.dominant_component,
                vol_label=vol.label if vol else "QUIET",
                sector=(sector_map or {}).get(sym, ""),
                news_headline=news_ctx.top_headline if news_ctx and news_ctx.top_headline != "No recent news" else "",
                macro_bias=macro_bias,
            )

        results_list = await asyncio.gather(*tasks.values(), return_exceptions=False)
        return dict(zip(tasks.keys(), results_list))
