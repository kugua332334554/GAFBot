import os
import json
import asyncio
from telethon import TelegramClient, events
from flask import Flask, request
import logging
from dotenv import load_dotenv
import re
import time

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("TELEGRAM_APP_ID"))
API_HASH = os.getenv("TELEGRAM_APP_HASH")
API_PORT = int(os.getenv("API_PORT", "7788"))

code_cache = {}
client_connections = {}

SUC_HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>成功找到绒布球喵~</title>
    <link rel="stylesheet" href="https://unpkg.com/mdui@2/mdui.css">
    <script src="https://unpkg.com/mdui@2/mdui.global.js"></script>
    <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">
    
    <style>
        :root {
            --mdui-color-primary: #00ff59dd; 
        }

        body {
            background-color: rgb(var(--mdui-color-surface-container-low));
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            font-family: 'Roboto', 'Noto Sans SC', sans-serif;
            -webkit-font-smoothing: antialiased;
        }

        .result-card {
            display: flex;
            width: calc(100% - 32px);
            max-width: 900px;
            min-height: 600px;
            border-radius: 28px;
            overflow: hidden;
            animation: m3-entrance 0.6s cubic-bezier(0.34, 1.56, 0.64, 1);
            background-color: rgb(var(--mdui-color-surface));
        }

        @keyframes m3-entrance {
            from { opacity: 0; transform: scale(0.9) translateY(30px); }
            to { opacity: 1; transform: scale(1) translateY(0); }
        }

        .side-status {
            width: 35%;
            background-color: var(--mdui-color-primary);
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            color: white;
            padding: 24px;
        }

        .side-status mdui-typography[variant="title-large"] {
            font-size: 30px !important;
            font-weight: 400;
            margin-top: 12px;
        }

        .content-area {
            flex: 1;
            padding: 40px 48px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            background-color: rgb(var(--mdui-color-surface));
        }

        .code-section {
            display: flex;
            flex-direction: column;
            gap: 16px;
            margin-bottom: 32px;
        }

        .code-box {
            background-color: rgb(var(--mdui-color-surface-container-high));
            padding: 16px 24px;
            border-radius: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border: 1px solid rgb(var(--mdui-color-outline-variant));
            transition: all 0.2s;
        }

        .code-box:hover {
            background-color: rgb(var(--mdui-color-surface-container-highest));
        }

        .code-label {
            font-size: 14px;
            color: rgb(var(--mdui-color-on-surface-variant));
            margin-bottom: 4px;
            display: block;
        }

        .code-value {
            font-family: 'JetBrains Mono', 'Roboto Mono', monospace;
            font-size: 36px;
            font-weight: 700;
            letter-spacing: 2px;
            color: var(--mdui-color-primary);
        }

        .helper-text {
            color: rgb(var(--mdui-color-on-surface-variant));
            font-size: 16px;
            line-height: 1.5;
            margin-bottom: 24px;
            font-weight: 300;
        }

        .status-icon {
            background: rgba(255,255,255,0.15);
            padding: 24px;
            border-radius: 50%;
            animation: check-pop 0.7s 0.3s backwards cubic-bezier(0.175, 0.885, 0.32, 1.275);
        }

        @media (max-width: 600px) {
            .result-card { flex-direction: column; max-width: 380px; }
            .side-status { width: 100%; padding: 32px 24px; }
            .content-area { padding: 32px 24px; }
            .code-value { font-size: 28px; }
        }

        @keyframes check-pop {
            0% { transform: scale(0) rotate(-45deg); opacity: 0; }
            100% { transform: scale(1) rotate(0); opacity: 1; }
        }
    </style>
</head>
<body>
    <mdui-card class="result-card" variant="elevated">
        <div class="side-status">
            <div class="status-icon">
                <mdui-icon name="sms" style="font-size: 64px; color: white;"></mdui-icon>
            </div>
            <mdui-typography variant="title-large">获取成功</mdui-typography>
        </div>

        <div class="content-area">
            <mdui-typography variant="headline-small" style="margin-bottom: 8px; color: var(--mdui-color-primary); font-weight: 500;">您的登录凭据</mdui-typography>

            <div class="code-section">
                <div class="code-box">
                    <div>
                        <span class="code-label">验证码 (Verify Code)</span>
                        <div class="code-value" id="verify-code">{code}</div>
                    </div>
                    <mdui-button-icon onclick="copyText('verify-code')" icon="content_copy" variant="filled"></mdui-button-icon>
                </div>

                <div class="code-box">
                    <div>
                        <span class="code-label">2FA 密码 (2FA Password)</span>
                        <div class="code-value" id="two-fa-code">{twofa}</div>
                    </div>
                    <mdui-button-icon onclick="copyText('two-fa-code')" icon="content_copy" variant="filled"></mdui-button-icon>
                </div>
            </div>
        </div>
    </mdui-card>

    <script>
        function copyText(elementId) {
            const text = document.getElementById(elementId).innerText;
            navigator.clipboard.writeText(text).then(() => {
                mdui.snackbar({
                    message: '复制成功：' + text,
                    placement: 'top',
                    closeable: true
                });
            }).catch(err => {
                console.error('无法复制: ', err);
            });
        }
    </script>
</body>
</html>'''

def get_twofa_from_api(sid):
    try:
        api_path = "acd/api.json"
        if os.path.exists(api_path):
            with open(api_path, 'r') as f:
                api_data = json.load(f)
                return api_data.get(sid, {}).get('two_fa', '------')
    except:
        pass
    return '------'

@app.route('/getcode', methods=['GET'])
def get_code():
    sid = request.args.get('id')
    if not sid:
        return "缺少id参数", 400
    
    session_path = f"acd/{sid}.session"
    if not os.path.exists(session_path):
        return "Session不存在", 404
    
    twofa = get_twofa_from_api(sid)
    
    if sid in code_cache:
        return SUC_HTML.replace('{code}', code_cache[sid]).replace('{twofa}', twofa)
    
    code = fetch_code_sync(sid, session_path)
    if code:
        return SUC_HTML.replace('{code}', code).replace('{twofa}', twofa)
    return "未获取到验证码", 404

def fetch_code_sync(sid, session_path):
    async def _fetch():
        client = TelegramClient(session_path, API_ID, API_HASH)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return None
            
            msgs = await client.get_messages(777000, limit=50)
            for msg in msgs:
                text = msg.message or ''
                codes = re.findall(r'\d{5,6}', text)
                if codes:
                    code_cache[sid] = codes[0]
                    logger.info(f"从历史消息获取验证码 {codes[0]} for {sid}")
                    return codes[0]
            
            future = asyncio.Future()
            
            @client.on(events.NewMessage)
            async def handler(event):
                if event.sender_id == 777000:
                    text = event.message.message or ''
                    codes = re.findall(r'\d{5,6}', text)
                    if codes and not future.done():
                        code_cache[sid] = codes[0]
                        logger.info(f"从新消息获取验证码 {codes[0]} for {sid}")
                        future.set_result(codes[0])
                        await client.disconnect()
            
            try:
                result = await asyncio.wait_for(future, timeout=30)
                return result
            except asyncio.TimeoutError:
                logger.info(f"获取验证码超时 {sid}")
                return None
                
        except Exception as e:
            logger.error(f"获取验证码失败 {sid}: {e}")
            return None
        finally:
            await client.disconnect()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=API_PORT, debug=False)
