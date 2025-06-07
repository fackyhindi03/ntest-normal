#!/usr/bin/env python3
# bot.py

from dotenv import load_dotenv
load_dotenv()

import os
import logging
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackQueryHandler,
    CallbackContext,
)

import hianimez_scraper
from hianimez_scraper import (
    search_anime,
    get_episodes_list,
    extract_episode_stream_and_subtitle,
)
from utils import download_and_rename_subtitle

# ——————————————————————————————————————————————————————————————
# 1) Load & validate environment
# ——————————————————————————————————————————————————————————————
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN environment variable is not set")

ANIWATCH_API_BASE = os.getenv("ANIWATCH_API_BASE")
if not ANIWATCH_API_BASE:
    raise RuntimeError("ANIWATCH_API_BASE environment variable is not set")

# Inject your base URL into the scraper module so its functions use it:
hianimez_scraper.ANIWATCH_API_BASE = ANIWATCH_API_BASE

# ——————————————————————————————————————————————————————————————
# 2) Set up logging, Updater & Dispatcher
# ——————————————————————————————————————————————————————————————
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

updater = Updater(TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher

# ——————————————————————————————————————————————————————————————
# 3) In‐memory caches per chat
# ——————————————————————————————————————————————————————————————
search_cache = {}    # chat_id → [ (title, slug), … ]
episode_cache = {}   # chat_id → [ (ep_num, episode_id), … ]

# ——————————————————————————————————————————————————————————————
# 4) /start handler
# ——————————————————————————————————————————————————————————————
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Hello! Use `/search <anime name>` to look up shows on hianimez.\n"
        "Then tap a button to pick an episode or Download All."
    )

