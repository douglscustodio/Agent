"""
setup_wizard.py — Jarvis AI Setup Wizard

Executar: python setup_wizard.py

Guia interativo para configurar o Jarvis AI Trading Monitor.
"""

import os
import sys


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║     🤖 JARVIS AI TRADING MONITOR - SETUP WIZARD            ║
║                                                              ║
║     Seu assistente pessoal de trading com IA                ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)


def print_step(num, total, title):
    print(f"\n{'='*60}")
    print(f"  PASSO {num}/{total}: {title}")
    print('='*60)


def print_success(msg):
    print(f"  ✅ {msg}")


def print_warning(msg):
    print(f"  ⚠️ {msg}")


def print_error(msg):
    print(f"  ❌ {msg}")


def ask_question(question, default=None, required=True):
    while True:
        if default:
            prompt = f"  {question} [{default}]: "
        else:
            prompt = f"  {question}: "
        
        answer = input(prompt).strip()
        
        if not answer and default:
            return default
        
        if not answer and required:
            print_error("Este campo é obrigatório!")
            continue
        
        return answer


def ask_yes_no(question, default="n"):
    while True:
        suffix = " [s/N]: " if default == "n" else " [S/n]: "
        answer = input(f"  {question}{suffix}").strip().lower()
        
        if not answer:
            return default == "s"
        
        if answer in ["s", "sim", "y", "yes"]:
            return True
        if answer in ["n", "nao", "não", "no"]:
            return False
        
        print_error("Digite 's' para sim ou 'n' para não")


def setup_env_file():
    print_step(1, 5, "Configurar Arquivo .env")
    
    env_vars = {}
    
    print("\n  Vamos configurar suas variáveis de ambiente.")
    print("  Se você não tem alguma chave, pode deixar em branco.\n")
    
    # Hyperliquid
    print("\n  📊 HYPERLIQUID (Dados de Mercado)")
    print("  ─────────────────────────────────────")
    print("  Configure em: https://app.hyperliquid.xyz/")
    print("  Vá em Settings > API Keys > Create Key")
    
    env_vars['HYPERLIQUID_ADDRESS'] = ask_question(
        "Endereço da wallet (0x...)",
        required=False
    )
    env_vars['HYPERLIQUID_PRIVATE_KEY'] = ask_question(
        "Chave privada da API",
        required=False
    )
    
    # Telegram
    print("\n  📱 TELEGRAM (Alertas)")
    print("  ─────────────────────────────────────")
    print("  1. Abra o Telegram e procure @BotFather")
    print("  2. Envie /newbot e siga as instruções")
    print("  3. Copie o token que receber")
    print("  4. inicie uma conversa com seu bot e envie qualquer mensagem")
    print("  5. Acesse: https://api.telegram.org/bot<SEU_TOKEN>/getUpdates")
    print("  6. Copie o 'chat' > 'id'")
    
    env_vars['TELEGRAM_BOT_TOKEN'] = ask_question(
        "Token do bot Telegram"
    )
    env_vars['TELEGRAM_CHAT_ID'] = ask_question(
        "Seu Chat ID (número)"
    )
    
    # Groq AI
    print("\n  🤖 GROQ AI (Análise Inteligente) - Opcional")
    print("  ─────────────────────────────────────")
    print("  Configure em: https://console.groq.com/")
    print("  Crie uma conta gratuita e gere uma API Key")
    
    env_vars['GROQ_API_KEY'] = ask_question(
        "Chave da Groq API",
        required=False
    )
    
    # CryptoPanic
    print("\n  📰 CRYPTOPANIC (Notícias) - Opcional")
    print("  ─────────────────────────────────────")
    print("  Configure em: https://cryptopanic.com/")
    print("  Crie conta > Settings > API > Developer API")
    
    env_vars['CRYPTOPANIC_TOKEN'] = ask_question(
        "Token do CryptoPanic",
        required=False
    )
    
    # Database
    print("\n  🗄️ DATABASE (Histórico) - Opcional")
    print("  ─────────────────────────────────────")
    print("  Padrão: SQLite (automático)")
    print("  Para PostgreSQL: postgresql://user:pass@host:5432/db")
    
    db_choice = ask_yes_no(
        "Deseja usar PostgreSQL em vez de SQLite?",
        default="n"
    )
    
    if db_choice:
        env_vars['DATABASE_URL'] = ask_question(
            "URL do PostgreSQL"
        )
    else:
        env_vars['DATABASE_URL'] = "sqlite:///jarvis.db"
    
    return env_vars


