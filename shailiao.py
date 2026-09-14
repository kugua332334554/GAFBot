import os
import zipfile
import shutil
import tempfile
import time
import json
import asyncio
import random
import traceback
import sqlite3
import logging
from datetime import datetime
from opentele.tl import TelegramClient
from opentele.api import API
from opentele.td import TDesktop
from telethon import errors
from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
from telethon.tl.types import InputPhoneContact, InputUser
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from dotenv import load_dotenv
from i18n import tr, lang_from_update, get_env_i18n
from task_engine import BatchTask, run_batch, arm_timeout

load_dotenv()
logger = logging.getLogger(__name__)

CHECK_MATERIAL_BACK = os.getenv("CHECK_MATERIAL_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
TARGET_PHONE = "+16055666666"
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60

user_material_states = {}

def log_time(msg):
    logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] {msg}")

def repair_session(session_path):
    if not os.path.exists(session_path):
        return False

    backup_path = session_path + ".bak"
    try:
        shutil.copy2(session_path, backup_path)
        logger.info(f"已备份 {session_path} 到 {backup_path}")

        conn = sqlite3.connect(session_path)
        c = conn.cursor()
        c.execute("PRAGMA table_info(sessions)")
        existing_columns = [row[1] for row in c.fetchall()]
        required_columns = ['dc_id', 'server_address', 'port', 'auth_key', 'takeout_id', 'tmp_auth_key']
        if existing_columns == required_columns:
            conn.close()
            return True

        c.execute("BEGIN TRANSACTION")
        c.execute("CREATE TABLE sessions_new (dc_id INTEGER, server_address TEXT, port INTEGER, auth_key BLOB, takeout_id INTEGER, tmp_auth_key BLOB)")
        select_cols = []
        for col in required_columns:
            if col in existing_columns:
                select_cols.append(col)
            else:
                select_cols.append("NULL")
        select_sql = f"SELECT {', '.join(select_cols)} FROM sessions"
        c.execute(select_sql)
        rows = c.fetchall()
        for row in rows:
            c.execute("INSERT INTO sessions_new VALUES (?,?,?,?,?,?)", row)
        c.execute("DROP TABLE sessions")
        c.execute("ALTER TABLE sessions_new RENAME TO sessions")
        conn.commit()
        conn.close()
        logger.info(f"成功重建 {session_path} 的表结构，共迁移 {len(rows)} 行数据")
        return True
    except Exception as e:
        logger.error(f"修复 {session_path} 失败: {e}")
        return False

def load_proxies():
    global _proxy_list, _proxy_list_last_load
    
    current_time = time.time()
    if _proxy_list is not None and (current_time - _proxy_list_last_load) < PROXY_LIST_CACHE_TIME:
        log_time("使用缓存的代理列表")
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
                    except ValueError:
                        continue
    
    except Exception:
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

