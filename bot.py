#!/usr/bin/env python3
# bot.py

from dotenv import load_dotenv
load_dotenv()

import os
import logging
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.utils.helpers import escape_markdown
from telegram.error import BadRequest
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext

import hianimez_scraper
from hianimez_scraper import search_anime, get_episodes_list, extract_episode_stream_and_subtitle
from utils import download_and_rename_subtitle

# Load and validate environment
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANIWATCH_API_BASE = os.getenv("ANIWATCH_API_BASE")
if not TELEGRAM_TOKEN or not ANIWATCH_API_BASE:
    raise RuntimeError("Required environment variables are not set")
hianimez_scraper.ANIWATCH_API_BASE = ANIWATCH_API_BASE

# Authorized users
AUTHORIZED_USERS = {1423807625, 5476335536, 2096201372, 633599652}

def restricted(func):
    @wraps(func)
    def wrapped(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in AUTHORIZED_USERS:
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "🚫 <b>Access Denied!</b>\n"
                    "You are not authorized to use this bot."
                    "📩 Contact @THe_vK_3 for access!"
                ),
                parse_mode="HTML"
            )
            return
        return func(update, context, *args, **kwargs)
    return wrapped

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot setup
updater = Updater(TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher

# In-memory caches
search_cache = {}    # chat_id → [(title, slug), ...]
episode_cache = {}   # chat_id → [(ep_num, ep_id), ...]

@restricted
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "🌸 *Hianime Downloader* 🌸\n\n"
        "🔍 *Find \\& Download Anime Episodes Directly*\n\n"
        "🎯 *What I Can Do:*\n"
        "• Search for your favorite anime on hianimez\\.to\n"
        "• Give that direct m3u8 link\n"
        "• Include English subtitles \\(SRT/VTT\\)\n"
        "• Send everything as a document \\(no quality loss\\)\n\n"
        "📝 *How to Use:*\n"
        "1️⃣ `/search <anime name>` \\- Find anime titles\n"
        "2️⃣ Select the anime from the list of results\n"
        "3️⃣ Choose an episode to get link\\(or tap \"Download All\"\\)\n"
        "4️⃣ Receive the high\\-quality download link \\+ subtitles automatically\n\n"
        "📩 *Contact @THe\\_vK\\_3 if any problem or Query*",
        parse_mode="MarkdownV2"
    )

