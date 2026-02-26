import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from keep_alive import keep_alive

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("sharkv1")

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = "orders.db"

CATALOG = {
    "Netflix": {
        "1 day": 5,
        "7 days": 20,
        "30 days": 60,
    },
    "YouTube Premium": {
        "1 day": 4,
        "7 days": 15,
        "30 days": 45,
    },
    "Spotify": {
        "1 day": 3,
        "7 days": 12,
        "30 days": 35,
    },
}

ACCOUNT_TYPES = ["Basic", "Premium", "VIP"]


# ------------------------- Database Layer -------------------------
def db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create required tables if they do not already exist."""
    with closing(db_connection()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                product TEXT,
                duration TEXT,
                account_type TEXT,
                details_text TEXT,
                details_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                message_text TEXT,
                created_at TEXT,
                status TEXT DEFAULT 'open'
            )
            """
        )


def save_order(
    user_id: int,
    username: Optional[str],
    product: str,
    duration: str,
    account_type: str,
    details_text: Optional[str],
    details_file_id: Optional[str],
) -> int:
    with closing(db_connection()) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO orders (
                user_id, username, product, duration, account_type,
                details_text, details_file_id, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                user_id,
                username,
                product,
                duration,
                account_type,
                details_text,
                details_file_id,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        return int(cursor.lastrowid)


def get_user_orders(user_id: int) -> list[sqlite3.Row]:
    with closing(db_connection()) as conn:
        return conn.execute(
            """
            SELECT id, product, duration, status
            FROM orders
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (user_id,),
        ).fetchall()


def get_profile_counts(user_id: int) -> tuple[int, int]:
    with closing(db_connection()) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        approved = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE user_id = ? AND status = 'approved'",
            (user_id,),
        ).fetchone()[0]
    return int(total), int(approved)


def save_ticket(user_id: int, username: Optional[str], message_text: str) -> int:
    with closing(db_connection()) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO support_tickets (user_id, username, message_text, created_at, status)
            VALUES (?, ?, ?, ?, 'open')
            """,
            (
                user_id,
                username,
                message_text,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        return int(cursor.lastrowid)


# ------------------------- UI Helpers -------------------------
def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🛒 Order", callback_data="menu:order"),
                InlineKeyboardButton("📦 My Orders", callback_data="menu:orders"),
            ],
            [
                InlineKeyboardButton("👤 Profile", callback_data="menu:profile"),
                InlineKeyboardButton("🛟 Support", callback_data="menu:support"),
            ],
            [
                InlineKeyboardButton("🔥 Offers", callback_data="menu:offers"),
                InlineKeyboardButton("🧾 How it works", callback_data="menu:how"),
            ],
        ]
    )


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="menu:home")]])


def products_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(name, callback_data=f"product:{name}")] for name in CATALOG]
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def durations_keyboard(product: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(d, callback_data=f"duration:{d}")] for d in CATALOG[product]]
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data="back:products"),
            InlineKeyboardButton("🏠 Home", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def account_types_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(a, callback_data=f"account:{a}")] for a in ACCOUNT_TYPES]
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data="back:durations"),
            InlineKeyboardButton("🏠 Home", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Confirm", callback_data="order:confirm")],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="back:account_types"),
                InlineKeyboardButton("❌ Cancel", callback_data="menu:home"),
            ],
        ]
    )


def order_summary_text(draft: dict) -> str:
    details = draft.get("details_text") or "[photo uploaded]"
    return (
        "*Confirm your order*\n"
        f"• Product: `{draft.get('product', '-')}`\n"
        f"• Duration: `{draft.get('duration', '-')}`\n"
        f"• Account: `{draft.get('account_type', '-')}`\n"
        f"• Details: `{details}`"
    )


def clear_modes(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("mode", None)


async def show_main_menu(target_send) -> None:
    text = (
        "🦈 *SharkV1 Shop*\n"
        "✅ Premium Services\n"
        "🔒 Secure Payments\n"
        "⚡ Instant Delivery\n"
        "🛟 24/7 Support\n"
        "👇 Choose a category:"
    )
    await target_send(text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())


# ------------------------- Handlers -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    if update.message:
        await show_main_menu(update.message.reply_text)


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    clear_modes(context)
    data = query.data or ""

    if data == "menu:home":
        context.user_data.pop("draft", None)
        await show_main_menu(query.edit_message_text)

    elif data == "menu:order":
        context.user_data["draft"] = {}
        await query.edit_message_text(
            "*Select a product:*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=products_keyboard(),
        )

    elif data == "menu:orders":
        orders = get_user_orders(query.from_user.id)
        if not orders:
            text = "You have no orders yet."
        else:
            lines = [f"Order #{o['id']} | {o['product']} | {o['duration']} | {o['status']}" for o in orders]
            text = "*Your last orders:*\n" + "\n".join(lines)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=home_keyboard())

    elif data == "menu:profile":
        total, approved = get_profile_counts(query.from_user.id)
        text = (
            "*👤 Profile*\n"
            f"Total Orders: {total}\n"
            f"Approved Orders: {approved}"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=home_keyboard())

    elif data == "menu:support":
        context.user_data["mode"] = "support"
        await query.edit_message_text(
            "🛟 *Support*\nPlease type your issue and send it now.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=home_keyboard(),
        )

    elif data == "menu:offers":
        text = (
            "*🔥 Current Offers*\n"
            "- 10% off on 30-day plans\n"
            "- Buy 2 services, get priority delivery\n"
            "- Weekend flash deals for VIP members"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=home_keyboard())

    elif data == "menu:how":
        text = (
            "*🧾 How it works*\n"
            "1. Choose a product\n"
            "2. Pick duration and account type\n"
            "3. Send details or upload photo\n"
            "4. Confirm and receive your order ID"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=home_keyboard())


