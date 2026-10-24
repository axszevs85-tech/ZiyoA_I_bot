import os, time, asyncio, requests, anthropic
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import (ApplicationBuilder, MessageHandler, CommandHandler,
                          filters, ContextTypes)

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-haiku-5-5"
BASE = "https://api.football-data.org/v4"
HISTORY = {}
CACHE = {}
MAX_DEEP = 4  # bir javobda team_form + h2h jami

SYSTEM = (
    "Sen o'zbek tilida gaplashadigan aqlli, samimiy AI yordamchisan. "
    "Oddiy savol, suhbat, maslahat, yozish, tarjima va boshqa hamma narsada "
    "oddiy AI sifatida o'zing javob ber, asboblarni ishlatma. "
    "Faqat futbol o'yinlari, natijalar, jadval yoki prognoz so'ralgandagina futbol asboblarini ishlat. "
    "Bugungi sana: {today} (Toshkent). "
    "Bitta liga bo'yicha ko'p o'yin so'ralsa upcoming_with_stats ishlat "
    "(o'yinlar va jamoalar statistikasini bir yo'la beradi). Sana bo'yicha o'yinlar uchun matches_by_date. "
    "Bir javobda team_form va h2h ni jami 4 martadan ko'p chaqirma, qolgan o'yinlar uchun jadvalga tayan. "
    "Prognozda: o'yin, taxminiy natija (G1 / Durang / G2), taxminiy hisob, 2-3 qisqa sabab. "
    "Faqat asboblardan olgan ma'lumotga tayan, raqamlarni o'ylab topma. "
    "Hech qachon 'ma'lumot olaman' deb to'xtama, darhol yakuniy javobni yoz. "
    "Ma'lumot chiqmasa, bu liga tarifga kirmasligi mumkinligini ayt. "
    "Futbol prognozi oxirida kafolat emasligini bir jumlada eslat. "
    "Markdown belgilarini (** yoki #) ishlatma, oddiy matn yoz."
)

TOOLS = [
    {"name": "competitions",
     "description": "Mavjud ligalar ro'yxati (kod, nom, davlat)",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "matches_by_date",
     "description": "Barcha ligalardagi o'yinlar. Sana YYYY-MM-DD, oraliq 10 kundan oshmasin. Bo'sh bo'lsa bugun.",
     "input_schema": {"type": "object",
                      "properties": {"date_from": {"type": "string"},
                                     "date_to": {"type": "string"}}}},
    {"name": "upcoming_with_stats",
     "description": "Bitta liganing yaqin o'yinlari + har jamoaning jadvaldagi o'rni, ochko, golllari. Kodlar: PL Angliya, PD Ispaniya, SA Italiya, BL1 Germaniya, FL1 Fransiya, CL Chempionlar ligasi, DED Niderlandiya, PPL Portugaliya, ELC Angliya Chempionat, BSA Braziliya",
     "input_schema": {"type": "object",
                      "properties": {"league": {"type": "string"}},
                      "required": ["league"]}},
    {"name": "standings",
     "description": "Liga turnir jadvali",
     "input_schema": {"type": "object",
                      "properties": {"league": {"type": "string"}},
                      "required": ["league"]}},
    {"name": "team_form",
     "description": "Jamoaning oxirgi 5 ta o'yini (team_id kerak)",
     "input_schema": {"type": "object",
                      "properties": {"team_id": {"type": "integer"}},
                      "required": ["team_id"]}},
    {"name": "h2h",
     "description": "Ikki jamoaning oxirgi o'zaro uchrashuvlari (match_id kerak)",
     "input_schema": {"type": "object",
                      "properties": {"match_id": {"type": "integer"}},
                      "required": ["match_id"]}},
]


def api(path, params=None):
    key = (path, str(sorted((params or {}).items())))
    hit = CACHE.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    r = None
    for attempt in range(2):
        r = requests.get(BASE + path, params=params, timeout=15,
                         headers={"X-Auth-Token": os.environ["FOOTBALL_API_KEY"]})
        if r.status_code == 429 and attempt == 0:
            time.sleep(20)
            continue
        break
    if r.status_code == 200:
        data = r.json()
        CACHE[key] = (time.time(), data)
        return data
    if r.status_code == 403:
        raise Exception("Bu liga tarifga kirmaydi (403)")
    if r.status_code == 429:
        raise Exception("So'rovlar limiti tugadi, 1 daqiqadan keyin urinib ko'ring (429)")
    raise Exception(f"API xato {r.status_code}")


def score(m):
    s = m["score"]["fullTime"]
    return f'{s["home"]}-{s["away"]}' if s["home"] is not None else "-"


