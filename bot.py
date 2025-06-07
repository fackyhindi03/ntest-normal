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
import CommandHandler, CallbackQueryHandler, CallbackContext
from telegram.ext import Updater

from hianimez_scraper import (
    search_anime,
    get_episodes_list,
    extract_episode_stream_and_subtitle,
)
from utils import download_and_rename_subtitle

# ——————————————————————————————————————————————————————————————
# 1) Load environment variables
# ——————————————————————————————————————————————————————————————
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN environment variable is not set")

KOYEB_APP_URL = os.getenv("KOYEB_APP_URL")
if not KOYEB_APP_URL:
    raise RuntimeError(
        "KOYEB_APP_URL environment variable is not set. It must be your bot’s public HTTPS URL (no trailing slash)."
    )

ANIWATCH_API_BASE = os.getenv("ANIWATCH_API_BASE")
if not ANIWATCH_API_BASE:
    raise RuntimeError(
        "ANIWATCH_API_BASE environment variable is not set. It should be your AniWatch API URL."
    )

# ——————————————————————————————————————————————————————————————
# 2) Initialize Bot + Dispatcher
# ——————————————————————————————————————————————————————————————
updater = Updater(TOKEN, use_context=True)
dispatcher = updater.dispatcher

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ——————————————————————————————————————————————————————————————
# 3) In‐memory caches for search results & episode lists per chat
# ——————————————————————————————————————————————————————————————
search_cache = {}   # chat_id → [ (title, slug), … ]
episode_cache = {}  # chat_id → [ (ep_num, episode_id), … ]


# ——————————————————————————————————————————————————————————————
# 4) /start handler
# ——————————————————————————————————————————————————————————————
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Hello! I can help you search for anime on hianimez.to and\n"
        " extract the SUB-HD2 Video HLS link + English subtitles.\n\n"
        "Use /search <anime name> to begin."
    )


