# Updated bot.py
#!/usr/bin/env python3

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

# 1) Load & validate environment
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN environment variable is not set")

ANIWATCH_API_BASE = os.getenv("ANIWATCH_API_BASE")
if not ANIWATCH_API_BASE:
    raise RuntimeError("ANIWATCH_API_BASE environment variable is not set")

# Inject base URL into scraper
hianimez_scraper.ANIWATCH_API_BASE = ANIWATCH_API_BASE

# 2) Logging & dispatcher
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
updater = Updater(TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher

# 3) Caches
search_cache = {}
episode_cache = {}

# 4) /start
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Hello! Use `/search <anime name>` to lookup on hianimez.\n"
        "Tap a button to pick an episode or Download All.",
        parse_mode="MarkdownV2"
    )

# 5) /search
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
        msg.edit_text("❌ Search failed.")
        return
    if not results:
        msg.edit_text(f"No results for *{query}*.", parse_mode="MarkdownV2")
        return
    search_cache[chat_id] = [(title, slug) for title, _, slug in results]
    buttons = [[InlineKeyboardButton(title, callback_data=f"anime_idx:{i}")]
               for i, (title, _) in enumerate(search_cache[chat_id])]
    msg.edit_text("Select the anime:", reply_markup=InlineKeyboardMarkup(buttons))

# 6) anime_idx
def anime_callback(update: Update, context: CallbackContext):
    query = update.callback_query; query.answer()
    chat_id = query.message.chat.id
    try:
        idx = int(query.data.split(":",1)[1])
        title, slug = search_cache[chat_id][idx]
    except Exception:
        query.edit_message_text("❌ Invalid selection.")
        return
    context.user_data['anime_title'] = title
    query.edit_message_text(f"🔍 Fetching episodes for *{title}*…", parse_mode="MarkdownV2")
    try:
        episodes = get_episodes_list(f"{ANIWATCH_API_BASE}/watch/{slug}")
    except Exception:
        logger.exception("Fetch episodes error")
        query.edit_message_text("❌ Could not fetch episodes.")
        return
    if not episodes:
        query.edit_message_text("No episodes found.")
        return
    episode_cache[chat_id] = episodes
    buttons = [[InlineKeyboardButton(f"Episode {ep_num}", callback_data=f"episode_idx:{i}")]
               for i,(ep_num,_) in enumerate(episodes)]
    buttons.append([InlineKeyboardButton("Download All", callback_data="episode_all")])
    query.edit_message_text("Select an episode (or Download All):",
                             reply_markup=InlineKeyboardMarkup(buttons))

# 7a) episode_idx
def episode_callback(update: Update, context: CallbackContext):
    query = update.callback_query; query.answer()
    chat_id = query.message.chat.id
    original_id = query.message.message_id
    try:
        idx = int(query.data.split(":",1)[1])
        ep_num, ep_id = episode_cache[chat_id][idx]
    except Exception:
        query.edit_message_text("❌ Invalid episode.")
        return
    anime_title = context.user_data.get('anime_title','Unknown')
    # details
    header="🔰 *Details Of Anime* 🔰"
    details = f"🎬 *Name:* {anime_title}\n🔢 *Episode:* {ep_num}"
    query.message.reply_text(f"{header}\n\n{details}", parse_mode="MarkdownV2")
    # video
    try:
        video_url, sub_url = extract_episode_stream_and_subtitle(ep_id)
        video_path = download_and_rename_video(video_url, anime_title, ep_num)
        with open(video_path,'rb') as vf:
            context.bot.send_video(chat_id=chat_id, video=InputFile(vf,filename=os.path.basename(video_path)),supports_streaming=True)
        os.remove(video_path)
    except Exception:
        logger.exception("Video error")
        context.bot.send_message(chat_id, f"⚠️ Failed video Ep{ep_num}.")
        context.bot.delete_message(chat_id, original_id)
        return
    # subtitle
    try:
        if sub_url:
            local_vtt=download_and_rename_subtitle(sub_url,ep_num)
            with open(local_vtt,'rb') as f:
                context.bot.send_document(chat_id=chat_id, document=InputFile(f,filename=os.path.basename(local_vtt)), caption=f"Subtitle Ep{ep_num}")
            os.remove(local_vtt)
    except Exception:
        logger.exception("Subtitle error")
        context.bot.send_message(chat_id, f"⚠️ Failed subtitle Ep{ep_num}.")
    context.bot.delete_message(chat_id,original_id)

# 7b) download all
def episodes_all_callback(update: Update, context: CallbackContext):
    query=update.callback_query; query.answer()
    chat_id=query.message.chat.id; orig=query.message.message_id
    eps=episode_cache.get(chat_id,[])
    if not eps:
        query.edit_message_text("❌ Nothing to download.")
        return
    anime_title=context.user_data.get('anime_title','Unknown')
    header="🔰 *Details Of Anime* 🔰"
    details=f"🎬 *Name:* {anime_title}\n🔢 *Episode:* All"
    query.message.reply_text(f"{header}\n\n{details}", parse_mode="MarkdownV2")
    query.edit_message_text("🔄 Downloading all episodes… please wait.")
    for ep_num,ep_id in eps:
        try:
            video_url,sub_url=extract_episode_stream_and_subtitle(ep_id)
            vpath=download_and_rename_video(video_url,anime_title,ep_num)
            with open(vpath,'rb') as vf:
                context.bot.send_video(chat_id=chat_id,video=InputFile(vf,filename=os.path.basename(vpath)),supports_streaming=True)
            os.remove(vpath)
        except Exception:
            logger.exception("Bulk video error")
            context.bot.send_message(chat_id,f"⚠️ Failed video Ep{ep_num}.")
            continue
        try:
            if sub_url:
                l=download_and_rename_subtitle(sub_url,ep_num)
                with open(l,'rb') as f:
                    context.bot.send_document(chat_id=chat_id,document=InputFile(f,filename=os.path.basename(l)),caption=f"Subtitle Ep{ep_num}")
                os.remove(l)
        except Exception:
            logger.exception("Bulk subtitle error")
            context.bot.send_message(chat_id,f"⚠️ Failed subtitle Ep{ep_num}.")
    context.bot.delete_message(chat_id, orig)

# 8) error handler
 def error_handler(update,context):
    logger.error("Error",exc_info=context.error)
    if isinstance(update,Update) and update.callback_query:
        update.callback_query.message.reply_text("⚠️ Oops.")

dispatcher.add_handler(CommandHandler("start",start))
dispatcher.add_handler(CommandHandler("search",search_command))
dispatcher.add_handler(CallbackQueryHandler(anime_callback,pattern=r"^anime_idx:"))
dispatcher.add_handler(CallbackQueryHandler(episode_callback,pattern=r"^episode_idx:"))
dispatcher.add_handler(CallbackQueryHandler(episodes_all_callback,pattern=r"^episode_all$"))
dispatcher.add_error_handler(error_handler)
if __name__=="__main__":
    logger.info("🔄 Bot started")
    updater.start_polling()
    updater.idle()
