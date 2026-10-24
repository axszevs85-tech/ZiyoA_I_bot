import os, asyncio, requests, anthropic
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import (ApplicationBuilder, MessageHandler, CommandHandler,
                          filters, ContextTypes)

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-haiku-5-5"
BASE = "https://api.football-data.org/v4"
HISTORY = {}

SYSTEM = (
    "Sen o'zbek tilida javob beradigan professional futbol tahlilchisan. "
    "Bugungi sana: {today}. "
    "Prognoz so'ralsa: avval o'yinlarni top (matches_by_date yoki upcoming_matches), "
    "keyin standings, team_form va h2h asboblari bilan ma'lumot ol. "
    "Javob formati: o'yin, taxminiy natija (g'alaba/durang/g'alaba), taxminiy hisob, "
    "2-3 qisqa sabab. Faqat asboblardan olgan ma'lumotga tayan, o'ylab topma. "
    "Ma'lumot chiqmasa, bu liga tarifga kirmasligi mumkinligini ayt. "
    "Oxirida prognoz kafolat emasligini bir jumlada eslat."
)

TOOLS = [
    {"name": "competitions",
     "description": "Mavjud ligalar va turnirlar ro'yxati (kod, nom, davlat)",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "matches_by_date",
     "description": "Barcha mavjud ligalardagi o'yinlar. Sana YYYY-MM-DD. Oraliq 10 kundan oshmasin. Bo'sh qoldirilsa bugun.",
     "input_schema": {"type": "object",
                      "properties": {"date_from": {"type": "string"},
                                     "date_to": {"type": "string"}}}},
    {"name": "upcoming_matches",
     "description": "Bitta liganing yaqin o'yinlari. Kodlar: PL Angliya, PD Ispaniya, SA Italiya, BL1 Germaniya, FL1 Fransiya, CL Chempionlar ligasi, DED Niderlandiya, PPL Portugaliya, ELC Angliya Chempionat, BSA Braziliya, EC Yevro, WC Jahon chempionati",
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
    r = requests.get(BASE + path, params=params, timeout=15,
                     headers={"X-Auth-Token": os.environ["FOOTBALL_API_KEY"]})
    if r.status_code == 200:
        return r.json()
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


def run_tool(name, a):
    try:
        if name == "competitions":
            d = api("/competitions")
            return str([{"kod": c["code"], "nom": c["name"], "davlat": c["area"]["name"]}
                        for c in d["competitions"]])
        if name == "matches_by_date":
            today = datetime.now(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d")
            d = api("/matches", {"dateFrom": a.get("date_from") or today,
                                 "dateTo": a.get("date_to") or a.get("date_from") or today})
            return str([short(m) for m in d["matches"][:40]]) or "O'yin yo'q"
        if name == "upcoming_matches":
            d = api(f"/competitions/{a['league']}/matches", {"status": "SCHEDULED"})
            return str([short(m) for m in d["matches"][:10]])
        if name == "standings":
            d = api(f"/competitions/{a['league']}/standings")
            t = d["standings"][0]["table"][:20]
            return str([{"orin": r["position"], "jamoa": r["team"]["name"],
                         "oyin": r["playedGames"], "g": r["won"], "d": r["draw"],
                         "m": r["lost"], "gol": f'{r["goalsFor"]}-{r["goalsAgainst"]}',
                         "ochko": r["points"]} for r in t])
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
    text = "Javob topa olmadim."
    for _ in range(8):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=1200,
                system=SYSTEM.format(today=today),
                tools=TOOLS, messages=messages)
        except Exception as e:
            return f"AI xatosi: {e}"
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text") or text
            break
        messages.append({"role": "assistant", "content": resp.content})
        results = [{"type": "tool_result", "tool_use_id": b.id,
                    "content": run_tool(b.name, b.input)}
                   for b in resp.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": results})
    hist.extend([{"role": "user", "content": user_text},
                 {"role": "assistant", "content": text}])
    del hist[:-12]
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men futbol tahlilchisiman.\n"
        "Masalan: \"Bugungi o'yinlar prognozi\", \"Angliya ligasi jadvali\", "
        "\"Real Madrid oxirgi formasi\".")


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    hist = HISTORY.setdefault(msg.chat_id, [])
    answer = await asyncio.to_thread(agent, msg.text, hist)
    await msg.reply_text(answer[:4000])


app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(
    filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, handle))
app.run_polling()
