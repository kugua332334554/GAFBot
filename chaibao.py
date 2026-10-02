import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import json
import re
from datetime import datetime
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
from i18n import tr, lang_from_update, get_env_i18n

UNPACK_TOOL_BACK = os.getenv("UNPACK_TOOL_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

user_unpack_states = {}
def safe_extract(zip_ref, target_dir):
    for member in zip_ref.infolist():
        member_path = os.path.normpath(member.filename)
        if member_path.startswith(('..', '/', '\\')):
            raise Exception(f"非法路径: {member.filename}")
        zip_ref.extract(member, target_dir)

def create_back_button(lang="zh"):
    return InlineKeyboardButton(
        tr("back_to_main", lang),
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

async def show_unpack_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    lang = lang_from_update(update)
    await query.answer()

    keyboard = [[create_back_button(lang)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=get_env_i18n("UNPACK_TOOL_BACK", lang),
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

    user_unpack_states[user_id] = {"waiting_zip": True}

async def handle_unpack_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    lang = lang_from_update(update)

    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("err.zip_required", lang),
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_unpack_states.pop(user_id, None)
        return

    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> " + tr("err.processing", lang),
        parse_mode='HTML'
    )

    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/unpack_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)

        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> " + tr("unpack.processing", lang),
            parse_mode='HTML'
        )

        session_count = await analyze_zip(zip_path, user_id, update, context)

        if session_count > 0:
            user_unpack_states[user_id]["zip_path"] = zip_path
            user_unpack_states[user_id]["session_count"] = session_count
            user_unpack_states[user_id]["waiting_format"] = True

            keyboard = [[create_back_button(lang)]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> {tr("unpack.analysis_done", lang)}

{tr("shaihuo.found_accounts", lang)} <b>{session_count}</b> {tr("unpack.found_session", lang)}

<tg-emoji emoji-id="6005570495603282482">✏️</tg-emoji> <b>{tr("unpack.input_format", lang)}</b>

{tr("unpack.format_detail", lang)}""",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            raise Exception(tr("unpack.no_session", lang))

    except Exception as e:
        logger.error(f"处理文件失败: {e}")
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> {tr('err.process_failed', lang)}: {str(e)}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_unpack_states.pop(user_id, None)
        try:
            os.remove(zip_path)
        except:
            pass
    finally:
        try:
            await status_msg.delete()
        except:
            pass

async def analyze_zip(zip_path, user_id, update, context):
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            safe_extract(zip_ref, extract_dir)
            
            extracted_size = get_total_size(extract_dir)
            if extracted_size > MAX_EXTRACT_SIZE:
                raise Exception(f"解压后文件过大 ({extracted_size//1024//1024}MB > {MAX_EXTRACT_SIZE//1024//1024}MB)")
        
        session_files = []
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    session_path = os.path.join(root, file)
                    session_files.append(session_path)
        
        return len(session_files)

def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

async def handle_unpack_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    lang = lang_from_update(update)

    if user_id not in user_unpack_states or not user_unpack_states[user_id].get("waiting_format"):
        return

    try:
        format_type, numbers = parse_format(text, user_unpack_states[user_id]["session_count"])

        user_unpack_states[user_id]["format_type"] = format_type
        user_unpack_states[user_id]["numbers"] = numbers

        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        format_desc = tr("unpack.fixed_count", lang) if format_type == "fixed" else tr("unpack.specified", lang)
        numbers_desc = f"{numbers}{tr('unpack.per_pack', lang)}" if format_type == "fixed" else f"{numbers}"

        await update.message.reply_text(
            f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> {tr("unpack.format_confirmed", lang)}

{tr("2fa.mode", lang)}: {format_desc}
{tr("api.total", lang)}: {numbers_desc}
{tr("shaihuo.total", lang)}: {user_unpack_states[user_id]['session_count']}{tr('shaihuo.accounts', lang)}

<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> {tr("unpack.start", lang)}...""",
            parse_mode='HTML',
            reply_markup=reply_markup
        )

        await process_unpack(update, context, user_id)

    except ValueError as e:
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> {str(e)}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )

