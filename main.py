import os, asyncio, requests, anthropic
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-haiku-5-5"

SYSTEM = (
    "Sen o'zbek tilida javob beradigan futbol tahlilchisan va yordamchisan. "
    "Prognoz berishdan oldin asboblar orqali jadval va jamoa formasini ol. "
    "Javobda taxminiy natija va qisqa sababni yoz. "
    "Prognoz kafolat emasligini bir jumlada eslat."
)

TOOLS = [
    {"name": "get_time",
     "description": "Toshkent bo'yicha hozirgi vaqt va sanani beradi",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "prayer_times",
     "description": "Shahar uchun bugungi namoz vaqtlarini beradi",
     "input_schema": {"type": "object",
                      "properties": {"city": {"type": "string"}},
                      "required": ["city"]}},
    {"name": "calculate",
     "description": "Matematik ifodani hisoblaydi, masalan 12*5+3",
     "input_schema": {"type": "object",
                      "properties": {"expression": {"type": "string"}},
                      "required": ["expression"]}},
    {"name": "upcoming_matches",
     "description": "Liganing yaqin o'yinlarini beradi. Kodlar: PL (Angliya), PD (Ispaniya), SA (Italiya), BL1 (Germaniya), FL1 (Fransiya), CL (Chempionlar ligasi)",
     "input_schema": {"type": "object",
                      "properties": {"league": {"type": "string"}},
                      "required": ["league"]}},
    {"name": "standings",
     "description": "Liga turnir jadvalini beradi",
     "input_schema": {"type": "object",
                      "properties": {"league": {"type": "string"}},
                      "required": ["league"]}},
    {"name": "team_form",
     "description": "Jamoaning oxirgi 5 ta o'yin natijasi (team_id kerak, upcoming_matches natijasida bor)",
     "input_schema": {"type": "object",
                      "properties": {"team_id": {"type": "integer"}},
                      "required": ["team_id"]}},
]


def run_tool(name, args):
    try:
        if name == "get_time":
            return datetime.now(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d %H:%M")

        if name == "prayer_times":
            r = requests.get("https://api.aladhan.com/v1/timingsByCity",
                             params={"city": args["city"], "country": "Uzbekistan", "method": 2},
                             timeout=10).json()
            return str(r["data"]["timings"])

        if name in ("upcoming_matches", "standings", "team_form"):
            h = {"X-Auth-Token": os.environ["FOOTBALL_API_KEY"]}
            base = "https://api.football-data.org/v4"
            if name == "upcoming_matches":
                d = requests.get(f"{base}/competitions/{args['league']}/matches",
                                 params={"status": "SCHEDULED"}, headers=h, timeout=10).json()
                return str([{"id_home": m["homeTeam"]["id"], "home": m["homeTeam"]["name"],
                             "id_away": m["awayTeam"]["id"], "away": m["awayTeam"]["name"],
                             "date": m["utcDate"]} for m in d["matches"][:8]])
            if name == "standings":
                d = requests.get(f"{base}/competitions/{args['league']}/standings",
                                 headers=h, timeout=10).json()
                return str([{"o'rin": r["position"], "jamoa": r["team"]["name"],
                             "o'yin": r["playedGames"], "ochko": r["points"],
                             "gol": f'{r["goalsFor"]}-{r["goalsAgainst"]}'}
                            for r in d["standings"][0]["table"][:20]])
            if name == "team_form":
                d = requests.get(f"{base}/teams/{args['team_id']}/matches",
                                 params={"status": "FINISHED", "limit": 5},
                                 headers=h, timeout=10).json()
                return str([f'{m["homeTeam"]["name"]} {m["score"]["fullTime"]["home"]}-'
                            f'{m["score"]["fullTime"]["away"]} {m["awayTeam"]["name"]}'
                            for m in d["matches"]])

        if name == "calculate":
            expr = args["expression"]
            if not set(expr) <= set("0123456789+-*/(). "):
                return "Xato: ruxsat etilmagan belgi"
            return str(eval(expr, {"__builtins__": {}}))
    except Exception as e:
        return f"Xato: {e}"
    return "Noma'lum asbob"


def agent(user_text):
    messages = [{"role": "user", "content": user_text}]
    for _ in range(6):  # ko'pi bilan 6 qadam
        resp = client.messages.create(model=MODEL, max_tokens=1000,
                                      system=SYSTEM, tools=TOOLS, messages=messages)
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")
        messages.append({"role": "assistant", "content": resp.content})
        results = [{"type": "tool_result", "tool_use_id": b.id,
                    "content": run_tool(b.name, b.input)}
                   for b in resp.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": results})
    return "Javob topa olmadim."


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = await asyncio.to_thread(agent, update.message.text)
    await update.message.reply_text(answer)


app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
app.run_polling()
                 
