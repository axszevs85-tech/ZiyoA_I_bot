import os, re, json, time, html, copy, random, asyncio, datetime, logging
import httpx
import feedparser
from anthropic import AsyncAnthropic
from telegram import Update, ChatPermissions, InlineKeyboardButton as B, InlineKeyboardMarkup as M
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
GROUP_ID = os.environ.get("GROUP_ID", "")          # ixtiyoriy: faqat shu guruhda ishlaydi
GROUP_URL = os.environ.get("GROUP_URL", "")        # umumiy AI guruhining taklif havolasi
CHANNEL_URL = os.environ.get("CHANNEL_URL") or (
    f"https://t.me/{CHANNEL_ID[1:]}" if CHANNEL_ID.startswith("@") else "")
ai = AsyncAnthropic()
MODEL = "claude-haiku-5-5"
DB = "data.json"
TZ = datetime.timezone(datetime.timedelta(hours=5))

CITIES = {
    "Toshkent": ("Tashkent", 41.31, 69.28), "Samarqand": ("Samarkand", 39.65, 66.96),
    "Buxoro": ("Bukhara", 39.77, 64.42), "Andijon": ("Andijan", 40.78, 72.34),
    "Farg'ona": ("Fergana", 40.38, 71.79), "Namangan": ("Namangan", 41.0, 71.67),
    "Qarshi": ("Karshi", 38.86, 65.79), "Nukus": ("Nukus", 42.46, 59.61),
    "Urganch": ("Urgench", 41.55, 60.63), "Navoiy": ("Navoi", 40.1, 65.37),
    "Jizzax": ("Jizzakh", 40.12, 67.84), "Termiz": ("Termez", 37.22, 67.28),
    "Guliston": ("Gulistan", 40.49, 68.78),
}
CITY_NAMES = list(CITIES)

SPAM = ["1xbet", "1xbit", "казино", "casino", "ставки", "stavka", "pul ishlang", "daromad topish",
        "bepul pul", "obuna bo'ling", "подписывайся", "заработок", "kripto signal",
        "investitsiya qiling", "pulni 2 barobar"]
LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|\b[\w-]+\.(?:com|ru|org|net|io|me|xyz|top)\b)", re.I)

DEFAULTS = {
    "sources": [], "seen": [], "topic": "", "schedules": [],
    "auto_ai": False, "auto_rss": True, "ai_hours": 6, "rss_min": 30,
    "users": [], "stats": {"posts": 0, "giveaways": 0, "ads": 0},
    "mhist": {}, "react": {}, "react_on": False, "force_sub": True, "auto_backup": False,
    "mod": True, "ai_reply": True, "welcome_on": True,
    "welcome": "Xush kelibsiz, {name}! 👋\nGuruh qoidalari: reklama, havola va so'kinish taqiqlanadi.",
    "badwords": ["ahmoq", "tentak", "jinni", "yaramas", "iflos", "аҳмоқ", "дурак", "идиот", "тупой"],
    "gw": None,
    "daily_on": False, "daily_time": "07:00", "daily_city": "Toshkent",
    "daily_parts": {"kurs": True, "havo": True, "namoz": True, "hikmat": True},
    "quiz_on": False, "quiz_time": "18:00", "quiz_topic": "",
    "delayed": [],
    "ad_price": "💰 Reklama narxlari:\n• 1 ta post — kelishiladi\n\nAdmin narxni belgilaydi.",
}

WARN, FLOOD, AIRATE = {}, {}, {}


# ---------- db ----------
def load():
    d = copy.deepcopy(DEFAULTS)
    if os.path.exists(DB):
        d.update(json.load(open(DB, encoding="utf-8")))
    return d


def save(d):
    json.dump(d, open(DB, "w", encoding="utf-8"), ensure_ascii=False)


def inc(d, k):
    d["stats"][k] = d["stats"].get(k, 0) + 1


def track(uid):
    d = load()
    if uid not in d["users"]:
        d["users"].append(uid)
        save(d)


# ---------- helpers ----------
async def ask_ai(prompt, tokens=700):
    r = await ai.messages.create(model=MODEL, max_tokens=tokens,
                                 messages=[{"role": "user", "content": prompt}])
    return r.content[0].text


def jparse(t):
    return json.loads(t[t.index("{"): t.rindex("}") + 1])


async def jget(url, **params):
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as h:
        r = await h.get(url, params=params or None)
        r.raise_for_status()
        return r.json()


