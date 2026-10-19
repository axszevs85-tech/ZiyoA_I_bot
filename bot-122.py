import os, re, json, time, html, random, datetime, logging
from collections import defaultdict, deque

import feedparser
import httpx
from anthropic import AsyncAnthropic
from telegram import (Update, ChatPermissions, InlineKeyboardButton as B,
                      InlineKeyboardMarkup as M)
from telegram.constants import ParseMode
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]            # @kanal yoki -100...
ADMIN_ID = int(os.environ["ADMIN_ID"])
GROUP_ID = int(os.environ.get("GROUP_ID", "0"))  # kanal muhokama guruhi (ixtiyoriy)
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "")
if not CHANNEL_LINK and str(CHANNEL_ID).startswith("@"):
    CHANNEL_LINK = f"https://t.me/{str(CHANNEL_ID)[1:]}"

ai = AsyncAnthropic()
MODEL = "claude-haiku-5-5"
DB = "data.json"
TZ = datetime.timezone(datetime.timedelta(hours=5))

DEFAULTS = {
    "sources": [], "seen": [], "topic": "", "schedules": [],
    "auto_ai": False, "auto_rss": True, "ai_hours": 6, "rss_min": 30,
    # yangi
    "mod": False, "welcome": True, "banwords": [], "ai_reply": False,
    "force_sub": False, "quiz": False, "quiz_hours": 24,
    "d_kurs": False, "d_ob": False, "d_namoz": False, "d_hikmat": False,
    "daily_time": "08:00", "city": "Tashkent",
    "giveaways": {}, "ad_text": "Reklama narxi va shartlari uchun admin bilan bog'laning.",
}

# xotirada (restartda tozalanadi)
strikes = defaultdict(int)
flood = defaultdict(lambda: deque(maxlen=6))
last_ai_reply = {"t": 0.0}
user_ai_cd = {}


def load():
    d = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(DB):
        d.update(json.load(open(DB)))
    return d


def save(d):
    json.dump(d, open(DB, "w"), ensure_ascii=False)


async def ask_ai(prompt, system=None, max_tokens=700):
    kw = {"system": system} if system else {}
    r = await ai.messages.create(model=MODEL, max_tokens=max_tokens,
                                 messages=[{"role": "user", "content": prompt}], **kw)
    return r.content[0].text


def parse_json(t):
    t = re.sub(r"^```(?:json)?|```$", "", t.strip(), flags=re.M).strip()
    return json.loads(t[t.find("{"): t.rfind("}") + 1])


# ================= klaviaturalar / ekranlar =================
def menu():
    return M([
        [B("📝 Post joylash", callback_data="post"), B("⏰ Jadval", callback_data="jadval")],
        [B("🤖 AI post", callback_data="ai"), B("🎯 Mavzu", callback_data="mavzu")],
        [B("🌐 Tarjima", callback_data="tr"), B("🎁 Konkurs", callback_data="gv")],
        [B("📅 Kunlik postlar", callback_data="daily"), B("🧠 Viktorina", callback_data="quiz")],
        [B("🛡 Moderator", callback_data="mod"), B("📢 Reklama", callback_data="ad")],
        [B("📰 Manbalar", callback_data="manba"), B("⚙️ Sozlamalar", callback_data="set")],
    ])


def back(to="menu"):
    return M([[B("⬅️ Orqaga", callback_data=to)]])


def tgl(d, key, label):
    return B(f"{'✅' if d[key] else '⛔'} {label}", callback_data=f"tg:{key}")


def scr_set(d):
    kb = M([[tgl(d, "auto_ai", "AI avto-post")],
            [tgl(d, "auto_rss", "RSS avto-post")],
            [tgl(d, "ai_reply", "Izohlarga AI javob")],
            [tgl(d, "force_sub", "Majburiy obuna")],
            [B("⬅️ Orqaga", callback_data="menu")]])
    return (f"Mavzu: {d['topic'] or '—'}\nManbalar: {len(d['sources'])} ta\n"
            f"Jadval: {len(d['schedules'])} ta"), kb


