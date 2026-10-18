import os, httpx
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (ApplicationBuilder, CommandHandler,
                          MessageHandler, filters, ContextTypes)

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_KEY = os.environ["ANTHROPIC_API_KEY"].strip().strip('"').strip("'")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
SYSTEM = "Sen foydali AI yordamchisan. Foydalanuvchi qaysi tilda yozsa, o'sha tilda (asosan o'zbekcha) aniq va qisqa javob ber."
history = {}

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men AI yordamchiman. Istalgan savolingni yoz.\n/new - yangi suhbat")

async def new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    history.pop(update.effective_user.id, None)
    await update.message.reply_text("Suhbat tozalandi.")

async def chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msgs = history.setdefault(uid, [])
    msgs.append({"role": "user", "content": update.message.text})
    del msgs[:-20]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": API_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": 1000,
                      "system": SYSTEM, "messages": msgs})
            if r.status_code != 200:
                print("XATO:", r.status_code, r.text)
                r.raise_for_status()
            answer = r.json()["content"][0]["text"]
    except Exception as e:
        msgs.pop()
        print("XATO:", repr(e))
        await update.message.reply_text("Xatolik yuz berdi, qayta urinib ko'r.")
        return

    msgs.append({"role": "assistant", "content": answer})
    for i in range(0, len(answer), 4000):
        await update.message.reply_text(answer[i:i+4000])

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("new", new))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
app.run_polling()
