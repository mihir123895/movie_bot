"""
Telegram Movie Delivery Bot - Webhook Version (upgraded for python-telegram-bot v20+/v21+)
"""

from dotenv import load_dotenv
load_dotenv()
import os
import asyncio
import sqlite3
import secrets
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------- CONFIG ----------------------
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [1963601117]
DB_PATH = "movies.db"
EXPIRY_SECONDS = 15 * 60

# Webhook settings
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://movie-bot-f64d.onrender.com")
PORT = int(os.getenv("PORT", "10000"))

print(f"TOKEN present: {bool(TOKEN)}")
print(f"WEBHOOK_URL: {WEBHOOK_URL}")
print(f"PORT: {PORT}")

# ---------------------- DB Helpers ----------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE,
            send_method TEXT,
            file_id TEXT,
            from_chat_id INTEGER,
            from_message_id INTEGER,
            filename TEXT,
            added_by INTEGER,
            added_at TEXT,
            uses_allowed INTEGER DEFAULT -1,
            used_count INTEGER DEFAULT 0,
            expires_at TEXT DEFAULT NULL
        )
    """
    )
    conn.commit()
    conn.close()


def add_movie_record(
    token: str,
    send_method: str,
    filename: str,
    added_by: int,
    file_id: Optional[str] = None,
    from_chat_id: Optional[int] = None,
    from_message_id: Optional[int] = None,
    uses_allowed: int = -1,
    expires_at: Optional[str] = None,
):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
    INSERT INTO movies
    (token, send_method, file_id, from_chat_id, from_message_id, filename, added_by, added_at, uses_allowed, used_count, expires_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
    """,
        (
            token,
            send_method,
            file_id,
            from_chat_id,
            from_message_id,
            filename,
            added_by,
            datetime.utcnow().isoformat(),
            uses_allowed,
            expires_at,
        ),
    )

    conn.commit()
    conn.close()