def scr_mod(d):
    kb = M([[tgl(d, "mod", "Guruh moderatori")],
            [tgl(d, "welcome", "Yangi a'zoga salom")],
            [B("➕ Taqiqlangan so'z", callback_data="bw_add"), B("🗑 Tozalash", callback_data="bw_clear")],
            [B("⬅️ Orqaga", callback_data="menu")]])
    return (f"Moderator: spam, havola, reklama, haqorat va flood o'chiriladi; "
            f"3 ta ogohlantirishdan keyin 1 soat mute.\n"
            f"Taqiqlangan so'zlar: {', '.join(d['banwords']) or '—'}"), kb


def scr_daily(d):
    kb = M([[tgl(d, "d_kurs", "Valyuta kursi"), tgl(d, "d_ob", "Ob-havo")],
            [tgl(d, "d_namoz", "Namoz vaqti"), tgl(d, "d_hikmat", "Kun hikmati")],
            [B("🕐 Vaqt", callback_data="dtime"), B("🏙 Shahar", callback_data="city")],
            [B("▶️ Hozir yuborish", callback_data="dnow")],
            [B("⬅️ Orqaga", callback_data="menu")]])
    return f"Har kuni {d['daily_time']} da bitta post bo'lib chiqadi.\nShahar: {d['city']}", kb


def scr_quiz(d):
    kb = M([[tgl(d, "quiz", "AI viktorina")],
            [B("▶️ Hozir yuborish", callback_data="qnow")],
            [B("⬅️ Orqaga", callback_data="menu")]])
    return (f"Mavzu: {d['topic'] or 'umumiy bilimlar'}\n"
            f"Har {d['quiz_hours']} soatda yangi savol (poll) joylanadi."), kb


def scr_gv(d):
    rows = []
    for gid, g in d["giveaways"].items():
        if g["open"]:
            rows.append([B(f"🏆 {g['prize'][:20]} ({len(g['users'])})", callback_data=f"gend:{gid}")])
    rows.append([B("➕ Yangi konkurs", callback_data="gnew")])
    rows.append([B("⬅️ Orqaga", callback_data="menu")])
    return "Ochiq konkurslar (tugmani bossangiz g'olib tanlanadi):", M(rows)


def scr_ad(d):
    kb = M([[B("✏️ Narx matnini o'zgartirish", callback_data="adtext")],
            [B("⬅️ Orqaga", callback_data="menu")]])
    return f"Foydalanuvchilarga ko'rsatiladigan matn:\n\n{d['ad_text']}", kb


def user_menu():
    return M([[B("📢 Reklama narxi", callback_data="u_ad")],
              [B("🤖 AI ga savol berish", callback_data="u_ai")]])


async def show(u, text, kb=None):
    try:
        await u.callback_query.edit_message_text(text, reply_markup=kb)
    except Exception:
        pass


# ================= yordamchilar =================
async def subscribed(bot, uid):
    try:
        m = await bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in ("member", "administrator", "creator", "restricted")
    except Exception:
        return True  # tekshirib bo'lmasa to'smaymiz


def join_kb():
    rows = []
    if CHANNEL_LINK:
        rows.append([B("📢 Kanalga o'tish", url=CHANNEL_LINK)])
    rows.append([B("✅ Tekshirish", callback_data="chk")])
    return M(rows)


async def gate(bot, uid, d):
    return (not d["force_sub"]) or await subscribed(bot, uid)


async def is_group_admin(bot, chat_id, uid):
    try:
        m = await bot.get_chat_member(chat_id, uid)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


# ================= AI post / tarjima =================
async def preview(c, text, send, regen=True):
    c.user_data["pending"] = text
    row2 = [B("❌ Bekor", callback_data="menu")]
    if regen:
        row2.insert(0, B("🔄 Qayta yozish", callback_data="regen"))
    await send(text, M([[B("✅ Kanalga joylash", callback_data="pub")], row2]))


def ai_post_prompt(d):
    return f"Telegram kanal uchun '{d['topic']}' mavzusida qiziqarli, qisqa o'zbekcha post yoz. Emoji ishlat."


async def make_preview(c, send):
    d = load()
    await preview(c, await ask_ai(ai_post_prompt(d)), send)


# ================= jobs =================
async def send_job(ctx):
    await ctx.bot.send_message(CHANNEL_ID, ctx.job.data)


