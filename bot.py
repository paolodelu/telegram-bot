"""Telegram bot demo: book finder and reminders.

Commands
    /start, /help           show the help message
    /top [n]                top rated books (default 5, max 10)
    /search <word>          search book titles
    /remind <min> <text>    get a reminder message after <min> minutes

Setup
    1. Create a bot with @BotFather on Telegram and copy the token.
    2. Put it in a .env file:  TELEGRAM_BOT_TOKEN=your-token-here
    3. pip install -r requirements.txt
    4. python bot.py
"""

import asyncio
import logging
import os
import re
import time

import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

BASE_URL = "https://books.toscrape.com/catalogue/page-{}.html"
RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
HEADERS = {"User-Agent": "Mozilla/5.0 (portfolio telegram bot demo)"}
PAGES_TO_LOAD = 3          # 20 books per page
CACHE_SECONDS = 600        # reuse downloaded data for 10 minutes
MAX_REMINDER_MINUTES = 24 * 60

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("bookbot")

HELP_TEXT = (
    "Hi! I'm a demo bot built with Python.\n\n"
    "/top [n] - top rated books (default 5, max 10)\n"
    "/search <word> - search book titles\n"
    "/remind <minutes> <text> - remind me later\n"
    "/help - show this message"
)


# ---------- data layer (same idea as the scraper project) ----------

def parse_books(html):
    """Extract book records from one catalogue page of HTML."""
    soup = BeautifulSoup(html, "html.parser")
    books = []
    for article in soup.select("article.product_pod"):
        link = article.select_one("h3 a")
        price_text = article.select_one("p.price_color").get_text(strip=True)
        rating_class = article.select_one("p.star-rating")["class"]
        rating_word = next((c for c in rating_class if c in RATINGS), None)
        books.append(
            {
                "title": link["title"],
                "price": float(re.sub(r"[^\d.]", "", price_text)),
                "rating": RATINGS.get(rating_word, 0),
            }
        )
    return books


def fetch_books(pages=PAGES_TO_LOAD):
    """Download the first pages of the catalogue (blocking, run in a thread)."""
    books = []
    with requests.Session() as session:
        for page in range(1, pages + 1):
            response = session.get(BASE_URL.format(page), headers=HEADERS, timeout=10)
            if response.status_code == 404:
                break
            response.raise_for_status()
            response.encoding = "utf-8"
            books.extend(parse_books(response.text))
    return books


async def get_books(context):
    """Return cached books, downloading them again when the cache is old."""
    cache = context.application.bot_data
    if cache.get("books") and time.time() - cache.get("ts", 0) < CACHE_SECONDS:
        return cache["books"]
    books = await asyncio.to_thread(fetch_books)
    cache["books"] = books
    cache["ts"] = time.time()
    return books


def format_book(book):
    stars = "*" * book["rating"]
    return f"{book['title']} - GBP {book['price']:.2f} - {stars} ({book['rating']}/5)"


def top_books(books, n):
    """Best rated first, cheaper first on ties."""
    return sorted(books, key=lambda b: (-b["rating"], b["price"]))[:n]


def search_books(books, word, limit=10):
    word = word.lower()
    return [b for b in books if word in b["title"].lower()][:limit]


# ---------- command handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = 5
    if context.args:
        if not context.args[0].isdigit() or not 1 <= int(context.args[0]) <= 10:
            await update.message.reply_text("Usage: /top [n]  (n between 1 and 10)")
            return
        n = int(context.args[0])
    try:
        books = await get_books(context)
    except requests.RequestException:
        await update.message.reply_text("Sorry, I can't reach the book site right now.")
        return
    lines = [f"{i}. {format_book(b)}" for i, b in enumerate(top_books(books, n), 1)]
    await update.message.reply_text("Top rated books:\n\n" + "\n".join(lines))


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /search <word>   e.g. /search love")
        return
    word = " ".join(context.args)
    try:
        books = await get_books(context)
    except requests.RequestException:
        await update.message.reply_text("Sorry, I can't reach the book site right now.")
        return
    found = search_books(books, word)
    if not found:
        await update.message.reply_text(f"No books found for '{word}'.")
        return
    lines = [f"- {format_book(b)}" for b in found]
    await update.message.reply_text(f"Results for '{word}':\n\n" + "\n".join(lines))


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(context.job.chat_id, f"Reminder: {context.job.data}")


async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usage = "Usage: /remind <minutes> <text>   e.g. /remind 10 drink water"
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(usage)
        return
    minutes = int(context.args[0])
    if not 1 <= minutes <= MAX_REMINDER_MINUTES:
        await update.message.reply_text(f"Choose between 1 and {MAX_REMINDER_MINUTES} minutes.")
        return
    text = " ".join(context.args[1:])
    context.job_queue.run_once(
        send_reminder, minutes * 60, chat_id=update.effective_chat.id, data=text
    )
    await update.message.reply_text(f"Ok! I'll remind you in {minutes} min: {text}")


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("I don't know that command. Try /help")


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled error", exc_info=context.error)


def build_application(token):
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("remind", remind))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    app.add_error_handler(on_error)
    return app


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN. Put it in a .env file or set it as an environment variable.")
    logger.info("Bot started. Press Ctrl+C to stop.")
    build_application(token).run_polling()


if __name__ == "__main__":
    main()
