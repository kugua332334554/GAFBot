import os
import zipfile
import shutil
import tempfile
import time
import json
import asyncio
import random
import struct
import re
import traceback
from datetime import datetime
from telethon import TelegramClient as TelethonClient
from telethon.tl.functions import TLRequest
from telethon.tl.functions.contacts import GetContactsRequest, DeleteContactsRequest
from telethon.errors import FloodWaitError
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from dotenv import load_dotenv
from opentele.tl import TelegramClient
from opentele.api import API
from telethon.tl.functions.contacts import BlockRequest
load_dotenv()
logger = logging.getLogger(__name__)

CLEAN_ACCOUNT_BACK = os.getenv("CLEAN_ACCOUNT_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60

user_clean_states = {}

class GetPasskeysManual(TLRequest):
    CONSTRUCTOR_ID = 0xea1f0c52
    def __init__(self):
        super().__init__()
    def _bytes(self):
        return struct.pack('<I', self.CONSTRUCTOR_ID)
    async def resolve(self, client, utils):
        pass
    def __bytes__(self):
        return self._bytes()

class DeletePasskeyManual(TLRequest):
    CONSTRUCTOR_ID = 0xf5b5563f
    def __init__(self, pk_id):
        super().__init__()
        self.pk_id = pk_id
    def _bytes(self):
        res = struct.pack('<I', self.CONSTRUCTOR_ID)
        b_id = self.pk_id.encode('utf-8')
        L = len(b_id)
        if L <= 253:
            res += struct.pack('<B', L)
        else:
            res += b'\xfe' + struct.pack('<I', L)[:3]
        res += b_id
        while len(res) % 4 != 0:
            res += b'\x00'
        return res
    async def resolve(self, client, utils):
        pass
    def __bytes__(self):
        return self._bytes()

def parse_raw_passkeys(raw_bytes):
    matches = re.findall(b'[\x20-\x7e]{4,}', raw_bytes)
    results = []
    for m in matches:
        text = m.decode('utf-8', errors='ignore')
        if len(text) > 5 and not text.startswith('telethon'):
            results.append(text)
    passkeys = []
    for i in range(0, len(results)-1, 2):
        passkeys.append({'id': results[i], 'name': results[i+1]})
    return passkeys

async def delete_passkeys_for_client(client):
    deleted_count = 0
    errors = []
    try:
        client.session.layer = 188
        try:
            result = await client(GetPasskeysManual())
            if hasattr(result, 'passkeys'):
                passkeys = result.passkeys
                logger.info(f"成功获取到 {len(passkeys)} 个Passkey")
                for pk in passkeys:
                    pk_id = pk.id
                    pk_name = pk.name
                    logger.info(f"准备删除Passkey: ID={pk_id}, Name={pk_name}")
                    try:
                        await client(DeletePasskeyManual(pk_id))
                        deleted_count += 1
                        logger.info(f"成功删除Passkey: ID={pk_id}, Name={pk_name}")
                    except Exception as del_e:
                        error_msg = f"删除Passkey {pk_id} 失败: {str(del_e)}"
                        errors.append(error_msg)
                        logger.error(error_msg)
            else:
                logger.warning("返回结果中没有passkeys属性")
        except Exception as e:
            err_msg = str(e)
            if "Remaining bytes:" in err_msg:
                raw_data = eval(err_msg.split("Remaining bytes: ")[1])
                passkeys = parse_raw_passkeys(raw_data)
                logger.info(f"通过原始解析获取到 {len(passkeys)} 个Passkey")
                for pk in passkeys:
                    pk_id = pk['id']
                    pk_name = pk['name']
                    logger.info(f"准备删除Passkey: ID={pk_id}, Name={pk_name}")
                    try:
                        await client(DeletePasskeyManual(pk_id))
                        deleted_count += 1
                        logger.info(f"成功删除Passkey: ID={pk_id}, Name={pk_name}")
                    except Exception as del_e:
                        error_msg = f"删除Passkey {pk_id} 失败: {str(del_e)}"
                        errors.append(error_msg)
                        logger.error(error_msg)
            else:
                logger.error(f"获取Passkey列表失败: {err_msg}")
                errors.append(f"获取Passkey列表失败: {err_msg}")
    except Exception as e:
        logger.error(f"处理Passkey时出错: {e}")
        errors.append(f"处理Passkey时出错: {str(e)}")
    return deleted_count, errors

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
    if not proxy:
        return None
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

async def show_clean_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("删除所有对话", callback_data="clean_chats").to_dict() | {"icon_custom_emoji_id": "5877307202888273539"},
            InlineKeyboardButton("删除所有联系人", callback_data="clean_contacts").to_dict() | {"icon_custom_emoji_id": "5877318502947229960"}
        ],
        [
            InlineKeyboardButton("删除所有Passkey", callback_data="clean_passkeys").to_dict() | {"icon_custom_emoji_id": "5886505193180239900"}
        ],
        [
            InlineKeyboardButton("全部删除", callback_data="clean_all").to_dict() | {"icon_custom_emoji_id": "5922712343011135025"}
        ],
        [create_back_button()]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=CLEAN_ACCOUNT_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    user_clean_states[user_id] = {"waiting_selection": True}