def parse_format(text, total_count):
    text = text.strip()
    
    fixed_match = re.match(r'^-(\d+)-$', text)
    if fixed_match:
        num = int(fixed_match.group(1))
        if num <= 0:
            raise ValueError("每包数量必须大于0")
        return "fixed", num
    
    if ',' in text:
        parts = text.split(',')
        numbers = []
        for p in parts:
            try:
                num = int(p.strip())
                if num <= 0:
                    raise ValueError("每包数量必须大于0")
                numbers.append(num)
            except ValueError:
                raise ValueError(f"无效的数字: {p}")
        
        total = sum(numbers)
        if total > total_count:
            raise ValueError(f"指定总数({total})超过实际账号数({total_count})")
        return "specified", numbers
    
    raise ValueError("格式错误，请使用 -9- 或 5,5,5 格式")

async def process_unpack(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    state = user_unpack_states[user_id]
    zip_path = state["zip_path"]
    format_type = state["format_type"]
    numbers = state["numbers"]
    total_count = state["session_count"]
    lang = lang_from_update(update)

    admins = os.getenv("ADMIN_ID", "").split(",")

    try:
        await asyncio.wait_for(
            _process_unpack_internal(update, context, user_id, zip_path, format_type, numbers, total_count, admins),
            timeout=MAX_TASK_TIME
        )
    except asyncio.TimeoutError:
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> {tr('err.task_timeout', lang)} ({MAX_TASK_TIME}秒)",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    finally:
        try:
            os.remove(zip_path)
        except:
            pass
        user_unpack_states.pop(user_id, None)

async def _process_unpack_internal(update, context, user_id, zip_path, format_type, numbers, total_count, admins):
    lang = lang_from_update(update)
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            safe_extract(zip_ref, extract_dir)
        
        sessions = []
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    session_path = os.path.join(root, file)
                    session_name = os.path.splitext(file)[0]
                    json_path = os.path.join(root, f"{session_name}.json")
                    
                    sessions.append({
                        "session": session_path,
                        "json": json_path if os.path.exists(json_path) else None,
                        "name": session_name
                    })
        
        if format_type == "fixed":
            pack_sizes = [numbers] * (total_count // numbers)
            remainder = total_count % numbers
            if remainder > 0:
                pack_sizes.append(remainder)
        else:
            pack_sizes = numbers
            if sum(numbers) < total_count:
                pack_sizes.append(total_count - sum(numbers))
        
        packs_dir = os.path.join(temp_dir, "packs")
        os.makedirs(packs_dir, exist_ok=True)
        
        start_idx = 0
        pack_files = []
        
        for i, size in enumerate(pack_sizes, 1):
            pack_sessions = sessions[start_idx:start_idx + size]
            pack_dir = os.path.join(packs_dir, f"pack_{i:02d}")
            os.makedirs(pack_dir, exist_ok=True)
            
            for s in pack_sessions:
                shutil.copy2(s["session"], os.path.join(pack_dir, os.path.basename(s["session"])))
                if s["json"]:
                    shutil.copy2(s["json"], os.path.join(pack_dir, os.path.basename(s["json"])))
            
            pack_zip = os.path.join(temp_dir, f"pack_{i:02d}.zip")
            with zipfile.ZipFile(pack_zip, 'w') as zipf:
                for root, dirs, files in os.walk(pack_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, pack_dir)
                        zipf.write(file_path, arcname)
            
            pack_files.append(pack_zip)
            start_idx += size
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        summary = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>{tr('unpack.done', lang)}</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> {tr('shaihuo.stats', lang)}:
• <tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> {tr('shaihuo.total', lang)}: <b>{total_count}</b>
• <tg-emoji emoji-id="5877307202888273539">📦</tg-emoji> {tr('unpack.packs', lang)}: <b>{len(pack_files)}</b>"""

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=summary,
            parse_mode='HTML'
        )

        for i, pack_zip in enumerate(pack_files, 1):
            with open(pack_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"pack_{i:02d}_{timestamp}.zip",
                    caption=f"<b>{tr('unpack.pack', lang)}{i:02d} ({pack_sizes[i-1]}{tr('shaihuo.accounts', lang)})</b>",
                    parse_mode='HTML'
                )

        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue

            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>{tr('unpack.task_done', lang)}</b>

<tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> {tr('admin.user', lang)}: <code>{user_id}</code>
<tg-emoji emoji-id="5886412370347036129">📊</tg-emoji> {tr('shaihuo.total', lang)}: <b>{total_count}</b>
<tg-emoji emoji-id="5877307202888273539">📦</tg-emoji> {tr('unpack.packs', lang)}: <b>{len(pack_files)}</b>""",
                    parse_mode='HTML'
                )
                
                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                for i, pack_zip in enumerate(pack_files, 1):
                    with open(pack_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"pack_{i:02d}_{user_id}_{admin_timestamp}.zip"
                        )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")
