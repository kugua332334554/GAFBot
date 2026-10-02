import asyncio
import logging
import os
import re
import shutil
from collections import Counter
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from i18n import tr

CONCURRENCY = int(os.getenv("THREADS", "1"))
if CONCURRENCY < 1:
    CONCURRENCY = 1

# 单个账号处理的硬超时(秒)。连不上的账号会被取消并记为失败,不再拖垮整批。
ACCOUNT_TIMEOUT = int(os.getenv("ACCOUNT_TIMEOUT", "120"))
if ACCOUNT_TIMEOUT < 1:
    ACCOUNT_TIMEOUT = 1

_active_tasks = {}


class BatchTask:
    def __init__(self, user_id, chat_id, accounts, module_name="task"):
        self.user_id = user_id
        self.chat_id = chat_id
        self.accounts = list(accounts)
        self.module_name = module_name
        self.stop_event = asyncio.Event()
        self.semaphore = asyncio.Semaphore(CONCURRENCY)
        self.done = []
        self._index = 0
        self.started = 0
        self.completed = 0
        self.stopped = False
        self._workers = []

    def next_account(self):
        if self.stop_event.is_set():
            return None
        if self._index >= len(self.accounts):
            return None
        item = self.accounts[self._index]
        self._index += 1
        self.started += 1
        return item

    def record_done(self, result):
        if result is None:
            result = {}
        self.done.append(result)
        self.completed += 1

    def remaining_pending(self):
        return self.accounts[self._index:]

    @property
    def total(self):
        return len(self.accounts)


async def run_batch(update, context, task, process_one, on_progress=None, progress_text_fn=None):
    _active_tasks[task.user_id] = task
    lang = "zh"
    try:
        from i18n import lang_from_update as _lfu
        lang = _lfu(update)
    except Exception:
        pass

    stop_btn = InlineKeyboardMarkup([[
        InlineKeyboardButton(tr('task.stop_button', lang), callback_data="task_stop")
    ]])

    async def refresh_progress():
        if not hasattr(task, "_progress_msg") or task._progress_msg is None:
            return
        body = getattr(task, "_progress_text", None)
        if body is None and progress_text_fn:
            try:
                body = progress_text_fn(task)
            except Exception:
                body = None
        if body is None:
            body = f"<tg-emoji emoji-id='5839200986022812209'>🔄</tg-emoji> {tr('task.running', lang)}"
        if body == getattr(task, "_last_progress_body", None):
            return
        try:
            await context.bot.edit_message_text(
                chat_id=task.chat_id,
                message_id=task._progress_msg.message_id,
                text=body,
                parse_mode='HTML',
                reply_markup=stop_btn
            )
            task._last_progress_body = body
        except Exception:
            pass

    task._progress_msg = None
    try:
        task._progress_msg = await context.bot.send_message(
            chat_id=task.chat_id,
            text=f"<tg-emoji emoji-id='5839200986022812209'>🔄</tg-emoji> {tr('task.running', lang)}",
            parse_mode='HTML',
            reply_markup=stop_btn
        )
    except Exception as e:
        logger.error(f"发送进度消息失败: {e}")

    async def worker():
        while True:
            if task.stop_event.is_set():
                return
            item = task.next_account()
            if item is None:
                return
            async with task.semaphore:
                if task.stop_event.is_set():
                    return
                try:
                    result = await asyncio.wait_for(process_one(item, task), timeout=ACCOUNT_TIMEOUT)
                    task.record_done(result)
                except asyncio.TimeoutError:
                    logger.error(f"[{task.module_name}] 单账号处理超时(>{ACCOUNT_TIMEOUT}s): {item!r}")
                    task.record_done({"category": "_error", "name": repr(item)})
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"[{task.module_name}] 单账号处理异常: {e}", exc_info=True)
                    task.record_done({"category": "_error", "name": repr(item)})
                if on_progress:
                    try:
                        await on_progress(task)
                    except Exception:
                        pass

    workers = [asyncio.create_task(worker()) for _ in range(CONCURRENCY)]
    task._workers = workers

    async def progress_pump():
        try:
            while True:
                all_done = all(w.done() for w in workers)
                await refresh_progress()
                if all_done:
                    break
                await asyncio.sleep(1.5)
        except Exception:
            pass

    pump_task = asyncio.create_task(progress_pump())

    try:
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        task.stopped = task.stop_event.is_set()
        _active_tasks.pop(task.user_id, None)
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        if task._progress_msg:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=task.chat_id,
                    message_id=task._progress_msg.message_id,
                    reply_markup=None
                )
            except Exception:
                pass
    return task


