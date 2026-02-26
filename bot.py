import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
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

# Advanced product catalog (price optional, useful for better summaries)
CATALOG: dict[str, dict[str, int]] = {
    "Netflix": {"1 day": 5, "7 days": 20, "30 days": 60},
    "YouTube Premium": {"1 day": 4, "7 days": 15, "30 days": 45},
    "Spotify": {"1 day": 3, "7 days": 12, "30 days": 35},
    "ChatGPT Plus": {"1 day": 6, "7 days": 25, "30 days": 80},
}

ACCOUNT_TYPES = ["Basic", "Premium", "VIP"]

WELCOME_TEXT = (
    "🦈 *SharkV1 Shop*\n"
    "✅ Premium Services\n"
    "🔒 Secure Payments\n"
    "⚡ Instant Delivery\n"
    "🛟 24/7 Support\n"
    "👇 Choose a category:"
)


# ------------------------- Database Layer -------------------------
def db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create required SQLite tables if they do not exist."""
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
            SELECT id, product, duration, status, created_at
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


# ------------------------- State Helpers -------------------------
def get_draft(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    return context.user_data.setdefault("draft", {})


def clear_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("mode", None)
    context.user_data.pop("draft", None)


# ------------------------- Keyboards -------------------------
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
    rows = [[InlineKeyboardButton(product, callback_data=f"product:{product}")] for product in CATALOG]
    rows.append(
        [
            InlineKeyboardButton("🏠 Home", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def durations_keyboard(product: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{duration} · ${price}", callback_data=f"duration:{duration}")]
        for duration, price in CATALOG[product].items()
    ]
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data="back:products"),
            InlineKeyboardButton("🏠 Home", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def account_types_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(account, callback_data=f"account:{account}")] for account in ACCOUNT_TYPES]
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data="back:durations"),
            InlineKeyboardButton("🏠 Home", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def details_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back", callback_data="back:account_types"), InlineKeyboardButton("🏠 Home", callback_data="menu:home")]]
    )


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Confirm", callback_data="order:confirm")],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="back:details"),
                InlineKeyboardButton("❌ Cancel", callback_data="menu:home"),
            ],
        ]
    )


# ------------------------- Text Builders -------------------------
def render_order_summary(draft: dict[str, Any]) -> str:
    product = draft.get("product", "-")
    duration = draft.get("duration", "-")
    account_type = draft.get("account_type", "-")
    price = "-"
    if product in CATALOG and duration in CATALOG[product]:
        price = f"${CATALOG[product][duration]}"

    details_text = draft.get("details_text")
    details_file_id = draft.get("details_file_id")
    if details_text:
        details = details_text
    elif details_file_id:
        details = "[photo uploaded]"
    else:
        details = "-"

    return (
        "*Order Confirmation*\n"
        f"• Product: `{product}`\n"
        f"• Duration: `{duration}`\n"
        f"• Account Type: `{account_type}`\n"
        f"• Price: `{price}`\n"
        f"• Details: `{details}`"
    )


async def safe_edit_or_send(query, text: str, reply_markup: InlineKeyboardMarkup, parse_mode: Optional[str] = None) -> None:
    try:
        await query.edit_message_text(text=text, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest:
        await query.message.reply_text(text=text, parse_mode=parse_mode, reply_markup=reply_markup)


# ------------------------- Handlers -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_flow(context)
    if update.message:
        await update.message.reply_text(
            WELCOME_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(),
        )


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    action = query.data or ""

    if action == "menu:home":
        clear_flow(context)
        await safe_edit_or_send(
            query,
            WELCOME_TEXT,
            main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if action == "menu:order":
        clear_flow(context)
        context.user_data["draft"] = {}
        await safe_edit_or_send(
            query,
            "*Select a product:*",
            products_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if action == "menu:orders":
        orders = get_user_orders(query.from_user.id)
        if not orders:
            text = "📦 You have no orders yet."
        else:
            lines = [
                f"Order #{row['id']} | {row['product']} | {row['duration']} | {row['status']}"
                for row in orders
            ]
            text = "*📦 Your last 20 orders*\n" + "\n".join(lines)

        await safe_edit_or_send(query, text, home_keyboard(), parse_mode=ParseMode.MARKDOWN)
        return

    if action == "menu:profile":
        total, approved = get_profile_counts(query.from_user.id)
        text = (
            "*👤 Profile*\n"
            f"Total Orders: {total}\n"
            f"Approved Orders: {approved}"
        )
        await safe_edit_or_send(query, text, home_keyboard(), parse_mode=ParseMode.MARKDOWN)
        return

    if action == "menu:support":
        context.user_data["mode"] = "support"
        await safe_edit_or_send(
            query,
            "🛟 *Support*\nPlease type your issue and send it now.",
            home_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if action == "menu:offers":
        text = (
            "*🔥 Special Offers*\n"
            "• 10% OFF on all 30-day plans\n"
            "• Priority queue for Premium/VIP orders\n"
            "• Weekend flash discounts on selected products"
        )
        await safe_edit_or_send(query, text, home_keyboard(), parse_mode=ParseMode.MARKDOWN)
        return

    if action == "menu:how":
        text = (
            "*🧾 How it works*\n"
            "1. Tap *Order* and select your product\n"
            "2. Choose duration and account type\n"
            "3. Send order details as text or photo\n"
            "4. Confirm and receive your order ID"
        )
        await safe_edit_or_send(query, text, home_keyboard(), parse_mode=ParseMode.MARKDOWN)


async def back_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    action = (query.data or "").split(":", maxsplit=1)[1]
    draft = get_draft(context)

    if action == "products":
        await safe_edit_or_send(query, "*Select a product:*", products_keyboard(), parse_mode=ParseMode.MARKDOWN)
        return

    if action == "durations":
        product = draft.get("product")
        if not product:
            await safe_edit_or_send(query, "*Select a product:*", products_keyboard(), parse_mode=ParseMode.MARKDOWN)
            return
        await safe_edit_or_send(
            query,
            f"*{product}*\nSelect duration:",
            durations_keyboard(product),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await query.answer()

    if action == "account_types":
        await safe_edit_or_send(query, "Select account type:", account_types_keyboard())
        return

    if action == "details":
        context.user_data["mode"] = "order_details"
        await safe_edit_or_send(query, "Send details (text) or upload photo", details_keyboard())


async def on_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    product = (query.data or "").split(":", maxsplit=1)[1]
    if product not in CATALOG:
        await safe_edit_or_send(query, "Invalid product. Please choose again.", products_keyboard())
        return

    draft = get_draft(context)
    draft["product"] = product
    draft.pop("duration", None)
    draft.pop("account_type", None)
    draft.pop("details_text", None)
    draft.pop("details_file_id", None)

    await safe_edit_or_send(
        query,
        f"*{product}*\nSelect duration:",
        durations_keyboard(product),
        parse_mode=ParseMode.MARKDOWN,
    )


async def on_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    duration = (query.data or "").split(":", maxsplit=1)[1]
    draft = get_draft(context)
    product = draft.get("product")

    if not product or product not in CATALOG:
        await safe_edit_or_send(query, "Please select a product first.", products_keyboard())
        return
    if duration not in CATALOG[product]:
        await safe_edit_or_send(query, "Invalid duration. Please choose again.", durations_keyboard(product))
        return

    draft["duration"] = duration
    draft.pop("account_type", None)
    draft.pop("details_text", None)
    draft.pop("details_file_id", None)

    await safe_edit_or_send(query, "Select account type:", account_types_keyboard())


async def on_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    account_type = (query.data or "").split(":", maxsplit=1)[1]
    if account_type not in ACCOUNT_TYPES:
        await safe_edit_or_send(query, "Invalid account type. Please choose again.", account_types_keyboard())
        return

    draft = get_draft(context)
    if not draft.get("product") or not draft.get("duration"):
        await safe_edit_or_send(query, "Please select product and duration first.", products_keyboard())
        return

    draft["account_type"] = account_type
    draft.pop("details_text", None)
    draft.pop("details_file_id", None)
    context.user_data["mode"] = "order_details"

    await safe_edit_or_send(query, "Send details (text) or upload photo", details_keyboard())


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    draft = context.user_data.get("draft", {})
    required = ("product", "duration", "account_type")
    if any(not draft.get(key) for key in required):
        await safe_edit_or_send(
            query,
            "Order draft is incomplete. Please start again.",
            home_keyboard(),
        )
        return

    if not draft.get("details_text") and not draft.get("details_file_id"):
        context.user_data["mode"] = "order_details"
        await safe_edit_or_send(query, "Please send details first (text or photo).", details_keyboard())
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

    clear_flow(context)
    await safe_edit_or_send(query, f"✅ Order placed! Order ID: {order_id}", home_keyboard())


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    mode = context.user_data.get("mode")

    if mode == "support":
        ticket_text = (update.message.text or "").strip()
        if not ticket_text:
            await update.message.reply_text("Please send your issue as text.")
            return

        ticket_id = save_ticket(
            user_id=update.effective_user.id,
            username=update.effective_user.username,
            message_text=ticket_text,
        )
        context.user_data.pop("mode", None)
        await update.message.reply_text(
            f"✅ Support request sent! Ticket #{ticket_id}",
            reply_markup=home_keyboard(),
        )
        return

    if mode == "order_details":
        draft = get_draft(context)

        if update.message.text:
            details = update.message.text.strip()
            if not details:
                await update.message.reply_text("Details cannot be empty. Send text or upload photo.")
                return
            draft["details_text"] = details
            draft["details_file_id"] = None
        elif update.message.photo:
            draft["details_text"] = None
            draft["details_file_id"] = update.message.photo[-1].file_id
        else:
            await update.message.reply_text("Please send details as text or upload a photo.")
            return

        context.user_data.pop("mode", None)
        await update.message.reply_text(
            render_order_summary(draft),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=confirm_keyboard(),
        )
        return

    await update.message.reply_text(
        "Use /start to open the main menu.",
        reply_markup=main_menu_keyboard(),
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)


def build_application() -> Application:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(menu_router, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(back_router, pattern=r"^back:"))
    application.add_handler(CallbackQueryHandler(on_product, pattern=r"^product:"))
    application.add_handler(CallbackQueryHandler(on_duration, pattern=r"^duration:"))
    application.add_handler(CallbackQueryHandler(on_account, pattern=r"^account:"))
    application.add_handler(CallbackQueryHandler(on_confirm, pattern=r"^order:confirm$"))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))
    application.add_error_handler(on_error)

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
