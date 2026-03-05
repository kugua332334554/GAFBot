import os
import zipfile
import shutil
import tempfile
import time
import json
import asyncio
from datetime import datetime
from telethon import TelegramClient, errors
from telethon.tl.functions.contacts import AddContactRequest, DeleteContactsRequest
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

CHECK_MATERIAL_BACK = os.getenv("CHECK_MATERIAL_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
TARGET_PHONE = "+16055666666"
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

user_material_states = {}

def create_back_button():
    return InlineKeyboardButton(
        "返回主菜单", 
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

async def show_material_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=CHECK_MATERIAL_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    user_material_states[str(query.from_user.id)] = "waiting_material_zip"

def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

async def check_material_capability(session_file, json_file, api_id, api_hash):
    client = None
    result = {
        "session": os.path.basename(session_file),
        "status": "unknown",
        "has_capability": False,
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
        
        try:
            contact = await client.get_input_entity(TARGET_PHONE)
            await client(AddContactRequest(
                id=contact,
                first_name="Test",
                last_name="",
                phone=TARGET_PHONE,
                add_phone_privacy_exception=False
            ))
            
            await asyncio.sleep(1)
            
            try:
                await client(DeleteContactsRequest(id=[contact]))
            except:
                pass
            
            result["has_capability"] = True
            result["status"] = "success"
            result["message"] = "有能力"
            
        except errors.rpcerrorlist.PhoneNumberInvalidError:
            result["has_capability"] = False
            result["status"] = "success"
            result["message"] = "无能力"
        except errors.rpcerrorlist.ContactAddMissingError:
            result["has_capability"] = False
            result["status"] = "success"
            result["message"] = "无能力"
        except errors.FloodWaitError as e:
            result["has_capability"] = False
            result["status"] = "success"
            result["message"] = f"无能力 (等待{e.seconds}秒)"
        except Exception as e:
            error_str = str(e).lower()
            if "cannot add" in error_str or "privacy" in error_str:
                result["has_capability"] = False
                result["status"] = "success"
                result["message"] = "无能力"
            else:
                result["status"] = "failed"
                result["message"] = f"检查失败: {str(e)[:50]}"
                
    except Exception as e:
        result["status"] = "failed"
        result["message"] = f"错误: {str(e)[:30]}"
    finally:
        if client:
            await client.disconnect()
    
    return result

async def handle_material_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    
    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_material_states.pop(user_id, None)
        return
    
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/material_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 开始检查筛料能力...",
            parse_mode='HTML'
        )
        
        await process_material_check(update, context, zip_path, user_id)
        
        try:
            os.remove(zip_path)
        except:
            pass
        
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
        user_material_states.pop(user_id, None)
        try:
            await status_msg.delete()
        except:
            pass

async def process_material_check(update, context, zip_path, user_id):
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
            _process_material_internal(update, context, zip_path, user_id, api_id, api_hash, admins), 
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

async def _process_material_internal(update, context, zip_path, user_id, api_id, api_hash, admins):
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
        
        session_map = {}
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    base = os.path.splitext(file)[0]
                    session_path = os.path.join(root, file)
                    json_path = os.path.join(root, f"{base}.json")
                    session_map[session_path] = json_path if os.path.exists(json_path) else None
        
        if not session_map:
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
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>筛料能力检查进行中</b>

目标联系人: <code>{TARGET_PHONE}</code>
找到 <b>{len(session_map)}</b> 个账号
<tg-emoji emoji-id="5775887550262546277">🔄</tg-emoji>正在处理，请稍候...""",
            parse_mode='HTML'
        )
        
        capability_dir = os.path.join(temp_dir, "has_capability")
        no_capability_dir = os.path.join(temp_dir, "no_capability")
        failed_dir = os.path.join(temp_dir, "failed")
        
        os.makedirs(capability_dir, exist_ok=True)
        os.makedirs(no_capability_dir, exist_ok=True)
        os.makedirs(failed_dir, exist_ok=True)
        
        capability_count = 0
        no_capability_count = 0
        failed_count = 0
        results = []
        
        sessions_list = list(session_map.items())
        for i, (session_file, json_file) in enumerate(sessions_list, 1):
            if i % 3 == 0 or i == len(sessions_list):
                try:
                    await status_msg.edit_text(
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>筛料能力检查进行中</b>

进度: {i}/{len(sessions_list)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>有能力: {capability_count} | <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>无能力: {no_capability_count} | <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji>失败: {failed_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass
            
            result = await check_material_capability(session_file, json_file, api_id, api_hash)
            results.append(result)
            
            if result["status"] == "success":
                if result["has_capability"]:
                    target_dir = capability_dir
                    capability_count += 1
                else:
                    target_dir = no_capability_dir
                    no_capability_count += 1
            else:
                target_dir = failed_dir
                failed_count += 1
            
            try:
                shutil.copy2(session_file, os.path.join(target_dir, os.path.basename(session_file)))
                if json_file and os.path.exists(json_file):
                    shutil.copy2(json_file, os.path.join(target_dir, os.path.basename(json_file)))
            except:
                pass
            
            await asyncio.sleep(0.5)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        capability_zip = os.path.join(temp_dir, "has_capability.zip")
        if capability_count > 0:
            with zipfile.ZipFile(capability_zip, 'w') as zipf:
                for root, dirs, files in os.walk(capability_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, capability_dir)
                        zipf.write(file_path, arcname)
        
        no_capability_zip = os.path.join(temp_dir, "no_capability.zip")
        if no_capability_count > 0:
            with zipfile.ZipFile(no_capability_zip, 'w') as zipf:
                for root, dirs, files in os.walk(no_capability_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, no_capability_dir)
                        zipf.write(file_path, arcname)
        
        failed_zip = os.path.join(temp_dir, "failed.zip")
        if failed_count > 0:
            with zipfile.ZipFile(failed_zip, 'w') as zipf:
                for root, dirs, files in os.walk(failed_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, failed_dir)
                        zipf.write(file_path, arcname)
        
        result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>筛料能力检查完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• <tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 总账号: <b>{len(sessions_list)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 有能力: <b>{capability_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 无能力: <b>{no_capability_count}</b>
• <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji> 检查失败: <b>{failed_count}</b>"""

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=result_text,
            parse_mode='HTML'
        )
        
        if capability_count > 0:
            with open(capability_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"has_capability_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 有能力账号 ({capability_count}个)</b>\n测试后已自动删除联系人",
                    parse_mode='HTML'
                )
        
        if no_capability_count > 0:
            with open(no_capability_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"no_capability_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 无能力账号 ({no_capability_count}个)</b>",
                    parse_mode='HTML'
                )
        
        if failed_count > 0:
            with open(failed_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"failed_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5846008814129649022'>⚠️</tg-emoji> 检查失败 ({failed_count}个)</b>",
                    parse_mode='HTML'
                )
        
        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue
            
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>筛料能力检查完成</b>

<tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 用户: <code>{user_id}</code>
目标联系人: <code>{TARGET_PHONE}</code>
<tg-emoji emoji-id="5886412370347036129">📊</tg-emoji> 总账号: <b>{len(sessions_list)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 有能力: <b>{capability_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 无能力: <b>{no_capability_count}</b>
• <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji> 检查失败: <b>{failed_count}</b>人""",
                    parse_mode='HTML'
                )
                
                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                
                if capability_count > 0:
                    with open(capability_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"has_capability_{user_id}_{admin_timestamp}.zip"
                        )
                
                if no_capability_count > 0:
                    with open(no_capability_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"no_capability_{user_id}_{admin_timestamp}.zip"
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