async def show_material_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = lang_from_update(update)
    query = update.callback_query
    await query.answer()

    keyboard = [[create_back_button(lang)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=get_env_i18n("CHECK_MATERIAL_BACK", lang),
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

def read_2fa_from_folder(folder_path: str):
    for file in os.listdir(folder_path):
        if file.lower() in ['2fa.txt', '2fa', 'password.txt']:
            try:
                with open(os.path.join(folder_path, file), 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except:
                pass
    return None

async def convert_tdata_to_session_with_proxy(tdata_dir, output_dir, twofa, proxy_dict):
    start_time = time.time()
    log_time(f"开始转换 tdata: {tdata_dir}")
    API_ID = int(os.getenv("TELEGRAM_APP_ID", "2040"))
    API_HASH = os.getenv("TELEGRAM_APP_HASH", "b18441a1ff607e10a989891a5462e627")
    
    try:
        tdesk = TDesktop(tdata_dir)
        if not tdesk.isLoaded():
            return False, None, None, None, "tdata 文件无法加载"
        
        from opentele.api import UseCurrentSession
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
        elapsed = time.time() - start_time
        log_time(f"tdata 转换成功: {tdata_dir} -> {phone}，耗时 {elapsed:.2f}秒")
        return True, phone, final_session, json_path, None
        
    except Exception as e:
        elapsed = time.time() - start_time
        log_time(f"tdata 转换失败 {tdata_dir}: {e}，耗时 {elapsed:.2f}秒")
        logger.error(f"转换 tdata 失败 {tdata_dir}: {e}")
        return False, None, None, None, str(e)

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
        return json_path
    except Exception:
        return None

async def check_material_capability(session_file, json_file, api_id, api_hash, tdata_dir=None):
    start_time = time.time()
    log_time(f"开始筛料检查 session: {os.path.basename(session_file)}")
    
    temp_dir = tempfile.mkdtemp(prefix="shailiao_temp_")
    temp_session = os.path.join(temp_dir, os.path.basename(session_file))
    try:
        shutil.copy2(session_file, temp_session)
        log_time(f"已创建临时 session 副本: {temp_session}")
        use_session = temp_session
    except Exception as e:
        log_time(f"复制 session 到临时目录失败: {e}，将使用原文件")
        use_session = session_file
        temp_dir = None
    
    client = None
    final_json_file = json_file if json_file and os.path.exists(json_file) else None
    
    result = {
        "session": os.path.basename(session_file),
        "status": "unknown",
        "has_capability": False,
        "message": "",
        "phone": None,
        "json_file": final_json_file,
        "tdata_dir": tdata_dir
    }
    
    json_config = {}
    if final_json_file:
        try:
            with open(final_json_file, 'r', encoding='utf-8') as f:
                json_config = json.load(f)
        except Exception:
            final_json_file = None
            result["json_file"] = None
    
    final_api_id = api_id
    final_api_hash = api_hash
    if json_config:
        if 'app_id' in json_config and json_config['app_id']:
            try:
                final_api_id = int(json_config['app_id'])
            except (ValueError, TypeError):
                pass
        if 'app_hash' in json_config and json_config['app_hash']:
            final_api_hash = str(json_config['app_hash'])
    
    device_model = json_config.get('device_model') or None
    app_version = json_config.get('app_version') or None
    system_lang_code = json_config.get('system_lang_code') or None
    system_vision = json_config.get('system_version') or json_config.get('sdk') or None
    lang_pack = json_config.get('lang_pack') or None
    
    retry_count = 0
    while retry_count < 2:
        try:
            proxy = get_random_proxy()
            proxy_dict = create_proxy_dict(proxy) if proxy else None

            official_api = API.TelegramDesktop.Generate()
            if device_model is None:
                max_attempts = 100
                attempt = 0
                while 'linux' in official_api.device_model.lower() and attempt < max_attempts:
                    official_api = API.TelegramDesktop.Generate()
                    attempt += 1
                if 'linux' in official_api.device_model.lower():
                    official_api.device_model = "Desktop"
            else:
                official_api.device_model = device_model

            official_api.api_id = final_api_id
            official_api.api_hash = final_api_hash
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
                use_session,
                api=official_api,
                proxy=proxy_dict,
                receive_updates=False,
                timeout=10,
                connection_retries=1
            )
            break
        except ValueError as e:
            err_msg = str(e)
            if ("not enough values to unpack (expected 6, got 5)" in err_msg or
                "too many values to unpack (expected 6)" in err_msg) and retry_count == 0:
                logger.warning(f"检测到 session 文件格式问题: {use_session}，尝试自动修复")
                if repair_session(use_session):
                    logger.info(f"修复完成，重试创建客户端")
                    retry_count += 1
                    continue
                else:
                    logger.error(f"自动修复失败，无法使用该 session: {use_session}")
                    result["status"] = "failed"
                    result["message"] = "Session文件损坏且修复失败"
                    return result
            else:
                result["status"] = "failed"
                result["message"] = f"创建客户端失败: {err_msg[:30]}"
                return result
        except Exception as ex:
            result["status"] = "failed"
            result["message"] = f"创建客户端异常: {str(ex)[:30]}"
            return result

    try:
        connect_start = time.time()
        await asyncio.wait_for(client.connect(), timeout=15)
        log_time(f"连接耗时: {time.time() - connect_start:.2f}秒")
        
        auth_start = time.time()
        if not await asyncio.wait_for(client.is_user_authorized(), timeout=10):
            result["status"] = "failed"
            result["message"] = "session无效"
            return result
        log_time(f"授权检查耗时: {time.time() - auth_start:.2f}秒")
        
        me_start = time.time()
        me = await asyncio.wait_for(client.get_me(), timeout=10)
        if not me:
            result["status"] = "failed"
            result["message"] = "无法获取用户信息"
            return result
        log_time(f"获取用户信息耗时: {time.time() - me_start:.2f}秒")
        
        result["phone"] = me.phone

        if not final_json_file:
            generated_path = await generate_json_for_session(
                session_file, client, me, final_api_id, final_api_hash, official_api
            )
            if generated_path:
                final_json_file = generated_path
                result["json_file"] = generated_path
        
        try:
            contact = InputPhoneContact(
                client_id=random.randint(1, 2**31 - 1),
                phone=TARGET_PHONE,
                first_name="Test",
                last_name=""
            )
            import_result = await client(ImportContactsRequest(contacts=[contact]))
            
            if import_result.imported:
                imported_user = import_result.imported[0]
                try:
                    user_to_delete = InputUser(user_id=imported_user.user_id, access_hash=imported_user.access_hash)
                    await client(DeleteContactsRequest(id=[user_to_delete]))
                except Exception:
                    pass
                
                result["has_capability"] = True
                result["status"] = "success"
                result["message"] = "有能力"
            else:
                result["has_capability"] = False
                result["status"] = "success"
                result["message"] = "无能力"
            
        except errors.rpcerrorlist.FloodWaitError as e:
            result["has_capability"] = False
            result["status"] = "success"
            result["message"] = f"无能力 (等待{e.seconds}秒)"
        except Exception as e:
            error_str = str(e).lower()
            if "cannot add" in error_str or "privacy" in error_str or "USER_PRIVACY_RESTRICTED" in str(e):
                result["has_capability"] = False
                result["status"] = "success"
                result["message"] = "无能力"
            else:
                result["status"] = "failed"
                result["message"] = f"检查失败: {str(e)[:50]}"
        
        total_time = time.time() - start_time
        log_time(f"账号 {os.path.basename(session_file)} 筛料检查完成，状态={result['status']}，有能力={result['has_capability']}，总耗时={total_time:.2f}秒")
                
    except asyncio.TimeoutError:
        log_time(f"账号 {os.path.basename(session_file)} 网络操作超时")
        result["status"] = "failed"
        result["message"] = "网络操作超时"
    except Exception as e:
        result["status"] = "failed"
        result["message"] = f"错误: {str(e)[:30]}"
    finally:
        if client:
            disconnect_start = time.time()
            await client.disconnect()
            log_time(f"断开连接耗时: {time.time() - disconnect_start:.2f}秒")
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            log_time(f"已清理临时目录: {temp_dir}")
    
    return result

async def handle_material_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    lang = lang_from_update(update)
    document = update.message.document

    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("err.zip_required", lang),
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_material_states.pop(user_id, None)
        return

    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> " + tr("err.processing", lang),
        parse_mode='HTML'
    )

    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/material_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)

        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> " + tr("material.processing", lang),
            parse_mode='HTML'
        )

        await process_material_check(update, context, zip_path, user_id)

        try:
            os.remove(zip_path)
        except:
            pass

    except Exception as e:
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("err.process_failed", lang) + f": {str(e)}",
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
    lang = lang_from_update(update)
    api_id_str = os.getenv("TELEGRAM_APP_ID")
    api_hash = os.getenv("TELEGRAM_APP_HASH")
    admins = os.getenv("ADMIN_ID", "").split(",")

    if not api_id_str or not api_hash:
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("err.system_not_configured", lang),
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return

    try:
        api_id = int(api_id_str)
    except (ValueError, TypeError):
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("err.api_config_error", lang),
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return

    await _process_material_internal(update, context, zip_path, user_id, api_id, api_hash, admins)

