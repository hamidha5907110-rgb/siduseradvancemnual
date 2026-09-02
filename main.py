#!/usr/bin/env python3
"""
MANSURI PREMIUM HOSTER — Manual Userbot Hoster
Accepts user-uploaded scripts (.py / .zip), links via OTP, and hosts them 24/7.
"""

import asyncio
import ast
import hashlib
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
    FloodWaitError,
)
from telethon.sessions import StringSession

# ─────────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@MANSURIxGOD").strip()
MAX_USERBOTS = max(1, int(os.getenv("MAX_USERBOTS", "50") or 50))
MAX_ACCOUNTS_PER_USER = max(1, int(os.getenv("MAX_ACCOUNTS_PER_USER", "3") or 3))
DEFAULT_API_ID = int(os.getenv("TELEGRAM_API_ID", "2040"))
DEFAULT_API_HASH = os.getenv("TELEGRAM_API_HASH", "b18441a1ff607e10a989891a5462e627").strip()
MAX_UPLOAD_MB = 20
MAX_ZIP_FILES = 250
PIP_TIMEOUT = 300

if not BOT_TOKEN or not OWNER_ID:
    print("⚠️ Please set BOT_TOKEN and OWNER_ID in the script or environment variables.")

PACKAGE_MAP = {
    "PIL": "Pillow", "cv2": "opencv-python", "bs4": "beautifulsoup4", "yaml": "PyYAML",
    "telethon": "Telethon", "pyrogram": "pyrogram tgcrypto", "tgcrypto": "tgcrypto",
    "telegram": "python-telegram-bot", "dotenv": "python-dotenv", "Crypto": "pycryptodome",
    "crypto": "pycryptodome", "sqlalchemy": "SQLAlchemy", "aiohttp": "aiohttp",
    "aiofiles": "aiofiles", "requests": "requests", "yt_dlp": "yt-dlp", "gtts": "gTTS",
    "qrcode": "qrcode", "psutil": "psutil"
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("mansuri-hoster")
START_TIME = time.time()

# ─────────────────────────────────────────────────────────────────────────
#  FONT STYLES (Mansuri Branding)
# ─────────────────────────────────────────────────────────────────────────
def bold_serif(t: str) -> str:
    result = ""
    for c in t:
        if 'A' <= c <= 'Z': result += chr(ord(c) - ord('A') + 0x1D400)
        elif 'a' <= c <= 'z': result += chr(ord(c) - ord('a') + 0x1D41A)
        elif '0' <= c <= '9': result += chr(ord(c) - ord('0') + 0x1D7CE)
        else: result += c
    return result

def italic_serif(t: str) -> str:
    special = {'h': '𝒽', 'e': '𝑒', 'i': '𝑖', 'j': '𝑗'}
    result = ""
    for c in t:
        if c in special: result += special[c]
        elif 'A' <= c <= 'Z': result += chr(ord(c) - ord('A') + 0x1D434)
        elif 'a' <= c <= 'z': result += chr(ord(c) - ord('a') + 0x1D44E)
        else: result += c
    return result

def script_font(t: str) -> str:
    result = ""
    for c in t:
        if 'A' <= c <= 'Z': result += chr(ord(c) - ord('A') + 0x1D4D0)
        elif 'a' <= c <= 'z': result += chr(ord(c) - ord('a') + 0x1D4EA)
        else: result += c
    return result

def double_struck(t: str) -> str:
    special_map = {'C': 'ℂ', 'H': 'ℍ', 'N': 'ℕ', 'P': 'ℙ', 'Q': 'ℚ', 'R': 'ℝ', 'Z': 'ℤ'}
    result = ""
    for c in t:
        if c in special_map: result += special_map[c]
        elif 'A' <= c <= 'Z': result += chr(ord(c) - ord('A') + 0x1D538)
        elif 'a' <= c <= 'z': result += chr(ord(c) - ord('a') + 0x1D552)
        elif '0' <= c <= '9': result += chr(ord(c) - ord('0') + 0x1D7D8)
        else: result += c
    return result

def sans_bold(t: str) -> str:
    result = ""
    for c in t:
        if 'A' <= c <= 'Z': result += chr(ord(c) - ord('A') + 0x1D5D4)
        elif 'a' <= c <= 'z': result += chr(ord(c) - ord('a') + 0x1D5EE)
        elif '0' <= c <= '9': result += chr(ord(c) - ord('0') + 0x1D7EC)
        else: result += c
    return result

def mono(t: str) -> str:
    result = ""
    for c in t:
        if 'A' <= c <= 'Z': result += chr(ord(c) - ord('A') + 0x1D670)
        elif 'a' <= c <= 'z': result += chr(ord(c) - ord('a') + 0x1D68A)
        elif '0' <= c <= '9': result += chr(ord(c) - ord('0') + 0x1D7F6)
        else: result += c
    return result

def fraktur(t: str) -> str:
    special = {'C': 'ℭ', 'H': 'ℌ', 'I': 'ℑ', 'R': 'ℜ', 'Z': 'ℨ'}
    result = ""
    for c in t:
        if c in special: result += special[c]
        elif 'A' <= c <= 'Z': result += chr(ord(c) - ord('A') + 0x1D504)
        elif 'a' <= c <= 'z': result += chr(ord(c) - ord('a') + 0x1D51E)
        else: result += c
    return result

DIV = "━━━━━━━━━━━━━━━━━━━━━━━━━━"
DIV2 = "·͜·͜·͜·͜·͜·͜·͜·͜·͜·͜·͜·͜·͜·͜·͜·͜·͜·"
DIV3 = "⋯⋯⋯⋯⋯⋯⋯⋯⋯⋯⋯⋯⋯"
TOP = "╔══════════════════════════╗"
BOT = "╚══════════════════════════╝"
def esc(t: str) -> str: return html.escape(str(t), quote=False)

# ─────────────────────────────────────────────────────────────────────────
#  DATABASE & FILES
# ─────────────────────────────────────────────────────────────────────────
DB_DIR = Path(os.getcwd()) / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
_db_lock = threading.Lock()

def _db_path(*parts):
    p = DB_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def _write_json(path, data):
    with _db_lock:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

def user_exists(uid): return _db_path("users", str(uid), "meta.json").exists()

def save_user_meta(uid, data):
    path = _db_path("users", str(uid), "meta.json")
    existing = _read_json(path, {})
    existing.update(data)
    _write_json(path, existing)

def get_all_users():
    users_dir = DB_DIR / "users"
    if not users_dir.exists(): return []
    return [d.name for d in users_dir.iterdir() if d.is_dir() and d.name.isdigit()]

def get_accounts(uid): return _read_json(_db_path("users", str(uid), "accounts.json"), [])
def get_account(uid, slot):
    for a in get_accounts(uid):
        if a.get("slot") == slot: return a
    return None

def add_account(uid, acct):
    accounts = [a for a in get_accounts(uid) if a.get("slot") != acct.get("slot")]
    accounts.append(acct)
    _write_json(_db_path("users", str(uid), "accounts.json"), accounts)

def remove_account(uid, slot):
    accounts = [a for a in get_accounts(uid) if a.get("slot") != slot]
    _write_json(_db_path("users", str(uid), "accounts.json"), accounts)

def hosted_count():
    return sum(1 for uid in get_all_users() for a in get_accounts(int(uid)) if a.get("hosted"))

def is_blocked(uid): return uid in _read_json(_db_path("blocked.json"), [])
def block_user(uid):
    b = _read_json(_db_path("blocked.json"), [])
    if uid not in b: b.append(uid); _write_json(_db_path("blocked.json"), b)
def unblock_user(uid):
    b = [x for x in _read_json(_db_path("blocked.json"), []) if x != uid]
    _write_json(_db_path("blocked.json"), b)
def get_sudo_users(): return _read_json(_db_path("sudo.json"), [])
def is_owner(uid): return uid == OWNER_ID
def is_premium(uid): return is_owner(uid) or uid in get_sudo_users()

def script_root(uid: int, slot: int) -> Path: return DB_DIR / "scripts" / str(uid) / f"slot_{slot}"
def _phone_label(acct): return acct.get("phone", f"Account #{acct.get('slot', 0) + 1}")
def uptime_str():
    e = int(time.time() - START_TIME)
    h, r = divmod(e, 3600); m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"

# ─────────────────────────────────────────────────────────────────────────
#  AST SCANNER & API DETECTION
# ─────────────────────────────────────────────────────────────────────────
API_ID_RE = re.compile(r"(?im)\b(?:API_ID|api_id|TG_API_ID|TELEGRAM_API_ID)\b\s*[:=]\s*(?:int\(\s*)?[\"']?(\d{5,12})")
API_HASH_RE = re.compile(r"(?im)\b(?:API_HASH|api_hash|TG_API_HASH|TELEGRAM_API_HASH)\b\s*[:=]\s*(?:str\(\s*)?[\"']([A-Za-z0-9]{16,128})[\"']")

def detect_credentials(text: str) -> Tuple[int, str]:
    aid, ahash = None, None
    m_id = API_ID_RE.search(text)
    if m_id:
        try: aid = int(m_id.group(1))
        except: pass
    m_hash = API_HASH_RE.search(text)
    if m_hash: ahash = m_hash.group(1).strip()
    return aid or DEFAULT_API_ID, ahash or DEFAULT_API_HASH

def collect_all_imports(root: Path) -> List[str]:
    imports = set()
    import_re = re.compile(r"^\s*(?:from|import)\s+([a-zA-Z0-9_]+)", re.MULTILINE)
    for py_path in root.rglob("*.py"):
        if ".venv" in py_path.parts or "__pycache__" in py_path.parts: continue
        try:
            for m in import_re.finditer(py_path.read_text(encoding="utf-8", errors="ignore")):
                imports.add(m.group(1))
        except: pass
    return sorted(imports)

def safe_extract_zip(zip_path: Path, target: Path) -> Tuple[bool, str]:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if len(zf.infolist()) > MAX_ZIP_FILES: return False, "Too many files."
            zf.extractall(target)
        return True, "Extracted."
    except Exception as exc: return False, str(exc)

def find_entrypoint(root: Path) -> Optional[Path]:
    main = root / "main.py"
    if main.exists(): return main
    py_files = [p for p in root.rglob("*.py") if ".venv" not in p.parts and "__pycache__" not in p.parts and p.name != "run_wrapper.py"]
    return py_files[0] if py_files else None

# ─────────────────────────────────────────────────────────────────────────
#  SUBPROCESS ENGINE & SESSION INJECTION
# ─────────────────────────────────────────────────────────────────────────
_procs: Dict[Tuple[int, int], subprocess.Popen] = {}
_start_times: Dict[Tuple[int, int], float] = {}

def stop_script(uid: int, slot: int):
    key = (uid, slot)
    proc = _procs.pop(key, None)
    _start_times.pop(key, None)
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except:
            try: proc.kill()
            except: pass

async def start_script(uid: int, slot: int, script_path: Path, session_string: str, api_id: int, api_hash: str, phone: str = "") -> Tuple[bool, str]:
    key = (uid, slot)
    stop_script(uid, slot)

    root = script_path.parent
    entry_name = script_path.name
    venv_dir = root / ".venv"
    python_exe = venv_dir / "bin" / "python" if os.name != "nt" else venv_dir / "Scripts" / "python.exe"

    if not venv_dir.exists():
        p = await asyncio.create_subprocess_exec(sys.executable, "-m", "venv", str(venv_dir))
        await p.wait()

    exe_path = str(python_exe if python_exe.exists() else sys.executable)
    std_libs = set(getattr(sys, "stdlib_module_names", set()))
    packages = {"telethon", "tgcrypto", "aiohttp", "aiofiles", "requests", "Pillow"}
    for imp in collect_all_imports(root):
        if imp not in std_libs and not imp.startswith("_"):
            packages.add(PACKAGE_MAP.get(imp, imp))

    req_path = root / "requirements.txt"
    with open(req_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(packages)))

    pip_proc = await asyncio.create_subprocess_exec(exe_path, "-m", "pip", "install", "-r", str(req_path))
    try:
        await asyncio.wait_for(pip_proc.wait(), timeout=PIP_TIMEOUT)
    except asyncio.TimeoutError:
        return False, "Dependency installation timed out."

    wrapper_code = f"""
import os
import sys
import runpy

session_str = os.environ.get("SESSION_STRING", "")
api_id = os.environ.get("API_ID", "")
api_hash = os.environ.get("API_HASH", "")

try:
    import telethon
    from telethon.sessions import StringSession

    _orig_client_init = telethon.TelegramClient.__init__
    def _patched_client_init(self, session, *args, **kwargs):
        session = StringSession(session_str)
        if api_id and api_hash:
            kwargs['api_id'] = int(api_id)
            kwargs['api_hash'] = api_hash
        _orig_client_init(self, session, *args, **kwargs)
    telethon.TelegramClient.__init__ = _patched_client_init
    
    _orig_client_start = telethon.TelegramClient.start
    async def _patched_client_start(self, *args, **kwargs):
        if await self.is_user_authorized():
            return self
        return await _orig_client_start(self, *args, **kwargs)
    telethon.TelegramClient.start = _patched_client_start

except Exception as e:
    pass

try:
    import pyrogram
    _orig_pyro_init = pyrogram.Client.__init__
    def _patched_pyro_init(self, name, *args, **kwargs):
        kwargs['session_string'] = session_str
        if api_id and api_hash:
            kwargs['api_id'] = int(api_id)
            kwargs['api_hash'] = api_hash
        _orig_pyro_init(self, name, *args, **kwargs)
    pyrogram.Client.__init__ = _patched_pyro_init
except Exception as e:
    pass

target_script = "{entry_name}"
sys.argv = [target_script]
runpy.run_path(target_script, run_name="__main__")
"""
    wrapper_path = root / "run_wrapper.py"
    wrapper_path.write_text(wrapper_code.strip(), encoding="utf-8")

    env = os.environ.copy()
    env.update({
        "API_ID": str(api_id), "API_HASH": str(api_hash),
        "SESSION_STRING": str(session_string), "PHONE_NUMBER": str(phone),
        "OWNER_ID": str(uid), "USERBOT_OWNER_ID": str(uid),
        "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(root) + os.pathsep + env.get("PYTHONPATH", ""),
    })

    log_path = root / "runtime.log"
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    log_file.write(f"\n===== CONTAINER BOOT AT {time.ctime()} =====\n")

    proc = subprocess.Popen(
        [exe_path, str(wrapper_path)], cwd=str(root), env=env,
        stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
    )

    await asyncio.sleep(2.5)
    if proc.poll() is not None:
        log_file.flush()
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                err_tail = "".join(f.readlines()[-15:]).strip()
        except: err_tail = "Closed unexpectedly."
        return False, f"Process crashed:\n\n{err_tail}"

    _procs[key] = proc
    _start_times[key] = time.time()
    return True, "Running"