@restricted
def search_command(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text(
            "Usage: `/search Naruto`", parse_mode="MarkdownV2"
        )
        return

    chat_id = update.effective_chat.id
    query = " ".join(context.args).strip()
    safe_q = escape_markdown(query, version=2)
    msg = update.message.reply_text(
        f"🔍 Searching for *{safe_q}*…", parse_mode="MarkdownV2"
    )
    try:
        results = search_anime(query)
    except Exception:
        logger.exception("Search error")
        msg.edit_text("❌ Search failed. Try again later.")
        return

    if not results:
        msg.edit_text(f"No results for *{safe_q}*.", parse_mode="MarkdownV2")
        return

    search_cache[chat_id] = [(title, slug) for title, _, slug in results]
    buttons = [
        [InlineKeyboardButton(title, callback_data=f"anime_idx:{i}")]
        for i, (title, _) in enumerate(search_cache[chat_id])
    ]
    msg.edit_text("Select the anime:", reply_markup=InlineKeyboardMarkup(buttons))

@restricted
def anime_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    try:
        query.answer()
    except BadRequest:
        pass

    chat_id = query.message.chat.id
    idx = int(query.data.split(":", 1)[1])
    title, slug = search_cache[chat_id][idx]
    context.user_data['anime_title'] = title
    safe_t = escape_markdown(title, version=2)
    query.edit_message_text(
        f"🔍 Fetching episodes for *{safe_t}*…", parse_mode="MarkdownV2"
    )

    episodes = get_episodes_list(f"{ANIWATCH_API_BASE}/watch/{slug}")
    episode_cache[chat_id] = episodes

    buttons = [
        [InlineKeyboardButton(f"Episode {num}", callback_data=f"episode_idx:{i}")]
        for i, (num, _) in enumerate(episodes)
    ]
    buttons.append([InlineKeyboardButton("Download All", callback_data="episode_all")])
    query.edit_message_text(
        "Select an episode (or Download All):",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@restricted
def episode_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    try:
        query.answer()
    except BadRequest:
        pass

    chat_id = query.message.chat.id
    idx = int(query.data.split(":", 1)[1])
    ep_num, ep_id = episode_cache[chat_id][idx]
    title = context.user_data.get('anime_title', 'Unknown')

    header = "🔰 *Details Of Anime* 🔰"
    details = (
        f"🎬 *Name:* {escape_markdown(title, 2)}\n"
        f"🔢 *Episode:* {ep_num}"
    )
    query.message.reply_text(
        f"{header}\n\n{details}", parse_mode="MarkdownV2"
    )

    subtitle_dir = os.path.join("subtitles_cache", str(chat_id))
    os.makedirs(subtitle_dir, exist_ok=True)

    hls_link, sub_url = extract_episode_stream_and_subtitle(ep_id)
    context.bot.send_message(
        chat_id=chat_id,
        text=f"🔗 *HLS Link for Episode {ep_num}:*\n`{hls_link}`",
        parse_mode="MarkdownV2"
    )

    local_vtt = download_and_rename_subtitle(sub_url, ep_num, cache_dir=subtitle_dir)
    with open(local_vtt, "rb") as f:
        context.bot.send_document(
            chat_id=chat_id,
            document=InputFile(f, filename=os.path.basename(local_vtt)),
            caption=f"Subtitle for Episode {ep_num}"
        )
    os.remove(local_vtt)
    context.bot.delete_message(chat_id, query.message.message_id)

@restricted
def episodes_all_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    try:
        query.answer()
    except BadRequest:
        pass

    chat_id = query.message.chat.id
    eps = episode_cache.get(chat_id, [])
    if not eps:
        query.edit_message_text("❌ Nothing to download.")
        return

    context.bot.delete_message(chat_id, query.message.message_id)
    subtitle_dir = os.path.join("subtitles_cache", str(chat_id))
    os.makedirs(subtitle_dir, exist_ok=True)

    title = context.user_data.get('anime_title', 'Unknown')
    header = "🔰 *Details Of Anime* 🔰"
    details = (
        f"🎬 *Name:* {escape_markdown(title, 2)}\n"
        "🔢 *Episode:* All"
    )
    context.bot.send_message(
        chat_id=chat_id,
        text=f"{header}\n\n{details}",
        parse_mode="MarkdownV2"
    )

    for ep_num, ep_id in eps:
        hls_link, sub_url = extract_episode_stream_and_subtitle(ep_id)
        context.bot.send_message(
            chat_id=chat_id,
            text=f"🔗 *Episode {ep_num} HLS Link:*\n`{hls_link}`",
            parse_mode="MarkdownV2"
        )

        local_vtt = download_and_rename_subtitle(sub_url, ep_num, cache_dir=subtitle_dir)
        with open(local_vtt, "rb") as f:
            context.bot.send_document(
                chat_id=chat_id,
                document=InputFile(f, filename=os.path.basename(local_vtt)),
                caption=f"Subtitle for Episode {ep_num}"
            )
        os.remove(local_vtt)

# Error handler
def error_handler(update: object, context: CallbackContext):
    logger.error("Update caused error", exc_info=context.error)
    if update and getattr(update, 'callback_query', None):
        update.callback_query.message.reply_text("⚠️ Oops, something went wrong.")

# Register handlers
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("search", search_command))
dispatcher.add_handler(CallbackQueryHandler(anime_callback, pattern=r"^anime_idx:"))
dispatcher.add_handler(CallbackQueryHandler(episode_callback, pattern=r"^episode_idx:"))
dispatcher.add_handler(CallbackQueryHandler(episodes_all_callback, pattern=r"^episode_all$"))
dispatcher.add_error_handler(error_handler)

if __name__ == "__main__":
    logger.info("🔄 Bot started with long-polling")
    updater.start_polling()
    updater.idle()
