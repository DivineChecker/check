#!/usr/bin/env python3
"""
New-API Daily Check-in Telegram Bot
Supports cookie+api_user auth OR username+password auth.
Runs daily at 11:30 IST. Single-user (owner only).
"""

import asyncio
import logging
import os
import json
from datetime import time
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db import Database
from checkin import verify_account, do_checkin_site
from backup import export_sites, import_sites

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ["BOT_TOKEN"]
OWNER_ID   = int(os.environ["OWNER_CHAT_ID"])
IST        = ZoneInfo("Asia/Kolkata")
DB_PATH    = os.environ.get("DB_PATH", "data/sites.db")

db = Database(DB_PATH)

# ── Conversation states ────────────────────────────────────────────────────
(
    ASK_AUTH_TYPE,
    ASK_NAME, ASK_URL,
    # cookie flow
    ASK_COOKIE, ASK_API_USER,
    # password flow
    ASK_USERNAME, ASK_PASSWORD,
    # edit
    EDIT_CHOOSE_FIELD, EDIT_VALUE,
    AWAIT_RESTORE_FILE,
) = range(10)


# ══════════════════════════════════════════════════════════════════════════
# GUARDS
# ══════════════════════════════════════════════════════════════════════════

def owner_only(func):
    """Decorator: silently ignore messages from non-owners."""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = (update.effective_user or update.callback_query.from_user).id
        if uid != OWNER_ID:
            return
        return await func(update, ctx)
    return wrapper


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

def site_summary(site: dict) -> str:
    auth = "🍪 Cookie" if site["auth_type"] == "cookie" else "🔑 Password"
    valid = "✅" if site.get("last_verify") == "ok" else "❓"
    last  = site.get("last_checkin") or "Never"
    return (
        f"<b>{site['name']}</b>  {valid}\n"
        f"🌐 {site['url']}\n"
        f"🔐 Auth: {auth}\n"
        f"⏰ Last check-in: {last}\n"
        f"🆔 ID: {site['id']}"
    )


async def send_main_menu(update: Update):
    kb = [
        [InlineKeyboardButton("➕ Add site",    callback_data="menu_add")],
        [InlineKeyboardButton("📋 List sites",  callback_data="menu_list")],
        [InlineKeyboardButton("▶️ Run now",     callback_data="menu_run")],
        [InlineKeyboardButton("🗑 Remove site",  callback_data="menu_remove")],
        [InlineKeyboardButton("✅ Verify all",   callback_data="menu_verify")],
        [InlineKeyboardButton("💾 Backup sites", callback_data="menu_backup")],
        [InlineKeyboardButton("📥 Restore backup", callback_data="menu_restore")],
    ]
    text = "🤖 <b>New-API Check-in Bot</b>\n\nWhat would you like to do?"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════════════

@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_main_menu(update)


@owner_only
async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_main_menu(update)


@owner_only
async def cmd_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await run_all_checkins(ctx.application, manual=True, chat_id=update.effective_chat.id)


@owner_only
async def cmd_verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Verifying all accounts...")
    await verify_all(ctx.application, chat_id=update.effective_chat.id)


