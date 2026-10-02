import os
import zipfile
import shutil
import tempfile
import json
from datetime import datetime
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
from i18n import tr, lang_from_update, get_env_i18n
from task_engine import sanitize_filename_part, send_task_end
BACK_BUTTON_EMOJI_ID = "5877629862306385808"
CONFIRM_BUTTON_EMOJI_ID = "5839200986022812209"

user_merge_sessions = {}

def create_back_button(lang="zh"):
    return InlineKeyboardButton(
        tr("back_to_main", lang),
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

def safe_extract(zip_ref, target_dir):
    for member in zip_ref.infolist():
        member_path = os.path.normpath(member.filename)
        if member_path.startswith(('..', '/', '\\')):
            raise Exception(f"非法路径: {member.filename}")
        zip_ref.extract(member, target_dir)

async def show_merge_packs(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, user_states: dict):
    query = update.callback_query
    user_id = str(query.from_user.id)
    lang = lang_from_update(update)

    keyboard = [[create_back_button(lang)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=get_env_i18n("MERGE_PACKS_BACK", lang) or text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    
    user_states[user_id] = "waiting_merge_packs"
    user_merge_sessions[user_id] = {
        "files": [],
        "bases": [],
        "messages": []
    }

async def handle_merge_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    lang = lang_from_update(update)

    if not document.file_name.endswith('.zip'):
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("err.zip_required", lang),
            parse_mode=ParseMode.HTML
        )
        return
    
    if user_id not in user_merge_sessions:
        user_merge_sessions[user_id] = {
            "files": [],
            "bases": [],
            "messages": []
        }
    
    session = user_merge_sessions[user_id]
    
    for msg_id in session["messages"]:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
        except:
            pass
    session["messages"] = []
    
    file = await context.bot.get_file(document.file_id)
    zip_path = f"downloads/merge_{user_id}_{len(session['files'])}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    os.makedirs("downloads", exist_ok=True)
    await file.download_to_drive(zip_path)
    
    session["files"].append(zip_path)
    session.setdefault("bases", []).append(os.path.splitext(document.file_name)[0])
    
    confirm_button = InlineKeyboardButton(
        tr("merge.confirm", lang),
        callback_data="confirm_merge"
    ).to_dict() | {"icon_custom_emoji_id": CONFIRM_BUTTON_EMOJI_ID}

    confirm_keyboard = [[confirm_button, create_back_button(lang)]]
    confirm_msg = await update.message.reply_text(
        f"<tg-emoji emoji-id='5920052658743283381'>📦</tg-emoji> {tr('merge.received', lang)}{len(session['files'])} {tr('merge.pending', lang)}\n"
        f"{tr('shaihuo.found_accounts', lang)} <b>{len(session['files'])}</b> {tr('merge.pending', lang)}\n\n"
        "<tg-emoji emoji-id='5954175920506933873'>📦</tg-emoji>" + tr("merge.click_confirm", lang),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(confirm_keyboard)
    )
    session["messages"].append(confirm_msg.message_id)

async def confirm_merge(update: Update, context: ContextTypes.DEFAULT_TYPE, user_states: dict):
    query = update.callback_query
    user_id = str(query.from_user.id)
    lang = lang_from_update(update)
    await query.answer()

    if user_id not in user_merge_sessions or not user_merge_sessions[user_id]["files"]:
        await query.edit_message_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("merge.no_files", lang),
            parse_mode=ParseMode.HTML
        )
        return

    session = user_merge_sessions[user_id]
    zip_files = session["files"].copy()
    zip_bases = session.get("bases", [])

    await query.edit_message_text(
        "<tg-emoji emoji-id='5443127283898405358'>⚙️</tg-emoji> " + tr("merge.merging", lang),
        parse_mode=ParseMode.HTML
    )
    
    try:
        await process_merge(update, context, user_id, zip_files, zip_bases)
    finally:
        for zip_path in zip_files:
            try:
                os.remove(zip_path)
            except:
                pass
        user_merge_sessions.pop(user_id, None)
        user_states.pop(user_id, None)

async def process_merge(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str, zip_files: list, zip_bases: list = None):
    lang = lang_from_update(update)
    zip_base = (zip_bases[0] if zip_bases else None) or f"merged_{user_id}"
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        for zip_path in zip_files:
            try:
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    safe_extract(zip_ref, extract_dir)
            except Exception as e:
                logger.error(f"解压失败 {zip_path}: {e}")
        
        session_files = []
        json_files = {}
        
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    session_path = os.path.join(root, file)
                    session_files.append(session_path)
                    base_name = os.path.splitext(file)[0]
                    
                    json_path = os.path.join(root, f"{base_name}.json")
                    if os.path.exists(json_path):
                        json_files[base_name] = json_path
        
        if not session_files:
            keyboard = [[create_back_button(lang)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.effective_chat.send_message(
                "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("merge.no_session", lang),
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
            return
        
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        
        session_map = {}
        for session_path in session_files:
            base_name = os.path.splitext(os.path.basename(session_path))[0]
            session_map[base_name] = session_path
        
        for base_name, session_path in session_map.items():
            shutil.copy2(session_path, os.path.join(output_dir, os.path.basename(session_path)))
            
            if base_name in json_files:
                shutil.copy2(json_files[base_name], os.path.join(output_dir, os.path.basename(json_files[base_name])))
        
        output_zip = os.path.join(temp_dir, "merged.zip")
        with zipfile.ZipFile(output_zip, 'w') as zipf:
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, output_dir)
                    zipf.write(file_path, arcname)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        caption = f"<b><tg-emoji emoji-id='5877307202888273539'>📦</tg-emoji> {tr('merge.done', lang)}</b>\n\n{tr('merge.total_accounts', lang)}: <b>{len(session_files)}</b>"
        out_name = f"{sanitize_filename_part(zip_base)}_{len(session_map)}_成功.zip"
        
        with open(output_zip, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=out_name,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
        
        admins = os.getenv("ADMIN_ID", "").split(",")
        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue
            try:
                with open(output_zip, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=admin_id,
                        document=f,
                        filename=f"merged_{user_id}_{timestamp}.zip",
                        caption=f"<tg-emoji emoji-id='5877307202888273539'>📦</tg-emoji>{tr('admin.user', lang)} {user_id} {tr('merge.done', lang)} - {len(session_files)} {tr('shaihuo.accounts', lang)}",
                        parse_mode=ParseMode.HTML
                    )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")

        await send_task_end(context, update.effective_chat.id)