async def on_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    action = (query.data or "").split(":", maxsplit=1)[1]
    draft = context.user_data.get("draft", {})

    if action == "products":
        await query.edit_message_text("*Select a product:*", parse_mode=ParseMode.MARKDOWN, reply_markup=products_keyboard())
    elif action == "durations":
        product = draft.get("product")
        if not product:
            await query.edit_message_text("Please select a product first.", reply_markup=products_keyboard())
            return
        await query.edit_message_text(
            f"*{product}*\nSelect duration:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=durations_keyboard(product),
        )
    elif action == "account_types":
        await query.edit_message_text(
            "Select account type:",
            reply_markup=account_types_keyboard(),
        )
        context.user_data["mode"] = "order_details"


async def on_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    product = (query.data or "").split(":", maxsplit=1)[1]
    draft = context.user_data.setdefault("draft", {})
    draft["product"] = product

    await query.edit_message_text(
        f"*{product}*\nSelect duration:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=durations_keyboard(product),
    )


async def on_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    duration = (query.data or "").split(":", maxsplit=1)[1]
    draft = context.user_data.setdefault("draft", {})
    draft["duration"] = duration

    await query.edit_message_text("Select account type:", reply_markup=account_types_keyboard())


async def on_account_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    account_type = (query.data or "").split(":", maxsplit=1)[1]
    draft = context.user_data.setdefault("draft", {})
    draft["account_type"] = account_type
    draft.pop("details_text", None)
    draft.pop("details_file_id", None)
    context.user_data["mode"] = "order_details"

    await query.edit_message_text(
        "Send details (text) or upload photo",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Home", callback_data="menu:home")]]
        ),
    )


async def on_capture_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    mode = context.user_data.get("mode")

    if mode == "support":
        message_text = (update.message.text or "").strip()
        if not message_text:
            await update.message.reply_text("Please send your issue as text.")
            return
        ticket_id = save_ticket(update.effective_user.id, update.effective_user.username, message_text)
        context.user_data.pop("mode", None)
        await update.message.reply_text(
            f"✅ Support request sent! Ticket #{ticket_id}",
            reply_markup=home_keyboard(),
        )
        return

    if mode == "order_details":
        draft = context.user_data.get("draft", {})
        details_text = None
        details_file_id = None

        if update.message.text:
            details_text = update.message.text.strip()
        elif update.message.photo:
            details_file_id = update.message.photo[-1].file_id
        else:
            await update.message.reply_text("Please send text or upload a photo.")
            return

        draft["details_text"] = details_text
        draft["details_file_id"] = details_file_id
        context.user_data["draft"] = draft
        context.user_data.pop("mode", None)

        await update.message.reply_text(
            order_summary_text(draft),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=confirm_keyboard(),
        )
        return


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    draft = context.user_data.get("draft") or {}
    required = ["product", "duration", "account_type"]
    if any(not draft.get(k) for k in required):
        await query.edit_message_text("Order draft is incomplete. Please start again.", reply_markup=home_keyboard())
        return

    order_id = save_order(
        user_id=query.from_user.id,
        username=query.from_user.username,
        product=draft["product"],
        duration=draft["duration"],
        account_type=draft["account_type"],
        details_text=draft.get("details_text"),
        details_file_id=draft.get("details_file_id"),
    )

    context.user_data.pop("draft", None)
    await query.edit_message_text(
        f"✅ Order placed! Order ID: {order_id}",
        reply_markup=home_keyboard(),
    )


def build_application() -> Application:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_menu, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(on_back, pattern=r"^back:"))
    application.add_handler(CallbackQueryHandler(on_product, pattern=r"^product:"))
    application.add_handler(CallbackQueryHandler(on_duration, pattern=r"^duration:"))
    application.add_handler(CallbackQueryHandler(on_account_type, pattern=r"^account:"))
    application.add_handler(CallbackQueryHandler(on_confirm, pattern=r"^order:confirm$"))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_capture_message))

    return application


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Please set BOT_TOKEN environment variable.")

    init_db()
    keep_alive()

    app = build_application()
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
