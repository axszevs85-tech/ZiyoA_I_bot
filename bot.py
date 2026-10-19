import os, asyncio, base64, hmac, hashlib, json, time
from datetime import date
from urllib.parse import parse_qsl

import httpx
from aiohttp import web
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      WebAppInfo, MenuButtonWebApp, BotCommand)
from telegram.constants import ChatAction
from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters, ContextTypes)

# ---------- Sozlamalar (Railway Variables) ----------
BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)       # sizning Telegram ID
CHANNEL = os.environ.get("CHANNEL", "").strip()            # masalan @mening_kanalim
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "30"))     # kuniga so'rovlar soni
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip().rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))

LANG = "Foydalanuvchi qaysi tilda yozsa, o'sha tilda (asosan o'zbekcha) javob ber. "
MODES = {
    "chat": ("💬 Suhbat", "Sen foydali AI yordamchisan. " + LANG + "Kod so'ralsa, to'liq kod yoz."),
    "tarjima": ("🌐 Tarjimon", "Sen tarjimonsan. Matn o'zbekcha bo'lsa inglizchaga, boshqa tilda bo'lsa o'zbekchaga tarjima qil. Faqat tarjimani yoz."),
    "post": ("📝 Post yozuvchi", "Sen Telegram kanallar uchun post yozuvchisan. Berilgan mavzu bo'yicha jozibador, qisqa post yoz, mos emojilar va hashtaglar qo'sh. O'zbek tilida yoz."),
    "kod": ("👨‍💻 Kod yordamchi", "Sen tajribali dasturchisan. " + LANG + "To'liq, ishlaydigan kod yoz va qisqa tushuntir."),
    "oqituvchi": ("🎓 O'qituvchi", "Sen sabrli o'qituvchisan. Mavzuni oddiy tilda, misollar bilan tushuntir, oxirida 2-3 ta qisqa test savoli ber. " + LANG),
}

# ---------- Xotira (qayta ishga tushganda tozalanadi) ----------
history, modes, users = {}, {}, set()
usage, usage_day = {}, [date.today()]


def allow(uid):
    if uid == ADMIN_ID:
        return True
    today = date.today()
    if usage_day[0] != today:
        usage.clear()
        usage_day[0] = today
    n = usage.get(uid, 0)
    if n >= DAILY_LIMIT:
        return False
    usage[uid] = n + 1
    return True


async def ask_claude(system, msgs, max_tokens=3000):
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": max_tokens,
                  "system": system, "messages": msgs})
    if r.status_code != 200:
        print("XATO:", r.status_code, r.text)
        r.raise_for_status()
    blocks = r.json()["content"]
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    return text or "Javob bo'sh keldi, qayta urinib ko'r."


async def subscribed(bot, uid):
    if not CHANNEL or uid == ADMIN_ID:
        return True
    try:
        m = await bot.get_chat_member(CHANNEL, uid)
        return m.status in ("member", "administrator", "creator", "owner", "restricted")
    except Exception as e:
        print("OBUNA TEKSHIRUVI XATO (bot kanalda admin emasmi?):", repr(e))
        return True


def menu_kb():
    rows = []
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton("🚀 Mini App ochish", web_app=WebAppInfo(url=WEBAPP_URL))])
    items = list(MODES.items())
    for i in range(0, len(items), 2):
        rows.append([InlineKeyboardButton(v[0], callback_data=f"mode:{k}") for k, v in items[i:i + 2]])
    return InlineKeyboardMarkup(rows)


async def send_long(message, text):
    for i in range(0, len(text), 4000):
        await message.reply_text(text[i:i + 4000])


async def gate(update, ctx):
    uid = update.effective_user.id
    users.add(uid)
    if not await subscribed(ctx.bot, uid):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Kanalga obuna bo'lish", url=f"https://t.me/{CHANNEL.lstrip('@')}")],
            [InlineKeyboardButton("✅ Tekshirish", callback_data="check")]])
        await update.effective_message.reply_text(
            "Botdan foydalanish uchun avval kanalimizga obuna bo'ling.", reply_markup=kb)
        return False
    if not allow(uid):
        await update.effective_message.reply_text(
            f"Bugungi limit ({DAILY_LIMIT} ta so'rov) tugadi. Ertaga qayta urinib ko'ring.")
        return False
    return True


# ---------- Telegram buyruqlari ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users.add(update.effective_user.id)
    await update.message.reply_text(
        "Salom! Men AI yordamchiman.\n\n"
        "Rejimni tanlang yoki shunchaki yozing.\n"
        "📷 Rasm yuborsangiz, uni tushuntirib beraman.\n"
        "/new - suhbatni tozalash",
        reply_markup=menu_kb())


async def new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    history.pop(update.effective_user.id, None)
    await update.message.reply_text("Suhbat tozalandi.")


async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        f"Foydalanuvchilar: {len(users)}\nBugungi so'rovlar: {sum(usage.values())}\n"
        f"Model: {MODEL}\nKunlik limit: {DAILY_LIMIT}")


