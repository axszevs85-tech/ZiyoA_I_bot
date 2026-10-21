import asyncio
import logging
import os
import time
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# Railway menyusidagi Variables bo'limidan BOT_TOKEN ni oladi
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)

# Web Scraping (Saytdan ma'lumot olish) funksiyasi
def get_game_data():
    options = Options()
    options.add_argument("--headless")  # Brauzerni fonda (ekransiz) ishlatish
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(options=options)
    try:
        # Bu yerga ma'lumot olinadigan bukmeyker sayti havolasi yoziladi
        url = "https://example.com/mortal-kombat" 
        driver.get(url)
        time.sleep(3) # Sahifa yuklanishini kutish
        
        # Namuna uchun natijalar (buni real sayt class/id lariga moslaysiz)
        results = [
            "Geras vs Johnny Cage — Koeffitsient: 1.001 / 20",
            "Djoker vs Kollektor — Koeffitsient: 1.15 / 5.1",
            "Sindel vs Kollektor — Koeffitsient: 1.01 / 13"
        ]
        return results
    except Exception as e:
        logging.error(f"Xatolik yuz berdi: {e}")
        return ["Ma'lumotlarni olishda xatolik yuz berdi."]
    finally:
        driver.quit() # Brauzerni yopish

# /start buyrug'i
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Salom! Mortal Kombat prognoz botiga xush kelibsiz.\n"
        "Tahlilni ko'rish uchun /predict buyrug'ini bosing."
    )

# /predict buyrug'i
@dp.message(Command("predict"))
async def cmd_predict(message: types.Message):
    await message.answer("🔄 Ma'lumotlar yig'ilmoqda, iltimos kuting...")
    
    # Bot qotib qolmasligi uchun Selenium-ni alohida oqimda ishga tushiramiz
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, get_game_data)
    
    response_text = "📊 **Mortal Kombat o'yinlari prognozi:**\n\n" + "\n".join(data)
    await message.answer(response_text, parse_mode="Markdown")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
