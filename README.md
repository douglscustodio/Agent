# 🤖 Jarvis AI Trading Monitor

Assistente pessoal de trading com IA que monitora o mercado 24/7, analisa oportunidades e te avisa via Telegram.

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
| `/start` | Boas-vindas |
| `/status` | Status do sistema |
| `/sinais` | Ver oportunidades |
| `/news` | Últimas notícias |
| `/macro` | Contexto macro |
| `/risk` | Proteção de risco |
| `/ai` | Pergunte qualquer coisa |

---

## 🎯 Funcionalidades

- **Análise**: 24+ criptomoedas, funding, OI, volatilidade
- **IA**: Validação com Groq AI em português
- **Proteção**: Kill Switch, Portfolio Risk, Squeeze Detector
- **Alertas**: Telegram proativo, dedup, correlação

---

## ⚙️ Configuração

```env
# Obrigatório
HYPERLIQUID_ADDRESS=0x...
HYPERLIQUID_PRIVATE_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Opcional
GROQ_API_KEY=...
CRYPTOPANIC_TOKEN=...
DATABASE_URL=sqlite:///jarvis.db
```

---

## 🛡️ Sistema de Proteção

| Camada | Função |
|--------|--------|
| Kill Switch | Para se perda > 5% |
| Portfolio Risk | Max 3 trades, correlação |
| NO_TRADE Zone | Só opera com edge |
| Squeeze Detector | Evita armadilhas |

---

## 📄 Licença

MIT License

---

## ⚠️ Aviso

Software educacional - risco de perda total do capital.
