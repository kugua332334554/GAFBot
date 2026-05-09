import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import json
import base64
import hashlib
import cbor2
from pathlib import Path
from datetime import datetime
import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from dotenv import load_dotenv
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from telethon.errors import SessionPasswordNeededError
from telethon.errors.rpcbaseerrors import BadRequestError
from telethon import functions
from telethon.tl.types import (
    DataJSON, 
    InputPasskeyCredentialPublicKey, 
    InputPasskeyResponseRegister,
    InputPasskeyResponseLogin
)
from opentele.tl import TelegramClient
from opentele.api import API
import random
logger = logging.getLogger(__name__)
load_dotenv()

PASSKEY_BACK = os.getenv("PASSKEY_BACK", "🔑 <b>Passkey 功能管理</b>\n\n请选择您要执行的操作：").replace('\\n', '\n')
user_passkey_states = {}
def get_random_proxy_dict():
    proxy_file = "proxy.txt"
    if not os.path.exists(proxy_file):
        return None
    try:
        import random
        valid_proxies = []
        current_time = time.time()
        with open(proxy_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                parts = line.split(':')
                if len(parts) >= 5:
                    ip, port, username, password, expire_ts = parts[:5]
                    try:
                        if current_time < int(expire_ts):
                            valid_proxies.append({
                                'proxy_type': 'http', 'addr': ip, 'port': int(port),
                                'username': username, 'password': password, 'rdns': True
                            })
                    except ValueError: continue
        if valid_proxies:
            return random.choice(valid_proxies)
    except Exception:
        pass
    return None

def B64UrlEncode(Data: bytes) -> str: 
    return base64.urlsafe_b64encode(Data).decode("ascii").rstrip("=")

def B64UrlDecodeToLatin1(Text: str) -> str: 
    Pad = "=" * ((4 - len(Text) % 4) % 4)
    return base64.urlsafe_b64decode(Text + Pad).decode("latin1")

async def generate_json_for_session(session_path, client, me, api_id, api_hash, official_api, twofa=None):
    session_path = Path(session_path)
    json_path = session_path.with_suffix('.json')
    phone = me.phone if me.phone else session_path.stem
    reg_time = datetime.now().strftime("%Y-%m-%d")

    device_model = getattr(official_api, 'device_model', 'Desktop')
    system_version = getattr(official_api, 'system_version', '')
    app_version = getattr(official_api, 'app_version', '')
    system_lang_code = getattr(official_api, 'system_lang_code', 'en')
    lang_pack = getattr(official_api, 'lang_pack', '')
    lang_code = getattr(official_api, 'lang_code', 'en')
    pid = getattr(official_api, 'pid', random.randint(100000, 999999))  

    json_data = {
        "api_id": api_id,
        "api_hash": api_hash,
        "device_model": device_model,
        "system_version": system_version,
        "app_version": app_version,
        "system_lang_code": system_lang_code,
        "lang_pack": lang_pack,
        "lang_code": lang_code,
        "pid": pid,
        "user_id": me.id,
        "phone": phone,
        "twofa": twofa if twofa is not None else "",   
        "password": twofa if twofa is not None else "",
        "app_id": api_id,
        "app_hash": api_hash,
        "session_file": session_path.stem,            
        "device": device_model,
        "username": me.username or "",
        "sex": None,
        "avatar": "img/default.png",
        "package_id": "",                              
        "installer": "",                               
        "ipv6": False,
        "SDK": system_version,                          
        "sdk": system_version,                       
        "system_lang_pack": system_lang_code,         
        "premium": getattr(me, 'premium', False),
        "reg_time": reg_time
    }

    # 写入文件
    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        return str(json_path)
    except Exception as e:
        logger.error(f"生成 JSON 失败 {session_path}: {e}")
        return None

async def create_single_passkey(session_file, json_file, out_dir, api_id, api_hash):
    client = None
    try:
        json_config = {}
        if json_file and os.path.exists(json_file):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    json_config = json.load(f)
            except: pass
            
        final_api_id = int(json_config.get('app_id', json_config.get('api_id', api_id)))
        final_api_hash = str(json_config.get('app_hash', json_config.get('api_hash', api_hash)))
        twofa_pwd = json_config.get('twofa', json_config.get('password', ''))

        official_api = API.TelegramDesktop.Generate()
        official_api.api_id = final_api_id
        official_api.api_hash = final_api_hash
        if json_config.get('device_model'): official_api.device_model = json_config['device_model']

        proxy = get_random_proxy_dict()
        client = TelegramClient(str(session_file), api=official_api, proxy=proxy)
        await client.connect()

        if not await client.is_user_authorized():
            return False, "会话未授权/已掉线"

        Me = await client.get_me()
        
        # 发起注册请求
        Result = await client(functions.account.InitPasskeyRegistrationRequest())
        OptionsJson = json.loads(Result.options.data)
        PublicKey = OptionsJson.get("publicKey", {})
        ChallengeB64 = PublicKey["challenge"]
        RpId = PublicKey["rp"]["id"]

        # 生成密钥
        PrivateKey = ec.generate_private_key(ec.SECP256R1())
        PublicKeyObj = PrivateKey.public_key()
        Pn = PublicKeyObj.public_numbers()
        PrivateKeyHex = PrivateKey.private_numbers().private_value.to_bytes(32, "big").hex()

        # 构造cbor和认证数据
        CoseKey = {1: 2, 3: -7, -1: 1, -2: Pn.x.to_bytes(32, "big"), -3: Pn.y.to_bytes(32, "big")}
        CoseKeyCbor = cbor2.dumps(CoseKey)
        RpIdHash = hashlib.sha256(RpId.encode("utf-8")).digest()
        Flags = b"\x45"
        SignCount = b"\x00\x00\x00\x00"
        Aaguid = b"\x00" * 16
        CredentialId = os.urandom(32)
        CredIdLen = len(CredentialId).to_bytes(2, "big")
        AuthData = RpIdHash + Flags + SignCount + Aaguid + CredIdLen + CredentialId + CoseKeyCbor

        ClientData = {"type": "webauthn.create", "challenge": ChallengeB64, "origin": "https://web.telegram.org", "crossOrigin": False}
        AttObj = {"fmt": "none", "attStmt": {}, "authData": AuthData}
        
        RegisterResponse = InputPasskeyResponseRegister(
            client_data=DataJSON(data=json.dumps(ClientData, separators=(",", ":"))), 
            attestation_data=cbor2.dumps(AttObj)
        )
        CredIdB64Url = B64UrlEncode(CredentialId)
        Credential = InputPasskeyCredentialPublicKey(id=CredIdB64Url, raw_id=CredIdB64Url, response=RegisterResponse)
        
        await client(functions.account.RegisterPasskeyRequest(credential=Credential))

        UserHandleB64 = ((PublicKey.get("user") or {}).get("id") if isinstance(PublicKey, dict) else None)
        UserHandlePlain = B64UrlDecodeToLatin1(UserHandleB64) if UserHandleB64 else ""

        PasskeyPayload = {
            "Phone": Me.phone or session_file.stem,
            "TwoFA": twofa_pwd,
            "CredentialId": CredIdB64Url,
            "PrivateKeyHex": PrivateKeyHex,
            "UserHandle": UserHandlePlain,
            "UserHandleEncoding": "plain",
            "SignCount": 0,
            "RpId": RpId,
            "Origin": "https://web.telegram.org",
            "SigFormat": "der"
        }
        
        out_file = out_dir / f"{Me.phone or session_file.stem}.Passkey"
        out_file.write_text(json.dumps(PasskeyPayload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, "成功"
        
    except FloodWaitError as e:
        return False, f"频繁限制: {e.seconds}秒"
    except Exception as e:
        return False, f"错误: {str(e)[:20]}"
    finally:
        if client: await client.disconnect()

async def login_single_passkey(passkey_file, out_dir, api_id, api_hash):
    """使用Passkey文件执行登录做号"""
    client = None
    try:
        raw_data = passkey_file.read_text(encoding="utf-8")
        PasskeyData = json.loads(raw_data)
        
        phone = PasskeyData.get("Phone", passkey_file.stem)
        SessionPath = out_dir / f"{phone}.session"
        
        # 上下文准备
        CredentialId = PasskeyData["CredentialId"]
        PrivateKeyHex = PasskeyData["PrivateKeyHex"]
        RpId = PasskeyData["RpId"]
        Origin = PasskeyData["Origin"]
        UserHandle = PasskeyData["UserHandle"]
        SignCount = int(PasskeyData.get("SignCount", 0))
        Flags = bytes.fromhex(str(PasskeyData.get("LoginFlagsHex", "01")))
        
        RpIdHash = hashlib.sha256(RpId.encode("utf-8")).digest()
        PrivateKey = ec.derive_private_key(int(PrivateKeyHex, 16), ec.SECP256R1())

        official_api = API.TelegramDesktop.Generate()
        official_api.api_id = api_id
        official_api.api_hash = api_hash
        
        proxy = get_random_proxy_dict()
        client = TelegramClient(str(SessionPath), api=official_api, proxy=proxy)
        await client.connect()

        MaxAttempts = 3
        CurrentSignCount = SignCount + 1
        LoginOk = False

        for Attempt in range(1, MaxAttempts + 1):
            try:
                Options = await client(functions.auth.InitPasskeyLoginRequest(api_id, api_hash))
                OptionsJson = json.loads(Options.options.data)
                Challenge = OptionsJson.get("publicKey", {}).get("challenge")

                AuthenticatorData = RpIdHash + Flags + CurrentSignCount.to_bytes(4, "big", signed=False)
                ClientDataText = json.dumps({"type": "webauthn.get", "challenge": Challenge, "origin": Origin, "crossOrigin": False}, separators=(",", ":"))
                ClientDataHash = hashlib.sha256(ClientDataText.encode("utf-8")).digest()
                Signature = PrivateKey.sign(AuthenticatorData + ClientDataHash, ec.ECDSA(hashes.SHA256()))

                Response = InputPasskeyResponseLogin(
                    client_data=DataJSON(data=ClientDataText), 
                    authenticator_data=AuthenticatorData, 
                    signature=Signature, 
                    user_handle=UserHandle
                )
                Credential = InputPasskeyCredentialPublicKey(id=CredentialId, raw_id=CredentialId, response=Response)
                
                await client(functions.auth.FinishPasskeyLoginRequest(credential=Credential))
                LoginOk = True
                break
            except SessionPasswordNeededError:
                TwoFa = PasskeyData.get("TwoFA")
                if TwoFa:
                    await client.sign_in(password=TwoFa)
                    LoginOk = True
                else:
                    return False, "需提供2FA密码"
                break
            except BadRequestError as E:
                if "PASSKEY_CHALLENGE_EXPIRED" in str(E) and Attempt < MaxAttempts:
                    CurrentSignCount += 1
                    await asyncio.sleep(0.15)
                    continue
                raise E

        if not LoginOk: return False, "登录失败"

        # 回写下次计数
        Me = await client.get_me()
        
        # 生成配套JSON
        await generate_json_for_session(SessionPath, client, Me, api_id, api_hash, official_api, PasskeyData.get("TwoFA"))
        return True, "成功"

    except Exception as e:
        return False, f"错误: {str(e)[:20]}"
    finally:
        if client: await client.disconnect()

async def show_passkey_menu(update, context):
    from bot import create_back_button
    
    keyboard = [
        [
            InlineKeyboardButton(
                text="创建Passkey", 
                callback_data="passkey_create",
                icon_custom_emoji_id="6005570495603282482",
                style="primary"
            ),
            InlineKeyboardButton(
                text="Passkey登录", 
                callback_data="passkey_login",
                icon_custom_emoji_id="6019523512908124649",
                style="success"
            )
        ],
        [create_back_button()]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        text=PASSKEY_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def handle_passkey_selection(update, context):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    from bot import create_back_button

    if data == "passkey_create":
        text = "<tg-emoji emoji-id='6005570495603282482'>🔑</tg-emoji> <b>创建 Passkey</b>\n\n请上传包含 <code>.session</code> 和配套 <code>.json</code> 的 ZIP 压缩包，将为您导出 <code>.Passkey</code> 凭据文件。"
        user_passkey_states[user_id] = {"state": "create", "waiting_zip": True}
    else:
        text = "<tg-emoji emoji-id='6019523512908124649'>📱</tg-emoji> <b>Passkey 登录做号</b>\n\n请上传包含 <code>.Passkey</code> 凭据文件的 ZIP 压缩包，将为您自动登录并生成 <code>.session</code> 和 <code>.json</code>。"
        user_passkey_states[user_id] = {"state": "login", "waiting_zip": True}

    keyboard = [[create_back_button()]]
    await query.edit_message_text(
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_passkey_document(update, context, user_id):
    document = update.message.document
    from bot import create_back_button
    
    if not document.file_name.lower().endswith('.zip'):
        keyboard = [[create_back_button()]]
        await update.message.reply_text(
            "<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 请上传 ZIP 格式的压缩包",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    mode = user_passkey_states[user_id].get("state")
    status_msg = await update.message.reply_text("<tg-emoji emoji-id='5942826671290715541'>📥</tg-emoji> 正在下载文件...", parse_mode='HTML')

    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/passkey_{mode}_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        
        user_passkey_states.pop(user_id, None)

        if mode == "create":
            await process_passkey_create(update, context, zip_path, user_id, status_msg)
        else:
            await process_passkey_login(update, context, zip_path, user_id, status_msg)
            
        try: os.remove(zip_path)
        except: pass

    except Exception as e:
        logger.error(f"Passkey 文件处理失败: {e}")
        keyboard = [[create_back_button()]]
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    finally:
        try: await status_msg.delete()
        except: pass


async def process_passkey_create(update, context, zip_path, user_id, status_msg):
    api_id = int(os.getenv("TELEGRAM_APP_ID", "2040"))
    api_hash = os.getenv("TELEGRAM_APP_HASH", "b18441a1ff607e10a989891a5462e627")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = Path(temp_dir) / "extracted"
        out_dir = Path(temp_dir) / "output"
        os.makedirs(extract_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 解压失败: {str(e)}", parse_mode='HTML')
            return

        session_files = list(extract_dir.rglob("*.session"))
        if not session_files:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 未找到 .session 文件", parse_mode='HTML')
            return

        success_count = 0
        fail_count = 0

        for i, session_file in enumerate(session_files, 1):
            if i % 3 == 0 or i == len(session_files):
                try:
                    await status_msg.edit_text(
                        f"""<tg-emoji emoji-id="5942826671290715541">⚙️</tg-emoji> <b>正在创建 Passkey</b>\n\n进度: {i}/{len(session_files)}\n<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: {success_count} | <tg-emoji emoji-id="5886496611835581345">❌</tg-emoji> 失败: {fail_count}""",
                        parse_mode='HTML'
                    )
                except: pass

            json_file = session_file.with_suffix(".json")
            is_ok, reason = await create_single_passkey(session_file, json_file, out_dir, api_id, api_hash)
            if is_ok: success_count += 1
            else: fail_count += 1
            await asyncio.sleep(0.5)

        if success_count > 0:
            result_zip = Path(temp_dir) / "Passkeys_Exported.zip"
            with zipfile.ZipFile(result_zip, 'w') as zipf:
                for f in out_dir.iterdir():
                    zipf.write(f, f.name)
            
            with open(result_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"Passkeys_{int(time.time())}.zip",
                    caption=f"<tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> <b>创建完成！</b>\n成功提取: {success_count} 个",
                    parse_mode='HTML'
                )
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 全部创建失败", parse_mode='HTML')


async def process_passkey_login(update, context, zip_path, user_id, status_msg):
    api_id = int(os.getenv("TELEGRAM_APP_ID", "2040"))
    api_hash = os.getenv("TELEGRAM_APP_HASH", "b18441a1ff607e10a989891a5462e627")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = Path(temp_dir) / "extracted"
        out_dir = Path(temp_dir) / "output"
        os.makedirs(extract_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 解压失败: {str(e)}", parse_mode='HTML')
            return

        passkey_files = list(extract_dir.rglob("*.Passkey"))
        if not passkey_files:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 未找到 .Passkey 凭据文件", parse_mode='HTML')
            return

        success_count = 0
        fail_count = 0

        for i, pk_file in enumerate(passkey_files, 1):
            if i % 3 == 0 or i == len(passkey_files):
                try:
                    await status_msg.edit_text(
                        f"""<tg-emoji emoji-id="5942826671290715541">⚙️</tg-emoji> <b>正在通过 Passkey 登录</b>\n\n进度: {i}/{len(passkey_files)}\n<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: {success_count} | <tg-emoji emoji-id="5886496611835581345">❌</tg-emoji> 失败: {fail_count}""",
                        parse_mode='HTML'
                    )
                except: pass

            is_ok, reason = await login_single_passkey(pk_file, out_dir, api_id, api_hash)
            if is_ok: success_count += 1
            else: fail_count += 1
            await asyncio.sleep(0.5)

        if success_count > 0:
            result_zip = Path(temp_dir) / "Passkey_Sessions.zip"
            with zipfile.ZipFile(result_zip, 'w') as zipf:
                for f in out_dir.iterdir():
                    zipf.write(f, f.name)
            
            with open(result_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"Passkey_Login_{int(time.time())}.zip",
                    caption=f"<tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> <b>做号登录完成！</b>\n成功生成: {success_count} 个会话",
                    parse_mode='HTML'
                )
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 全部登录失败", parse_mode='HTML')