@owner_only
async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sites = db.list_sites()
    if not sites:
        await update.message.reply_text("No sites added yet. Use /add to add one.")
        return
    for s in sites:
        await update.message.reply_text(site_summary(s), parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════
# MENU CALLBACKS
# ══════════════════════════════════════════════════════════════════════════

@owner_only
async def cb_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "menu_add":
        await q.edit_message_text(
            "What auth method does this site use?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🍪 Cookie + API User", callback_data="auth_cookie")],
                [InlineKeyboardButton("🔑 Username + Password", callback_data="auth_password")],
                [InlineKeyboardButton("❌ Cancel", callback_data="menu_cancel")],
            ])
        )
        return ASK_AUTH_TYPE

    if data == "menu_list":
        sites = db.list_sites()
        if not sites:
            await q.edit_message_text("No sites added yet.\n\nUse ➕ Add site to get started.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu_back")]]))
        else:
            await q.edit_message_text(f"You have {len(sites)} site(s):",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu_back")]]))
            for s in sites:
                await update.effective_chat.send_message(site_summary(s), parse_mode="HTML")
        return ConversationHandler.END

    if data == "menu_run":
        await q.edit_message_text("⏳ Running check-ins now...")
        await run_all_checkins(ctx.application, manual=True, chat_id=q.message.chat_id)
        return ConversationHandler.END

    if data == "menu_remove":
        sites = db.list_sites()
        if not sites:
            await q.edit_message_text("No sites to remove.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu_back")]]))
            return ConversationHandler.END
        kb = [[InlineKeyboardButton(f"🗑 {s['name']}", callback_data=f"remove_{s['id']}")] for s in sites]
        kb.append([InlineKeyboardButton("❌ Cancel", callback_data="menu_back")])
        await q.edit_message_text("Select site to remove:", reply_markup=InlineKeyboardMarkup(kb))
        return ConversationHandler.END

    if data == "menu_verify":
        await q.edit_message_text("🔍 Verifying all accounts...")
        await verify_all(ctx.application, chat_id=q.message.chat_id)
        return ConversationHandler.END

    if data == "menu_backup":
        await q.edit_message_text("💾 Generating backup...")
        await cmd_backup_inline(q)
        return ConversationHandler.END

    if data == "menu_restore":
        await q.edit_message_text(
            "📥 <b>Restore from backup</b>\n\n"
            "Send me your <code>.bak</code> file now.\n"
            "Use /cancel to abort.",
            parse_mode="HTML"
        )
        return AWAIT_RESTORE_FILE

    if data in ("menu_back", "menu_cancel"):
        await send_main_menu(update)
        return ConversationHandler.END


@owner_only
async def cb_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    site_id = int(q.data.split("_")[1])
    site = db.get_site(site_id)
    if site:
        db.delete_site(site_id)
        await q.edit_message_text(f"🗑 Removed: <b>{site['name']}</b>", parse_mode="HTML")
    else:
        await q.edit_message_text("Site not found.")


# ══════════════════════════════════════════════════════════════════════════
# ADD SITE CONVERSATION
# ══════════════════════════════════════════════════════════════════════════

@owner_only
async def cb_auth_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["auth_type"] = "cookie" if q.data == "auth_cookie" else "password"
    await q.edit_message_text("Give this site a name (e.g. DawCode Account 1):")
    return ASK_NAME


@owner_only
async def ask_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("Enter the site URL (e.g. https://dawclaudecode.com):")
    return ASK_URL


@owner_only
async def ask_credentials(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip().rstrip("/")
    if not url.startswith("http"):
        await update.message.reply_text("❌ Invalid URL. Must start with http:// or https://")
        return ASK_URL
    ctx.user_data["url"] = url

    if ctx.user_data["auth_type"] == "cookie":
        await update.message.reply_text(
            "Paste your <b>session cookie</b> value.\n\n"
            "<i>F12 → Network → any /api/ request → Request Headers → Cookie → copy value after <code>session=</code></i>",
            parse_mode="HTML"
        )
        return ASK_COOKIE
    else:
        await update.message.reply_text("Enter your <b>username or email</b>:", parse_mode="HTML")
        return ASK_USERNAME


# ── Cookie flow ────────────────────────────────────────────────────────────

@owner_only
async def ask_api_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["session_cookie"] = update.message.text.strip()
    await update.message.reply_text(
        "Enter your <b>API User ID</b> (numeric).\n\n"
        "<i>F12 → Network → any /api/ request → Request Headers → <code>new-api-user</code> value</i>",
        parse_mode="HTML"
    )
    return ASK_API_USER


@owner_only
async def save_cookie_site(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["api_user"] = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Verifying account...")

    site = {
        "name":           ctx.user_data["name"],
        "url":            ctx.user_data["url"],
        "auth_type":      "cookie",
        "session_cookie": ctx.user_data["session_cookie"],
        "api_user":       ctx.user_data["api_user"],
        "username":       None,
        "password":       None,
    }

    ok, detail = await verify_account(site)
    if ok:
        site_id = db.add_site(site, last_verify="ok")
        await msg.edit_text(
            f"✅ Account verified!\n\n{site_summary(db.get_site(site_id))}\n\n"
            f"💰 {detail}",
            parse_mode="HTML"
        )
    else:
        import uuid
        token = uuid.uuid4().hex[:12]
        _pending_saves[token] = site
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💾 Save anyway", callback_data=f"force_save:{token}")],
            [InlineKeyboardButton("❌ Discard",     callback_data="menu_back")],
        ])
        await msg.edit_text(
            f"⚠️ Verification failed: <code>{detail}</code>\n\nSave anyway?",
            reply_markup=kb, parse_mode="HTML"
        )

    ctx.user_data.clear()
    return ConversationHandler.END


