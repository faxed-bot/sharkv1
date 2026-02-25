# sharkv1 Telegram Bot

Production-ready Telegram ordering bot built with **Python** and **python-telegram-bot v21+** using async polling.

## Features
- Async bot app via `ApplicationBuilder`
- SQLite order storage (`orders` table)
- Inline keyboard based order flow
- Payment confirmation with transaction ID or screenshot evidence
- Admin approve/reject controls from Telegram
- Graceful shutdown handling (`SIGINT` / `SIGTERM`)
- Railway-friendly process setup via `Procfile`

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | Yes | Telegram bot token |
| `ADMIN_CHAT_ID` | No | Admin Telegram user/chat id for order review |
| `UPI_ID` | No | Payment UPI id shown to buyers |
| `BINANCE_ID` | No | Payment Binance id shown to buyers |
| `DB_PATH` | No | SQLite database path (default: `orders.db`) |

If `UPI_ID` or `BINANCE_ID` are missing, the bot safely shows `Not configured`.

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="<your-token>"
export ADMIN_CHAT_ID="<admin-chat-id>"  # optional but recommended
export UPI_ID="your-upi@bank"            # optional
export BINANCE_ID="123456"               # optional
python bot.py
```

## Database Schema

`orders` table fields:
- `id` (auto increment)
- `user_id`
- `username`
- `product`
- `duration`
- `price`
- `account_type` (`USER_PROVIDED` / `OUR_ACCOUNT`)
- `email` (nullable)
- `password` (nullable)
- `status` (`PENDING`, `APPROVED`, `REJECTED`)
- `created_at`
- `payment_txn` (nullable)

## Deploy on Railway
1. Push this project to a GitHub repo.
2. Create a new Railway project from the repo.
3. Add environment variables in Railway.
4. Railway uses `Procfile`:

```procfile
worker: python bot.py
```

## Notes
- Keep secrets only in environment variables.
- Do not hardcode credentials.
- Ensure `ADMIN_CHAT_ID` matches your Telegram account to approve/reject orders.