def setup_optional_features():
    print_step(2, 5, "Configurar Features Opcionais")
    
    features = {}
    
    features['ENABLE_NEWS'] = ask_yes_no(
        "Ativar monitoramento de notícias?",
        default="s"
    )
    
    features['ENABLE_MACRO'] = ask_yes_no(
        "Ativar análise macroeconômica?",
        default="s"
    )
    
    features['ENABLE_AI'] = ask_yes_no(
        "Ativar análise com IA (Groq)?",
        default="s"
    )
    
    return features


def setup_risk_parameters():
    print_step(3, 5, "Configurar Parâmetros de Risco")
    
    print("\n  ⚠️ Estes parâmetros controlam a proteção do seu capital")
    print("  ⚠️ Ajuste com cuidado baseado no seu perfil de risco\n")
    
    risk_params = {}
    
    risk_params['MAX_DAILY_LOSS'] = ask_question(
        "Perda diária máxima (%)",
        default="5"
    )
    
    risk_params['MAX_CONSECUTIVE_LOSSES'] = ask_question(
        "Máximo de perdas consecutivas",
        default="3"
    )
    
    risk_params['MAX_SIMULTANEOUS_TRADES'] = ask_question(
        "Máximo de trades simultâneos",
        default="3"
    )
    
    risk_params['MIN_SIGNAL_SCORE'] = ask_question(
        "Score mínimo para sinal (0-100)",
        default="45"
    )
    
    return risk_params


def write_env_file(env_vars, features, risk_params):
    print_step(4, 5, "Gerar Arquivo .env")
    
    content = """# ============================================================
# JARVIS AI TRADING MONITOR - Configuração
# ============================================================
# Gerado automaticamente pelo setup_wizard.py
# ============================================================

# ─── HYPERLIQUID (Obrigatório) ───
# Dados de mercado em tempo real
HYPERLIQUID_ADDRESS={}
HYPERLIQUID_PRIVATE_KEY={}

# ─── TELEGRAM (Obrigatório) ───
# Alertas via Telegram
TELEGRAM_BOT_TOKEN={}
TELEGRAM_CHAT_ID={}

# ─── GROQ AI (Opcional) ───
# Análise inteligente com IA
GROQ_API_KEY={}

# ─── CRYPTOPANIC (Opcional) ───
# Feed de notícias
CRYPTOPANIC_TOKEN={}

# ─── DATABASE ───
# Padrão: SQLite
DATABASE_URL={}

# ─── LOGS ───
LOG_LEVEL=INFO

# ─── SCANNER ───
# Intervalo de candles (1m, 5m, 15m, 1h, 4h, 1d)
SCAN_INTERVAL=15m
# Símbolos para monitorar (separados por vírgula)
SCAN_SYMBOLS=BTC,ETH,SOL,ARB,OP,AVAX,NEAR,APT,SUI,INJ,TIA,JTO,PYTH,WIF,BONK,PEPE,LDO,RNDR,FET,TAO,DOGE,LINK,UNI,AAVE

# ─── FEATURES ───
ENABLE_NEWS={}
ENABLE_MACRO={}
ENABLE_AI={}

# ─── RISK PARAMETERS ───
MAX_DAILY_LOSS={}
MAX_CONSECUTIVE_LOSSES={}
MAX_SIMULTANEOUS_TRADES={}
MIN_SIGNAL_SCORE={}

# ─── EXECUTION (Avançado) ───
# Não altere a menos que saiba o que está fazendo
EXECUTION_MODE=simulation
""".format(
        env_vars.get('HYPERLIQUID_ADDRESS', ''),
        env_vars.get('HYPERLIQUID_PRIVATE_KEY', ''),
        env_vars.get('TELEGRAM_BOT_TOKEN', ''),
        env_vars.get('TELEGRAM_CHAT_ID', ''),
        env_vars.get('GROQ_API_KEY', ''),
        env_vars.get('CRYPTOPANIC_TOKEN', ''),
        env_vars.get('DATABASE_URL', 'sqlite:///jarvis.db'),
        'true' if features.get('ENABLE_NEWS') else 'false',
        'true' if features.get('ENABLE_MACRO') else 'false',
        'true' if features.get('ENABLE_AI') else 'false',
        risk_params.get('MAX_DAILY_LOSS', '5'),
        risk_params.get('MAX_CONSECUTIVE_LOSSES', '3'),
        risk_params.get('MAX_SIMULTANEOUS_TRADES', '3'),
        risk_params.get('MIN_SIGNAL_SCORE', '45'),
    )
    
    with open('.env', 'w') as f:
        f.write(content)
    
    print_success("Arquivo .env gerado com sucesso!")
    print(f"  Local: {os.path.abspath('.env')}")


