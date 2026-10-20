import os, re, json, time, random, datetime, logging
import feedparser
from anthropic import AsyncAnthropic
from telegram import Update, InlineKeyboardButton as B, InlineKeyboardMarkup as M
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
ai = AsyncAnthropic()
MODEL = "claude-haiku-5-5"
DB = "data.json"
TZ = datetime.timezone(datetime.timedelta(hours=5))

KINDS = {
    "qisqa": "qisqa (3-5 gaplik) post",
    "uzun": "batafsil (150-200 so'zli) maqola-post",
    "savol": "obunachilarni muhokamaga chorlaydigan savolli post",
    "fakt": "kam ma'lum qiziqarli fakt haqida post",
}
RW = {
    "improve": "Matnni ravon, qiziqarli va xatosiz qilib qayta yoz. Ma'nosini saqla.",
    "short": "Matnni 2 barobar qisqartir, asosiy ma'noni saqla.",
    "ru": "Matnni rus tiliga tabiiy tarjima qil.",
    "en": "Matnni ingliz tiliga tabiiy tarjima qil.",
    "uz": "Matnni o'zbek tiliga tabiiy tarjima qil.",
    "tags": "Matn oxiriga 5 ta mos hashtag qo'sh. Matnni o'zgartirma.",
}
DEFAULT = {
    "sources": [], "seen": [], "topic": "", "style": "", "schedules": [], "queue": [],
    "recent": [], "last": {}, "auto_ai": False, "auto_rss": True, "auto_quiz": False,
    "group_ai": False, "group_mod": False, "ai_hours": 6, "rss_min": 30, "quiz_hours": 24,
}


# ---------- storage ----------
def load():
    d = json.loads(json.dumps(DEFAULT))
    if os.path.exists(DB):
        d.update(json.load(open(DB, encoding="utf-8")))
    return d


def save(d):
    json.dump(d, open(DB, "w", encoding="utf-8"), ensure_ascii=False)


def remember(d, text):
    d["recent"].append(text[:60].replace("\n", " "))
    d["recent"] = d["recent"][-20:]


# ---------- AI ----------
def system(d):
    s = ("Sen professional Telegram kanal muharririsan. Odatiy til: o'zbek (lotin). "
         "Javobda faqat tayyor matnni yoz, izoh yoki kirish gap yozma.")
    if d["topic"]:
        s += f" Kanal mavzusi: {d['topic']}."
    if d["style"]:
        s += f" Kanal uslubi: {d['style']}."
    return s


async def ask_ai(prompt, d, max_tokens=800):
    r = await ai.messages.create(model=MODEL, max_tokens=max_tokens, system=system(d),
                                 messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in r.content if b.type == "text").strip()


def parse_json(raw):
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    return json.loads(raw)


async def gen_post(d, kind):
    p = f"Kanal uchun {KINDS[kind]} yoz. Emoji va hashtaglarni me'yorida ishlat."
    if d["recent"]:
        p += " Quyidagi mavzularni takrorlama: " + "; ".join(d["recent"][-10:])
    return await ask_ai(p, d)


async def gen_plan(d):
    p = ("Kanal uchun 7 kunlik kontent rejasi tuz: har kun uchun tayyor post. Formatlar turli bo'lsin "
         "(fakt, savol, maslahat, qisqa maqola). FAQAT JSON massiv qaytar: "
         "[{\"kun\":\"Dushanba\",\"text\":\"...\"}]")
    return parse_json(await ask_ai(p, d, 3500))


async def gen_quiz(d):
    p = ("Kanal mavzusiga oid bitta viktorina savoli tuz. FAQAT JSON qaytar: "
         "{\"q\":\"savol\",\"options\":[\"A\",\"B\",\"C\",\"D\"],\"correct\":0,\"explain\":\"qisqa izoh\"}. "
         "correct 0-3 indeks, to'g'ri javob joyi tasodifiy bo'lsin.")
    if d["recent"]:
        p += " Quyidagilarni takrorlama: " + "; ".join(d["recent"][-10:])
    return parse_json(await ask_ai(p, d, 600))


