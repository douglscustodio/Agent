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
from notifier import _translate_news_title, _translate_macro_title

log = get_logger("chatbot")

TELEGRAM_API = "https://api.telegram.org/bot{token}"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
AI_MODEL = "llama-3.1-8b-instant"
AI_MODEL_VISION = "llama-3.2-11b-vision-preview"
AI_TIMEOUT_S = 15
AI_MAX_TOKENS = 500
IMAGE_ANALYSIS_PROMPT = """Você é um analista de trading profissional. Analise esta imagem (pode ser gráfico, print de trading, dashboard, etc) e forneça:
1. O que você vê na imagem
2. Análise técnica (se aplicável)
3. Insights ou observações importantes
4. Sugestões ou alertas

Responda em português de forma clara e profissional."""


class JarvisChatbot:
    def __init__(self, token: str):
        self._token = token
        self._api_base = TELEGRAM_API.format(token=token)
        self._last_update_id = 0
        self._system_refs: Dict[str, Any] = {}
        self._user_first_contact: Dict[str, bool] = {}
        self._alert_chat_id: Optional[str] = None
        self._system_refs: Dict[str, any] = {}
        self._user_first_contact: Dict[str, bool] = {}
        self._signal_alerted_today: Dict[str, float] = {}  # key -> timestamp
        self._last_scores: Dict[str, float] = {}  # key -> score
        self._session: Optional[aiohttp.ClientSession] = None  # Shared session for performance
        
        self._commands = {
            "/start": self._cmd_start,
            "/help": self._cmd_help,
            "/status": self._cmd_status,
            "/sinais": self._cmd_sinais,
            "/news": self._cmd_news,
            "/macro": self._cmd_macro,
            "/performance": self._cmd_performance,
            "/scan": self._cmd_scan,
            "/risk": self._cmd_risk,
            "/ai": self._cmd_ai,
            "/learn": self._cmd_learn,
            "/debug": self._cmd_debug,
        }
        
        self._quick_tips = [
            "💡 Digite /sinais para ver oportunidades de trade",
            "💡 Use /ai + sua pergunta para conversar comigo",
            "💡 /news mostra as últimas notícias do mercado",
            "💡 /status verifica se tudo está funcionando",
            "💡 /risk mostra status de proteção de risco",
            "💡 /learn mostra o que eu aprendi dos seus trades",
        ]

    def set_system_refs(self, **refs) -> None:
        self._system_refs.update(refs)

    async def _send_message(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
        if not chat_id:
            log.error("CHATBOT_ERROR", "send_message called with empty chat_id")
            return False
        
        url = f"{self._api_base}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    log.info("CHATBOT_SENT", f"message sent to {chat_id}: {text[:50]}...")
                    return True
                body = await resp.text()
                log.error("CHATBOT_ERROR", f"send failed: {resp.status} - {body[:100]}")
                return False
        except Exception as exc:
            log.error("CHATBOT_ERROR", f"send failed: {exc}")
            return False

    async def send_alert(self, text: str) -> bool:
        if not self._alert_chat_id:
            log.warning("CHATBOT_ALERT", "send_alert called but _alert_chat_id is None")
            return False
        log.info("CHATBOT_ALERT", f"sending alert: {text[:50]}...")
        return await self._send_message(self._alert_chat_id, text)

    def set_alert_chat_id(self, chat_id: str) -> None:
        self._alert_chat_id = chat_id
        log.info("CHATBOT_CONFIG", f"alert chat_id set: {chat_id}")

    async def alert_signal(self, symbol: str, direction: str, score: float, reason: str = "") -> None:
        if not self._alert_chat_id:
            log.warning("CHATBOT_ALERT", f"Cannot send signal alert - no chat_id set")
            return
        
        import time
        key = f"{symbol}:{direction}"
        
        # Verificar cooldown de 45 minutos
        if key in self._signal_alerted_today:
            last_sent = self._signal_alerted_today[key]
            elapsed = time.time() - last_sent
            if elapsed < 2700:  # 45 minutos
                # Só permite se score melhorou muito (>10 pontos)
                if self._last_scores.get(key, 0) >= score - 10:
                    log.info("CHATBOT_ALERT", f"signal {key} in cooldown ({elapsed/60:.0f}min), skipping")
                    return
                else:
                    log.info("CHATBOT_ALERT", f"signal {key} score improved {score - self._last_scores.get(key, 0):.0f}pts, allowing")
        
        self._signal_alerted_today[key] = time.time()
        self._last_scores[key] = score
        
        emoji = "📈" if direction == "LONG" else "📉"
        msg = (
            f"{emoji} *OPORTUNIDADE DETECTADA!*\n\n"
            f"{symbol}/USDT — {direction}\n"
            f"Score: `{score:.0f}/100`\n"
        )
        if reason:
            msg += f"\n{reason}\n"
        msg += "\nDigite /sinais para mais detalhes"
        
        log.info("CHATBOT_ALERT", f"Sending signal alert: {symbol} {direction} score={score}")
        await self.send_alert(msg)

    def reset_daily_alerts(self) -> None:
        self._signal_alerted_today.clear()
        self._last_scores.clear()

    async def alert_btc_spike(self, direction: str, pct: float, price: float) -> None:
        emoji = "🚀" if direction == "UP" else "💥"
        msg = (
            f"{emoji} *ALERTA BTC*\n\n"
            f"BTC {direction} `{pct:+.1f}%`\n"
            f"Preço: `${price:,.2f}`\n\n"
            "Cuidado com volatilidade!"
        )
        await self.send_alert(msg)

    async def alert_macro_risk(self, risk_score: float, event: str) -> None:
        import time
        cooldown_key = "macro_risk"
        if hasattr(self, '_last_macro_alert'):
            elapsed = time.time() - self._last_macro_alert
            if elapsed < 7200:  # 2 hour cooldown para não ser intrusivo
                log.info("CHATBOT_MACRO", f"macro alert in cooldown ({elapsed/60:.0f}min), skipping")
                return
        self._last_macro_alert = time.time()
        
        if risk_score >= 70:
            emoji = "⚠️"
        elif risk_score >= 50:
            emoji = "💡"
        else:
            emoji = "✅"
        msg = (
            f"{emoji} *Contexto Macro*\n\n"
            f"Índice de risco: {risk_score:.0f}/100\n"
            f"Evento: {event[:100]}\n\n"
            "Fique atento aos próximos sinais."
        )
        await self.send_alert(msg)

    async def alert_data_issue(self, issue: str) -> None:
        msg = (
            f"⚠️ *AVISO DO SISTEMA*\n\n"
            f"{issue}\n\n"
            "Dados podem estar desatualizados."
        )
        await self.send_alert(msg)

    async def alert_daily_summary(self, stats: dict) -> None:
        total = stats.get("total", 0)
        win_rate = stats.get("win_rate", 0)
        pnl = stats.get("avg_pnl", 0)
        msg = (
            "📊 *RESUMO DIÁRIO*\n\n"
            f"Sinais enviados: {total}\n"
            f"Taxa de acerto: {win_rate:.1f}%\n"
            f"PnL médio: {pnl:+.2f}%\n\n"
            "Keep going! 🚀"
        )
        await self.send_alert(msg)

    async def poll(self) -> Optional[Dict]:
        url = f"{self._api_base}/getUpdates"
        params = {
            "offset": self._last_update_id + 1,
            "timeout": 30,
            "allowed_updates": "message",
        }
        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                if resp.status != 200:
                    log.warning("CHATBOT_POLL", f"poll HTTP error: {resp.status}")
                    return None
                
                data = await resp.json()
                if not data.get("ok"):
                    log.warning("CHATBOT_POLL", f"Telegram API error: {data}")
                    return None
                
                updates = data.get("result", [])
                
                if not updates:
                    self._last_update_id += 1
                    return None
                
                for update in updates:
                    update_id = update.get("update_id", 0)
                    if update_id <= self._last_update_id:
                        continue
                    
                    self._last_update_id = update_id
                    message = update.get("message", {})
                    
                    if not message:
                        continue
                    
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    text = message.get("text", "")
                    photo = message.get("photo")
                    voice = message.get("voice")
                    video = message.get("video")
                    document = message.get("document")
                    
                    if photo:
                        return {"text": "", "chat_id": chat_id, "photo": photo}
                    elif document:
                        return {"text": "", "chat_id": chat_id, "document": document}
                    elif voice or video:
                        return {"text": "", "chat_id": chat_id, "non_text": True}
                    
                    if text:
                        return {"text": text, "chat_id": chat_id}
                
                return None
                    
        except asyncio.TimeoutError:
            return None
        except Exception as exc:
            log.warning("CHATBOT_POLL", f"poll error: {exc}")
            return None

    async def handle_message(self, text: str, chat_id: str, photo: list = None, 
                          document: dict = None, non_text: bool = False) -> str:
        if non_text:
            if photo:
                return await self._handle_photo(chat_id, photo)
            elif document:
                return await self._handle_document(chat_id, document)
            return "📎 Tipo de mensagem não suportado.\n\nDigite /help para ver o que posso fazer!"
        
        if not text:
            return ""
        
        # Detectar URL no texto
        if "http://" in text or "https://" in text:
            return await self._handle_url(chat_id, text)
        
        text = text.strip()
        
        text = text.strip()
        
        is_first = self._user_first_contact.get(chat_id, True)
        self._user_first_contact[chat_id] = False
        
        if text.startswith("/"):
            parts = text.split(" ", 1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            
            if cmd in self._commands:
                try:
                    response = await self._commands[cmd](chat_id, args)
                    if is_first and cmd != "/start":
                        response = self._add_welcome_tip(response)
                    return response
                except Exception as exc:
                    log.error("CHATBOT_ERROR", f"command error {cmd}: {exc}")
                    return f"❌ Erro ao executar comando: {exc}"
            else:
                return f"❓ Comando '{cmd}' não reconhecido. Digite /help para ver comandos disponíveis."
        
        if len(text) > 3:
            return await self._cmd_ai(chat_id, text)
        
        return "💬 Quer ajuda? Digite /help para ver o que posso fazer!"

    def _add_welcome_tip(self, response: str) -> str:
        import random
        tip = random.choice(self._quick_tips)
        return response + "\n\n" + tip
    
    async def _handle_photo(self, chat_id: str, photo: list) -> str:
        """Analisa imagem enviada pelo usuário."""
        log.info("CHATBOT_IMAGE", f"Received photo from {chat_id}")
        
        try:
            # Pegar a foto de maior resolução
            photo_id = photo[-1].get("file_id") if photo else None
            if not photo_id:
                return "📷 Não consegui processar a imagem. Tente novamente."
            
            # Baixar a imagem
            file_url = f"{self._api_base}/getFile?file_id={photo_id}"
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    if resp.status != 200:
                        return "📷 Erro ao baixar imagem."
                    file_data = await resp.json()
            
            file_path = file_data.get("result", {}).get("file_path")
            if not file_path:
                return "📷 Não consegui acessar a imagem."
            
            # Baixar imagem em alta resolução
            download_url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url) as resp:
                    if resp.status != 200:
                        return "📷 Erro ao baixar imagem."
                    image_bytes = await resp.read()
            
            # Enviar para IA analisar
            api_key = os.getenv("GROQ_API_KEY", "")
            if not api_key:
                return "🤖 IA não disponível. Configure GROQ_API_KEY para analisar imagens."
            
            # Analisar com visão
            result = await self._analyze_image(image_bytes, api_key)
            if result:
                return f"📊 *Análise da Imagem*\n\n{result}\n\n_Jarvis AI Trading Monitor_"
            else:
                return "🤖 Erro ao analisar imagem. Tente novamente."
                
        except Exception as exc:
            log.error("CHATBOT_IMAGE_ERROR", f"failed: {exc}")
            return f"📷 Erro ao processar imagem: {exc}"
    
    async def _handle_document(self, chat_id: str, document: dict) -> str:
        """Processa documento enviado."""
        file_name = document.get("file_name", "documento")
        file_id = document.get("file_id")
        
        # Verificar se é imagem
        mime = document.get("mime_type", "")
        if "image" in mime:
            return await self._handle_photo(chat_id, [{"file_id": file_id}])
        
        return f"📄 Documento '{file_name}' recebido.\n\nNo momento só suporto imagens para análise.\nTente enviar um print ou screenshot."
    
    async def _handle_url(self, chat_id: str, text: str) -> str:
        """Analisa conteúdo de URL enviada."""
        log.info("CHATBOT_URL", f"URL detected from {chat_id}")
        
        try:
            # Extrair URL do texto
            import re
            urls = re.findall(r'https?://[^\s<>"\']+', text)
            
            if not urls:
                return await self._cmd_ai(chat_id, text)
            
            url = urls[0][:500]  # Limitar tamanho
            
            # Fetch do conteúdo
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return f"🔗 Não consegui acessar a URL (status: {resp.status})"
                    
                    content_type = resp.headers.get("Content-Type", "")
                    
                    if "text/html" in content_type:
                        html = await resp.text()
                        # Extrair texto relevante
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(html, "html.parser")
                        
                        # Remover scripts e styles
                        for script in soup(["script", "style"]):
                            script.decompose()
                        
                        text_content = soup.get_text()
                        # Limpar texto
                        lines = [line.strip() for line in text_content.split('\n') if line.strip()]
                        text_content = ' '.join(lines)[:3000]
                        
                        if not text_content:
                            return "🔗 Conteúdo da página não pôde ser extraído."
                        
                        # Analisar com IA
                        api_key = os.getenv("GROQ_API_KEY", "")
                        if not api_key:
                            return f"🔗 *Conteúdo da URL:*\n\n{text_content[:1000]}...\n\nConfigure GROQ_API_KEY para análise com IA."
                        
                        analysis = await self._analyze_url_content(url, text_content, api_key)
                        return analysis
                    else:
                        return f"🔗 URL acessível mas conteúdo não é HTML. Tipo: {content_type[:50]}"
                        
        except asyncio.TimeoutError:
            return "🔗 Tempo esgotado ao acessar URL. A página pode estar lenta."
        except Exception as exc:
            log.error("CHATBOT_URL_ERROR", f"failed: {exc}")
            return f"🔗 Erro ao acessar URL: {exc}"
    
    async def _analyze_image(self, image_bytes: bytes, api_key: str) -> Optional[str]:
        """Analisa imagem com IA."""
        try:
            import base64
            image_b64 = base64.b64encode(image_bytes).decode()
            
            payload = {
                "model": AI_MODEL_VISION,
                "messages": [
                    {"role": "system", "content": IMAGE_ANALYSIS_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Analise esta imagem:"},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                        ]
                    }
                ],
                "max_tokens": 800,
            }
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    GROQ_API_URL, 
                    json=payload, 
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        log.error("CHATBOT_VISION", f"API error: {resp.status}")
                        return None
                    
                    data = await resp.json()
                    return data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    
        except Exception as exc:
            log.error("CHATBOT_VISION", f"failed: {exc}")
            return None
    
    async def _analyze_url_content(self, url: str, content: str, api_key: str) -> str:
        """Analisa conteúdo de URL com IA."""
        try:
            payload = {
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": "Você é um analista de trading profissional. Analise o conteúdo desta página web e forneça insights relevantes sobre crypto, trading ou mercados financeiros."},
                    {"role": "user", "content": f"URL: {url}\n\nConteúdo:\n{content[:4000]}"}
                ],
                "max_tokens": 600,
            }
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    GROQ_API_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    if resp.status != 200:
                        return f"🔗 *Conteúdo da URL:*\n\n{content[:1000]}...\n\n(IA indisponível para análise)"
                    
                    data = await resp.json()
                    analysis = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    return f"🔗 *Análise da URL*\n\n📄 {url}\n\n{analysis}\n\n_Jarvis AI Trading Monitor_"
                    
        except Exception as exc:
            log.error("CHATBOT_URL_AI", f"failed: {exc}")
            return f"🔗 *Conteúdo:*\n\n{content[:1000]}...\n\n(Erro na análise: {exc})"

    async def _cmd_start(self, chat_id: str, args: str) -> str:
        log.info("CHATBOT_START", f"start command from {chat_id}")
        
        # Verificar configuração básica
        import os
        checks = []
        if os.getenv("GROQ_API_KEY"):
            checks.append("✅ GROQ_API_KEY configurada")
        else:
            checks.append("⚠️ GROQ_API_KEY não configurada")
        
        if os.getenv("TELEGRAM_BOT_TOKEN"):
            checks.append("✅ Telegram configurado")
        else:
            checks.append("⚠️ Telegram não configurado")
        
        return (
            "👋 *Bem-vindo ao Jarvis AI Trading Monitor!*\n\n"
            "Sou seu assistente pessoal de trading de criptomoedas.\n"
            "Estou monitorando o mercado 24/7 e te alertando sobre oportunidades.\n\n"
            "*Configuração:*\n" + "\n".join(checks) + "\n\n"
            "*O que posso fazer:*\n"
            "📊 Analisar o mercado e identificar sinais de trade\n"
            "📰 Buscar as últimas notícias relevantes\n"
            "🌍 Acompanhar o contexto macroeconômico\n"
            "🛡️ Gerenciar riscos da sua carteira\n"
            "🤖 Responder suas perguntas sobre crypto\n\n"
            "*Comece com:*\n"
            "/debug — Diagnosticar o sistema\n"
            "/status — Ver como o sistema está\n"
            "/sinais — Ver oportunidades de trade\n"
            "/help — Ver todos os comandos"
        )
        return welcome

    async def _cmd_help(self, chat_id: str, args: str) -> str:
        return (
            "📚 *Todos os Comandos*\n\n"
            "🔍 *Mercado:*\n"
            "/sinais    - Ver oportunidades de trade\n"
            "/news      - Últimas notícias\n"
            "/macro     - Contexto macroeconômico\n\n"
            "📊 *Sistema:*\n"
            "/status    - Como está o sistema\n"
            "/performance - Nossos resultados\n"
            "/debug     - Diagnóstico completo\n\n"
            "🧠 *Aprendizado:*\n"
            "/learn     - Ver o que eu aprendi\n\n"
            "🤖 *Conversar:*\n"
            "/ai [pergunta] - Fazer qualquer pergunta\n"
            "Ex: /ai BTC vai subir?\n\n"
            "⚡ *Ações:*\n"
            "/scan - Forçar nova análise\n\n"
            "💡 *Dica:* Pode perguntar direto também!"
        )

    async def _cmd_status(self, chat_id: str, args: str) -> str:
        quality = get_current_quality()
        
        emoji_hl = "✅" if quality.hyperliquid_available else "❌"
        emoji_ws = "✅" if quality.ws_connected else "⚠️"
        emoji_news = "✅" if quality.news_api_available else "❌"
        emoji_macro = "✅" if quality.macro_api_available else "❌"
        emoji_ai = "✅" if quality.ai_available else "❌"
        
        status_lines = [
            "📊 *Status do Sistema*",
            f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_",
            "",
            "*🔗 Conexões:*",
            f"• Hyperliquid: {emoji_hl} Preços em tempo real",
            f"• WebSocket: {emoji_ws} {"Atualizações live" if quality.ws_connected else "Reconectando..."}",
            f"• Notícias: {emoji_news} Fontes ativas",
            f"• Macro: {emoji_macro} Dados econômicos",
            f"• IA Groq: {emoji_ai} {"Disponível" if quality.ai_available else "Sem IA"}",
            "",
            "*📡 Qualidade:*",
            f"• {quality.quality_label}",
            f"• Dados de mercado: {quality.market_age_minutes:.0f}min",
            f"• Notícias: {quality.news_age_minutes:.0f}min",
            f"• Símbolos monitorados: {quality.symbols_with_data}/{quality.symbols_requested}",
        ]
        
        if quality.warnings:
            status_lines.append("")
            status_lines.append("*⚠️ Atenção:*")
            for w in quality.warnings[:2]:
                status_lines.append(w)
        
        status_lines.append("")
        status_lines.append("_Jarvis AI Trading Monitor_")
        return "\n".join(status_lines)

    async def _cmd_sinais(self, chat_id: str, args: str) -> str:
        ranking = self._system_refs.get("last_ranking")
        
        if not ranking or not ranking.top:
            quality = get_current_quality()
            ws_status = "✅ Conectado" if quality.ws_connected else "❌ Desconectado"
            return (
                "📭 *Nenhum sinal agora*\n\n"
                "O sistema monitora 24/7, mas nem sempre há oportunidades claras.\n\n"
                f"Status WebSocket: {ws_status}\n"
                "• Tente /scan para forçar uma análise\n"
                "• Use /status para ver o estado do sistema\n"
                "• /news mostra últimas notícias"
            )
        
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
            return "📰 *Notícias*\n\nSistema de notícias não disponível."
        
        try:
            articles = news_engine._cache[:5] if hasattr(news_engine, "_cache") and news_engine._cache else []
            
            if not articles:
                return "📰 *Notícias*\n\nNenhuma notícia disponível. O sistema pode estar buscando dados..."
            
            lines = [
                "📰 *Últimas Notícias*",
                f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_",
                "",
            ]
            
            for art in articles:
                emoji = "🟢" if art.sentiment == "positive" else ("🔴" if art.sentiment == "negative" else "⚪")
                age_min = (time.time() - art.published_at) / 60
                translated_title = _translate_news_title(art.title)
                title_short = translated_title[:80]
                lines.append(f"{emoji} *{title_short}*")
                lines.append(f"   {age_min:.0f}min | Impacto: {art.impact_score:.0f}")
                lines.append("")
            
            lines.append("_Jarvis AI Trading Monitor_")
            return "\n".join(lines)
        except Exception as exc:
            log.error("CHATBOT_ERROR", f"news error: {exc}")
            return f"📰 *Notícias*\n\nErro ao buscar notícias: {exc}"

    async def _cmd_macro(self, chat_id: str, args: str) -> str:
        macro_engine = self._system_refs.get("macro_engine")
        
        if not macro_engine:
            return "🌍 *Macro*\n\nSistema macro não disponível."
        
        try:
            snap = macro_engine.get_snapshot()
            
            if not snap:
                return "🌍 *Contexto Macroeconômico*\n\nDados macro ainda carregando..."
            
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
            return f"🌍 *Macro*\n\nErro ao buscar dados macro: {exc}"

    async def _cmd_performance(self, chat_id: str, args: str) -> str:
        tracker = self._system_refs.get("tracker")
        
        if not tracker:
            return "📈 *Performance*\n\nSistema de tracking não disponível."
        
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
            return f"📈 *Performance*\n\nErro ao buscar performance: {exc}"

    async def _cmd_learn(self, chat_id: str, args: str) -> str:
        tracker = self._system_refs.get("tracker")
        
        if not tracker:
            return "🧠 *Aprendizado*\n\nSistema de tracking não disponível."
        
        try:
            from proactive_agent import ProactiveAgent
            
            proactive = ProactiveAgent()
            records = await tracker.get_recent_performance(days=7)
            
            if not records:
                return (
                    "🧠 *O que eu aprendi*\n\n"
                    "Ainda não tenho dados suficientes para aprender.\n"
                    "Aguarde mais trades para eu analisar padrões."
                )
            
            insights = await proactive.learn_from_outcomes(records)
            
            lines = [
                "🧠 *O QUE EU APRENDI*\n",
                f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_\n",
            ]
            
            overall_wr = insights.get("overall_win_rate", 0)
            lines.append(f"📊 Win rate geral: `{overall_wr:.1f}%`")
            lines.append("")
            
            long_wr = insights.get("win_rate_by_direction", {}).get("LONG", {}).get("win_rate", 0)
            short_wr = insights.get("win_rate_by_direction", {}).get("SHORT", {}).get("win_rate", 0)
            lines.append(f"📈 LONGs: `{long_wr:.1f}%` | 📉 SHORTs: `{short_wr:.1f}%`")
            lines.append("")
            
            best_symbols = insights.get("best_symbols", {})
            if best_symbols:
                sorted_symbols = sorted(
                    [(s, d) for s, d in best_symbols.items() if d.get("total", 0) >= 2],
                    key=lambda x: x[1].get("win_rate", 0),
                    reverse=True
                )[:3]
                if sorted_symbols:
                    lines.append("*🏆 Melhores símbolos:*")
                    for sym, data in sorted_symbols:
                        wr = data.get("win_rate", 0)
                        total = data.get("total", 0)
                        lines.append(f"  • {sym}: `{wr:.0f}%` ({total} trades)")
                    lines.append("")
            
            best_hours = insights.get("best_hours", {})
            if best_hours:
                sorted_hours = sorted(
                    [(h, d) for h, d in best_hours.items() if d.get("total", 0) >= 2],
                    key=lambda x: x[1].get("win_rate", 0),
                    reverse=True
                )[:3]
                if sorted_hours:
                    lines.append("*⏰ Melhores horários:*")
                    for hour, data in sorted_hours:
                        wr = data.get("win_rate", 0)
                        lines.append(f"  • {hour}: `{wr:.0f}%` win rate")
                    lines.append("")
            
            lines.append("_Use /ai para perguntar sobre insights específicos_")
            return "\n".join(lines)
        except Exception as exc:
            log.error("CHATBOT_ERROR", f"learn error: {exc}")
            return f"🧠 *Aprendizado*\n\nErro ao analisar padrões: {exc}"

    async def _cmd_debug(self, chat_id: str, args: str) -> str:
        """Mostra diagnóstico detalhado do sistema."""
        from data_quality import get_current_quality
        from websocket_client import ws_state
        from kill_switch import KillSwitch
        
        lines = [
            "🔍 *DIAGNÓSTICO DO SISTEMA*",
            f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_",
            "",
        ]
        
        quality = get_current_quality()
        lines.append("*📊 Qualidade dos Dados:*")
        lines.append(f"• Score: `{quality.quality_score:.0%}`")
        lines.append(f"• Status: {'✅ OK' if not quality.should_block_signals else '❌ BLOQUEADO'}")
        lines.append(f"• Hyperliquid: {'✅' if quality.hyperliquid_available else '❌'}")
        lines.append(f"• Symbols: {quality.symbols_with_data}/{quality.symbols_requested}")
        lines.append(f"• Dados frescos: {quality.market_age_minutes:.0f}min")
        lines.append("")
        
        ws_status = ws_state.get("status", "UNKNOWN")
        ws_last = ws_state.get("last_message_at", "nunca")
        lines.append("*🌐 WebSocket:*")
        lines.append(f"• Status: {ws_status}")
        lines.append(f"• Último msg: {ws_last}")
        lines.append("")
        
        kill_switch = KillSwitch()
        lines.append("*🛑 Kill Switch:*")
        lines.append(f"• Pode operar: {'✅ SIM' if kill_switch.can_trade() else '❌ NÃO'}")
        if not kill_switch.can_trade():
            status = kill_switch.get_status()
            lines.append(f"• Motivo: {status.reason}")
        lines.append("")
        
        groq_key = os.getenv("GROQ_API_KEY", "")
        lines.append("*🤖 IA (Groq):*")
        lines.append(f"• API Key: {'✅ Configurada' if groq_key else '❌ NÃO CONFIGURADA'}")
        lines.append("")
        
        ranking = self._system_refs.get("last_ranking")
        lines.append("*📈 Últimos Sinais:*")
        if ranking and ranking.top:
            lines.append(f"• Qtd: {len(ranking.top)} sinais")
            for s in ranking.top[:3]:
                lines.append(f"  • {s.symbol}: {s.direction} ({s.score:.0f})")
        else:
            lines.append("• Nenhum sinal gerado")
        lines.append("")
        
        return "\n".join(lines)

    async def _cmd_scan(self, chat_id: str, args: str) -> str:
        scan_fn = self._system_refs.get("scanner_module")
        
        if not scan_fn:
            log.error("CHATBOT_SCAN", "scanner_module not in _system_refs")
            return "⚡ *Scan*\n\nScanner não disponível.\n\nUse /debug para ver o que está bloqueando."
        
        try:
            log.info("CHATBOT_SCAN", "Starting scan from chatbot command")
            ranking = await scan_fn()
            log.info("CHATBOT_SCAN", f"Scan completed, signals: {len(ranking.top) if ranking and ranking.top else 0}")
            
            if ranking and ranking.top:
                count = len(ranking.top)
                lines = [f"✅ *Scan Completo*\n\n{count} sinal(ais) encontrado(s):\n"]
                for sig in ranking.top[:3]:
                    emoji = "📈" if sig.direction == "LONG" else "📉"
                    lines.append(f"{emoji} {sig.symbol}/USDT ({sig.direction}) — Score: `{sig.score:.0f}`")
                return "\n".join(lines)
            else:
                return "✅ *Scan Completo*\n\nNenhum sinal válido encontrado.\nMotivo: condições de mercado não favoráveis ou dados indisponíveis.\n\nUse /debug para diagnóstico."
        except Exception as exc:
            log.error("CHATBOT_ERROR", f"scan error: {exc}")
            return f"⚡ *Scan*\n\nErro ao executar scan: {exc}\n\nUse /debug para diagnóstico completo."

    async def _cmd_risk(self, chat_id: str, args: str) -> str:
        risk_manager = self._system_refs.get("risk_manager")
        kill_switch = self._system_refs.get("kill_switch")
        
        lines = [
            "🛡️ *Status de Risco*",
            f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_",
            "",
        ]
        
        if kill_switch:
            status = kill_switch.get_status()
            if status.is_active:
                lines.append("🛑 *KILL SWITCH ATIVO*")
                lines.append(f"Motivo: {status.reason}")
            else:
                lines.append("✅ *Kill Switch: OFF*")
            
            lines.append(f"• P&L Diário: `{status.daily_pnl_pct*100:+.2f}%`")
            lines.append(f"• Perdas Consecutivas: {status.consecutive_losses}")
            lines.append(f"• Trades Hoje: {status.trades_today}")
            lines.append(f"• Pode Operar: {'✅ SIM' if not status.block_new_trades else '❌ NÃO'}")
            lines.append("")
        
        if risk_manager:
            r_status = risk_manager.get_status()
            lines.append("*📊 Portfolio Risk:*")
            lines.append(f"• Posições Abertas: {r_status['open_positions']}/{r_status['max_allowed']}")
            lines.append(f"• Qualidade Dados: {'✅ OK' if r_status['data_quality_ok'] else '❌ INVÁLIDOS'}")
            lines.append("")
        
        lines.append("_Jarvis AI Trading Monitor_")
        return "\n".join(lines)

    async def _cmd_ai(self, chat_id: str, args: str) -> str:
        api_key = os.getenv("GROQ_API_KEY", "")
        
        if not api_key:
            log.warning("CHATBOT_AI", "GROQ_API_KEY not configured")
            return "🤖 *IA não disponível*\n\nGROQ_API_KEY não configurada.\n\nConfigure a variável de ambiente GROQ_API_KEY no seu .env ou no Railway."
        
        if not args:
            return (
                "🤖 *Pergunte-me qualquer coisa!*\n\n"
                "Exemplos:\n"
                "• BTC vai subir essa semana?\n"
                "• O que é funding rate?\n"
                "• Analise SOL para mim\n"
                "• Devo operar agora?\n\n"
                "Digite sua pergunta diretamente! 👇"
            )
        
        try:
            response = await self._call_groq(args, api_key)
            if response:
                return f"🤖 *Resposta IA:*\n\n{response}\n\n_Resposta gerada por IA - use como referência, não como conselho financeiro_"
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
            translated_news = _translate_news_title(top_news)
            context_parts.append(f"ÚLTIMA NOTÍCIA: {translated_news}")
        
        if macro_engine:
            snap = macro_engine.get_snapshot()
            if snap:
                context_parts.append(f"MACRO: risco={snap.risk_score} bias={snap.crypto_bias}")
        
        context = "\n".join(context_parts) if context_parts else "Sistema sem dados disponíveis no momento."
        
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