STOP_CALLBACK_DATA = "task_stop"


async def task_stop_callback(update, context):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    lang = "zh"
    try:
        from i18n import lang_from_update as _lfu
        lang = _lfu(update)
    except Exception:
        pass
    if request_stop(user_id):
        try:
            await query.edit_message_text(
                f"<tg-emoji emoji-id='5846008814129649022'>⏸️</tg-emoji> {tr('task.stopping', lang)}",
                parse_mode='HTML'
            )
        except Exception:
            pass
    else:
        try:
            await query.edit_message_text(
                f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> {tr('task.no_running', lang)}",
                parse_mode='HTML'
            )
        except Exception:
            pass


def request_stop(user_id):
    t = _active_tasks.get(user_id)
    if t and not t.stop_event.is_set():
        t.stop_event.set()
        return True
    return False


def get_active_task(user_id):
    return _active_tasks.get(user_id)


async def _watchdog(task, seconds):
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        return
    task.stop_event.set()
    for w in list(task._workers):
        w.cancel()


def arm_timeout(task, seconds):
    return asyncio.create_task(_watchdog(task, seconds))


import io
import math
import zipfile
import shutil as _shutil


def zip_dir(src_dir, dst_zip):
    if not os.path.isdir(src_dir) or not os.listdir(src_dir):
        return False
    with zipfile.ZipFile(dst_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(src_dir):
            for f in files:
                fp = os.path.join(root, f)
                zf.write(fp, os.path.relpath(fp, src_dir))
    return True


# ---------------- 错误分类 / 结果包命名 ----------------

ERR_DEAD = "死号"
ERR_CODE_SEND = "发送验证码错误"
ERR_2FA_RESET_FAIL = "2fa错误_重置2fa失败"
ERR_2FA_CUR_WRONG = "2fa错误_当前密码错误"
ERR_2FA_NO_OLD = "2fa错误_无旧密码"
ERR_DUP_LOGIN = "重复登录"
ERR_UNKNOWN = "未知错误"

_DEAD_CLASSES = {"AuthKeyUnregisteredError", "AuthKeyInvalidError", "AuthKeyPermEmptyError",
                 "SessionRevokedError", "SessionExpiredError", "UserDeactivatedError",
                 "UserDeactivatedBanError", "PhoneNumberUnoccupiedError", "PhoneNotOccupiedError"}
_DEAD_CODES = ("AUTH_KEY_UNREGISTERED", "AUTH_KEY_INVALID", "AUTH_KEY_PERM_EMPTY",
               "SESSION_REVOKED", "SESSION_EXPIRED", "USER_DEACTIVATED",
               "PHONE_NUMBER_UNOCCUPIED", "PHONE_NOT_OCCUPIED")

_2FA_FAIL_CLASSES = {"SessionPasswordNeededError", "PasswordEmptyError",
                     "SrpPasswordChangedError", "PhonePasswordProtectedError", "PhonePasswordFloodError",
                     "PasswordRecoveryNaError", "PasswordRecoveryExpiredError", "EmailUnconfirmedError",
                     "EmailHashExpiredError", "EmailVerifyExpiredError", "CodeInvalidError",
                     "TmpPasswordInvalidError", "TmpPasswordDisabledError"}
_2FA_FAIL_CODES = ("SESSION_PASSWORD_NEEDED", "PASSWORD_EMPTY",
                   "SRP_PASSWORD_CHANGED", "PHONE_PASSWORD_PROTECTED", "PHONE_PASSWORD_FLOOD",
                   "PASSWORD_RECOVERY_NA", "PASSWORD_RECOVERY_EXPIRED", "EMAIL_UNCONFIRMED",
                   "EMAIL_HASH_EXPIRED", "EMAIL_VERIFY_EXPIRED", "CODE_INVALID",
                   "TMP_PASSWORD_INVALID", "TMP_PASSWORD_DISABLED")

_CODE_SEND_CLASSES = {"PhoneNumberInvalidError", "PhoneNumberFloodError", "SmsCodeCreateFailedError",
                      "PhoneCodeHashEmptyError", "CodeEmptyError", "PhoneNumberAppSignupForbiddenError",
                      "SessionTooFreshError", "PhoneCodeExpiredError", "PhoneCodeInvalidError",
                      "CodeHashInvalidError", "AuthBytesInvalidError"}
_CODE_SEND_CODES = ("PHONE_NUMBER_INVALID", "PHONE_NUMBER_FLOOD", "SMS_CODE_CREATE_FAILED",
                    "PHONE_CODE_HASH_EMPTY", "CODE_EMPTY", "PHONE_NUMBER_APP_SIGNUP_FORBIDDEN",
                    "SESSION_TOO_FRESH", "PHONE_CODE_EXPIRED", "PHONE_CODE_INVALID",
                    "CODE_HASH_INVALID", "AUTH_BYTES_INVALID")

_DUP_CLASSES = {"AuthKeyDuplicatedError", "AuthRestartError", "PasskeyAuthRestartError"}
_DUP_CODES = ("AUTH_KEY_DUPLICATED", "AUTH_RESTART")


def classify_rpc_error(err, stage="login"):
    """把 Telethon RPCError(或任意异常)归类为 6 类中文标签。已冻结不经 RPC 判定。"""
    name = type(err).__name__
    msg = str(err)
    up = (name + " " + msg).upper()

    if name == "TwoFaConfirmWaitError" or "2FA_CONFIRM_WAIT" in up:
        secs = getattr(err, "seconds", None)
        if secs is None:
            m = re.search(r'CONFIRM_WAIT_(\d+)', up) or re.search(r'(\d+)\s*SECONDS', up)
            secs = int(m.group(1)) if m else None
        if secs:
            days = max(1, math.ceil(int(secs) / 86400))
            return f"2fa错误_已重置还剩{days}天"
        return ERR_2FA_RESET_FAIL

    if name in _DEAD_CLASSES or any(c in up for c in _DEAD_CODES):
        return ERR_DEAD
    if name in _DUP_CLASSES or any(c in up for c in _DUP_CODES):
        return ERR_DUP_LOGIN
    if name == "PasswordHashInvalidError" or "PASSWORD_HASH_INVALID" in up:
        return ERR_2FA_CUR_WRONG
    if name in _2FA_FAIL_CLASSES or any(c in up for c in _2FA_FAIL_CODES):
        return ERR_2FA_RESET_FAIL
    if name in _CODE_SEND_CLASSES or any(c in up for c in _CODE_SEND_CODES):
        return ERR_CODE_SEND
    if name == "FloodWaitError" or "FLOOD_WAIT" in up:
        return ERR_CODE_SEND if stage == "sendcode" else ERR_UNKNOWN
    return ERR_UNKNOWN


def sanitize_filename_part(s):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(s)).strip()
    return s or "task"


