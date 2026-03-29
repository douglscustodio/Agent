"""
test_telegram.py — Teste isolado do Telegram
Execute: python test_telegram.py
"""
import asyncio
import os
import aiohttp

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

async def test():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERRO: TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID nao definidos")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       "✅ Teste Railway → Telegram funcionando!",
        "parse_mode": "Markdown",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            body = await resp.json()
            if resp.status == 200:
                print("✅ SUCESSO — mensagem enviada!")
            else:
                print(f"❌ ERRO {resp.status}: {body}")

asyncio.run(test())