def reg(jq, s):
    hh, mm = map(int, s["time"].split(":"))
    jq.run_daily(send_job, datetime.time(hh, mm, tzinfo=TZ), data=s["text"], name=f"sch{s['id']}")


async def ai_job(ctx):
    d = load()
    if d["auto_ai"] and d["topic"]:
        await ctx.bot.send_message(CHANNEL_ID, await ask_ai(ai_post_prompt(d)))


async def rss_job(ctx):
    d = load()
    if not d["auto_rss"]:
        return
    new_seen = []
    for url in d["sources"]:
        for e in feedparser.parse(url).entries[:3]:
            if e.link in d["seen"] or e.link in new_seen:
                continue
            new_seen.append(e.link)
            t = await ask_ai(f"Quyidagi yangilikni o'zbek tiliga qisqa tarjima qilib, kanal posti ko'rinishida yoz:\n{e.title}\n{e.get('summary', '')}")
            await ctx.bot.send_message(CHANNEL_ID, f"{t}\n\n{e.link}")
    d = load()  # admin bu orada sozlamani o'zgartirgan bo'lishi mumkin
    d["seen"] = (d["seen"] + new_seen)[-500:]
    save(d)


# ----- kunlik post -----
async def build_daily(d):
    parts = ["🌅 Xayrli tong!"]
    async with httpx.AsyncClient(timeout=15) as h:
        if d["d_kurs"]:
            try:
                rates = {x["Ccy"]: x for x in (await h.get("https://cbu.uz/uz/arkhiv-kursov-valyut/json/")).json()}
                lines = [f"{c}: {float(rates[c]['Rate']):,.2f} so'm".replace(",", " ")
                         for c in ("USD", "EUR", "RUB") if c in rates]
                parts.append("💵 Valyuta kursi (MB):\n" + "\n".join(lines))
            except Exception:
                logging.exception("kurs")
        if d["d_ob"]:
            try:
                g = (await h.get("https://geocoding-api.open-meteo.com/v1/search",
                                 params={"name": d["city"], "count": 1})).json()["results"][0]
                w = (await h.get("https://api.open-meteo.com/v1/forecast", params={
                    "latitude": g["latitude"], "longitude": g["longitude"], "timezone": "auto",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max"})).json()["daily"]
                parts.append(f"🌤 {d['city']} ob-havosi: {w['temperature_2m_min'][0]:.0f}° … "
                             f"{w['temperature_2m_max'][0]:.0f}°C, yog'ingarchilik {w['precipitation_probability_max'][0]}%")
            except Exception:
                logging.exception("ob-havo")
        if d["d_namoz"]:
            try:
                t = (await h.get("https://api.aladhan.com/v1/timingsByCity", params={
                    "city": d["city"], "country": "Uzbekistan", "method": 2, "school": 1})).json()["data"]["timings"]
                parts.append(f"🕌 Namoz vaqtlari ({d['city']}):\nBomdod {t['Fajr']} · Quyosh {t['Sunrise']} · "
                             f"Peshin {t['Dhuhr']}\nAsr {t['Asr']} · Shom {t['Maghrib']} · Xufton {t['Isha']}")
            except Exception:
                logging.exception("namoz")
    if d["d_hikmat"]:
        try:
            parts.append("💡 " + await ask_ai(
                "Bugun uchun bitta qisqa ilhomlantiruvchi fikr yoz (o'zbekcha, 1-2 gap). "
                "Muallif ko'rsatma, faqat matnning o'zi.", max_tokens=200))
        except Exception:
            logging.exception("hikmat")
    return "\n\n".join(parts) if len(parts) > 1 else None


async def daily_job(ctx):
    t = await build_daily(load())
    if t:
        await ctx.bot.send_message(CHANNEL_ID, t)


def reg_daily(jq, d):
    for j in jq.get_jobs_by_name("daily"):
        j.schedule_removal()
    hh, mm = map(int, d["daily_time"].split(":"))
    jq.run_daily(daily_job, datetime.time(hh, mm, tzinfo=TZ), name="daily")


# ----- viktorina -----
async def send_quiz(bot):
    d = load()
    topic = d["topic"] or "umumiy bilimlar"
    raw = await ask_ai(
        f"Telegram viktorinasi uchun '{topic}' mavzusida bitta o'zbekcha savol tuz. "
        'FAQAT JSON qaytar: {"q":"savol","correct":"to\'g\'ri javob","wrong":["xato1","xato2","xato3"],"explain":"qisqa izoh"}. '
        "Har bir javob 90 belgidan qisqa bo'lsin.", max_tokens=500)
    j = parse_json(raw)
    opts = [str(j["correct"])[:100]] + [str(x)[:100] for x in j["wrong"][:3]]
    random.shuffle(opts)
    await bot.send_poll(CHANNEL_ID, str(j["q"])[:300], opts, type="quiz",
                        correct_option_id=opts.index(str(j["correct"])[:100]),
                        explanation=str(j.get("explain", ""))[:200], is_anonymous=True)


async def quiz_job(ctx):
    if load()["quiz"]:
        try:
            await send_quiz(ctx.bot)
        except Exception:
            logging.exception("quiz")


# ================= tugmalar =================
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    c.user_data["mode"] = c.user_data["umode"] = None
    if u.effective_user.id == ADMIN_ID:
        await u.message.reply_text("Kanal boshqaruv paneli 👇", reply_markup=menu())
        return
    d = load()
    if not await gate(c.bot, u.effective_user.id, d):
        await u.message.reply_text("Botdan foydalanish uchun avval kanalga obuna bo'ling 👇", reply_markup=join_kb())
        return
    await u.message.reply_text("Salom! Nima kerak? 👇", reply_markup=user_menu())


async def public_cb(u, c, x):
    """Admin bo'lmaganlar ham bosadigan tugmalar."""
    q = u.callback_query
    uid = q.from_user.id
    d = load()

    if x.startswith("gj:"):
        gid = x[3:]
        g = d["giveaways"].get(gid)
        if not g or not g["open"]:
            return await q.answer("Konkurs tugagan", show_alert=True)
        if not await subscribed(c.bot, uid):
            return await q.answer("Avval kanalga obuna bo'ling", show_alert=True)
        if uid in g["users"]:
            return await q.answer("Siz allaqachon qatnashyapsiz ✅", show_alert=True)
        g["users"].append(uid)
        save(d)
        await q.answer("Qatnashdingiz ✅", show_alert=True)
        try:
            await q.edit_message_reply_markup(M([[B(f"🎁 Qatnashish ({len(g['users'])})", callback_data=f"gj:{gid}")]]))
        except Exception:
            pass
        return

    if x == "chk":
        if await subscribed(c.bot, uid):
            await q.answer()
            await show(u, "Rahmat! Nima kerak? 👇", user_menu())
        else:
            await q.answer("Hali obuna bo'lmadingiz", show_alert=True)
        return

    await q.answer()
    if not await gate(c.bot, uid, d):
        return await show(u, "Avval kanalga obuna bo'ling 👇", join_kb())
    if x == "u_ad":
        c.user_data["umode"] = "ad"
        await show(u, f"{d['ad_text']}\n\nBuyurtma yoki savolingizni shu yerga yozing 👇")
    elif x == "u_ai":
        c.user_data["umode"] = "uai"
        await show(u, "Savolingizni yozing 👇")


async def cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    x = q.data
    if x.startswith(("gj:", "u_")) or x == "chk":
        return await public_cb(u, c, x)

    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    d = load()
    c.user_data["mode"] = None

    if x == "menu":
        await show(u, "Kanal boshqaruv paneli 👇", menu())

    elif x == "post":
        c.user_data["mode"] = "post"
        await show(u, "Postni yuboring (matn, rasm yoki video) 👇", back())

    # --- jadval ---
    elif x == "jadval":
        rows = [[B(f"❌ {s['time']} - {s['text'][:20]}", callback_data=f"jdel:{s['id']}")] for s in d["schedules"]]
        rows.append([B("➕ Yangi jadval", callback_data="jadd")])
        rows.append([B("⬅️ Orqaga", callback_data="menu")])
        await show(u, "Har kuni joylanadigan postlar:" if d["schedules"] else "Jadval bo'sh.", M(rows))
    elif x == "jadd":
        c.user_data["mode"] = "jt"
        await show(u, "Vaqtni yozing, masalan: 09:30", back())
    elif x.startswith("jdel:"):
        sid = int(x[5:])
        for j in c.job_queue.get_jobs_by_name(f"sch{sid}"):
            j.schedule_removal()
        d["schedules"] = [s for s in d["schedules"] if s["id"] != sid]
        save(d)
        await show(u, "O'chirildi ✅", M([[B("⬅️ Jadvalga", callback_data="jadval")]]))

    # --- AI post ---
    elif x == "ai":
        if not d["topic"]:
            c.user_data["mode"] = "mavzu_ai"
            await show(u, "Avval mavzuni yozing (masalan: futbol yangiliklari) 👇", back())
        else:
            await show(u, "⏳ Yozilmoqda...")
            await make_preview(c, lambda t, k: show(u, t, k))
    elif x == "regen":
        await show(u, "⏳ Yozilmoqda...")
        await make_preview(c, lambda t, k: show(u, t, k))
    elif x == "pub":
        t = c.user_data.pop("pending", None)
        if t:
            await c.bot.send_message(CHANNEL_ID, t)
            await show(u, "✅ Kanalga joylandi", menu())
    elif x == "mavzu":
        c.user_data["mode"] = "mavzu"
        await show(u, f"Hozirgi mavzu: {d['topic'] or '—'}\n\nYangi mavzuni yozing 👇", back())

    # --- tarjima ---
    elif x == "tr":
        c.user_data["mode"] = "tr"
        await show(u, "Tarjima qilinadigan postni yozing (istalgan tilda).\n"
                      "Natija 🇺🇿 🇷🇺 🇬🇧 uch tilda chiqadi 👇", back())

    # --- konkurs ---
    elif x == "gv":
        await show(u, *scr_gv(d))
    elif x == "gnew":
        c.user_data["mode"] = "gv"
        await show(u, "Sovg'a nima? Qisqa yozing (masalan: 100 000 so'm)", back("gv"))
    elif x.startswith("gend:"):
        gid = x[5:]
        g = d["giveaways"].get(gid)
        if g and g["open"]:
            g["open"] = False
            save(d)
            if not g["users"]:
                await c.bot.send_message(CHANNEL_ID, f"🎁 Konkurs ({g['prize']}) tugadi, qatnashchi bo'lmadi.")
            else:
                w = random.choice(g["users"])
                try:
                    name = (await c.bot.get_chat_member(CHANNEL_ID, w)).user.full_name
                except Exception:
                    name = "G'olib"
                await c.bot.send_message(
                    CHANNEL_ID,
                    f"🏆 Konkurs yakunlandi!\n\nSovg'a: {html.escape(g['prize'])}\n"
                    f"Qatnashchilar: {len(g['users'])}\n"
                    f"G'olib: <a href=\"tg://user?id={w}\">{html.escape(name)}</a> 🎉",
                    parse_mode=ParseMode.HTML)
        await show(u, "✅ G'olib e'lon qilindi", M([[B("⬅️ Orqaga", callback_data="gv")]]))

    # --- kunlik ---
    elif x == "daily":
        await show(u, *scr_daily(d))
    elif x == "dtime":
        c.user_data["mode"] = "dtime"
        await show(u, "Vaqtni yozing, masalan: 08:00", back("daily"))
    elif x == "city":
        c.user_data["mode"] = "city"
        await show(u, "Shahar nomini inglizcha yozing (Tashkent, Samarkand, Bukhara...)", back("daily"))
    elif x == "dnow":
        t = await build_daily(d)
        if t:
            await c.bot.send_message(CHANNEL_ID, t)
        await show(u, "✅ Yuborildi" if t else "Avval kamida bitta bo'limni yoqing", M([[B("⬅️ Orqaga", callback_data="daily")]]))

    # --- viktorina ---
    elif x == "quiz":
        await show(u, *scr_quiz(d))
    elif x == "qnow":
        await show(u, "⏳ Savol tuzilmoqda...")
        try:
            await send_quiz(c.bot)
            await show(u, "✅ Viktorina joylandi", M([[B("⬅️ Orqaga", callback_data="quiz")]]))
        except Exception:
            logging.exception("quiz")
            await show(u, "❌ Xatolik, qayta urinib ko'ring", M([[B("⬅️ Orqaga", callback_data="quiz")]]))

    # --- moderator ---
    elif x == "mod":
        await show(u, *scr_mod(d))
    elif x == "bw_add":
        c.user_data["mode"] = "bw"
        await show(u, "Taqiqlangan so'z(lar)ni yozing (vergul bilan ajrating)", back("mod"))
    elif x == "bw_clear":
        d["banwords"] = []
        save(d)
        await show(u, *scr_mod(d))

    # --- reklama ---
    elif x == "ad":
        await show(u, *scr_ad(d))
    elif x == "adtext":
        c.user_data["mode"] = "adtext"
        await show(u, "Reklama narxi/shartlari matnini yozing 👇", back("ad"))

    # --- manbalar ---
    elif x == "manba":
        rows = [[B(f"❌ {s[:35]}", callback_data=f"del:{i}")] for i, s in enumerate(d["sources"])]
        rows.append([B("➕ Manba qo'shish", callback_data="addsrc")])
        rows.append([B("⬅️ Orqaga", callback_data="menu")])
        await show(u, "RSS manbalar:" if d["sources"] else "Manba yo'q.", M(rows))
    elif x == "addsrc":
        c.user_data["mode"] = "manba"
        await show(u, "RSS havolasini yuboring (masalan https://kun.uz/news/rss)", back())
    elif x.startswith("del:"):
        i = int(x[4:])
        if i < len(d["sources"]):
            d["sources"].pop(i)
            save(d)
        await show(u, "O'chirildi ✅", M([[B("⬅️ Manbalarga", callback_data="manba")]]))

    # --- sozlamalar / toggle ---
    elif x == "set":
        await show(u, *scr_set(d))
    elif x.startswith("tg:"):
        k = x[3:]
        if k in DEFAULTS and isinstance(DEFAULTS[k], bool):
            d[k] = not d[k]
            save(d)
        if k.startswith("d_"):
            await show(u, *scr_daily(d))
        elif k in ("mod", "welcome"):
            await show(u, *scr_mod(d))
        elif k == "quiz":
            await show(u, *scr_quiz(d))
        else:
            await show(u, *scr_set(d))


# ================= admin xabarlari =================
async def on_msg(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = u.message
    mode = c.user_data.get("mode")
    d = load()

    if mode == "post":
        await c.bot.copy_message(CHANNEL_ID, m.chat_id, m.message_id)
        c.user_data["mode"] = None
        await m.reply_text("✅ Joylandi", reply_markup=menu())

    elif mode == "jt":
        try:
            hh, mm = map(int, (m.text or "").strip().split(":"))
            assert 0 <= hh < 24 and 0 <= mm < 60
        except Exception:
            await m.reply_text("Format noto'g'ri. Masalan: 09:30")
            return
        c.user_data["jt"] = f"{hh:02d}:{mm:02d}"
        c.user_data["mode"] = "jx"
        await m.reply_text("Endi shu vaqtda joylanadigan matnni yozing 👇")

    elif mode == "jx":
        s = {"id": int(time.time()), "time": c.user_data["jt"], "text": m.text or ""}
        d["schedules"].append(s)
        save(d)
        reg(c.job_queue, s)
        c.user_data["mode"] = None
        await m.reply_text(f"✅ Har kuni {s['time']} da joylanadi", reply_markup=menu())

    elif mode == "manba":
        if not (m.text or "").startswith("http"):
            await m.reply_text("Havola http bilan boshlanishi kerak")
            return
        d["sources"].append(m.text.strip())
        save(d)
        c.user_data["mode"] = None
        await m.reply_text("✅ Manba qo'shildi", reply_markup=menu())

    elif mode in ("mavzu", "mavzu_ai"):
        d["topic"] = (m.text or "").strip()
        save(d)
        c.user_data["mode"] = None
        if mode == "mavzu_ai":
            await m.reply_text("⏳ Yozilmoqda...")
            await make_preview(c, lambda t, k: m.reply_text(t, reply_markup=k))
        else:
            await m.reply_text("✅ Mavzu saqlandi", reply_markup=menu())

    elif mode == "tr":
        c.user_data["mode"] = None
        await m.reply_text("⏳ Tarjima qilinmoqda...")
        t = await ask_ai(
            "Quyidagi Telegram postini uch tilda yoz: avval 🇺🇿 o'zbekcha, keyin 🇷🇺 ruscha, keyin 🇬🇧 inglizcha. "
            "Har bir blok bayroq belgisi bilan boshlansin, ma'no, uslub va emojilar saqlansin. "
            f"Boshqa izoh yozma.\n\n{m.text or m.caption or ''}", max_tokens=1500)
        await preview(c, t, lambda tt, k: m.reply_text(tt, reply_markup=k), regen=False)

    elif mode == "gv":
        gid = str(int(time.time()))
        prize = (m.text or "").strip()
        d["giveaways"][gid] = {"prize": prize, "users": [], "open": True}
        save(d)
        c.user_data["mode"] = None
        await c.bot.send_message(
            CHANNEL_ID, f"🎁 KONKURS!\n\nSovg'a: {prize}\n\nQatnashish uchun pastdagi tugmani bosing 👇",
            reply_markup=M([[B("🎁 Qatnashish (0)", callback_data=f"gj:{gid}")]]))
        await m.reply_text("✅ Konkurs kanalga joylandi", reply_markup=menu())

    elif mode == "dtime":
        try:
            hh, mm = map(int, (m.text or "").strip().split(":"))
            assert 0 <= hh < 24 and 0 <= mm < 60
        except Exception:
            await m.reply_text("Format noto'g'ri. Masalan: 08:00")
            return
        d["daily_time"] = f"{hh:02d}:{mm:02d}"
        save(d)
        reg_daily(c.job_queue, d)
        c.user_data["mode"] = None
        await m.reply_text(f"✅ Kunlik post {d['daily_time']} ga o'rnatildi", reply_markup=menu())

    elif mode == "city":
        d["city"] = (m.text or "").strip() or d["city"]
        save(d)
        c.user_data["mode"] = None
        await m.reply_text(f"✅ Shahar: {d['city']}", reply_markup=menu())

    elif mode == "bw":
        words = [w.strip().lower() for w in (m.text or "").split(",") if w.strip()]
        d["banwords"] = sorted(set(d["banwords"] + words))
        save(d)
        c.user_data["mode"] = None
        await m.reply_text(f"✅ Qo'shildi ({len(words)} ta)", reply_markup=menu())

    elif mode == "adtext":
        d["ad_text"] = (m.text or "").strip() or d["ad_text"]
        save(d)
        c.user_data["mode"] = None
        await m.reply_text("✅ Saqlandi", reply_markup=menu())

    else:
        await m.reply_text("Menyudan tanlang 👇", reply_markup=menu())


# ================= oddiy foydalanuvchi (shaxsiy chat) =================
async def on_user(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m, usr = u.message, u.effective_user
    d = load()
    if not await gate(c.bot, usr.id, d):
        await m.reply_text("Avval kanalga obuna bo'ling 👇", reply_markup=join_kb())
        return
    mode = c.user_data.get("umode")

    if mode == "ad":
        who = f'<a href="tg://user?id={usr.id}">{html.escape(usr.full_name)}</a>'
        uname = f" (@{usr.username})" if usr.username else ""
        await c.bot.send_message(ADMIN_ID, f"📩 Reklama so'rovi\nKimdan: {who}{uname}\nID: {usr.id}",
                                 parse_mode=ParseMode.HTML)
        await c.bot.copy_message(ADMIN_ID, m.chat_id, m.message_id)
        c.user_data["umode"] = None
        await m.reply_text("✅ So'rovingiz adminga yuborildi, tez orada javob beriladi.", reply_markup=user_menu())

    elif mode == "uai" and m.text:
        if time.time() - user_ai_cd.get(usr.id, 0) < 8:
            await m.reply_text("Biroz kuting ⏳")
            return
        user_ai_cd[usr.id] = time.time()
        a = await ask_ai(m.text, system="Sen Telegram kanal yordamchisisan. Qisqa, aniq, do'stona javob ber. Foydalanuvchi qaysi tilda yozsa, shu tilda javob ber.")
        await m.reply_text(a)

    else:
        await m.reply_text("Nima kerak? 👇", reply_markup=user_menu())


async def on_private(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id == ADMIN_ID:
        await on_msg(u, c)
    else:
        await on_user(u, c)


# ================= guruh: moderator + AI javob =================
LINK_RE = re.compile(r"(https?://|www\.|t\.me/|\b[\w-]+\.(?:com|ru|uz|org|net|me|io|xyz|bet)\b)", re.I)


async def on_group(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m, usr, chat = u.effective_message, u.effective_user, u.effective_chat
    if not m or not usr or usr.is_bot or m.sender_chat or m.is_automatic_forward:
        return
    if GROUP_ID and chat.id != GROUP_ID:
        return
    d = load()
    text = m.text or m.caption or ""
    low = text.lower()

    reason = None
    if d["mod"]:
        ents = (m.entities or ()) + (m.caption_entities or ())
        fl = flood[(chat.id, usr.id)]
        fl.append(time.time())
        if any(e.type in ("url", "text_link") for e in ents) or LINK_RE.search(text):
            reason = "havola/reklama"
        elif any(w in low for w in d["banwords"]):
            reason = "taqiqlangan so'z"
        elif len(fl) >= 6 and fl[-1] - fl[0] < 10:
            reason = "flood"
    if not reason and d["force_sub"] and not await subscribed(c.bot, usr.id):
        reason = "kanalga obuna bo'lmagan"

    if reason:
        if usr.id == ADMIN_ID or await is_group_admin(c.bot, chat.id, usr.id):
            return
        try:
            await m.delete()
        except Exception:
            pass
        k = (chat.id, usr.id)
        strikes[k] += 1
        if strikes[k] >= 3:
            strikes[k] = 0
            try:
                await c.bot.restrict_chat_member(
                    chat.id, usr.id, ChatPermissions(can_send_messages=False),
                    until_date=datetime.datetime.now(TZ) + datetime.timedelta(hours=1))
                note = f"🔇 {usr.mention_html()} 1 soatga cheklandi ({reason})."
            except Exception:
                note = f"⚠️ {usr.mention_html()}, qoidani buzmang ({reason})."
        else:
            note = f"⚠️ {usr.mention_html()}, xabaringiz o'chirildi: {reason}. ({strikes[k]}/3)"
        w = await c.bot.send_message(chat.id, note, parse_mode=ParseMode.HTML)
        c.job_queue.run_once(lambda ctx: w.delete(), 10)
        return

    # AI javob
    if d["ai_reply"] and len(text) > 8 and time.time() - last_ai_reply["t"] > 15:
        to_bot = bool(m.reply_to_message and m.reply_to_message.from_user
                      and m.reply_to_message.from_user.id == c.bot.id)
        if "?" in text or to_bot:
            last_ai_reply["t"] = time.time()
            try:
                a = await ask_ai(
                    text, system="Sen Telegram kanal izohlaridagi yordamchisan. Savolga qisqa (3-4 gap), aniq, "
                                 "do'stona javob ber. Bilmasang, o'ylab topma. Foydalanuvchi tilida javob ber.")
                await m.reply_text(a)
            except Exception:
                logging.exception("ai_reply")


async def on_join(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = u.effective_message
    if GROUP_ID and u.effective_chat.id != GROUP_ID:
        return
    d = load()
    if d["mod"]:
        try:
            await m.delete()  # "X qo'shildi" servis xabarini o'chirish
        except Exception:
            pass
    if d["welcome"]:
        names = ", ".join(x.mention_html() for x in m.new_chat_members if not x.is_bot)
        if names:
            w = await c.bot.send_message(u.effective_chat.id,
                                         f"👋 Xush kelibsiz, {names}! Qoidalarga rioya qiling.",
                                         parse_mode=ParseMode.HTML)
            c.job_queue.run_once(lambda ctx: w.delete(), 60)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS & filters.ChatType.GROUPS, on_join))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.ALL & ~filters.COMMAND, on_private))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL & ~filters.COMMAND, on_group))
    d = load()
    for s in d["schedules"]:
        reg(app.job_queue, s)
    reg_daily(app.job_queue, d)
    app.job_queue.run_repeating(ai_job, interval=d["ai_hours"] * 3600, first=120)
    app.job_queue.run_repeating(rss_job, interval=d["rss_min"] * 60, first=60)
    app.job_queue.run_repeating(quiz_job, interval=d["quiz_hours"] * 3600, first=300)
    app.run_polling()


if __name__ == "__main__":
    main()