async def _process_material_internal(update, context, zip_path, user_id, api_id, api_hash, admins):
    lang = lang_from_update(update)
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                safe_extract(zip_ref, extract_dir)
                extracted_size = get_total_size(extract_dir)
                if extracted_size > MAX_EXTRACT_SIZE:
                    raise Exception(f"解压后文件过大 ({extracted_size//1024//1024}MB > {MAX_EXTRACT_SIZE//1024//1024}MB)")
        except Exception as e:
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("err.extract_failed", lang) + f": {str(e)}",
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
        
        accounts = []
        if session_map:
            for sess, jsonf in session_map.items():
                accounts.append((None, sess, jsonf, None))
        else:
            tdata_dirs = find_tdata_folders(extract_dir)
            if not tdata_dirs:
                keyboard = [[create_back_button()]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("err.no_session_tdata", lang),
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return
            
            status_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>{tr('shaihuo.converting', lang)}</b>

{tr('shaihuo.found_accounts', lang)} <b>{len(tdata_dirs)}</b> tdata
{tr('err.processing', lang)}...""",
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
                            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>{tr('shaihuo.convert_progress', lang)}</b>

{tr('shaihuo.progress', lang)}: {i}/{len(tdata_dirs)}
{tr('shaihuo.success', lang)}: {len(accounts)}""",
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
                    text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("err.all_tdata_failed", lang),
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return
        
        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>{tr('material.in_progress', lang)}</b>

{tr('shaihuo.found_accounts', lang)} <b>{len(accounts)}</b> {tr('shaihuo.accounts', lang)}
<tg-emoji emoji-id="5775887550262546277">🔄</tg-emoji>{tr('material.processing', lang)}...""",
            parse_mode='HTML'
        )
        
        capability_dir = os.path.join(temp_dir, "has_capability")
        no_capability_dir = os.path.join(temp_dir, "no_capability")
        failed_dir = os.path.join(temp_dir, "failed")
        
        os.makedirs(capability_dir, exist_ok=True)
        os.makedirs(no_capability_dir, exist_ok=True)
        os.makedirs(failed_dir, exist_ok=True)
        
        pending_dir = os.path.join(temp_dir, "pending")
        os.makedirs(pending_dir, exist_ok=True)

        def count_cats():
            c = {"cap": 0, "nocap": 0, "fail": 0}
            for r in task.done:
                cat = r.get("category")
                if cat == "cap":
                    c["cap"] += 1
                elif cat == "nocap":
                    c["nocap"] += 1
                elif cat == "fail":
                    c["fail"] += 1
            return c

        async def process_one(item, task):
            phone, session_file, json_file, tdata_dir = item
            result = await check_material_capability(session_file, json_file, api_id, api_hash, tdata_dir)
            updated_json = result.get("json_file")
            if updated_json:
                json_file = updated_json
            account_phone = result.get("phone") or phone or os.path.splitext(os.path.basename(session_file))[0]
            if result["status"] == "success":
                if result["has_capability"]:
                    target_dir, cat = capability_dir, "cap"
                else:
                    target_dir, cat = no_capability_dir, "nocap"
            else:
                target_dir, cat = failed_dir, "fail"

            account_folder = os.path.join(target_dir, account_phone)
            os.makedirs(account_folder, exist_ok=True)
            if tdata_dir and os.path.exists(tdata_dir):
                shutil.copytree(tdata_dir, os.path.join(account_folder, "tdata"), dirs_exist_ok=True)
            if session_file and os.path.exists(session_file):
                shutil.copy2(session_file, os.path.join(account_folder, os.path.basename(session_file)))
            if json_file and os.path.exists(json_file):
                shutil.copy2(json_file, os.path.join(account_folder, os.path.basename(json_file)))

            if cat == "fail":
                reason_dir = failed_dir
                reasons_file = os.path.join(reason_dir, "failed_reasons.txt")
                with open(reasons_file, 'a', encoding='utf-8') as f:
                    f.write(f"{result.get('session','?')}: {result.get('message','')}\n")
            return {"category": cat, "name": account_phone}

        task = BatchTask(user_id, update.effective_chat.id, accounts, "shailiao")
        watchdog = arm_timeout(task, MAX_TASK_TIME)

        async def on_progress(t):
            c = count_cats()
            try:
                await status_msg.edit_text(
                    text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>{tr('material.in_progress', lang)}</b>

{tr('shaihuo.progress', lang)}: {t.completed}/{len(accounts)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>{tr('material.has_capability', lang)}: {c['cap']} | <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>{tr('material.no_capability', lang)}: {c['nocap']} | <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji>{tr('2fa.failed', lang)}: {c['fail']}""",
                    parse_mode='HTML'
                )
            except:
                pass

        await run_batch(update, context, task, process_one, on_progress=on_progress)

        if not watchdog.done():
            watchdog.cancel()

        if task.stopped:
            for item in task.remaining_pending():
                phone, session_file, json_file, tdata_dir = item
                key = phone or os.path.splitext(os.path.basename(str(session_file)))[0]
                pdir = os.path.join(pending_dir, key)
                os.makedirs(pdir, exist_ok=True)
                if tdata_dir and os.path.exists(tdata_dir):
                    shutil.copytree(tdata_dir, os.path.join(pdir, "tdata"), dirs_exist_ok=True)
                if session_file and os.path.exists(session_file):
                    shutil.copy2(session_file, os.path.join(pdir, os.path.basename(session_file)))
                if json_file and os.path.exists(json_file):
                    shutil.copy2(json_file, os.path.join(pdir, os.path.basename(json_file)))

        cats = count_cats()
        capability_count, no_capability_count, failed_count = cats['cap'], cats['nocap'], cats['fail']
        pending_count = len(task.remaining_pending())

        def zip_dir(src, dst):
            with zipfile.ZipFile(dst, 'w') as zipf:
                for root, dirs, files in os.walk(src):
                    for file in files:
                        file_path = os.path.join(root, file)
                        zipf.write(file_path, os.path.relpath(file_path, src))

        capability_zip = os.path.join(temp_dir, "has_capability.zip")
        if capability_count > 0:
            zip_dir(capability_dir, capability_zip)
        no_capability_zip = os.path.join(temp_dir, "no_capability.zip")
        if no_capability_count > 0:
            zip_dir(no_capability_dir, no_capability_zip)
        failed_zip = os.path.join(temp_dir, "failed.zip")
        if failed_count > 0:
            zip_dir(failed_dir, failed_zip)
        pending_zip = os.path.join(temp_dir, "pending.zip")
        if pending_count > 0:
            zip_dir(pending_dir, pending_zip)

        stop_tag = " (已终止)" if task.stopped else ""

        result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>{tr('material.done', lang)}{stop_tag}</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> {tr('shaihuo.stats', lang)}:
