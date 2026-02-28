import os
import zipfile
import shutil
import asyncio
import tempfile
import time
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError, UsernameNotOccupiedError
import logging

logger = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()
SHAIHUO_BACK = os.getenv("SHAIHUO_BACK", "").replace('\\n', '\n')

MAX_ZIP_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))

async def check_session_alive(session_file, json_file, api_id, api_hash):
    client = None
    try:
        client = TelegramClient(session_file, api_id, api_hash)
        await client.connect()
        
        if not await client.is_user_authorized():
            return False, "验证失效"
        
        me = await client.get_me()
        if not me:
            return False, "无法获取用户信息"
        
        try:
            user = await client.get_entity('@aaaa')
            if user:
                return True, "存活"
        except UsernameNotOccupiedError:
            return True, "存活"
        except FloodWaitError as e:
            return False, f"等待{e.seconds}秒"
        except Exception as e:
            error_str = str(e).lower()
            if "cannot find" in error_str or "username not found" in error_str:
                return True, "存活"
            elif "deactivated" in error_str or "banned" in error_str:
                return False, "账号被封"
            elif "flood" in error_str:
                return False, "触发限流"
            elif "peer_id_invalid" in error_str or "invite" in error_str:
                return True, "冻结"
            else:
                return True, "冻结"
        
    except SessionPasswordNeededError:
        return False, "2FA验证"
    except FloodWaitError as e:
        return False, f"等待{e.seconds}秒"
    except Exception as e:
        return False, f"错误:{str(e)[:20]}"
    finally:
        if client:
            await client.disconnect()

async def handle_shaihuo_document(update, context, user_id, user_states):
    document = update.message.document
    if not document.file_name.endswith('.zip'):
        from bot import create_back_button
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return
    
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5942826671290715541'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/shaihuo_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5942826671290715541'>🔍</tg-emoji> 开始处理筛活任务...",
            parse_mode='HTML'
        )
        await process_shaihuo(update, context, zip_path, user_id)
        try:
            os.remove(zip_path)
        except:
            pass
        
    except Exception as e:
        logger.error(f"处理文件失败: {e}")
        from bot import create_back_button
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    finally:
        user_states.pop(user_id, None)
        try:
            await status_msg.delete()
        except:
            pass

async def process_shaihuo(update, context, zip_path, user_id):
    from telegram import InlineKeyboardMarkup
    from bot import create_back_button
    file_size = os.path.getsize(zip_path)
    if file_size > MAX_ZIP_SIZE:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 文件过大，最大允许 {MAX_ZIP_SIZE//1024//1024}MB",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return
    
    # 获取API配置
    api_id_str = os.getenv("TELEGRAM_APP_ID")
    api_hash = os.getenv("TELEGRAM_APP_HASH")
    admins = os.getenv("ADMIN_ID", "").split(",")
    
    if not api_id_str or not api_hash:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 系统未配置，请联系管理员",
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
            text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> API配置错误，请联系管理员",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return
    
    try:
        await asyncio.wait_for(
            _process_shaihuo_internal(update, context, zip_path, user_id, api_id, api_hash, admins), 
            timeout=MAX_TASK_TIME
        )
    except asyncio.TimeoutError:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 任务执行超时 ({MAX_TASK_TIME}秒)",
            parse_mode='HTML',
            reply_markup=reply_markup
        )

async def _process_shaihuo_internal(update, context, zip_path, user_id, api_id, api_hash, admins):
    from telegram import InlineKeyboardMarkup
    from bot import create_back_button
    
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
        except Exception as e:
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 解压失败: {str(e)}",
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
                text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 未找到session文件",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return
        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5942826671290715541">🔍</tg-emoji> <b>筛活进行中</b>

找到 <b>{len(session_files)}</b> 个session文件
正在检测存活状态，请稍候...""",
            parse_mode='HTML'
        )
        alive_dir = os.path.join(temp_dir, "alive")
        frozen_dir = os.path.join(temp_dir, "frozen")
        dead_dir = os.path.join(temp_dir, "dead")
        os.makedirs(alive_dir, exist_ok=True)
        os.makedirs(frozen_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)
        alive_count = 0
        frozen_count = 0
        dead_count = 0
        for i, session_file in enumerate(session_files, 1):
            session_name = os.path.splitext(os.path.basename(session_file))[0]
            json_file = os.path.join(os.path.dirname(session_file), f"{session_name}.json")
            if not os.path.exists(json_file):
                json_file = None
            if i % 5 == 0 or i == len(session_files):
                try:
                    await status_msg.edit_text(
                        text=f"""<tg-emoji emoji-id="5942826671290715541">🔍</tg-emoji> <b>筛活进行中</b>

