# bot.py
import os
import logging
import re
import time
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from pay import OkayPay, add_order, remove_order, cleanup_expired_orders, ORDER_TIMEOUT, load_all_users, save_all_users
from shaihuo import process_shaihuo, handle_shaihuo_document, SHAIHUO_BACK
from login import LoginHandler, ACCOUNT_LOGIN_BACK
from xiugai2fa import (
    show_2fa_menu, handle_2fa_mode_selection, handle_2fa_text_input, 
    handle_2fa_document, CHANGE_2FA_BACK
)

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
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

if isinstance(ACCOUNT_LOGIN_BACK, str):
    ACCOUNT_LOGIN_BACK = ACCOUNT_LOGIN_BACK.replace('\\n', '\n')

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

user_states = {}
login_handlers = {}

def get_or_create_user(user):
    data = load_all_users()
    user_id_str = str(user.id)
    if user_id_str not in data:
        data[user_id_str] = {
            "id": user.id,
            "full_name": user.full_name,
            "username": user.username,
            "status": "free"
        }
        save_all_users(data)
    return data.get(user_id_str)

async def post_init(application):
    commands = [
        BotCommand("start", "开启机器人"),
    ]
    await application.bot.set_my_commands(commands)

def create_back_button():
    back_button = InlineKeyboardButton(
        "返回主菜单", 
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}
    return back_button

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    
    await query.answer()
    
    all_users = load_all_users()
    user_data = all_users.get(user_id, {})
    
    # 非VIP禁止使用任何功能（除了返回主菜单）
    if user_data.get("status") != "vip" and data not in ["back_to_main"]:
        await query.edit_message_text(
            text=UN_ACTIVE_MSG,
            parse_mode=ParseMode.HTML
        )
        return
    
    if data == "back_to_main":
        await start(update, context)
        return
    
    if data == "check_active":
        formatted_text = SHAIHUO_BACK.replace('\\n', '\n') if isinstance(SHAIHUO_BACK, str) else SHAIHUO_BACK
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=formatted_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        user_states[user_id] = "waiting_shaihuo"
        
    elif data == "account_login":
        formatted_text = ACCOUNT_LOGIN_BACK.replace('\\n', '\n') if isinstance(ACCOUNT_LOGIN_BACK, str) else "📱 账号登陆功能\n\n输入手机号，发送验证码，返回Session+Json协议包"
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=formatted_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        login_handlers[user_id] = LoginHandler(user_id, update.effective_chat.id)
        user_states[user_id] = "waiting_phone"
        
    elif data == "change_2fa":
        await show_2fa_menu(update, context)
        
    elif data in ["2fa_input_mode", "2fa_auto_mode", "2fa_force_mode"]:
        await handle_2fa_mode_selection(update, context)
        
    elif data in ["merge_packs", "test_bidirectional", "kick_devices", 
                "privacy_config", "format_convert", "convert_api", "prevent_recovery", 
                "check_ban", "check_material", "clean_account", "unpack_tool"]:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text="""<b><tg-emoji emoji-id='5881702736843511327'>⚠️</tg-emoji> 功能维护中</b>

很抱歉，该功能正在升级维护，暂时无法使用。
请稍后再试，感谢您的理解与支持！

<tg-emoji emoji-id='5843553939672274145'>🕐</tg-emoji> 预计恢复时间：请关注通知""",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    
    all_users = load_all_users()
    user_data = all_users.get(user_id, {})
    
    if '2fa_state' in context.user_data:
        await handle_2fa_text_input(update, context)
        return
    
    # 非VIP禁止使用任何功能
    if user_data.get("status") != "vip":
        await update.message.reply_text(UN_ACTIVE_MSG, parse_mode=ParseMode.HTML)
        return
    
    if user_id not in user_states:
        return
    
    state = user_states.get(user_id)
    
    if state == "waiting_phone":
        phone = text.strip()
        if not re.match(r'^\+?[0-9]{7,15}$', phone):
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 手机号格式错误，请重新发送",
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

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    all_users = load_all_users()
    user_data = all_users.get(user_id, {})
    
    if '2fa_state' in context.user_data and context.user_data['2fa_state'] == "waiting_2fa_zip":
        await handle_2fa_document(update, context)
        return
    
    # 非VIP禁止使用任何功能
    if user_data.get("status") != "vip":
        await update.message.reply_text(UN_ACTIVE_MSG, parse_mode=ParseMode.HTML)
        return
    
    state = user_states.get(user_id)
    if state == "waiting_shaihuo":
        await handle_shaihuo_document(update, context, user_id, user_states)

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
                text=f"""<tg-emoji emoji-id="5900104897885376843">⏰</tg-emoji> <b>订单已过期</b>

订单号：<code>{platform_order_id}</code>
有效时间：5分钟

如需继续购买，请重新发送 /start 选择支付。""",
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
            success_text = f"""<tg-emoji emoji-id="5825794181183836432">✔️</tg-emoji> <b>支付成功！</b>

<tg-emoji emoji-id="5765017520612315383">❤️</tg-emoji> 感谢您的支持，您已成为 VIP 用户
<tg-emoji emoji-id="6005843436479975944">🔁</tg-emoji> 请重新发送 /start 使用功能

订单号：<code>{platform_order_id}</code>"""
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
        await update.message.reply_text('<tg-emoji emoji-id="5987583383021034169">💰</tg-emoji> 支付系统配置错误，请联系管理员。', parse_mode="HTML")
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
        
        keyboard = [[InlineKeyboardButton(f"立即支付 {OKPAY_COST} {OKPAY_PAYED}", url=pay_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"""{UN_ACTIVE_MSG}

<tg-emoji emoji-id="5994636050033545139">🪧</tg-emoji><b>订单详情：</b>
<tg-emoji emoji-id="5967548335542767952">💳</tg-emoji>订单号：<code>{platform_order_id}</code>
<tg-emoji emoji-id="5992430854909989581">🪙</tg-emoji>金额：{OKPAY_COST} {OKPAY_PAYED}
<tg-emoji emoji-id="5900104897885376843">⏰</tg-emoji>过期时间：{expire_time} (5分钟有效)

<tg-emoji emoji-id="5900104897885376843">🕓</tg-emoji> 正在等待支付结果，请在完成支付后稍等片刻...
<tg-emoji emoji-id="5994636050033545139">⚠️</tg-emoji> 订单5分钟后自动过期，过期后需重新生成""",
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
        "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 无法生成支付链接，请联系管理员或稍后再试。",
        parse_mode=ParseMode.HTML
    )

async def set_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        return

    if not context.args:
        await update.message.reply_text("用法: /vip 用户ID")
        return

    target_id = context.args[0]
    data = load_all_users()
    
    if target_id in data:
        data[target_id]["status"] = "vip"
        save_all_users(data)
        await update.message.reply_text(f'<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 用户 {target_id} 已升级为 VIP。', parse_mode="HTML")
    else:
        await update.message.reply_text('<tg-emoji emoji-id="5886496611835581345">❌</tg-emoji> 找不到该用户，请确认对方是否已通过 /start 注册。', parse_mode="HTML")

async def remove_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("用法: /unvip 用户ID")
        return
    target_id = context.args[0]
    data = load_all_users()
    if target_id in data:
        data[target_id]["status"] = "free"
        save_all_users(data)
        text = f'<tg-emoji emoji-id="5886496611835581345">👤</tg-emoji> 用户 {target_id} 已降级为普通用户。'
    else:
        text = '<tg-emoji emoji-id="5922712343011135025">🚫</tg-emoji> 找不到该用户。'

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if update.callback_query:
        message = update.callback_query.message
        chat_id = message.chat_id
        user = update.callback_query.from_user
    else:
        message = update.message
        chat_id = update.effective_chat.id
        user = update.effective_user
    
    user_data = get_or_create_user(user)
    joined = await is_user_joined(context, user.id)
    
    if JOIN_ID and not joined:
        clean_username = JOIN_ID.replace('@', '')
        invite_link = f"https://t.me/{clean_username}"
        keyboard = [[InlineKeyboardButton("点击加入频道/群组", url=invite_link)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        join_msg = os.getenv("START_JOIN_MESSAGE", "请先加入频道后再使用。").replace('\\n', '\n')
        
        if update.callback_query:
            await update.callback_query.edit_message_text(join_msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(join_msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        return

    if user_data.get("status") == "vip":
        text = START_MESSAGE_TEMPLATE.replace("{USER}", user.full_name)
        text = re.sub(r'\* (.*)', r'* <code>\1</code>', text)
        
        def btn(text, data, emoji_id):
            return InlineKeyboardButton(text, callback_data=data).to_dict() | {"icon_custom_emoji_id": emoji_id}

        keyboard = [
            [btn("账号筛活", "check_active", "5942826671290715541"), 
             btn("账号登陆", "account_login", "5920090136627908485")],
            [btn("修改2FA", "change_2fa", "6005570495603282482"),
             btn("整合号包", "merge_packs", "5877307202888273539")],
            [btn("双向测试", "test_bidirectional", "5922612721244704425"), 
             btn("踢其他设备", "kick_devices", "5877318502947229960")],
            [btn("隐私配置", "privacy_config", "5931409969613116639"),
             btn("格式互转", "format_convert", "6005843436479975944")],
            [btn("转API", "convert_api", "5877597667231534929"),
             btn("防止找回", "prevent_recovery", "5870734657384877785")],
            [btn("号码筛BAN", "check_ban", "5922712343011135025"),
             btn("筛料能力", "check_material", "5944940516754853337")],
            [btn("清理账号", "clean_account", "6007942490076745785"),
             btn("拆包工具", "unpack_tool", "5877540355187937244")]
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

if __name__ == '__main__':
    os.makedirs("downloads", exist_ok=True)
    cleanup_expired_orders()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CommandHandler("vip", set_vip))
    app.add_handler(CommandHandler("unvip", remove_vip))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.job_queue.run_repeating(periodic_order_cleanup, interval=60, first=10)
    
    print("Bot 正在运行...")
    app.run_polling()
