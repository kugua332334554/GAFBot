import os
import logging
import re
import time
import asyncio
from datetime import datetime
from collections import defaultdict
from asyncio import Queue
from dotenv import load_dotenv
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from pay import OkayPay, add_order, remove_order, cleanup_expired_orders, ORDER_TIMEOUT, load_all_users, save_all_users
from shaihuo import process_shaihuo, handle_shaihuo_document, SHAIHUO_BACK
from login import LoginHandler, QrLoginHandler, ACCOUNT_LOGIN_BACK
from xiugai2fa import (
    show_2fa_menu, handle_2fa_mode_selection, handle_2fa_text_input, 
    handle_2fa_document, CHANGE_2FA_BACK
)
from zhenghe import show_merge_packs, handle_merge_document, confirm_merge, user_merge_sessions
from tishebei import show_kick_devices, handle_kick_document, user_kick_states, KICK_DEVICES_BACK
from shuangxiang import show_bidirectional, handle_bidirectional_document, user_bidirectional_states, TEST_BIDIRECTIONAL_BACK
from yinsi import (
    show_privacy_config, handle_privacy_selection, handle_privacy_option,
    handle_privacy_confirm_upload, handle_privacy_reset_all, handle_privacy_document,
    user_privacy_states
)
from huzhuan import (
    show_convert_menu, handle_convert_selection, handle_convert_document,
    FORMAT_CONVERT_BACK, user_convert_states
)
from zhuanapi import (
    show_convert_api, handle_api_mode, handle_api_text, handle_api_document,
    user_api_states, CONVERT_API_BACK
)
from qingli import (
    show_clean_menu, handle_clean_document, user_clean_states, CLEAN_ACCOUNT_BACK,
    handle_clean_selection
)
from shailiao import (
    show_material_menu, handle_material_document, user_material_states
)
from chaibao import show_unpack_menu, handle_unpack_document, handle_unpack_format, user_unpack_states, UNPACK_TOOL_BACK
from shaiban import show_check_ban, handle_ban_document, user_ban_states
from fangzhaohui import (
    show_prevent_recovery, handle_recovery_document, handle_recovery_2fa_input,
    handle_recovery_skip, user_recovery_states
)
from task_engine import task_stop_callback
from xiaohui import handle_destroy_document, DESTROY_BACK
from passkey import (
    show_passkey_menu, handle_passkey_selection, handle_passkey_document,
    user_passkey_states, PASSKEY_BACK
)
from shaireg import handle_regtime_document
from i18n import tr, resolve_lang, lang_from_update, get_env_i18n
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
RAW_MESSAGE = os.getenv("START_MESSAGE")
START_MESSAGE_TEMPLATE = RAW_MESSAGE.replace('\\n', '\n') if RAW_MESSAGE else ""
RAW_MESSAGE_UN = os.getenv("START_MESSAGE_UN")
UN_ACTIVE_MSG = RAW_MESSAGE_UN.replace('\\n', '\n') if RAW_MESSAGE_UN else ""
JOIN_ID = os.getenv("START_JOIN_USERNAME") 
ADMIN_ID = os.getenv("ADMIN_ID")
OKPAY_ID = os.getenv("OKPAY_ID")
OKPAY_TOKEN = os.getenv("OKPAY_TOKEN")
OKPAY_PAYED = os.getenv("OKPAY_PAYED")
OKPAY_COST = os.getenv("OKPAY_COST")
MERGE_PACKS_BACK = os.getenv("MERGE_PACKS_BACK", "").replace('\\n', '\n')
BACK_BUTTON_EMOJI_ID = "5877629862306385808"
REGTIME_BACK = os.getenv("REGTIME_BACK", "").replace('\\n', '\n')
if isinstance(ACCOUNT_LOGIN_BACK, str):
    ACCOUNT_LOGIN_BACK = ACCOUNT_LOGIN_BACK.replace('\\n', '\n')

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

user_states = {}
login_handlers = {}
user_qr_handlers = {}
user_queues = defaultdict(Queue)
user_tasks = {} 
queue_locks = defaultdict(asyncio.Lock)
broadcast_message = None
broadcast_users = []

