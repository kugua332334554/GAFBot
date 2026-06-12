import os
import json
import asyncio
from flask import Flask, request, send_from_directory
import logging
from dotenv import load_dotenv
import re
import time
import random
from datetime import datetime
from opentele.tl import TelegramClient
from telethon import events
from telethon.errors import RPCError, FloodWaitError
from opentele.api import API
import sqlite3
import shutil

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("TELEGRAM_APP_ID"))
API_HASH = os.getenv("TELEGRAM_APP_HASH")
API_PORT = int(os.getenv("API_PORT", "7788"))

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

def sanitize_sid(sid):
    if not sid:
        return None
    if not re.match(r'^[a-zA-Z0-9_\-]+$', sid):
        return None
    return sid

def get_html_template(template_name):
    template_path = os.path.join(os.path.dirname(__file__), template_name)
    if os.path.exists(template_path):
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    return None

def get_twofa_from_api(sid):
    try:
        api_path = "acd/api.json"
        if os.path.exists(api_path):
            with open(api_path, 'r') as f:
                api_data = json.load(f)
                return api_data.get(sid, {}).get('two_fa', '------')
    except:
        pass
    return '------'

def get_session_config(sid):
    try:
        api_path = "acd/api.json"
        if os.path.exists(api_path):
            with open(api_path, 'r') as f:
                api_data = json.load(f)
                return api_data.get(sid, {})
    except:
        pass
    return {}

def get_ads_from_env():
    ads = []
    for i in range(1, 4):
        ads_str = os.getenv(f"ADS_{i}")
        if ads_str and '-' in ads_str:
            try:
                text, url = ads_str.split('-', 1)
                ads.append({
                    'text': text.strip(),
                    'url': url.strip()
                })
            except ValueError:
                continue
    return ads

def render_with_ads(template_name, **kwargs):
    template = get_html_template(template_name)
    if not template:
        return None

    ads_data = get_ads_from_env()
    ads_meta = json.dumps(ads_data, ensure_ascii=False)

    for key, value in kwargs.items():
        template = template.replace(f'{{{key}}}', str(value))

    meta_tag = f'<meta name="ads-data" content=\'{ads_meta}\'>'
    template = template.replace('<head>', f'<head>{meta_tag}')

    return template

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

@app.route('/getcode', methods=['GET'])
def get_code():
    sid = request.args.get('id')
    if not sid:
        return render_with_ads('unavailable.html', error='缺少id参数'), 400

    safe_sid = sanitize_sid(sid)
    if not safe_sid:
        logger.warning(f"非法sid: {sid}")
        return render_with_ads('unavailable.html', error='无效的id参数'), 400

    session_path = f"acd/{safe_sid}.session"
    if not os.path.exists(session_path):
        logger.warning(f"访问请求失败: Session文件 {safe_sid}.session 不存在")
        return render_with_ads('unavailable.html', error='Session不存在或已失效'), 404

    twofa = get_twofa_from_api(safe_sid)

    code, msg_time, error_type = fetch_code_sync(safe_sid, session_path)

    if code and msg_time:
        return render_with_ads(
            'suc.html', 
            code=code, 
            twofa=twofa, 
            time=msg_time
        )

    error_messages = {
        'account_banned': '账号已被 Telegram 封禁，无法登录',
        'flood_wait': '请求频率过高，请稍后重试',
        'two_fa_required': '账号开启了二次验证，请联系分销商',
        'unauthorized': '会话未授权，需要重新登录',
        'auth_key_invalid': '授权密钥无效，请重置会话',
        'api_config_invalid': 'API 配置错误，请联系管理员',
        'code_expired': '验证码已过期，请重新获取',
        'phone_invalid': '手机号无效',
        'phone_not_registered': '该手机号未注册 Telegram',
        'session_format': 'Session 文件格式损坏',
        'client_create': '客户端创建失败',
        'no_code': '暂未接收到最新验证码，请稍后重试'
    }
    error_msg = error_messages.get(error_type, '获取验证码失败，请稍后重试')
    logger.info(f"ID {safe_sid} 获取验证码失败: {error_type}")
    return render_with_ads('unavailable.html', error=error_msg), 404

