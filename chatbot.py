"""
chatbot.py — Chatbot Telegram com IA para Jarvis AI Trading Monitor

Comandos disponíveis:
  /start     - Inicia o bot e mostra mensagem de boas-vindas
  /help      - Lista todos os comandos disponíveis
  /status    - Status do sistema (conexões, dados, últimas atualizações)
  /sinais    - Lista sinais gerados no último scan
  /news      - Últimas notícias
  /macro     - Contexto macroeconômico atual
  /performance - Performance recente do sistema
  /scan      - Força um novo scan (se disponível)
  /ai        - Pergunta em linguagem natural sobre o mercado

O bot usa Groq AI para respostas inteligentes em português.
"""

import os
import time
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any

import aiohttp

from logger import get_logger
from data_quality import get_current_quality

log = get_logger("chatbot")

TELEGRAM_API = "https://api.telegram.org/bot{token}"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
AI_MODEL = "llama-3.1-8b-instant"
AI_TIMEOUT_S = 15
AI_MAX_TOKENS = 500


class JarvisChatbot:
    def __init__(self, token: str):
        self._token = token
        self._api_base = TELEGRAM_API.format(token=token)
        self._last_update_id = 0
        self._system_refs: Dict[str, Any] = {}
        
        self._commands = {
            "/start": self._cmd_start,
            "/help": self._cmd_help,
            "/status": self._cmd_status,
            "/sinais": self._cmd_sinais,
            "/news": self._cmd_news,
            "/macro": self._cmd_macro,
            "/performance": self._cmd_performance,
            "/scan": self._cmd_scan,
            "/ai": self._cmd_ai,
        }

    def set_system_refs(self, **refs) -> None:
        self._system_refs.update(refs)

    async def _send_message(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
        url = f"{self._api_base}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return True
                    log.error("CHATBOT_ERROR", f"send failed: {resp.status}")
                    return False
        except Exception as exc:
            log.error("CHATBOT_ERROR", f"send failed: {exc}")
            return False

    async def poll(self) -> Optional[str]:
        url = f"{self._api_base}/getUpdates"
        params = {
            "offset": self._last_update_id + 1,
            "timeout": 30,
            "allowed_updates": "message",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    updates = data.get("result", [])
                    
                    for update in updates:
                        self._last_update_id = max(self._last_update_id, update["update_id"])
                        message = update.get("message", {})
                        if message.get("text"):
                            return message["text"]
        except Exception as exc:
            log.warning("CHATBOT_POLL", f"poll error: {exc}")
        return None

    async def handle_message(self, text: str, chat_id: str) -> str:
        if not text:
            return ""
        
        text = text.strip()
        
        if text.startswith("/"):
            parts = text.split(" ", 1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            
            if cmd in self._commands:
                try:
                    return await self._commands[cmd](chat_id, args)
                except Exception as exc:
                    log.error("CHATBOT_ERROR", f"command error {cmd}: {exc}")
                    return f"❌ Erro ao executar comando: {exc}"
            else:
                return f"❓ Comando '{cmd}' não reconhecido. Digite /help para ver comandos disponíveis."
        
        if len(text) > 3:
            return await self._cmd_ai(chat_id, text)
        
        return "💬 Digite um comando (/help) ou faça uma pergunta sobre o mercado."

    async def _cmd_start(self, chat_id: str, args: str) -> str:
        quality = get_current_quality()
        lines = [
            "🤖 *Jarvis AI Trading Monitor*",
            "",
            "Olá! Sou o assistente de trading do Jarvis.",
            "",
            "*O que eu posso fazer:*",
            "• Analisar o mercado de crypto em tempo real",
            "• Gerar sinais de trade com IA",
            "• Monitorar notícias e contexto macroeconômico",
            "• Acompanhar a performance do sistema",
            "",
            "*Comandos disponíveis:*",
            "/help    - Ver todos os comandos",
            "/status  - Status do sistema",
            "/sinais  - Últimos sinais gerados",
            "/news    - Últimas notícias",
            "/macro   - Contexto macro atual",
            "/ai      - Pergunte qualquer coisa",
            "",
            "🏆 *Qualidade dos dados:* " + quality.quality_label,
            "",
            "_Jarvis AI Trading Monitor_",
        ]
        return "\n".join(lines)

    async def _cmd_help(self, chat_id: str, args: str) -> str:
        lines = [
            "📚 *Manual de Comandos*",
            "",
            "*📊 Informações do Mercado:*",
            "/sinais  - Lista sinais de trade do último scan",
            "/news    - Últimas notícias do mercado",
            "/macro   - Contexto macroeconômico (Fed, CPI, etc)",
            "",
            "*📈 Status do Sistema:*",
            "/status  - Conexões, dados, kesehatan do sistema",
            "/performance - Taxa de acerto, PnL recente",
            "",
            "*🤖 Inteligência Artificial:*",
            "/ai [pergunta] - Faça qualquer pergunta sobre crypto",
            "Ex: /ai o BTC vai subir essa semana?",
            "",
            "*⚡ Ações:*",
            "/scan    - Força um novo scan (quando disponível)",
            "",
            "_Digite /ai [sua pergunta] para conversar_",
        ]
        return "\n".join(lines)

    async def _cmd_status(self, chat_id: str, args: str) -> str:
        quality = get_current_quality()
        scanner = self._system_refs.get("scanner")
        news_engine = self._system_refs.get("news_engine")
        macro_engine = self._system_refs.get("macro_engine")
        
        lines = [
            "📊 *Status do Sistema*",
            f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_",
            "",
            "*🔗 Conexões:*",
            f"• Hyperliquid: {'✅' if quality.hyperliquid_available else '❌'}",
            f"• WebSocket: {'✅' if quality.ws_connected else '❌'}",
            f"• News API: {'✅' if quality.news_api_available else '❌'}",
            f"• Macro API: {'✅' if quality.macro_api_available else '❌'}",
            f"• Groq AI: {'✅' if quality.ai_available else '❌'}",
            "",
            "*📡 Dados:*",
            f"• Qualidade: {quality.quality_label}",
            f"• Score: {quality.quality_score:.0%}",
            f"• Market data: {quality.market_age_minutes:.0f}min atrás",
            f"• Notícias: {quality.news_age_minutes:.0f}min atrás",
            f"• Símbolos: {quality.symbols_with_data}/{quality.symbols_requested}",
            "",
        ]
        
        if quality.warnings:
            lines.append("*⚠️ Avisos:*")
            for w in quality.warnings[:3]:
                lines.append(w)
            lines.append("")
        
        lines.append("_Jarvis AI Trading Monitor_")
        return "\n".join(lines)

    async def _cmd_sinais(self, chat_id: str, args: str) -> str:
        ranking = self._system_refs.get("last_ranking")
        
        if not ranking or not ranking.top:
            return "📭 *Sinais*\\n\\nNenhum sinal disponível no momento. O sistema ainda não completou um scan ou não encontrou oportunidades."
        
        lines = [
            "🚨 *Sinais de Trade*",
            f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_",
            "",
        ]
        
        for i, sig in enumerate(ranking.top[:3], 1):
            emoji = "📈" if sig.direction == "LONG" else "📉"
            lines.append(f"{i}. {emoji} *{sig.symbol}/USDT* — {sig.direction}")
            lines.append(f"   Score: `{sig.score:.0f}/100`")
            lines.append(f"   Band: {sig.band}")
            
            comp = sig.components
            if "relative_strength" in comp:
                lines.append(f"   RS: `{comp['relative_strength']:.0f}`")
            if "adx_regime" in comp:
                lines.append(f"   ADX: `{comp['adx_regime']:.0f}`")
            lines.append("")
        
        lines.append("_Jarvis AI Trading Monitor_")
        return "\n".join(lines)

    async def _cmd_news(self, chat_id: str, args: str) -> str:
        news_engine = self._system_refs.get("news_engine")
        
        if not news_engine:
            return "📰 *Notícias*\\n\\nSistema de notícias não disponível."
        
        try:
            articles = news_engine._cache[:5] if hasattr(news_engine, "_cache") and news_engine._cache else []
            
            if not articles:
                return "📰 *Notícias*\\n\\nNenhuma notícia disponível. O sistema pode estar buscando dados..."
            
            lines = [
                "📰 *Últimas Notícias*",
                f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_",
                "",
            ]
            
            for art in articles:
                emoji = "🟢" if art.sentiment == "positive" else ("🔴" if art.sentiment == "negative" else "⚪")
                age_min = (time.time() - art.published_at) / 60
                title_short = art.title[:80]
                lines.append(f"{emoji} *{title_short}*")
                lines.append(f"   {age_min:.0f}min | Impacto: {art.impact_score:.0f}")
                lines.append("")
            
            lines.append("_Jarvis AI Trading Monitor_")
            return "\n".join(lines)
        except Exception as exc:
            log.error("CHATBOT_ERROR", f"news error: {exc}")
            return f"📰 *Notícias*\\n\\nErro ao buscar notícias: {exc}"

    async def _cmd_macro(self, chat_id: str, args: str) -> str:
        macro_engine = self._system_refs.get("macro_engine")
        
        if not macro_engine:
            return "🌍 *Macro*\\n\\nSistema macro não disponível."
        
        try:
            snap = macro_engine.get_snapshot()
            
            if not snap:
                return "🌍 *Contexto Macroeconômico*\\n\\nDados macro ainda carregando..."
            
            lines = [
                "🌍 *Contexto Macroeconômico*",
                f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_",
                "",
                f"*Risco: {snap.risk_label}*",
                f"*Viés Crypto: {snap.crypto_bias}*",
                "",
            ]
            
            for attr, label in [
                ("sp500", "S&P 500"),
                ("nasdaq", "Nasdaq"),
                ("dxy", "Dólar (DXY)"),
                ("vix", "VIX"),
            ]:
                md = getattr(snap, attr, None)
                if md:
                    arrow = "⬆️" if md.trend == "UP" else ("⬇️" if md.trend == "DOWN" else "➡️")
                    lines.append(f"• {label}: `{md.price:,.2f}` {arrow} ({md.change_pct:+.2f}%)")
            
            if snap.explanation:
                lines.append("")
                lines.append("*💡 Análise:*")
                for exp in snap.explanation[:3]:
                    lines.append(f"• {exp}")
            
            lines.append("")
            lines.append("_Jarvis AI Trading Monitor_")
            return "\n".join(lines)
        except Exception as exc:
            log.error("CHATBOT_ERROR", f"macro error: {exc}")
            return f"🌍 *Macro*\\n\\nErro ao buscar dados macro: {exc}"

    async def _cmd_performance(self, chat_id: str, args: str) -> str:
        tracker = self._system_refs.get("tracker")
        
        if not tracker:
            return "📈 *Performance*\\n\\nSistema de tracking não disponível."
        
        try:
            stats = await tracker.get_recent_stats(days=7)
            
            lines = [
                "📈 *Performance (7 dias)*",
                f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_",
                "",
            ]
            
            win_rate = stats.get("win_rate", 0)
            total = stats.get("total", 0)
            tp1 = stats.get("tp1", 0)
            sl = stats.get("sl", 0)
            avg_pnl = stats.get("avg_pnl", 0)
            
            if total == 0:
                lines.append("Nenhum sinal registrado ainda.")
            else:
                lines.append(f"*Total de sinais: {total}*")
                lines.append(f"• ✅ Acertos (TP1): {tp1}")
                lines.append(f"• ❌ Stop Loss: {sl}")
                lines.append(f"• Taxa de acerto: `{win_rate:.1f}%`")
                lines.append(f"• PnL médio: `{avg_pnl:+.2f}%`")
            
            lines.append("")
            lines.append("_Jarvis AI Trading Monitor_")
            return "\n".join(lines)
        except Exception as exc:
            log.error("CHATBOT_ERROR", f"performance error: {exc}")
            return f"📈 *Performance*\\n\\nErro ao buscar performance: {exc}"

    async def _cmd_scan(self, chat_id: str, args: str) -> str:
        scanner = self._system_refs.get("scanner")
        
        if not scanner:
            return "⚡ *Scan*\\n\\nScanner não disponível."
        
        try:
            ranking = await scanner.run_scan_cycle()
            
            if ranking and ranking.top:
                count = len(ranking.top)
                top_sig = ranking.top[0]
                emoji = "📈" if top_sig.direction == "LONG" else "📉"
                return f"✅ *Scan Completo*\\n\\n{count} sinal(ais) encontrado(s):\\n{emoji} {top_sig.symbol}/USDT ({top_sig.direction}) com score `{top_sig.score:.0f}`"
            else:
                return "✅ *Scan Completo*\\n\\nNenhum sinal válido encontrado neste scan."
        except Exception as exc:
            log.error("CHATBOT_ERROR", f"scan error: {exc}")
            return f"⚡ *Scan*\\n\\nErro ao executar scan: {exc}"

    async def _cmd_ai(self, chat_id: str, args: str) -> str:
        api_key = os.getenv("GROQ_API_KEY", "")
        
        if not api_key:
            return "🤖 *IA não disponível*\\n\\nGROQ_API_KEY não configurada. Configure a variável de ambiente para usar a IA."
        
        if not args:
            return "🤖 *Pergunte-me qualquer coisa*\\n\\nDigite sua pergunta após o comando:\\n/ai BTC vai subir essa semana?\\n/ai o que é funding rate?\\n/ai analise o mercado de altcoins"
        
        try:
            response = await self._call_groq(args, api_key)
            if response:
                return f"🤖 *Resposta IA:*\\n\\n{response}\\n\\n_Resposta gerada por IA - use como referência, não como conselho financeiro_"
            else:
                return "🤖 *IA indisponível no momento.* Tente novamente mais tarde."
        except Exception as exc:
            log.error("CHATBOT_ERROR", f"AI error: {exc}")
            return f"🤖 *Erro na IA:* {exc}"

    async def _call_groq(self, question: str, api_key: str) -> Optional[str]:
        ranking = self._system_refs.get("last_ranking")
        news_engine = self._system_refs.get("news_engine")
        macro_engine = self._system_refs.get("macro_engine")
        
        context_parts = []
        
        if ranking and ranking.top:
            signals = [f"{s.symbol}/USDT {s.direction} (score {s.score:.0f})" for s in ranking.top[:3]]
            context_parts.append(f"SINAIS ATUAIS: {', '.join(signals)}")
        
        if news_engine and hasattr(news_engine, "_cache") and news_engine._cache:
            top_news = news_engine._cache[0].title[:100]
            context_parts.append(f"ÚLTIMA NOTÍCIA: {top_news}")
        
        if macro_engine:
            snap = macro_engine.get_snapshot()
            if snap:
                context_parts.append(f"MACRO: risco={snap.risk_score} bias={snap.crypto_bias}")
        
        context = "\\n".join(context_parts) if context_parts else "Sistema sem dados disponíveis no momento."
        
        prompt = f"""Você é o assistente de trading do Jarvis AI Monitor. Responda em português brasileiro, de forma clara e útil.

CONTEXTO DO SISTEMA:
{context}

PERGUNTA DO USUÁRIO:
{question}

Responda de forma concisa (máximo 3-4 frases), focada e útil. Se não souber algo, diga claramente. Nunca dê sinais de compra/venda explícitos."""

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": AI_MODEL,
            "max_tokens": AI_MAX_TOKENS,
            "temperature": 0.5,
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
                        return None
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
        except Exception:
            return None
