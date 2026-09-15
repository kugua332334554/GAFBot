import os
import re
import asyncio
import tempfile
import random
import time
from datetime import datetime
import logging
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
from i18n import tr, lang_from_update, get_env_i18n
from task_engine import BatchTask, run_batch, arm_timeout
BACK_BUTTON_EMOJI_ID = "5877629862306385808"
CHECK_BAN_BACK = os.getenv("CHECK_BAN_BACK", "").replace('\\n', '\n')
MAX_PHONES = 100

_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60

user_ban_states = {}

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

def create_back_button(lang="zh"):
    return InlineKeyboardButton(tr("back_to_main", lang), callback_data="back_to_main").to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

async def show_check_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    lang = lang_from_update(update)
    await query.answer()

    keyboard = [[create_back_button(lang)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=get_env_i18n("CHECK_BAN_BACK", lang),
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    user_ban_states[user_id] = {"waiting": True}

async def handle_ban_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    lang = lang_from_update(update)
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("err.txt_required", lang),
            parse_mode=ParseMode.HTML
        )
        return

    file = await context.bot.get_file(document.file_id)
    txt_path = f"downloads/ban_{user_id}_{int(datetime.now().timestamp())}.txt"
    os.makedirs("downloads", exist_ok=True)
    await file.download_to_drive(txt_path)

    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            phones = [line.strip() for line in f if line.strip()]

        phones = [re.sub(r'\D', '', p) for p in phones if re.sub(r'\D', '', p)]
        phones = [f"+{p}" if not p.startswith('+') else p for p in phones]
        phones = list(dict.fromkeys(phones))[:MAX_PHONES]

        if not phones:
            await update.message.reply_text(
                "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("ban.no_valid_phone", lang),
                parse_mode=ParseMode.HTML
            )
            return

        await process_ban_check(update, context, user_id, phones)
    except Exception as e:
        logger.error(f"处理文件失败: {e}")
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> {tr('err.process_failed', lang)}: {str(e)}",
            parse_mode=ParseMode.HTML
        )
    finally:
        try: os.remove(txt_path)
        except: pass
        user_ban_states.pop(user_id, None)

