import asyncio
import logging
import os
import shutil
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from i18n import tr

CONCURRENCY = int(os.getenv("THREADS", "1"))
if CONCURRENCY < 1:
    CONCURRENCY = 1

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


async def run_batch(update, context, task, process_one, on_progress=None):
    _active_tasks[task.user_id] = task
    lang = lang_from_update(update) if 'lang_from_update' in globals() else "zh"

    try:
        from i18n import lang_from_update as _lfu
        lang = _lfu(update)
    except Exception:
        lang = "zh"

    stop_message = None
    try:
        stop_message = await context.bot.send_message(
            chat_id=task.chat_id,
            text=f"<tg-emoji emoji-id='5839200986022812209'>🔄</tg-emoji> {tr('task.running', lang)}",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(tr('task.stop_button', lang), callback_data="task_stop")
            ]])
        )
    except Exception as e:
        logger.error(f"发送停止按钮失败: {e}")

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
                    result = await process_one(item, task)
                    task.record_done(result)
                except Exception as e:
                    logger.error(f"[{task.module_name}] 单账号处理异常: {e}", exc_info=True)
                    task.record_done({"category": "_error", "name": repr(item)})
                if on_progress:
                    try:
                        await on_progress(task)
                    except Exception:
                        pass

    workers = [asyncio.create_task(worker()) for _ in range(CONCURRENCY)]
    try:
        await asyncio.gather(*workers)
    finally:
        task.stopped = task.stop_event.is_set()
        _active_tasks.pop(task.user_id, None)
        if stop_message:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=task.chat_id,
                    message_id=stop_message.message_id,
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


def arm_timeout(task, seconds):
    return asyncio.create_task(_watchdog(task, seconds))


import io
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


async def pack_and_send(context, update, task, categories, admins, lang,
                        result_text_fn=None, status_fn=None, pending_dir=None):
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
    for key, (dir_path, fname, cap) in categories.items():
        if cat_counts[key] > 0:
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