def parse_hm(s):
    try:
        hh, mm = map(int, (s or "").strip().split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60
        return f"{hh:02d}:{mm:02d}"
    except Exception:
        return None


def num(v):
    return f"{v:,.2f}".replace(",", " ")


def react_kb(c=None):
    c = c or {"up": 0, "fire": 0, "down": 0}
    return M([[B(f"👍 {c['up']}", callback_data="r:up"), B(f"🔥 {c['fire']}", callback_data="r:fire"),
               B(f"👎 {c['down']}", callback_data="r:down")]])


async def publish(bot, text):
    d = load()
    m = await bot.send_message(CHANNEL_ID, text, reply_markup=react_kb() if d["react_on"] else None)
    d = load()
    inc(d, "posts")
    save(d)
    return m


async def publish_copy(bot, chat_id, msg_id):
    d = load()
    await bot.copy_message(CHANNEL_ID, chat_id, msg_id, reply_markup=react_kb() if d["react_on"] else None)
    d = load()
    inc(d, "posts")
    save(d)


async def del_job(ctx):
    chat, mid = ctx.job.data
    try:
        await ctx.bot.delete_message(chat, mid)
    except Exception:
        pass


def tmp(c, chat, mid, sec=20):
    c.job_queue.run_once(del_job, sec, data=(chat, mid))


# ---------- majburiy obuna ----------
async def is_sub(bot, uid):
    try:
        m = await bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in ("member", "administrator", "creator") or \
            (m.status == "restricted" and getattr(m, "is_member", False))
    except Exception:
        logging.warning("Obunani tekshirib bo'lmadi (bot kanalda admin emasmi?)")
        return True


async def gate(u, c):
    d = load()
    uid = u.effective_user.id
    if uid == ADMIN_ID or not d["force_sub"] or await is_sub(c.bot, uid):
        return True
    rows = []
    if CHANNEL_URL:
        rows.append([B("📢 Kanalga obuna bo'lish", url=CHANNEL_URL)])
    rows.append([B("✅ Tekshirish", callback_data="chk")])
    txt = "Botdan foydalanish uchun avval kanalimizga a'zo bo'ling 👇"
    target = u.callback_query.message if u.callback_query else u.message
    await target.reply_text(txt, reply_markup=M(rows))
    return False


# ---------- valyuta / ob-havo / namoz / hikmat ----------
async def rates():
    data = await jget("https://cbu.uz/uz/arkhiv-kursov-valyut/json/")
    return {x["Ccy"]: (float(x["Rate"]) / float(x.get("Nominal") or 1), float(x.get("Diff") or 0)) for x in data}


def kurs_fmt(r):
    lines = ["💱 Valyuta kursi (Markaziy bank)"]
    for ccy, flag in (("USD", "🇺🇸"), ("EUR", "🇪🇺"), ("RUB", "🇷🇺")):
        if ccy in r:
            v, df = r[ccy]
            ar = "🔺" if df > 0 else "🔻" if df < 0 else "➖"
            lines.append(f"{flag} 1 {ccy} = {num(v)} so'm {ar}")
    return "\n".join(lines)


async def kurs_text():
    return kurs_fmt(await rates())


def wdesc(c):
    if c == 0: return "☀️ Ochiq"
    if c in (1, 2): return "🌤 Qisman bulutli"
    if c == 3: return "☁️ Bulutli"
    if c in (45, 48): return "🌫 Tuman"
    if 51 <= c <= 57: return "🌦 Mayda yomg'ir"
    if 61 <= c <= 67 or 80 <= c <= 82: return "🌧 Yomg'ir"
    if 71 <= c <= 77 or c in (85, 86): return "❄️ Qor"
    if c >= 95: return "⛈ Momaqaldiroq"
    return "🌡 Noaniq"


async def weather_text(city):
    _, lat, lon = CITIES[city]
    j = await jget("https://api.open-meteo.com/v1/forecast", latitude=lat, longitude=lon,
                   current="temperature_2m,weather_code", daily="temperature_2m_max,temperature_2m_min",
                   timezone="Asia/Tashkent", forecast_days=1)
    cur, dl = j["current"], j["daily"]
    return (f"🌤 {city} ob-havosi\n{wdesc(int(cur['weather_code']))}, hozir {cur['temperature_2m']:.0f}°\n"
            f"Kun davomida: {dl['temperature_2m_min'][0]:.0f}° … {dl['temperature_2m_max'][0]:.0f}°")


async def prayer_text(city):
    en = CITIES[city][0]
    j = await jget("https://api.aladhan.com/v1/timingsByCity", city=en, country="Uzbekistan", method=3, school=1)
    t = j["data"]["timings"]
    names = [("Fajr", "Bomdod"), ("Sunrise", "Quyosh"), ("Dhuhr", "Peshin"),
             ("Asr", "Asr"), ("Maghrib", "Shom"), ("Isha", "Xufton")]
    return f"🕌 {city} namoz vaqtlari\n" + "\n".join(f"{u}: {t[k][:5]}" for k, u in names)


async def hikmat_text():
    t = await ask_ai("Bitta qisqa, ma'noli hikmatli so'z yoki maqolni o'zbek tilida yoz va 1 jumlada izohla. "
                     "Faqat matnni yoz.", 250)
    return f"💡 Kun hikmati\n{t.strip()}"


async def build_daily(d):
    p, city = d["daily_parts"], d["daily_city"]
    out = [f"🌅 Xayrli tong! Bugun {datetime.datetime.now(TZ).strftime('%d.%m.%Y')}"]
    for key, fn in (("kurs", kurs_text), ("havo", lambda: weather_text(city)),
                    ("namoz", lambda: prayer_text(city)), ("hikmat", hikmat_text)):
        if p.get(key):
            try:
                out.append(await fn())
            except Exception:
                logging.exception("daily: %s", key)
    return "\n\n".join(out)


# ---------- viktorina ----------
async def send_quiz(bot, topic):
    t = await ask_ai(
        f"'{topic}' mavzusida bitta qiziqarli viktorina savoli tuz (o'zbek tilida). Faqat JSON qaytar: "
        '{"q":"savol","opts":["a","b","c","d"],"ans":0,"exp":"qisqa izoh"}. '
        "ans — to'g'ri variant indeksi (0-3), to'g'ri javob joyi tasodifiy bo'lsin.", 500)
    j = jparse(t)
    opts = [str(o)[:100] for o in j["opts"]][:4]
    ans = int(j["ans"])
    assert len(opts) >= 2 and 0 <= ans < len(opts)
    await bot.send_poll(CHANNEL_ID, ("🧠 " + str(j["q"]))[:290], opts, type="quiz",
                        correct_option_id=ans, explanation=str(j.get("exp", ""))[:195], is_anonymous=True)
    d = load()
    inc(d, "posts")
    save(d)


# ---------- menus ----------
def menu():
    return M([
        [B("📝 Post joylash", callback_data="post"), B("⏰ Jadval", callback_data="jadval")],
        [B("🤖 AI post", callback_data="ai"), B("🎯 Mavzu", callback_data="mavzu")],
        [B("📰 Manbalar", callback_data="manba"), B("⚙️ Sozlamalar", callback_data="set")],
        [B("🎁 Konkurs", callback_data="gw"), B("🧠 Viktorina", callback_data="quiz")],
        [B("🌅 Kunlik post", callback_data="daily"), B("🌐 Tarjima", callback_data="tr")],
        [B("✨ AI tahrir", callback_data="edit"), B("💡 G'oyalar", callback_data="idea")],
        [B("⏱ Kechiktirilgan", callback_data="dl"), B("📣 Xabar yuborish", callback_data="bc")],
        [B("📊 Statistika", callback_data="stat"), B("🛡 Guruh", callback_data="grp")],
        [B("📢 Reklama", callback_data="ad"), B("💾 Zaxira", callback_data="bak")],
    ])


def user_menu():
    rows = [[B("🤖 AI guruh suhbati", callback_data="u_ai_group")],
            [B("📢 Reklama buyurtma qilish", callback_data="u_ad")],
            [B("💱 Valyuta kursi", callback_data="u_kurs"), B("📞 Admin bilan aloqa", callback_data="u_contact")]]
    if GROUP_URL:
        rows[0] = [B("🤖 AI guruhiga o'tish", url=GROUP_URL)]
    return M(rows)


def back():
    return M([[B("⬅️ Orqaga", callback_data="menu")]])


def pv_kb(regen=True):
    row = [B("❌ Bekor", callback_data="menu")]
    if regen:
        row.insert(0, B("🔄 Qayta yozish", callback_data="regen"))
    return M([[B("✅ Kanalga joylash", callback_data="pub")], row])


def tg(d, key, label, data):
    return B(("✅ " if d[key] else "⛔ ") + label, callback_data=data)


def set_view(d):
    kb = M([[tg(d, "auto_ai", "AI avto-post", "t_ai")],
            [tg(d, "auto_rss", "RSS avto-post", "t_rss")],
            [tg(d, "react_on", "Reaksiya tugmalari", "t_react")],
            [tg(d, "force_sub", "Majburiy obuna", "t_fsub")],
            [tg(d, "auto_backup", "Kunlik zaxira", "t_bak")],
            [B(f"⏱ AI post oralig'i: {d['ai_hours']} soat", callback_data="aih")],
            [B("⬅️ Orqaga", callback_data="menu")]])
    return f"Mavzu: {d['topic'] or '—'}\nManbalar: {len(d['sources'])} ta\nJadval: {len(d['schedules'])} ta\nAI avto-post: {'yoqilgan' if d['auto_ai'] else 'oʻchirilgan'}", kb


def grp_view(d):
    kb = M([[tg(d, "mod", "Moderator", "t_mod")],
            [tg(d, "ai_reply", "Izohlarga AI javob", "t_air")],
            [tg(d, "welcome_on", "Salomlashish", "t_wel")],
            [B("✏️ Salom matni", callback_data="welc"), B("🚫 Taqiqlangan so'zlar", callback_data="bw")],
            [B("⬅️ Orqaga", callback_data="menu")]])
    return ("🛡 Guruh sozlamalari\nSpam, havola, so'kinish va flood o'chiriladi. 3 ogohlantirishdan keyin "
            "1 soat mute.\n⚠️ Bot guruhda admin bo'lishi kerak."), kb


def quiz_view(d):
    kb = M([[tg(d, "quiz_on", "Kunlik viktorina", "t_dq")],
            [B(f"🕒 {d['quiz_time']}", callback_data="qtime"), B("🎯 Mavzu", callback_data="qtopic")],
            [B("▶️ Hozir yuborish", callback_data="qnow")],
            [B("⬅️ Orqaga", callback_data="menu")]])
    return f"🧠 AI viktorina\nMavzu: {d['quiz_topic'] or d['topic'] or 'umumiy bilimlar'}\nVaqt: {d['quiz_time']}", kb


def daily_view(d):
    p = d["daily_parts"]

    def pb(k, label):
        return B(("✅ " if p.get(k) else "⛔ ") + label, callback_data=f"dp:{k}")
    kb = M([[tg(d, "daily_on", "Kunlik post", "t_dd")],
            [pb("kurs", "Kurs"), pb("havo", "Ob-havo")],
            [pb("namoz", "Namoz"), pb("hikmat", "Hikmat")],
            [B(f"🕒 {d['daily_time']}", callback_data="dtime"), B(f"📍 {d['daily_city']}", callback_data="dcity")],
            [B("▶️ Hozir yuborish", callback_data="dnow")],
            [B("⬅️ Orqaga", callback_data="menu")]])
    return f"🌅 Kunlik avto-post\nVaqt: {d['daily_time']}\nShahar: {d['daily_city']}", kb


VIEWS = {"set": set_view, "grp": grp_view, "quiz": quiz_view, "daily": daily_view}
TOG = {"t_ai": "auto_ai", "t_rss": "auto_rss", "t_react": "react_on", "t_fsub": "force_sub", "t_bak": "auto_backup",
       "t_mod": "mod", "t_air": "ai_reply", "t_wel": "welcome_on", "t_dq": "quiz_on", "t_dd": "daily_on"}
SCREEN = {"t_ai": "set", "t_rss": "set", "t_react": "set", "t_fsub": "set", "t_bak": "set",
          "t_mod": "grp", "t_air": "grp", "t_wel": "grp", "t_dq": "quiz", "t_dd": "daily"}


async def show(u, text, kb):
    try:
        await u.callback_query.edit_message_text(text, reply_markup=kb)
    except Exception:
        logging.debug("edit failed", exc_info=True)


async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_chat.type != "private":
        return
    c.user_data["mode"] = None
    uid = u.effective_user.id
    if uid == ADMIN_ID:
        await u.message.reply_text("Kanal boshqaruv paneli 👇", reply_markup=menu())
        return
    track(uid)
    if not await gate(u, c):
        return
    await u.message.reply_text(f"Salom, {u.effective_user.first_name}! 👋 Kerakli bo'limni tanlang 👇",
                               reply_markup=user_menu())


# ---------- /kurs konvertor ----------
async def kurs_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_chat.type == "private" and not await gate(u, c):
        return
    try:
        r = await rates()
    except Exception:
        await u.message.reply_text("Kurs olinmadi, keyinroq urinib ko'ring.")
        return
    a = c.args or []
    if not a:
        await u.message.reply_text(kurs_fmt(r) + "\n\nMisol: /kurs 100 usd  yoki  /kurs 1000000 uzs")
        return
    try:
        if len(a) == 1 and not a[0].replace(".", "", 1).replace(",", "", 1).isdigit():
            amt, ccy = 1.0, a[0]
        else:
            amt, ccy = float(a[0].replace(",", ".")), (a[1] if len(a) > 1 else "usd")
    except Exception:
        await u.message.reply_text("Misol: /kurs 100 usd")
        return
    ccy = ccy.upper()
    if ccy in ("UZS", "SOM", "SO'M", "SUM"):
        lines = [f"{num(amt)} so'm ="] + [f"{num(amt / r[k][0])} {k}" for k in ("USD", "EUR", "RUB") if k in r]
    elif ccy in r:
        lines = [f"{num(amt)} {ccy} = {num(amt * r[ccy][0])} so'm"]
    else:
        lines = ["Valyuta topilmadi. Masalan: usd, eur, rub, uzs"]
    await u.message.reply_text("\n".join(lines))


# ---------- AI preview ----------
async def make_preview(c, send, topic=None):
    d = load()
    topic = topic or d["topic"]
    c.user_data["ptopic"] = topic
    t = await ask_ai(f"Telegram kanal uchun '{topic}' mavzusida qiziqarli, qisqa o'zbekcha post yoz. Emoji ishlat.")
    c.user_data["pending"] = t
    await send(t, pv_kb())


async def gen_ideas(c, topic):
    t = await ask_ai(f"'{topic}' mavzusida Telegram kanal uchun 7 ta turli post g'oyasi ber. O'zbek tilida, "
                     "har biri alohida qatorda, 1 jumla. Faqat g'oyalarning o'zi.", 500)
    ideas = [re.sub(r"^[\-\*\d\.\)\s]+", "", l).strip() for l in t.splitlines() if l.strip()][:7]
    c.user_data["ideas"], c.user_data["itopic"] = ideas, topic
    return ("💡 Post g'oyalari:\n\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(ideas, 1)) +
            "\n\nPost yozdirish uchun raqamni yozing 👇")


IDEAS_KB = M([[B("🔄 Yangi g'oyalar", callback_data="idea")], [B("⬅️ Orqaga", callback_data="menu")]])
ED_KB = M([[B("✍️ Yaxshilash", callback_data="ed:imp"), B("✂️ Qisqartirish", callback_data="ed:short")],
           [B("🔥 Jozibali qilish", callback_data="ed:hook"), B("#️⃣ Hashtag", callback_data="ed:tag")],
           [B("⬅️ Orqaga", callback_data="menu")]])
TR_KB = M([[B("🇺🇿 UZ", callback_data="tr:uz"), B("🇷🇺 RU", callback_data="tr:ru"), B("🇬🇧 EN", callback_data="tr:en")],
           [B("🌍 Hammasi", callback_data="tr:all")], [B("⬅️ Orqaga", callback_data="menu")]])


def gw_kb(n):
    return M([[B(f"🎉 Qatnashish ({n})", callback_data="gwj")]])


# ---------- jobs ----------
async def send_job(ctx):
    await publish(ctx.bot, ctx.job.data)


def reg(jq, s):
    hh, mm = map(int, s["time"].split(":"))
    jq.run_daily(send_job, datetime.time(hh, mm, tzinfo=TZ), data=s["text"], name=f"sch{s['id']}")


def reg_time(jq, name, fn, hm):
    for j in jq.get_jobs_by_name(name):
        j.schedule_removal()
    hh, mm = map(int, hm.split(":"))
    jq.run_daily(fn, datetime.time(hh, mm, tzinfo=TZ), name=name)


async def ai_job(ctx):
    d = load()
    if d["auto_ai"] and d["topic"]:
        t = await ask_ai(f"Telegram kanal uchun '{d['topic']}' mavzusida qiziqarli, qisqa o'zbekcha post yoz. Emoji ishlat.")
        await publish(ctx.bot, t)


async def rss_job(ctx):
    d = load()
    if not d["auto_rss"]:
        return
    seen = list(d["seen"])
    for url in d["sources"]:
        try:
            feed = await asyncio.to_thread(feedparser.parse, url)
        except Exception:
            continue
        for e in feed.entries[:3]:
            if e.link in seen:
                continue
            seen.append(e.link)
            try:
                t = await ask_ai(f"Quyidagi yangilikni o'zbek tiliga qisqa tarjima qilib, kanal posti ko'rinishida yoz:\n{e.title}\n{e.get('summary', '')}")
                await publish(ctx.bot, f"{t}\n\n{e.link}")
            except Exception:
                logging.exception("rss")
    d = load()
    d["seen"] = seen[-500:]
    save(d)


async def daily_job(ctx):
    d = load()
    if d["daily_on"]:
        await publish(ctx.bot, await build_daily(d))


async def quiz_job(ctx):
    d = load()
    if d["quiz_on"]:
        try:
            await send_quiz(ctx.bot, d["quiz_topic"] or d["topic"] or "umumiy bilimlar")
        except Exception:
            logging.exception("quiz")


async def delayed_job(ctx):
    it = ctx.job.data
    try:
        await publish_copy(ctx.bot, it["chat"], it["msg"])
    except Exception:
        logging.exception("delayed")
        try:
            await ctx.bot.send_message(ADMIN_ID, "⚠️ Kechiktirilgan postni joylab bo'lmadi.")
        except Exception:
            pass
    d = load()
    d["delayed"] = [x for x in d["delayed"] if x["id"] != it["id"]]
    save(d)


async def rec_job(ctx):
    try:
        n = await ctx.bot.get_chat_member_count(CHANNEL_ID)
    except Exception:
        return
    d = load()
    d["mhist"][datetime.datetime.now(TZ).strftime("%Y-%m-%d")] = n
    for k in sorted(d["mhist"])[:-60]:
        d["mhist"].pop(k)
    save(d)


async def backup_job(ctx):
    d = load()
    if d["auto_backup"] and os.path.exists(DB):
        with open(DB, "rb") as f:
            await ctx.bot.send_document(ADMIN_ID, f, filename="data.json", caption="💾 Kunlik zaxira")


# ---------- foydalanuvchi va kanal tugmalari (hamma uchun) ----------
async def pub_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    x, uid = q.data, q.from_user.id

    if x.startswith("r:"):                                   # reaksiya
        k = x[2:]
        if k not in ("up", "fire", "down"):
            await q.answer()
            return
        d = load()
        r = d["react"].setdefault(str(q.message.message_id), {"up": [], "fire": [], "down": []})
        if uid in r[k]:
            r[k].remove(uid)
            note = "Reaksiya olib tashlandi"
        else:
            for kk in r:
                if uid in r[kk]:
                    r[kk].remove(uid)
            r[k].append(uid)
            note = "Rahmat! 🙌"
        while len(d["react"]) > 300:
            d["react"].pop(next(iter(d["react"])))
        save(d)
        await q.answer(note)
        try:
            await q.edit_message_reply_markup(react_kb({kk: len(v) for kk, v in r.items()}))
        except Exception:
            pass

    elif x == "gwj":                                         # konkursda qatnashish
        d = load()
        g = d["gw"]
        if not g or g["msg"] != q.message.message_id:
            await q.answer("Bu konkurs tugagan", show_alert=True)
            return
        if str(uid) in g["users"]:
            await q.answer("Siz allaqachon qatnashyapsiz ✅")
            return
        g["users"][str(uid)] = q.from_user.full_name
        save(d)
        await q.answer("🎉 Qatnashdingiz! Omad!")
        try:
            await q.edit_message_reply_markup(gw_kb(len(g["users"])))
        except Exception:
            pass

    elif x == "chk":                                         # obunani tekshirish
        if await is_sub(c.bot, uid):
            await q.answer("✅ Rahmat!")
            await q.message.reply_text("Xush kelibsiz! Bo'limni tanlang 👇", reply_markup=user_menu())
        else:
            await q.answer("Siz hali a'zo emassiz ❌", show_alert=True)

    elif x.startswith("u_"):
        await q.answer()
        if not await gate(u, c):
            return
        d = load()
        if x == "u_ad":
            c.user_data["mode"] = "ad"
            await q.message.reply_text(d["ad_price"] + "\n\n👇 Reklama materialini (matn/rasm/video) yuboring.")
        elif x == "u_kurs":
            try:
                await q.message.reply_text(await kurs_text() + "\n\nKonvertor: /kurs 100 usd")
            except Exception:
                await q.message.reply_text("Kurs olinmadi, keyinroq urinib ko'ring.")
        elif x == "u_contact":
            c.user_data["mode"] = "contact"
            await q.message.reply_text("Adminga xabaringizni yozing 👇")
        elif x == "u_ai_group":
            if GROUP_URL:
                await q.message.reply_text("🤖 Umumiy AI suhbatiga qo'shilish uchun quyidagi tugmani bosing:",
                                           reply_markup=M([[B("AI guruhiga o'tish", url=GROUP_URL)]]))
            else:
                await q.message.reply_text(
                    "🤖 AI guruh havolasi hali sozlanmagan. Railway → Variables bo'limida GROUP_URL nomli o'zgaruvchiga guruh taklif havolasini kiriting.\n\n"
                    "Guruhda botga @username orqali murojaat qiling yoki bot xabariga Reply qiling.")


# ---------- admin tugmalari ----------
async def cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    d = load()
    x = q.data
    c.user_data["mode"] = None

    if x == "menu":
        await show(u, "Kanal boshqaruv paneli 👇", menu())

    elif x == "post":
        c.user_data["mode"] = "post"
        await show(u, "Postni yuboring (matn, rasm yoki video) 👇", back())

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

    elif x == "ai":
        if not d["topic"]:
            c.user_data["mode"] = "mavzu_ai"
            await show(u, "Avval mavzuni yozing (masalan: futbol yangiliklari) 👇", back())
        else:
            await show(u, "⏳ Yozilmoqda...", None)
            await make_preview(c, lambda t, k: show(u, t, k))

    elif x == "regen":
        await show(u, "⏳ Yozilmoqda...", None)
        await make_preview(c, lambda t, k: show(u, t, k), topic=c.user_data.get("ptopic"))

    elif x == "pub":
        t = c.user_data.get("pending")
        if t:
            await publish(c.bot, t)
            c.user_data["pending"] = None
            await show(u, "✅ Kanalga joylandi", menu())

    elif x == "aih":
        c.user_data["mode"] = "ai_hours"
        await show(u, f"AI avtomatik post oralig'ini soatlarda kiriting (1–168). Hozir: {d['ai_hours']} soat.", back())

    elif x == "mavzu":
        c.user_data["mode"] = "mavzu"
        await show(u, f"Hozirgi mavzu: {d['topic'] or '—'}\n\nYangi mavzuni yozing 👇", back())

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

    # ----- ekranlar va tumblerlar -----
    elif x in VIEWS:
        await show(u, *VIEWS[x](d))

    elif x in TOG:
        d[TOG[x]] = not d[TOG[x]]
        save(d)
        await show(u, *VIEWS[SCREEN[x]](d))

    # ----- kunlik post -----
    elif x.startswith("dp:"):
        k = x[3:]
        d["daily_parts"][k] = not d["daily_parts"].get(k, True)
        save(d)
        await show(u, *daily_view(d))

    elif x == "dtime":
        c.user_data["mode"] = "dtime"
        await show(u, "Kunlik post vaqtini yozing, masalan: 07:00", back())

    elif x == "dcity":
        rows = [[B(n, callback_data=f"city:{i}") for i, n in list(enumerate(CITY_NAMES))[j:j + 2]]
                for j in range(0, len(CITY_NAMES), 2)]
        rows.append([B("⬅️ Orqaga", callback_data="daily")])
        await show(u, "Shaharni tanlang 📍", M(rows))

    elif x.startswith("city:"):
        d["daily_city"] = CITY_NAMES[int(x[5:])]
        save(d)
        await show(u, *daily_view(d))

    elif x == "dnow":
        await show(u, "⏳ Tayyorlanmoqda...", None)
        await publish(c.bot, await build_daily(d))
        t, kb = daily_view(load())
        await show(u, "✅ Kanalga joylandi\n\n" + t, kb)

    # ----- viktorina -----
    elif x == "qtime":
        c.user_data["mode"] = "qtime"
        await show(u, "Viktorina vaqtini yozing, masalan: 18:00", back())

    elif x == "qtopic":
        c.user_data["mode"] = "qtopic"
        await show(u, "Viktorina mavzusini yozing (masalan: tarix, texnologiya) 👇", back())

    elif x == "qnow":
        await show(u, "⏳ Savol tuzilmoqda...", None)
        try:
            await send_quiz(c.bot, d["quiz_topic"] or d["topic"] or "umumiy bilimlar")
            t, kb = quiz_view(load())
            await show(u, "✅ Viktorina joylandi\n\n" + t, kb)
        except Exception:
            logging.exception("quiz")
            await show(u, "❌ Xatolik, qayta urinib ko'ring.", back())

    # ----- konkurs -----
    elif x == "gw":
        g = d["gw"]
        if g:
            kb = M([[B("🏁 Yakunlash va g'oliblarni tanlash", callback_data="gwend")],
                    [B("🗑 Bekor qilish", callback_data="gwcancel")],
                    [B("⬅️ Orqaga", callback_data="menu")]])
            await show(u, f"🎁 Faol konkurs\nSovrin: {g['prize']}\nG'oliblar: {g['n']} ta\nQatnashchilar: {len(g['users'])} ta", kb)
        else:
            await show(u, "Faol konkurs yo'q.", M([[B("➕ Yangi konkurs", callback_data="gwnew")],
                                                   [B("⬅️ Orqaga", callback_data="menu")]]))

    elif x == "gwnew":
        c.user_data["mode"] = "gw_prize"
        await show(u, "Sovrinni yozing (masalan: iPhone 15) 👇", back())

    elif x == "gwcancel":
        g = d["gw"]
        if g:
            try:
                await c.bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=g["msg"], reply_markup=None)
            except Exception:
                pass
        d = load()
        d["gw"] = None
        save(d)
        await show(u, "Konkurs bekor qilindi.", menu())

    elif x == "gwend":
        g = d["gw"]
        if not g:
            await show(u, "Faol konkurs yo'q.", menu())
            return
        users = list(g["users"].items())
        win = random.sample(users, min(g["n"], len(users)))
        if win:
            lines = "\n".join(f'{i}. <a href="tg://user?id={w}">{html.escape(nm)}</a>' for i, (w, nm) in enumerate(win, 1))
            text = (f"🏆 <b>KONKURS YAKUNLANDI!</b>\n\n🎁 Sovrin: {html.escape(g['prize'])}\n"
                    f"👥 Qatnashchilar: {len(users)}\n\n🥇 G'oliblar:\n{lines}\n\nTabriklaymiz! 🎉")
        else:
            text = "Konkurs yakunlandi, lekin qatnashchilar bo'lmadi 😔"
        await c.bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML)
        try:
            await c.bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=g["msg"], reply_markup=None)
        except Exception:
            pass
        d = load()
        d["gw"] = None
        inc(d, "giveaways")
        save(d)
        await show(u, "✅ Konkurs yakunlandi, g'oliblar kanalga e'lon qilindi.", menu())

    # ----- tarjima -----
    elif x == "tr":
        c.user_data["mode"] = "tr"
        await show(u, "Tarjima qilinadigan post matnini yuboring 👇", back())

    elif x.startswith("tr:"):
        k, src = x[3:], c.user_data.get("trsrc")
        if not src:
            await show(u, "Matn topilmadi. Qaytadan boshlang.", back())
            return
        await show(u, "⏳ Tarjima qilinmoqda...", None)
        if k == "all":
            t = await ask_ai("Quyidagi postni uchta tilga tarjima qil: o'zbek, rus, ingliz. Har bir tarjima oldiga "
                             "bayroq (🇺🇿 🇷🇺 🇬🇧) qo'y, orasida bo'sh qator qoldir. Format va emojilarni saqla. "
                             f"Faqat tarjimalarni yoz.\n\n{src}", 1500)
        else:
            name = {"uz": "o'zbek", "ru": "rus", "en": "ingliz"}[k]
            t = await ask_ai(f"Quyidagi postni {name} tiliga tarjima qil. Format va emojilarni saqla. "
                             f"Faqat tarjimani yoz.\n\n{src}", 1000)
        c.user_data["pending"] = t
        await show(u, t, pv_kb(regen=False))

    # ----- AI tahrir -----
    elif x == "edit":
        c.user_data["mode"] = "edit"
        await show(u, "Tahrir qilinadigan matnni yuboring 👇", back())

    elif x.startswith("ed:"):
        draft = c.user_data.get("draft")
        if not draft:
            await show(u, "Matn topilmadi. Qaytadan boshlang.", back())
            return
        P = {"imp": "Quyidagi postni tahrirla: xatolarni tuzat, uslubini yaxshila, ma'nosini saqla.",
             "short": "Quyidagi postni ma'nosini yo'qotmasdan taxminan 2 barobar qisqartir.",
             "hook": "Quyidagi postni jozibador qil: kuchli boshlanish, emoji va oxirida chaqiriq qo'sh.",
             "tag": "Quyidagi postning oxiriga mos 3-5 ta hashtag qo'sh. Post matnini o'zgartirma."}
        await show(u, "⏳ Ishlanmoqda...", None)
        t = await ask_ai(f"{P[x[3:]]} Faqat tayyor post matnini qaytar.\n\n{draft}")
        c.user_data["pending"] = t
        await show(u, t, pv_kb(regen=False))

    # ----- g'oyalar -----
    elif x == "idea":
        if not d["topic"]:
            c.user_data["mode"] = "idea_topic"
            await show(u, "G'oyalar uchun mavzuni yozing 👇", back())
        else:
            await show(u, "⏳ G'oyalar o'ylanmoqda...", None)
            await show(u, await gen_ideas(c, d["topic"]), IDEAS_KB)
            c.user_data["mode"] = "idea_pick"

    # ----- statistika -----
    elif x == "stat":
        try:
            n = await c.bot.get_chat_member_count(CHANNEL_ID)
        except Exception:
            n = None
        d = load()
        line = "👥 Kanal a'zolari: noma'lum"
        if n is not None:
            d["mhist"][datetime.datetime.now(TZ).strftime("%Y-%m-%d")] = n
            save(d)

            def gr(days):
                day = (datetime.datetime.now(TZ) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
                old = d["mhist"].get(day)
                return "—" if old is None else f"{n - old:+d}"
            line = f"👥 Kanal a'zolari: {n}  (kecha {gr(1)}, 7 kun {gr(7)})"
        st = d["stats"]
        await show(u, "📊 Statistika\n\n" + line +
                   f"\n🤖 Bot foydalanuvchilari: {len(d['users'])}\n📝 Joylangan postlar: {st.get('posts', 0)}"
                   f"\n🎁 O'tkazilgan konkurslar: {st.get('giveaways', 0)}\n📢 Reklama buyurtmalari: {st.get('ads', 0)}"
                   f"\n⏰ Jadval: {len(d['schedules'])}  📰 Manba: {len(d['sources'])}  ⏱ Kutilayotgan: {len(d['delayed'])}",
                   back())

    # ----- ommaviy xabar -----
    elif x == "bc":
        c.user_data["mode"] = "bc"
        await show(u, f"Botdagi {len(d['users'])} ta foydalanuvchiga yuboriladigan xabarni yuboring 👇", back())

    elif x == "bcgo":
        src = c.user_data.get("bcm")
        if not src:
            await show(u, "Xabar topilmadi.", back())
            return
        await show(u, "⏳ Yuborilmoqda...", None)
        ok = bad = 0
        dead = []
        for uid in d["users"]:
            try:
                await c.bot.copy_message(uid, src[0], src[1])
                ok += 1
            except Forbidden:
                dead.append(uid)
                bad += 1
            except Exception:
                bad += 1
            await asyncio.sleep(0.05)
        d = load()
        d["users"] = [i for i in d["users"] if i not in dead]
        save(d)
        c.user_data["bcm"] = None
        await show(u, f"✅ Yuborildi: {ok}\n❌ Xato: {bad}", menu())

    # ----- kechiktirilgan post -----
    elif x == "dl":
        rows = [[B("❌ " + datetime.datetime.fromtimestamp(i["ts"], TZ).strftime("%d.%m %H:%M"),
                   callback_data=f"dldel:{i['id']}")] for i in d["delayed"]]
        rows.append([B("➕ Yangi", callback_data="dladd")])
        rows.append([B("⬅️ Orqaga", callback_data="menu")])
        await show(u, "Kutilayotgan postlar:" if d["delayed"] else "Kechiktirilgan post yo'q.", M(rows))

    elif x == "dladd":
        c.user_data["mode"] = "dl_min"
        await show(u, "Necha daqiqadan keyin joylansin? (masalan: 45)", back())

    elif x.startswith("dldel:"):
        i = int(x[6:])
        for j in c.job_queue.get_jobs_by_name(f"dl{i}"):
            j.schedule_removal()
        d["delayed"] = [y for y in d["delayed"] if y["id"] != i]
        save(d)
        await show(u, "O'chirildi ✅", M([[B("⬅️ Orqaga", callback_data="dl")]]))

    # ----- zaxira -----
    elif x == "bak":
        save(d)
        with open(DB, "rb") as f:
            await c.bot.send_document(ADMIN_ID, f, filename="data.json", caption="💾 Zaxira nusxa")

    # ----- reklama -----
    elif x == "ad":
        kb = M([[B("✏️ Narx matnini o'zgartirish", callback_data="adprice")], [B("⬅️ Orqaga", callback_data="menu")]])
        await show(u, f"📢 Reklama buyurtmalari: {d['stats'].get('ads', 0)} ta\n\nNarx matni:\n{d['ad_price']}", kb)

    elif x == "adprice":
        c.user_data["mode"] = "adprice"
        await show(u, "Yangi narx matnini yozing 👇", back())

    elif x.startswith(("adok:", "adno:")):
        uid = int(x.split(":")[1])
        ok = x.startswith("adok")
        try:
            await c.bot.send_message(uid, "✅ Reklama buyurtmangiz qabul qilindi. Admin siz bilan bog'lanadi." if ok
                                     else "❌ Afsuski, reklama buyurtmangiz rad etildi.")
        except Exception:
            pass
        await show(u, ("✅ Qabul qilindi" if ok else "❌ Rad etildi") + f" (ID {uid})",
                   M([[B("💬 Javob yozish", callback_data=f"reply:{uid}")]]))

    elif x.startswith("reply:"):
        c.user_data["mode"] = "reply"
        c.user_data["rto"] = int(x[6:])
        await q.message.reply_text("Javobingizni yozing 👇")

    # ----- guruh -----
    elif x == "welc":
        c.user_data["mode"] = "welcome"
        await show(u, "Yangi salomlashish matnini yozing. {name} — a'zo ismi o'rniga qo'yiladi.\n\n"
                      f"Hozirgi:\n{d['welcome']}", back())

    elif x == "bw":
        kb = M([[B("➕ Qo'shish", callback_data="bwadd"), B("➖ O'chirish", callback_data="bwdel")],
                [B("⬅️ Orqaga", callback_data="grp")]])
        await show(u, "🚫 Taqiqlangan so'zlar:\n" + (", ".join(d["badwords"]) or "—"), kb)

    elif x in ("bwadd", "bwdel"):
        c.user_data["mode"] = "bw_add" if x == "bwadd" else "bw_del"
        await show(u, "So'zlarni vergul yoki yangi qator bilan ajratib yozing 👇", back())


# ---------- guruh: moderator, salomlashish, AI javob ----------
def violation(d, m):
    t = m.text or m.caption or ""
    low = t.lower()
    ents = list(m.entities or []) + list(m.caption_entities or [])
    if any(en.type in ("url", "text_link") for en in ents) or LINK_RE.search(t):
        return "havola/reklama"
    if any(w in low for w in d["badwords"]):
        return "so'kinish"
    if any(w in low for w in SPAM):
        return "spam"
    return None


def flooding(uid):
    now = time.time()
    if len(FLOOD) > 5000:
        FLOOD.clear()
    L = [t for t in FLOOD.get(uid, []) if now - t < 8] + [now]
    FLOOD[uid] = L
    return len(L) > 5


async def is_chat_admin(bot, chat_id, uid):
    if uid == ADMIN_ID:
        return True
    try:
        m = await bot.get_chat_member(chat_id, uid)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


async def punish(c, m, reason):
    user, chat = m.from_user, m.chat_id
    try:
        await m.delete()
    except Exception:
        logging.warning("O'chirib bo'lmadi (bot guruhda admin emasmi?)")
    key = (chat, user.id)
    WARN[key] = WARN.get(key, 0) + 1
    n = WARN[key]
    if reason == "flood" or n >= 3:
        sec = 300 if reason == "flood" else 3600
        try:
            await c.bot.restrict_chat_member(
                chat, user.id, ChatPermissions(can_send_messages=False),
                until_date=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=sec))
        except Exception:
            pass
        WARN[key] = 0
        FLOOD[user.id] = []
        txt = f"🔇 {user.full_name} {sec // 60} daqiqaga cheklandi ({reason})."
    else:
        txt = f"⚠️ {user.full_name}, {reason} taqiqlanadi! Ogohlantirish {n}/3."
    s = await c.bot.send_message(chat, txt)
    tmp(c, chat, s.message_id, 20)