def verify_setup():
    print_step(5, 5, "Verificar Configuração")
    
    errors = []
    warnings = []
    
    # Check required fields
    if not os.getenv('TELEGRAM_BOT_TOKEN'):
        errors.append("TELEGRAM_BOT_TOKEN não configurado")
    if not os.getenv('TELEGRAM_CHAT_ID'):
        errors.append("TELEGRAM_CHAT_ID não configurado")
    
    # Check optional fields
    if not os.getenv('HYPERLIQUID_ADDRESS'):
        warnings.append("Hyperliquid não configurado - dados de mercado limitados")
    if not os.getenv('GROQ_API_KEY'):
        warnings.append("Groq AI não configurado - análise de IA desabilitada")
    
    # Display results
    print()
    if errors:
        print_error("ERROS ENCONTRADOS:")
        for e in errors:
            print(f"  - {e}")
        print()
    
    if warnings:
        print_warning("AVISOS:")
        for w in warnings:
            print(f"  - {w}")
        print()
    
    if not errors:
        print_success("Configuração básica completa!")
        print("\n  Para iniciar o Jarvis, execute:")
        print("  python main.py")
    else:
        print_error("Corrija os erros acima antes de iniciar.")
    
    print("\n  Para reconfigurar, execute novamente:")
    print("  python setup_wizard.py")


def main():
    print_banner()
    
    print("""
  Este assistente vai te ajudar a configurar o Jarvis AI.
  
  Você precisará de:
  • Token do bot Telegram (obrigatório)
  • Chat ID do Telegram (obrigatório)
  • Chave da API Hyperliquid (recomendado)
  • Chave da API Groq (opcional)
    """)
    
    if not ask_yes_no("Deseja continuar com o setup?", default="s"):
        print("\n  Setup cancelado.")
        return
    
    # Run setup steps
    env_vars = setup_env_file()
    features = setup_optional_features()
    risk_params = setup_risk_parameters()
    write_env_file(env_vars, features, risk_params)
    verify_setup()
    
    print("""
  ╔══════════════════════════════════════════════════════════════╗
  ║                                                              ║
  ║     🎉 CONFIGURAÇÃO CONCLUÍDA!                             ║
  ║                                                              ║
  ║     Próximo passo: python main.py                          ║
  ║                                                              ║
  ╚══════════════════════════════════════════════════════════════╝
    """)


if __name__ == "__main__":
    main()