def short(m):
    return {
        "match_id": m["id"],
        "liga": m.get("competition", {}).get("name", ""),
        "davlat": m.get("area", {}).get("name", ""),
        "uy": m["homeTeam"]["name"], "uy_id": m["homeTeam"]["id"],
        "mehmon": m["awayTeam"]["name"], "mehmon_id": m["awayTeam"]["id"],
        "vaqt_utc": m["utcDate"], "holat": m["status"], "hisob": score(m),
    }


def table_map(league):
    d = api(f"/competitions/{league}/standings")
    rows = {}
    for s in d["standings"]:
        if s.get("type") == "TOTAL":
            for r in s["table"]:
                rows[r["team"]["id"]] = {
                    "orin": r["position"], "jamoa": r["team"]["name"],
                    "oyin": r["playedGames"],
                    "g": r["won"], "d": r["draw"], "m": r["lost"],
                    "gol": f'{r["goalsFor"]}-{r["goalsAgainst"]}',
                    "ochko": r["points"]}
    return rows


def run_tool(name, a, used):
    try:
        if name in ("team_form", "h2h"):
            if used["n"] >= MAX_DEEP:
                return "Limit: bu javobda boshqa team_form/h2h so'rama, jadval asosida javob yoz."
            used["n"] += 1
        if name == "competitions":
            d = api("/competitions")
            return str([{"kod": c["code"], "nom": c["name"], "davlat": c["area"]["name"]}
                        for c in d["competitions"]])
        if name == "matches_by_date":
            today = datetime.now(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d")
            d = api("/matches", {"dateFrom": a.get("date_from") or today,
                                 "dateTo": a.get("date_to") or a.get("date_from") or today})
            return str([short(m) for m in d["matches"][:40]]) or "O'yin yo'q"
        if name == "upcoming_with_stats":
            st = table_map(a["league"])
            d = api(f"/competitions/{a['league']}/matches", {"status": "SCHEDULED"})
            out = []
            for m in d["matches"][:10]:
                x = short(m)
                x["uy_stat"] = st.get(m["homeTeam"]["id"])
                x["mehmon_stat"] = st.get(m["awayTeam"]["id"])
                out.append(x)
            return str(out)
        if name == "standings":
            st = table_map(a["league"])
            return str(sorted(st.values(), key=lambda i: i["orin"])[:20])
        if name == "team_form":
            d = api(f"/teams/{a['team_id']}/matches", {"status": "FINISHED", "limit": 5})
            return str([f'{m["homeTeam"]["name"]} {score(m)} {m["awayTeam"]["name"]}'
                        for m in d["matches"]])
        if name == "h2h":
            d = api(f"/matches/{a['match_id']}/head2head", {"limit": 5})
            return str([f'{m["homeTeam"]["name"]} {score(m)} {m["awayTeam"]["name"]}'
                        for m in d["matches"]])
    except Exception as e:
        return f"Xato: {e}"
    return "Noma'lum asbob"


def agent(user_text, hist):
    today = datetime.now(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d %H:%M")
    messages = hist + [{"role": "user", "content": user_text}]
    used = {"n": 0}
    text = "Kechirasiz, javob tayyorlay olmadim. Qaytadan urinib ko'ring."
    for _ in range(12):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=3000,
                system=SYSTEM.format(today=today),
                tools=TOOLS, messages=messages)
        except Exception as e:
            return f"AI xatosi: {e}"
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text") or text
            break
        messages.append({"role": "assistant", "content": resp.content})
        results = [{"type": "tool_result", "tool_use_id": b.id,
                    "content": run_tool(b.name, b.input, used)}
                   for b in resp.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": results})
    hist.extend([{"role": "user", "content": user_text},
                 {"role": "assistant", "content": text}])
    del hist[:-20]
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    HISTORY.pop(update.effective_chat.id, None)
    await update.message.reply_text(
        "Salom! Men AI yordamchiman. Xohlagan narsa haqida gaplashishimiz mumkin.\n"
        "Futbol haqida so'rasangiz, jonli ma'lumot bilan tahlil qilaman.\n"
        "Masalan: \"Bugungi o'yinlar prognozi\", \"Premier Liga jadvali\".")


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    await context.bot.send_chat_action(msg.chat_id, "typing")
    hist = HISTORY.setdefault(msg.chat_id, [])
    answer = await asyncio.to_thread(agent, msg.text, hist)
    for i in range(0, len(answer), 4000):
        await msg.reply_text(answer[i:i + 4000])


app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(
    filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, handle))
app.run_polling()