async def on_group(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m, ch = u.effective_message, u.effective_chat
    if not m or (GROUP_ID and str(ch.id) != GROUP_ID):
        return
    d = load()

    if m.new_chat_members:                                   # salomlashish
        if d["mod"]:
            try:
                await m.delete()
            except Exception:
                pass
        if d["welcome_on"]:
            for nm in m.new_chat_members:
                if not nm.is_bot:
                    s = await c.bot.send_message(ch.id, d["welcome"].replace("{name}", nm.full_name))
                    tmp(c, ch.id, s.message_id, 60)
        return
    if m.left_chat_member:
        if d["mod"]:
            try:
                await m.delete()
            except Exception:
                pass
        return

    user = u.effective_user
    if not user or user.is_bot or m.is_automatic_forward:
        return

    if d["mod"]:                                             # moderator
        reason = violation(d, m) or ("flood" if flooding(user.id) else None)
        if reason and not await is_chat_admin(c.bot, ch.id, user.id):
            await punish(c, m, reason)
            return

    if not d["ai_reply"] or u.edited_message or not m.text:  # izohlarga AI javob
        return
    txt = m.text
    rt = m.reply_to_message
    mention = f"@{c.bot.username}".lower() in txt.lower()
    to_bot = bool(rt and rt.from_user and rt.from_user.id == c.bot.id)
    to_human = bool(rt and not rt.is_automatic_forward and rt.from_user and rt.from_user.id != c.bot.id)
    question = "?" in txt and len(txt) > 8 and not to_human
    if not (mention or to_bot or question):
        return
    if time.time() - AIRATE.get(user.id, 0) < 30:
        return
    AIRATE[user.id] = time.time()
    if d["force_sub"] and not await is_sub(c.bot, user.id):
        s = await m.reply_text("Savolga javob olish uchun avval kanalimizga a'zo bo'ling 📢")
        tmp(c, ch.id, s.message_id, 20)
        return
    try:
        await c.bot.send_chat_action(ch.id, "typing")
        ans = await ask_ai(
            f"Sen '{d['topic'] or 'umumiy'}' mavzusidagi Telegram kanalining yordamchisisan. Foydalanuvchi savoliga "
            "o'zbek tilida (agar u boshqa tilda yozgan bo'lsa — o'sha tilda) aniq va qisqa (3-5 jumla) javob ber.\n"
            f"Savol: {txt}", 500)
        await m.reply_text(ans)
    except Exception:
        logging.exception("ai_reply")


async def ai_group_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Explicit AI command available in the shared group: /ai question"""
    if u.effective_chat.type not in ("group", "supergroup"):
        await u.effective_message.reply_text("/ai buyrug'i umumiy guruhda ishlaydi.")
        return
    if GROUP_ID and str(u.effective_chat.id) != GROUP_ID:
        return
    user = u.effective_user
    msg = u.effective_message
    question = " ".join(c.args or []).strip()
    if not question:
        await msg.reply_text("Savolingizni shunday yuboring: /ai Ingliz tilida Present Perfect nima?")
        return
    if time.time() - AIRATE.get(user.id, 0) < 10:
        await msg.reply_text("Biroz kutib, keyin yana urinib ko'ring.")
        return
    AIRATE[user.id] = time.time()
    d = load()
    if d["force_sub"] and not await is_sub(c.bot, user.id):
        await msg.reply_text("AI javobidan foydalanish uchun avval kanalga obuna bo'ling 📢")
        return
    try:
        await c.bot.send_chat_action(u.effective_chat.id, "typing")
        answer = await ask_ai(
            f"Sen Telegram guruhidagi yordamchi AIsan. Savolga aniq va foydali javob ber. "
            f"Foydalanuvchi: {user.full_name}. Savol: {question}", 600)
        await msg.reply_text(answer)
    except Exception:
        logging.exception("ai_group_cmd")
        await msg.reply_text("AI hozir javob bera olmadi. Keyinroq urinib ko'ring.")


# ---------- shaxsiy chat: oddiy foydalanuvchilar ----------
async def user_msg(u, c, mode):
    if not await gate(u, c):
        return
    m, user = u.message, u.effective_user
    track(user.id)
    if mode in ("ad", "contact"):
        await c.bot.copy_message(ADMIN_ID, m.chat_id, m.message_id)
        uname = f"@{user.username}" if user.username else user.full_name
        rep = B("💬 Javob yozish", callback_data=f"reply:{user.id}")
        if mode == "ad":
            head = "📢 Yangi reklama buyurtmasi"
            kb = M([[B("✅ Qabul", callback_data=f"adok:{user.id}"), B("❌ Rad", callback_data=f"adno:{user.id}")], [rep]])
            d = load()
            inc(d, "ads")
            save(d)
        else:
            head, kb = "💬 Yangi xabar", M([[rep]])
        await c.bot.send_message(ADMIN_ID, f"{head}\nKimdan: {uname} (ID {user.id})", reply_markup=kb)
        c.user_data["mode"] = None
        await m.reply_text("✅ Yuborildi. Tez orada javob beramiz.", reply_markup=user_menu())
    else:
        await m.reply_text("Menyudan tanlang 👇", reply_markup=user_menu())


TEXT_MODES = {"jt", "jx", "manba", "mavzu", "mavzu_ai", "ai_hours", "gw_prize", "gw_n", "tr", "edit", "idea_topic",
              "idea_pick", "dl_min", "dtime", "qtime", "qtopic", "adprice", "welcome", "bw_add", "bw_del"}


# ---------- shaxsiy chat: admin ----------
async def on_msg(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = u.message
    if not m:
        return
    mode = c.user_data.get("mode")
    if u.effective_user.id != ADMIN_ID:
        await user_msg(u, c, mode)
        return
    d = load()

    if mode in TEXT_MODES and not m.text:
        await m.reply_text("Iltimos, matn yuboring.")
        return

    if mode == "post":
        await publish_copy(c.bot, m.chat_id, m.message_id)
        c.user_data["mode"] = None
        await m.reply_text("✅ Joylandi", reply_markup=menu())

    elif mode == "jt":
        hm = parse_hm(m.text)
        if not hm:
            await m.reply_text("Format noto'g'ri. Masalan: 09:30")
            return
        c.user_data["jt"] = hm
        c.user_data["mode"] = "jx"
        await m.reply_text("Endi shu vaqtda joylanadigan matnni yozing 👇")

    elif mode == "jx":
        s = {"id": int(time.time()), "time": c.user_data["jt"], "text": m.text}
        d["schedules"].append(s)
        save(d)
        reg(c.job_queue, s)
        c.user_data["mode"] = None
        await m.reply_text(f"✅ Har kuni {s['time']} da joylanadi", reply_markup=menu())

    elif mode == "manba":
        if not m.text.startswith("http"):
            await m.reply_text("Havola http bilan boshlanishi kerak")
            return
        d["sources"].append(m.text.strip())
        save(d)
        c.user_data["mode"] = None
        await m.reply_text("✅ Manba qo'shildi", reply_markup=menu())

    elif mode == "ai_hours":
        try:
            hours = int(m.text.strip())
            if not 1 <= hours <= 168:
                raise ValueError
        except (ValueError, TypeError):
            await m.reply_text("1 dan 168 gacha butun son kiriting. Masalan: 6")
            return
        d["ai_hours"] = hours
        save(d)
        for job in c.job_queue.get_jobs_by_name("ai_auto"):
            job.schedule_removal()
        c.job_queue.run_repeating(ai_job, interval=hours * 3600, first=60, name="ai_auto")
        c.user_data["mode"] = None
        await m.reply_text(f"✅ AI avto-post oralig'i {hours} soat qilib saqlandi.", reply_markup=menu())

    elif mode in ("mavzu", "mavzu_ai"):
        d["topic"] = m.text.strip()
        save(d)
        c.user_data["mode"] = None
        if mode == "mavzu_ai":
            await m.reply_text("⏳ Yozilmoqda...")
            await make_preview(c, lambda t, k: m.reply_text(t, reply_markup=k))
        else:
            await m.reply_text("✅ Mavzu saqlandi", reply_markup=menu())

    # --- konkurs ---
    elif mode == "gw_prize":
        c.user_data["gwp"] = m.text.strip()
        c.user_data["mode"] = "gw_n"
        await m.reply_text("Nechta g'olib bo'ladi? (1-10)")

    elif mode == "gw_n":
        try:
            n = int(m.text.strip())
            assert 1 <= n <= 10
        except Exception:
            await m.reply_text("1 dan 10 gacha son yozing")
            return
        prize = c.user_data.get("gwp", "")
        msg = await c.bot.send_message(
            CHANNEL_ID, f"🎁 KONKURS!\n\nSovrin: {prize}\nG'oliblar soni: {n}\n\nQatnashish uchun pastdagi tugmani bosing 👇",
            reply_markup=gw_kb(0))
        d = load()
        d["gw"] = {"prize": prize, "n": n, "users": {}, "msg": msg.message_id}
        inc(d, "posts")
        save(d)
        c.user_data["mode"] = None
        await m.reply_text("✅ Konkurs kanalga joylandi", reply_markup=menu())

    # --- tarjima / tahrir / g'oyalar ---
    elif mode == "tr":
        c.user_data["trsrc"] = m.text
        c.user_data["mode"] = None
        await m.reply_text("Qaysi tilga tarjima qilay?", reply_markup=TR_KB)

    elif mode == "edit":
        c.user_data["draft"] = m.text
        c.user_data["mode"] = None
        await m.reply_text("Nima qilay?", reply_markup=ED_KB)

    elif mode == "idea_topic":
        c.user_data["mode"] = None
        await m.reply_text("⏳ G'oyalar o'ylanmoqda...")
        await m.reply_text(await gen_ideas(c, m.text.strip()), reply_markup=IDEAS_KB)
        c.user_data["mode"] = "idea_pick"

    elif mode == "idea_pick":
        ideas = c.user_data.get("ideas", [])
        try:
            i = int(m.text.strip()) - 1
            assert 0 <= i < len(ideas)
        except Exception:
            await m.reply_text(f"1 dan {len(ideas)} gacha raqam yozing")
            return
        c.user_data["mode"] = None
        await m.reply_text("⏳ Yozilmoqda...")
        await make_preview(c, lambda t, k: m.reply_text(t, reply_markup=k),
                           topic=f"{c.user_data.get('itopic', '')} — {ideas[i]}")

    # --- ommaviy xabar ---
    elif mode == "bc":
        c.user_data["bcm"] = (m.chat_id, m.message_id)
        c.user_data["mode"] = None
        await m.reply_text(f"{len(d['users'])} ta foydalanuvchiga yuborilsinmi?",
                           reply_markup=M([[B("✅ Yuborish", callback_data="bcgo"), B("❌ Bekor", callback_data="menu")]]))

    # --- kechiktirilgan post ---
    elif mode == "dl_min":
        try:
            n = int(m.text.strip())
            assert 1 <= n <= 10080
        except Exception:
            await m.reply_text("1 dan 10080 gacha daqiqa yozing")
            return
        c.user_data["dlm"] = n
        c.user_data["mode"] = "dl_msg"
        await m.reply_text("Endi postni yuboring (matn, rasm yoki video) 👇")

    elif mode == "dl_msg":
        n = c.user_data.get("dlm", 1)
        it = {"id": int(time.time()), "ts": time.time() + n * 60, "chat": m.chat_id, "msg": m.message_id}
        d["delayed"].append(it)
        save(d)
        c.job_queue.run_once(delayed_job, n * 60, data=it, name=f"dl{it['id']}")
        c.user_data["mode"] = None
        when = datetime.datetime.fromtimestamp(it["ts"], TZ).strftime("%d.%m %H:%M")
        await m.reply_text(f"✅ {when} da kanalga joylanadi.\n(Bu xabarni chatdan o'chirmang!)", reply_markup=menu())

    # --- vaqt/mavzu sozlamalari ---
    elif mode in ("dtime", "qtime"):
        hm = parse_hm(m.text)
        if not hm:
            await m.reply_text("Format noto'g'ri. Masalan: 07:30")
            return
        if mode == "dtime":
            d["daily_time"] = hm
            reg_time(c.job_queue, "daily", daily_job, hm)
            view = daily_view
        else:
            d["quiz_time"] = hm
            reg_time(c.job_queue, "quiz", quiz_job, hm)
            view = quiz_view
        save(d)
        c.user_data["mode"] = None
        t, kb = view(d)
        await m.reply_text("✅ Saqlandi\n\n" + t, reply_markup=kb)

    elif mode == "qtopic":
        d["quiz_topic"] = m.text.strip()
        save(d)
        c.user_data["mode"] = None
        t, kb = quiz_view(d)
        await m.reply_text("✅ Saqlandi\n\n" + t, reply_markup=kb)

    # --- reklama / aloqa ---
    elif mode == "adprice":
        d["ad_price"] = m.text.strip()
        save(d)
        c.user_data["mode"] = None
        await m.reply_text("✅ Narx matni yangilandi", reply_markup=menu())

    elif mode == "reply":
        try:
            await c.bot.send_message(c.user_data["rto"], "💬 Admin javobi:")
            await c.bot.copy_message(c.user_data["rto"], m.chat_id, m.message_id)
            await m.reply_text("✅ Javob yuborildi", reply_markup=menu())
        except Exception:
            await m.reply_text("❌ Yuborib bo'lmadi (foydalanuvchi botni bloklagan bo'lishi mumkin)", reply_markup=menu())
        c.user_data["mode"] = None

    # --- guruh sozlamalari ---
    elif mode == "welcome":
        d["welcome"] = m.text.strip()
        save(d)
        c.user_data["mode"] = None
        t, kb = grp_view(d)
        await m.reply_text("✅ Saqlandi\n\n" + t, reply_markup=kb)

    elif mode in ("bw_add", "bw_del"):
        words = [w.strip().lower() for w in re.split(r"[,\n]", m.text) if w.strip()]
        if mode == "bw_add":
            d["badwords"] += [w for w in words if w not in d["badwords"]]
        else:
            d["badwords"] = [w for w in d["badwords"] if w not in words]
        save(d)
        c.user_data["mode"] = None
        t, kb = grp_view(d)
        await m.reply_text("✅ Yangilandi: " + (", ".join(d["badwords"]) or "—") + "\n\n" + t, reply_markup=kb)

    else:
        await m.reply_text("Menyudan tanlang 👇", reply_markup=menu())


def main():
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("kurs", kurs_cmd))
    app.add_handler(CommandHandler("ai", ai_group_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(CallbackQueryHandler(pub_cb, pattern=r"^(r:|gwj$|chk$|u_)"))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, on_group))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_msg))

    d = load()
    jq = app.job_queue
    for s in d["schedules"]:
        reg(jq, s)
    reg_time(jq, "daily", daily_job, d["daily_time"])
    reg_time(jq, "quiz", quiz_job, d["quiz_time"])
    for it in d["delayed"]:
        jq.run_once(delayed_job, max(5, it["ts"] - time.time()), data=it, name=f"dl{it['id']}")
    jq.run_repeating(ai_job, interval=d["ai_hours"] * 3600, first=120, name="ai_auto")
    jq.run_repeating(rss_job, interval=d["rss_min"] * 60, first=60)
    jq.run_repeating(rec_job, interval=6 * 3600, first=30)
    jq.run_repeating(backup_job, interval=24 * 3600, first=3600)
    app.run_polling()


if __name__ == "__main__":
    main()
