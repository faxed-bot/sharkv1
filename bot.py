import asyncio
import logging
import os
import signal
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("sharkv1-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
UPI_ID = os.getenv("UPI_ID", "").strip()
BINANCE_ID = os.getenv("BINANCE_ID", "").strip()
DB_PATH = os.getenv("DB_PATH", "orders.db").strip() or "orders.db"

CATALOG: dict[str, dict[str, int]] = {
    "YT": {"1M": 25, "3M": 149},
    "Gemini": {"12M": 159},
    "Spotify": {"2M": 49, "3M": 89},
    "Crunchyroll": {"1M": 39},
}

PRODUCTS_REQUIRE_LOGIN = {"Spotify", "Crunchyroll"}


def db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(db_connection()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                product TEXT NOT NULL,
                duration TEXT NOT NULL,
                price INTEGER NOT NULL,
                account_type TEXT NOT NULL,
                email TEXT,
                password TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                payment_txn TEXT
            )
            """
        )
    logger.info("Database initialized at %s", DB_PATH)


def create_order(payload: dict[str, Any]) -> int:
    with closing(db_connection()) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO orders (
                user_id, username, product, duration, price, account_type,
                email, password, status, created_at, payment_txn
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
            """,
            (
                payload["user_id"],
                payload.get("username"),
                payload["product"],
                payload["duration"],
                payload["price"],
                payload["account_type"],
                payload.get("email"),
                payload.get("password"),
                datetime.utcnow().isoformat(timespec="seconds"),
                payload.get("payment_txn"),
            ),
        )
        return int(cursor.lastrowid)


def update_payment_txn(order_id: int, payment_txn: str) -> None:
    with closing(db_connection()) as conn, conn:
        conn.execute(
            "UPDATE orders SET payment_txn = ?, status = 'PENDING' WHERE id = ?",
            (payment_txn, order_id),
        )


def update_order_status(order_id: int, status: str) -> None:
    with closing(db_connection()) as conn, conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))


def get_order(order_id: int) -> sqlite3.Row | None:
    with closing(db_connection()) as conn:
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def get_user_orders(user_id: int) -> list[sqlite3.Row]:
    with closing(db_connection()) as conn:
        return conn.execute(
            "SELECT id, product, duration, status FROM orders WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()


def get_profile_counts(user_id: int) -> tuple[int, int]:
    with closing(db_connection()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (user_id,)).fetchone()[0]
        approved = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE user_id = ? AND status = 'APPROVED'",
            (user_id,),
        ).fetchone()[0]
    return int(total), int(approved)


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒 Order", callback_data="menu:order")],
            [InlineKeyboardButton("📦 My Orders", callback_data="menu:orders")],
            [InlineKeyboardButton("👤 Profile", callback_data="menu:profile")],
            [InlineKeyboardButton("📞 Support", callback_data="menu:support")],
        ]
    )


