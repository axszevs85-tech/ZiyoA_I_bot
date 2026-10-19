import os, json, datetime, logging
import feedparser
from anthropic import AsyncAnthropic
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]  # masalan: @kanalim yoki -100123...
ADMIN_ID = int(os.environ["ADMIN_ID"])
ai = AsyncAnthropic()  # ANTHROPIC_API_KEY env'dan olinadi
MODEL = "claude-haiku-5-5"
DB = "data.json"


def load():
    if os.path.exists(DB):
        return json.load(open(DB))
    return {"sources": [], "seen": [], "topic": "", "ai_hours": 6, "rss_min": 30}


def save(d):
    json.dump(d, open(DB, "w"), ensure_ascii=False)


async def ask_ai(prompt):
    r = await ai.messages.create(
        model=MODEL,
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    return r.content[0].text


def admin_only(fn):
    async def w(u: Update, c: ContextTypes.DEFAULT_TYPE):
        if u.effective_user.id != ADMIN_ID:
            return
        await fn(u, c)
    return w


@admin_only
async def start(u, c):
    await u.message.reply_text(
        "Buyruqlar:\n"
        "/post matn - hozir joylash\n"
        "/jadval 09:30 matn - har kuni shu vaqtda\n"
        "/manba rss_link - yangilik manbasi qo'shish\n"
        "/mavzu mavzu - AI shu mavzuda avto post yozadi\n"
        "/ai - hozir AI post\n"
        "/royxat - sozlamalar"
    )


@admin_only
async def post(u, c):
    text = " ".join(c.args)
    await c.bot.send_message(CHANNEL_ID, text)
    await u.message.reply_text("Joylandi")


@admin_only
async def jadval(u, c):
    hh, mm = map(int, c.args[0].split(":"))
    text = " ".join(c.args[1:])

    async def job(ctx):
        await ctx.bot.send_message(CHANNEL_ID, text)

    c.job_queue.run_daily(job, datetime.time(hh, mm, tzinfo=datetime.timezone(datetime.timedelta(hours=5))))
    await u.message.reply_text(f"Har kuni {c.args[0]} da joylanadi")


@admin_only
async def manba(u, c):
    d = load()
    d["sources"].append(c.args[0])
    save(d)
    await u.message.reply_text("Manba qo'shildi")


@admin_only
async def mavzu(u, c):
    d = load()
    d["topic"] = " ".join(c.args)
    save(d)
    await u.message.reply_text("Mavzu saqlandi")


async def ai_post(ctx):
    d = load()
    if not d["topic"]:
        return
    t = await ask_ai(f"Telegram kanal uchun '{d['topic']}' mavzusida qiziqarli, qisqa o'zbekcha post yoz. Emoji ishlat.")
    await ctx.bot.send_message(CHANNEL_ID, t)


@admin_only
async def ai_now(u, c):
    await ai_post(c)
    await u.message.reply_text("AI post joylandi")


async def rss_job(ctx):
    d = load()
    for url in d["sources"]:
        for e in feedparser.parse(url).entries[:3]:
            if e.link in d["seen"]:
                continue
            d["seen"].append(e.link)
            t = await ask_ai(
                f"Quyidagi yangilikni o'zbek tiliga qisqa tarjima qilib, kanal posti ko'rinishida yoz:\n{e.title}\n{e.get('summary', '')}"
            )
            await ctx.bot.send_message(CHANNEL_ID, f"{t}\n\n{e.link}")
    d["seen"] = d["seen"][-500:]
    save(d)


@admin_only
async def royxat(u, c):
    d = load()
    await u.message.reply_text(f"Manbalar: {d['sources']}\nMavzu: {d['topic']}")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    for name, fn in [("start", start), ("post", post), ("jadval", jadval), ("manba", manba),
                     ("mavzu", mavzu), ("ai", ai_now), ("royxat", royxat)]:
        app.add_handler(CommandHandler(name, fn))
    d = load()
    app.job_queue.run_repeating(ai_post, interval=d["ai_hours"] * 3600, first=60)
    app.job_queue.run_repeating(rss_job, interval=d["rss_min"] * 60, first=30)
    app.run_polling()


if __name__ == "__main__":
    main()
               