def is_running(uid: int, slot: int) -> bool:
    key = (uid, slot)
    proc = _procs.get(key)
    if proc and proc.poll() is None: return True
    if proc:
        _procs.pop(key, None)
        _start_times.pop(key, None)
    return False

def get_uptime(uid: int, slot: int) -> str:
    key = (uid, slot)
    if key not in _start_times: return "—"
    e = int(time.time() - _start_times[key])
    h, r = divmod(e, 3600); m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"

def running_count():
    dead = [k for k, p in list(_procs.items()) if p.poll() is not None]
    for k in dead:
        _procs.pop(k, None)
        _start_times.pop(k, None)
    return len(_procs)

# ─────────────────────────────────────────────────────────────────────────
#  TELEGRAM BOT HANDLERS & CONVERSATION
# ─────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_blocked(uid): return
    if not user_exists(uid):
        save_user_meta(uid, {"first_name": update.effective_user.first_name or "User", "joined_at": int(time.time())})

    accounts = get_accounts(uid)
    hosted = [a for a in accounts if a.get("hosted")]
    running = [a for a in hosted if is_running(uid, a["slot"])]

    keyboard = [
        [InlineKeyboardButton("🚀 Deploy Script", callback_data="host")],
        [InlineKeyboardButton("🎛️ Control Panel", callback_data="myaccounts"), InlineKeyboardButton("📊 Status", callback_data="status")],
    ]

    text = (
        f"{TOP}\n║  🤖  {double_struck('Mansuri Premium Hoster')}  🤖  ║\n{BOTTOM}\n\n"
        f"🌟 {script_font('Welcome back')}, {bold_serif(update.effective_user.first_name or 'User')}!\n\n"
        f"{DIV}\n"
        f"📦 {sans_bold('Hosted Scripts')} : {mono(str(len(hosted)))}/{MAX_ACCOUNTS_PER_USER}\n"
        f"🟢 {sans_bold('Active Online')}  : {mono(str(len(running)))}\n"
        f"🪪 {fraktur('Your ID')} : <code>{uid}</code>\n"
        f"{DIV}\n\n"
        f"Upload any Telethon/Pyrogram script to host it 24/7."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))