async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Foydalanish: /broadcast xabar matni")
        return
    ok = 0
    for uid in list(users):
        try:
            await ctx.bot.send_message(uid, text)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"Yuborildi: {ok}/{len(users)}")


async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data or ""
    uid = q.from_user.id
    if d.startswith("mode:") and d.split(":", 1)[1] in MODES:
        m = d.split(":", 1)[1]
        modes[uid] = m
        history.pop(uid, None)
        await q.answer()
        await q.message.reply_text(f"Rejim: {MODES[m][0]}\nEndi yozing.")
    elif d == "check":
        if await subscribed(ctx.bot, uid):
            await q.answer()
            await q.message.reply_text("Rahmat! Endi foydalanishingiz mumkin.", reply_markup=menu_kb())
        else:
            await q.answer("Hali obuna bo'lmagansiz.", show_alert=True)
    else:
        await q.answer()


async def text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await gate(update, ctx):
        return
    uid = update.effective_user.id
    mode = modes.get(uid, "chat")
    msgs = history.setdefault(uid, [])
    msgs.append({"role": "user", "content": update.message.text})
    del msgs[:-20]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        answer = await ask_claude(MODES[mode][1], msgs)
    except Exception as e:
        msgs.pop()
        print("XATO:", repr(e))
        await update.message.reply_text("Xatolik yuz berdi, qayta urinib ko'r.")
        return
    msgs.append({"role": "assistant", "content": answer})
    await send_long(update.message, answer)


async def photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await gate(update, ctx):
        return
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        f = await update.message.photo[-1].get_file()
        data = bytes(await f.download_as_bytearray())
        caption = update.message.caption or "Bu rasmda nima bor? Batafsil tushuntir."
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                          "data": base64.b64encode(data).decode()}},
            {"type": "text", "text": caption}]
        mode = modes.get(update.effective_user.id, "chat")
        answer = await ask_claude(MODES[mode][1], [{"role": "user", "content": content}])
    except Exception as e:
        print("RASM XATO:", repr(e))
        await update.message.reply_text("Rasmni o'qib bo'lmadi, qayta urinib ko'r.")
        return
    await send_long(update.message, answer)


async def on_error(update, ctx):
    print("XATO (handler):", repr(ctx.error))


# ---------- Mini App (veb qism) ----------
def check_init(init_data):
    """Telegram Mini App initData imzosini tekshiradi."""
    try:
        d = dict(parse_qsl(init_data, keep_blank_values=True))
        h = d.pop("hash")
        s = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(key, s.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, h):
            return None
        if time.time() - int(d.get("auth_date", "0")) > 86400:
            return None
        return json.loads(d["user"])
    except Exception:
        return None


async def index(request):
    return web.Response(text=PAGE, content_type="text/html", charset="utf-8")


