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
from utils import (
    download_and_rename_video,
    download_and_rename_subtitle,
)

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
# 3) In‐memory caches per chat
# ——————————————————————————————————————————————————————————————
search_cache = {}    # chat_id → [ (title, slug), … ]
episode_cache = {}   # chat_id → [ (ep_num, episode_id), … ]

# ——————————————————————————————————————————————————————————————
# 4) /start handler
# ——————————————————————————————————————————————————————————————
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
        "3️⃣ Choose an episode to get link\\(or tap \\\"Download All\\\"\\)\n"
        "4️⃣ Receive the high\\-quality download link \\+ subtitles automatically\n\n"
        "📩 *Contact @THe\\_vK\\_3 if any problem or Query* ",
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
    reply_markup = InlineKeyboardMarkup(buttons)
    msg.edit_text("Select the anime:", reply_markup=reply_markup)

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

    # 1) Send details block
    header = "🔰 *Details Of Anime* 🔰"
    details = f"🎬 *Name:* {anime_title}\n🔢 *Episode:* {ep_num}"
    details_msg = query.message.reply_text(
        f"{header}\n\n{details}",
        parse_mode="MarkdownV2"
    )

    # 2) Download video
    try:
        video_path = download_and_rename_video(
            extract_episode_stream_and_subtitle(ep_id)[0],
            anime_title, ep_num,
            cache_dir="video_cache"
        )
        with open(video_path, "rb") as vf:
            context.bot.send_video(
                chat_id=chat_id,
                video=InputFile(vf, filename=os.path.basename(video_path)),
                supports_streaming=True
            )
        os.remove(video_path)
    except Exception:
        logger.exception("Video download error")
        context.bot.send_message(chat_id, f"⚠️ Failed to download video for Episode {ep_num}.")
        context.bot.delete_message(chat_id, original_msg_id)
        return

    # 3) Download subtitle
    try:
        sub_url = extract_episode_stream_and_subtitle(ep_id)[1]
        local_vtt = download_and_rename_subtitle(
            sub_url, ep_num, cache_dir="subtitles_cache"
        )
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

    # Delete the episode list message
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
    header = "🔰 *Details Of Anime* 🔰"
    details = f"🎬 *Name:* {anime_title}\n🔢 *Episode:* All"
    details_msg = query.message.reply_text(
        f"{header}\n\n{details}",
        parse_mode="MarkdownV2"
    )

    query.edit_message_text(
        "🔄 Downloading all episodes… this may take some time."
    )
    for ep_num, ep_id in eps:
        try:
            hls_link, sub_url = extract_episode_stream_and_subtitle(ep_id)
            video_path = download_and_rename_video(
                hls_link, anime_title, ep_num,
                cache_dir="video_cache"
            )
            with open(video_path, "rb") as vf:
                context.bot.send_video(
                    chat_id=chat_id,
                    video=InputFile(vf, filename=os.path.basename(video_path)),
                    supports_streaming=True
                )
            os.remove(video_path)
        except Exception:
            logger.exception("Bulk video download error")
            context.bot.send_message(chat_id, f"⚠️ Failed to download video Ep {ep_num}.")
            continue

        try:
            local_vtt = download_and_rename_subtitle(
                sub_url, ep_num, cache_dir="subtitles_cache"
            )
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

    # Delete the episode list message after bulk
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
dispatcher.add_handler(CallbackQueryHandler(anime_callback,   pattern=r"^anime_idx:"))
dispatcher.add_handler(CallbackQueryHandler(episode_callback, pattern=r"^episode_idx:"))
dispatcher.add_handler(CallbackQueryHandler(episodes_all_callback, pattern=r"^episode_all$"))
dispatcher.add_error_handler(error_handler)

if __name__ == "__main__":
    logger.info("🔄 Bot started with long-polling")
    updater.start_polling()
    updater.idle()