async def send_quiz(bot, chat, j, kb=None, anonymous=True):
    await bot.send_poll(chat, j["q"][:300], [str(o)[:100] for o in j["options"][:4]], type="quiz",
                        correct_option_id=int(j["correct"]), explanation=str(j.get("explain", ""))[:190],
                        is_anonymous=anonymous, reply_markup=kb)


# ---------- keyboards ----------
def on(v):
    return "✅" if v else "⛔"


def menu():
    return M([
        [B("📝 Post joylash", callback_data="post"), B("⏰ Jadval", callback_data="jadval")],
        [B("🤖 AI post", callback_data="ai"), B("📅 Haftalik reja", callback_data="plan")],
        [B("✍️ AI tahrir", callback_data="rw"), B("🧠 AI viktorina", callback_data="quiz")],
        [B("📰 Manbalar", callback_data="manba"), B("🎯 Mavzu va uslub", callback_data="mavzu")],
        [B("💬 Guruh AI", callback_data="grp"), B("⚙️ Sozlamalar", callback_data="set")],
    ])


def back():
    return M([[B("⬅️ Orqaga", callback_data="menu")]])


def prev_kb():
    return M([[B("✅ Joylash", callback_data="pub"), B("🔄 Qayta", callback_data="regen")],
              [B("✏️ AI bilan o'zgartirish", callback_data="aiedit")],
              [B("❌ Bekor", callback_data="menu")]])


def set_kb(d):
    return M([
        [B(f"{on(d['auto_ai'])} AI avto-post", callback_data="t_ai"), B(f"⏱ {d['ai_hours']} soat", callback_data="c_ai")],
        [B(f"{on(d['auto_rss'])} RSS avto-post", callback_data="t_rss"), B(f"⏱ {d['rss_min']} daq", callback_data="c_rss")],
        [B(f"{on(d['auto_quiz'])} AI viktorina", callback_data="t_quiz"), B(f"⏱ {d['quiz_hours']} soat", callback_data="c_quiz")],
        [B("⬅️ Orqaga", callback_data="menu")],
    ])


def set_text(d):
    return (f"Mavzu: {d['topic'] or '—'}\nUslub: {d['style'] or '—'}\n"
            f"Manbalar: {len(d['sources'])} ta | Jadval: {len(d['schedules'])} ta | Navbat: {len(d['queue'])} ta")


def grp_kb(d):
    return M([[B(f"{on(d['group_ai'])} Savollarga AI javob", callback_data="t_gai")],
              [B(f"{on(d['group_mod'])} AI spam filtr", callback_data="t_gmod")],
              [B("⬅️ Orqaga", callback_data="menu")]])


GRP_TEXT = ("💬 Kanal guruhi (izohlar) uchun AI:\n\n"
            "1. Botni guruhga qo'shing va admin qiling (xabar o'chirish huquqi bilan)\n"
            "2. @BotFather → /setprivacy → Disable\n"
            "3. Pastdan yoqing\n\n"
            "Javob: bot nomi tegilganda yoki bot xabariga reply qilinganda.\n"
            "Spam filtr: havolali/reklama xabarlarni AI tekshirib o'chiradi.")


async def show(u, text, kb):
    try:
        await u.callback_query.edit_message_text(text, reply_markup=kb)
    except Exception:
        pass


# ---------- preview helpers ----------
async def run_preview(c, send, coro):
    try:
        t = await coro
    except Exception as e:
        logging.exception("AI")
        await send(f"⚠️ AI xatosi: {str(e)[:150]}", back())
        return
    c.user_data["pending"] = t
    await send(t[:4000], prev_kb())


def regen_coro(c):
    d = load()
    r = c.user_data.get("regen")
    if not r:
        return None
    if r[0] == "post":
        return gen_post(d, r[1])
    if r[0] == "rw":
        return ask_ai(f"{RW[r[1]]}\n\nMatn:\n{c.user_data.get('src', '')}", d)


async def act_ai_menu(send):
    kb = M([[B("⚡ Qisqa post", callback_data="k:qisqa"), B("📄 Uzun post", callback_data="k:uzun")],
            [B("❓ Savolli post", callback_data="k:savol"), B("💡 Qiziqarli fakt", callback_data="k:fakt")],
            [B("⬅️ Orqaga", callback_data="menu")]])
    await send("Qanday post yozay?", kb)


