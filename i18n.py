# -*- coding: utf-8 -*-
"""
GAFBot 多语言支持模块。
集中管理所有用户界面文案（简体 zh / 繁体 tw / 英文 en），并提供语言判定函数。

语言判定规则：
- stored（用户手动选择）优先；
- 否则按 Telegram 用户的 language_code 判断：含 tw/hk/hant -> tw，含 zh -> zh，其余 -> en；
- 空值兜底 zh。
"""
import os

# ============ 三语文案字典 ============
# 键名约定：`域.子项`，如 menu.check_active / login.select_method / err.zip_required
STRINGS = {
    # ---- 通用 ----
    "back_to_main": {"zh": "返回主菜单", "tw": "返回主選單", "en": "Back to Main Menu"},
    "confirm": {"zh": "确认", "tw": "確認", "en": "Confirm"},
    "back": {"zh": "返回", "tw": "返回", "en": "Back"},
    "common.please_wait": {"zh": "请稍候", "tw": "請稍候", "en": "Please wait"},
    "common.checking": {"zh": "正在检查", "tw": "正在檢查", "en": "Checking"},
    "common.count": {"zh": "个", "tw": "個", "en": ""},
    "common.processing_wait": {"zh": "正在处理，请稍候...", "tw": "正在處理，請稍候...", "en": "Processing, please wait..."},
    "common.tdata_folders": {"zh": "个tdata文件夹", "tw": "個tdata文件夾", "en": "tdata folders"},

    # ---- 命令 ----
    "cmd.start": {"zh": "开启机器人", "tw": "開啟機器人", "en": "Start bot"},

    # ---- 主菜单按钮（17 个功能）----
    "menu.check_active": {"zh": "账号筛活", "tw": "帳號篩活", "en": "Check Active"},
    "menu.account_login": {"zh": "账号登陆", "tw": "帳號登錄", "en": "Account Login"},
    "menu.change_2fa": {"zh": "修改2FA", "tw": "修改2FA", "en": "Change 2FA"},
    "menu.merge_packs": {"zh": "整合号包", "tw": "整合號包", "en": "Merge Packs"},
    "menu.test_bidirectional": {"zh": "双向测试", "tw": "雙向測試", "en": "Bidirectional Test"},
    "menu.kick_devices": {"zh": "踢其他设备", "tw": "踢其他設備", "en": "Kick Devices"},
    "menu.privacy_config": {"zh": "隐私配置", "tw": "隱私配置", "en": "Privacy Config"},
    "menu.format_convert": {"zh": "格式互转", "tw": "格式互轉", "en": "Format Convert"},
    "menu.convert_api": {"zh": "转API", "tw": "轉API", "en": "Convert to API"},
    "menu.prevent_recovery": {"zh": "防止找回", "tw": "防止找回", "en": "Prevent Recovery"},
    "menu.check_ban": {"zh": "号码筛BAN", "tw": "號碼篩BAN", "en": "Check Ban"},
    "menu.check_material": {"zh": "筛料能力", "tw": "篩料能力", "en": "Check Material"},
    "menu.clean_account": {"zh": "清理账号", "tw": "清理帳號", "en": "Clean Account"},
    "menu.unpack_tool": {"zh": "拆包工具", "tw": "拆包工具", "en": "Unpack Tool"},
    "menu.destroy_session": {"zh": "销毁会话", "tw": "銷毀會話", "en": "Destroy Session"},
    "menu.passkey": {"zh": "Passkey功能", "tw": "Passkey功能", "en": "Passkey"},
    "menu.regtime": {"zh": "注册时间", "tw": "註冊時間", "en": "Registration Time"},
    "menu.language": {"zh": "🌐 语言 / Language", "tw": "🌐 語言 / Language", "en": "🌐 Language"},

    # ---- 语言切换 ----
    "lang.choose": {"zh": "请选择语言：", "tw": "請選擇語言：", "en": "Choose your language:"},
    "lang.zh": {"zh": "简体中文", "tw": "簡體中文", "en": "简体中文"},
    "lang.tw": {"zh": "繁體中文", "tw": "繁體中文", "en": "繁體中文"},
    "lang.en": {"zh": "English", "tw": "English", "en": "English"},
    "lang.changed": {"zh": "语言已切换为简体中文", "tw": "語言已切換為繁體中文", "en": "Language switched to English"},

    # ---- 登录 ----
    "login.select_method": {"zh": "请选择登录方式：", "tw": "請選擇登錄方式：", "en": "Select a login method:"},
    "login.phone": {"zh": "手机号登录", "tw": "手機號登錄", "en": "Phone Login"},
    "login.qr": {"zh": "扫码登录", "tw": "掃碼登錄", "en": "QR Login"},
    "login.qr_generating": {"zh": "正在生成二维码，请稍候...", "tw": "正在生成二維碼，請稍候...", "en": "Generating QR code, please wait..."},
    "login.qr_scan_hint": {"zh": "请扫描二维码完成登录，无需发送消息", "tw": "請掃描二維碼完成登錄，無需發送消息", "en": "Scan the QR code to log in, no message needed"},
    "login.phone_invalid": {"zh": "手机号格式错误", "tw": "手機號格式錯誤", "en": "Invalid phone number format"},
    "login.code_sent": {"zh": "验证码已发送，请输入：", "tw": "驗證碼已發送，請輸入：", "en": "Verification code sent, please enter:"},
    "login.server_busy": {"zh": "服务器繁忙，请稍后重试", "tw": "服務器繁忙，請稍後重試", "en": "Server busy, please try again later"},
    "login.need_2fa": {"zh": "需要2FA密码，请输入：", "tw": "需要2FA密碼，請輸入：", "en": "2FA password required, please enter:"},
    "login.code_timeout": {"zh": "验证超时，请重新发送验证码", "tw": "驗證超時，請重新發送驗證碼", "en": "Verification timed out, please resend the code"},
    "login.failed": {"zh": "登录失败", "tw": "登錄失敗", "en": "Login failed"},
    "login.2fa_failed": {"zh": "2FA验证失败", "tw": "2FA驗證失敗", "en": "2FA verification failed"},
    "login.success": {"zh": "登录成功", "tw": "登錄成功", "en": "Login successful"},
    "login.qr_success": {"zh": "扫码登录成功", "tw": "掃碼登錄成功", "en": "QR login successful"},
    "login.qr_timeout": {"zh": "扫码登录超时，请重新尝试", "tw": "掃碼登錄超時，請重新嘗試", "en": "QR login timed out, please try again"},
    "login.qr_2fa_hint": {"zh": "该账号已开启两步验证，请输入密码：", "tw": "該帳號已開啟兩步驗證，請輸入密碼：", "en": "Two-step verification enabled, please enter password:"},
    "login.qr_failed": {"zh": "扫码登录失败", "tw": "掃碼登錄失敗", "en": "QR login failed"},
    "login.flood": {"zh": "请求过于频繁，请等待 {s} 秒后重试", "tw": "請求過於頻繁，請等待 {s} 秒後重試", "en": "Too many requests, please wait {s} seconds"},
    "login.2fa_timeout": {"zh": "2FA输入超时，请重新扫码", "tw": "2FA輸入超時，請重新掃碼", "en": "2FA input timed out, please scan again"},
    "login.export_failed": {"zh": "导出文件失败", "tw": "導出文件失敗", "en": "Export failed"},
    "login.qr_expiry": {"zh": "请使用 Telegram 手机端扫描二维码登录\n\n⏱️ 二维码有效期为2分钟", "tw": "請使用 Telegram 手機端掃描二維碼登錄\n\n⏱️ 二維碼有效期為2分鐘", "en": "Scan the QR code with Telegram mobile to log in\n\n⏱️ QR code valid for 2 minutes"},

    # ---- 通用错误 ----
    "err.process_failed_retry": {"zh": "处理失败，请重试", "tw": "處理失敗，請重試", "en": "Processing failed, please retry"},
    "err.zip_required": {"zh": "请上传ZIP格式的压缩包", "tw": "請上傳ZIP格式的壓縮包", "en": "Please upload a ZIP archive"},
    "err.txt_required": {"zh": "请上传TXT格式文件", "tw": "請上傳TXT格式文件", "en": "Please upload a TXT file"},
    "err.upload_after_select": {"zh": "请先选择功能再上传文件", "tw": "請先選擇功能再上傳文件", "en": "Select a feature before uploading"},
    "err.system_not_configured": {"zh": "系统未配置，请联系管理员", "tw": "系統未配置，請聯繫管理員", "en": "System not configured, contact admin"},
    "err.api_config_error": {"zh": "API配置错误，请联系管理员", "tw": "API配置錯誤，請聯繫管理員", "en": "API config error, contact admin"},
    "err.extract_failed": {"zh": "解压失败", "tw": "解壓失敗", "en": "Extraction failed"},
    "err.file_too_large": {"zh": "解压后文件过大", "tw": "解壓後文件過大", "en": "Extracted files too large"},
    "err.file_size_max": {"zh": "文件过大，最大支持 {mb}MB", "tw": "文件過大，最大支持 {mb}MB", "en": "File too large, max {mb}MB"},
    "err.no_session_tdata": {"zh": "未找到session或tdata文件夹", "tw": "未找到session或tdata文件夾", "en": "No session or tdata folder found"},
    "err.all_tdata_failed": {"zh": "所有tdata转换失败，无法继续", "tw": "所有tdata轉換失敗，無法繼續", "en": "All tdata conversion failed"},
    "err.task_timeout": {"zh": "任务执行超时", "tw": "任務執行超時", "en": "Task timed out"},
    "err.processing": {"zh": "正在下载文件...", "tw": "正在下載文件...", "en": "Downloading file..."},
    "err.process_failed": {"zh": "处理失败", "tw": "處理失敗", "en": "Processing failed"},

    # ---- 筛活 ----
    "shaihuo.processing": {"zh": "开始处理筛活任务...", "tw": "開始處理篩活任務...", "en": "Starting active check..."},
    "shaihuo.in_progress": {"zh": "筛活进行中", "tw": "篩活進行中", "en": "Active check in progress"},
    "shaihuo.converting": {"zh": "检测到tdata，正在转换为session...", "tw": "檢測到tdata，正在轉換為session...", "en": "tdata detected, converting to session..."},
    "shaihuo.convert_progress": {"zh": "tdata转换进度", "tw": "tdata轉換進度", "en": "tdata conversion progress"},
    "shaihuo.found_accounts": {"zh": "找到", "tw": "找到", "en": "Found"},
    "shaihuo.accounts": {"zh": "个账号", "tw": "個帳號", "en": "accounts"},
    "shaihuo.done": {"zh": "筛活完成", "tw": "篩活完成", "en": "Active check complete"},
    "shaihuo.stats": {"zh": "统计结果", "tw": "統計結果", "en": "Statistics"},
    "shaihuo.total": {"zh": "总账号", "tw": "總帳號", "en": "Total accounts"},
    "shaihuo.alive": {"zh": "存活", "tw": "存活", "en": "Alive"},
    "shaihuo.frozen": {"zh": "冻结", "tw": "凍結", "en": "Frozen"},
    "shaihuo.dead": {"zh": "失效", "tw": "失效", "en": "Dead"},
    "shaihuo.progress": {"zh": "进度", "tw": "進度", "en": "Progress"},
    "shaihuo.success": {"zh": "成功", "tw": "成功", "en": "Success"},
    "shaihuo.alive_caption": {"zh": "存活账号", "tw": "存活帳號", "en": "Alive accounts"},
    "shaihuo.frozen_caption": {"zh": "冻结账号", "tw": "凍結帳號", "en": "Frozen accounts"},
    "shaihuo.dead_caption": {"zh": "失效账号", "tw": "失效帳號", "en": "Dead accounts"},
    "shaihuo.pending": {"zh": "未完成", "tw": "未完成", "en": "Pending"},
    "shaihuo.pending_caption": {"zh": "未完成账号(未处理)", "tw": "未完成帳號(未處理)", "en": "Pending accounts (unprocessed)"},
    "shaihuo.task_done": {"zh": "筛活任务完成", "tw": "篩活任務完成", "en": "Active check task complete"},
    "task.stopping": {"zh": "正在终止任务，请稍候打包已完成的账号…", "tw": "正在終止任務，請稍候打包已完成的帳號…", "en": "Stopping task, packing completed accounts…"},
    "task.no_running": {"zh": "当前没有正在运行的任务", "tw": "當前沒有正在運行的任務", "en": "No running task"},
    "task.running": {"zh": "任务运行中，可点击按钮提前停止（已完成账号将打包发送）", "tw": "任務運行中，可點擊按鈕提前停止（已完成帳號將打包發送）", "en": "Task running, tap button to stop early (completed accounts will be packed)"},
    "task.stop_button": {"zh": "⏹ 停止任务", "tw": "⏹ 停止任務", "en": "⏹ Stop Task"},
    "task.stopped": {"zh": "任务已停止，已打包完成的账号。", "tw": "任務已停止，已打包完成的帳號。", "en": "Task stopped, completed accounts packed."},
    "task.stop_failed": {"zh": "停止失败或没有运行中的任务", "tw": "停止失敗或沒有運行中的任務", "en": "Stop failed or no running task"},

    # ---- 修改2FA ----
    "2fa.manual": {"zh": "手动输入", "tw": "手動輸入", "en": "Manual Input"},
    "2fa.auto": {"zh": "自动识别", "tw": "自動識別", "en": "Auto Detect"},
    "2fa.manual_mode": {"zh": "手动输入模式", "tw": "手動輸入模式", "en": "Manual input mode"},
    "2fa.auto_mode": {"zh": "自动识别模式", "tw": "自動識別模式", "en": "Auto detect mode"},
    "2fa.manual_detail": {
        "zh": "请按照以下格式发送：\n旧密码 新密码\n\n例如：123456 654321\n\n如果账号没有设置2FA，只想设置新密码，请发送：\nNone 新密码",
        "tw": "請按照以下格式發送：\n舊密碼 新密碼\n\n例如：123456 654321\n\n如果帳號沒有設置2FA，只想設置新密碼，請發送：\nNone 新密碼",
        "en": "Send in the following format:\nOLD_PASSWORD NEW_PASSWORD\n\nExample: 123456 654321\n\nIf no 2FA is set and you only want a new password, send:\nNone NEW_PASSWORD"
    },
    "2fa.auto_detail": {
        "zh": "请发送您想要设置的新2FA密码：\n\n（系统将自动从json中读取旧密码）",
        "tw": "請發送您想要設置的新2FA密碼：\n\n（系統將自動從json中讀取舊密碼）",
        "en": "Send the new 2FA password you want:\n\n(Old password will be read from json automatically)"
    },
    "2fa.saved": {"zh": "信息已保存", "tw": "信息已保存", "en": "Info saved"},
    "2fa.old_pass": {"zh": "旧密码", "tw": "舊密碼", "en": "Old password"},
    "2fa.new_pass": {"zh": "新密码", "tw": "新密碼", "en": "New password"},
    "2fa.none": {"zh": "无", "tw": "無", "en": "None"},
    "2fa.upload_zip": {"zh": "现在请上传包含session或tdata的ZIP文件", "tw": "現在請上傳包含session或tdata的ZIP文件", "en": "Now upload a ZIP containing session or tdata"},
    "2fa.format_error": {"zh": "格式错误", "tw": "格式錯誤", "en": "Format error"},
    "2fa.pass_empty": {"zh": "密码不能为空，请重新输入", "tw": "密碼不能為空，請重新輸入", "en": "Password cannot be empty"},
    "2fa.saved_new": {"zh": "新密码已保存", "tw": "新密碼已保存", "en": "New password saved"},
    "2fa.no_new_pass": {"zh": "未设置新密码，请重新选择模式", "tw": "未設置新密碼，請重新選擇模式", "en": "No new password set, reselect mode"},
    "2fa.incomplete": {"zh": "未完整设置新旧密码，请重新选择模式", "tw": "未完整設置新舊密碼，請重新選擇模式", "en": "Old/new password incomplete"},
    "2fa.processing": {"zh": "开始处理2FA修改任务...", "tw": "開始處理2FA修改任務...", "en": "Starting 2FA change..."},
    "2fa.in_progress": {"zh": "2FA修改进行中", "tw": "2FA修改進行中", "en": "2FA change in progress"},
    "2fa.mode": {"zh": "模式", "tw": "模式", "en": "Mode"},
    "2fa.done": {"zh": "2FA修改完成", "tw": "2FA修改完成", "en": "2FA change complete"},
    "2fa.success_change": {"zh": "成功修改", "tw": "成功修改", "en": "Changed"},
    "2fa.reset_success": {"zh": "重置成功", "tw": "重置成功", "en": "Reset"},
    "2fa.reset_failed": {"zh": "重置失败", "tw": "重置失敗", "en": "Reset failed"},
    "2fa.failed": {"zh": "失败", "tw": "失敗", "en": "Failed"},
    "2fa.success_caption": {"zh": "成功修改2FA", "tw": "成功修改2FA", "en": "2FA changed"},
    "2fa.reset_caption": {"zh": "重置成功", "tw": "重置成功", "en": "Reset success"},
    "2fa.reset_failed_caption": {"zh": "重置失败", "tw": "重置失敗", "en": "Reset failed"},
    "2fa.task_done": {"zh": "2FA修改任务完成", "tw": "2FA修改任務完成", "en": "2FA change task complete"},
    "2fa.converting": {"zh": "检测到tdata，正在转换为session...", "tw": "檢測到tdata，正在轉換為session...", "en": "tdata detected, converting to session..."},
    "2fa.convert_progress": {"zh": "tdata转换进度", "tw": "tdata轉換進度", "en": "tdata conversion progress"},
    "2fa.found": {"zh": "找到", "tw": "找到", "en": "Found"},
    "2fa.tdata_dirs": {"zh": "个tdata文件夹", "tw": "個tdata文件夾", "en": "tdata folders"},
    "2fa.accounts": {"zh": "个账号", "tw": "個帳號", "en": "accounts"},
    "2fa.progress": {"zh": "进度", "tw": "進度", "en": "Progress"},
    "2fa.success": {"zh": "成功", "tw": "成功", "en": "Success"},
    "2fa.stats": {"zh": "统计结果", "tw": "統計結果", "en": "Statistics"},
    "2fa.total": {"zh": "总账号", "tw": "總帳號", "en": "Total accounts"},
    "2fa.processing_hint": {"zh": "正在处理，请稍候", "tw": "正在處理，請稍候", "en": "Processing, please wait"},
    "2fa.format_error_detail": {"zh": "请重新输入", "tw": "請重新輸入", "en": "Please re-enter"},
    "2fa.auto_read_hint": {"zh": "（系统将自动从json中读取旧密码）", "tw": "（系統將自動從json中讀取舊密碼）", "en": "(Old password will be read from json automatically)"},

    # ---- 双向测试 ----
    "bidir.processing": {"zh": "开始处理双向测试...", "tw": "開始處理雙向測試...", "en": "Starting bidirectional test..."},
    "bidir.in_progress": {"zh": "双向测试进行中", "tw": "雙向測試進行中", "en": "Bidirectional test in progress"},
    "bidir.done": {"zh": "双向测试完成", "tw": "雙向測試完成", "en": "Bidirectional test complete"},
    "bidir.unlimited": {"zh": "无限制", "tw": "無限制", "en": "Unlimited"},
    "bidir.limited": {"zh": "有限制", "tw": "有限制", "en": "Limited"},
    "bidir.unlimited_caption": {"zh": "无限制账户", "tw": "無限制帳戶", "en": "Unlimited accounts"},
    "bidir.limited_caption": {"zh": "有限制账户", "tw": "有限制帳戶", "en": "Limited accounts"},
    "bidir.task_done": {"zh": "双向测试任务完成", "tw": "雙向測試任務完成", "en": "Bidirectional test complete"},

    # ---- 踢设备 ----
    "kick.processing": {"zh": "开始处理踢设备任务...", "tw": "開始處理踢設備任務...", "en": "Starting kick devices..."},
    "kick.in_progress": {"zh": "踢设备进行中", "tw": "踢設備進行中", "en": "Kicking devices..."},
    "kick.done": {"zh": "踢设备完成", "tw": "踢設備完成", "en": "Kick complete"},
    "kick.success_caption": {"zh": "成功踢设备", "tw": "成功踢設備", "en": "Kicked"},
    "kick.task_done": {"zh": "踢设备任务完成", "tw": "踢設備任務完成", "en": "Kick task complete"},

    # ---- 隐私配置 ----
    "privacy.phone": {"zh": "手机号", "tw": "手機號", "en": "Phone Number"},
    "privacy.last_seen": {"zh": "最后在线时间", "tw": "最後在線時間", "en": "Last Seen"},
    "privacy.forward": {"zh": "转发内容", "tw": "轉發內容", "en": "Forwards"},
    "privacy.profile_photo": {"zh": "个人头像", "tw": "個人頭像", "en": "Profile Photo"},
    "privacy.everyone": {"zh": "所有人", "tw": "所有人", "en": "Everyone"},
    "privacy.contacts": {"zh": "联系人", "tw": "聯繫人", "en": "Contacts"},
    "privacy.nobody": {"zh": "没有人", "tw": "沒有人", "en": "Nobody"},
    "privacy.unset": {"zh": "未设置", "tw": "未設置", "en": "Not set"},
    "privacy.keep": {"zh": "保持不变", "tw": "保持不變", "en": "Keep unchanged"},
    "privacy.set_prefix": {"zh": "设置", "tw": "設置", "en": "Set "},
    "privacy.confirm_upload": {"zh": "确认并上传", "tw": "確認並上傳", "en": "Confirm & Upload"},
    "privacy.reset_all": {"zh": "全部重置", "tw": "全部重置", "en": "Reset All"},
    "privacy.title": {"zh": "隐私配置", "tw": "隱私配置", "en": "Privacy Config"},
    "privacy.current": {"zh": "当前设置", "tw": "當前設置", "en": "Current settings"},
    "privacy.visible_range": {"zh": "可见范围", "tw": "可見範圍", "en": "Visibility"},
    "privacy.select_who": {"zh": "请选择谁可以查看", "tw": "請選擇誰可以查看", "en": "Select who can view"},
    "privacy.set_at_least_one": {"zh": "请先设置至少一项隐私选项", "tw": "請先設置至少一項隱私選項", "en": "Set at least one privacy option first"},
    "privacy.will_apply": {"zh": "将应用以下设置", "tw": "將應用以下設置", "en": "The following settings will be applied"},
    "privacy.upload_zip": {"zh": "请上传包含session或tdata的ZIP文件", "tw": "請上傳包含session或tdata的ZIP文件", "en": "Upload a ZIP containing session or tdata"},
    "privacy.set_first": {"zh": "请先设置隐私选项", "tw": "請先設置隱私選項", "en": "Set privacy options first"},
    "privacy.processing": {"zh": "开始处理隐私配置任务...", "tw": "開始處理隱私配置任務...", "en": "Starting privacy config..."},
    "privacy.in_progress": {"zh": "隐私配置进行中", "tw": "隱私配置進行中", "en": "Privacy config in progress"},
    "privacy.done": {"zh": "隐私配置完成", "tw": "隱私配置完成", "en": "Privacy config complete"},
    "privacy.applied": {"zh": "已应用设置", "tw": "已應用設置", "en": "Applied settings"},
    "privacy.no_change": {"zh": "无设置变更", "tw": "無設置變更", "en": "No changes"},
    "privacy.success_caption": {"zh": "隐私配置成功", "tw": "隱私配置成功", "en": "Privacy success"},
    "privacy.failed_caption": {"zh": "配置失败", "tw": "配置失敗", "en": "Config failed"},
    "privacy.task_done": {"zh": "隐私配置任务完成", "tw": "隱私配置任務完成", "en": "Privacy config complete"},

    # ---- 格式互转 ----
    "format.session_to_tdata": {"zh": "Session → Tdata", "tw": "Session → Tdata", "en": "Session → Tdata"},
    "format.tdata_to_session": {"zh": "Tdata → Session", "tw": "Tdata → Session", "en": "Tdata → Session"},
    "format.st_title": {"zh": "Session → Tdata 转换", "tw": "Session → Tdata 轉換", "en": "Session → Tdata conversion"},
    "format.st_upload": {"zh": "请上传包含 .session 文件的ZIP压缩包", "tw": "請上傳包含 .session 文件的ZIP壓縮包", "en": "Upload a ZIP containing .session files"},
    "format.ts_title": {"zh": "Tdata → Session 转换", "tw": "Tdata → Session 轉換", "en": "Tdata → Session conversion"},
    "format.ts_upload": {"zh": "请上传包含 tdata 文件夹的ZIP压缩包", "tw": "請上傳包含 tdata 文件夾的ZIP壓縮包", "en": "Upload a ZIP containing a tdata folder"},
    "format.processing": {"zh": "开始处理转换任务...", "tw": "開始處理轉換任務...", "en": "Starting conversion..."},
    "format.st_in_progress": {"zh": "Session转Tdata进行中", "tw": "Session轉Tdata進行中", "en": "Session→Tdata in progress"},
    "format.ts_in_progress": {"zh": "Tdata转Session进行中", "tw": "Tdata轉Session進行中", "en": "Tdata→Session in progress"},
    "format.success": {"zh": "成功转换", "tw": "成功轉換", "en": "Converted"},
    "format.failed_caption": {"zh": "失败账号", "tw": "失敗帳號", "en": "Failed accounts"},
    "format.no_session": {"zh": "未找到session文件", "tw": "未找到session文件", "en": "No session files found"},
    "format.no_tdata": {"zh": "未找到tdata文件夹", "tw": "未找到tdata文件夾", "en": "No tdata folder found"},
    "format.st_note": {"zh": "转换后将返回包含tdata文件夹的ZIP包", "tw": "轉換後將返回包含tdata文件夾的ZIP包", "en": "A ZIP containing the tdata folder will be returned"},
    "format.ts_note": {"zh": "转换后将返回包含session+json的ZIP包", "tw": "轉換後將返回包含session+json的ZIP包", "en": "A ZIP containing session+json will be returned"},
    "format.session_unit": {"zh": "个session文件", "tw": "個session文件", "en": "session files"},
    "format.tdata_unit": {"zh": "个tdata文件夹", "tw": "個tdata文件夾", "en": "tdata folders"},
    "format.converting": {"zh": "正在转换，请稍候...", "tw": "正在轉換，請稍候...", "en": "Converting, please wait..."},
    "format.unit": {"zh": "个", "tw": "個", "en": ""},

    # ---- 转API ----
    "api.no_2fa": {"zh": "无2FA", "tw": "無2FA", "en": "No 2FA"},
    "api.manual_2fa": {"zh": "手动输入2FA", "tw": "手動輸入2FA", "en": "Manual 2FA"},
    "api.from_json": {"zh": "从JSON提取", "tw": "從JSON提取", "en": "From JSON"},
    "api.select_2fa": {"zh": "请选择2FA处理方式：", "tw": "請選擇2FA處理方式：", "en": "Select 2FA handling:"},
    "api.upload_no_2fa": {"zh": "请上传session或tdata的ZIP包（无2FA）", "tw": "請上傳session或tdata的ZIP包（無2FA）", "en": "Upload ZIP (no 2FA)"},
    "api.input_2fa": {"zh": "请输入2FA密码：", "tw": "請輸入2FA密碼：", "en": "Enter 2FA password:"},
    "api.upload_from_json": {"zh": "请上传session或tdata的ZIP包（将自动从JSON提取2FA和手机号）", "tw": "請上傳session或tdata的ZIP包（將自動從JSON提取2FA和手機號）", "en": "Upload ZIP (auto-extract from JSON)"},
    "api.2fa_recorded": {"zh": "2FA已记录，请上传session或tdata的ZIP包", "tw": "2FA已記錄，請上傳session或tdata的ZIP包", "en": "2FA recorded, upload ZIP"},
    "api.processing": {"zh": "开始处理转换...", "tw": "開始處理轉換...", "en": "Processing conversion..."},
    "api.processing_count": {"zh": "处理中", "tw": "處理中", "en": "Processing"},
    "api.done": {"zh": "转换完成", "tw": "轉換完成", "en": "Conversion complete"},
    "api.total": {"zh": "总计", "tw": "總計", "en": "Total"},
    "api.link_caption": {"zh": "API链接", "tw": "API鏈接", "en": "API Links"},

    # ---- 清理账号 ----
    "clean.delete_chats": {"zh": "删除所有对话", "tw": "刪除所有對話", "en": "Delete all chats"},
    "clean.delete_contacts": {"zh": "删除所有联系人", "tw": "刪除所有聯繫人", "en": "Delete all contacts"},
    "clean.delete_passkeys": {"zh": "删除所有Passkey", "tw": "刪除所有Passkey", "en": "Delete all passkeys"},
    "clean.delete_all": {"zh": "全部删除", "tw": "全部刪除", "en": "Delete all"},
    "clean.delete_all_types": {"zh": "删除所有对话、联系人和Passkey", "tw": "刪除所有對話、聯繫人和Passkey", "en": "Delete all chats, contacts and passkeys"},
    "clean.selected": {"zh": "已选择", "tw": "已選擇", "en": "Selected"},
    "clean.upload_zip": {"zh": "请上传包含session或tdata的ZIP文件", "tw": "請上傳包含session或tdata的ZIP文件", "en": "Upload ZIP with session or tdata"},
    "clean.processing": {"zh": "开始处理清理任务...", "tw": "開始處理清理任務...", "en": "Starting clean..."},
    "clean.in_progress": {"zh": "清理账号进行中", "tw": "清理帳號進行中", "en": "Cleaning account..."},
    "clean.type": {"zh": "清理类型", "tw": "清理類型", "en": "Clean type"},
    "clean.done": {"zh": "清理账号完成", "tw": "清理帳號完成", "en": "Clean complete"},
    "clean.deleted_chats": {"zh": "删除对话", "tw": "刪除對話", "en": "Chats deleted"},
    "clean.deleted_contacts": {"zh": "删除联系人", "tw": "刪除聯繫人", "en": "Contacts deleted"},
    "clean.deleted_passkeys": {"zh": "删除Passkey", "tw": "刪除Passkey", "en": "Passkeys deleted"},
    "clean.success_caption": {"zh": "清理成功", "tw": "清理成功", "en": "Clean success"},
    "clean.failed_caption": {"zh": "清理失败", "tw": "清理失敗", "en": "Clean failed"},
    "clean.task_done": {"zh": "清理账号任务完成", "tw": "清理帳號任務完成", "en": "Clean task complete"},

    # ---- 筛料能力 ----
    "material.processing": {"zh": "开始检查筛料能力...", "tw": "開始檢查篩料能力...", "en": "Checking material capability..."},
    "material.in_progress": {"zh": "筛料能力检查进行中", "tw": "篩料能力檢查進行中", "en": "Material check in progress"},
    "material.done": {"zh": "筛料能力检查完成", "tw": "篩料能力檢查完成", "en": "Material check complete"},
    "material.has_capability": {"zh": "有能力", "tw": "有能力", "en": "Has capability"},
    "material.no_capability": {"zh": "无能力", "tw": "無能力", "en": "No capability"},
    "material.check_failed": {"zh": "检查失败", "tw": "檢查失敗", "en": "Check failed"},
    "material.has_caption": {"zh": "有能力账号", "tw": "有能力帳號", "en": "Accounts with capability"},
    "material.no_caption": {"zh": "无能力账号", "tw": "無能力帳號", "en": "Accounts without capability"},
    "material.failed_reason_hint": {"zh": "内含失败原因文本文件", "tw": "內含失敗原因文本檔案", "en": "Contains failure reason text file"},
    "material.task_done": {"zh": "筛料能力检查完成", "tw": "篩料能力檢查完成", "en": "Material check complete"},

    # ---- 号码筛BAN ----
    "ban.processing": {"zh": "开始检测", "tw": "開始檢測", "en": "Checking"},
    "ban.phones": {"zh": "个号码", "tw": "個號碼", "en": "numbers"},
    "ban.progress": {"zh": "检测进度", "tw": "檢測進度", "en": "Check progress"},
    "ban.banned": {"zh": "已封禁", "tw": "已封禁", "en": "Banned"},
    "ban.normal": {"zh": "正常", "tw": "正常", "en": "Normal"},
    "ban.done": {"zh": "封禁检测完成", "tw": "封禁檢測完成", "en": "Ban check complete"},
    "ban.total": {"zh": "总号码数", "tw": "總號碼數", "en": "Total numbers"},
    "ban.banned_caption": {"zh": "已封禁号码", "tw": "已封禁號碼", "en": "Banned numbers"},
    "ban.normal_caption": {"zh": "正常号码", "tw": "正常號碼", "en": "Normal numbers"},
    "ban.task_done": {"zh": "封禁检测任务完成", "tw": "封禁檢測任務完成", "en": "Ban check task complete"},
    "ban.no_valid_phone": {"zh": "文件中没有有效手机号", "tw": "文件中沒有有效手機號", "en": "No valid phone numbers"},

    # ---- 防止找回 ----
    "recovery.skip_2fa": {"zh": "跳过2FA", "tw": "跳過2FA", "en": "Skip 2FA"},
    "recovery.extracted": {"zh": "已提取", "tw": "已提取", "en": "Extracted"},
    "recovery.ask_2fa": {"zh": "请发送2FA密码（如果没有2FA，请点击“跳过2FA”按钮）：", "tw": "請發送2FA密碼（如果沒有2FA，請點擊“跳過2FA”按鈕）：", "en": "Send 2FA password (or tap \"Skip 2FA\"):"},
    "recovery.expired": {"zh": "会话已过期，请重新开始", "tw": "會話已過期，請重新開始", "en": "Session expired, restart"},
    "recovery.start": {"zh": "防止找回任务开始", "tw": "防止找回任務開始", "en": "Recovery prevention started"},
    "recovery.in_progress": {"zh": "防止找回任务进行中", "tw": "防止找回任務進行中", "en": "Recovery prevention in progress"},
    "recovery.timeout_msg": {"zh": "任务执行超时", "tw": "任務執行超時", "en": "Task timed out"},
    "recovery.failed": {"zh": "任务执行失败", "tw": "任務執行失敗", "en": "Task failed"},
    "recovery.done": {"zh": "防止找回任务完成", "tw": "防止找回任務完成", "en": "Recovery prevention complete"},
    "recovery.success_caption": {"zh": "成功转移的账号", "tw": "成功轉移的帳號", "en": "Accounts transferred"},
    "recovery.failed_caption": {"zh": "失败的账号", "tw": "失敗的帳號", "en": "Failed accounts"},
    "recovery.extracting": {"zh": "正在解压并提取账号...", "tw": "正在解壓並提取帳號...", "en": "Extracting accounts..."},
    "recovery.tdata_detected": {"zh": "检测到", "tw": "檢測到", "en": "Detected"},
    "recovery.tdata_unit": {"zh": "个tdata，正在转换...", "tw": "個tdata，正在轉換...", "en": "tdata, converting..."},
    "recovery.accounts_unit": {"zh": "个账号", "tw": "個帳號", "en": "accounts"},
    "recovery.total_accounts": {"zh": "总账号数", "tw": "總帳號數", "en": "Total accounts"},
    "recovery.processing": {"zh": "正在处理，请稍候...", "tw": "正在處理，請稍候...", "en": "Processing, please wait..."},
    "recovery.seconds": {"zh": "秒", "tw": "秒", "en": "s"},

    # ---- 拆包工具 ----
    "unpack.processing": {"zh": "正在解压分析...", "tw": "正在解壓分析...", "en": "Analyzing archive..."},
    "unpack.analysis_done": {"zh": "分析完成", "tw": "分析完成", "en": "Analysis complete"},
    "unpack.found_session": {"zh": "个session文件", "tw": "個session文件", "en": "session files"},
    "unpack.input_format": {"zh": "请输入拆分格式：", "tw": "請輸入拆分格式：", "en": "Enter split format:"},
    "unpack.format_detail": {
        "zh": "• 固定数量: -X- (每个包X个账号)\n• 指定数量: 5,5,5 (拆成3个包，每包5个)\n• 混合数量: 10,8,6 (分别指定每包数量)\n\n最后一个包可以不满，多余账号会单独打包",
        "tw": "• 固定數量: -X- (每個包X個帳號)\n• 指定數量: 5,5,5 (拆成3個包，每包5個)\n• 混合數量: 10,8,6 (分別指定每包數量)\n\n最後一個包可以不滿，多餘帳號會單獨打包",
        "en": "• Fixed count: -X- (X accounts per pack)\n• Specified: 5,5,5 (3 packs of 5)\n• Mixed: 10,8,6 (specify each pack)\n\nThe last pack may be partial; extras are packed separately"
    },
    "unpack.format_confirmed": {"zh": "格式已确认", "tw": "格式已確認", "en": "Format confirmed"},
    "unpack.fixed_count": {"zh": "固定数量", "tw": "固定數量", "en": "Fixed count"},
    "unpack.specified": {"zh": "指定数量", "tw": "指定數量", "en": "Specified count"},
    "unpack.per_pack": {"zh": "个/包", "tw": "個/包", "en": "/pack"},
    "unpack.start": {"zh": "开始拆包处理...", "tw": "開始拆包處理...", "en": "Starting unpack..."},
    "unpack.done": {"zh": "拆包完成", "tw": "拆包完成", "en": "Unpack complete"},
    "unpack.packs": {"zh": "生成包数", "tw": "生成包數", "en": "Packs generated"},
    "unpack.pack": {"zh": "包", "tw": "包", "en": "Pack"},
    "unpack.task_done": {"zh": "拆包任务完成", "tw": "拆包任務完成", "en": "Unpack task complete"},
    "unpack.no_session": {"zh": "未找到session文件", "tw": "未找到session文件", "en": "No session files"},

    # ---- 销毁会话 ----
    "destroy.processing": {"zh": "开始处理销毁任务...", "tw": "開始處理銷毀任務...", "en": "Starting destroy..."},
    "destroy.in_progress": {"zh": "销毁会话进行中", "tw": "銷毀會話進行中", "en": "Destroying sessions..."},
    "destroy.done": {"zh": "销毁完成", "tw": "銷毀完成", "en": "Destroy complete"},
    "destroy.success_destroy": {"zh": "成功销毁", "tw": "成功銷毀", "en": "Destroyed"},
    "destroy.success_caption": {"zh": "成功销毁", "tw": "成功銷毀", "en": "Destroyed"},
    "destroy.task_done": {"zh": "销毁会话任务完成", "tw": "銷毀會話任務完成", "en": "Destroy task complete"},

    # ---- Passkey ----
    "passkey.create": {"zh": "创建Passkey", "tw": "創建Passkey", "en": "Create Passkey"},
    "passkey.login": {"zh": "Passkey登录", "tw": "Passkey登錄", "en": "Passkey Login"},
    "passkey.create_hint": {"zh": "请上传包含 .session 或 tdata 的 ZIP 压缩包，将为您导出 .Passkey 凭据文件。", "tw": "請上傳包含 .session 或 tdata 的 ZIP 壓縮包，將為您導出 .Passkey 憑據文件。", "en": "Upload ZIP with .session or tdata to export .Passkey credentials."},
    "passkey.login_hint": {"zh": "请上传包含 .Passkey 凭据文件的 ZIP 压缩包，将为您自动登录并生成 .session 和 .json。", "tw": "請上傳包含 .Passkey 憑據文件的 ZIP 壓縮包，將為您自動登錄並生成 .session 和 .json。", "en": "Upload ZIP with .Passkey to auto-login and generate .session/.json."},
    "passkey.creating": {"zh": "正在创建 Passkey", "tw": "正在創建 Passkey", "en": "Creating Passkey"},
    "passkey.logging_in": {"zh": "正在通过 Passkey 登录", "tw": "正在通過 Passkey 登錄", "en": "Logging in via Passkey"},
    "passkey.create_done": {"zh": "创建完成！", "tw": "創建完成！", "en": "Creation complete!"},
    "passkey.login_done": {"zh": "做号登录完成！", "tw": "做號登錄完成！", "en": "Login complete!"},
    "passkey.extracted": {"zh": "成功提取", "tw": "成功提取", "en": "Extracted"},
    "passkey.generated": {"zh": "成功生成", "tw": "成功生成", "en": "Generated"},
    "passkey.all_failed": {"zh": "全部创建失败", "tw": "全部創建失敗", "en": "All creation failed"},
    "passkey.all_login_failed": {"zh": "全部登录失败", "tw": "全部登錄失敗", "en": "All login failed"},
    "passkey.no_file": {"zh": "未找到 .Passkey 凭据文件", "tw": "未找到 .Passkey 憑據文件", "en": "No .Passkey credentials found"},
    "passkey.convert_progress": {"zh": "转换进度", "tw": "轉換進度", "en": "Conversion progress"},
    "passkey.session_unit": {"zh": "个会话", "tw": "個會話", "en": "sessions"},
    "passkey.secured": {"zh": "已安全加固", "tw": "已安全加固", "en": "Secured"},

    # ---- 整合号包 ----
    "merge.confirm": {"zh": "确认整合", "tw": "確認整合", "en": "Confirm Merge"},
    "merge.received": {"zh": "已接收第", "tw": "已接收第", "en": "Received pack #"},
    "merge.pending": {"zh": "个ZIP包待整合", "tw": "個ZIP包待整合", "en": "ZIP packs pending"},
    "merge.click_confirm": {"zh": "点击确认开始整合所有包", "tw": "點擊確認開始整合所有包", "en": "Tap confirm to merge all packs"},
    "merge.no_files": {"zh": "没有找到待整合的文件", "tw": "沒有找到待整合的文件", "en": "No files to merge"},
    "merge.merging": {"zh": "正在整合号包，请稍候...", "tw": "正在整合號包，請稍候...", "en": "Merging packs, please wait..."},
    "merge.no_session": {"zh": "未找到任何session文件", "tw": "未找到任何session文件", "en": "No session files found"},
    "merge.done": {"zh": "整合号包完成", "tw": "整合號包完成", "en": "Merge complete"},
    "merge.total_accounts": {"zh": "总账号数", "tw": "總帳號數", "en": "Total accounts"},

    # ---- 注册时间 ----
    "regtime.processing": {"zh": "开始处理注册时间任务...", "tw": "開始處理註冊時間任務...", "en": "Processing registration time..."},
    "regtime.result_caption": {"zh": "按注册时间分类的结果", "tw": "按註冊時間分類的結果", "en": "Results by registration time"},
    "regtime.done": {"zh": "注册时间筛选完成", "tw": "註冊時間篩選完成", "en": "Registration time filtering complete"},
    "regtime.date_cats": {"zh": "日期分类", "tw": "日期分類", "en": "Date categories"},

    # ---- 支付 / 管理 ----
    "pay.config_error": {"zh": "支付系统配置错误，请联系管理员。", "tw": "支付系統配置錯誤，請聯繫管理員。", "en": "Payment system misconfigured, contact admin."},
    "pay.pay_now": {"zh": "立即支付", "tw": "立即支付", "en": "Pay Now"},
    "pay.order_detail": {"zh": "订单详情", "tw": "訂單詳情", "en": "Order Details"},
    "pay.order_no": {"zh": "订单号", "tw": "訂單號", "en": "Order ID"},
    "pay.amount": {"zh": "金额", "tw": "金額", "en": "Amount"},
    "pay.expire": {"zh": "过期时间", "tw": "過期時間", "en": "Expires"},
    "pay.order_expired": {"zh": "订单已过期", "tw": "訂單已過期", "en": "Order expired"},
    "pay.valid_time": {"zh": "有效时间：5分钟", "tw": "有效時間：5分鐘", "en": "Valid for 5 minutes"},
    "pay.pay_success": {"zh": "支付成功！", "tw": "支付成功！", "en": "Payment successful!"},
    "pay.thanks": {"zh": "感谢您的支持，您已成为 VIP 用户", "tw": "感謝您的支持，您已成為 VIP 用戶", "en": "Thanks for your support, you are now a VIP"},
    "pay.resend_start": {"zh": "请重新发送 /start 使用功能", "tw": "請重新發送 /start 使用功能", "en": "Resend /start to use features"},
    "pay.gen_fail": {"zh": "无法生成支付链接，请联系管理员或稍后再试。", "tw": "無法生成支付鏈接，請聯繫管理員或稍後再試。", "en": "Cannot generate payment link, contact admin."},
    "pay.waiting_result": {"zh": "正在等待支付结果，请在完成支付后稍等片刻...", "tw": "正在等待支付結果，請在完成支付後稍等片刻...", "en": "Waiting for payment result, please wait a moment after paying..."},
    "pay.auto_expire": {"zh": "订单5分钟后自动过期，过期后需重新生成", "tw": "訂單5分鐘後自動過期，過期後需重新生成", "en": "Order expires in 5 minutes, regenerate if expired"},
    "admin.usage_vip": {"zh": "用法: /vip 用户ID", "tw": "用法: /vip 用戶ID", "en": "Usage: /vip USER_ID"},
    "admin.usage_unvip": {"zh": "用法: /unvip 用户ID", "tw": "用法: /unvip 用戶ID", "en": "Usage: /unvip USER_ID"},
    "admin.vip_set": {"zh": "已升级为 VIP", "tw": "已升級為 VIP", "en": "Upgraded to VIP"},
    "admin.user_not_found": {"zh": "找不到该用户", "tw": "找不到該用戶", "en": "User not found"},
    "admin.downgraded": {"zh": "已降级为普通用户", "tw": "已降級為普通用戶", "en": "Downgraded to normal user"},
    "admin.broadcast_input": {"zh": "请输入要广播的消息内容", "tw": "請輸入要廣播的消息內容", "en": "Enter the broadcast message"},
    "admin.broadcast_to_users": {"zh": "将发送给 {n} 个用户：", "tw": "將發送給 {n} 個用戶：", "en": "Will be sent to {n} users:"},
    "admin.broadcast_start": {"zh": "开始广播", "tw": "開始廣播", "en": "Broadcasting"},
    "admin.broadcast_per_second": {"zh": "共 {n} 个用户，每秒20条...", "tw": "共 {n} 個用戶，每秒20條...", "en": "{n} users total, 20 per second..."},
    "admin.broadcast_done": {"zh": "广播完成", "tw": "廣播完成", "en": "Broadcast complete"},
    "admin.success": {"zh": "成功", "tw": "成功", "en": "success"},
    "admin.fail": {"zh": "失败", "tw": "失敗", "en": "fail"},
    "admin.user": {"zh": "用户", "tw": "用戶", "en": "User"},

    # ---- 加入频道 ----
    "join.click_join": {"zh": "点击加入频道/群组", "tw": "點擊加入頻道/群組", "en": "Join Channel/Group"},
    "join.required": {"zh": "请先加入频道后再使用。", "tw": "請先加入頻道後再使用。", "en": "Please join the channel first."},

    # ---- 转API/验证码网页（luyou.py 错误）----
    "web.account_banned": {"zh": "账号已被 Telegram 封禁，无法登录", "tw": "帳號已被 Telegram 封禁，無法登錄", "en": "Account banned by Telegram"},
    "web.flood_wait": {"zh": "请求频率过高，请稍后重试", "tw": "請求頻率過高，請稍後重試", "en": "Too frequent, retry later"},
    "web.two_fa_required": {"zh": "账号开启了二次验证，请联系分销商", "tw": "帳號開啟了二次驗證，請聯繫分銷商", "en": "2FA enabled, contact distributor"},
    "web.unauthorized": {"zh": "会话未授权，需要重新登录", "tw": "會話未授權，需要重新登錄", "en": "Session unauthorized"},
    "web.auth_key_invalid": {"zh": "授权密钥无效，请重置会话", "tw": "授權密鑰無效，請重置會話", "en": "Auth key invalid"},
    "web.api_config_invalid": {"zh": "API 配置错误，请联系管理员", "tw": "API 配置錯誤，請聯繫管理員", "en": "API config error"},
    "web.code_expired": {"zh": "验证码已过期，请重新获取", "tw": "驗證碼已過期，請重新獲取", "en": "Code expired"},
    "web.phone_invalid": {"zh": "手机号无效", "tw": "手機號無效", "en": "Invalid phone number"},
    "web.phone_not_registered": {"zh": "该手机号未注册 Telegram", "tw": "該手機號未註冊 Telegram", "en": "Phone not registered"},
    "web.session_format": {"zh": "Session 文件格式损坏", "tw": "Session 文件格式損壞", "en": "Session format corrupted"},
    "web.client_create": {"zh": "客户端创建失败", "tw": "客戶端創建失敗", "en": "Client creation failed"},
    "web.no_code": {"zh": "暂未接收到最新验证码，请稍后重试", "tw": "暫未接收到最新驗證碼，請稍後重試", "en": "No code received yet, retry later"},
    "web.fail_default": {"zh": "获取验证码失败，请稍后重试", "tw": "獲取驗證碼失敗，請稍後重試", "en": "Failed to get code, retry later"},
}


