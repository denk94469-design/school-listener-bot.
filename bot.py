import os
import sqlite3
import logging
from datetime import datetime, timezone

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)

# Configure these environment variables before starting the bot:
# BOT_TOKEN: token from @BotFather
# ADMIN_ID: your numeric Telegram user ID (not your username)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
DB_PATH = os.getenv("DB_PATH", "school_feedback.sqlite3")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("school_feedback_bot")

if not BOT_TOKEN:
    raise SystemExit("Помилка: спочатку задай змінну середовища BOT_TOKEN.")
if not ADMIN_ID_RAW.isdigit():
    raise SystemExit("Помилка: задай ADMIN_ID — свій числовий Telegram ID.")
ADMIN_ID = int(ADMIN_ID_RAW)

# User flow labels
KINDS = {
    "bug": "🐛 Баг",
    "idea": "💡 Ідея для гри",
    "feature": "🎮 Пропозиція функції",
    "other": "✉️ Інше",
}
user_menu = InlineKeyboardMarkup([
    [InlineKeyboardButton("🐛 Повідомити про баг", callback_data="send:bug")],
    [InlineKeyboardButton("💡 Запропонувати ідею", callback_data="send:idea")],
    [InlineKeyboardButton("🎮 Запропонувати нову функцію", callback_data="send:feature")],
    [InlineKeyboardButton("✉️ Написати розробнику", callback_data="send:other")],
])
admin_menu = InlineKeyboardMarkup([
    [InlineKeyboardButton("📋 Перевірити сповіщення", callback_data="admin:list:0")],
])

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                display_name TEXT,
                kind TEXT NOT NULL,
                message_text TEXT,
                media_file_id TEXT,
                media_type TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new'
            )
        """)

def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)

def main_keyboard(update: Update):
    rows = [[InlineKeyboardButton("✍️ Надіслати сповіщення", callback_data="menu:send")]]
    if is_admin(update):
        rows.append([InlineKeyboardButton("📋 Перевірити сповіщення", callback_data="admin:list:0")])
    return InlineKeyboardMarkup(rows)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("report_kind", None)
    text = (
        "🏫 Вітаємо за дверима «Жахливої школи»!\n\n"
        "Це бот зворотного зв’язку гри. Тут можна повідомити про баг, "
        "запропонувати ідею або нову функцію.\n\n"
        "Обери дію нижче. Після відправлення бот присвоїть твоєму сповіщенню номер."
    )
    await update.message.reply_text(text, reply_markup=main_keyboard(update))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Як користуватися ботом:\n"
        "1. Натисни «Надіслати сповіщення».\n"
        "2. Обери тип і напиши повідомлення.\n"
        "3. За бажанням додай фото або відео окремим повідомленням.\n"
        "4. Натисни «Надіслати сповіщення» для завершення.\n\n"
        "Розробник може відкрити список сповіщень кнопкою «Перевірити сповіщення»."
    )

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "menu:send":
        context.user_data.pop("report_kind", None)
        await q.message.reply_text("Що хочеш надіслати?", reply_markup=user_menu)
        return

    if data.startswith("send:"):
        kind = data.split(":", 1)[1]
        if kind not in KINDS:
            return
        context.user_data["report_kind"] = kind
        context.user_data["draft_text"] = ""
        context.user_data["draft_media"] = None
        context.user_data["draft_media_type"] = None
        await q.message.reply_text(
            f"{KINDS[kind]}\n\n"
            "Напиши опис повідомлення. Можеш також надіслати фото або відео.\n"
            "Коли все готово, натисни «Надіслати сповіщення».",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📨 Надіслати сповіщення", callback_data="report:submit")],
                [InlineKeyboardButton("❌ Скасувати", callback_data="report:cancel")],
            ]),
        )
        return

    if data == "report:cancel":
        context.user_data.pop("report_kind", None)
        context.user_data.pop("draft_text", None)
        context.user_data.pop("draft_media", None)
        context.user_data.pop("draft_media_type", None)
        await q.message.reply_text("Створення сповіщення скасовано.", reply_markup=main_keyboard(update))
        return

    if data == "report:submit":
        kind = context.user_data.get("report_kind")
        text = (context.user_data.get("draft_text") or "").strip()
        media_id = context.user_data.get("draft_media")
        media_type = context.user_data.get("draft_media_type")
        if not kind:
            await q.message.reply_text("Спочатку обери тип сповіщення через меню.")
            return
        if not text and not media_id:
            await q.message.reply_text("Додай текст, фото або відео перед відправленням.")
            return
        user = q.from_user
        created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        with db() as conn:
            cur = conn.execute(
                """INSERT INTO reports
                (user_id, username, display_name, kind, message_text, media_file_id, media_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user.id, user.username, user.full_name, kind, text, media_id, media_type, created),
            )
            report_id = cur.lastrowid
        # Notify the developer in their private chat if they have opened the bot.
        admin_text = (
            f"🔔 Нове сповіщення №{report_id}\n"
            f"Тип: {KINDS[kind]}\n"
            f"Від: {user.full_name} (@{user.username or 'без username'})\n"
            f"ID користувача: {user.id}\n\n{text or '[Без тексту]'}"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)
            if media_id:
                if media_type == "photo":
                    await context.bot.send_photo(chat_id=ADMIN_ID, photo=media_id, caption=f"Вкладення до сповіщення №{report_id}")
                elif media_type == "video":
                    await context.bot.send_video(chat_id=ADMIN_ID, video=media_id, caption=f"Вкладення до сповіщення №{report_id}")
                elif media_type == "document":
                    await context.bot.send_document(chat_id=ADMIN_ID, document=media_id, caption=f"Вкладення до сповіщення №{report_id}")
        except Exception:
            log.exception("Не вдалося надіслати повідомлення адміністратору")

        context.user_data.pop("report_kind", None)
        context.user_data.pop("draft_text", None)
        context.user_data.pop("draft_media", None)
        context.user_data.pop("draft_media_type", None)
        await q.message.reply_text(
            f"✅ Дякуємо! Твоє сповіщення успішно надіслано.\n\n"
            f"🔢 Номер сповіщення: №{report_id}\n"
            "Розробник зможе прочитати його та розглянути для майбутніх оновлень гри.",
            reply_markup=main_keyboard(update),
        )
        return

    if data.startswith("admin:list:"):
        if not is_admin(update):
            await q.message.reply_text("⛔ Ця команда доступна лише розробнику.")
            return
        page = max(0, int(data.split(":")[2]))
        page_size = 8
        offset = page * page_size
        with db() as conn:
            rows = conn.execute(
                "SELECT id, kind, message_text, status FROM reports ORDER BY id DESC LIMIT ? OFFSET ?",
                (page_size, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        if not rows:
            await q.message.reply_text("Поки що сповіщень немає.", reply_markup=admin_menu)
            return
        buttons = []
        for row in rows:
            excerpt = (row["message_text"] or "[без тексту]").replace("\\n", " ")
            if len(excerpt) > 34:
                excerpt = excerpt[:31] + "..."
            status_mark = "✅" if row["status"] == "processed" else "🆕"
            buttons.append([InlineKeyboardButton(
                f"{status_mark} №{row['id']} · {KINDS.get(row['kind'], row['kind'])} · {excerpt}",
                callback_data=f"admin:view:{row['id']}",
            )])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Новіші", callback_data=f"admin:list:{page-1}"))
        if offset + page_size < total:
            nav.append(InlineKeyboardButton("Старіші ➡️", callback_data=f"admin:list:{page+1}"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton("🔄 Оновити список", callback_data=f"admin:list:{page}")])
        await q.message.reply_text(
            f"📋 Сповіщення ({total} загалом)\nНатисни номер, щоб прочитати повністю:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("admin:view:"):
        if not is_admin(update):
            await q.message.reply_text("⛔ Ця команда доступна лише розробнику.")
            return
        report_id = int(data.split(":")[2])
        with db() as conn:
            row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            await q.message.reply_text("Сповіщення не знайдено.")
            return
        status = "Оброблено ✅" if row["status"] == "processed" else "Нове 🆕"
        text = (
            f"📨 Сповіщення №{row['id']}\n"
            f"Тип: {KINDS.get(row['kind'], row['kind'])}\n"
            f"Статус: {status}\n"
            f"Дата: {row['created_at']}\n"
            f"Автор: {row['display_name']} (@{row['username'] or 'без username'})\n"
            f"ID автора: {row['user_id']}\n\n"
            f"Опис:\n{row['message_text'] or '[Без тексту]'}"
        )
        buttons = []
        if row["status"] != "processed":
            buttons.append([InlineKeyboardButton("✅ Обробити", callback_data=f"admin:process:{report_id}")])
        if row["media_file_id"]:
            buttons.append([InlineKeyboardButton("📎 Показати вкладення", callback_data=f"admin:media:{report_id}")])
        buttons.append([InlineKeyboardButton("🗑 Видалити", callback_data=f"admin:delete:{report_id}")])
        buttons.append([InlineKeyboardButton("⬅️ До списку сповіщень", callback_data="admin:list:0")])
        await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("admin:media:"):
        if not is_admin(update):
            return
        report_id = int(data.split(":")[2])
        with db() as conn:
            row = conn.execute("SELECT media_file_id, media_type FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not row or not row["media_file_id"]:
            await q.message.reply_text("Вкладення не знайдено.")
            return
        caption = f"Вкладення до сповіщення №{report_id}"
        if row["media_type"] == "photo":
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=row["media_file_id"], caption=caption)
        elif row["media_type"] == "video":
            await context.bot.send_video(chat_id=ADMIN_ID, video=row["media_file_id"], caption=caption)
        else:
            await context.bot.send_document(chat_id=ADMIN_ID, document=row["media_file_id"], caption=caption)
        return

    if data.startswith("admin:process:"):
        if not is_admin(update):
            return
        report_id = int(data.split(":")[2])
        with db() as conn:
            conn.execute("UPDATE reports SET status='processed' WHERE id=?", (report_id,))
        await q.message.reply_text(
            f"✅ Сповіщення №{report_id} позначено як «Оброблено».\n"
            "Це позначка для тебе — вона не додає зміни до гри автоматично.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Перевірити сповіщення", callback_data="admin:list:0")]
            ]),
        )
        return

    if data.startswith("admin:delete:"):
        if not is_admin(update):
            return
        report_id = int(data.split(":")[2])
        with db() as conn:
            conn.execute("DELETE FROM reports WHERE id=?", (report_id,))
        await q.message.reply_text(
            f"🗑 Сповіщення №{report_id} видалено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Перевірити сповіщення", callback_data="admin:list:0")]
            ]),
        )