async def act_plan(c, send):
    d = load()
    try:
        plan = await gen_plan(d)
    except Exception as e:
        logging.exception("plan")
        await send(f"⚠️ Reja tuzilmadi: {str(e)[:150]}", back())
        return
    c.user_data["plan"] = plan
    lines = [f"{i + 1}. {p['kun']}: {p['text'][:70].replace(chr(10), ' ')}..." for i, p in enumerate(plan)]
    kb = M([[B("✅ Jadvalga qo'yish", callback_data="plan_ok")],
            [B("🔄 Qayta tuzish", callback_data="plan_re"), B("❌ Bekor", callback_data="menu")]])
    await send("📅 7 kunlik reja:\n\n" + "\n".join(lines), kb)


async def act_quiz(c, send):
    d = load()
    try:
        j = await gen_quiz(d)
        c.user_data["quiz"] = j
        kb = M([[B("✅ Kanalga joylash", callback_data="qpub"), B("🔄 Boshqasi", callback_data="quiz")],
                [B("⬅️ Menyu", callback_data="menu")]])
        await send_quiz(c.bot, ADMIN_ID, j, kb, anonymous=False)
    except Exception as e:
        logging.exception("quiz")
        await send(f"⚠️ Viktorina tuzilmadi: {str(e)[:150]}", back())


# ---------- jobs ----------
async def send_job(ctx):
    await ctx.bot.send_message(CHANNEL_ID, ctx.job.data)


def reg(jq, s):
    hh, mm = map(int, s["time"].split(":"))
    jq.run_daily(send_job, datetime.time(hh, mm, tzinfo=TZ), data=s["text"], name=f"sch{s['id']}")


async def send_once(ctx):
    it = ctx.job.data
    await ctx.bot.send_message(CHANNEL_ID, it["text"][:4000])
    d = load()
    d["queue"] = [x for x in d["queue"] if x["id"] != it["id"]]
    remember(d, it["text"])
    save(d)


def reg_once(jq, it):
    at = datetime.datetime.fromisoformat(it["at"])
    if at > datetime.datetime.now(TZ):
        jq.run_once(send_once, when=at, data=it, name=f"q{it['id']}")


async def tick(ctx):
    d = load()
    now = time.time()
    last = d["last"]
    due = []
    if d["auto_ai"] and d["topic"] and now - last.get("ai", 0) >= d["ai_hours"] * 3600:
        due.append("ai")
    if d["auto_rss"] and d["sources"] and now - last.get("rss", 0) >= d["rss_min"] * 60:
        due.append("rss")
    if d["auto_quiz"] and d["topic"] and now - last.get("quiz", 0) >= d["quiz_hours"] * 3600:
        due.append("quiz")
    if not due:
        return
    for k in due:
        last[k] = now
    save(d)
    for k in due:
        try:
            if k == "ai":
                d = load()
                t = await gen_post(d, random.choice(list(KINDS)))
                await ctx.bot.send_message(CHANNEL_ID, t[:4000])
                d = load(); remember(d, t); save(d)
            elif k == "quiz":
                d = load()
                j = await gen_quiz(d)
                await send_quiz(ctx.bot, CHANNEL_ID, j)
                d = load(); remember(d, j["q"]); save(d)
            elif k == "rss":
                d = load()
                seen, count = d["seen"], 0
                for url in d["sources"]:
                    for e in feedparser.parse(url).entries[:3]:
                        if e.link in seen or count >= 3:
                            continue
                        seen.append(e.link)
                        summ = re.sub(r"<[^>]+>", "", e.get("summary", ""))[:800]
                        t = await ask_ai(f"Quyidagi yangilikni o'zbek tilida qisqa (3-4 gap), qiziqarli kanal posti qilib yoz:\n{e.title}\n{summ}", d)
                        await ctx.bot.send_message(CHANNEL_ID, f"{t}\n\n{e.link}"[:4000])
                        count += 1
                d = load(); d["seen"] = seen[-500:]; save(d)
        except Exception:
            logging.exception(f"tick {k}")


# ---------- commands & buttons ----------
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        return
    c.user_data["mode"] = None
    await u.message.reply_text("🤖 AI kanal boshqaruv paneli 👇", reply_markup=menu())