async def api_chat(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Noto'g'ri so'rov"}, status=400)
    user = check_init(body.get("initData", ""))
    if not user:
        return web.json_response({"error": "Mini App'ni Telegram ichida oching"}, status=401)
    uid = user["id"]
    users.add(uid)
    if not await subscribed(request.app["bot"], uid):
        return web.json_response({"error": "Avval kanalga obuna bo'ling"}, status=403)
    mode = body.get("mode", "chat")
    if mode not in MODES:
        mode = "chat"
    clean = []
    for m in (body.get("messages") or [])[-20:]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip():
            clean.append({"role": m["role"], "content": m["content"][:4000]})
    while clean and clean[0]["role"] != "user":
        clean.pop(0)
    if not clean or clean[-1]["role"] != "user":
        return web.json_response({"error": "Xabar topilmadi"}, status=400)
    if not allow(uid):
        return web.json_response({"error": f"Bugungi limit ({DAILY_LIMIT}) tugadi"}, status=429)
    try:
        reply = await ask_claude(MODES[mode][1], clean)
    except Exception as e:
        print("MINIAPP XATO:", repr(e))
        return web.json_response({"error": "AI xatolik berdi, qayta urinib ko'ring"}, status=500)
    return web.json_response({"reply": reply})


PAGE = r"""<!doctype html>
<html lang="uz"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>ZiyoAI</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;display:flex;flex-direction:column;font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--tg-theme-bg-color,#fff);color:var(--tg-theme-text-color,#111)}
header{display:flex;align-items:center;gap:8px;padding:10px 12px 6px}
header h1{font-size:18px;margin:0;flex:1}
#clr{border:1px solid rgba(128,128,128,.4);background:none;color:inherit;border-radius:999px;padding:6px 14px;font:inherit;font-size:14px}
#chips{display:flex;gap:8px;overflow-x:auto;padding:4px 12px 10px}
.chip{flex:none;border:1px solid var(--tg-theme-hint-color,#999);background:none;color:inherit;border-radius:999px;padding:7px 14px;font:inherit;font-size:14px}
.chip.on{background:var(--tg-theme-button-color,#2481cc);border-color:transparent;color:var(--tg-theme-button-text-color,#fff)}
#log{flex:1;overflow-y:auto;padding:8px 12px;display:flex;flex-direction:column;gap:8px}
.m{max-width:88%;padding:9px 13px;border-radius:16px;white-space:pre-wrap;overflow-wrap:anywhere}
.m.user{align-self:flex-end;background:var(--tg-theme-button-color,#2481cc);color:var(--tg-theme-button-text-color,#fff);border-bottom-right-radius:4px}
.m.bot{align-self:flex-start;background:var(--tg-theme-secondary-bg-color,#f0f0f0);border-bottom-left-radius:4px}
pre{margin:6px 0 0;padding:10px;border-radius:10px;background:rgba(0,0,0,.14);overflow-x:auto;white-space:pre}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}
#bar{display:flex;gap:8px;align-items:flex-end;padding:8px 12px calc(10px + env(safe-area-inset-bottom));border-top:1px solid rgba(128,128,128,.25)}
#inp{flex:1;resize:none;max-height:120px;border:1px solid rgba(128,128,128,.4);border-radius:18px;padding:10px 14px;font:inherit;background:var(--tg-theme-secondary-bg-color,#f6f6f6);color:inherit;outline:none}
#go{border:0;border-radius:50%;width:44px;height:44px;font-size:20px;background:var(--tg-theme-button-color,#2481cc);color:var(--tg-theme-button-text-color,#fff)}
</style></head><body>
<header><h1>ZiyoAI</h1><button id="clr">Yangi suhbat</button></header>
<div id="chips"></div>
<div id="log"></div>
<div id="bar"><textarea id="inp" rows="1" placeholder="Xabar yozing..."></textarea><button id="go">&uarr;</button></div>
<script>
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const MODES = [["chat","Suhbat"],["tarjima","Tarjimon"],["post","Post yozuvchi"],["kod","Kod"],["oqituvchi","O'qituvchi"]];
const HELLO = {
  chat: "Salom! Savolingizni yozing.",
  tarjima: "Matn yozing, tarjima qilaman.",
  post: "Mavzuni yozing, kanal uchun post tayyorlayman.",
  kod: "Qanday kod kerak? Yozing.",
  oqituvchi: "Qaysi mavzuni o'rganamiz?"
};
let mode = "chat", hist = [], busy = false;
const $ = id => document.getElementById(id);
function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function fmt(s) {
  s = esc(s);
  s = s.replace(/```[a-zA-Z0-9]*\n?([\s\S]*?)```/g, "<pre><code>$1</code></pre>");
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  return s;
}
function add(role, text) {
  const d = document.createElement("div");
  d.className = "m " + role;
  d.innerHTML = fmt(text);
  $("log").appendChild(d);
  $("log").scrollTop = $("log").scrollHeight;
  return d;
}
function reset() { hist = []; $("log").innerHTML = ""; add("bot", HELLO[mode]); }
function chips() {
  $("chips").innerHTML = "";
  MODES.forEach(([k, n]) => {
    const b = document.createElement("button");
    b.className = "chip" + (k === mode ? " on" : "");
    b.textContent = n;
    b.onclick = () => { mode = k; chips(); reset(); };
    $("chips").appendChild(b);
  });
}
async function send() {
  const t = $("inp").value.trim();
  if (!t || busy) return;
  busy = true;
  $("inp").value = ""; $("inp").style.height = "auto";
  add("user", t);
  hist.push({role: "user", content: t});
  const w = add("bot", "...");
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({initData: tg ? tg.initData : "", mode: mode, messages: hist.slice(-20)})
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "Xatolik");
    w.innerHTML = fmt(j.reply);
    hist.push({role: "assistant", content: j.reply});
  } catch (e) {
    w.textContent = "Xatolik: " + e.message;
    hist.pop();
  }
  busy = false;
  $("log").scrollTop = $("log").scrollHeight;
}
$("go").onclick = send;
$("clr").onclick = reset;
$("inp").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
$("inp").addEventListener("input", () => {
  const t = $("inp"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 120) + "px";
});
chips(); reset();
</script>
</body></html>"""


# ---------- Ishga tushirish ----------
async def setup_bot(bot):
    try:
        await bot.set_my_commands([
            BotCommand("start", "Boshlash"), BotCommand("menu", "Rejimlar menyusi"),
            BotCommand("new", "Suhbatni tozalash")])
        if WEBAPP_URL:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
                text="Mini App", web_app=WebAppInfo(url=WEBAPP_URL)))
    except Exception as e:
        print("Menyu sozlash xatosi:", repr(e))


async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", start))
    application.add_handler(CommandHandler("new", new))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CallbackQueryHandler(cb))
    application.add_handler(MessageHandler(filters.PHOTO, photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    application.add_error_handler(on_error)

    await application.initialize()
    await setup_bot(application.bot)
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    web_app = web.Application()
    web_app["bot"] = application.bot
    web_app.router.add_get("/", index)
    web_app.router.add_post("/api/chat", api_chat)
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print("Bot va Mini App ishga tushdi, port:", PORT)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
