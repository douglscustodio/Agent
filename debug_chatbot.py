"""
debug_chatbot.py - Debug direto do chatbot Telegram

Rode este script para testar o bot manualmente:
python debug_chatbot.py
"""

import asyncio
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

API_BASE = f"https://api.telegram.org/bot{TOKEN}"


async def test_bot():
    print("=" * 50)
    print("DEBUG DO CHATBOT")
    print("=" * 50)
    
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN não configurado!")
        return
    
    if not CHAT_ID:
        print("❌ TELEGRAM_CHAT_ID não configurado!")
        return
    
    print(f"✅ Token: {TOKEN[:10]}...")
    print(f"✅ Chat ID: {CHAT_ID}")
    
    async with aiohttp.ClientSession() as session:
        # Teste 1: Verificar se o bot está online
        print("\n📡 Teste 1: Verificando bot...")
        async with session.get(f"{API_BASE}/getMe") as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("ok"):
                    print(f"✅ Bot online: @{data['result']['username']}")
                else:
                    print(f"❌ Bot error: {data}")
            else:
                print(f"❌ HTTP error: {resp.status}")
        
        # Teste 2: Enviar mensagem de teste
        print("\n📤 Teste 2: Enviando mensagem de teste...")
        payload = {
            "chat_id": CHAT_ID,
            "text": "🔧 *Teste do Jarvis Bot*\n\nOlá! Este é um teste para verificar se o bot está funcionando.\n\nSe você está vendo esta mensagem, o bot está OK!",
            "parse_mode": "Markdown"
        }
        async with session.post(f"{API_BASE}/sendMessage", json=payload) as resp:
            if resp.status == 200:
                print("✅ Mensagem enviada com sucesso!")
            else:
                body = await resp.text()
                print(f"❌ Erro ao enviar: {resp.status} - {body}")
        
        # Teste 3: Verificar updates
        print("\n📬 Teste 3: Verificando mensagens pendentes...")
        params = {"timeout": 1}
        async with session.get(f"{API_BASE}/getUpdates", params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                updates = data.get("result", [])
                print(f"📬 Updates pendentes: {len(updates)}")
                for u in updates[:3]:
                    msg = u.get("message", {})
                    print(f"   - {msg.get('from', {}).get('first_name', '?')}: {msg.get('text', '')[:50]}")
            else:
                body = await resp.text()
                print(f"❌ Erro: {resp.status} - {body}")
    
    print("\n" + "=" * 50)
    print("TESTE COMPLETO")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_bot())