async def cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    d = load()
    x = q.data
    c.user_data["mode"] = None
    c.user_data.pop("after", None)
    send = lambda t, k: show(u, t, k)

    def need_topic(after):
        c.user_data["mode"] = "mavzu"
        c.user_data["after"] = after
        return show(u, "Avval kanal mavzusini yozing (masalan: futbol yangiliklari) 👇", back())

    if x == "menu":
        await show(u, "🤖 AI kanal boshqaruv paneli 👇", menu())

    elif x == "post":
        c.user_data["mode"] = "post"
        await show(u, "Postni yuboring (matn, rasm yoki video) 👇", back())

    # --- schedules ---
    elif x == "jadval":
        rows = [[B(f"❌ {s['time']} - {s['text'][:20]}", callback_data=f"jdel:{s['id']}")] for s in d["schedules"]]
        rows.append([B("➕ Kunlik jadval", callback_data="jadd")])
        if d["queue"]:
            rows.append([B(f"🗑 Navbatni tozalash ({len(d['queue'])} ta)", callback_data="qclear")])
        rows.append([B("⬅️ Orqaga", callback_data="menu")])
        await show(u, "Har kuni joylanadigan postlar:" if d["schedules"] else "Kunlik jadval bo'sh.", M(rows))

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

    elif x == "qclear":
        for it in d["queue"]:
            for j in c.job_queue.get_jobs_by_name(f"q{it['id']}"):
                j.schedule_removal()
        d["queue"] = []
        save(d)
        await show(u, "Navbat tozalandi ✅", M([[B("⬅️ Jadvalga", callback_data="jadval")]]))

    # --- AI post ---
    elif x == "ai":
        if not d["topic"]:
            await need_topic("ai")
        else:
            await act_ai_menu(send)

    elif x.startswith("k:"):
        kind = x[2:]
        c.user_data["regen"] = ("post", kind)
        await show(u, "⏳ Yozilmoqda...", None)
        await run_preview(c, send, gen_post(d, kind))

    elif x == "regen":
        coro = regen_coro(c)
        if coro is None:
            await show(u, "Bu postni qayta yozib bo'lmaydi.", menu())
        else:
            await show(u, "⏳ Yozilmoqda...", None)
            await run_preview(c, send, coro)

    elif x == "aiedit":
        c.user_data["mode"] = "edit"
        await show(u, "Qanday o'zgartiray? (masalan: qisqaroq qil, ruscha yoz, hazil qo'sh) 👇", back())

    elif x == "pub":
        t = c.user_data.get("pending")
        if t:
            await c.bot.send_message(CHANNEL_ID, t[:4000])
            remember(d, t)
            save(d)
            await show(u, "✅ Kanalga joylandi", menu())

    # --- AI rewrite ---
    elif x == "rw":
        c.user_data["mode"] = "rw"
        await show(u, "Tahrirlash uchun matnni yuboring 👇", back())

    elif x.startswith("rw:"):
        c.user_data["regen"] = ("rw", x[3:])
        await show(u, "⏳ Ishlanmoqda...", None)
        await run_preview(c, send, regen_coro(c))

    # --- weekly plan ---
    elif x == "plan":
        if not d["topic"]:
            await need_topic("plan")
        else:
            await show(u, "⏳ 7 kunlik reja tuzilmoqda (biroz vaqt oladi)...", None)
            await act_plan(c, send)

    elif x == "plan_re":
        await show(u, "⏳ Qayta tuzilmoqda...", None)
        await act_plan(c, send)

    elif x == "plan_ok":
        c.user_data["mode"] = "plan_time"
        await show(u, "Har kuni qaysi vaqtda joylansin? Masalan: 09:30 (ertadan boshlanadi)", back())

    # --- quiz ---
    elif x == "quiz":
        if not d["topic"]:
            await need_topic("quiz")
        else:
            await show(u, "⏳ Savol tuzilmoqda...", None)
            await act_quiz(c, send)

    elif x == "qpub":
        j = c.user_data.get("quiz")
        if j:
            await send_quiz(c.bot, CHANNEL_ID, j)
            remember(d, j["q"])
            save(d)
            await c.bot.send_message(ADMIN_ID, "✅ Viktorina kanalga joylandi", reply_markup=menu())

    # --- topic & style ---
    elif x == "mavzu":
        kb = M([[B("🎯 Mavzuni o'zgartirish", callback_data="mset"), B("🎨 Uslubni o'zgartirish", callback_data="sset")],
                [B("⬅️ Orqaga", callback_data="menu")]])
        await show(u, f"🎯 Mavzu: {d['topic'] or '—'}\n🎨 Uslub: {d['style'] or '—'}\n\n"
                      "Hamma AI funksiyalar shu mavzu va uslubga qarab yozadi.", kb)

    elif x == "mset":
        c.user_data["mode"] = "mavzu"
        await show(u, "Yangi mavzuni yozing 👇", back())

    elif x == "sset":
        c.user_data["mode"] = "style"
        await show(u, "Uslubni yozing. Masalan: yoshlar uchun, hazil bilan, qisqa gaplar 👇", back())

    # --- sources ---
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

    # --- group AI ---
    elif x == "grp":
        await show(u, GRP_TEXT, grp_kb(d))

    # --- settings ---
    elif x == "set":
        await show(u, set_text(d), set_kb(d))

    elif x in ("t_ai", "t_rss", "t_quiz", "t_gai", "t_gmod"):
        key = {"t_ai": "auto_ai", "t_rss": "auto_rss", "t_quiz": "auto_quiz",
               "t_gai": "group_ai", "t_gmod": "group_mod"}[x]
        d[key] = not d[key]
        if d[key] and x in ("t_ai", "t_rss", "t_quiz"):
            d["last"][x[2:]] = time.time()
        save(d)
        if x in ("t_gai", "t_gmod"):
            await show(u, GRP_TEXT, grp_kb(d))
        else:
            await show(u, set_text(d), set_kb(d))

    elif x in ("c_ai", "c_rss", "c_quiz"):
        key, opts = {"c_ai": ("ai_hours", [3, 6, 12, 24]),
                     "c_rss": ("rss_min", [15, 30, 60, 180]),
                     "c_quiz": ("quiz_hours", [12, 24, 48])}[x]
        i = opts.index(d[key]) if d[key] in opts else -1
        d[key] = opts[(i + 1) % len(opts)]
        save(d)
        await show(u, set_text(d), set_kb(d))