进度: {i}/{len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>存活: {alive_count} | <tg-emoji emoji-id="5985347654974967782">❄️</tg-emoji>冻结: {frozen_count} | <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失效: {dead_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass
            is_alive, reason = await check_session_alive(session_file, json_file, api_id, api_hash)
            if is_alive and reason == "存活":
                target_dir = alive_dir
                alive_count += 1
            elif is_alive and reason == "冻结":
                target_dir = frozen_dir
                frozen_count += 1
            else:
                target_dir = dead_dir
                dead_count += 1
            try:
                shutil.copy2(session_file, os.path.join(target_dir, os.path.basename(session_file)))
            except:
                pass
            
            if json_file and os.path.exists(json_file):
                try:
                    shutil.copy2(json_file, os.path.join(target_dir, os.path.basename(json_file)))
                except:
                    pass
            
            await asyncio.sleep(0.5)
        alive_zip = os.path.join(temp_dir, "alive.zip")
        if alive_count > 0:
            with zipfile.ZipFile(alive_zip, 'w') as zipf:
                for root, dirs, files in os.walk(alive_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, alive_dir)
                        zipf.write(file_path, arcname)
        
        frozen_zip = os.path.join(temp_dir, "frozen.zip")
        if frozen_count > 0:
            with zipfile.ZipFile(frozen_zip, 'w') as zipf:
                for root, dirs, files in os.walk(frozen_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, frozen_dir)
                        zipf.write(file_path, arcname)
        
        dead_zip = os.path.join(temp_dir, "dead.zip")
        if dead_count > 0:
            with zipfile.ZipFile(dead_zip, 'w') as zipf:
                for root, dirs, files in os.walk(dead_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, dead_dir)
                        zipf.write(file_path, arcname)
        
        # 发送统计结果
        result_text = f"""<tg-emoji emoji-id="5845955401916355857">✅</tg-emoji> <b>筛活完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• <tg-emoji emoji-id="5879770735999717115">👤</tg-emoji> 总账号: <b>{len(session_files)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 存活: <b>{alive_count}</b>
• <tg-emoji emoji-id="5985347654974967782">❄️</tg-emoji> 冻结: <b>{frozen_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失效: <b>{dead_count}</b>"""

        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=result_text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"发送结果失败: {e}")
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        if alive_count > 0:
            try:
                with open(alive_zip, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=f"alive_{timestamp}.zip",
                        caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 存活账号 ({alive_count}个)</b>",
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"发送存活zip失败: {e}")

        if frozen_count > 0:
            try:
                with open(frozen_zip, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=f"frozen_{timestamp}.zip",
                        caption=f"<b><tg-emoji emoji-id='5985347654974967782'>❄️</tg-emoji> 冻结账号 ({frozen_count}个)</b>",
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"发送冻结zip失败: {e}")

        if dead_count > 0:
            try:
                with open(dead_zip, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=f"dead_{timestamp}.zip",
                        caption=f"<b><tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 失效账号 ({dead_count}个)</b>",
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"发送失效zip失败: {e}")
        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue
                
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5771695636411847302">📢</tg-emoji> <b>筛活任务完成</b>

<tg-emoji emoji-id="5879770735999717115">👤</tg-emoji> 用户: <code>{user_id}</code>
<tg-emoji emoji-id="5764747792371160364">📊</tg-emoji> 总账号: <b>{len(session_files)}</b>
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 存活: <b>{alive_count}</b>
<tg-emoji emoji-id="5985347654974967782">❄️</tg-emoji> 冻结: <b>{frozen_count}</b>
<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失效: <b>{dead_count}</b>""",
                    parse_mode='HTML'
                )
                
                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                
                if alive_count > 0:
                    with open(alive_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"alive_{user_id}_{admin_timestamp}.zip",
                            caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 存活 ({alive_count})</b>",
                            parse_mode='HTML'
                        )
                
                if frozen_count > 0:
                    with open(frozen_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"frozen_{user_id}_{admin_timestamp}.zip",
                            caption=f"<b><tg-emoji emoji-id='5985347654974967782'>❄️</tg-emoji> 冻结 ({frozen_count})</b>",
                            parse_mode='HTML'
                        )
                
                if dead_count > 0:
                    with open(dead_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"dead_{user_id}_{admin_timestamp}.zip",
                            caption=f"<b><tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 失效 ({dead_count})</b>",
                            parse_mode='HTML'
                        )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")
        try:
            await status_msg.delete()
        except:
            pass
