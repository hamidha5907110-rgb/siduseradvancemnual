#!/usr/bin/env python3
"""
SID HOSTER — Userbot script hoster with animated flow
Combines Telethon login with user-provided scripts and SQLite DB.
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
import sqlite3
import psutil
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
    RPCError,
)
from telethon.sessions import StringSession

# ─────────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN", "8602762499:AAHRU4hAlT6G94Iz5ZHmPEjekT80G5Z4fpk").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "2119464081") or 0)
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support").strip()
MAX_USERBOTS = max(1, int(os.getenv("MAX_USERBOTS", "50") or 50))
MAX_SCRIPTS_PER_USER = max(1, int(os.getenv("MAX_SCRIPTS_PER_USER", "3") or 3))
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "2040"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "b18441a1ff607e10a989891a5462e627").strip()
MAX_UPLOAD_MB = 20
MAX_ZIP_FILES = 250
PIP_TIMEOUT = 300
SESSION_VALIDATE_INTERVAL = 300  # seconds between session checks

if not BOT_TOKEN or not OWNER_ID:
    raise ValueError("BOT_TOKEN and OWNER_ID must be set in environment.")

# --- Malware Detection Configuration ---
MALWARE_SIGNATURES = [
    b'MZ',  # Windows executable
    b'\x7fELF',  # Linux executable
    b'\xfe\xed\xfa',  # Mach-O binary
    b'\xce\xfa\xed\xfe',  # Mach-O binary (reverse)
    b'Rar!',  # RAR archive
]

SUSPICIOUS_KEYWORDS = [
    b'ransomware', b'trojan', b'virus', b'malware', 
    b'backdoor', b'exploit', b'payload', b'botnet', b'keylogger', b'rootkit'
]

PACKAGE_MAP = {
    "PIL": "Pillow", "cv2": "opencv-python", "bs4": "beautifulsoup4", "yaml": "PyYAML",
    "telethon": "Telethon", "pyrogram": "pyrogram tgcrypto", "tgcrypto": "tgcrypto",
    "telegram": "python-telegram-bot", "dotenv": "python-dotenv", "dateutil": "python-dateutil",
    "Crypto": "pycryptodome", "crypto": "pycryptodome", "OpenSSL": "pyOpenSSL",
    "sqlalchemy": "SQLAlchemy", "jwt": "PyJWT", "mutagen": "mutagen", "aiohttp": "aiohttp",
    "aiofiles": "aiofiles", "requests": "requests", "motor": "motor", "pymongo": "pymongo",
    "redis": "redis", "git": "GitPython", "telegraph": "telegraph", "pytz": "pytz",
    "dns": "dnspython", "socks": "PySocks", "uvloop": "uvloop", "speedtest": "speedtest-cli",
    "youtube_dl": "youtube_dl", "yt_dlp": "yt-dlp", "apscheduler": "APScheduler",
    "wheel": "wheel", "hachoir": "hachoir", "wget": "wget", "urllib3": "urllib3",
    "certifi": "certifi", "qrcode": "qrcode", "psutil": "psutil",
}

# ─────────────────────────────────────────────────────────────────────────
#  DATABASE (SQLite 3, thread-safe)
# ─────────────────────────────────────────────────────────────────────────

DB_DIR = Path(os.getcwd()) / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "hoster.db"

_db_lock = threading.Lock()

def _init_db():
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS store (
                        key TEXT PRIMARY KEY,
                        value TEXT
                     )''')
        conn.commit()
        conn.close()

_init_db()

def _read_db(key, default):
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM store WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        if row:
            try:
                return json.loads(row[0])
            except:
                return default
        return default

def _write_db(key, value):
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("REPLACE INTO store (key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))
        conn.commit()
        conn.close()

def user_exists(uid):
    return _read_db(f"meta_{uid}", None) is not None

def save_user_meta(uid, data):
    existing = _read_db(f"meta_{uid}", {})
    existing.update(data)
    _write_db(f"meta_{uid}", existing)

def get_all_users():
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT key FROM store WHERE key LIKE 'meta_%'")
        rows = c.fetchall()
        conn.close()
        return [row[0].replace("meta_", "") for row in rows]

def user_count():
    return len(get_all_users())

def get_accounts(uid):
    return _read_db(f"accts_{uid}", [])

def get_account(uid, slot):
    for a in get_accounts(uid):
        if a.get("slot") == slot:
            return a
    return None

def add_account(uid, acct):
    accounts = get_accounts(uid)
    accounts = [a for a in accounts if a.get("slot") != acct.get("slot")]
    accounts.append(acct)
    _write_db(f"accts_{uid}", accounts)

def remove_account(uid, slot):
    accounts = [a for a in get_accounts(uid) if a.get("slot") != slot]
    _write_db(f"accts_{uid}", accounts)

def hosted_count():
    total = 0
    for uid_str in get_all_users():
        for a in get_accounts(int(uid_str)):
            if a.get("hosted"):
                total += 1
    return total

def get_sudo_users():
    return _read_db("sudo", [])

def add_sudo(uid):
    s = get_sudo_users()
    if uid not in s:
        s.append(uid)
        _write_db("sudo", s)

def remove_sudo(uid):
    s = [x for x in get_sudo_users() if x != uid]
    _write_db("sudo", s)

def is_sudo(uid):
    return uid == OWNER_ID or uid in get_sudo_users()

def get_blocked():
    return _read_db("blocked", [])

def block_user(uid):
    b = get_blocked()
    if uid not in b:
        b.append(uid)
        _write_db("blocked", b)

def unblock_user(uid):
    b = [x for x in get_blocked() if x != uid]
    _write_db("blocked", b)

def is_blocked(uid):
    return uid in get_blocked()

# ─────────────────────────────────────────────────────────────────────────
#  PROCESSOR & MALWARE SCANNING
# ─────────────────────────────────────────────────────────────────────────

API_ID_NAMES = {"API_ID", "api_id", "TG_API_ID", "TELEGRAM_API_ID", "CLIENT_API_ID"}
API_HASH_NAMES = {"API_HASH", "api_hash", "TG_API_HASH", "TELEGRAM_API_HASH", "CLIENT_API_HASH"}
API_ID_RE = re.compile(r"(?im)\b(?:API_ID|api_id|TG_API_ID|TELEGRAM_API_ID)\b\s*(?:[:=])\s*(?:int\(\s*)?[\"']?(\d{5,12})")
API_HASH_RE = re.compile(r"(?im)\b(?:API_HASH|api_hash|TG_API_HASH|TELEGRAM_API_HASH)\b\s*(?:[:=])\s*(?:str\(\s*)?[\"']([A-Za-z0-9]{16,128})[\"']")
PHONE_RE = re.compile(r"(?<!\d)(\+?[1-9]\d{7,14})(?!\d)")

def detect_api(text: str) -> Tuple[Optional[int], Optional[str]]:
    aid = ahash = None
    m = API_ID_RE.search(text)
    if m:
        try: aid = int(m.group(1))
        except: pass
    m2 = API_HASH_RE.search(text)
    if m2: ahash = m2.group(1)
    return aid, ahash

def detect_phone(text: str) -> Optional[str]:
    for m in PHONE_RE.finditer(text):
        candidate = m.group(1)
        digits = candidate.replace("+", "")
        if 8 <= len(digits) <= 15: return candidate
    return None

def collect_all_imports(root: Path) -> List[str]:
    imports = set()
    import_re = re.compile(r"^\s*(?:from|import)\s+([a-zA-Z0-9_]+)", re.MULTILINE)
    for py_path in root.rglob("*.py"):
        if ".venv" in py_path.parts or "__pycache__" in py_path.parts: continue
        try:
            content = py_path.read_text(encoding="utf-8", errors="ignore")
            for m in import_re.finditer(content): imports.add(m.group(1))
        except Exception: pass
    return sorted(imports)

def scan_file_for_malware(file_content: bytes, file_name: str, user_id: int) -> Tuple[bool, str]:
    """Scan file for malware signatures or suspicious extensions."""
    if user_id == OWNER_ID:
        return True, "Owner bypassed security check"
    
    file_lower = file_name.lower()
    suspicious_extensions = ['.exe', '.dll', '.bat', '.cmd', '.scr', '.com', '.msi', '.vbs']
    if any(file_lower.endswith(ext) for ext in suspicious_extensions):
        return False, f"Suspicious file extension detected: {file_name}"
    
    for signature in MALWARE_SIGNATURES:
        if file_content.startswith(signature):
            return False, "Malware executable signature detected."
            
    sample_text = file_content[:4096].decode('utf-8', errors='ignore').lower()
    for keyword in SUSPICIOUS_KEYWORDS:
        if keyword.decode('utf-8').lower() in sample_text:
            return False, f"Suspicious keyword found in code: {keyword.decode('utf-8')}"
            
    return True, "File appears safe"

def scan_script(path: Path) -> dict:
    raw = path.read_bytes()
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        return {"ok": False, "reason": f"File > {MAX_UPLOAD_MB} MB."}
    try: text = raw.decode("utf-8", errors="ignore")
    except Exception: text = ""
    
    ext = path.suffix.lower()
    language = "python" if ext == ".py" else "nodejs" if ext == ".js" else "unknown"
    aid, ahash = detect_api(text)
    phone = detect_phone(text)
    imports = collect_all_imports(path.parent) if language == "python" else []
    
    return {
        "ok": True, "language": language, "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(), "imports": imports,
        "api_id": aid, "api_hash": ahash, "phone": phone,
        "warnings": [], "blocked": [],
    }

def safe_extract_zip(zip_path: Path, target: Path) -> Tuple[bool, str]:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_FILES:
                return False, f"ZIP contains > {MAX_ZIP_FILES} files."
            total = sum(max(0, i.file_size) for i in infos)
            if total > MAX_UPLOAD_MB * 1024 * 1024:
                return False, "ZIP expands beyond upload limit."
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or "../" in name.split("/"):
                    return False, "Unsafe ZIP path."
            zf.extractall(target)
            
        # Recursive directory flattening fix
        root_files = os.listdir(target)
        if not any(f.endswith(('.py', '.js')) for f in root_files):
            target_dir = target
            for root, dirs, files in os.walk(target):
                # Ignore system/hidden folders like __MACOSX or .git
                dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('__')]
                if any(f.endswith(('.py', '.js')) for f in files):
                    target_dir = Path(root)
                    break
            
            if target_dir != target:
                for item in os.listdir(target_dir):
                    s = target_dir / item
                    d = target / item
                    if d.exists():
                        if d.is_dir(): shutil.rmtree(d)
                        else: d.unlink()
                    shutil.move(str(s), str(d))
                    
        return True, "ZIP extracted."
    except zipfile.BadZipFile:
        return False, "Invalid ZIP."
    except Exception as exc:
        return False, f"Extraction error: {exc}"

def find_entrypoint(root: Path, language: str) -> Optional[Path]:
    if language == "python":
        main = root / "main.py"
        if main.exists(): return main
        py_files = [p for p in root.rglob("*.py") if ".venv" not in p.parts and "__pycache__" not in p.parts]
        return py_files[0] if py_files else None
    elif language == "nodejs":
        for name in ["index.js", "main.js", "app.js", "server.js"]:
            p = root / name
            if p.exists(): return p
        js_files = [p for p in root.rglob("*.js") if "node_modules" not in p.parts]
        return js_files[0] if js_files else None
    return None

def generate_package_json(root: Path, entry_point: str) -> Path:
    pkg = root / "package.json"
    if not pkg.exists():
        data = {
            "name": "userbot-script", "version": "1.0.0",
            "main": entry_point, "scripts": {"start": f"node {entry_point}"},
            "dependencies": {}
        }
        with open(pkg, "w") as f:
            json.dump(data, f, indent=2)
    return pkg

# ─────────────────────────────────────────────────────────────────────────
#  SUBPROCESS RUNNER (24/7 ASYNC WATCHDOG & AUTO-REQUIREMENTS INJECTION)
# ─────────────────────────────────────────────────────────────────────────

_procs: Dict[Tuple[int, int], asyncio.subprocess.Process] = {}
_start_times: Dict[Tuple[int, int], float] = {}
_script_files: Dict[Tuple[int, int], Path] = {}
_log_files: Dict[Tuple[int, int], Path] = {}
_monitor_tasks: Dict[Tuple[int, int], asyncio.Task] = {}

def stop_script(uid: int, slot: int):
    """Kills process and all its children recursively using psutil."""
    key = (uid, slot)
    proc = _procs.pop(key, None)
    _start_times.pop(key, None)
    _script_files.pop(key, None)
    _log_files.pop(key, None)
    task = _monitor_tasks.pop(key, None)
    if task and not task.done(): task.cancel()
    
    if proc:
        try:
            parent = psutil.Process(proc.pid)
            children = parent.children(recursive=True)
            for child in children:
                try: child.kill()
                except psutil.NoSuchProcess: pass
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            try: proc.kill()
            except: pass

def _acquire_lock(lock_path: Path, timeout: int = 10) -> bool:
    import os as _os
    start = time.time()
    while time.time() - start < timeout:
        try:
            fd = _os.open(str(lock_path), _os.O_CREAT | _os.O_EXCL | _os.O_RDWR)
            _os.close(fd)
            return True
        except FileExistsError:
            time.sleep(0.2)
    return False

def _release_lock(lock_path: Path):
    try: lock_path.unlink(missing_ok=True)
    except: pass

def rotate_log(log_path: Path, max_size: int = 10 * 1024 * 1024):
    if log_path.exists() and log_path.stat().st_size > max_size:
        backup = log_path.with_suffix(".log.old")
        shutil.move(str(log_path), str(backup))

async def validate_session(session_string: str, api_id: int, api_hash: str) -> Tuple[bool, Optional[str]]:
    try:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()
        me = await client.get_me()
        await client.disconnect()
        return True, me.phone
    except Exception as e:
        return False, str(e)

async def start_script(uid: int, slot: int, script_path: Path, session_string: str,
                       api_id: int, api_hash: str, phone: str = "", language: str = "python"):
    key = (uid, slot)

    valid, info = await validate_session(session_string, api_id, api_hash)
    if not valid:
        acct = get_account(uid, slot)
        if acct:
            acct["needs_rehost"] = True
            acct["rehost_reason"] = f"Session invalid: {info}"
            add_account(uid, acct)
        return False, f"Session expired: {info}. Please /host again."

    stop_script(uid, slot)

    root = script_path.parent
    entry_name = script_path.name
    log_path = root / "runtime.log"
    rotate_log(log_path)

    # Injecting environment variables for standard userbots
    env = os.environ.copy()
    env.update({
        "API_ID": str(api_id),
        "API_HASH": api_hash,
        "SESSION_STRING": session_string,
        "STRING_SESSION": session_string,
        "SESSION": session_string,
        "TELETHON_SESSION": session_string,
        "PYROGRAM_SESSION": session_string,
        "TG_SESSION": session_string,
        "PHONE_NUMBER": phone,
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(root) + os.pathsep + env.get("PYTHONPATH", ""),
    })

    if language == "python":
        venv_dir = root / ".venv"
        python_exe = venv_dir / "bin" / "python" if os.name != "nt" else venv_dir / "Scripts" / "python.exe"
        if not venv_dir.exists():
            p1 = await asyncio.create_subprocess_exec(sys.executable, "-m", "venv", str(venv_dir))
            await p1.wait()

        exe = str(python_exe if python_exe.exists() else sys.executable)
        
        req_path = root / "requirements.txt"
        discovered_imports = collect_all_imports(root)
        std_libs = set(getattr(sys, "stdlib_module_names", {
            'os', 'sys', 'time', 'json', 're', 'asyncio', 'logging', 'hashlib', 'threading',
            'math', 'random', 'datetime', 'collections', 'pathlib', 'subprocess', 'shutil',
            'typing', 'zipfile', 'ast', 'html', 'sqlite3', 'urllib', 'base64', 'binascii',
            'socket', 'ssl', 'tempfile', 'uuid', 'warnings', 'io', 'functools', 'itertools',
            'string', 'struct', 'traceback', 'types', 'weakref', 'abc', 'argparse', 'contextlib'
        }))

        needed_packages = {"telethon", "pyrogram", "tgcrypto", "aiohttp", "aiofiles", "requests"}
        for imp in discovered_imports:
            imp_name = imp.split(".")[0]
            if imp_name not in std_libs and not imp_name.startswith("_") and imp_name != script_path.stem:
                needed_packages.add(PACKAGE_MAP.get(imp_name, imp_name))

        if not req_path.exists():
            with open(req_path, "w", encoding="utf-8") as f:
                f.write("\n".join(sorted(needed_packages)))

        lock_path = req_path.with_suffix(".lock")
        if _acquire_lock(lock_path, timeout=60):
            try:
                pip_proc = await asyncio.create_subprocess_exec(exe, "-m", "pip", "install", "-r", str(req_path))
                await asyncio.wait_for(pip_proc.wait(), timeout=PIP_TIMEOUT)
            except Exception as e:
                logging.warning(f"Initial pip install warning: {e}")
            finally:
                _release_lock(lock_path)
        cmd = [exe, str(script_path)]

    else:
        pkg = generate_package_json(root, entry_name)
        if pkg.exists():
            try:
                npm_proc = await asyncio.create_subprocess_exec("npm", "install", cwd=str(root))
                await asyncio.wait_for(npm_proc.wait(), timeout=PIP_TIMEOUT)
            except Exception as e:
                logging.warning(f"npm install warning: {e}")
        exe = "node"
        cmd = [exe, str(script_path)]

    # Dynamic Dependency Pre-Check Loop (Adapted from Source [2])
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        log_file = open(log_path, "a", encoding="utf-8", buffering=1)
        log_file.write(f"\n===== SID HOSTER START ATTEMPT {attempt} at {time.ctime()} =====\n")
        
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(root), env=env, stdout=log_file, stderr=subprocess.STDOUT
        )
        
        try:
            # Pre-check for 5 seconds
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            
            # If process exits quickly, find the missing module
            exit_code = proc.returncode
            log_file.close()
            
            with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                tail = lf.read()[-3000:]
                
            missing_pkg = None
            if language == "python":
                match = re.search(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]", tail)
                if match: missing_pkg = match.group(1)
            elif language == "nodejs":
                match = re.search(r"Cannot find module ['\"]([^'\"]+)['\"]", tail)
                if match: missing_pkg = match.group(1)
                
            if missing_pkg:
                target_pkg = PACKAGE_MAP.get(missing_pkg, missing_pkg)
                if language == "python":
                    p = await asyncio.create_subprocess_exec(exe, "-m", "pip", "install", target_pkg)
                else:
                    if not missing_pkg.startswith("."):
                        p = await asyncio.create_subprocess_exec("npm", "install", missing_pkg, cwd=str(root))
                await p.wait()
                continue  # Retry execution
            else:
                return False, f"Script crashed immediately (Exit code {exit_code}). Please check Logs."
                
        except asyncio.TimeoutError:
            # Process survived the 5 second check. It is running smoothly!
            _procs[key] = proc
            _start_times[key] = time.time()
            _script_files[key] = script_path
            _log_files[key] = log_path
            
            task = asyncio.create_task(_monitor_process(uid, slot, proc, log_path))
            _monitor_tasks[key] = task
            return True, "Running"

    return False, "Failed to start after multiple automatic dependency installation attempts."

async def _monitor_process(uid: int, slot: int, proc: asyncio.subprocess.Process, log_path: Path):
    key = (uid, slot)
    try:
        await proc.wait()
        exit_code = proc.returncode
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== EXIT with code {exit_code} at {time.ctime()} =====\n")
    except asyncio.CancelledError:
        pass
    finally:
        _procs.pop(key, None)
        _start_times.pop(key, None)
        _script_files.pop(key, None)
        _log_files.pop(key, None)
        _monitor_tasks.pop(key, None)

def is_running(uid: int, slot: int) -> bool:
    key = (uid, slot)
    proc = _procs.get(key)
    if not proc:
        return False
    if proc.returncode is not None:
        _procs.pop(key, None)
        _start_times.pop(key, None)
        _script_files.pop(key, None)
        _log_files.pop(key, None)
        task = _monitor_tasks.pop(key, None)
        if task and not task.done(): task.cancel()
        return False
    return True

def get_uptime(uid: int, slot: int) -> str:
    key = (uid, slot)
    t = _start_times.get(key)
    if not t: return "—"
    e = int(time.time() - t)
    h, r = divmod(e, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"

def running_count():
    dead = [k for k, p in list(_procs.items()) if p.returncode is not None]
    for k in dead:
        _procs.pop(k, None)
        _start_times.pop(k, None)
        _script_files.pop(k, None)
        _log_files.pop(k, None)
        task = _monitor_tasks.pop(k, None)
        if task and not task.done(): task.cancel()
    return len(_procs)

def stop_all_for_user(uid):
    for k in list(_procs.keys()):
        if k[0] == uid:
            stop_script(k[0], k[1])

# ─────────────────────────────────────────────────────────────────────────
#  FONT & STYLE HELPERS
# ─────────────────────────────────────────────────────────────────────────

def bold(t: str) -> str: return f"<b>{t}</b>"
def esc(t: str) -> str: return html.escape(str(t), quote=False)

def double_struck(text: str) -> str:
    mapping = {
        'A': '𝔸', 'B': '𝔹', 'C': 'ℂ', 'D': '𝔻', 'E': '𝔼', 'F': '𝔽', 'G': '𝔾',
        'H': 'ℍ', 'I': '𝕀', 'J': '𝕁', 'K': '𝕂', 'L': '𝕃', 'M': '𝕄', 'N': 'ℕ',
        'O': '𝕆', 'P': 'ℙ', 'Q': 'ℚ', 'R': 'ℝ', 'S': '𝕊', 'T': '𝕋', 'U': '𝕌',
        'V': '𝕍', 'W': '𝕎', 'X': '𝕏', 'Y': '𝕐', 'Z': 'ℤ',
        'a': '𝕒', 'b': '𝕓', 'c': '𝕔', 'd': '𝕕', 'e': '𝕖', 'f': '𝕗', 'g': '𝕘',
        'h': '𝕙', 'i': '𝕚', 'j': '𝕛', 'k': '𝕜', 'l': '𝕝', 'm': '𝕞', 'n': '𝕟',
        'o': '𝕠', 'p': '𝕡', 'q': '𝕢', 'r': '𝕣', 's': '𝕤', 't': '𝕥', 'u': '𝕦',
        'v': '𝕧', 'w': '𝕨', 'x': '𝕩', 'y': '𝕪', 'z': '𝕫'
    }
    return "".join(mapping.get(c, c) for c in text)

DIV = "━━━━━━━━━━━━━━━━━━━━━━━━━━"
TOP = "╔══════════════════════════╗"
BOTTOM = "╚══════════════════════════╝"

# ─────────────────────────────────────────────────────────────────────────
#  ANIMATION HELPERS
# ─────────────────────────────────────────────────────────────────────────

async def animate_scanning(msg, steps=6, delay=0.15):
    frames = [
        "🔎 Preparing Environment `▱▱▱▱▱`",
        "🔎 Preparing Environment `▰▱▱▱▱`",
        "🔎 Checking Dependencies `▰▰▱▱▱`",
        "🔎 Checking Dependencies `▰▰▰▱▱`",
        "🔎 Finalizing Container `▰▰▰▰▱`",
        "🔎 Container Ready `▰▰▰▰▰`",
    ]
    for frame in frames[:steps]:
        try:
            await msg.edit_text(frame, parse_mode=ParseMode.HTML)
            await asyncio.sleep(delay)
        except: break

async def animate_connecting(msg):
    frames = ["📡 Connecting `▱▱▱`", "📡 Connecting `▰▱▱`", "📡 Connecting `▰▰▱`", "📡 Connecting `▰▰▰`"]
    for frame in frames:
        try:
            await msg.edit_text(frame, parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.15)
        except: break

async def animate_deploy(msg):
    frames = ["🚀 Deploying 24/7 `▱▱▱`", "🚀 Deploying 24/7 `▰▱▱`", "🚀 Deploying 24/7 `▰▰▱`", "🚀 Deploying 24/7 `▰▰▰`", "🚀 Deploying 24/7 `▰▰▰` ✨"]
    for frame in frames:
        try:
            await msg.edit_text(frame, parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.12)
        except: break

async def animate_otp_verify(msg):
    frames = ["🔐 Verifying OTP `·`", "🔐 Verifying OTP `··`", "🔐 Verifying OTP `···`", "🔐 Verifying OTP `✔`"]
    for frame in frames:
        try:
            await msg.edit_text(frame, parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.2)
        except: break

async def simulate_button_animation(query, text="⏳ Processing"):
    try:
        loading_kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"{text}...", callback_data="none")]])
        await query.message.edit_reply_markup(reply_markup=loading_kb)
        await asyncio.sleep(0.4)
    except: pass

# ─────────────────────────────────────────────────────────────────────────
#  BOT UI HELPERS
# ─────────────────────────────────────────────────────────────────────────

START_TIME = time.time()
def uptime_str():
    e = int(time.time() - START_TIME)
    h, r = divmod(e, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"

def is_owner(uid): return uid == OWNER_ID
def is_premium(uid): return is_owner(uid) or is_sudo(uid)
def script_root(uid: int, slot: int) -> Path: return DB_DIR / "scripts" / str(uid) / f"slot_{slot}"
def _phone_label(acct): return acct.get("phone", f"Script #{acct.get('slot', 0)+1}")

def main_keyboard(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ 🚀 Deploy New Userbot ✨", callback_data="host")],
        [InlineKeyboardButton("🎛️ My Control Panel", callback_data="myaccounts"),
         InlineKeyboardButton("📊 Live System Status", callback_data="status")],
        [InlineKeyboardButton("📖 User Guide & Help", callback_data="help"),
         InlineKeyboardButton("🎧 24/7 Support", callback_data="support")],
    ])

# ─────────────────────────────────────────────────────────────────────────
#  COMMANDS
# ─────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_blocked(uid):
        await update.message.reply_text("🚫 You are blocked.")
        return
    if not user_exists(uid):
        save_user_meta(uid, {"first_name": update.effective_user.first_name or "User", "joined_at": int(time.time())})

    accounts = get_accounts(uid)
    hosted = [a for a in accounts if a.get("hosted")]
    running = [a for a in hosted if is_running(uid, a["slot"])]

    text = (
        f"{TOP}\n║  🤖  {double_struck('Sid Hoster')}  🤖  ║\n{BOTTOM}\n\n"
        f"👋 Welcome, {esc(update.effective_user.first_name or 'User')}!\n"
        f"{DIV}\n"
        f"📦 Hosted Scripts: {len(hosted)}/{MAX_SCRIPTS_PER_USER}\n"
        f"🟢 Active 24/7: {len(running)}\n"
        f"🆔 User ID: `{uid}`\n\n"
        f"👇 *Select an option below to manage your scripts*"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard(uid))

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_blocked(update.effective_user.id): return
    text = (
        f"❓ {bold('Help & Commands')}\n{DIV}\n\n"
        f"🔹 /start      – Welcome dashboard\n"
        f"🔹 /host       – Upload and deploy a script\n"
        f"🔹 /myaccounts – Manage, stop, and view logs\n"
        f"🔹 /status     – Check running status\n"
        f"🔹 /support    – Contact support\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_blocked(update.effective_user.id): return
    await update.message.reply_text(
        f"{TOP}\n║  📞  {bold('Support')}  📞  ║\n{BOTTOM}\n\n"
        f"👤 Admin: {SUPPORT_USERNAME}\n⚡ Response: Fast\n"
    )

# ─────────────────────────────────────────────────────────────────────────
#  HOST CONVERSATION
# ─────────────────────────────────────────────────────────────────────────

UPLOAD_WAIT_FILE, UPLOAD_WAIT_PHONE, UPLOAD_WAIT_OTP, UPLOAD_WAIT_2FA = range(4)
pending_logins: Dict[int, dict] = {}

async def host_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        reply = update.callback_query.message.reply_text
    else: reply = update.message.reply_text

    uid = update.effective_user.id
    if is_blocked(uid):
        await reply("🚫 You are blocked."); return ConversationHandler.END

    accounts = get_accounts(uid)
    hosted = [a for a in accounts if a.get("hosted")]
    if len(hosted) >= MAX_SCRIPTS_PER_USER:
        await reply(f"📱 Limit reached ({MAX_SCRIPTS_PER_USER}). Please delete one first.")
        return ConversationHandler.END

    if hosted_count() >= MAX_USERBOTS and not is_premium(uid):
        await reply(f"😔 All global server slots are full. Contact {SUPPORT_USERNAME}")
        return ConversationHandler.END

    pending_logins.pop(uid, None)
    await reply(
        f"{TOP}\n║  📤  {bold('Upload Script')}  📤  ║\n{BOTTOM}\n\n"
        f"Please send your <code>.py</code>, <code>.js</code>, or <code>.zip</code> file.\n"
        f"The hoster will scan for malware and auto-fulfill all required packages.\n\n"
        f"💡 <i>Send /cancel to abort at any time</i>", parse_mode=ParseMode.HTML,
    )
    return UPLOAD_WAIT_FILE

async def host_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    doc = update.message.document
    if not doc or not doc.file_name:
        await update.message.reply_text("❌ Please send a valid file.")
        return UPLOAD_WAIT_FILE

    name = doc.file_name
    if not (name.lower().endswith((".py", ".js", ".zip"))):
        await update.message.reply_text("❌ Only .py, .js, or .zip files are supported.")
        return UPLOAD_WAIT_FILE

    if doc.file_size and doc.file_size > MAX_UPLOAD_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ File exceeds {MAX_UPLOAD_MB} MB.")
        return UPLOAD_WAIT_FILE

    msg = await update.message.reply_text("🔎 Scanning and Preparing Environment...", parse_mode=ParseMode.HTML)
    await animate_scanning(msg)

    accounts = get_accounts(uid)
    existing_slots = {a.get("slot") for a in accounts}
    slot = 0
    while slot in existing_slots: slot += 1

    root = script_root(uid, slot)
    root.mkdir(parents=True, exist_ok=True)
    incoming = root / name

    tg_file = await context.bot.get_file(doc.file_id)
    raw_bytes = await tg_file.download_as_bytearray()
    
    # Run Malware Scan
    is_safe, scan_reason = scan_file_for_malware(bytes(raw_bytes), name, uid)
    if not is_safe:
        shutil.rmtree(root, ignore_errors=True)
        await msg.edit_text(f"🚨 Security Alert: {scan_reason}\nUpload rejected.")
        return ConversationHandler.END

    with open(incoming, "wb") as f:
        f.write(raw_bytes)

    if name.lower().endswith(".zip"):
        ok, zmsg = safe_extract_zip(incoming, root)
        try: incoming.unlink()
        except: pass
        if not ok:
            shutil.rmtree(root, ignore_errors=True)
            await msg.edit_text(f"❌ {esc(zmsg)}")
            return ConversationHandler.END
        
        py_files = list(root.rglob("*.py"))
        js_files = list(root.rglob("*.js"))
        language = "python" if py_files else "nodejs" if js_files else "unknown"
        
        entry = find_entrypoint(root, language)
        if not entry:
            shutil.rmtree(root, ignore_errors=True)
            await msg.edit_text("❌ No entrypoint found (main.py, index.js, etc.).")
            return ConversationHandler.END
    else:
        language = "python" if name.lower().endswith(".py") else "nodejs"
        entry = root / ("main.py" if language == "python" else "index.js")
        shutil.move(str(incoming), str(entry))

    result = scan_script(entry)
    context.user_data["pending_slot"] = slot
    context.user_data["pending_entry"] = str(entry.relative_to(root))
    context.user_data["pending_name"] = name
    context.user_data["language"] = language

    account_data = {
        "slot": slot, "name": name, "entrypoint": str(entry.relative_to(root)),
        "phone": result.get("phone"), "hosted": False, "is_stopped": False, 
        "uploaded_at": int(time.time()), "language": language, "needs_rehost": False,
    }
    context.user_data["account_data"] = account_data

    await msg.edit_text(
        f"✅ {bold('Environment prepared')}\n{DIV}\n"
        f"📱 {bold('Phone number required')}\n"
        f"Please send the Telegram phone number to login and link this userbot.\n"
        f"Example: <code>+919876543210</code>", parse_mode=ParseMode.HTML,
    )
    return UPLOAD_WAIT_PHONE

async def host_got_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    phone = update.message.text.strip()
    digits = phone.replace("+", "").replace(" ", "").replace("-", "")
    if not digits.isdigit() or len(digits) < 7:
        await update.message.reply_text("❌ Invalid number. Use format +91XXXXXXXXXX")
        return UPLOAD_WAIT_PHONE
    return await _start_telethon_login(update, context, uid, context.user_data["pending_slot"], phone)

async def _start_telethon_login(update, context, uid, slot, phone):
    msg = await update.message.reply_text(f"⏳ Sending OTP to {esc(phone)}...")
    await animate_connecting(msg)
    try:
        client = TelegramClient(StringSession(), TELEGRAM_API_ID, TELEGRAM_API_HASH)
        await client.connect()
        result = await client.send_code_request(phone)
        pending_logins[uid] = {
            "client": client, "phone": phone, "phone_code_hash": result.phone_code_hash, "slot": slot,
        }
        await msg.edit_text(
            f"📨 {bold('OTP sent')}\n\nEnter the login code you received.\n"
            f"💡 <i>Send with spaces</i> – e.g. <code>1 2 3 4 5</code>", parse_mode=ParseMode.HTML,
        )
        return UPLOAD_WAIT_OTP
    except FloodWaitError as e:
        await msg.edit_text(f"⏳ Flood wait {e.seconds}s. Please try again later.")
        return ConversationHandler.END
    except Exception as e:
        await msg.edit_text(f"❌ Error sending OTP: {esc(str(e)[:120])}")
        return ConversationHandler.END

async def host_got_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    code = update.message.text.strip().replace(" ", "")
    pending = pending_logins.get(uid)
    if not pending:
        await update.message.reply_text("❌ Session expired. Start /host again.")
        return ConversationHandler.END

    client, phone, hash_, slot = pending["client"], pending["phone"], pending["phone_code_hash"], pending["slot"]
    msg = await update.message.reply_text("🔐 Verifying OTP `·`", parse_mode=ParseMode.HTML)
    await animate_otp_verify(msg)

    try:
        await client.sign_in(phone, code, phone_code_hash=hash_)
        session_string = client.session.save()
        await client.disconnect()
        pending_logins.pop(uid, None)
        await msg.edit_text("✅ OTP verified! Preparing 24/7 container...")
        return await _deploy_script(update, context, uid, slot, session_string, phone)
    except SessionPasswordNeededError:
        await msg.edit_text(f"🔒 {bold('2FA Detected')}\n\nPlease enter your Two-Step Verification password.", parse_mode=ParseMode.HTML)
        return UPLOAD_WAIT_2FA
    except PhoneCodeInvalidError:
        await msg.edit_text("❌ Wrong code. Try again.")
        return UPLOAD_WAIT_OTP
    except Exception as e:
        await msg.edit_text(f"❌ Error: {esc(str(e)[:120])}")
        return ConversationHandler.END

async def host_got_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    password = update.message.text
    pending = pending_logins.get(uid)
    if not pending:
        await update.message.reply_text("❌ Session expired. Start /host again.")
        return ConversationHandler.END

    client, slot, phone = pending["client"], pending["slot"], pending["phone"]
    msg = await update.message.reply_text("🔐 Verifying 2FA `·`", parse_mode=ParseMode.HTML)
    await animate_otp_verify(msg)

    try:
        await client.sign_in(password=password)
        session_string = client.session.save()
        await client.disconnect()
        pending_logins.pop(uid, None)
        await msg.edit_text("✅ 2FA verified! Preparing 24/7 container...")
        return await _deploy_script(update, context, uid, slot, session_string, phone)
    except Exception as e:
        await msg.edit_text(f"❌ Wrong 2FA: {esc(str(e)[:120])}")
        return UPLOAD_WAIT_2FA

async def _deploy_script(update, context, uid, slot, session_string, phone):
    account_data = context.user_data.get("account_data")
    if not account_data:
        await update.message.reply_text("❌ Script data lost. Please /host again.")
        return ConversationHandler.END

    account_data.update({
        "session_string": session_string, "hosted": True, "is_stopped": False,
        "hosted_at": int(time.time()), "phone": phone, "needs_rehost": False
    })
    add_account(uid, account_data)

    msg = await update.message.reply_text("🚀 Deploying `▱▱▱`", parse_mode=ParseMode.HTML)
    await animate_deploy(msg)

    root = script_root(uid, slot)
    entry = root / account_data["entrypoint"]
    language = account_data.get("language", "python")
    ok, result_msg = await start_script(uid, slot, entry, session_string, TELEGRAM_API_ID, TELEGRAM_API_HASH, phone, language)

    if ok:
        await msg.edit_text(
            f"{TOP}\n║  🎉  {bold('Deployed & Online 24/7')}  🎉  ║\n{BOTTOM}\n\n"
            f"✅ Your userbot <code>{esc(account_data['name'])}</code> is now active.\n"
            f"📱 Linked to: <code>{esc(phone)}</code>\n"
            f"🖥️ Platform: {bold(account_data['language'].upper())}\n\n"
            f"Use /myaccounts to View Live Logs, Stop, or Restart.", parse_mode=ParseMode.HTML
        )
    else:
        await msg.edit_text(f"❌ Launch failed: {esc(result_msg)}\nUse /myaccounts and check the Logs.")

    return ConversationHandler.END

async def host_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pending_logins.pop(uid, None)
    await update.message.reply_text("🚫 Cancelled.")
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────────────────
#  CONTROL PANEL
# ─────────────────────────────────────────────────────────────────────────

async def cmd_myaccounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_blocked(update.effective_user.id): return
    uid = update.effective_user.id
    hosted = [a for a in get_accounts(uid) if a.get("hosted")]

    if not hosted:
        kb = [[InlineKeyboardButton("✨ 🚀 Deploy New Userbot ✨", callback_data="host")]]
        await update.message.reply_text(f"📭 {bold('No scripts deployed yet')}\n\nClick below to upload and deploy.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    for acct in hosted:
        slot = acct["slot"]
        phone = _phone_label(acct)
        is_alive = is_running(uid, slot)
        is_stopped = acct.get("is_stopped", False)
        needs_rehost = acct.get("needs_rehost", False)
        language = acct.get("language", "python").upper()
        
        status_icon = "⚠️ Session Expired – Re-host required" if needs_rehost else ("🟢 Active 24/7" if is_alive else ("⏸ Stopped" if is_stopped else "🔴 Auto-Restarting"))
        uptime = get_uptime(uid, slot) if is_alive else "N/A"
        
        text = f"⚙️ {bold(f'Userbot #{slot+1}')} | {status_icon}\n📱 <code>{esc(phone)}</code>\n⏱ Uptime: {uptime}\n{'🐍' if language == 'PYTHON' else '🟨'} Language: {language}"
        
        if needs_rehost: kb = [[InlineKeyboardButton("🔄 Re‑host (Re‑login)", callback_data=f"rehost_{slot}")], [InlineKeyboardButton("💥 Delete Userbot", callback_data=f"logout_{slot}")]]
        else: kb = [[InlineKeyboardButton("♻️ Restart Engine", callback_data=f"restart_{slot}"), InlineKeyboardButton("🟢 Start Bot" if is_stopped or not is_alive else "🛑 Stop Bot", callback_data=f"toggle_{slot}")], [InlineKeyboardButton("📝 View Live Logs", callback_data=f"logs_{slot}"), InlineKeyboardButton("💥 Delete Userbot", callback_data=f"logout_{slot}")]]
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    await update.message.reply_text("🔹 /start to return to Main Menu")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_blocked(update.effective_user.id): return
    uid = update.effective_user.id
    hosted = [a for a in get_accounts(uid) if a.get("hosted")]
    if not hosted: return await update.message.reply_text("❌ No scripts deployed.")
    lines = [f"{'🟢' if is_running(uid, a['slot']) else '🔴'} {bold(f'#{a['slot']+1}')} — <code>{esc(_phone_label(a))}</code> ({a.get('language', 'python').upper()})\n   ⏱️ {get_uptime(uid, a['slot']) if is_running(uid, a['slot']) else '—'}" for a in hosted]
    await update.message.reply_text(f"{TOP}\n║  📊  {bold('System Status')}  📊  ║\n{BOTTOM}\n\n" + "\n\n".join(lines), parse_mode=ParseMode.HTML)

# ─────────────────────────────────────────────────────────────────────────
#  CALLBACK HANDLERS
# ─────────────────────────────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid, data = update.effective_user.id, query.data

    if data == "cancel_action": await query.message.delete(); return
    if data == "host": await simulate_button_animation(query, "🚀 Preparing"); await query.message.delete(); await query.message.reply_text("📤 Use /host to upload and deploy your script."); return
    if data == "myaccounts": await simulate_button_animation(query, "🎛 Loading"); await query.message.delete(); await cmd_myaccounts(update, context); return
    if data == "status": await simulate_button_animation(query, "📊 Fetching"); await query.message.delete(); await cmd_status(update, context); return
    if data == "help": await simulate_button_animation(query, "❓ Loading"); await query.message.delete(); await cmd_help(update, context); return
    if data == "support": await simulate_button_animation(query, "📞 Loading"); await query.message.delete(); await cmd_support(update, context); return

    if data.startswith("restart_"):
        slot = int(data.split("_")[1])
        acct = get_account(uid, slot)
        if not acct: return await query.message.reply_text("❌ Script not found.")
        if acct.get("needs_rehost"): return await query.message.reply_text("❌ Session expired. Please re‑host this userbot.")
        await simulate_button_animation(query, "♻️ Restarting")
        acct["is_stopped"] = False
        add_account(uid, acct)
        ok, msg = await start_script(uid, slot, script_root(uid, slot) / acct["entrypoint"], acct.get("session_string"), TELEGRAM_API_ID, TELEGRAM_API_HASH, acct.get("phone", ""), acct.get("language", "python"))
        await query.message.reply_text(f"✅ Userbot #{slot+1} successfully restarted." if ok else f"❌ Restart failed: {msg}")

    elif data.startswith("toggle_"):
        slot = int(data.split("_")[1])
        acct = get_account(uid, slot)
        if not acct: return await query.message.reply_text("❌ Script not found.")
        if acct.get("needs_rehost"): return await query.message.reply_text("❌ Session expired. Please re‑host this userbot.")

        if is_running(uid, slot):
            await simulate_button_animation(query, "🛑 Stopping")
            stop_script(uid, slot)
            acct["is_stopped"] = True
            add_account(uid, acct)
            await query.message.reply_text(f"⏸ Userbot #{slot+1} stopped. Auto-restart disabled.")
        else:
            await simulate_button_animation(query, "🟢 Starting")
            acct["is_stopped"] = False
            add_account(uid, acct)
            ok, msg = await start_script(uid, slot, script_root(uid, slot) / acct["entrypoint"], acct.get("session_string"), TELEGRAM_API_ID, TELEGRAM_API_HASH, acct.get("phone", ""), acct.get("language", "python"))
            await query.message.reply_text(f"▶️ Userbot #{slot+1} started." if ok else f"❌ Start failed: {msg}")

    elif data.startswith("logs_"):
        slot = int(data.split("_")[1])
        log_path = script_root(uid, slot) / "runtime.log"
        await simulate_button_animation(query, "📝 Fetching")
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-30:]
            log_text = "".join(lines).strip()
            if not log_text: log_text = "Logs are currently empty."
        else: log_text = "No log file generated yet."
        await query.message.reply_text(f"📄 {bold(f'Terminal Logs (Slot #{slot+1})')}\n<pre>{esc(log_text)}</pre>", parse_mode=ParseMode.HTML)

    elif data.startswith("rehost_"):
        slot = int(data.split("_")[1])
        acct = get_account(uid, slot)
        if not acct: return await query.message.reply_text("❌ Script not found.")
        context.user_data["rehost_slot"] = slot
        context.user_data["rehost_account"] = acct
        phone = acct.get("phone")
        if phone:
            await query.message.reply_text("🔄 Re‑hosting will re‑authenticate your session. Sending OTP...")
            await _start_telethon_login_from_acct(update, context, uid, slot, acct)
        else:
            context.user_data["waiting_rehost_phone"] = slot
            await query.message.reply_text("Please send your phone number to re‑host.\nPlease use /host to deploy a new script, then delete the expired one.")

    elif data.startswith("logout_"):
        slot = int(data.split("_")[1])
        acct = get_account(uid, slot)
        if not acct: return await query.message.reply_text("❌ Script not found.")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Yes, Delete it!", callback_data=f"confirm_logout_{slot}")], [InlineKeyboardButton("❌ No, Keep it!", callback_data="cancel_action")]])
        await query.message.reply_text(f"⚠️ Are you sure you want to completely delete <code>{esc(_phone_label(acct))}</code>?", parse_mode=ParseMode.HTML, reply_markup=kb)

    elif data.startswith("confirm_logout_"):
        slot = int(data.split("_")[2])
        if get_account(uid, slot):
            stop_script(uid, slot)
            remove_account(uid, slot)
            shutil.rmtree(script_root(uid, slot), ignore_errors=True)
            await query.message.edit_text(f"🗑️ Script #{slot+1} successfully permanently removed.")
        else: await query.message.reply_text("❌ Already removed.")

async def _start_telethon_login_from_acct(update, context, uid, slot, acct):
    phone = acct.get("phone")
    if not phone: return await update.callback_query.message.reply_text("❌ No phone number stored.")
    msg = await update.callback_query.message.reply_text(f"⏳ Sending OTP to {esc(phone)}...")
    await animate_connecting(msg)
    try:
        client = TelegramClient(StringSession(), TELEGRAM_API_ID, TELEGRAM_API_HASH)
        await client.connect()
        result = await client.send_code_request(phone)
        pending_logins[uid] = {"client": client, "phone": phone, "phone_code_hash": result.phone_code_hash, "slot": slot, "is_rehost": True}
        await msg.edit_text(f"📨 {bold('OTP sent')}\n\nEnter the login code you received.\n💡 <i>Send with spaces</i> – e.g. <code>1 2 3 4 5</code>", parse_mode=ParseMode.HTML)
        context.user_data["rehost_slot"] = slot
    except Exception as e: await msg.edit_text(f"❌ Error sending OTP: {esc(str(e)[:120])}")

# ─────────────────────────────────────────────────────────────────────────
#  ADMIN COMMANDS
# ─────────────────────────────────────────────────────────────────────────

async def owner_only(update: Update) -> bool:
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🔒 Owner only.")
        return False
    return True

async def cmd_restartall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update): return
    msg = await update.message.reply_text("🔄 Restarting all active scripts...")
    count = 0
    for uid_str in get_all_users():
        uid = int(uid_str)
        if is_blocked(uid): continue
        for acct in get_accounts(uid):
            if not acct.get("hosted") or not acct.get("session_string") or acct.get("is_stopped") or acct.get("needs_rehost"): continue
            slot = acct["slot"]
            entry = script_root(uid, slot) / acct["entrypoint"]
            if entry.exists():
                ok, _ = await start_script(uid, slot, entry, acct["session_string"], TELEGRAM_API_ID, TELEGRAM_API_HASH, acct.get("phone", ""), acct.get("language", "python"))
                if ok: count += 1
    await msg.edit_text(f"✅ Restarted {count} 24/7 scripts across all users.")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update): return
    await update.message.reply_text(f"{TOP}\n║  📊  {bold('Stats')}  📊  ║\n{BOTTOM}\n\n👥 Users: {user_count()}\n🚀 Hosted: {hosted_count()}\n🟢 Running: {running_count()}\n🔴 Stopped: {hosted_count() - running_count()}\n🚫 Blocked: {len(get_blocked())}\n👑 Sudo: {len(get_sudo_users())}\n🕒 Uptime: {uptime_str()}")

async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update): return
    if not context.args: return await update.message.reply_text("Usage: /block <user_id>")
    try:
        target = int(context.args[0])
        if target == OWNER_ID: return await update.message.reply_text("❌ Cannot block owner.")
        block_user(target); stop_all_for_user(target)
        await update.message.reply_text(f"🚫 Blocked {target}.")
    except: await update.message.reply_text("❌ Invalid ID.")

async def cmd_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update): return
    if not context.args: return await update.message.reply_text("Usage: /unblock <user_id>")
    try:
        target = int(context.args[0])
        unblock_user(target)
        await update.message.reply_text(f"✅ Unblocked {target}.")
    except: await update.message.reply_text("❌ Invalid ID.")

async def cmd_blockeduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update): return
    blocked = get_blocked()
    if not blocked: return await update.message.reply_text("No blocked users.")
    await update.message.reply_text(f"Blocked:\n" + "\n".join(f"🚫 {u}" for u in blocked))

async def cmd_sudolist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update): return
    args = context.args
    if args and args[0] == "add" and len(args) > 1:
        try: add_sudo(int(args[1])); return await update.message.reply_text(f"✅ Added {args[1]} to sudo.")
        except: return await update.message.reply_text("❌ Invalid ID.")
    if args and args[0] == "del" and len(args) > 1:
        try: remove_sudo(int(args[1])); return await update.message.reply_text(f"✅ Removed {args[1]} from sudo.")
        except: return await update.message.reply_text("❌ Invalid ID.")
    sudo = get_sudo_users()
    if not sudo: return await update.message.reply_text("No sudo users.")
    await update.message.reply_text(f"Sudo users:\n" + "\n".join(f"👑 {u}" for u in sudo))

async def cmd_setdp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update): return
    if not update.message.reply_to_message or not update.message.reply_to_message.photo: return await update.message.reply_text("Reply to a photo with /setdp")
    photo = update.message.reply_to_message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    data = await file.download_as_bytearray()
    from io import BytesIO
    try: await context.bot.set_my_profile_photo(BytesIO(bytes(data))); await update.message.reply_text("✅ Profile photo updated.")
    except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update): return
    await update.message.reply_text(f"🔁 Refreshed System Variables\nRunning: {running_count()}\nHosted: {hosted_count()}\nUptime: {uptime_str()}")

async def cmd_secretfunction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update): return
    await update.message.reply_text(f"🔐 Secret Admin Commands:\n/sudolist add <uid>\n/sudolist del <uid>\n/block <uid>\n/unblock <uid>\n/restartall\n/refresh\n/stats\n/setdp\n/blockeduser")

# ─────────────────────────────────────────────────────────────────────────
#  AUTO HEALTH CHECK 
# ─────────────────────────────────────────────────────────────────────────

async def auto_health_check(context: ContextTypes.DEFAULT_TYPE):
    try:
        for uid_str in get_all_users():
            uid = int(uid_str)
            if is_blocked(uid): continue
            for acct in get_accounts(uid):
                if not acct.get("hosted") or not acct.get("session_string") or acct.get("is_stopped") or acct.get("needs_rehost"): continue
                slot = acct["slot"]
                if not is_running(uid, slot):
                    root = script_root(uid, slot)
                    entry = root / acct["entrypoint"]
                    if not entry.exists(): continue
                    language = acct.get("language", "python")
                    valid, _ = await validate_session(acct["session_string"], TELEGRAM_API_ID, TELEGRAM_API_HASH)
                    if not valid:
                        acct["needs_rehost"] = True
                        add_account(uid, acct)
                        continue
                    await start_script(uid, slot, entry, acct["session_string"], TELEGRAM_API_ID, TELEGRAM_API_HASH, acct.get("phone", ""), language)
    except Exception as e: logging.error(f"Health check error: {e}", exc_info=True)

# ─────────────────────────────────────────────────────────────────────────
#  PERIODIC SESSION VALIDATOR
# ─────────────────────────────────────────────────────────────────────────

async def session_validator(context: ContextTypes.DEFAULT_TYPE):
    try:
        for uid_str in get_all_users():
            uid = int(uid_str)
            if is_blocked(uid): continue
            for acct in get_accounts(uid):
                if not acct.get("hosted") or not acct.get("session_string") or acct.get("needs_rehost") or acct.get("is_stopped"): continue
                valid, _ = await validate_session(acct["session_string"], TELEGRAM_API_ID, TELEGRAM_API_HASH)
                if not valid:
                    acct["needs_rehost"] = True
                    add_account(uid, acct)
                    stop_script(uid, acct["slot"])
                    logging.info(f"Session expired for user {uid}, slot {acct['slot']}")
    except Exception as e: logging.error(f"Session validator error: {e}", exc_info=True)

# ─────────────────────────────────────────────────────────────────────────
#  GRACEFUL SHUTDOWN
# ─────────────────────────────────────────────────────────────────────────

def shutdown_handler(sig, frame):
    logging.info("Shutting down, stopping all scripts...")
    for key in list(_procs.keys()): stop_script(key[0], key[1])
    sys.exit(0)

import signal
signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

# ─────────────────────────────────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "Open dashboard"), BotCommand("host", "Deploy new script"),
            BotCommand("myaccounts", "Control Panel (Logs/Stop/Restart)"), BotCommand("status", "System Status"),
            BotCommand("support", "Get support"),
        ])
        count = 0
        for uid_str in get_all_users():
            uid = int(uid_str)
            if is_blocked(uid): continue
            for acct in get_accounts(uid):
                if not acct.get("hosted") or not acct.get("session_string") or acct.get("is_stopped"): continue
                valid, _ = await validate_session(acct["session_string"], TELEGRAM_API_ID, TELEGRAM_API_HASH)
                if not valid:
                    acct["needs_rehost"] = True
                    add_account(uid, acct)
                    logging.warning(f"Session expired for user {uid}, slot {acct['slot']} on startup")
                    continue
                slot = acct["slot"]
                root = script_root(uid, slot)
                entry = root / acct["entrypoint"]
                if not entry.exists(): continue
                language = acct.get("language", "python")
                ok, _ = await start_script(uid, slot, entry, acct["session_string"], TELEGRAM_API_ID, TELEGRAM_API_HASH, acct.get("phone", ""), language)
                if ok: count += 1
        logging.info(f"Auto-started {count} userbots into 24/7 hosting state.")
        if app.job_queue: app.job_queue.run_repeating(session_validator, interval=SESSION_VALIDATE_INTERVAL, first=60)
        else: logging.warning("JobQueue not available – session validator will not run.")
    except Exception as e: logging.error(f"post_init error: {e}", exc_info=True)

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if not BOT_TOKEN or not OWNER_ID: raise ValueError("BOT_TOKEN and OWNER_ID required.")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    host_conv = ConversationHandler(
        entry_points=[CommandHandler("host", host_start), CallbackQueryHandler(host_start, pattern="^host$")],
        states={
            UPLOAD_WAIT_FILE: [MessageHandler(filters.Document.ALL, host_got_file)],
            UPLOAD_WAIT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_got_phone)],
            UPLOAD_WAIT_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_got_otp)],
            UPLOAD_WAIT_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_got_2fa)],
        },
        fallbacks=[CommandHandler("cancel", host_cancel)], allow_reentry=True,
    )
    app.add_handler(host_conv)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("support", cmd_support))
    app.add_handler(CommandHandler("myaccounts", cmd_myaccounts))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("restartall", cmd_restartall))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("block", cmd_block))
    app.add_handler(CommandHandler("unblock", cmd_unblock))
    app.add_handler(CommandHandler("blockeduser", cmd_blockeduser))
    app.add_handler(CommandHandler("sudolist", cmd_sudolist))
    app.add_handler(CommandHandler("setdp", cmd_setdp))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("secretfunction", cmd_secretfunction))

    app.add_handler(CallbackQueryHandler(callback_handler))

    if app.job_queue: app.job_queue.run_repeating(auto_health_check, interval=20, first=10)
    else: logging.warning("JobQueue not available – health check will not run.")

    logging.info("🚀 Sid Hoster Cloud Started (Enhanced Edition)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    try: main()
    except Exception as e: logging.critical(f"Fatal error in main: {e}", exc_info=True); sys.exit(1)