def get_or_create_user(user):
    data = load_all_users()
    user_id_str = str(user.id)
    if user_id_str not in data:
        default_status = "vip" if not OKPAY_COST else "free"
        data[user_id_str] = {
            "id": user.id,
            "full_name": user.full_name,
            "username": user.username,
            "status": default_status,
            "lang": resolve_lang(getattr(user, "language_code", None))
        }
        save_all_users(data)
    return data.get(user_id_str)

def lang_for_user_id(user_id):
    """根据用户 ID 查语言，返回 'zh'|'tw'|'en'。"""
    try:
        data = load_all_users()
        return data.get(str(user_id), {}).get("lang") or "zh"
    except Exception:
        return "zh"

async def show_lang_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = lang_from_update(update)
    def btn(text, data):
        return InlineKeyboardButton(text, callback_data=data)
    keyboard = [
        [btn(tr("lang.zh", lang), "lang_zh")],
        [btn(tr("lang.tw", lang), "lang_tw")],
        [btn(tr("lang.en", lang), "lang_en")],
        [create_back_button(lang)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=tr("lang.choose", lang),
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def post_init(application):
    commands = [
        BotCommand("start", tr("cmd.start")),
        BotCommand("lang", tr("menu.language").strip()),
    ]
    await application.bot.set_my_commands(commands)

def create_back_button(lang="zh"):
    back_button = InlineKeyboardButton(
        tr("back_to_main", lang),
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}
    return back_button

async def message_queue_processor(user_id: str):
    try:
        while True:
            try:
                msg_type, update, context = await asyncio.wait_for(
                    user_queues[user_id].get(), 
                    timeout=60
                )
                
                try:
                    if msg_type == 'callback':
                        await process_button_callback(update, context)
                    elif msg_type == 'message':
                        await process_handle_message(update, context)
                    elif msg_type == 'document':
                        await process_handle_document(update, context)
                except Exception as e:
                    logger.error(f"处理用户 {user_id} 消息失败: {e}", exc_info=True)
                    try:
                        if update.callback_query:
                            await update.callback_query.message.reply_text(
                                "<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> " + tr("err.process_failed_retry", lang_from_update(update)),
                                parse_mode=ParseMode.HTML
                            )
                        elif update.message:
                            await update.message.reply_text(
                                "<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> " + tr("err.process_failed_retry", lang_from_update(update)),
                                parse_mode=ParseMode.HTML
                            )
                    except:
                        pass
                
                user_queues[user_id].task_done()
                
            except asyncio.TimeoutError:
                async with queue_locks[user_id]:
                    if user_queues[user_id].empty():
                        if user_id in user_tasks:
                            del user_tasks[user_id]
                        if user_id in user_queues and user_queues[user_id].empty():
                            del user_queues[user_id]
                        break
                    continue
            except asyncio.CancelledError:
                logger.info(f"用户 {user_id} 的队列处理器被取消")
                break
            except Exception as e:
                logger.error(f"队列处理器未知错误 {user_id}: {e}", exc_info=True)
                continue
    finally:
        if user_id in queue_locks:
            del queue_locks[user_id]

async def ensure_queue_processor(user_id: str):
    async with queue_locks[user_id]:
        if user_id not in user_tasks or user_tasks[user_id].done():
            user_tasks[user_id] = asyncio.create_task(
                message_queue_processor(user_id),
                name=f"queue_processor_{user_id}"
            )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    await user_queues[user_id].put(('callback', update, context))
    await ensure_queue_processor(user_id)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    await user_queues[user_id].put(('message', update, context))
    await ensure_queue_processor(user_id)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    await user_queues[user_id].put(('document', update, context))
    await ensure_queue_processor(user_id)

async def process_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data

    await query.answer()

    all_users = load_all_users()
    user_data = all_users.get(user_id, {})

    if user_data.get("status") != "vip" and data not in ["back_to_main"]:
        await query.edit_message_text(
            text=get_env_i18n("START_MESSAGE_UN", lang_from_update(update)),
            parse_mode=ParseMode.HTML
        )
        return

    if data == "back_to_main":
        await start(update, context)
        return

    if data in ("lang_zh", "lang_tw", "lang_en"):
        new_lang = data.split("_")[1]
        data_map = load_all_users()
        if user_id in data_map:
            data_map[user_id]["lang"] = new_lang
            save_all_users(data_map)
        await start(update, context)
        return

    if data == "show_lang":
        await show_lang_menu(update, context)
        return

    if data == "check_active":
        lang = lang_from_update(update)
        formatted_text = get_env_i18n("SHAIHUO_BACK", lang)
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text=formatted_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        user_states[user_id] = "waiting_shaihuo"

    elif data == "account_login":
        lang = lang_from_update(update)
        def btn(text, data, emoji_id):
            return InlineKeyboardButton(text, callback_data=data).to_dict() | {"icon_custom_emoji_id": emoji_id}

        keyboard = [
            [btn(tr("login.phone", lang), "phone_login", "5877316724830768997")],
            [btn(tr("login.qr", lang), "qr_login", "5877318502947229960")],
            [create_back_button(lang)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text=tr("login.select_method", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

    elif data == "phone_login":
        lang = lang_from_update(update)
        formatted_text = get_env_i18n("ACCOUNT_LOGIN_BACK", lang)
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text=formatted_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        login_handlers[user_id] = LoginHandler(user_id, update.effective_chat.id)
        user_states[user_id] = "waiting_phone"

    elif data == "check_regtime":
        lang = lang_from_update(update)
        formatted_text = get_env_i18n("REGTIME_BACK", lang)
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=formatted_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        user_states[user_id] = "waiting_regtime_zip"


    elif data == "qr_login":
        await query.answer()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="<tg-emoji emoji-id='5877318502947229960'>📱</tg-emoji> " + tr("login.qr_generating", lang_from_update(update)),
            parse_mode=ParseMode.HTML
        )
        def on_cleanup():
            user_qr_handlers.pop(user_id, None)
        qr_handler = QrLoginHandler(user_id, update.effective_chat.id, context, cleanup_callback=on_cleanup)
        user_qr_handlers[user_id] = qr_handler
        asyncio.create_task(qr_handler.start_qr_login(update, context))
        user_states[user_id] = "waiting_qr_login"

    elif data == "change_2fa":
        await show_2fa_menu(update, context)

    elif data in ["2fa_input_mode", "2fa_auto_mode"]:
        await handle_2fa_mode_selection(update, context)

    elif data == "merge_packs":
        await show_merge_packs(update, context, MERGE_PACKS_BACK, user_states)

    elif data == "confirm_merge":
        await confirm_merge(update, context, user_states)

    elif data == "kick_devices":
        await show_kick_devices(update, context)

    elif data == "test_bidirectional":
        await show_bidirectional(update, context)

    elif data == "privacy_config":
        await show_privacy_config(update, context)

    elif data in ["privacy_phone", "privacy_last_seen", "privacy_forward", "privacy_profile_photo"]:
        await handle_privacy_selection(update, context)

    elif data in ["privacy_set_everyone", "privacy_set_contacts", "privacy_set_nobody"]:
        await handle_privacy_option(update, context)

    elif data == "privacy_confirm_upload":
        await handle_privacy_confirm_upload(update, context)

    elif data == "privacy_reset_all":
        await handle_privacy_reset_all(update, context)

    elif data == "format_convert":
        await show_convert_menu(update, context)

    elif data in ["convert_session_to_tdata", "convert_tdata_to_session"]:
        await handle_convert_selection(update, context)

    elif data == "convert_api":
        await show_convert_api(update, context)

    elif data in ["api_no_2fa", "api_manual_2fa", "api_from_json"]:
        await handle_api_mode(update, context)

    elif data == "clean_account":
        await show_clean_menu(update, context)

    elif data in ["clean_chats", "clean_contacts", "clean_all"]:
        await handle_clean_selection(update, context)

    elif data == "clean_passkeys":
        await handle_clean_selection(update, context)

    elif data == "check_material":
        await show_material_menu(update, context)

    elif data == "check_ban":
        await show_check_ban(update, context)

    elif data == "prevent_recovery":
        await show_prevent_recovery(update, context)

    elif data == "unpack_tool":
        await show_unpack_menu(update, context)

    elif data == "recovery_skip_2fa":
        await handle_recovery_skip(update, context)

    elif data == "destroy_session":
        lang = lang_from_update(update)
        formatted_text = get_env_i18n("DESTROY_BACK", lang)
        keyboard = [[create_back_button(lang)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=formatted_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        user_states[user_id] = "waiting_destroy_zip"

    elif data == "passkey_menu":
        await show_passkey_menu(update, context)

    elif data in ["passkey_create", "passkey_login"]:
        await handle_passkey_selection(update, context)

async def process_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    if user_id in user_qr_handlers:
        handler = user_qr_handlers[user_id]
        if hasattr(handler, 'waiting_for_2fa') and handler.waiting_for_2fa:
            await handler.submit_2fa(update, context, text.strip())
            return
    if user_states.get(user_id) == "waiting_qr_login":
        await update.message.reply_text(
            "<tg-emoji emoji-id='5877318502947229960'>📱</tg-emoji> " + tr("login.qr_scan_hint", lang_from_update(update)),
            parse_mode=ParseMode.HTML
        )
        return
    if user_id in user_recovery_states and user_recovery_states[user_id].get("state") == "waiting_2fa":
        await handle_recovery_2fa_input(update, context)
        return
    if '2fa_state' in context.user_data:
        await handle_2fa_text_input(update, context)
        return
    if user_id in user_api_states and user_api_states[user_id].get("waiting_2fa"):
        await handle_api_text(update, context)
        return
    if user_id in user_unpack_states and user_unpack_states[user_id].get("waiting_format"):
        await handle_unpack_format(update, context)
        return
    all_users = load_all_users()
    user_data = all_users.get(user_id, {})
    if user_data.get("status") != "vip":
        await update.message.reply_text(get_env_i18n("START_MESSAGE_UN", lang_from_update(update)), parse_mode=ParseMode.HTML)
        return
    if user_states.get(user_id) == "waiting_regtime_zip":
        await handle_regtime_document(update, context, user_id, InlineKeyboardMarkup([[create_back_button(lang_from_update(update))]]))
        user_states.pop(user_id, None)
        return
    if user_id not in user_states:
        return

    state = user_states.get(user_id)

    if state == "waiting_phone":
        phone = re.sub(r'\s+', '', text.strip())
        if not re.match(r'^\+?[0-9]{7,15}$', phone):
            keyboard = [[create_back_button(lang_from_update(update))]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> " + tr("login.phone_invalid", lang_from_update(update)),
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return
        handler = login_handlers.get(user_id)
        if handler:
            await handler.handle_phone(update, context, phone)
            user_states[user_id] = "waiting_code"

    elif state == "waiting_code":
        code = text.strip()
        handler = login_handlers.get(user_id)
        if handler:
            result = await handler.handle_code(update, context, code)
            if result is True:
                user_states.pop(user_id, None)
                login_handlers.pop(user_id, None)
            elif result is False:
                user_states[user_id] = "waiting_2fa"
            else:
                user_states.pop(user_id, None)
                login_handlers.pop(user_id, None)

    elif state == "waiting_2fa":
        password = text.strip()
        handler = login_handlers.get(user_id)
        if handler:
            result = await handler.handle_2fa(update, context, password)
            if result:
                user_states.pop(user_id, None)
                login_handlers.pop(user_id, None)
    
    
                
async def process_handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    state = user_states.get(user_id)

    if user_states.get(user_id) == "waiting_qr_login":
        await update.message.reply_text(
            " " + tr("login.qr_scan_hint", lang_from_update(update)),
            parse_mode=ParseMode.HTML
        )
        return

    if user_id in user_recovery_states and user_recovery_states[user_id].get("state") == "waiting_zip":
        await handle_recovery_document(update, context, user_id)
        return

    if user_id in user_ban_states:
        await handle_ban_document(update, context, user_id)
        return

    if '2fa_state' in context.user_data and context.user_data['2fa_state'] == "waiting_2fa_zip":
        await handle_2fa_document(update, context)
        return

    if user_id in user_convert_states and user_convert_states[user_id].get("waiting_zip"):
        await handle_convert_document(update, context, user_id)
        return

    if user_id in user_api_states and user_api_states[user_id].get("waiting_zip"):
        await handle_api_document(update, context, user_id)
        return

    if user_id in user_clean_states and user_clean_states[user_id].get("waiting_zip"):
        await handle_clean_document(update, context, user_id)
        return

    if user_id in user_unpack_states and user_unpack_states[user_id].get("waiting_zip"):
        await handle_unpack_document(update, context, user_id)
        return

    if user_id in user_passkey_states and user_passkey_states[user_id].get("waiting_zip"):
        await handle_passkey_document(update, context, user_id)
        return

    if user_states.get(user_id) == "waiting_destroy_zip":
        await handle_destroy_document(update, context, user_id)
        user_states.pop(user_id, None)
        return

    all_users = load_all_users()
    user_data = all_users.get(user_id, {})
    if user_data.get("status") != "vip":
        await update.message.reply_text(get_env_i18n("START_MESSAGE_UN", lang_from_update(update)), parse_mode=ParseMode.HTML)
        return

    if state == "waiting_regtime_zip":
        await handle_regtime_document(update, context, user_id, InlineKeyboardMarkup([[create_back_button(lang_from_update(update))]]))
        user_states.pop(user_id, None)
        return

    if state == "waiting_shaihuo":
        await handle_shaihuo_document(update, context, user_id, user_states)
    elif state == "waiting_merge_packs":
        await handle_merge_document(update, context, user_id)
    elif state == "waiting_kick_zip" or user_id in user_kick_states:
        await handle_kick_document(update, context, user_id)
    elif state == "waiting_bidirectional_zip" or user_id in user_bidirectional_states:
        await handle_bidirectional_document(update, context, user_id)
    elif user_id in user_privacy_states and user_privacy_states[user_id].get("waiting_zip"):
        await handle_privacy_document(update, context, user_id)
    elif state == "waiting_material_zip" or user_id in user_material_states:
        await handle_material_document(update, context, user_id)
    else:
        keyboard = [[create_back_button(lang_from_update(update))]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("err.upload_after_select", lang_from_update(update)),
            parse_mode='HTML',
            reply_markup=reply_markup
        )


async def check_pay_status(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    platform_order_id = job.data.get('order_id')
    user_id = job.data.get('user_id')
    created_time = job.data.get('time')
    chat_id = job.data.get('chat_id')
    
    current_time = time.time()
    time_elapsed = current_time - created_time
    
    if time_elapsed > ORDER_TIMEOUT:
        logger.info(f"订单 {platform_order_id} 已过期")
        remove_order(platform_order_id)
        
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"""<tg-emoji emoji-id="5900104897885376843">⏰</tg-emoji> <b>{tr("pay.order_expired", lang_for_user_id(user_id))}</b>

{tr("pay.order_no", lang_for_user_id(user_id))}：<code>{platform_order_id}</code>
{tr("pay.valid_time", lang_for_user_id(user_id))}

{tr("pay.resend_start", lang_for_user_id(user_id))}""",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"发送过期提醒失败: {e}")
        
        job.schedule_removal()
        return
    
    pay_client = OkayPay(OKPAY_ID, OKPAY_TOKEN)
    is_paid = pay_client.check_order(platform_order_id)

    if is_paid:
        logger.info(f"订单 {platform_order_id} 支付成功")
        
        data = load_all_users()
        user_str = str(user_id)
        if user_str in data:
            data[user_str]["status"] = "vip"
            save_all_users(data)
        
        remove_order(platform_order_id)
        
        try:
            l = lang_for_user_id(user_id)
            success_text = f"""<tg-emoji emoji-id="5825794181183836432">✔️</tg-emoji> <b>{tr("pay.pay_success", l)}</b>

<tg-emoji emoji-id="5765017520612315383">❤️</tg-emoji> {tr("pay.thanks", l)}
<tg-emoji emoji-id="6005843436479975944">🔁</tg-emoji> {tr("pay.resend_start", l)}

{tr("pay.order_no", l)}：<code>{platform_order_id}</code>"""
            await context.bot.send_message(chat_id=chat_id, text=success_text, parse_mode='HTML')
        except Exception as e:
            logger.error(f"发送成功消息失败: {e}")
        
        job.schedule_removal()

async def periodic_order_cleanup(context: ContextTypes.DEFAULT_TYPE):
    expired_orders = cleanup_expired_orders()
    if expired_orders:
        logger.info(f"定期清理了 {len(expired_orders)} 个过期订单")

async def is_user_joined(context, user_id):
    if not JOIN_ID:
        return True
    try:
        chat_target = JOIN_ID if str(JOIN_ID).startswith(('@', '-100')) else f"@{JOIN_ID}"
        member = await context.bot.get_chat_member(chat_id=chat_target, user_id=user_id)
        return member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except Exception as e:
        logger.error(f"检查加入状态失败: {e}")
        return False

async def send_payment_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if not (OKPAY_ID and OKPAY_TOKEN):
        await update.message.reply_text('<tg-emoji emoji-id="5987583383021034169">💰</tg-emoji> ' + tr("pay.config_error", lang_from_update(update)), parse_mode="HTML")
        return
    
    pay_client = OkayPay(OKPAY_ID, OKPAY_TOKEN)
    local_unique_id = f"VIP_{user.id}_{int(time.time())}"
    pay_url, platform_order_id = pay_client.get_pay_link(
        unique_id=local_unique_id,
        amount=OKPAY_COST,
        coin=OKPAY_PAYED,
        name=f"VIP Membership - {user.id}"
    )
    
    if pay_url and platform_order_id:
        add_order(platform_order_id, user.id, update.effective_chat.id, time.time())
        
        expire_time = datetime.fromtimestamp(time.time() + ORDER_TIMEOUT).strftime('%H:%M:%S')
        
        keyboard = [[InlineKeyboardButton(f"{tr('pay.pay_now', lang_from_update(update))} {OKPAY_COST} {OKPAY_PAYED}", url=pay_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        l = lang_from_update(update)
        await update.message.reply_text(
            f"""{get_env_i18n('START_MESSAGE_UN', l)}

<tg-emoji emoji-id="5994636050033545139">🪧</tg-emoji><b>{tr('pay.order_detail', l)}：</b>
<tg-emoji emoji-id="5967548335542767952">💳</tg-emoji>{tr('pay.order_no', l)}：<code>{platform_order_id}</code>
<tg-emoji emoji-id="5992430854909989581">🪙</tg-emoji>{tr('pay.amount', l)}：{OKPAY_COST} {OKPAY_PAYED}
<tg-emoji emoji-id="5900104897885376843">⏰</tg-emoji>{tr('pay.expire', l)}：{expire_time} (5分钟有效)

<tg-emoji emoji-id="5900104897885376843">🕓</tg-emoji> {tr('pay.waiting_result', l)}
<tg-emoji emoji-id="5994636050033545139">⚠️</tg-emoji> {tr('pay.auto_expire', l)}""",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        context.job_queue.run_repeating(
            check_pay_status, 
            interval=5,
            first=3,
            data={
                'order_id': platform_order_id, 
                'user_id': user.id,
                'chat_id': update.effective_chat.id,
                'time': time.time()
            },
            name=f"pay_check_{platform_order_id}"
        )
    else:
        await update.message.reply_text(
        "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> " + tr("pay.gen_fail", lang_from_update(update)),
        parse_mode=ParseMode.HTML
    )

async def set_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        return

    if not context.args:
        await update.message.reply_text(tr("admin.usage_vip", lang_from_update(update)))
        return

    target_id = context.args[0]
    data = load_all_users()

    if target_id in data:
        data[target_id]["status"] = "vip"
        save_all_users(data)
        await update.message.reply_text(f'<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> {tr("admin.user", lang_from_update(update))} {target_id} {tr("admin.vip_set", lang_from_update(update))}。', parse_mode="HTML")
    else:
        await update.message.reply_text(f'<tg-emoji emoji-id="5886496611835581345">❌</tg-emoji> {tr("admin.user_not_found", lang_from_update(update))}，{tr("join.required", lang_from_update(update))}', parse_mode="HTML")

async def remove_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text(tr("admin.usage_unvip", lang_from_update(update)))
        return
    target_id = context.args[0]
    data = load_all_users()
    if target_id in data:
        data[target_id]["status"] = "free"
        save_all_users(data)
        text = f'<tg-emoji emoji-id="5886496611835581345">👤</tg-emoji> {tr("admin.user", lang_from_update(update))} {target_id} {tr("admin.downgraded", lang_from_update(update))}。'
    else:
        text = f'<tg-emoji emoji-id="5922712343011135025">🚫</tg-emoji> {tr("admin.user_not_found", lang_from_update(update))}。'

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        return
    global broadcast_message, broadcast_users
    broadcast_message = None
    data = load_all_users()
    broadcast_users = list(data.keys())
    await update.message.reply_text(tr("admin.broadcast_to_users", lang_from_update(update)).format(n=len(broadcast_users)))
    context.user_data["awaiting_broadcast"] = True

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID or not context.user_data.get("awaiting_broadcast"):
        return
    global broadcast_message, broadcast_users
    broadcast_message = update.message
    context.user_data["awaiting_broadcast"] = False
    await update.message.reply_text(tr("admin.broadcast_per_second", lang_from_update(update)).format(n=len(broadcast_users)))
    asyncio.create_task(send_broadcast(context))

async def send_broadcast(context: ContextTypes.DEFAULT_TYPE):
    global broadcast_message, broadcast_users
    if not broadcast_message or not broadcast_users:
        return
    success = 0
    fail = 0
    for i, uid in enumerate(broadcast_users):
        try:
            await broadcast_message.copy(chat_id=uid)
            success += 1
        except Exception as e:
            logger.error(f"广播失败 {uid}: {e}")
            fail += 1
        if (i + 1) % 20 == 0:
            await asyncio.sleep(1)
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"{tr('admin.broadcast_done', 'zh')}：{tr('admin.success', 'zh')} {success}，{tr('admin.fail', 'zh')} {fail}"
    )
    broadcast_message = None
    broadcast_users = []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    user_states.pop(user_id, None)
    login_handlers.pop(user_id, None)
    user_qr_handlers.pop(user_id, None)
    user_merge_sessions.pop(user_id, None)
    user_kick_states.pop(user_id, None)
    user_bidirectional_states.pop(user_id, None)
    user_privacy_states.pop(user_id, None)
    user_convert_states.pop(user_id, None)
    user_api_states.pop(user_id, None)
    user_clean_states.pop(user_id, None)
    user_material_states.pop(user_id, None)
    user_ban_states.pop(user_id, None)
    user_recovery_states.pop(user_id, None)
    user_unpack_states.pop(user_id, None)
    context.user_data.clear()
    
    if update.callback_query:
        message = update.callback_query.message
        chat_id = message.chat_id
        user = update.callback_query.from_user
    else:
        message = update.message
        chat_id = update.effective_chat.id
        user = update.effective_user
    
    user_data = get_or_create_user(user)
    lang = user_data.get("lang") or resolve_lang(getattr(user, "language_code", None))
    joined = await is_user_joined(context, user.id)

    if JOIN_ID and not joined:
        clean_username = JOIN_ID.replace('@', '')
        invite_link = f"https://t.me/{clean_username}"
        keyboard = [[InlineKeyboardButton(tr("join.click_join", lang), url=invite_link)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        join_msg = get_env_i18n("START_JOIN_MESSAGE", lang) or tr("join.required", lang)

        if update.callback_query:
            await update.callback_query.edit_message_text(join_msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(join_msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        return

    if user_data.get("status") == "vip":
        text = get_env_i18n("START_MESSAGE", lang) or START_MESSAGE_TEMPLATE
        text = text.replace("{USER}", user.full_name)
        text = re.sub(r'\* (.*)', r'* <code>\1</code>', text)

        def btn(text, data, emoji_id):
            return InlineKeyboardButton(text, callback_data=data).to_dict() | {"icon_custom_emoji_id": emoji_id}

        keyboard = [
            [btn(tr("menu.check_active", lang), "check_active", "5942826671290715541"),
             btn(tr("menu.account_login", lang), "account_login", "5920090136627908485")],
            [btn(tr("menu.change_2fa", lang), "change_2fa", "6005570495603282482"),
             btn(tr("menu.merge_packs", lang), "merge_packs", "5877307202888273539")],
            [btn(tr("menu.test_bidirectional", lang), "test_bidirectional", "5922612721244704425"),
             btn(tr("menu.kick_devices", lang), "kick_devices", "5877318502947229960")],
            [btn(tr("menu.privacy_config", lang), "privacy_config", "5931409969613116639"),
             btn(tr("menu.format_convert", lang), "format_convert", "6005843436479975944")],
            [btn(tr("menu.convert_api", lang), "convert_api", "5877597667231534929"),
             btn(tr("menu.prevent_recovery", lang), "prevent_recovery", "5870734657384877785")],
            [btn(tr("menu.check_ban", lang), "check_ban", "5922712343011135025"),
             btn(tr("menu.check_material", lang), "check_material", "5944940516754853337")],
            [btn(tr("menu.clean_account", lang), "clean_account", "6007942490076745785"),
             btn(tr("menu.unpack_tool", lang), "unpack_tool", "5877540355187937244")],
            [btn(tr("menu.destroy_session", lang), "destroy_session", "5879937509579820068"),
             btn(tr("menu.passkey", lang), "passkey_menu", "6008118472066732010")],
            [btn(tr("menu.regtime", lang), "check_regtime", "5900104897885376843")],
            [InlineKeyboardButton(tr("menu.language", lang), callback_data="show_lang")],
        ]
        
        for i in range(1, 4):
            ads_str = os.getenv(f"ADS_{i}")
            if ads_str and "-" in ads_str:
                try:
                    ads_text, ads_url = ads_str.split("-", 1)
                    keyboard.append([InlineKeyboardButton(text=ads_text.strip(), url=ads_url.strip())])
                except ValueError:
                    continue
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(text=text.strip(), parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text=text.strip(), parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    else:
        if update.callback_query:
            await send_payment_prompt(update, context)
        else:
            await send_payment_prompt(update, context)

async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/lang 命令：直接进入语言切换菜单。"""
    user = update.effective_user
    get_or_create_user(user)
    data = load_all_users()
    lang = data.get(str(user.id), {}).get("lang") or resolve_lang(getattr(user, "language_code", None))
    def btn(text, data_name):
        return InlineKeyboardButton(text, callback_data=data_name)
    keyboard = [
        [btn(tr("lang.zh", lang), "lang_zh")],
        [btn(tr("lang.tw", lang), "lang_tw")],
        [btn(tr("lang.en", lang), "lang_en")],
    ]
    await update.message.reply_text(
        text=tr("lang.choose", lang),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

if __name__ == '__main__':
    os.makedirs("downloads", exist_ok=True)
    os.makedirs("acd", exist_ok=True)
    cleanup_expired_orders()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lang", lang_command))
    app.add_handler(CommandHandler("vip", set_vip))
    app.add_handler(CommandHandler("unvip", remove_vip))
    app.add_handler(CommandHandler("gb", broadcast))
    app.add_handler(CallbackQueryHandler(task_stop_callback, pattern="^task_stop$"))
    app.add_handler(CallbackQueryHandler(button_callback))

    async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if user_id == ADMIN_ID and context.user_data.get("awaiting_broadcast"):
            await handle_broadcast_message(update, context)
        else:
            await handle_message(update, context)
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.job_queue.run_repeating(periodic_order_cleanup, interval=60, first=10)
    
    print("Bot 正在运行...")
    app.run_polling()