async def collect_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("report_kind"):
        # Don't hijack normal chat messages; show the menu.
        await update.message.reply_text("Щоб надіслати повідомлення, натисни кнопку нижче.", reply_markup=main_keyboard(update))
        return
    msg = update.message
    if msg.text and not msg.text.startswith("/"):
        context.user_data["draft_text"] = msg.text.strip()
        await msg.reply_text(
            "📝 Текст збережено. Можеш додати фото/відео або завершити відправлення.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📨 Надіслати сповіщення", callback_data="report:submit")],
                [InlineKeyboardButton("❌ Скасувати", callback_data="report:cancel")],
            ]),
        )
    elif msg.photo:
        context.user_data["draft_media"] = msg.photo[-1].file_id
        context.user_data["draft_media_type"] = "photo"
        await msg.reply_text("📷 Фото додано. Натисни «Надіслати сповіщення», коли будеш готовий/готова.")
    elif msg.video:
        context.user_data["draft_media"] = msg.video.file_id
        context.user_data["draft_media_type"] = "video"
        await msg.reply_text("🎥 Відео додано. Натисни «Надіслати сповіщення», коли будеш готовий/готова.")
    elif msg.document:
        context.user_data["draft_media"] = msg.document.file_id
        context.user_data["draft_media_type"] = "document"
        await msg.reply_text("📎 Файл додано. Натисни «Надіслати сповіщення», коли будеш готовий/готова.")
    else:
        await msg.reply_text("Надішли текст, фото, відео або файл.")

async def post_init(app: Application):
    init_db()
    try:
        await app.bot.send_message(
            chat_id=ADMIN_ID,
            text="🤖 Бот «Шкільний Слухач» запущено. Коли надійдуть повідомлення, вони з’являться тут. Команда /start показує меню.",
        )
    except Exception:
        log.info("Не вдалося повідомити адміністратора при запуску; відкрий бота і натисни /start.")

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(
        filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL,
        collect_content,
    ))
    log.info("Бот запускається...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