def get_movie_by_token(token: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, token, send_method, file_id, from_chat_id, from_message_id, filename, uses_allowed, used_count, expires_at
        FROM movies WHERE token = ?
    """,
        (token,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def increment_used_count(movie_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE movies SET used_count = used_count + 1 WHERE id = ?", (movie_id,))
    conn.commit()
    conn.close()


def remove_movie_by_token(token: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM movies WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def list_movies_all():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, token, send_method, filename, added_at, uses_allowed, used_count, expires_at, added_by FROM movies"
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------------------- Utils ----------------------
def parse_args_for_register(args: list) -> Tuple[int, Optional[str]]:
    uses_allowed = -1
    expires_at_iso = None
    for a in args:
        a = a.strip().lower()
        if re.fullmatch(r"\d+", a):
            try:
                uses_allowed = int(a)
            except:
                pass
            continue
        m = re.fullmatch(r"(\d+)([smhd])", a)
        if m:
            num = int(m.group(1))
            unit = m.group(2)
            delta = None
            if unit == "s":
                delta = timedelta(seconds=num)
            elif unit == "m":
                delta = timedelta(minutes=num)
            elif unit == "h":
                delta = timedelta(hours=num)
            elif unit == "d":
                delta = timedelta(days=num)
            if delta:
                expires_at_iso = (datetime.utcnow() + delta).isoformat()
            continue
    return uses_allowed, expires_at_iso


async def schedule_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, *message_ids: int):
    await asyncio.sleep(EXPIRY_SECONDS)
    for mid in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass


# ---------------------- Handlers ----------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Use:\n"
        "Forward/upload a file or channel post to the bot (ADMIN) -> auto-generate link.\n\n"
        "Or use:\n"
        "/register [uses] [time]  (reply to the file)\n"
        "/list  - list tokens (admin)\n"
        "/remove <token> - remove (admin)\n"
        "/help  - this message\n\n"
        "Example: /register 1 24h  (one-time, expires 24h)\n"
        f"Bot deletes delivered files after {EXPIRY_SECONDS//60} mins."
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    args = context.args
    if not args:
        await msg.reply_text("Welcome. Click a deep-link or use /start <token>.")
        return
    token = args[0]
    row = get_movie_by_token(token)
    if not row:
        await msg.reply_text("Invalid or expired link.")
        return

    movie_id, _tok, send_method, file_id, from_chat_id, from_message_id, filename, uses_allowed, used_count, expires_at_iso = row

    if expires_at_iso:
        try:
            expires_dt = datetime.fromisoformat(expires_at_iso)
            if datetime.utcnow() > expires_dt:
                await msg.reply_text("Sorry — this link expired.")
                return
        except Exception:
            pass

    if uses_allowed != -1 and used_count >= uses_allowed:
        await msg.reply_text("Sorry — link used maximum times.")
        return

    chat_id = msg.chat_id
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    except Exception:
        pass

    sent_message = None
    try:
        if send_method == "copy" and from_chat_id and from_message_id:
            sent_message = await context.bot.copy_message(chat_id=chat_id, from_chat_id=from_chat_id, message_id=from_message_id)
        else:
            # If send_method == "file", file_id should be the telegram file_id
            sent_message = await msg.reply_document(document=file_id, filename=filename)
    except Exception:
        await msg.reply_text("Failed to deliver file. Contact admin.")
        return

    warn = await msg.reply_text("⚠️ Save now — will delete in 15 mins for copyright reasons.")

    increment_used_count(movie_id)
    to_delete_ids = [m.message_id for m in [sent_message, warn] if m]
    # schedule deletion in background
    asyncio.create_task(schedule_deletion(context, chat_id, *to_delete_ids))


async def register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("Unauthorized.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to file and run /register.")
        return

    target = update.message.reply_to_message
    uses_allowed, expires_at_iso = parse_args_for_register(context.args)

    send_method = file_id = from_chat_id = from_message_id = filename = None

    forward_chat_id = getattr(target.forward_from_chat, "id", None)
    forward_msg_id = getattr(target, "forward_from_message_id", None)

    if forward_chat_id and forward_msg_id:
        send_method = "copy"
        from_chat_id = forward_chat_id
        from_message_id = forward_msg_id
        filename = getattr(target.document, "file_name", None) or target.caption or "file"
    elif target.document:
        send_method = "file"
        file_id = target.document.file_id
        filename = target.document.file_name or "file"
    elif target.video:
        send_method = "file"
        file_id = target.video.file_id
        filename = target.caption or "video.mp4"
    elif target.animation:
        send_method = "file"
        file_id = target.animation.file_id
        filename = target.caption or "animation.mp4"
    elif target.audio:
        send_method = "file"
        file_id = target.audio.file_id
        filename = target.caption or (target.audio.file_name or "audio")
    else:
        media = target.document or target.video or target.animation or target.audio
        if media:
            send_method = "file"
            file_id = getattr(media, "file_id", None)
            filename = getattr(media, "file_name", None) or target.caption or "file"
        else:
            await update.message.reply_text("Reply must contain a file/document.")
            return

    token = secrets.token_urlsafe(12)
    add_movie_record(
        token,
        send_method,
        filename,
        user.id,
        file_id=file_id,
        from_chat_id=from_chat_id,
        from_message_id=from_message_id,
        uses_allowed=uses_allowed,
        expires_at=expires_at_iso,
    )

    me = await context.bot.get_me()
    deep_link = f"https://t.me/{me.username}?start={token}"
    await update.message.reply_text(
        f"Registered!\nToken: {token}\nLink:\n{deep_link}\nFilename: {filename}\nUses: {'unlimited' if uses_allowed==-1 else uses_allowed}\nExpires: {expires_at_iso or 'none'}"
    )


async def auto_register_on_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        return  # ignore non-admins

    target = update.message
    if not target:
        return

    # ignore commands
    if target.text and target.text.startswith("/"):
        return

    send_method = file_id = from_chat_id = from_message_id = filename = None

    forward_chat = getattr(target, "forward_from_chat", None)
    forward_chat_id = getattr(forward_chat, "id", None) if forward_chat else None
    forward_msg_id = getattr(target, "forward_from_message_id", None)

    if forward_chat_id and forward_msg_id:
        send_method = "copy"
        from_chat_id = forward_chat_id
        from_message_id = forward_msg_id
        if getattr(target, "document", None) and getattr(target.document, "file_name", None):
            filename = target.document.file_name
        else:
            filename = target.caption or "file"
    elif getattr(target, "document", None):
        send_method = "file"
        file_id = target.document.file_id
        filename = target.document.file_name or "file"
    elif getattr(target, "video", None):
        send_method = "file"
        file_id = target.video.file_id
        filename = target.caption or "video.mp4"
    elif getattr(target, "animation", None):
        send_method = "file"
        file_id = target.animation.file_id
        filename = target.caption or "animation.mp4"
    elif getattr(target, "audio", None):
        send_method = "file"
        file_id = target.audio.file_id
        filename = target.caption or (target.audio.file_name or "audio")
    else:
        media = getattr(target, "document", None) or getattr(target, "video", None) or getattr(target, "animation", None) or getattr(target, "audio", None)
        if media:
            send_method = "file"
            file_id = getattr(media, "file_id", None)
            filename = getattr(media, "file_name", None) or target.caption or "file"
        else:
            return

    token = secrets.token_urlsafe(12)
    add_movie_record(
        token=token,
        send_method=send_method,
        filename=filename,
        added_by=user.id,
        file_id=file_id,
        from_chat_id=from_chat_id,
        from_message_id=from_message_id,
        uses_allowed=-1,
        expires_at=None,
    )

    me = await context.bot.get_me()
    deep_link = f"https://t.me/{me.username}?start={token}"
    await update.message.reply_text(
        f"Auto-registered!\nToken: {token}\nLink:\n{deep_link}\nFilename: {filename}\nUses: unlimited\nExpires: none"
    )


# ---------------------- ADMIN COMMANDS ----------------------
async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("Unauthorized.")
        return

    rows = list_movies_all()
    if not rows:
        await update.message.reply_text("No registered movies.")
        return

    texts = []
    for r in rows:
        _id, token, send_method, filename, added_at, uses_allowed, used_count, expires_at, added_by = r
        texts.append(
            f"id:{_id} token:{token}\nmethod:{send_method} name:{filename}\nadded:{added_at} by:{added_by}\nuses:{'unlimited' if uses_allowed==-1 else uses_allowed} used:{used_count} expires:{expires_at}\n"
        )
    big_text = "\n\n".join(texts)
    for chunk in [big_text[i : i + 3900] for i in range(0, len(big_text), 3900)]:
        await update.message.reply_text(chunk)


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove <token>")
        return
    token = context.args[0].strip()
    remove_movie_by_token(token)
    await update.message.reply_text("Removed (if existed).")


# ---------------------- Flask App ----------------------
from flask import Flask, request

flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "Telegram Movie Bot is Running! 🚀"


# We'll create a global 'app' variable for the telegram Application
app: Optional[Application] = None


@flask_app.route("/webhook", methods=["POST"])
def webhook():
    """Webhook route for Telegram updates"""
    if request.is_json:
        try:
            data = request.get_json()
            update = Update.de_json(data, app.bot)
            # schedule processing on the bot's event loop
            try:
                asyncio.create_task(app.process_update(update))
            except RuntimeError:
                # If loop isn't running in current thread, get the running loop and call create_task there.
                loop = asyncio.get_event_loop()
                loop.create_task(app.process_update(update))
        except Exception as e:
            print(f"Error processing update: {e}")
    return "OK"


# ---------------------- MAIN ----------------------
def main():
    """Main function"""
    init_db()

    if not TOKEN:
        print("ERROR: TOKEN environment variable is not set!")
        return

    print("Initializing bot...")

    global app
    app = Application.builder().token(TOKEN).build()

    # Add handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("register", register_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(MessageHandler(filters.ALL, auto_register_on_admin_message))

    # Initialize application (handlers ready) and set webhook
    async def initialize_app():
        # initialize only (no long-running start). initialize makes handlers ready.
        await app.initialize()
        if WEBHOOK_URL:
            webhook_url = f"{WEBHOOK_URL}/webhook"
            await app.bot.set_webhook(webhook_url)
            print(f"Webhook set to: {webhook_url}")
        else:
            print("No WEBHOOK_URL set; consider using polling in development")

    # Run initialization synchronously
    asyncio.run(initialize_app())

    # Start Flask (this will receive webhook POSTs and call app.process_update)
    print(f"Starting Flask server on port {PORT}...")
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