# ---------- private messages ----------
async def on_msg(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        return
    m = u.message
    mode = c.user_data.get("mode")
    text = (m.text or "").strip()
    d = load()
    reply = lambda t, k: m.reply_text(t[:4000], reply_markup=k)

    if mode == "post":
        await c.bot.copy_message(CHANNEL_ID, m.chat_id, m.message_id)
        c.user_data["mode"] = None
        await m.reply_text("✅ Joylandi", reply_markup=menu())

    elif mode == "jt":
        try:
            hh, mm = map(int, text.split(":"))
            assert 0 <= hh < 24 and 0 <= mm < 60
        except Exception:
            await m.reply_text("Format noto'g'ri. Masalan: 09:30")
            return
        c.user_data["jt"] = f"{hh:02d}:{mm:02d}"
        c.user_data["mode"] = "jx"
        await m.reply_text("Endi shu vaqtda joylanadigan matnni yozing 👇")

    elif mode == "jx":
        s = {"id": int(time.time()), "time": c.user_data["jt"], "text": text}
        d["schedules"].append(s)
        save(d)
        reg(c.job_queue, s)
        c.user_data["mode"] = None
        await m.reply_text(f"✅ Har kuni {s['time']} da joylanadi", reply_markup=menu())

    elif mode == "plan_time":
        try:
            hh, mm = map(int, text.split(":"))
            assert 0 <= hh < 24 and 0 <= mm < 60
        except Exception:
            await m.reply_text("Format noto'g'ri. Masalan: 09:30")
            return
        plan = c.user_data.get("plan") or []
        base = datetime.datetime.now(TZ).date() + datetime.timedelta(days=1)
        for i, p in enumerate(plan):
            day = base + datetime.timedelta(days=i)
            at = datetime.datetime.combine(day, datetime.time(hh, mm), tzinfo=TZ)
            it = {"id": int(time.time() * 1000) + i, "at": at.isoformat(), "text": p["text"]}
            d["queue"].append(it)
            reg_once(c.job_queue, it)
        save(d)
        c.user_data["mode"] = None
        await m.reply_text(f"✅ {len(plan)} ta post ertadan boshlab har kuni {hh:02d}:{mm:02d} da joylanadi", reply_markup=menu())

    elif mode == "manba":
        if not text.startswith("http"):
            await m.reply_text("Havola http bilan boshlanishi kerak")
            return
        d["sources"].append(text)
        save(d)
        c.user_data["mode"] = None
        await m.reply_text("✅ Manba qo'shildi", reply_markup=menu())

    elif mode == "style":
        d["style"] = text
        save(d)
        c.user_data["mode"] = None
        await m.reply_text("✅ Uslub saqlandi", reply_markup=menu())

    elif mode == "mavzu":
        d["topic"] = text
        save(d)
        c.user_data["mode"] = None
        after = c.user_data.pop("after", None)
        if after == "ai":
            await act_ai_menu(reply)
        elif after == "plan":
            await m.reply_text("⏳ 7 kunlik reja tuzilmoqda...")
            await act_plan(c, reply)
        elif after == "quiz":
            await m.reply_text("⏳ Savol tuzilmoqda...")
            await act_quiz(c, reply)
        else:
            await m.reply_text("✅ Mavzu saqlandi", reply_markup=menu())

    elif mode == "rw":
        c.user_data["src"] = text
        c.user_data["mode"] = None
        kb = M([[B("✨ Yaxshila", callback_data="rw:improve"), B("📉 Qisqartir", callback_data="rw:short")],
                [B("🇷🇺 Rus", callback_data="rw:ru"), B("🇬🇧 Ingliz", callback_data="rw:en"), B("🇺🇿 O'zbek", callback_data="rw:uz")],
                [B("#️⃣ Hashtag qo'sh", callback_data="rw:tags")],
                [B("⬅️ Orqaga", callback_data="menu")]])
        await m.reply_text("Nima qilay?", reply_markup=kb)

    elif mode == "edit":
        pending = c.user_data.get("pending", "")
        c.user_data["mode"] = None
        c.user_data["regen"] = None
        await m.reply_text("⏳ O'zgartirilmoqda...")
        await run_preview(c, reply, ask_ai(
            f"Quyidagi postni ko'rsatma bo'yicha o'zgartir. Ko'rsatma: {text}\n\nPost:\n{pending}", d))

    else:
        await m.reply_text("Menyudan tanlang 👇", reply_markup=menu())


# ---------- group: AI answers + spam filter ----------
async def on_group(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = u.message
    if not m or not m.text or not m.from_user or m.from_user.is_bot:
        return
    d = load()
    text = m.text
    uname = c.bot.username or ""
    mentioned = bool(uname) and f"@{uname}".lower() in text.lower()
    replied = bool(m.reply_to_message and m.reply_to_message.from_user
                   and m.reply_to_message.from_user.id == c.bot.id)

    if d["group_ai"] and (mentioned or replied):
        qtext = re.sub(f"@{re.escape(uname)}", "", text, flags=re.I).strip() if uname else text
        try:
            ans = await ask_ai(f"Kanal obunachisi savol berdi. Qisqa va foydali javob ber (ko'pi bilan 5 gap).\n\nSavol: {qtext}", d, 500)
            await m.reply_text(ans[:4000])
        except Exception:
            logging.exception("group ai")
        return

    if d["group_mod"] and re.search(r"(https?://|t\.me/|www\.|@\w{4,})", text):
        try:
            mem = await c.bot.get_chat_member(m.chat_id, m.from_user.id)
            if mem.status not in ("administrator", "creator"):
                v = await ask_ai(f"Bu xabar spam, reklama yoki firibgarlikmi? Faqat SPAM yoki OK deb javob ber.\n\nXabar: {text}", d, 10)
                if "SPAM" in v.upper():
                    await m.delete()
        except Exception:
            logging.exception("group mod")


async def on_error(u, c):
    logging.error("Xato", exc_info=c.error)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start, filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, on_group))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_msg))
    app.add_error_handler(on_error)
    d = load()
    for s in d["schedules"]:
        reg(app.job_queue, s)
    for it in d["queue"]:
        reg_once(app.job_queue, it)
    app.job_queue.run_repeating(tick, interval=60, first=30)
    app.run_polling()


if __name__ == "__main__":
    main()