async def handle_clean_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    await query.answer()
    clean_type = {
        "clean_chats": "chats",
        "clean_contacts": "contacts",
        "clean_passkeys": "passkeys",
        "clean_all": "all"
    }.get(data)
    user_clean_states[user_id] = {
        "type": clean_type,
        "waiting_zip": True
    }
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    type_names = {
        "chats": "删除所有对话",
        "contacts": "删除所有联系人",
        "passkeys": "删除所有Passkey",
        "all": "删除所有对话、联系人和Passkey"
    }
    await query.edit_message_text(
        text=f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 已选择: {type_names[clean_type]}

<tg-emoji emoji-id="5877540355187937244">📤</tg-emoji> 请上传包含session和json的ZIP文件""",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def handle_clean_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    clean_info = user_clean_states.get(user_id, {})
    clean_type = clean_info.get("type")
    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_clean_states.pop(user_id, None)
        return
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/clean_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 开始处理清理任务...",
            parse_mode='HTML'
        )
        await process_clean(update, context, zip_path, user_id, clean_type)
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
        user_clean_states.pop(user_id, None)
        try:
            await status_msg.delete()
        except:
            pass

def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

async def clean_account_operations(client, clean_type):
    results = {
        "chats_deleted": 0,
        "contacts_deleted": 0,
        "passkeys_deleted": 0,
        "errors": []
    }
    
    if clean_type in ["chats", "all"]:
        try:
            dialogs = await client.get_dialogs()
            logger.info(f"获取到 {len(dialogs)} 个对话")
            for dialog in dialogs:
                try:
                    entity = dialog.entity
                    is_bot = False
                    bot_info = ""
                    if entity and hasattr(entity, 'bot') and entity.bot:
                        is_bot = True
                        bot_info = f" (机器人: {entity.username or entity.first_name or entity.id})"
                    
                    logger.info(f"正在删除对话: {dialog.name} (ID: {dialog.id}){bot_info}")
                    await client.delete_dialog(dialog.entity or dialog.id)
                    results["chats_deleted"] += 1
                    
                    if is_bot:
                        try:
                            await client(BlockRequest(entity.id))
                            logger.info(f"已屏蔽机器人: {dialog.name}")
                        except Exception as block_e:
                            logger.error(f"屏蔽机器人 {dialog.name} 失败: {block_e}")
                            results["errors"].append(f"屏蔽机器人失败: {str(block_e)[:100]}")
                    
                    await asyncio.sleep(0.5)
                except Exception as e:
                    err_detail = traceback.format_exc()
                    logger.error(f"删除对话失败: {e}\n{err_detail}")
                    results["errors"].append(f"删除对话失败: {str(e)[:100]}")
        except Exception as e:
            err_detail = traceback.format_exc()
            logger.error(f"获取对话列表失败: {e}\n{err_detail}")
            results["errors"].append(f"获取对话列表失败: {str(e)[:100]}")

    if clean_type in ["contacts", "all"]:
        try:
            contacts_result = await client(GetContactsRequest(hash=0))
            contact_entries = contacts_result.contacts
            logger.info(f"获取到 {len(contact_entries)} 个联系人（从 contacts 字段）")
            user_dict = {user.id: user for user in contacts_result.users}
            for contact_entry in contact_entries:
                user_id = contact_entry.user_id
                user_obj = user_dict.get(user_id)
                try:
                    if user_obj:
                        logger.info(f"正在删除联系人: id={user_id}, name={user_obj.first_name} {user_obj.last_name}")
                        await client(DeleteContactsRequest(id=[user_obj]))
                    else:
                        logger.info(f"正在删除联系人: id={user_id}（无完整用户信息）")
                        await client(DeleteContactsRequest(id=[user_id]))
                    results["contacts_deleted"] += 1
                    await asyncio.sleep(0.5)
                except FloodWaitError as flood:
                    wait_time = flood.seconds
                    logger.warning(f"触发 FloodWait，需等待 {wait_time} 秒")
                    await asyncio.sleep(wait_time)
                    try:
                        if user_obj:
                            await client(DeleteContactsRequest(id=[user_obj]))
                        else:
                            await client(DeleteContactsRequest(id=[user_id]))
                        results["contacts_deleted"] += 1
                        logger.info(f"重试后成功删除联系人 {user_id}")
                    except Exception as retry_e:
                        logger.error(f"重试后删除联系人 {user_id} 仍失败: {retry_e}")
                        results["errors"].append(f"删除联系人 {user_id} 失败: {str(retry_e)[:100]}")
                except Exception as e:
                    logger.error(f"删除联系人 {user_id} 失败: {e}\n{traceback.format_exc()}")
                    results["errors"].append(f"删除联系人 {user_id} 失败: {str(e)[:100]}")
        except Exception as e:
            logger.error(f"获取或删除联系人整体失败: {e}\n{traceback.format_exc()}")
            results["errors"].append(f"获取联系人列表失败: {str(e)[:100]}")

    if clean_type in ["passkeys", "all"]:
        deleted, errs = await delete_passkeys_for_client(client)
        results["passkeys_deleted"] = deleted
        results["errors"].extend(errs)

    return results

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

async def process_clean(update, context, zip_path, user_id, clean_type):
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
            _process_clean_internal(update, context, zip_path, user_id, api_id, api_hash, admins, clean_type),
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

async def _process_clean_internal(update, context, zip_path, user_id, api_id, api_hash, admins, clean_type):
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

        type_names = {
            "chats": "删除所有对话",
            "contacts": "删除所有联系人",
            "passkeys": "删除所有Passkey",
            "all": "删除所有对话、联系人和Passkey"
        }

        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>清理账号进行中</b>

清理类型: {type_names[clean_type]}
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
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>清理账号进行中</b>

进度: {i}/{len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {success_count} | <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失败: {failed_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass

            client = None
            try:
                json_config = {}
                if json_file:
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
                            logger.warning(f"无效的 app_id: {json_config['app_id']}, 使用默认值")
                    if 'app_hash' in json_config and json_config['app_hash']:
                        final_api_hash = str(json_config['app_hash'])

                device_model = json_config.get('device') or None
                app_version = json_config.get('app_version') or None
                system_lang_code = json_config.get('system_lang_pack') or None
                system_version = json_config.get('system_vision') or json_config.get('sdk') or None
                lang_pack = json_config.get('lang_pack') or None

                official_api = API.TelegramDesktop.Generate()
                official_api.api_id = final_api_id
                official_api.api_hash = final_api_hash
                if device_model:
                    official_api.device_model = device_model
                if app_version:
                    official_api.app_version = app_version
                if system_lang_code:
                    official_api.system_lang_code = system_lang_code
                if system_version:
                    official_api.system_version = system_version
                if lang_pack:
                    official_api.lang_pack = lang_pack
                    official_api.lang_code = lang_pack

                proxy = get_random_proxy()
                proxy_dict = create_proxy_dict(proxy) if proxy else None

                client = TelegramClient(
                    session_file,
                    api=official_api,
                    proxy=proxy_dict
                )

                await client.connect()
                if not await client.is_user_authorized():
                    result = {"session": os.path.basename(session_file), "status": "failed", "message": "session无效"}
                    target_dir = failed_dir
                    failed_count += 1
                    logger.warning(f"账号 {os.path.basename(session_file)} 未授权")
                else:
                    me = await client.get_me()
                    logger.info(f"开始处理账号 {me.phone} ({os.path.basename(session_file)})")

                    if not json_file:
                        generated_json = await generate_json_for_session(
                            session_file, client, me, final_api_id, final_api_hash, official_api
                        )
                        if generated_json:
                            json_file = generated_json

                    clean_results = await clean_account_operations(client, clean_type)
                    result = {
                        "session": os.path.basename(session_file),
                        "phone": me.phone if me else "unknown",
                        "status": "success",
                        "chats_deleted": clean_results["chats_deleted"],
                        "contacts_deleted": clean_results["contacts_deleted"],
                        "passkeys_deleted": clean_results["passkeys_deleted"],
                        "errors": clean_results["errors"]
                    }
                    target_dir = success_dir
                    success_count += 1
                    logger.info(f"账号 {me.phone} 清理完成: 对话={clean_results['chats_deleted']}, 联系人={clean_results['contacts_deleted']}, Passkey={clean_results['passkeys_deleted']}, 错误数={len(clean_results['errors'])}")

                results.append(result)
                shutil.copy2(session_file, os.path.join(target_dir, os.path.basename(session_file)))
                if json_file and os.path.exists(json_file):
                    shutil.copy2(json_file, os.path.join(target_dir, os.path.basename(json_file)))
                await asyncio.sleep(1)

            except FloodWaitError as e:
                logger.warning(f"账号 {os.path.basename(session_file)} 触发 FloodWait，需等待 {e.seconds} 秒")
                result = {"session": os.path.basename(session_file), "status": "failed", "message": f"等待{e.seconds}秒"}
                results.append(result)
                failed_count += 1
            except Exception as e:
                err_detail = traceback.format_exc()
                logger.error(f"处理账号 {os.path.basename(session_file)} 时出错: {e}\n{err_detail}")
                result = {"session": os.path.basename(session_file), "status": "failed", "message": str(e)[:100]}
                results.append(result)
                failed_count += 1
            finally:
                if client:
                    await client.disconnect()

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

        total_chats = sum(r.get("chats_deleted", 0) for r in results if r["status"] == "success")
        total_contacts = sum(r.get("contacts_deleted", 0) for r in results if r["status"] == "success")
        total_passkeys = sum(r.get("passkeys_deleted", 0) for r in results if r["status"] == "success")

        result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>清理账号完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• <tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 总账号: <b>{len(session_files)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: <b>{success_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: <b>{failed_count}</b>
• <tg-emoji emoji-id="5877307202888273539">💬</tg-emoji> 删除对话: <b>{total_chats}</b>
• <tg-emoji emoji-id="5877318502947229960">👥</tg-emoji> 删除联系人: <b>{total_contacts}</b>
• <tg-emoji emoji-id="5886505193180239900">🔑</tg-emoji> 删除Passkey: <b>{total_passkeys}</b>"""

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
                    filename=f"clean_success_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 清理成功 ({success_count}个)</b>",
                    parse_mode='HTML'
                )

        if failed_count > 0:
            with open(failed_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"clean_failed_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 清理失败 ({failed_count}个)</b>",
                    parse_mode='HTML'
                )

        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>清理账号任务完成</b>

<tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 用户: <code>{user_id}</code>
清理类型: {type_names[clean_type]}
<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 总账号: <b>{len(session_files)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: <b>{success_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: <b>{failed_count}</b>
• <tg-emoji emoji-id="5877307202888273539">💬</tg-emoji> 删除对话: <b>{total_chats}</b>
• <tg-emoji emoji-id="5877318502947229960">👥</tg-emoji> 删除联系人: <b>{total_contacts}</b>
• <tg-emoji emoji-id="5886505193180239900">🔑</tg-emoji> 删除Passkey: <b>{total_passkeys}</b>""",
                    parse_mode='HTML'
                )
                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                if success_count > 0:
                    with open(success_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"clean_success_{user_id}_{admin_timestamp}.zip"
                        )
                if failed_count > 0:
                    with open(failed_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"clean_failed_{user_id}_{admin_timestamp}.zip"
                        )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")

        try:
            await status_msg.delete()
        except:
            pass
