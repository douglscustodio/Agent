"""
ai_analyst.py — Camada de inteligência via Groq API
Pipeline: scanner → scoring → ranking (top 5) → AI → alerta final

A IA valida candidatos que já passaram pelo filtro quantitativo.
Nunca substitui o sistema — explica, valida e enriquece o sinal.

Responsabilidades:
  1. Validar o setup com linguagem natural em PT-BR
  2. Identificar riscos que regras fixas não capturam
  3. Gerar explicação humanizada para o Telegram
  4. Filtrar sinais com baixa confiança contextual

Modelo: llama-3.1-8b-instant (rápido, barato, suficiente)
Fallback: se API falhar, sinal segue sem análise de IA
Chave: GROQ_API_KEY
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import aiohttp

from logger import get_logger
from data_quality import update_quality

log = get_logger("ai_analyst")

GROQ_API_URL  = "https://api.groq.com/openai/v1/chat/completions"
AI_MODEL      = "llama-3.1-8b-instant"    # rápido + barato
AI_MAX_TOKENS = 400
AI_TIMEOUT_S  = 10     # não bloqueia o ciclo de scan
AI_CALL_COOLDOWN = 60  # segundos entre chamadas para o mesmo símbolo

_last_call: Dict[str, float] = {}


# ---------------------------------------------------------------------------
# Resultado da análise
# ---------------------------------------------------------------------------

@dataclass
class AIAnalysis:
    symbol:       str
    direction:    str
    approved:     bool
    confidence:   int       # 40–90
    reason:       str       # PT-BR, 2 frases
    risk_note:    str       # PT-BR, 1 frase
    context_tags: List[str]
    used_ai:      bool = True
    latency_ms:   float = 0.0

    @classmethod
    def fallback(cls, symbol: str, direction: str, reason: str = "") -> "AIAnalysis":
        return cls(
            symbol=symbol,
            direction=direction,
            approved=False,
            confidence=40,
            reason=reason or "Análise de IA indisponível — sinal requer revisão manual",
            risk_note="Sem validação de IA - gerencie o risco com stop loss",
            context_tags=["sem_ia"],
            used_ai=False,
        )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _build_prompt(data: dict) -> str:
    symbol     = data.get("symbol", "?")
    direction  = data.get("direction", "LONG")
    score      = data.get("score", 0)
    regime     = data.get("btc_regime", "UNKNOWN")
    funding    = data.get("funding_rate_8h", 0)
    oi_change  = data.get("oi_change_pct", 0)
    rsi        = data.get("rsi", 50)
    news       = data.get("news_headline", "sem notícias recentes")
    macro      = data.get("macro_bias", "NEUTRO")
    ev         = data.get("ev_score", 50)
    crowded    = data.get("is_crowded", False)
    dominant   = data.get("dominant_component", "")
    vol_label  = data.get("volume_label", "QUIET")
    sector     = data.get("sector", "")

    direction_pt = "COMPRA (LONG)" if direction == "LONG" else "VENDA (SHORT)"
    crowded_note = "\n[WARN] ATENÇÃO: Setup 'crowded trade' detectado" if crowded else ""

    return f"""Você é um analista sênior de crypto. Analise este setup e responda APENAS com JSON válido, sem texto adicional.

SETUP:
- Ativo: {symbol}/USDT — Direção: {direction_pt}
- Score quantitativo: {score:.0f}/100
- Componente dominante: {dominant}
- Regime BTC: {regime}
- RSI estimado: {rsi:.0f}
- Funding rate (8h): {funding*100:.4f}%
- Variação de OI: {oi_change:+.1f}%
- Volume: {vol_label}
- Valor esperado (EV): {ev:.0f}/100
- Setor: {sector}
- Notícia: {news[:120]}
- Macro: {macro}{crowded_note}

Responda SOMENTE com este JSON (sem markdown):
{{
  "approve": true,
  "confidence": 72,
  "reason": "motivo principal em PT-BR, máximo 2 frases",
  "risk_note": "principal risco em PT-BR, máximo 1 frase",
  "context_tags": ["tag1", "tag2"]
}}

Tags: momentum_real, funding_trap, oi_divergencia, crowded_long, volume_confirmado, squeeze_risco, tendencia_forte, mean_reversion, breakout, narrativa_quente, macro_favoravel, macro_desfavoravel