def place_failed(failed_dir, label, files, subdir_name=None):
    """把失败账号文件放进 failed_dir/<label>/ 子目录，供 pack_and_send 按错误类型拆包。"""
    d = os.path.join(failed_dir, sanitize_filename_part(label))
    if subdir_name:
        d = os.path.join(d, subdir_name)
    os.makedirs(d, exist_ok=True)
    for fp in files:
        if fp and os.path.exists(fp):
            _shutil.copy2(fp, os.path.join(d, os.path.basename(fp)))
    return sanitize_filename_part(label)


async def send_task_end(context, chat_id):
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="任务结束！~\n点击 /start 开始下一个任务."
        )
    except Exception as e:
        logger.error(f"发送任务结束消息失败: {e}")


async def pack_and_send(context, update, task, categories, admins, lang,
                        result_text_fn=None, status_fn=None, pending_dir=None,
                        zip_base=None, send_end=True, extra_names=None):
    cat_counts = {k: 0 for k in categories}
    for r in task.done:
        c = r.get("category")
        if c in cat_counts:
            cat_counts[c] += 1
    pending_count = len(task.remaining_pending())
    stop_tag = " (已终止)" if task.stopped else ""

    if status_fn:
        try:
            await status_fn(task, cat_counts, pending_count)
        except Exception:
            pass

    if result_text_fn:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=result_text_fn(cat_counts, pending_count) + stop_tag,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"发送统计失败: {e}")

    tmp_parent = os.path.dirname(list(categories.values())[0][0])
    zips_to_send = []

    base = sanitize_filename_part(zip_base) if zip_base else None
    fail_label_counts = Counter()
    if base and task:
        for r in task.done:
            c = str(r.get("category") or "")
            if c.startswith("fail"):
                fail_label_counts[sanitize_filename_part(r.get("error") or ERR_UNKNOWN)] += 1

    for key, (dir_path, fname, cap) in categories.items():
        if cat_counts[key] > 0:
            if base and key == "success":
                zpath = os.path.join(tmp_parent, f"_succ_{key}.zip")
                if zip_dir(dir_path, zpath):
                    real_cap = cap(cat_counts[key]) if callable(cap) else str(cap)
                    zips_to_send.append((zpath, f"{base}_{cat_counts[key]}_成功.zip", real_cap))
            elif base and key.startswith("fail"):
                named = (extra_names or {}).get(key)
                if named:
                    zpath = os.path.join(tmp_parent, f"_fail_{key}.zip")
                    if zip_dir(dir_path, zpath):
                        real_cap = cap(cat_counts[key]) if callable(cap) else str(cap)
                        zips_to_send.append((zpath, f"{base}_{cat_counts[key]}_{named}.zip", real_cap))
                    continue
                subdirs = sorted(
                    d for d in os.listdir(dir_path)
                    if os.path.isdir(os.path.join(dir_path, d)) and os.listdir(os.path.join(dir_path, d))
                )
                loose = [f for f in os.listdir(dir_path) if not os.path.isdir(os.path.join(dir_path, f))]
                labels = list(subdirs)
                if loose:
                    loose_dir = os.path.join(dir_path, "_未知错误")
                    os.makedirs(loose_dir, exist_ok=True)
                    for f in loose:
                        _shutil.move(os.path.join(dir_path, f), os.path.join(loose_dir, f))
                    labels.append("_未知错误")
                if labels:
                    for label in labels:
                        label_clean = sanitize_filename_part(label.lstrip('_'))
                        zpath = os.path.join(tmp_parent, f"_fail_{label}.zip")
                        if zip_dir(os.path.join(dir_path, label), zpath):
                            n = fail_label_counts.get(label) or fail_label_counts.get(label_clean) or len(
                                os.listdir(os.path.join(dir_path, label)))
                            real_cap = cap(n) if callable(cap) else str(cap)
                            zips_to_send.append((zpath, f"{base}_{n}_失败_{label_clean}.zip", real_cap))
                else:
                    zpath = os.path.join(tmp_parent, f"_fail_{key}.zip")
                    if zip_dir(dir_path, zpath):
                        real_cap = cap(cat_counts[key]) if callable(cap) else str(cap)
                        zips_to_send.append((zpath, f"{base}_{cat_counts[key]}_失败_{ERR_UNKNOWN}.zip", real_cap))
            elif base and (extra_names or {}).get(key):
                zpath = os.path.join(tmp_parent, f"_x_{key}.zip")
                if zip_dir(dir_path, zpath):
                    real_cap = cap(cat_counts[key]) if callable(cap) else str(cap)
                    zips_to_send.append((zpath, f"{base}_{cat_counts[key]}_{extra_names[key]}.zip", real_cap))
            else:
                zpath = os.path.join(tmp_parent, f"{key}.zip")
                if zip_dir(dir_path, zpath):
                    real_cap = cap(cat_counts[key]) if callable(cap) else str(cap)
                    zips_to_send.append((zpath, fname, real_cap))

    if pending_count > 0 and pending_dir and os.path.isdir(pending_dir) and os.listdir(pending_dir):
        pz = os.path.join(tmp_parent, "pending.zip")
        if zip_dir(pending_dir, pz):
            zips_to_send.append((pz, "pending.zip", tr("shaihuo.pending_caption", lang)))

    for zpath, fname, cap in zips_to_send:
        try:
            with open(zpath, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=fname,
                    caption=str(cap) + stop_tag,
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"发送zip失败 {fname}: {e}")

    for admin_id in admins:
        admin_id = admin_id.strip()
        if not admin_id:
            continue
        try:
            if result_text_fn:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=result_text_fn(cat_counts, pending_count).replace("{USER}", str(task.user_id)) + stop_tag,
                    parse_mode='HTML'
                )
            for zpath, fname, cap in zips_to_send:
                try:
                    with open(zpath, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=fname,
                            caption=str(cap) + stop_tag,
                            parse_mode='HTML'
                        )
                except Exception as e:
                    logger.error(f"发给管理员zip失败 {fname}: {e}")
        except Exception as e:
            logger.error(f"发给管理员 {admin_id} 失败: {e}")

    if base and send_end:
        await send_task_end(context, update.effective_chat.id)
