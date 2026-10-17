import os, httpx
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (ApplicationBuilder, CommandHandler,
                          MessageHandler, filters, ContextTypes)

BOT_TOKEN = os.environ["BOT_TOKEN"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

SYSTEM = "Sen foydali AI yordamchisan. Foydalanuvchi qaysi tilda yozsa, o'sha tilda (asosan o'zbekcha) aniq va qisqa javob ber."
history = {}  # user_id -> xabarlar ro'yxati

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men AI yordamchiman. Istalgan savolingni yoz.\n/new - yangi suhbat")

async def new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    history.pop(update.effective_user.id, None)
    await update.message.reply_text("Suhbat tozalandi.")

async def chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msgs = history.setdefault(uid, [])
    msgs.append({"role": "user", "parts": [{"text": update.message.text}]})
    del msgs[:-20]  # oxirgi 20 ta xabar

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(URL, params={"key": GEMINI_KEY}, json={
                "systemInstruction": {"parts": [{"text": SYSTEM}]},
                "contents": msgs})
            r.raise_for_status()
            answer = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        msgs.pop()
        await update.message.reply_text("Xatolik yuz berdi, qayta urinib ko'r.")
        return

    msgs.append({"role": "model", "parts": [{"text": answer}]})
    for i in range(0, len(answer), 4000):
        await update.message.reply_text(answer[i:i+4000])

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("new", new))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
app.run_polling()