def products_markup() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(product, callback_data=f"product:{product}")] for product in CATALOG]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def durations_markup(product: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{duration} - ₹{price}", callback_data=f"duration:{product}:{duration}")]
        for duration, price in CATALOG[product].items()
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:order")])
    return InlineKeyboardMarkup(rows)


async def show_main_menu(target, text: str) -> None:
    await target(text, reply_markup=main_menu_markup())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    if update.message:
        await show_main_menu(update.message.reply_text, "Welcome to SharkV1! Choose an option:")


async def on_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if data == "menu:home":
        await query.edit_message_text("Main Menu", reply_markup=main_menu_markup())
    elif data == "menu:order":
        context.user_data.pop("draft", None)
        await query.edit_message_text("Choose a product:", reply_markup=products_markup())
    elif data == "menu:orders":
        user = query.from_user
        orders = get_user_orders(user.id)
        if not orders:
            text = "You have no orders yet."
        else:
            text = "\n".join(
                f"• Order #{row['id']} | {row['product']} {row['duration']} | {row['status']}" for row in orders[:20]
            )
        await query.edit_message_text(text, reply_markup=main_menu_markup())
    elif data == "menu:profile":
        user = query.from_user
        total, approved = get_profile_counts(user.id)
        username = f"@{user.username}" if user.username else "N/A"
        text = (
            "Profile\n"
            f"User ID: {user.id}\n"
            f"Username: {username}\n"
            f"Total Orders: {total}\n"
            f"Approved Orders: {approved}"
        )
        await query.edit_message_text(text, reply_markup=main_menu_markup())
    elif data == "menu:support":
        support_text = "Support: Please message this bot with your issue. Our team will respond soon."
        await query.edit_message_text(support_text, reply_markup=main_menu_markup())


async def on_product_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    _, product = query.data.split(":", maxsplit=1)
    context.user_data["draft"] = {"product": product}
    await query.edit_message_text(f"Selected {product}. Choose duration:", reply_markup=durations_markup(product))


async def on_duration_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    _, product, duration = query.data.split(":", maxsplit=2)

    draft = context.user_data.get("draft", {})
    draft.update(
        {
            "product": product,
            "duration": duration,
            "price": CATALOG[product][duration],
        }
    )
    context.user_data["draft"] = draft

    if product in PRODUCTS_REQUIRE_LOGIN:
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Use My Account", callback_data="acct:USER_PROVIDED")],
                [InlineKeyboardButton("Use Seller Account", callback_data="acct:OUR_ACCOUNT")],
            ]
        )
        await query.edit_message_text("Choose account type:", reply_markup=keyboard)
        return

    draft["account_type"] = "OUR_ACCOUNT"
    await query.edit_message_text(render_order_summary(draft), reply_markup=confirm_markup())


def render_order_summary(draft: dict[str, Any]) -> str:
    return (
        "Order Summary\n"
        f"Product: {draft.get('product')}\n"
        f"Duration: {draft.get('duration')}\n"
        f"Price: ₹{draft.get('price')}\n"
        f"Account Type: {draft.get('account_type', 'OUR_ACCOUNT')}"
    )


def confirm_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Confirm Order", callback_data="order:confirm")],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="menu:home")],
        ]
    )


async def on_account_type_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    _, account_type = query.data.split(":", maxsplit=1)
    draft = context.user_data.get("draft", {})
    draft["account_type"] = account_type
    context.user_data["draft"] = draft

    if account_type == "USER_PROVIDED":
        context.user_data["awaiting_credentials"] = True
        await query.edit_message_text(
            "Please send your email and password in this format:\nemail,password"
        )
        return

    draft["email"] = None
    draft["password"] = None
    await query.edit_message_text(render_order_summary(draft), reply_markup=confirm_markup())


async def on_text_or_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    if context.user_data.get("awaiting_credentials"):
        text = (update.message.text or "").strip()
        if not text or "," not in text:
            await update.message.reply_text("Invalid format. Send as: email,password")
            return
        email, password = [v.strip() for v in text.split(",", maxsplit=1)]
        if not email or not password:
            await update.message.reply_text("Invalid format. Send as: email,password")
            return
        draft = context.user_data.get("draft", {})
        draft["email"] = email
        draft["password"] = password
        context.user_data["draft"] = draft
        context.user_data["awaiting_credentials"] = False
        await update.message.reply_text(render_order_summary(draft), reply_markup=confirm_markup())
        return

    pending_order_id = context.user_data.get("awaiting_payment_order_id")
    if pending_order_id:
        txn_data = ""
        if update.message.text:
            txn_data = update.message.text.strip()
        elif update.message.photo:
            txn_data = f"PHOTO_FILE_ID:{update.message.photo[-1].file_id}"

        if not txn_data:
            await update.message.reply_text("Please send a transaction ID text or a payment screenshot.")
            return

        update_payment_txn(int(pending_order_id), txn_data)
        order = get_order(int(pending_order_id))
        context.user_data["awaiting_payment_order_id"] = None

        await update.message.reply_text(
            f"Payment evidence received for Order #{pending_order_id}. Awaiting admin review.",
            reply_markup=main_menu_markup(),
        )

        if order:
            await notify_admin_new_order(context, order)
        return

    await update.message.reply_text("Use /start to open the menu.")