# ============ 语言判定 ============

def resolve_lang(lang_code, stored=None):
    """根据存储偏好或 language_code 解析语言。返回 'zh' | 'tw' | 'en'。"""
    if stored in ("zh", "tw", "en"):
        return stored
    if lang_code:
        lc = str(lang_code).lower()
        if any(x in lc for x in ("tw", "hk", "hant", "mo")):
            return "tw"
        if "zh" in lc:
            return "zh"
        return "en"
    return "zh"


def tr(key, lang="zh"):
    """取文案。缺语言回退简体，再缺省回退 key 本身。支持 {x} 占位符，调用方自行 .format()。"""
    entry = STRINGS.get(key)
    if not entry:
        return key
    return entry.get(lang) or entry.get("zh") or key


def lang_from_update(update):
    """从 update 解析当前用户语言。手动偏好优先，否则按 language_code。"""
    try:
        user = update.effective_user
    except Exception:
        return "zh"
    lang_code = getattr(user, "language_code", None)
    stored = None
    try:
        from pay import load_all_users
        uid = str(getattr(user, "id", ""))
        data = load_all_users()
        stored = data.get(uid, {}).get("lang")
    except Exception:
        stored = None
    return resolve_lang(lang_code, stored)


def get_env_i18n(key, lang="zh"):
    """读取 env 中某个 *_BACK 文案的三语版本。

    优先顺序：key（简体原键） -> key_EN -> key_TW -> i18n 内置同名键。
    返回已处理 \\n 的字符串。
    """
    env_key = {
        "zh": key,
        "en": f"{key}_EN",
        "tw": f"{key}_TW",
    }.get(lang, key)

    val = os.getenv(env_key, "")
    if val:
        return val.replace("\\n", "\n")
    # 回退到内置字典（键名去掉 _BACK 后缀，转小写）
    builtin_key = key.replace("_BACK", "").lower()
    builtin = STRINGS.get(builtin_key, {}).get(lang)
    if builtin:
        return builtin
    # 最后回退简体 env 或空串
    fallback = os.getenv(key, "")
    return fallback.replace("\\n", "\n") if fallback else ""