# ——————————————————————————————————————————————————————————————
# 5) /search handler
# ——————————————————————————————————————————————————————————————
def search_command(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        update.message.reply_text("Please provide an anime name. Example: /search Naruto")
        return

    chat_id = update.effective_chat.id
    query = " ".join(context.args).strip()
    msg = update.message.reply_text(f"🔍 Searching for \"{query}\"…")

    try:
        results = search_anime(query)
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        msg.edit_text("❌ Search error; please try again later.")
        return

    if not results:
        msg.edit_text(f"No anime found matching \"{query}\".")
        return

    # Store (title, slug) in search_cache[chat_id]
    search_cache[chat_id] = [(title, slug) for title, anime_url, slug in results]

    buttons = []
    for idx, (title, slug) in enumerate(search_cache[chat_id]):
        buttons.append([InlineKeyboardButton(title, callback_data=f"anime_idx:{idx}")])

    reply_markup = InlineKeyboardMarkup(buttons)
    msg.edit_text("Select the anime you want:", reply_markup=reply_markup)


# ——————————————————————————————————————————————————————————————
# 6) Callback when user taps an anime button (anime_idx)
# ——————————————————————————————————————————————————————————————
def anime_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    chat_id = query.message.chat.id
    data = query.data  # e.g. "anime_idx:3"
    try:
        _, idx_str = data.split(":", maxsplit=1)
        idx = int(idx_str)
    except Exception:
        query.edit_message_text("❌ Internal error: invalid anime selection.")
        return

    anime_list = search_cache.get(chat_id, [])
    if idx < 0 or idx >= len(anime_list):
        query.edit_message_text("❌ Internal error: anime index out of range.")
        return

    title, slug = anime_list[idx]
    anime_url = f"https://hianimez.to/watch/{slug}"

    msg = query.edit_message_text(
        f"🔍 Fetching episodes for *{title}*…", parse_mode="MarkdownV2"
    )

    try:
        episodes = get_episodes_list(anime_url)
    except Exception as e:
        logger.error(f"Error fetching episodes: {e}", exc_info=True)
        query.edit_message_text("❌ Failed to retrieve episodes for that anime.")
        return

    if not episodes:
        query.edit_message_text("No episodes found for that anime.")
        return

    # Store (ep_num, episode_id) in episode_cache[chat_id]
    episode_cache[chat_id] = []
    for ep_num, ep_id in episodes:
        episode_cache[chat_id].append((ep_num, ep_id))

    # Build buttons for each episode
    buttons = []
    for i, (ep_num, ep_id) in enumerate(episode_cache[chat_id]):
        buttons.append([InlineKeyboardButton(f"Episode {ep_num}", callback_data=f"episode_idx:{i}")])

    # Add one final row for "Download All"
    buttons.append([InlineKeyboardButton("Download All", callback_data="episode_all")])

    reply_markup = InlineKeyboardMarkup(buttons)
    query.edit_message_text("Select an episode (or download all):", reply_markup=reply_markup)


# ──────────────────────────────────────────────────────────────────────────────
# 7a) Callback when user taps a single episode button (episode_idx)
# ──────────────────────────────────────────────────────────────────────────────
def episode_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    chat_id = query.message.chat.id
    data = query.data  # e.g. "episode_idx:5"
    try:
        _, idx_str = data.split(":", maxsplit=1)
        idx = int(idx_str)
    except Exception:
        query.edit_message_text("❌ Internal error: invalid episode selection.")
        return

    ep_list = episode_cache.get(chat_id, [])
    if idx < 0 or idx >= len(ep_list):
        query.edit_message_text("❌ Internal error: episode index out of range.")
        return

    ep_num, episode_id = ep_list[idx]
    # Let the user know we are working on it:
    msg = query.edit_message_text(
        f"🔄 Retrieving SUB HD-2 Video link and English subtitle for Episode {ep_num}..."
    )

    try:
        hls_link, subtitle_url = extract_episode_stream_and_subtitle(episode_id)
    except Exception as e:
        logger.error(f"Error extracting episode data: {e}", exc_info=True)
        query.edit_message_text(f"❌ Failed to extract data for Episode {ep_num}.")
        return

    # If we couldn’t find any HLS URL, bail out:
    if not hls_link:
        query.edit_message_text(f"😔 Could not find a SUB HD-2 Video stream for Episode {ep_num}.")
        return

    # Build a plain-text response (no MarkdownV2 anywhere)
    text = (
        f"🎬 Episode {ep_num}\n\n"
        f"Video (SUB HD-2) HLS Link:\n"
        f"{hls_link}\n\n"
    )

    # If no English .vtt was found, just send the text so far:
    if not subtitle_url:
        text += "❗ No English subtitle (.vtt) found."
        query.message.reply_text(text)
        return

    # Otherwise, attempt to download & rename the .vtt locally
    try:
        local_vtt = download_and_rename_subtitle(subtitle_url, ep_num, cache_dir="subtitles_cache")
    except Exception as e:
        logger.error(f"Error downloading/renaming subtitle: {e}", exc_info=True)
        text += "⚠️ Found a subtitle URL, but failed to download it."
        query.message.reply_text(text)
        return

    # Indicate that subtitle was downloaded
    text += f"✅ English subtitle downloaded as \"Episode {ep_num}.vtt\"."
    query.message.reply_text(text)

    # Send the .vtt file
    with open(local_vtt, "rb") as f:
        query.message.reply_document(
            document=InputFile(f, filename=f"Episode {ep_num}.vtt"),
            caption=f"Here is the subtitle for Episode {ep_num}.",
        )

    # Clean up the local .vtt
    try:
        os.remove(local_vtt)
    except OSError:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# 7b) Callback when user taps "Download All" button (episode_all)
# ──────────────────────────────────────────────────────────────────────────────
def episodes_all_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    chat_id = query.message.chat.id

    ep_list = episode_cache.get(chat_id, [])
    if not ep_list:
        query.edit_message_text("❌ No episodes available to download.")
        return

    # Let the user know we are starting the bulk download
    query.edit_message_text("🔄 Downloading all episodes (SUB HD-2 Video + English subs)… This may take a while.")

    # Iterate through each episode, send its link + subtitle
    for ep_num, episode_id in ep_list:
        # Retrieve HLS link + subtitle
        try:
            hls_link, subtitle_url = extract_episode_stream_and_subtitle(episode_id)
        except Exception as e:
            logger.error(f"Error extracting episode {ep_num}: {e}", exc_info=True)
            bot.send_message(chat_id, f"❌ Failed to extract data for Episode {ep_num}. Skipping.")
            continue

        # If no HLS link, skip with message
        if not hls_link:
            bot.send_message(chat_id, f"😔 Episode {ep_num}: No SUB HD-2 Video stream found. Skipping.")
            continue

        # Send the episode HLS link + (placeholder for subtitle)
        text = (
            f"🎬 Episode {ep_num}\n\n"
            f"Video (SUB HD-2) HLS Link:\n"
            f"{hls_link}\n\n"
        )

        # If no subtitle, simply send text and continue
        if not subtitle_url:
            text += "❗ No English subtitle (.vtt) found."
            bot.send_message(chat_id, text)
            continue

        # Otherwise, download & rename the .vtt locally
        try:
            local_vtt = download_and_rename_subtitle(subtitle_url, ep_num, cache_dir="subtitles_cache")
        except Exception as e:
            logger.error(f"Error downloading subtitle for Episode {ep_num}: {e}", exc_info=True)
            text += "⚠️ Found a subtitle URL, but failed to download it."
            bot.send_message(chat_id, text)
            continue

        # Indicate subtitle downloaded
        text += f"✅ English subtitle downloaded as \"Episode {ep_num}.vtt\"."
        bot.send_message(chat_id, text)

        # Send the .vtt file
        try:
            with open(local_vtt, "rb") as f:
                bot.send_document(
                    chat_id=chat_id,
                    document=InputFile(f, filename=f"Episode {ep_num}.vtt"),
                    caption=f"Subtitle for Episode {ep_num}"
                )
        except Exception as e:
            logger.error(f"Error sending subtitle for Episode {ep_num}: {e}", exc_info=True)
            bot.send_message(chat_id, f"⚠️ Could not send subtitle file for Episode {ep_num}.")
        finally:
            try:
                os.remove(local_vtt)
            except OSError:
                pass


# ——————————————————————————————————————————————————————————————
# 8) Error handler
# ——————————————————————————————————————————————————————————————
def error_handler(update: object, context: CallbackContext):
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.callback_query:
        update.callback_query.message.reply_text("⚠️ Oops, something went wrong.")




# ——————————————————————————————————————————————————————————————
# 9) Flask app for webhook + health check
# ——————————————————————————————————————————————————————————————
def main():
    # register all your existing handlers exactly as before
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("search", search_command))
    dispatcher.add_handler(CallbackQueryHandler(anime_callback,   pattern=r"^anime_idx:"))
    dispatcher.add_handler(CallbackQueryHandler(episode_callback, pattern=r"^episode_idx:"))
    dispatcher.add_handler(CallbackQueryHandler(episodes_all_callback,
                                               pattern=r"^episode_all$"))
    dispatcher.add_error_handler(error_handler)

    logger.info("🔄 Starting long-polling…")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