# ── Password flow ──────────────────────────────────────────────────────────

@owner_only
async def ask_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["username"] = update.message.text.strip()
    await update.message.reply_text("Enter your <b>password</b>:", parse_mode="HTML")
    return ASK_PASSWORD


@owner_only
async def save_password_site(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["password"] = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Logging in and verifying...")

    site = {
        "name":           ctx.user_data["name"],
        "url":            ctx.user_data["url"],
        "auth_type":      "password",
        "session_cookie": None,
        "api_user":       None,
        "username":       ctx.user_data["username"],
        "password":       ctx.user_data["password"],
    }

    ok, detail = await verify_account(site)
    if ok:
        site_id = db.add_site(site, last_verify="ok")
        await msg.edit_text(
            f"✅ Login successful!\n\n{site_summary(db.get_site(site_id))}\n\n"
            f"💰 {detail}",
            parse_mode="HTML"
        )
    else:
        import uuid
        token = uuid.uuid4().hex[:12]
        _pending_saves[token] = site
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💾 Save anyway", callback_data=f"force_save:{token}")],
            [InlineKeyboardButton("❌ Discard",     callback_data="menu_back")],
        ])
        await msg.edit_text(
            f"⚠️ Verification failed: <code>{detail}</code>\n\nSave anyway?",
            reply_markup=kb, parse_mode="HTML"
        )

    ctx.user_data.clear()
    return ConversationHandler.END


# ── Force save (verification failed but user wants to save) ───────────────

# Temporary store for pending-save sites (keyed by a short token)
_pending_saves: dict = {}

@owner_only
async def cb_force_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    token = q.data[len("force_save:"):]
    site  = _pending_saves.pop(token, None)
    if not site:
        await q.edit_message_text("⚠️ Session expired. Please add the site again.")
        return
    site_id = db.add_site(site, last_verify="failed")
    await q.edit_message_text(
        f"💾 Saved (unverified).\n\n{site_summary(db.get_site(site_id))}",
        parse_mode="HTML"
    )


@owner_only
async def conv_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════
# CHECK-IN ENGINE
# ══════════════════════════════════════════════════════════════════════════

async def run_all_checkins(app, manual: bool = False, chat_id: int = None):
    cid = chat_id or OWNER_ID
    sites = db.list_sites()

    if not sites:
        await app.bot.send_message(cid, "⚠️ No sites configured. Use /add to add one.")
        return

    trigger = "🔘 Manual" if manual else "⏰ Scheduled (11:30 IST)"
    header  = f"<b>Check-in Report</b>  {trigger}\n🕐 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    lines   = []

    for site in sites:
        success, msg, balance = await do_checkin_site(site)
        status = "✅" if success else "❌"
        line   = f"{status} <b>{site['name']}</b>\n    {msg}"
        if balance:
            line += f"\n    💰 {balance}"
        lines.append(line)

        ts = __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M")
        db.update_checkin(site["id"], success, ts if success else None)

    await app.bot.send_message(cid, header + "\n\n".join(lines), parse_mode="HTML")