def fetch_code_sync(sid, session_path):
    async def _fetch():
        config = get_session_config(sid)

        app_id = config.get('app_id')
        if app_id is None:
            app_id = API_ID
        else:
            try:
                app_id = int(app_id)
            except (ValueError, TypeError):
                app_id = API_ID

        app_hash = config.get('app_hash')
        if not app_hash:
            app_hash = API_HASH

        device_model = config.get('device_model') or None
        app_version = config.get('app_version') or None
        system_lang_code = config.get('system_lang_code') or None
        system_vision = config.get('system_version') or None
        lang_pack = config.get('lang_pack') or None
        official_api = API.TelegramDesktop.Generate()

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

        official_api.api_id = app_id
        official_api.api_hash = app_hash
        proxy = get_random_proxy()
        proxy_dict = create_proxy_dict(proxy) if proxy else None

        client = None
        attempt = 0
        while attempt < 2:
            try:
                client = TelegramClient(
                    session_path,
                    api=official_api,
                    proxy=proxy_dict
                )
                break
            except ValueError as e:
                if "not enough values to unpack (expected 6, got 5)" in str(e) and attempt == 0:
                    logger.warning(f"检测到 session 文件格式问题，尝试自动修复: {session_path}")
                    if repair_session(session_path):
                        logger.info(f"修复完成，重试创建客户端...")
                        attempt += 1
                        continue
                    else:
                        logger.error(f"自动修复失败，无法使用该 session")
                        return None, None, 'session_format'
                elif "too many values to unpack (expected 6)" in str(e) and attempt == 0:
                    logger.warning(f"检测到 session 文件列数过多，尝试自动修复: {session_path}")
                    if repair_session(session_path):
                        logger.info(f"修复完成，重试创建客户端...")
                        attempt += 1
                        continue
                    else:
                        logger.error(f"自动修复失败，无法使用该 session")
                        return None, None, 'session_format'
                else:
                    logger.error(f"创建 TelegramClient 失败: {e}")
                    return None, None, 'client_create'

        try:
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    logger.error(f"ID {sid} 授权失效")
                    return None, None, 'unauthorized'
            except Exception as auth_err:
                err_name = type(auth_err).__name__
                if err_name == 'UnauthorizedError' or getattr(auth_err, 'code', 0) == 401:
                    return None, None, 'unauthorized'
                elif 'AuthKey' in err_name:
                    return None, None, 'auth_key_invalid'
                else:
                    raise auth_err

            try:
                msgs = await client.get_messages(777000, limit=20)
            except RPCError as rpc_err:
                error_type = parse_rpc_error_type(rpc_err)
                return None, None, error_type

            for msg in msgs:
                text = msg.message or ''
                codes = re.findall(r'\d{5,6}', text)
                if codes:
                    latest_code = codes[0]
                    msg_time = msg.date.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"历史记录获取成功: {latest_code} (时间: {msg_time})")
                    return latest_code, msg_time, None

            future = asyncio.Future()

            @client.on(events.NewMessage(chats=777000))
            async def handler(event):
                text = event.message.message or ''
                codes = re.findall(r'\d{5,6}', text)
                if codes and not future.done():
                    new_code = codes[0]
                    new_time = event.message.date.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"新消息获取成功: {new_code} (时间: {new_time})")
                    future.set_result((new_code, new_time))

            try:
                result = await asyncio.wait_for(future, timeout=30)
                return result[0], result[1], None
            except asyncio.TimeoutError:
                logger.info(f"等待验证码超时 {sid}")
                return None, None, 'no_code'

        except RPCError as rpc_err:
            error_type = parse_rpc_error_type(rpc_err)
            logger.error(f"获取验证码失败 {sid}: {rpc_err} -> {error_type}")
            return None, None, error_type
        except Exception as e:
            logger.error(f"获取验证码失败 {sid}: {e}")
            return None, None, 'unknown'
        finally:
            await client.disconnect()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()

def parse_rpc_error_type(error):
    error_name = type(error).__name__
    if error_name in ('UserDeactivatedBanError', 'UserBannedError', 'PhoneNumberBannedError', 'UserRestrictedError'):
        return 'account_banned'
    if 'Flood' in error_name:
        return 'flood_wait'
    if error_name == 'SessionPasswordNeededError':
        return 'two_fa_required'
    if error_name == 'UnauthorizedError' or getattr(error, 'code', 0) == 401:
        return 'unauthorized'
    if 'AuthKey' in error_name:
        return 'auth_key_invalid'
    if 'ApiId' in error_name or 'ApiHash' in error_name:
        return 'api_config_invalid'
    if error_name == 'PhoneCodeExpiredError':
        return 'code_expired'
    if 'PhoneCode' in error_name:
        return 'code_invalid'
    if error_name == 'PhoneNumberInvalidError':
        return 'phone_invalid'
    if error_name == 'PhoneNumberUnoccupiedError':
        return 'phone_not_registered'
    return 'unknown'

@app.route('/copy.svg')
def get_copy_svg():
    return send_from_directory(os.path.dirname(__file__), 'copy.svg')

@app.route('/logo.svg')
def get_logo_svg():
    return send_from_directory(os.path.dirname(__file__), 'logo.svg')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=API_PORT, debug=False)