# ——————————————————————————————————————————————————————————————
# 5) /search handler
# ——————————————————————————————————————————————————————————————
def search_command(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("Usage: `/search Naruto`", parse_mode="Markdown")
        return

    chat_id = update.effective_chat.id
    query = " ".join(context.args).strip()
    msg = update.message.reply_text(f"🔍 Searching for *{query}*…", parse_mode="Markdown")

    try:
        # now uses ANIWATCH_API_BASE internally
        results = search_anime(query)
    except Exception:
        logger.exception("Search error")
        msg.edit_text("❌ Search failed. Try again later.")
        return

    if not results:
        msg.edit_text(f"No results for *{query}*.", parse_mode="Markdown")
        return

    # Cache [(title, slug), …]
    search_cache[chat_id] = [(title, slug) for title, _, slug in results]

    buttons = [
        [InlineKeyboardButton(title, callback_data=f"anime_idx:{i}")]
        for i, (title, _) in enumerate(search_cache[chat_id])
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    msg.edit_message_text("Select the anime:", reply_markup=reply_markup)

# ——————————————————————————————————————————————————————————————
# 6) anime_idx callback
# ——————————————————————————————————————————————————————————————
def anime_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat.id

    try:
        idx = int(query.data.split(":", 1)[1])
        title, slug = search_cache[chat_id][idx]
    except Exception:
        query.edit_message_text("❌ Invalid selection.")
        return

    query.edit_message_text(f"🔍 Fetching episodes for *{title}*…", parse_mode="Markdown")

    try:
        episodes = get_episodes_list(f"{hianimez_scraper.ANIWATCH_API_BASE}/watch/{slug}")
    except Exception:
        logger.exception("Episode fetch error")
        query.edit_message_text("❌ Could not fetch episodes.")
        return

    if not episodes:
        query.edit_message_text("No episodes found.")
        return

    # Cache [(ep_num, ep_id), …]
    episode_cache[chat_id] = episodes

    buttons = [
        [InlineKeyboardButton(f"Episode {ep_num}", callback_data=f"episode_idx:{i}")]
        for i, (ep_num, _) in enumerate(episodes)
    ]
    buttons.append([InlineKeyboardButton("Download All", callback_data="episode_all")])

    query.edit_message_text(
        "Select an episode (or Download All):",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

# ——————————————————————————————————————————————————————————————
# 7a) episode_idx callback
# ——————————————————————————————————————————————————————————————
def episode_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat.id

    try:
        idx = int(query.data.split(":", 1)[1])
        ep_num, ep_id = episode_cache[chat_id][idx]
    except Exception:
        query.edit_message_text("❌ Invalid episode selection.")
        return

    query.edit_message_text(f"🔄 Preparing Episode {ep_num}…")
    try:
        hls_link, sub_url = extract_episode_stream_and_subtitle(ep_id)
    except Exception:
        logger.exception("Stream extract error")
        query.edit_message_text(f"❌ Could not extract Episode {ep_num}.")
        return

    if not hls_link:
        query.edit_message_text(f"😔 No SUB HD-2 stream for Episode {ep_num}.")
        return

    text = f"🎬 Episode {ep_num}\n\nVideo (SUB HD-2):\n{hls_link}\n"
    if not sub_url:
        query.message.reply_text(text + "\n❗ No English subtitles found.")
        return

    # download subtitle
    try:
        local_vtt = download_and_rename_subtitle(
            sub_url, ep_num, cache_dir="subtitles_cache"
        )
        text += "\n✅ Subtitle downloaded."
    except Exception:
        logger.exception("Subtitle download error")
        text += "\n⚠️ Failed to download subtitle."
        query.message.reply_text(text)
        return

    query.message.reply_text(text)
    with open(local_vtt, "rb") as f:
        context.bot.send_document(
            chat_id=chat_id,
            document=InputFile(f, filename=f"Episode {ep_num}.vtt"),
            caption=f"Subtitle for Episode {ep_num}"
        )
    os.remove(local_vtt)

# ——————————————————————————————————————————————————————————————
# 7b) Download All callback
# ——————————————————————————————————————————————————————————————
def episodes_all_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat.id
    eps = episode_cache.get(chat_id, [])

    if not eps:
        query.edit_message_text("❌ Nothing to download.")
        return

    query.edit_message_text("🔄 Downloading all episodes… this may take some time.")
    for ep_num, ep_id in eps:
        try:
            hls_link, sub_url = extract_episode_stream_and_subtitle(ep_id)
        except Exception:
            logger.exception("Bulk extract error")
            context.bot.send_message(chat_id, f"❌ Ep {ep_num} failed. Skipping.")
            continue

        if not hls_link:
            context.bot.send_message(chat_id, f"😔 Ep {ep_num}: no stream. Skipping.")
            continue

        text = f"🎬 Ep {ep_num}\n\n{hls_link}\n"
        if not sub_url:
            context.bot.send_message(chat_id, text + "\n❗ No subtitles.")
            continue

        # download subtitle
        try:
            local_vtt = download_and_rename_subtitle(
                sub_url, ep_num, cache_dir="subtitles_cache"
            )
            text += "\n✅ Subtitle downloaded."
        except Exception:
            logger.exception("Bulk subtitle error")
            context.bot.send_message(chat_id, text + "\n⚠️ Subtitle download failed.")
            continue

        context.bot.send_message(chat_id, text)
        with open(local_vtt, "rb") as f:
            context.bot.send_document(
                chat_id=chat_id,
                document=InputFile(f, filename=f"Episode {ep_num}.vtt"),
                caption=f"Subtitle for Episode {ep_num}"
            )
        os.remove(local_vtt)

# ——————————————————————————————————————————————————————————————
# 8) Error handler
# ——————————————————————————————————————————————————————————————
def error_handler(update: object, context: CallbackContext):
    logger.error("Update caused error", exc_info=context.error)
    if isinstance(update, Update) and update.callback_query:
        update.callback_query.message.reply_text("⚠️ Oops, something went wrong.")

# ——————————————————————————————————————————————————————————————
# 9) Register handlers & start polling
# ——————————————————————————————————————————————————————————————
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("search", search_command))
dispatcher.add_handler(CallbackQueryHandler(anime_callback,   pattern=r"^anime_idx:"))
dispatcher.add_handler(CallbackQueryHandler(episode_callback, pattern=r"^episode_idx:"))
dispatcher.add_handler(CallbackQueryHandler(episodes_all_callback,
                                           pattern=r"^episode_all$"))
dispatcher.add_error_handler(error_handler)

if __name__ == "__main__":
    logger.info("🔄 Bot started with long-polling")
    updater.start_polling()
    updater.idle()