UPLOAD_WAIT_FILE, UPLOAD_WAIT_PHONE, UPLOAD_WAIT_OTP, UPLOAD_WAIT_2FA = range(4)
pending_logins: Dict[int, dict] = {}

async def host_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_blocked(uid): return ConversationHandler.END

    if len([a for a in get_accounts(uid) if a.get("hosted")]) >= MAX_ACCOUNTS_PER_USER:
        msg = f"📱 Slot limit reached ({MAX_ACCOUNTS_PER_USER}). Delete an instance first."
        if update.callback_query: await update.callback_query.answer(msg, show_alert=True)
        else: await update.message.reply_text(msg)
        return ConversationHandler.END

    text = (
        f"{TOP}\n║  📤  {bold_serif('Upload Userbot Script')}  📤  ║\n{BOTTOM}\n\n"
        f"Send your script (<code>.py</code> or <code>.zip</code>).\n"
        f"I will extract API keys automatically and prepare your container.\n\n"
        f"💡 Send /cancel to abort."
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    return UPLOAD_WAIT_FILE

async def host_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    doc = update.message.document

    if not doc or not doc.file_name:
        await update.message.reply_text("❌ Please send a valid script file (.py or .zip).")
        return UPLOAD_WAIT_FILE

    name = doc.file_name
    if not (name.lower().endswith(".py") or name.lower().endswith(".zip")):
        await update.message.reply_text("❌ Only .py or .zip files are supported.")
        return UPLOAD_WAIT_FILE

    msg = await update.message.reply_text("🔎 <i>Scanning script & preparing container...</i>", parse_mode=ParseMode.HTML)

    slot = 0
    used = {a.get("slot") for a in get_accounts(uid)}
    while slot in used: slot += 1

    root = script_root(uid, slot)
    root.mkdir(parents=True, exist_ok=True)
    incoming = root / name
    tg_file = await context.bot.get_file(doc.file_id)
    await tg_file.download_to_drive(custom_path=str(incoming))

    if name.lower().endswith(".zip"):
        ok, zmsg = safe_extract_zip(incoming, root)
        if incoming.exists(): incoming.unlink()
        if not ok:
            shutil.rmtree(root, ignore_errors=True)
            await msg.edit_text(f"❌ Zip Error: {esc(zmsg)}", parse_mode=ParseMode.HTML)
            return ConversationHandler.END
        entry = find_entrypoint(root, "python")
        if not entry:
            shutil.rmtree(root, ignore_errors=True)
            await msg.edit_text("❌ No main Python file found in ZIP.")
            return ConversationHandler.END
    else:
        shutil.move(str(incoming), str(root / "main.py"))
        entry = root / "main.py"

    raw_text = entry.read_bytes().decode("utf-8", errors="ignore")
    api_id, api_hash = detect_credentials(raw_text)

    context.user_data["pending_slot"] = slot
    context.user_data["pending_entry"] = str(entry.relative_to(root))
    context.user_data["pending_name"] = name
    context.user_data["api_id"] = api_id
    context.user_data["api_hash"] = api_hash

    await msg.edit_text(
        f"✅ {bold_serif('Environment Prepared')}\n{DIV}\n"
        f"🔑 Detected API ID: <code>{api_id}</code>\n\n"
        f"📱 {sans_bold('Enter Telegram Phone Number')}\n"
        f"Format: <code>+91XXXXXXXXXX</code>",
        parse_mode=ParseMode.HTML,
    )
    return UPLOAD_WAIT_PHONE

async def host_got_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    phone = update.message.text.strip().replace(" ", "").replace("-", "")
    if not phone.replace("+", "").isdigit() or len(phone) < 8:
        await update.message.reply_text("❌ Invalid format. Please enter as +91XXXXXXXXXX.")
        return UPLOAD_WAIT_PHONE

    slot = context.user_data["pending_slot"]
    api_id = context.user_data["api_id"]
    api_hash = context.user_data["api_hash"]

    msg = await update.message.reply_text(f"⏳ Connecting to Telegram with API ID <code>{api_id}</code>...", parse_mode=ParseMode.HTML)

    try:
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        result = await client.send_code_request(phone)
        pending_logins[uid] = {
            "client": client, "phone": phone, "hash": result.phone_code_hash,
            "slot": slot, "api_id": api_id, "api_hash": api_hash,
        }
        await msg.edit_text(
            f"📨 {bold_serif('Telegram Login Code Sent')}\n\n"
            f"Enter the verification code received on Telegram.\n"
            f"💡 <i>Tip: Send with spaces (e.g. <code>1 2 3 4 5</code>)</i>",
            parse_mode=ParseMode.HTML,
        )
        return UPLOAD_WAIT_OTP
    except FloodWaitError as e:
        await msg.edit_text(f"⏳ Flood wait: Try again in {e.seconds}s.")
        return ConversationHandler.END
    except Exception as e:
        await msg.edit_text(f"❌ Failed to request OTP: {esc(str(e))}")
        return ConversationHandler.END

async def host_got_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    code = update.message.text.strip().replace(" ", "")
    pending = pending_logins.get(uid)
    if not pending:
        await update.message.reply_text("❌ Session expired. Run /host again.")
        return ConversationHandler.END

    client = pending["client"]
    msg = await update.message.reply_text("🔐 <i>Verifying code...</i>", parse_mode=ParseMode.HTML)
    try:
        await client.sign_in(pending["phone"], code, phone_code_hash=pending["hash"])
        session_string = client.session.save()
        await client.disconnect()
        pending_logins.pop(uid, None)
        return await _deploy_script(update, context, uid, pending["slot"], session_string, pending["phone"], pending["api_id"], pending["api_hash"], msg)
    except SessionPasswordNeededError:
        await msg.edit_text(f"🔒 {bold_serif('2FA Active')}\nEnter your Two-Step password:", parse_mode=ParseMode.HTML)
        return UPLOAD_WAIT_2FA
    except PhoneCodeInvalidError:
        await msg.edit_text("❌ Invalid code. Re-enter the code:")
        return UPLOAD_WAIT_OTP
    except Exception as e:
        await msg.edit_text(f"❌ Login error: {esc(str(e))}")
        return ConversationHandler.END

async def host_got_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    password = update.message.text.strip()
    pending = pending_logins.get(uid)
    if not pending: return ConversationHandler.END

    client = pending["client"]
    msg = await update.message.reply_text("🔐 <i>Verifying 2FA Password...</i>", parse_mode=ParseMode.HTML)
    try:
        await client.sign_in(password=password)
        session_string = client.session.save()
        await client.disconnect()
        pending_logins.pop(uid, None)
        return await _deploy_script(update, context, uid, pending["slot"], session_string, pending["phone"], pending["api_id"], pending["api_hash"], msg)
    except Exception as e:
        await msg.edit_text(f"❌ Incorrect 2FA Password: {esc(str(e))}\nTry entering it again:")
        return UPLOAD_WAIT_2FA

async def _deploy_script(update, context, uid, slot, session_string, phone, api_id, api_hash, msg):
    await msg.edit_text("🚀 <i>Installing dependencies and launching container...</i>", parse_mode=ParseMode.HTML)
    
    root = script_root(uid, slot)
    entry = root / context.user_data["pending_entry"]
    name = context.user_data["pending_name"]

    ok, res_msg = await start_script(uid, slot, entry, session_string, api_id, api_hash, phone)

    if ok:
        add_account(uid, {
            "slot": slot, "name": name, "entrypoint": str(entry.relative_to(root)),
            "session_string": session_string, "phone": phone, "hosted": True,
            "is_stopped": False, "hosted_at": int(time.time()),
            "api_id": api_id, "api_hash": api_hash
        })
        success_text = (
            f"{TOP}\n║  🎉  {bold_serif('Userbot Deployed 24/7')}  🎉  ║\n{BOTTOM}\n\n"
            f"✅ Script: <code>{esc(name)}</code>\n"
            f"📱 Connected: <code>{esc(phone)}</code>\n\n"
            f"Use /myaccounts to View Live Logs, Stop, or Restart."
        )
        await msg.edit_text(success_text, parse_mode=ParseMode.HTML)
    else:
        await msg.edit_text(f"❌ {bold_serif('Container Launch Failed')}\n\n<pre>{esc(res_msg)}</pre>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def host_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending_logins.pop(update.effective_user.id, None)
    await update.message.reply_text("🚫 Deployment aborted.")
    return ConversationHandler.END

async def cmd_myaccounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_blocked(update.effective_user.id): return
    uid = update.effective_user.id
    hosted = [a for a in get_accounts(uid) if a.get("hosted")]

    if not hosted:
        kb = [[InlineKeyboardButton("🚀 Deploy Userbot", callback_data="host")]]
        await update.message.reply_text("📭 No active userbot instances found.", reply_markup=InlineKeyboardMarkup(kb))
        return

    for acct in hosted:
        slot = acct["slot"]
        phone = _phone_label(acct)
        alive = is_running(uid, slot)
        status_icon = "🟢 Running" if alive else ("⏸ Stopped" if acct.get("is_stopped") else "🔴 Crashed/Offline")
        uptime = get_uptime(uid, slot) if alive else "Offline"

        text = (
            f"⚙️ {bold_serif(f'Slot #{slot+1}')} | {status_icon}\n"
            f"📱 Account: <code>{esc(phone)}</code>\n"
            f"⏱️ Uptime: <code>{uptime}</code>"
        )
        kb = [
            [
                InlineKeyboardButton("🔄 Restart", callback_data=f"restart_{slot}"),
                InlineKeyboardButton("🛑 Stop" if alive else "🟢 Start", callback_data=f"toggle_{slot}"),
            ],
            [
                InlineKeyboardButton("📄 Logs", callback_data=f"logs_{slot}"),
                InlineKeyboardButton("🗑️ Terminate", callback_data=f"logout_{slot}"),
            ]
        ]
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    hosted = [a for a in get_accounts(uid) if a.get("hosted")]
    if not hosted:
        await update.message.reply_text("❌ No hosted userbots found.")
        return

    lines = []
    for a in hosted:
        slot = a["slot"]
        alive = is_running(uid, slot)
        status = "🟢 ACTIVE" if alive else "🔴 OFFLINE"
        uptime = get_uptime(uid, slot) if alive else "—"
        lines.append(f"{status} Slot #{slot+1} (<code>{esc(_phone_label(a))}</code>) — Uptime: {uptime}")
    await update.message.reply_text(f"📊 {bold_serif('Instance Status')}\n{DIV}\n" + "\n".join(lines), parse_mode=ParseMode.HTML)

# ─────────────────────────────────────────────────────────────────────────
#  INLINE CALLBACK DISPATCHER
# ─────────────────────────────────────────────────────────────────────────
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    data = query.data

    if data == "cancel_action":
        await query.message.delete()
        return
    if data == "myaccounts":
        await cmd_myaccounts(update, context)
        return
    if data == "status":
        await cmd_status(update, context)
        return

    if data.startswith("restart_"):
        slot = int(data.split("_")[1])
        acct = get_account(uid, slot)
        if not acct: return await query.message.reply_text("❌ Instance not found.")
        await query.message.reply_text(f"🔄 Restarting Slot #{slot+1}...")
        
        root = script_root(uid, slot)
        entry = root / acct["entrypoint"]
        ok, msg = await start_script(uid, slot, entry, acct["session_string"], acct["api_id"], acct["api_hash"], acct.get("phone", ""))
        if ok:
            acct["is_stopped"] = False
            add_account(uid, acct)
            await query.message.reply_text(f"✅ Slot #{slot+1} restarted successfully.")
        else:
            await query.message.reply_text(f"❌ Restart failed:\n<pre>{esc(msg)}</pre>", parse_mode=ParseMode.HTML)

    if data.startswith("toggle_"):
        slot = int(data.split("_")[1])
        acct = get_account(uid, slot)
        if not acct: return
        
        if is_running(uid, slot):
            stop_script(uid, slot)
            acct["is_stopped"] = True
            add_account(uid, acct)
            await query.message.reply_text(f"🛑 Slot #{slot+1} stopped.")
        else:
            root = script_root(uid, slot)
            entry = root / acct["entrypoint"]
            ok, msg = await start_script(uid, slot, entry, acct["session_string"], acct["api_id"], acct["api_hash"], acct.get("phone", ""))
            if ok:
                acct["is_stopped"] = False
                add_account(uid, acct)
                await query.message.reply_text(f"🟢 Slot #{slot+1} started.")
            else:
                await query.message.reply_text(f"❌ Launch failed:\n<pre>{esc(msg)}</pre>", parse_mode=ParseMode.HTML)

    if data.startswith("logs_"):
        slot = int(data.split("_")[1])
        log_path = script_root(uid, slot) / "runtime.log"
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                tail = "".join(f.readlines()[-25:]).strip()
            text = tail or "No execution logs captured yet."
        else:
            text = "Log file not initialized."
        await query.message.reply_text(f"📄 {bold_serif(f'Logs (Slot #{slot+1})')}\n<pre>{esc(text)}</pre>", parse_mode=ParseMode.HTML)

    if data.startswith("logout_"):
        slot = int(data.split("_")[1])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Termination", callback_data=f"confirm_logout_{slot}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")],
        ])
        await query.message.reply_text(f"⚠️ Are you sure you want to permanently delete Slot #{slot+1}?", reply_markup=kb)

    if data.startswith("confirm_logout_"):
        slot = int(data.split("_")[2])
        stop_script(uid, slot)
        remove_account(uid, slot)
        shutil.rmtree(script_root(uid, slot), ignore_errors=True)
        await query.message.edit_text(f"🗑️ Slot #{slot+1} has been completely removed.")

