import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import random
import json
import logging
import traceback
import sqlite3
from datetime import datetime
from opentele.tl import TelegramClient
from opentele.api import API
from opentele.td import TDesktop
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
from i18n import tr, lang_from_update
from task_engine import BatchTask, run_batch, arm_timeout, pack_and_send

load_dotenv()
logger = logging.getLogger(__name__)

DESTROY_BACK = os.getenv("DESTROY_BACK", "").replace('\\n', '\n') or "upload"
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60

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
                            valid_proxies.append({
                                'ip': ip,
                                'port': int(port),
                                'username': username,
                                'password': password,
                                'expire': expire_timestamp
                            })
                    except ValueError:
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

def safe_extract(zip_ref, target_dir):
    for member in zip_ref.infolist():
        member_path = os.path.normpath(member.filename)
        if member_path.startswith(('..', '/', '\\')):
            raise Exception(f"非法路径: {member.filename}")
        zip_ref.extract(member, target_dir)

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
        logger.error(f"转换 tdata 失败 {tdata_dir}: {e}\n{traceback.format_exc()}")
        return False, None, None, None, str(e)

async def destroy_session(session_file, json_file, api_id, api_hash, tdata_dir=None):
    start_time = time.time()
    log_time(f"开始销毁 session: {os.path.basename(session_file)}")
    
    temp_dir = tempfile.mkdtemp(prefix="xiaohui_temp_")
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

            proxy = get_random_proxy()
            proxy_to_use = None
            if proxy:
                proxy_to_use = create_proxy_dict(proxy)

            client = TelegramClient(
                use_session,
                api=official_api,
                proxy=proxy_to_use,
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
                    return False, "Session文件损坏且修复失败", None
            else:
                return False, f"创建客户端失败: {err_msg[:30]}", None
        except Exception as ex:
            return False, f"创建客户端异常: {str(ex)[:30]}", None

    try:
        connect_start = time.time()
        await asyncio.wait_for(client.connect(), timeout=15)
        log_time(f"连接耗时: {time.time() - connect_start:.2f}秒")
        
        auth_start = time.time()
        if not await asyncio.wait_for(client.is_user_authorized(), timeout=10):
            return False, "session无效", None
        log_time(f"授权检查耗时: {time.time() - auth_start:.2f}秒")
        
        me_start = time.time()
        me = await asyncio.wait_for(client.get_me(), timeout=10)
        if not me:
            return False, "无法获取用户信息", None
        log_time(f"获取用户信息耗时: {time.time() - me_start:.2f}秒")
        phone = me.phone if me else None
        
        await client.log_out()
        total_time = time.time() - start_time
        log_time(f"账号 {os.path.basename(session_file)} 销毁成功，总耗时={total_time:.2f}秒")
        return True, "成功注销", phone
    except asyncio.TimeoutError:
        log_time(f"账号 {os.path.basename(session_file)} 网络操作超时")
        return False, "网络操作超时", None
    except FloodWaitError as e:
        return False, f"触发Flood等待{e.seconds}s", None
    except SessionPasswordNeededError:
        return False, "需要2FA验证", None
    except Exception as e:
        error_msg = f"错误: {str(e)[:100]}"
        logger.error(f"销毁会话失败 {session_file}: {e}\n{traceback.format_exc()}")
        return False, error_msg, None
    finally:
        if client:
            disconnect_start = time.time()
            await client.disconnect()
            log_time(f"断开连接耗时: {time.time() - disconnect_start:.2f}秒")
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            log_time(f"已清理临时目录: {temp_dir}")

async def handle_destroy_document(update, context, user_id):
    lang = lang_from_update(update)
    document = update.message.document
    if not document.file_name.endswith('.zip'):
        keyboard = [[InlineKeyboardButton(tr("back_to_main", lang), callback_data="back_to_main").to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> " + tr("err.zip_required", lang),
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return

    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> " + tr("err.processing", lang),
        parse_mode='HTML'
    )

    zip_path = None
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/destroy_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)

        await status_msg.edit_text(
            "<tg-emoji emoji-id='5942826671290715541'>🔍</tg-emoji> " + tr("destroy.processing", lang),
            parse_mode='HTML'
        )
        await process_destroy(update, context, zip_path, user_id)
    except Exception as e:
        logger.error(f"处理文件失败: {e}\n{traceback.format_exc()}")
        keyboard = [[InlineKeyboardButton(tr("back_to_main", lang), callback_data="back_to_main").to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> " + tr("err.process_failed", lang) + f": {str(e)}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    finally:
        if zip_path and os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except:
                pass
        try:
            await status_msg.delete()
        except:
            pass

async def process_destroy(update, context, zip_path, user_id):
    lang = lang_from_update(update)
    api_id_str = os.getenv("TELEGRAM_APP_ID")
    api_hash = os.getenv("TELEGRAM_APP_HASH")
    admins = os.getenv("ADMIN_ID", "").split(",")

    if not api_id_str or not api_hash:
        keyboard = [[InlineKeyboardButton(tr("back_to_main", lang), callback_data="back_to_main").to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> " + tr("err.system_not_configured", lang),
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return

    try:
        api_id = int(api_id_str)
    except (ValueError, TypeError):
        keyboard = [[InlineKeyboardButton(tr("back_to_main", lang), callback_data="back_to_main").to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> " + tr("err.api_config_error", lang),
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return

    try:
        await asyncio.wait_for(
            _process_destroy_internal(update, context, zip_path, user_id, api_id, api_hash, admins),
            timeout=MAX_TASK_TIME
        )
    except asyncio.TimeoutError:
        keyboard = [[InlineKeyboardButton(tr("back_to_main", lang), callback_data="back_to_main").to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> " + tr("err.task_timeout", lang) + f" ({MAX_TASK_TIME}秒)",
            parse_mode='HTML',
            reply_markup=reply_markup
        )

async def _process_destroy_internal(update, context, zip_path, user_id, api_id, api_hash, admins):
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
            keyboard = [[InlineKeyboardButton(tr("back_to_main", lang), callback_data="back_to_main").to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> " + tr("err.extract_failed", lang) + f": {str(e)}",
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
                keyboard = [[InlineKeyboardButton(tr("back_to_main", lang), callback_data="back_to_main").to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> " + tr("err.no_session_tdata", lang),
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return

            status_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"""<tg-emoji emoji-id="5942826671290715541">🔄</tg-emoji> <b>{tr('shaihuo.converting', lang)}</b>

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
                        t._progress_text = f"""<tg-emoji emoji-id="5942826671290715541">🔄</tg-emoji> <b>{tr('shaihuo.convert_progress', lang)}</b>

{tr('shaihuo.progress', lang)}: {i}/{len(tdata_dirs)}
{tr('shaihuo.success', lang)}: {len(accounts)}"""
                await asyncio.sleep(0.2)

            try:
                await status_msg.delete()
            except:
                pass

            if not accounts:
                keyboard = [[InlineKeyboardButton(tr("back_to_main", lang), callback_data="back_to_main").to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> " + tr("err.all_tdata_failed", lang),
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return

        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5942826671290715541">🗑️</tg-emoji> <b>{tr('destroy.in_progress', lang)}</b>

{tr('shaihuo.found_accounts', lang)} <b>{len(accounts)}</b> {tr('shaihuo.accounts', lang)}
{tr('destroy.processing', lang)}...""",
            parse_mode='HTML'
        )

        success_dir = os.path.join(temp_dir, "success")
        pending_dir = os.path.join(temp_dir, "pending")
        os.makedirs(pending_dir, exist_ok=True)

        async def process_one(item, task):
            phone, session_file, json_file, tdata_dir = item
            success, reason, account_phone = await destroy_session(session_file, json_file, api_id, api_hash, tdata_dir)
            phone_number = account_phone or phone or os.path.splitext(os.path.basename(session_file))[0]
            category = "success" if success else "fail"
            target_dir = success_dir if success else failed_dir
            account_folder = os.path.join(target_dir, phone_number)
            os.makedirs(account_folder, exist_ok=True)
            if tdata_dir and os.path.exists(tdata_dir):
                shutil.copytree(tdata_dir, os.path.join(account_folder, "tdata"), dirs_exist_ok=True)
            try:
                shutil.copy2(session_file, os.path.join(account_folder, os.path.basename(session_file)))
                if json_file and os.path.exists(json_file):
                    shutil.copy2(json_file, os.path.join(account_folder, os.path.basename(json_file)))
            except:
                pass
            if not success:
                with open(os.path.join(account_folder, "error.txt"), 'w', encoding='utf-8') as ef:
                    ef.write(f"销毁失败原因: {reason}\n")
                    ef.write(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            return {"category": category, "name": phone_number}

        task = BatchTask(user_id, update.effective_chat.id, accounts, "destroy")
        watchdog = arm_timeout(task, MAX_TASK_TIME)

        async def on_progress(t):
            c = {"success": 0, "fail": 0}
            for r in t.done:
                if r.get("category") in c:
                    c[r["category"]] += 1
                t._progress_text = f"""<tg-emoji emoji-id="5942826671290715541">🗑️</tg-emoji> <b>{tr('destroy.in_progress', lang)}</b>

{tr('shaihuo.progress', lang)}: {t.completed}/{len(accounts)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>{tr('shaihuo.success', lang)}: {c['success']} | <tg-emoji emoji-id="5886496611835581345">❌</tg-emoji>{tr('2fa.failed', lang)}: {c['fail']}"""

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

        def result_text_fn(cats, pending):
            return f"""<tg-emoji emoji-id="5879937509579820068">🗑️</tg-emoji> <b>{tr('destroy.done', lang)}</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> {tr('shaihuo.stats', lang)}:
• <tg-emoji emoji-id="5879770735999717115">👤</tg-emoji> {tr('shaihuo.total', lang)}: <b>{len(accounts)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> {tr('destroy.success_destroy', lang)}: <b>{cats['success']}</b>
• <tg-emoji emoji-id="5886496611835581345">❌</tg-emoji> {tr('2fa.failed', lang)}: <b>{cats['fail']}</b>
• <tg-emoji emoji-id="5846008814129649022">⏸️</tg-emoji> {tr('shaihuo.pending', lang)}: <b>{pending}</b>"""

        await pack_and_send(
            context, update, task,
            categories={
                "success": (success_dir, f"destroy_success_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip", lambda n: f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> {tr('destroy.success_caption', lang)} ({n})</b>"),
                "fail": (failed_dir, f"destroy_failed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip", lambda n: f"<b><tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> {tr('2fa.failed', lang)} ({n})</b>"),
            },
            admins=admins, lang=lang, result_text_fn=result_text_fn, pending_dir=pending_dir
        )

        try:
            await status_msg.delete()
        except:
            pass