• <tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> {tr('shaihuo.total', lang)}: <b>{len(accounts)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> {tr('material.has_capability', lang)}: <b>{capability_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> {tr('material.no_capability', lang)}: <b>{no_capability_count}</b>
• <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji> {tr('material.check_failed', lang)}: <b>{failed_count}</b>
• <tg-emoji emoji-id="5846008814129649022">⏸️</tg-emoji> {tr('shaihuo.pending', lang)}: <b>{pending_count}</b>"""

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=result_text,
            parse_mode='HTML'
        )

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        zips_to_send = []
        if capability_count > 0:
            zips_to_send.append((capability_zip, f"has_capability_{timestamp}.zip", f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> {tr('material.has_caption', lang)} ({capability_count})</b>"))
        if no_capability_count > 0:
            zips_to_send.append((no_capability_zip, f"no_capability_{timestamp}.zip", f"<b><tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> {tr('material.no_caption', lang)} ({no_capability_count})</b>"))
        if failed_count > 0:
            zips_to_send.append((failed_zip, f"failed_{timestamp}.zip", f"<b><tg-emoji emoji-id='5846008814129649022'>⚠️</tg-emoji> {tr('material.check_failed', lang)} ({failed_count})</b>\n" + tr("material.failed_reason_hint", lang)))
        if pending_count > 0:
            zips_to_send.append((pending_zip, f"pending_{timestamp}.zip", f"<b><tg-emoji emoji-id='5846008814129649022'>⏸️</tg-emoji> {tr('shaihuo.pending_caption', lang)} ({pending_count})</b>"))

        for zpath, fname, cap in zips_to_send:
            try:
                with open(zpath, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=fname,
                        caption=cap,
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"发送zip失败 {fname}: {e}")

        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue

            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>{tr('material.task_done', lang)}{stop_tag}</b>

<tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> {tr('admin.user', lang)}: <code>{user_id}</code>
<tg-emoji emoji-id="5886412370347036129">📊</tg-emoji> {tr('shaihuo.total', lang)}: <b>{len(accounts)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> {tr('material.has_capability', lang)}: <b>{capability_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> {tr('material.no_capability', lang)}: <b>{no_capability_count}</b>
• <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji> {tr('material.check_failed', lang)}: <b>{failed_count}</b>
• <tg-emoji emoji-id="5846008814129649022">⏸️</tg-emoji> {tr('shaihuo.pending', lang)}: <b>{pending_count}</b>""",
                    parse_mode='HTML'
                )

                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                for zpath, fname, cap in zips_to_send:
                    try:
                        with open(zpath, 'rb') as f:
                            await context.bot.send_document(
                                chat_id=admin_id,
                                document=f,
                                filename=fname.replace(timestamp, admin_timestamp),
                                caption=cap,
                                parse_mode='HTML'
                            )
                    except Exception as e:
                        logger.error(f"发给管理员zip失败 {fname}: {e}")
            except Exception:
                pass

        try:
            await status_msg.delete()
        except:
            pass