# ─────────────────────────────────────────────────────────────────────────
#  AUTO-HEALTH WATCHDOG
# ─────────────────────────────────────────────────────────────────────────
async def auto_health_check(context: ContextTypes.DEFAULT_TYPE):
    for uid_str in get_all_users():
        uid = int(uid_str)
        if is_blocked(uid): continue
        for acct in get_accounts(uid):
            if not acct.get("hosted") or not acct.get("session_string") or acct.get("is_stopped"):
                continue
            slot = acct["slot"]
            if not is_running(uid, slot):
                root = script_root(uid, slot)
                entry = root / acct["entrypoint"]
                if entry.exists():
                    await start_script(uid, slot, entry, acct["session_string"], acct["api_id"], acct["api_hash"], acct.get("phone", ""))

# ─────────────────────────────────────────────────────────────────────────
#  MAIN ENTRYPOINT
# ─────────────────────────────────────────────────────────────────────────
async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "Open Control Center"),
        BotCommand("host", "Deploy Userbot Script"),
        BotCommand("myaccounts", "Manage Hosted Userbots"),
        BotCommand("status", "Check Instance Status"),
    ])

    recovered = 0
    for uid_str in get_all_users():
        uid = int(uid_str)
        if is_blocked(uid): continue
        for acct in get_accounts(uid):
            if acct.get("hosted") and acct.get("session_string") and not acct.get("is_stopped"):
                slot = acct["slot"]
                root = script_root(uid, slot)
                entry = root / acct["entrypoint"]
                if entry.exists():
                    ok, _ = await start_script(uid, slot, entry, acct["session_string"], acct["api_id"], acct["api_hash"], acct.get("phone", ""))
                    if ok: recovered += 1
    logger.info(f"Re-established {recovered} userbot instances upon boot.")

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    host_conv = ConversationHandler(
        entry_points=[
            CommandHandler("host", host_start),
            CallbackQueryHandler(host_start, pattern="^host$"),
        ],
        states={
            UPLOAD_WAIT_FILE: [MessageHandler(~filters.COMMAND, host_got_file)],
            UPLOAD_WAIT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_got_phone)],
            UPLOAD_WAIT_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_got_otp)],
            UPLOAD_WAIT_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_got_2fa)],
        },
        fallbacks=[CommandHandler("cancel", host_cancel)],
        allow_reentry=True,
    )

    app.add_handler(host_conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myaccounts", cmd_myaccounts))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(callback_handler))

    if app.job_queue:
        app.job_queue.run_repeating(auto_health_check, interval=30, first=10)

    logger.info("🚀 Pure Manual Hoster Active")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
