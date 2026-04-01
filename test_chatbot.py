"""
test_chatbot.py - Teste standalone do chatbot

Uso:
1. Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env
2. Rode: python test_chatbot.py
3. Envie uma mensagem no Telegram
4. O bot deve responder
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from chatbot import JarvisChatbot


async def test_chatbot():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("❌ Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env")
        return
    
    print(f"✅ Token: {token[:10]}...")
    print(f"✅ Chat ID: {chat_id}")
    
    bot = JarvisChatbot(token)
    bot.set_alert_chat_id(chat_id)
    
    print("\n📤 Enviando mensagem de teste...")
    test_msg = "Olá! Teste do Jarvis Bot. Responda por favor!"
    sent = await bot._send_message(chat_id, test_msg)
    print(f"📤 Mensagem enviada: {sent}")
    
    if sent:
        print("\n✅ Bot funcionando! Agora:")
        print("   1. Abra o Telegram")
        print("   2. Envie /start ou qualquer mensagem")
        print("   3. O bot deve responder")
        print("\n⏳ Aguardando mensagens por 60 segundos...")
        
        for i in range(12):
            await asyncio.sleep(5)
            print(f"⏳ Aguardando... ({i*5}s)")
            
            result = await bot.poll()
            if result:
                print(f"\n📩 Mensagem recebida: {result}")
                text = result.get("text", "")
                user_id = result.get("chat_id", "")
                
                if text:
                    response = await bot.handle_message(text, user_id)
                    print(f"📤 Resposta: {response[:100]}...")
                    await bot._send_message(user_id, response)
                    print("✅ Resposta enviada!")
    else:
        print("❌ Falha ao enviar mensagem de teste")


if __name__ == "__main__":
    asyncio.run(test_chatbot())