async def on_order_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user = query.from_user
    draft = context.user_data.get("draft")
    if not draft:
        await query.edit_message_text("No active order draft. Please start again.", reply_markup=main_menu_markup())
        return

    order_id = create_order(
        {
            "user_id": user.id,
            "username": user.username,
            "product": draft.get("product"),
            "duration": draft.get("duration"),
            "price": draft.get("price"),
            "account_type": draft.get("account_type", "OUR_ACCOUNT"),
            "email": draft.get("email"),
            "password": draft.get("password"),
            "payment_txn": None,
        }
    )

    context.user_data["awaiting_payment_order_id"] = order_id
    payment_text = (
        "Payment Instructions:\n"
        f"UPI: {UPI_ID or 'Not configured'}\n"
        f"Binance ID: {BINANCE_ID or 'Not configured'}\n\n"
        f"Order ID: #{order_id}\n"
        "Click below after payment."
    )
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("I Have Paid", callback_data=f"paid:{order_id}")], [InlineKeyboardButton("⬅️ Main Menu", callback_data="menu:home")]]
    )
    await query.edit_message_text(payment_text, reply_markup=markup)


async def on_paid_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    _, order_id_str = query.data.split(":", maxsplit=1)
    context.user_data["awaiting_payment_order_id"] = int(order_id_str)
    await query.edit_message_text("Send Transaction ID OR upload payment screenshot.")


async def notify_admin_new_order(context: ContextTypes.DEFAULT_TYPE, order: sqlite3.Row) -> None:
    if not ADMIN_CHAT_ID:
        logger.warning("ADMIN_CHAT_ID not configured. Skipping admin notification for order %s", order["id"])
        return

    text = (
        "New Order:\n"
        f"Order ID: #{order['id']}\n"
        f"User: {order['username'] or 'N/A'} ({order['user_id']})\n"
        f"Product: {order['product']}\n"
        f"Duration: {order['duration']}\n"
        f"Account Type: {order['account_type']}\n"
        f"Txn ID: {order['payment_txn'] or 'N/A'}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Approve",
                    callback_data=f"admin:APPROVE:{order['id']}:{order['user_id']}",
                ),
                InlineKeyboardButton(
                    "Reject",
                    callback_data=f"admin:REJECTED:{order['id']}:{order['user_id']}",
                ),
            ]
        ]
    )

    try:
        await context.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=text, reply_markup=keyboard)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send admin notification: %s", exc)


async def on_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    if not ADMIN_CHAT_ID:
        await query.edit_message_text("ADMIN_CHAT_ID not configured.")
        return

    if str(query.from_user.id) != str(ADMIN_CHAT_ID):
        await query.answer("Unauthorized", show_alert=True)
        return

    _, new_status, order_id_str, user_id_str = query.data.split(":", maxsplit=3)
    order_id = int(order_id_str)
    user_id = int(user_id_str)

    if new_status == "APPROVE":
        status_to_set = "APPROVED"
    else:
        status_to_set = "REJECTED"

    update_order_status(order_id, status_to_set)
    await query.edit_message_text(f"Order #{order_id} marked as {status_to_set}.")

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"Your Order #{order_id} has been {status_to_set}.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not notify user %s for order %s: %s", user_id, order_id, exc)


def build_application() -> Application:
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_menu_click, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(on_product_click, pattern=r"^product:"))
    application.add_handler(CallbackQueryHandler(on_duration_click, pattern=r"^duration:"))
    application.add_handler(CallbackQueryHandler(on_account_type_click, pattern=r"^acct:"))
    application.add_handler(CallbackQueryHandler(on_order_confirm, pattern=r"^order:confirm$"))
    application.add_handler(CallbackQueryHandler(on_paid_click, pattern=r"^paid:"))
    application.add_handler(CallbackQueryHandler(on_admin_action, pattern=r"^admin:"))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_text_or_photo))

    return application


async def run_bot() -> None:
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is required. Set BOT_TOKEN environment variable.")
        return

    init_db()
    app = build_application()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            logger.warning("Signal handlers not supported on this platform.")

    await app.initialize()
    await app.start()
    if app.updater is None:
        logger.error("Updater failed to initialize.")
        return

    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Bot polling started")

    try:
        await stop_event.wait()
    finally:
        logger.info("Stopping bot...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("Bot stopped gracefully")


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot terminated")