async def process_ban_check(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str, phones: list):
    api_id = int(os.getenv("TELEGRAM_APP_ID"))
    api_hash = os.getenv("TELEGRAM_APP_HASH")
    admins = os.getenv("ADMIN_ID", "").split(",")
    lang = lang_from_update(update)

    status_msg = await update.message.reply_text(
        f"<tg-emoji emoji-id='5443127283898405358'>🔍</tg-emoji> {tr('ban.processing', lang)} {len(phones)} {tr('ban.phones', lang)}...",
        parse_mode=ParseMode.HTML
    )
    
    with tempfile.TemporaryDirectory() as temp_dir:
        list_lock = asyncio.Lock()

        async def on_progress(task):
            c = {"unbanned": 0, "banned": 0}
            for r in task.done:
                cat = r.get("category")
                if cat in c:
                    c[cat] += 1
            task._progress_text = (
                f"<tg-emoji emoji-id='5443127283898405358'>🔍</tg-emoji> {tr('ban.progress', lang)}: {task.completed}/{len(phones)}\n"
                f"<tg-emoji emoji-id='5922712343011135025'>🚫</tg-emoji> {tr('ban.banned', lang)}: {c['banned']} | "
                f"<tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> {tr('ban.normal', lang)}: {c['unbanned']}"
            )

        async def process_one(item, task):
            phone = item
            client = None
            result = "unbanned"
            try:
                proxy = get_random_proxy()
                proxy_dict = create_proxy_dict(proxy) if proxy else None
                client = TelegramClient(
                    tempfile.mktemp(dir=temp_dir),
                    api_id,
                    api_hash,
                    proxy=proxy_dict
                )
                await client.connect()
                try:
                    await client.send_code_request(phone)
                    result = "unbanned"
                except FloodWaitError as e:
                    result = "unbanned"
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    error_str = str(e).lower()
                    if "phone_number_invalid" in error_str or "phone_number_banned" in error_str:
                        result = "banned"
                    else:
                        result = "unbanned"
                await client.disconnect()
            except Exception as e:
                logger.error(f"检测 {phone} 失败: {e}")
                result = "unbanned"
                if client:
                    try: await client.disconnect()
                    except: pass
            return {"category": result, "phone": phone}

        task = BatchTask(user_id, update.effective_chat.id, phones, module_name="shaiban")
        wd = arm_timeout(task, int(os.getenv("TASK_TIMEOUT", "1800")))
        await run_batch(update, context, task, process_one, on_progress=on_progress)
        wd.cancel()

        banned = [r["phone"] for r in task.done if r.get("category") == "banned"]
        unbanned = [r["phone"] for r in task.done if r.get("category") == "unbanned"]
        pending = task.remaining_pending()

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        banned_file = os.path.join(temp_dir, "banned.txt")
        unbanned_file = os.path.join(temp_dir, "unbanned.txt")
        
        with open(banned_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(banned))
        with open(unbanned_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(unbanned))
        if pending:
            with open(unbanned_file, 'a', encoding='utf-8') as f:
                f.write('\n\n# 未完成 (已终止):\n' + '\n'.join(pending))

        stop_tag = " (已终止)" if (task.stopped or pending) else ""
        result_text = f"""<tg-emoji emoji-id='5909201569898827582'>✅</tg-emoji> <b>{tr('ban.done', lang)}</b>{stop_tag}

<tg-emoji emoji-id='5931472654660800739'>📊</tg-emoji> {tr('shaihuo.stats', lang)}:
• <tg-emoji emoji-id='5886412370347036129'>📱</tg-emoji> {tr('ban.total', lang)}: <b>{len(phones)}</b>
• <tg-emoji emoji-id='5922712343011135025'>🚫</tg-emoji> {tr('ban.banned_caption', lang)}: <b>{len(banned)}</b>
• <tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> {tr('ban.normal_caption', lang)}: <b>{len(unbanned)}</b>"""

        await update.message.reply_text(result_text, parse_mode=ParseMode.HTML)

        if banned:
            with open(banned_file, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"banned_{timestamp}.txt",
                    caption=f"<b><tg-emoji emoji-id='5922712343011135025'>🚫</tg-emoji> {tr('ban.banned_caption', lang)} ({len(banned)})</b>",
                    parse_mode=ParseMode.HTML
                )

        if unbanned:
            with open(unbanned_file, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"unbanned_{timestamp}.txt",
                    caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> {tr('ban.normal_caption', lang)} ({len(unbanned)})</b>",
                    parse_mode=ParseMode.HTML
                )
        
        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id: continue
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id='5909201569898827582'>📢</tg-emoji> <b>{tr('ban.task_done', lang)}</b>{stop_tag}

<tg-emoji emoji-id='5886412370347036129'>👤</tg-emoji> {tr('admin.user', lang)} ID: <code>{user_id}</code>
<tg-emoji emoji-id='5931472654660800739'>📊</tg-emoji> {tr('shaihuo.stats', lang)}:
• {tr('ban.total', lang)}: <b>{len(phones)}</b>
• <tg-emoji emoji-id='5922712343011135025'>🚫</tg-emoji> {tr('ban.banned', lang)}: <b>{len(banned)}</b>
• <tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> {tr('ban.normal', lang)}: <b>{len(unbanned)}</b>""",
                    parse_mode=ParseMode.HTML
                )

                if banned:
                    with open(banned_file, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"banned_{user_id}_{timestamp}.txt",
                            caption=f"<b><tg-emoji emoji-id='5922712343011135025'>🚫</tg-emoji> {tr('admin.user', lang)} {user_id} {tr('ban.banned_caption', lang)} ({len(banned)})</b>",
                            parse_mode=ParseMode.HTML
                        )

                if unbanned:
                    with open(unbanned_file, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"unbanned_{user_id}_{timestamp}.txt",
                            caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> {tr('admin.user', lang)} {user_id} {tr('ban.normal_caption', lang)} ({len(unbanned)})</b>",
                            parse_mode=ParseMode.HTML
                        )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")
        
        await status_msg.delete()
