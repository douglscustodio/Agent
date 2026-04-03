#  Guia de Deploy - Jarvis AI Trading Monitor

Este guia explica como colocar o Jarvis para rodar 24/7 em diferentes plataformas.

---

## Opções de Deploy

| Plataforma | Custo | Dificuldade | Recomendado para |
|------------|-------|-------------|------------------|
| Railway | $5/mês | Fácil | Iniciantes |
| Render | Grátis* | Fácil | Testes |
| VPS (DigitalOcean) | $6/mês | Média | Produção |
| VPS (Hetzner) | €4/mês | Média | Produção |
| Fly.io | Grátis* | Média | Avançados |

*Grátis com limitações

---

## 1. Railway (Recomendado para iniciantes)

### Passo a Passo:

1. **Criar conta**
   - Acesse [railway.app](https://railway.app)
   - Login com GitHub

2. **Deploy**
   - Click "New Project" → "Deploy from GitHub repo"
   - Selecione `douglscustodio/Agent`
   - Railway detecta Python automaticamente

3. **Configurar variáveis**
   - Vá em Settings → Variables
   - Adicione:
   ```
   TELEGRAM_BOT_TOKEN=seu_token
   TELEGRAM_CHAT_ID=seu_chat_id
   GROQ_API_KEY=sua_chave
   HYPERLIQUID_ADDRESS=seu_endereco
   HYPERLIQUID_PRIVATE_KEY=sua_chave_privada
   DATABASE_URL=sqlite:///jarvis.db
   ```

4. **Configurar startup**
   - Em Settings → Start Command:
   ```
   python main.py
   ```

5. **Deploy**
   - Click "Deploy" e aguarde

### Custo:
- $5/mês (Starter)
- $20/mês (para mais recursos)

---

## 2. Render (Grátis)

### Passo a Passo:

1. **Criar conta**
   - Acesse [render.com](https://render.com)
   - Login com GitHub

2. **Criar Web Service**
   - Click "New" → "Web Service"
   - Conecte seu repositório GitHub

3. **Configurar**
   - **Root Directory:** (deixe vazio)
   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`

4. **Variáveis de ambiente**
   - Adicione todas as variáveis como no Railway

5. **Plano gratuito**
   -Atenção: Sleep após 15 min de inatividade
   - Para 24/7, precisa do plano pago ($7/mês)

---

## 3. VPS (DigitalOcean/RackNerd/Hetzner)

### Passo a Passo:

1. **Criar droplet**
   - DigitalOcean: $6/mês (1 vCPU, 1GB RAM)
   - Hetzner: €4/mês (2 vCPU, 4GB RAM)
   - SO: Ubuntu 22.04

2. **Acessar via SSH**
   ```bash
   ssh root@seu_ip
   ```

3. **Instalar dependências**
   ```bash
   apt update && apt upgrade -y
   apt install python3 python3-pip git screen -y
   ```

4. **Clone do projeto**
   ```bash
   cd /opt
   git clone https://github.com/douglscustodio/Agent.git
   cd Agent
   pip3 install -r requirements.txt
   ```

5. **Configurar variáveis**
   ```bash
   cp env.example .env
   nano .env
   # Adicione suas variáveis
   ```

6. **Testar localmente**
   ```bash
   python3 main.py
   # Verifique se funciona
   # Ctrl+C para sair
   ```

7. **Rodar com screen (24/7)**
   ```bash
   # Criar nova screen
   screen -S jarvis
   
   # Dentro da screen
   cd /opt/Agent
   python3 main.py
   
   # Desconectar: Ctrl+A, depois D
   ```

8. **Comandos úteis do screen**
   ```bash
   screen -ls           # Listar screens
   screen -r jarvis     # Voltar para screen
   screen -X -S jarvis quit  # Encerrar
   ```

### Atualizar código
```bash
cd /opt/Agent
git pull
# Reinicie o screen: Ctrl+C e depois python3 main.py
```

---

## 4. Fly.io (Avançado - Grátis)

### Passo a Passo:

1. **Instalar flyctl**
   ```bash
   curl -L https://fly.io/install.sh | sh
   ```

2. **Login**
   ```bash
   fly auth login
   ```

3. **Criar app**
   ```bash
   cd /path/to/Agent
   fly launch
   ```

4. **Configurar secrets**
   ```bash
   fly secrets set TELEGRAM_BOT_TOKEN=seu_token
   fly secrets set TELEGRAM_CHAT_ID=seu_chat_id
   fly secrets set GROQ_API_KEY=sua_chave
   # ... outras variáveis
   ```

5. **Deploy**
   ```bash
   fly deploy
   ```

6. **Ver logs**
   ```bash
   fly logs
   ```

---

## Configuração de Variáveis

### Obrigatórias:
```env
# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=123456789

# Hyperliquid
HYPERLIQUID_ADDRESS=0x...
HYPERLIQUID_PRIVATE_KEY=0x...

# Database
DATABASE_URL=sqlite:///jarvis.db
```

### Opcionais (recomendadas):
```env
# IA
GROQ_API_KEY=gsk_...

# Notícias
CRYPTOPANIC_TOKEN=...

# logging
LOG_LEVEL=INFO
```

---

## Monitoramento

### Health Check
O Jarvis expõe um endpoint de health em:
```
http://seu_servidor:8080/health
```

### Logs
```bash
# Local (screen)
screen -r jarvis

# Fly.io
fly logs

# Railway
railway logs
```

---

## Troubleshooting

### Problema: Bot não conecta
1. Verifique TELEGRAM_BOT_TOKEN
2. Verifique TELEGRAM_CHAT_ID
3. Verifique conexão de internet

### Problema: Sem dados de mercado
1. Verifique HYPERLIQUID_ADDRESS
2. Verifique HYPERLIQUID_PRIVATE_KEY
3. Check: `curl https://api.hyperliquid.xyz/info`

### Problema: MemoryError
- Reduza número de símbolos monitorados
- Aumente RAM do servidor

### Problema: Bot para após algum tempo
- Use screen/tmux para manter rodando
- Configure restart automático (systemd)

---

## systemd (VPS - Restart Automático)

Crie `/etc/systemd/system/jarvis.service`:

```ini
[Unit]
Description=Jarvis AI Trading Monitor
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/Agent
ExecStart=/usr/bin/python3 /opt/Agent/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Ativar:
```bash
systemctl enable jarvis
systemctl start jarvis
systemctl status jarvis
```

---

## Checklist Pré-Deploy

- [ ] TELEGRAM_BOT_TOKEN configurado
- [ ] TELEGRAM_CHAT_ID configurado
- [ ] HYPERLIQUID_ADDRESS configurado
- [ ] HYPERLIQUID_PRIVATE_KEY configurado
- [ ] GROQ_API_KEY configurado (para IA)
- [ ] Testou localmente com sucesso
- [ ] Variables de ambiente configuradas na plataforma
- [ ] Database URL configurado

---

## Próximos Passos

Após deploy:
1. Envie `/start` no Telegram
2. Aguarde briefing inicial
3. Monitore os logs por 24h
4. Ajuste se necessário

---

Boa sorte com seus trades! 
