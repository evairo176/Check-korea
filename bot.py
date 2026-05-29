"""Telegram Bot for EPS Korea monitoring."""
import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from eps_scraper import get_eps_data, format_result

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Store user sessions (username, password, birth)
user_sessions = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    welcome = (
        "🇰🇷 **EPS Korea Monitor Bot**\n\n"
        "Bot ini untuk cek status pengiriman kerja ke Korea.\n\n"
        "**Perintah:**\n"
        "/check — Cek status dengan data yang sudah disimpan\n"
        "/set — Simpan data akun baru\n"
        "/status — Lihat status akun tersimpan\n"
        "/help — Bantuan\n\n"
        "Atau langsung kirim format:\n"
        "`NIK|Password|TanggalLahir`\n"
        "Contoh: `1234567890123|password123|020216`"
    )
    await update.message.reply_text(welcome, parse_mode='Markdown')


async def set_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /set command - save account data."""
    user_id = update.effective_user.id
    args = context.args

    if len(args) == 3:
        user_sessions[user_id] = {
            'username': args[0],
            'password': args[1],
            'birth': args[2]
        }
        await update.message.reply_text(
            f"✅ Data tersimpan!\n"
            f"NIK: `{args[0]}`\n"
            f"Gunakan /check untuk cek status",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "Format: `/set NIK Password TanggalLahir`\n"
            "Contoh: `/set 1234567890123 password123 020216`",
            parse_mode='Markdown'
        )


async def check_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /check command - check EPS status."""
    user_id = update.effective_user.id

    if user_id not in user_sessions:
        await update.message.reply_text(
            "⚠️ Data akun belum disimpan.\n"
            "Gunakan /set NIK Password TanggalLahir\n"
            "Atau kirim format: NIK|Password|TanggalLahir",
            parse_mode='Markdown'
        )
        return

    session = user_sessions[user_id]
    msg = await update.message.reply_text("⏳ Sedang mengecek status EPS...")

    data = await get_eps_data(session['username'], session['password'], session['birth'])

    if "error" in data:
        await msg.edit_text(f"❌ Error: {data['error']}")
    else:
        formatted = format_result(data, session['username'])
        await msg.edit_text(formatted, parse_mode='Markdown')


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle direct message with NIK|Password|Birth format."""
    text = update.message.text.strip()

    if "|" in text:
        parts = text.split("|")
        if len(parts) == 3:
            username, password, birth = parts[0].strip(), parts[1].strip(), parts[2].strip()

            msg = await update.message.reply_text("⏳ Sedang mengecek status EPS...")

            data = await get_eps_data(username, password, birth)

            if "error" in data:
                await msg.edit_text(f"❌ Error: {data['error']}")
            else:
                formatted = format_result(data, username)
                await msg.edit_text(formatted, parse_mode='Markdown')
        else:
            await update.message.reply_text(
                "Format salah. Gunakan: NIK|Password|TanggalLahir\n"
                "Contoh: 1234567890123|password123|020216"
            )
    else:
        await update.message.reply_text(
            "Gunakan /help untuk bantuan\n"
            "Atau kirim format: NIK|Password|TanggalLahir"
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "🇰🇷 **EPS Korea Monitor — Bantuan**\n\n"
        "**Cara Pakai:**\n\n"
        "1️⃣ Simpan data akun:\n"
        "`/set NIK Password TanggalLahir`\n\n"
        "2️⃣ Cek status:\n"
        "`/check`\n\n"
        "**Atau langsung kirim:**\n"
        "`NIK|Password|TanggalLahir`\n\n"
        "**Contoh:**\n"
        "`/set 1234567890123 password123 020216`\n"
        "`/check`\n\n"
        "**Informasi yang ditampilkan:**\n"
        "• Status Pengiriman\n"
        "• Status Penerimaan\n"
        "• Status SLC/Mediasi\n"
        "• Masa Berlaku Visa"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')


def main():
    """Run the Telegram bot."""
    token = os.environ.get('EPS_BOT_TOKEN', '')

    if not token:
        logger.error("EPS_BOT_TOKEN not set!")
        print("ERROR: Set EPS_BOT_TOKEN environment variable!")
        print("Example: export EPS_BOT_TOKEN='your-telegram-bot-token'")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set", set_account))
    app.add_handler(CommandHandler("check", check_status))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("EPS Monitor Bot started!")
    print("🤖 EPS Monitor Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
