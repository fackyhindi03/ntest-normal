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

# Inject base URL into the scraper
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
# 3) In-memory caches per chat
# ——————————————————————————————————————————————————————————————
search_cache = {}    # chat_id → [ (title, slug), … ]
episode_cache = {}   # chat_id → [ (ep_num, episode_id), … ]

# ——————————————————————————————————————————————————————————————
# 4) /start handler
# ——————————————————————————————————————————————————————————————
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Hello! Use `/search <anime name>` to look up shows on hianimez.\n"
        "Then pick an episode and I’ll send you a .strm file named <Anime> E<Num>.strm\n"
        "– open it in VLC or pass it to your downloader.",
        parse_mode="MarkdownV2"
    )

# ——————————————————————————————————————————————————————————————
# 5) /search handler
# ——————————————————————————————————————————————————————————————
def search_command(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("Usage: `/search Naruto`", parse_mode="MarkdownV2")
        return

    chat_id = update.effective_chat.id
    query = " ".join(context.args).strip()
    msg = update.message.reply_text(
        f"🔍 Searching for *{query}*…", parse_mode="MarkdownV2"
    )

    try:
        results = search_anime(query)
    except Exception:
        logger.exception("Search error")
        msg.edit_text("❌ Search failed. Try again later.")
        return

    if not results:
        msg.edit_text(f"No results for *{query}*.", parse_mode="MarkdownV2")
        return

    search_cache[chat_id] = [(title, slug) for title, _, slug in results]
    buttons = [
        [InlineKeyboardButton(title, callback_data=f"anime_idx:{i}")]
        for i, (title, _) in enumerate(search_cache[chat_id])
    ]
    msg.edit_text("Select the anime:", reply_markup=InlineKeyboardMarkup(buttons))

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

    context.user_data['anime_title'] = title
    query.edit_message_text(
        f"🔍 Fetching episodes for *{title}*…", parse_mode="MarkdownV2"
    )

    try:
        episodes = get_episodes_list(f"{ANIWATCH_API_BASE}/watch/{slug}")
    except Exception:
        logger.exception("Episode fetch error")
        query.edit_message_text("❌ Could not fetch episodes.")
        return

    if not episodes:
        query.edit_message_text("No episodes found.")
        return

    episode_cache[chat_id] = episodes
    buttons = [
        [InlineKeyboardButton(f"Episode {ep_num}", callback_data=f"episode_idx:{i}")]
        for i, (ep_num, _) in enumerate(episodes)
    ]
    buttons.append([InlineKeyboardButton("Download All", callback_data="episode_all")])

    query.edit_message_text(
        "Select an episode (or Download All):",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# —─────────────────────────────────────────────────────────────────────────────
# 7a) episode_idx callback
# —─────────────────────────────────────────────────────────────────────────────
def episode_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat.id
    original_msg_id = query.message.message_id

    try:
        idx = int(query.data.split(":", 1)[1])
        ep_num, ep_id = episode_cache[chat_id][idx]
    except Exception:
        query.edit_message_text("❌ Invalid episode selection.")
        return

    anime_title = context.user_data.get('anime_title', 'Unknown')

    # 1) Send details block in HTML
    context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🔰 <b>Details Of Anime</b> 🔰\n\n"
            f"🎬 <b>Name:</b> {anime_title}\n"
            f"🔢 <b>Episode:</b> {ep_num}"
        ),
        parse_mode="HTML"
    )

    # 2) Create .strm file named "<Anime> E<Num>.strm"
    os.makedirs("strm_files", exist_ok=True)
    strm_filename = f"{anime_title} E{ep_num}.strm"
    strm_path = os.path.join("strm_files", strm_filename)

    stream_url, sub_url = extract_episode_stream_and_subtitle(ep_id)
    with open(strm_path, "w") as f:
        f.write(stream_url)

    # Send the .strm file
    with open(strm_path, "rb") as f:
        context.bot.send_document(
            chat_id=chat_id,
            document=InputFile(f, filename=strm_filename),
            caption="Open this file in VLC or pass it to your downloader"
        )
    os.remove(strm_path)

    # 3) Download & send subtitle if available
    if sub_url:
        try:
            local_vtt = download_and_rename_subtitle(sub_url, ep_num, cache_dir="subtitles_cache")
            with open(local_vtt, "rb") as f:
                context.bot.send_document(
                    chat_id=chat_id,
                    document=InputFile(f, filename=os.path.basename(local_vtt)),
                    caption=f"Subtitle for Episode {ep_num}"
                )
            os.remove(local_vtt)
        except Exception:
            logger.exception("Subtitle download error")
            context.bot.send_message(chat_id, f"⚠️ Failed to download subtitle for Episode {ep_num}.")

    # Delete the episode-listing message
    context.bot.delete_message(chat_id, original_msg_id)

# —─────────────────────────────────────────────────────────────────────────────
# 7b) Download All callback
# —─────────────────────────────────────────────────────────────────────────────
def episodes_all_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat.id
    original_msg_id = query.message.message_id
    eps = episode_cache.get(chat_id, [])

    if not eps:
        query.edit_message_text("❌ Nothing to download.")
        return

    anime_title = context.user_data.get('anime_title', 'Unknown')

    # Details block for all
    context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🔰 <b>Details Of Anime</b> 🔰\n\n"
            f"🎬 <b>Name:</b> {anime_title}\n"
            "🔢 <b>Episode:</b> All"
        ),
        parse_mode="HTML"
    )

    # Notify start
    query.edit_message_text("🔄 Preparing .strm files for all episodes…")

    os.makedirs("strm_files", exist_ok=True)
    for ep_num, ep_id in eps:
        try:
            stream_url, sub_url = extract_episode_stream_and_subtitle(ep_id)
        except Exception:
            logger.exception("Bulk extract error")
            context.bot.send_message(chat_id, f"❌ Ep {ep_num} failed. Skipping.")
            continue

        strm_filename = f"{anime_title} E{ep_num}.strm"
        strm_path = os.path.join("strm_files", strm_filename)
        with open(strm_path, "w") as f:
            f.write(stream_url)

        with open(strm_path, "rb") as f:
            context.bot.send_document(
                chat_id=chat_id,
                document=InputFile(f, filename=strm_filename),
                caption=f"Stream file for Episode {ep_num}"
            )
        os.remove(strm_path)

        # Subtitle
        if sub_url:
            try:
                local_vtt = download_and_rename_subtitle(sub_url, ep_num, cache_dir="subtitles_cache")
                with open(local_vtt, "rb") as f:
                    context.bot.send_document(
                        chat_id=chat_id,
                        document=InputFile(f, filename=os.path.basename(local_vtt)),
                        caption=f"Subtitle for Episode {ep_num}"
                    )
                os.remove(local_vtt)
            except Exception:
                logger.exception("Bulk subtitle error")
                context.bot.send_message(chat_id, f"⚠️ Failed to download subtitle Ep {ep_num}.")

    # Delete the episode-listing message
    context.bot.delete_message(chat_id, original_msg_id)

# —─────────────────────────────────────────────────────────────────────────────
# 8) Error handler
# —─────────────────────────────────────────────────────────────────────────────
def error_handler(update: object, context: CallbackContext):
    logger.error("Update caused error", exc_info=context.error)
    if isinstance(update, Update) and update.callback_query:
        update.callback_query.message.reply_text("⚠️ Oops, something went wrong.")

# —─────────────────────────────────────────────────────────────────────────────
# 9) Register handlers & start polling
# —─────────────────────────────────────────────────────────────────────────────
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