async def verify_all(app, chat_id: int):
    sites = db.list_sites()
    if not sites:
        await app.bot.send_message(chat_id, "No sites to verify.")
        return

    lines = ["<b>🔍 Verification Results</b>\n"]
    for site in sites:
        ok, detail = await verify_account(site)
        status = "✅ Valid" if ok else "❌ Invalid"
        db.update_verify(site["id"], "ok" if ok else "failed")
        lines.append(f"{status}  <b>{site['name']}</b>\n    {detail}")

    await app.bot.send_message(chat_id, "\n\n".join(lines), parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════
# SCHEDULED JOB
# ══════════════════════════════════════════════════════════════════════════

async def scheduled_checkin(app):
    log.info("Running scheduled check-in...")
    await run_all_checkins(app, manual=False)


# ══════════════════════════════════════════════════════════════════════════
# BACKUP / RESTORE
# ══════════════════════════════════════════════════════════════════════════

async def cmd_backup_inline(q):
    """Called from menu button — sends encrypted .bak file."""
    sites = db.list_sites()
    if not sites:
        await q.edit_message_text("⚠️ No sites to back up.")
        return

    data     = export_sites(sites)
    filename = f"checkin_backup_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"

    await q.message.reply_document(
        document=data,
        filename=filename,
        caption=(
            f"💾 <b>Backup — {len(sites)} site(s)</b>\n\n"
            "🔐 Encrypted with your <code>BACKUP_PASSWORD</code>.\n"
            "Store this file safely. To restore, send it back to the bot via "
            "<b>📥 Restore backup</b> in the menu.",
        ),
        parse_mode="HTML",
    )
    await q.edit_message_text(f"✅ Backup sent ({len(sites)} sites).")


@owner_only
async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Direct /backup command."""
    sites = db.list_sites()
    if not sites:
        await update.message.reply_text("⚠️ No sites to back up.")
        return

    data     = export_sites(sites)
    filename = f"checkin_backup_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    msg      = await update.message.reply_text("💾 Generating backup...")

    await update.message.reply_document(
        document=data,
        filename=filename,
        caption=(
            f"💾 <b>Backup — {len(sites)} site(s)</b>\n\n"
            "🔐 Encrypted with your <code>BACKUP_PASSWORD</code>.\n"
            "To restore: send this file back to the bot.",
        ),
        parse_mode="HTML",
    )
    await msg.delete()


@owner_only
async def cmd_restore(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Direct /restore command — asks user to upload the file."""
    await update.message.reply_text(
        "📥 <b>Restore from backup</b>\n\n"
        "Send me your <code>.bak</code> file now.\n"
        "Use /cancel to abort.",
        parse_mode="HTML"
    )
    return AWAIT_RESTORE_FILE


@owner_only
async def handle_restore_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Receives the .bak file, decrypts, and restores sites."""
    doc = update.message.document
    if not doc:
        await update.message.reply_text("⚠️ Please send a file, not text. Try again or /cancel.")
        return AWAIT_RESTORE_FILE

    if not doc.file_name.endswith(".bak"):
        await update.message.reply_text("⚠️ That doesn't look like a .bak file. Try again or /cancel.")
        return AWAIT_RESTORE_FILE

    msg = await update.message.reply_text("🔓 Decrypting backup...")

    # Download file content
    file  = await doc.get_file()
    bdata = bytes(await file.download_as_bytearray())

    ok, sites, err = import_sites(bdata)
    if not ok:
        await msg.edit_text(f"❌ Restore failed: {err}")
        return ConversationHandler.END

    if not sites:
        await msg.edit_text("⚠️ Backup is valid but contains no sites.")
        return ConversationHandler.END

    # Show preview and ask for confirmation
    ctx.user_data["restore_sites"] = sites
    lines = [f"  • {s.get('name')} ({s.get('url')})" for s in sites]
    preview = "\n".join(lines)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Restore all (merge)",    callback_data="restore_merge")],
        [InlineKeyboardButton("🔄 Restore (replace all)", callback_data="restore_replace")],
        [InlineKeyboardButton("❌ Cancel",                 callback_data="menu_back")],
    ])
    await msg.edit_text(
        f"📦 Found <b>{len(sites)} site(s)</b> in backup:\n\n"
        f"<code>{preview}</code>\n\n"
        "<b>Merge</b> = add to existing sites\n"
        "<b>Replace</b> = wipe existing sites first",
        reply_markup=kb,
        parse_mode="HTML",
    )
    return ConversationHandler.END


@owner_only
async def cb_restore_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles merge/replace confirmation."""
    q = update.callback_query
    await q.answer()
    mode  = q.data  # "restore_merge" or "restore_replace"
    sites = ctx.user_data.pop("restore_sites", None)

    if not sites:
        await q.edit_message_text("⚠️ Session expired. Please start the restore again.")
        return

    if mode == "restore_replace":
        for s in db.list_sites():
            db.delete_site(s["id"])

    added = 0
    for site in sites:
        # Skip if exact same name+url already exists (merge mode)
        if mode == "restore_merge":
            existing = db.list_sites()
            if any(e["name"] == site["name"] and e["url"] == site["url"] for e in existing):
                continue
        db.add_site(site, last_verify=None)
        added += 1

    action = "Replaced all and restored" if mode == "restore_replace" else "Merged"
    await q.edit_message_text(
        f"✅ {action} <b>{added}</b> site(s) successfully!\n\n"
        "Use /list to see your sites or /run to test check-ins.",
        parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
    db.init()

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Conversation: Add site ──────────────────────────────────────────
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("add",     cmd_start),
            CommandHandler("restore", cmd_restore),
            CallbackQueryHandler(cb_menu, pattern="^menu_(add|restore)$"),
        ],
        states={
            ASK_AUTH_TYPE: [
                CallbackQueryHandler(cb_auth_type, pattern="^auth_(cookie|password)$"),
                CallbackQueryHandler(cb_menu,      pattern="^menu_cancel$"),
            ],
            ASK_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_url)],
            ASK_URL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_credentials)],
            ASK_COOKIE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_api_user)],
            ASK_API_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_cookie_site)],
            ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_password_site)],
            AWAIT_RESTORE_FILE: [
                MessageHandler(filters.Document.ALL, handle_restore_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_restore_file),
            ],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
        per_message=False,
    )

    app.add_handler(conv)

    # ── Other handlers ──────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("menu",    cmd_menu))
    app.add_handler(CommandHandler("run",     cmd_run))
    app.add_handler(CommandHandler("list",    cmd_list))
    app.add_handler(CommandHandler("verify",  cmd_verify))
    app.add_handler(CommandHandler("backup",  cmd_backup))
    app.add_handler(CommandHandler("restore", cmd_restore))
    app.add_handler(CallbackQueryHandler(cb_menu,           pattern="^menu_(list|run|remove|verify|backup|restore|back|cancel)$"))
    app.add_handler(CallbackQueryHandler(cb_remove,         pattern="^remove_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_force_save,     pattern="^force_save:"))
    app.add_handler(CallbackQueryHandler(cb_restore_confirm,pattern="^restore_(merge|replace)$"))

    # ── Scheduler: 11:30 IST daily ─────────────────────────────────────
    scheduler = AsyncIOScheduler(timezone=IST)
    scheduler.add_job(
        scheduled_checkin,
        trigger="cron",
        hour=11, minute=30,
        args=[app],
    )
    scheduler.start()
    log.info("Scheduler started — daily check-in at 11:30 IST")

    log.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
