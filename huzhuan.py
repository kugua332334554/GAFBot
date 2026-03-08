import os
import logging
import zipfile
import shutil
import tempfile
import json
import asyncio
from typing import Optional, Union, Tuple, List
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from opentele.td import TDesktop
from opentele.api import UseCurrentSession
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

FORMAT_CONVERT_BACK = os.getenv("FORMAT_CONVERT_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

user_convert_states = {}

def create_back_button():
    return InlineKeyboardButton(
        "返回主菜单", 
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

async def show_convert_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [
            InlineKeyboardButton("Session → Tdata", callback_data="convert_session_to_tdata").to_dict() | {"icon_custom_emoji_id": "5877307202888273539"},
            InlineKeyboardButton("Tdata → Session", callback_data="convert_tdata_to_session").to_dict() | {"icon_custom_emoji_id": "6005570495603282482"}
        ],
        [create_back_button()]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=FORMAT_CONVERT_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def handle_convert_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    await query.answer()
    
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if data == "convert_session_to_tdata":
        await query.edit_message_text(
            text="""<tg-emoji emoji-id="5877307202888273539">🔄</tg-emoji> <b>Session → Tdata 转换</b>

请上传包含 <code>.session</code> 文件的ZIP压缩包

<tg-emoji emoji-id="5775887550262546277">📌</tg-emoji> 转换后将返回包含tdata文件夹的ZIP包""",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        user_convert_states[user_id] = {
            "mode": "session_to_tdata",
            "waiting_zip": True
        }
        
    elif data == "convert_tdata_to_session":
        await query.edit_message_text(
            text="""<tg-emoji emoji-id="6005570495603282482">🔄</tg-emoji> <b>Tdata → Session 转换</b>

请上传包含 <code>tdata</code> 文件夹的ZIP压缩包

<tg-emoji emoji-id="5775887550262546277">📌</tg-emoji> 转换后将返回包含session+json的ZIP包""",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        user_convert_states[user_id] = {
            "mode": "tdata_to_session",
            "waiting_zip": True
        }

def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

def read_2fa_from_folder(folder_path: str) -> Optional[str]:
    for file in os.listdir(folder_path):
        if file.lower() in ['2fa.txt', '2fa', 'password.txt']:
            try:
                with open(os.path.join(folder_path, file), 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except:
                pass
    return None

async def convert_session_to_tdata(session_path: str, output_dir: str, twofa: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    client = None
    try:
        client = TelegramClient(session_path)
        await client.connect()
        
        if not await client.is_user_authorized():
            return False, "session未授权", None
        
        me = await client.get_me()
        if not me:
            return False, "无法获取用户信息", None
        
        tdesk = await client.ToTDesktop(flag=UseCurrentSession)
        
        account_name = me.phone or str(me.id)
        account_dir = os.path.join(output_dir, account_name)
        tdata_dir = os.path.join(account_dir, "tdata")
        os.makedirs(tdata_dir, exist_ok=True)
        
        tdesk.SaveTData(tdata_dir)
        
        if twofa:
            with open(os.path.join(account_dir, "2fa.txt"), 'w') as f:
                f.write(twofa)
        
        return True, account_name, account_dir
        
    except Exception as e:
        return False, str(e), None
    finally:
        if client:
            await client.disconnect()

async def convert_tdata_to_session(tdata_dir: str, output_dir: str, twofa: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    try:
        tdesk = TDesktop(tdata_dir)
        if not tdesk.isLoaded():
            return False, "tdata文件无法加载", None
        
        session_name = f"account.session"
        session_path = os.path.join(output_dir, session_name)
        
        client = await tdesk.ToTelethon(session=session_path, flag=UseCurrentSession)
        
        await client.connect()
        if not await client.is_user_authorized():
            return False, "会话未授权", None
        
        me = await client.get_me()
        if not me:
            return False, "无法获取用户信息", None
        
        phone = me.phone
        if not phone:
            return False, "无法获取手机号", None
        
        new_session_path = os.path.join(output_dir, f"{phone}.session")
        if os.path.exists(session_path):
            shutil.move(session_path, new_session_path)
        
        account_info = {
            "phone": phone,
            "2fa": twofa,
            "username": me.username,
            "id": me.id,
            "first_name": me.first_name,
            "last_name": me.last_name
        }
        
        json_path = os.path.join(output_dir, f"{phone}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(account_info, f, ensure_ascii=False, indent=2)
        
        await client.disconnect()
        return True, phone, output_dir
        
    except Exception as e:
        return False, str(e), None

async def handle_convert_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    
    if user_id not in user_convert_states:
        return
    
    state = user_convert_states[user_id]
    if not state.get("waiting_zip"):
        return
    
    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_convert_states.pop(user_id, None)
        return
    
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    
    zip_path = None
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/convert_{user_id}_{int(datetime.now().timestamp())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔄</tg-emoji> 开始处理转换任务...",
            parse_mode='HTML'
        )
        
        mode = state["mode"]
        
        if mode == "session_to_tdata":
            await process_session_to_tdata(update, context, zip_path, user_id)
        else:
            await process_tdata_to_session(update, context, zip_path, user_id)
        
    except Exception as e:
        logger.error(f"转换失败: {e}")
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    finally:
        user_convert_states.pop(user_id, None)
        if zip_path and os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except:
                pass
        try:
            await status_msg.delete()
        except:
            pass

async def process_session_to_tdata(update: Update, context: ContextTypes.DEFAULT_TYPE, zip_path: str, user_id: str):
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
                
                extracted_size = get_total_size(extract_dir)
                if extracted_size > MAX_EXTRACT_SIZE:
                    raise Exception(f"文件过大 ({extracted_size//1024//1024}MB > {MAX_EXTRACT_SIZE//1024//1024}MB)")
        except Exception as e:
            raise Exception(f"解压失败: {e}")
        
        session_files = []
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    session_files.append(os.path.join(root, file))
        
        if not session_files:
            raise Exception("未找到session文件")
        
        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>Session转Tdata进行中</b>

找到 <b>{len(session_files)}</b> 个session文件
正在转换，请稍候...""",
            parse_mode='HTML'
        )
        
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        
        success_count = 0
        failed_count = 0
        failed_details = []
        
        for i, session_file in enumerate(session_files, 1):
            session_dir = os.path.dirname(session_file)
            twofa = read_2fa_from_folder(session_dir)
            
            success, result, account_dir = await convert_session_to_tdata(
                session_file, output_dir, twofa
            )
            
            if success:
                success_count += 1
            else:
                failed_count += 1
                failed_details.append(f"{os.path.basename(session_file)}: {result}")
            
            if i % 3 == 0 or i == len(session_files):
                try:
                    await status_msg.edit_text(
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>Session转Tdata进行中</b>

进度: {i}/{len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {success_count}
<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失败: {failed_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass
        
        zip_filename = f"tdata_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, output_dir)
                    zipf.write(file_path, arcname)
        
        result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>转换完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• 总文件: {len(session_files)}
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: {success_count}
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: {failed_count}"""
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=result_text,
            parse_mode='HTML'
        )
        
        if success_count > 0:
            with open(zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=zip_filename,
                    caption=f"""<b><tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> Session转Tdata完成 ({success_count}个)</b>""",
                    parse_mode='HTML'
                )
        
        if failed_details:
            failed_text = "失败详情:\n" + "\n".join(failed_details[:10])
            if len(failed_details) > 10:
                failed_text += f"\n...等{len(failed_details)}个失败"
            
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"<tg-emoji emoji-id='5922712343011135025'>⚠️</tg-emoji> {failed_text}",
                parse_mode='HTML'
            )
        
        try:
            await status_msg.delete()
        except:
            pass

async def process_tdata_to_session(update: Update, context: ContextTypes.DEFAULT_TYPE, zip_path: str, user_id: str):
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
                
                extracted_size = get_total_size(extract_dir)
                if extracted_size > MAX_EXTRACT_SIZE:
                    raise Exception(f"文件过大 ({extracted_size//1024//1024}MB > {MAX_EXTRACT_SIZE//1024//1024}MB)")
        except Exception as e:
            raise Exception(f"解压失败: {e}")
        
        tdata_dirs = set()
        for root, dirs, files in os.walk(extract_dir):
            if os.path.basename(root) == 'tdata':
                if any(f in files for f in ['key_datas', 'map']):
                    tdata_dirs.add(root)
            elif 'tdata' in dirs:
                potential_tdata = os.path.join(root, 'tdata')
                if os.path.exists(potential_tdata):
                    sub_files = os.listdir(potential_tdata)
                    if any(f in sub_files for f in ['key_datas', 'map']):
                        tdata_dirs.add(potential_tdata)
        
        tdata_dirs = list(tdata_dirs)
        
        if not tdata_dirs:
            raise Exception("未找到有效的tdata文件夹")
        
        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>Tdata转Session进行中</b>

找到 <b>{len(tdata_dirs)}</b> 个tdata文件夹
正在转换，请稍候...""",
            parse_mode='HTML'
        )
        
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        
        success_count = 0
        failed_count = 0
        failed_details = []
        
        for i, tdata_dir in enumerate(tdata_dirs, 1):
            parent_dir = os.path.dirname(tdata_dir)
            twofa = read_2fa_from_folder(parent_dir)
            
            account_output = os.path.join(output_dir, f"account_{i}")
            os.makedirs(account_output, exist_ok=True)
            
            success, result, account_dir = await convert_tdata_to_session(
                tdata_dir, account_output, twofa
            )
            
            if success:
                success_count += 1
            else:
                failed_count += 1
                failed_details.append(f"tdata_{i}: {result}")
            
            if i % 3 == 0 or i == len(tdata_dirs):
                try:
                    await status_msg.edit_text(
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>Tdata转Session进行中</b>

进度: {i}/{len(tdata_dirs)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {success_count}
<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失败: {failed_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass
        
        zip_filename = f"session_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, output_dir)
                    zipf.write(file_path, arcname)
        
        result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>转换完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• 总文件夹: {len(tdata_dirs)}
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: {success_count}
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: {failed_count}"""
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=result_text,
            parse_mode='HTML'
        )
        
        if success_count > 0:
            with open(zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=zip_filename,
                    caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> Tdata转Session完成 ({success_count}个)</b>",
                    parse_mode='HTML'
                )
        
        if failed_details:
            failed_text = "失败详情:\n" + "\n".join(failed_details[:10])
            if len(failed_details) > 10:
                failed_text += f"\n...等{len(failed_details)}个失败"
            
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"<tg-emoji emoji-id='5922712343011135025'>⚠️</tg-emoji> {failed_text}",
                parse_mode='HTML'
            )
        
        try:
            await status_msg.delete()
        except:
            pass
0
