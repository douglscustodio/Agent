# 🤖 Jarvis AI Trading Monitor

Assistente pessoal de trading com IA que monitora o mercado 24/7, analisa oportunidades e te avisa via Telegram. **Totalmente proativo** - não precisa perguntar, ele te mantém antenado.

---

## 🚀 Quick Start

```bash
# 1. Clone
git clone https://github.com/douglscustodio/Agent.git
cd Agent

# 2. Configure
cp env.example .env
# Edite .env com suas chaves

# 3. Instale
pip install -r requirements.txt

# 4. Execute
python main.py
```

---

## 📱 Comandos Telegram

| Comando | Descrição |
|---------|-----------|
| `/start` | Inicia o bot e mostra mensagem de boas-vindas |
| `/help` | Lista todos os comandos disponíveis |
| `/status` | Status do sistema (conexões, dados, últimas atualizações) |
| `/sinais` | Lista sinais gerados no último scan |
| `/news` | Últimas notícias |
| `/macro` | Contexto macroeconômico atual |
| `/performance` | Performance recente do sistema |
| `/scan` | Força um novo scan (se disponível) |
| `/risk` | Status de proteção de risco |
| `/ai` | Pergunta em linguagem natural sobre o mercado |

---

## ⚡ Sistema Proativo

O Jarvis é **totalmente proativo**. Ele te mantém informado sem você precisar perguntar:

### Alertas Automáticos:

| Alerta | Quando | Descrição |
|--------|--------|-----------|
| 📊 **Pulso de Mercado** | A cada 15min | Resumo do mercado: BTC, regime, oportunidades |
| ⚡ **Mudança de Regime** | Quando detectar | Alerta quando tendência muda |
| 🚨 **Oportunidades** | A cada scan | Top oportunidades detectadas |
| 📰 **Notícias Importantes** | Quando detectado | SEC, ETF, hacks, etc |
| 💥 **BTC Spike** | Quando >3% move | Movimento brusco do BTC |
| 💸 **Funding Extremo** | Quando >1% | Risco de squeeze detectado |
| 🚨 **Sinal de Saída** | Quando detectado | Regime mudou contra posição |
| 📈 **Dashboard Performance** | A cada 4h | Performance do sistema |
| 🌍 **Sentimento** | A cada 2h | Como está o mercado |

---

## 🎯 Funcionalidades

- **Análise**: 24+ criptomoedas, funding, OI, volatilidade
- **IA**: Validação com Groq AI em português
- **Proteção**: Kill Switch, Portfolio Risk, Squeeze Detector
- **Alertas**: Telegram proativo, dedup, correlação
- **Proativo**: Mantém você antenado 24/7

---

## ⚙️ Configuração

```env
# Obrigatório
HYPERLIQUID_ADDRESS=0x...
HYPERLIQUID_PRIVATE_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Opcional (recomendado para IA)
GROQ_API_KEY=...

# Opcional
CRYPTOPANIC_TOKEN=...
DATABASE_URL=sqlite:///jarvis.db
```

### Obtendo o TELEGRAM_BOT_TOKEN:
1. Chat com @BotFather no Telegram
2. Envie `/newbot`
3. Siga as instruções
4. Copie o token

### Obtendo o TELEGRAM_CHAT_ID:
1. Chat com @userinfobot no Telegram
2. Copie o ID numérico

---

## 🛡️ Sistema de Proteção

| Camada | Função |
|--------|--------|
| Kill Switch | Para se perda > 5% ou 3 perdas seguidas |
| Portfolio Risk | Max 3 trades, correlação, exposição |
| NO_TRADE Zone | Só opera com edge confirmado |
| Squeeze Detector | Evita armadilhas de funding alto |
| Data Quality | Bloqueia sinais se dados inválidos |

---

## 📊 Fluxo de Alertas

```
Scanner (5min)
    ↓
Detecta oportunidades
    ↓
┌───────────────────────────────────────┐
│ SISTEMA PROATIVO ENVIA:              │
│ • Oportunidades automaticamente       │
│ • Regime change alerts                │
│ • Funding extreme warnings            │
│ • News alerts (SEC, ETF, hacks)      │
│ • BTC spike alerts                   │
│ • Exit signal warnings               │
└───────────────────────────────────────┘
    ↓
Notificador (Telegram)
    ↓
Você recebe no celular 📱
```

---

## 🚀 Deploy 24/7

### Railway (Recomendado)
1. railway.app
2. Deploy from GitHub
3. Configure variáveis
4. Done!

### VPS
```bash
# Clone e configure
git clone https://github.com/douglscustodio/Agent.git
cd Agent
pip install -r requirements.txt

# Execute com screen
screen -S jarvis
python main.py
# Ctrl+A, D para desconectar
```

Consulte `DEPLOY.md` para guia completo.

---

## 📁 Estrutura do Projeto

```
Agent/
├── main.py              # Entry point + jobs
├── chatbot.py           # Interface Telegram
├── scanner.py           # Scanner de mercado
├── scoring.py           # Motor de pontuação
├── notifier.py          # Dispatcher de alertas
├── proactive_agent.py    # Sistema proativo ⭐
├── kill_switch.py       # Proteção de drawdown
├── portfolio_risk.py    # Gestão de risco
├── squeeze_detector.py  # Detecção de squeeze
├── ai_analyst.py        # Análise com Groq AI
├── macro_intelligence.py # Dados macro
├── news_engine.py       # Agregador de notícias
├── websocket_client.py  # Preços em tempo real
├── btc_regime.py       # Detecção de regime
├── performance_tracker.py # Métricas
├── adaptive.py          # Aprendizado de pesos
├── database.py          # Persistência
├── logger.py           # Logging
├── scheduler.py         # Orquestrador
└── requirements.txt    # Dependências
```

---

## ⚠️ Aviso

Software educacional. Risco de perda total do capital. Não é conselho financeiro.

---

## 📄 Licença

MIT License

---

## 🔗 Links Úteis

- [GitHub Repository](https://github.com/douglscustodio/Agent)
- [Hyperliquid](https://hyperliquid.xyz)
- [Groq AI](https://console.groq.com)
- [Telegram Bot API](https://core.telegram.org/bots/api)