Regras:
- approve=false se funding_trap + preço parado, ou crowded + sem volume
- confidence entre 40-90
- reason: direto, em português
- risk_note: 1 frase, em português"""


# ---------------------------------------------------------------------------
# Chamada Groq API
# ---------------------------------------------------------------------------

async def _call_groq(prompt: str, api_key: str) -> Optional[dict]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    body = {
        "model":       AI_MODEL,
        "max_tokens":  AI_MAX_TOKENS,
        "temperature": 0.3,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GROQ_API_URL,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=AI_TIMEOUT_S),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    log.warning("AI_API_ERROR", f"Groq erro {resp.status}: {text[:200]}")
                    return None
                data = await resp.json()
                raw  = data["choices"][0]["message"]["content"].strip()
                # Strip markdown if model adds it
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                    raw = raw.strip()
                return json.loads(raw)

    except json.JSONDecodeError as exc:
        log.warning("AI_PARSE_ERROR", f"resposta não é JSON: {exc}")
    except Exception as exc:
        log.warning("AI_API_ERROR", f"Groq falhou: {exc}")
    return None


# ---------------------------------------------------------------------------
# AIAnalyst
# ---------------------------------------------------------------------------

class AIAnalyst:
    def __init__(self) -> None:
        self._api_key = os.getenv("GROQ_API_KEY", "")
        self._enabled = bool(self._api_key)
        update_quality(ai_available=self._enabled)
        if self._enabled:
            log.info("SYSTEM_READY", f"AI Analyst pronto — Groq {AI_MODEL}")
        else:
            log.warning("AI_DISABLED", "GROQ_API_KEY não configurada — AI Analyst desabilitado")

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def analyze(
        self,
        symbol:             str,
        direction:          str,
        score:              float,
        components:         dict,
        regime:             str,
        funding_8h:         float,
        oi_score:           float,
        ev_score:           float,
        is_crowded:         bool,
        dominant_component: str,
        vol_label:          str,
        sector:             str,
        news_headline:      str = "",
        macro_bias:         str = "NEUTRO",
    ) -> AIAnalysis:
        if not self._enabled:
            return AIAnalysis.fallback(symbol, direction, "GROQ_API_KEY não configurada")

        # Cooldown por símbolo
        if time.time() - _last_call.get(symbol, 0) < AI_CALL_COOLDOWN:
            return AIAnalysis.fallback(symbol, direction, "Análise recente — reutilizando")

        t0 = time.monotonic()

        # Estimar RSI a partir do componente
        rsi_score = components.get("rsi_quality", 50)
        rsi_est   = 55 if rsi_score >= 80 else (65 if rsi_score >= 60 else (45 if rsi_score >= 40 else 30))

        data = {
            "symbol":             symbol,
            "direction":          direction,
            "score":              score,
            "btc_regime":         regime,
            "funding_rate_8h":    funding_8h,
            "oi_change_pct":      (oi_score - 50) / 2,
            "rsi":                rsi_est,
            "news_headline":      news_headline or "sem notícias recentes",
            "macro_bias":         macro_bias,
            "ev_score":           ev_score,
            "is_crowded":         is_crowded,
            "dominant_component": dominant_component,
            "volume_label":       vol_label,
            "sector":             sector,
        }

        result  = await _call_groq(_build_prompt(data), self._api_key)
        latency = round((time.monotonic() - t0) * 1000, 1)
        _last_call[symbol] = time.time()

        if result is None:
            log.warning("AI_ANALYSIS_FAIL", f"Groq falhou para {symbol} — fallback")
            return AIAnalysis.fallback(symbol, direction)

        approved   = bool(result.get("approve", True))
        confidence = max(40, min(90, int(result.get("confidence", 60))))
        reason     = result.get("reason", "")[:200]
        risk_note  = result.get("risk_note", "Gerencie com stop loss")[:150]
        tags       = result.get("context_tags", [])[:5]

        log.info(
            "AI_ANALYSIS_DONE",
            f"AI {symbol} {direction}: aprovado={approved} confiança={confidence} {latency}ms",
            symbol=symbol, direction=direction, score=confidence, latency_ms=latency,
        )
        return AIAnalysis(
            symbol=symbol, direction=direction,
            approved=approved, confidence=confidence,
            reason=reason, risk_note=risk_note,
            context_tags=tags, used_ai=True, latency_ms=latency,
        )

    async def analyze_batch(
        self,
        candidates:  list,
        meta_map:    dict = None,
        news_map:    dict = None,
        macro_bias:  str  = "NEUTRO",
        crowd_map:   dict = None,
        vol_map:     dict = None,
        sector_map:  dict = None,
    ) -> Dict[str, AIAnalysis]:
        import asyncio
        tasks = {}
        for sig in candidates:
            sym      = sig.symbol
            meta     = (meta_map or {}).get(sym)
            news_ctx = (news_map or {}).get(sym)
            crowd    = (crowd_map or {}).get(sym)
            vol      = (vol_map or {}).get(sym)
            tasks[sym] = self.analyze(
                symbol=sym,
                direction=sig.direction,
                score=sig.score,
                components=sig.components,
                regime=getattr(getattr(sig, 'result', None), 'regime_used', "UNKNOWN"),
                funding_8h=meta.funding_8h if meta else 0.0,
                oi_score=sig.components.get("oi_acceleration", 50),
                ev_score=getattr(sig, 'ev_score', sig.score),
                is_crowded=getattr(crowd, 'is_crowded', False),
                dominant_component=getattr(getattr(sig, 'result', None), 'dominant_component', ""),
                vol_label=getattr(vol, 'label', "QUIET"),
                sector=(sector_map or {}).get(sym, ""),
                news_headline=news_ctx.top_headline if news_ctx and news_ctx.top_headline != "No recent news" else "",
                macro_bias=macro_bias,
            )
        results = await asyncio.gather(*tasks.values(), return_exceptions=False)
        return dict(zip(tasks.keys(), results))
