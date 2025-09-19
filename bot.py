# bot.py
import os
import asyncio
import datetime
import requests
from functools import wraps

from dotenv import load_dotenv
load_dotenv()

from telegram import Update, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters
)

# config
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DB_URL = os.getenv("DATABASE_URL", "sqlite:///infobot.db")
ADMIN_IDS = [int(x) for x in (os.getenv("ADMIN_IDS","").split(",") if os.getenv("ADMIN_IDS") else [])]
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "")

# DB
from models import get_sessionmaker, User, Transaction, Base
SessionLocal, engine = get_sessionmaker(DB_URL)

# ensure tables exist
Base.metadata.create_all(engine)

# helper: ensure user exists
def ensure_user(session, tg_user):
    user = session.query(User).filter_by(telegram_id=tg_user.id).first()
    if not user:
        user = User(telegram_id=tg_user.id, username=tg_user.username)
        session.add(user)
        session.commit()
    return user

def is_admin(tg_id):
    return tg_id in ADMIN_IDS

# -------------------------
# daily_quota decorator (DB-backed). limit applies to non-premium users.
# -------------------------
def daily_quota(limit):
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
            session = SessionLocal()
            tg = update.effective_user
            user = ensure_user(session, tg)
            today = datetime.date.today()

            # Reset quota if new day
            if user.last_request_date != today:
                user.last_request_date = today
                user.requests_today = 0

            # ✅ Admins bypass quota
            if is_admin(tg.id):
                session.commit()
                return await func(update, context, *a, **kw)

            # ✅ Premium users bypass quota
            if user.is_premium:
                session.commit()
                return await func(update, context, *a, **kw)

            # Normal users → enforce quota
            if user.requests_today >= limit:
                await update.message.reply_text(
                    f"Quota reached ({limit} requests/day). Buy premium with /buy or ask an admin to grant access."
                )
                session.commit()
                return

            user.requests_today += 1
            session.commit()
            return await func(update, context, *a, **kw)
        return wrapper
    return decorator

# -------------------------
# Handlers
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    ensure_user(session, update.effective_user)
    await update.message.reply_text(
        "🤖 Welcome to InfoBot!\n\n"
        "Commands:\n"
        "/weather <city>\n"
        "/crypto <symbol>\n"
        "/ask <question>\n"
        "/buy (manual payment instructions)\n"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Use /weather, /crypto, /ask. Admins: /grant_premium <tg_id> <days>")

# weather: asynchronous-safe by offloading requests to thread
async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /weather <city>")
        return
    city = " ".join(context.args)
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": WEATHER_API_KEY, "units": "metric"}

    def fetch():
        return requests.get(url, params=params, timeout=15)

    try:
        resp = await asyncio.to_thread(fetch)
        data = resp.json()
    except Exception as e:
        await update.message.reply_text("Error fetching weather. Try again later.")
        return

    if resp.status_code != 200:
        await update.message.reply_text("City not found 🌍")
        return
    desc = data["weather"][0]["description"]
    temp = data["main"]["temp"]
    await update.message.reply_text(f"🌦 Weather in {city.title()}: {desc}, {temp}°C")

# crypto: map common symbols, fallback to CoinGecko coin list (blocking but ok for dev)
COMMON = {"btc":"bitcoin","eth":"ethereum","bnb":"binancecoin","ada":"cardano","doge":"dogecoin"}
COINGECKO_PRICE = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_LIST = "https://api.coingecko.com/api/v3/coins/list"
_coin_list_cache = None

def get_coin_id(symbol: str):
    s = symbol.lower()
    if s in COMMON:
        return COMMON[s]
    global _coin_list_cache
    if _coin_list_cache is None:
        try:
            _coin_list_cache = requests.get(COINGECKO_LIST, timeout=20).json()
        except:
            _coin_list_cache = []
    for c in _coin_list_cache:
        if c.get("symbol","").lower() == s:
            return c["id"]
    return None

async def crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /crypto <symbol>, e.g. /crypto btc")
        return
    symbol = context.args[0]
    coin_id = await asyncio.to_thread(get_coin_id, symbol)
    if not coin_id:
        await update.message.reply_text("Coin not found.")
        return

    def fetch_price():
        return requests.get(COINGECKO_PRICE, params={"ids":coin_id,"vs_currencies":"usd"}, timeout=15)

    try:
        r = await asyncio.to_thread(fetch_price)
        data = r.json()
    except:
        await update.message.reply_text("Error fetching price.")
        return

    price = data.get(coin_id, {}).get("usd")
    if price is None:
        await update.message.reply_text("Price not available.")
        return
    await update.message.reply_text(f"📈 {symbol.upper()} ≈ ${price:,}")

# ask (OpenAI) — wrapped with daily_quota(2) during dev

import os
import requests
from telegram import Update
from telegram.ext import ContextTypes

HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")
HF_MODEL = "tiiuae/falcon-7b-instruct"  # you can change to another chat model

async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /ask <your question>")
        return

    question = " ".join(context.args)

    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
    payload = {"inputs": question}

    try:
        response = requests.post(
            f"https://api-inference.huggingface.co/models/{HF_MODEL}",
            headers=headers,
            json=payload,
            timeout=60,
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0 and "generated_text" in data[0]:
                answer = data[0]["generated_text"]
            else:
                answer = str(data)
        else:
            answer = f"HF API error {response.status_code}: {response.text}"

    except Exception as e:
        answer = f"HuggingFace API call failed: {e}"

    await update.message.reply_text(answer)
# -------------------------
# Manual payment flow (simple)
# -------------------------
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # instruct the user how to pay manually (M-Pesa/bank etc.)
    await update.message.reply_text(
        "To buy Premium (manual):\n\n"
        "1) Send payment to: MPESA PAYBILL 0729696729, account: yourname\n"
        "2) After paying, send proof (screenshot) to this chat and run:\n"
        "/confirm_payment <amount>\n\n"
        "An admin will review and grant you premium access."
    )

async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # user indicates they have paid & attached screenshot; in production you'd parse the message
    await update.message.reply_text("Thanks — payment confirmation received. Admins will verify and run /grant_premium.")

# Admin: grant premium
async def grant_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /grant_premium <telegram_id> <days>")
        return
    target_id = int(context.args[0])
    days = int(context.args[1])
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=target_id).first()
    if not user:
        await update.message.reply_text("User not found.")
        return
    user.is_premium = True
    user.premium_expires = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    session.commit()
    await update.message.reply_text(f"Granted premium to {target_id} for {days} days.")

# -------------------------
# Setup and run
# -------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("weather", weather))
    app.add_handler(CommandHandler("crypto", crypto))
    app.add_handler(CommandHandler("ask", ask))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("confirm_payment", confirm_payment))
    app.add_handler(CommandHandler("grant_premium", grant_premium))

    print("🤖 InfoBot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
