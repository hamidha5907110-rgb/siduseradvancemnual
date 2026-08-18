#!/usr/bin/env python3
"""
SID MANUAL USERBOT HOSTER V8

A Telegram bot that hosts USER-PROVIDED Python userbot scripts.
The hoster does not generate a userbot and does not replace uploaded code.
Authentication stays inside the uploaded user script.

Railway environment variables:
  BOT_TOKEN                required
  OWNER_ID                 required
  SUPPORT_USERNAME         optional, default @support
  MAX_RUNNING              optional, default 50
  FREE_SCRIPT_LIMIT        optional, default 3
  PREMIUM_SCRIPT_LIMIT     optional, default 5
  REFERRAL_TARGET          optional, default 5
  ADMIN_IDS                optional comma separated additional admins

Security notes:
  - Uploaded scripts run as subprocesses with a sanitized environment.
  - The bot token is never passed into uploaded scripts.
  - API_ID/API_HASH are injected only when a per-user profile exists.
  - OTP/2FA passwords are NOT collected by this hoster. The uploaded script
    remains responsible for its own Telegram authentication.
  - For real multi-tenant production use, isolate each script in a container
    or separate Railway service. A subprocess is not a security sandbox.
"""

import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import sys
import time
import uuid
import html
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    filters,
)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8602762499:AAHRU4hAlT6G94Iz5ZHmPEjekT80G5Z4fpk").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support").strip()
MAX_RUNNING = max(1, int(os.getenv("MAX_RUNNING", "50") or 50))
FREE_SCRIPT_LIMIT = max(1, int(os.getenv("FREE_SCRIPT_LIMIT", "3") or 3))
PREMIUM_SCRIPT_LIMIT = max(FREE_SCRIPT_LIMIT, int(os.getenv("PREMIUM_SCRIPT_LIMIT", "5") or 5))
REFERRAL_TARGET = max(1, int(os.getenv("REFERRAL_TARGET", "5") or 5))

EXTRA_ADMINS = set()
for raw in os.getenv("ADMIN_IDS", "").replace(";", ",").split(","):
    raw = raw.strip()
    if raw.isdigit():
        EXTRA_ADMINS.add(int(raw))

ROOT = Path(os.getcwd())
DATA = ROOT / "data_hoster"
USERS = DATA / "users"
DATA.mkdir(parents=True, exist_ok=True)
USERS.mkdir(parents=True, exist_ok=True)
WELCOME_FILE = DATA / "welcome_video.json"
GLOBAL_FILE = DATA / "global.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("sid-hoster")

# ---------------------------------------------------------------------------
# VISUAL STYLE / ANIMATION
# ---------------------------------------------------------------------------
DIV = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
TOP = "╭──────────────────────────────╮"
BOT = "╰──────────────────────────────╯"
SPARK = "✦ · ✧ · ✦ · ✧ · ✦"
BAR_FULL = "██████████"
BAR_EMPTY = "░░░░░░░░░░"

def esc(t: str) -> str:
    return html.escape(str(t), quote=False)

def premium_button(text: str, emoji: str, callback: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(f"{emoji}  {text}  ✦", callback_data=callback)

def progress_bar(value: int, total: int, width: int = 10) -> str:
    total = max(1, total)
    filled = max(0, min(width, int((value / total) * width)))
    return "█" * filled + "░" * (width - filled)


def bold(t: str) -> str:
    return f"<b>{t}</b>"


def mono(t: str) -> str:
    return f"<code>{t}</code>"


def user_dir(uid: int) -> Path:
    p = USERS / str(uid)
    p.mkdir(parents=True, exist_ok=True)
    return p


def scripts_dir(uid: int) -> Path:
    p = user_dir(uid) / "scripts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def meta_path(uid: int) -> Path:
    return user_dir(uid) / "meta.json"


def api_path(uid: int) -> Path:
    return user_dir(uid) / "api.json"


def scripts_path(uid: int) -> Path:
    return user_dir(uid) / "scripts.json"


class JSONStore:
    _lock = asyncio.Lock()

    @staticmethod
    def load(path: Path, default: Any) -> Any:
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    @staticmethod
    def save(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)


# ---------------------------------------------------------------------------
# USER / REFERRAL / PREMIUM DATA
# ---------------------------------------------------------------------------

def get_meta(uid: int) -> dict:
    return JSONStore.load(meta_path(uid), {})


def save_meta(uid: int, data: dict) -> dict:
    meta = get_meta(uid)
    meta.update(data)
    JSONStore.save(meta_path(uid), meta)
    return meta


def get_api(uid: int) -> dict:
    return JSONStore.load(api_path(uid), {})


def save_api(uid: int, api_id: int, api_hash: str) -> None:
    JSONStore.save(api_path(uid), {
        "api_id": int(api_id),
        "api_hash": api_hash,
        "updated_at": int(time.time()),
    })


def get_scripts(uid: int) -> list:
    return JSONStore.load(scripts_path(uid), [])


def save_scripts(uid: int, items: list) -> None:
    JSONStore.save(scripts_path(uid), items)


def find_script(uid: int, slot: int) -> Optional[dict]:
    for item in get_scripts(uid):
        if int(item.get("slot", -1)) == int(slot):
            return item
    return None


def replace_script(uid: int, entry: dict) -> None:
    items = [x for x in get_scripts(uid) if int(x.get("slot", -1)) != int(entry["slot"])]
    items.append(entry)
    items.sort(key=lambda x: int(x.get("slot", 0)))
    save_scripts(uid, items)


def remove_script(uid: int, slot: int) -> None:
    save_scripts(uid, [x for x in get_scripts(uid) if int(x.get("slot", -1)) != int(slot)])


def all_user_ids() -> List[int]:
    out = []
    for p in USERS.iterdir() if USERS.exists() else []:
        if p.is_dir() and p.name.isdigit():
            out.append(int(p.name))
    return out


def is_admin(uid: int) -> bool:
    return uid == OWNER_ID or uid in EXTRA_ADMINS


def is_premium(uid: int) -> bool:
    meta = get_meta(uid)
    return bool(meta.get("premium") or is_admin(uid))


def limit_for(uid: int) -> int:
    return PREMIUM_SCRIPT_LIMIT if is_premium(uid) else FREE_SCRIPT_LIMIT


def referral_link(context: ContextTypes.DEFAULT_TYPE, uid: int) -> str:
    username = context.bot.username or "SIDHosterBot"
    return f"https://t.me/{username}?start=ref_{uid}"


def register_user(uid: int, name: str, referrer: Optional[int] = None) -> dict:
    meta = get_meta(uid)
    if not meta:
        meta = {
            "first_name": name,
            "joined_at": int(time.time()),
            "premium": False,
            "referrer": None,
            "referrals": [],
        }
        if referrer and referrer != uid:
            meta["referrer"] = int(referrer)
        save_meta(uid, meta)
    else:
        if name:
            meta["first_name"] = name
            save_meta(uid, meta)

    # Reward only once and only for a newly-created account.
    if referrer and referrer != uid and meta.get("referrer") == int(referrer):
        ref_meta = get_meta(int(referrer))
        refs = list(ref_meta.get("referrals") or [])
        if uid not in refs:
            refs.append(uid)
            ref_meta["referrals"] = refs
            if len(refs) >= REFERRAL_TARGET:
                ref_meta["premium"] = True
                ref_meta["premium_reason"] = "referrals"
                ref_meta["premium_at"] = int(time.time())
            save_meta(int(referrer), ref_meta)
    return get_meta(uid)

# ---------------------------------------------------------------------------
# PROCESS MANAGER
# ---------------------------------------------------------------------------

PROCS: Dict[Tuple[int, int], asyncio.subprocess.Process] = {}
START_TIMES: Dict[Tuple[int, int], float] = {}
PROC_LOGS: Dict[Tuple[int, int], Path] = {}
PROC_LOCK = asyncio.Lock()


def running_count() -> int:
    dead = []
    for key, proc in list(PROCS.items()):
        if proc.returncode is not None:
            dead.append(key)
    for key in dead:
        PROCS.pop(key, None)
        START_TIMES.pop(key, None)
    return len(PROCS)


def script_root(uid: int, slot: int) -> Path:
    return scripts_dir(uid) / f"slot_{slot}"


def script_file(uid: int, slot: int) -> Path:
    return script_root(uid, slot) / "main.py"


def log_file(uid: int, slot: int) -> Path:
    root = script_root(uid, slot)
    root.mkdir(parents=True, exist_ok=True)
    return root / "runtime.log"


async def stop_script(uid: int, slot: int) -> bool:
    key = (uid, slot)
    proc = PROCS.pop(key, None)
    START_TIMES.pop(key, None)
    if not proc:
        return False
    try:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    except ProcessLookupError:
        pass
    except Exception as exc:
        log.warning("stop_script %s: %s", key, exc)
    return True


async def start_script(uid: int, slot: int) -> Tuple[bool, str]:
    item = find_script(uid, slot)
    if not item:
        return False, "Script not found."
    if running_count() >= MAX_RUNNING and (uid, slot) not in PROCS and not is_admin(uid):
        return False, f"Server running limit reached ({MAX_RUNNING})."

    path = script_file(uid, slot)
    if not path.exists():
        return False, "Uploaded file is missing."

    await stop_script(uid, slot)

    api = get_api(uid)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "HOME": str(script_root(uid, slot)),
        "TMPDIR": str(script_root(uid, slot)),
    }
    if api.get("api_id") and api.get("api_hash"):
        env["API_ID"] = str(api["api_id"])
        env["API_HASH"] = str(api["api_hash"])
        env["TELEGRAM_API_ID"] = str(api["api_id"])
        env["TELEGRAM_API_HASH"] = str(api["api_hash"])
    env["HOSTER_USER_ID"] = str(uid)
    env["HOSTER_SLOT"] = str(slot)

    logfile = log_file(uid, slot)
    with logfile.open("a", encoding="utf-8") as lf:
        lf.write(f"\n\n===== START {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")

    lf = logfile.open("a", encoding="utf-8", buffering=1)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            str(path),
            cwd=str(script_root(uid, slot)),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=lf,
            stderr=lf,
            start_new_session=True,
        )
    except Exception as exc:
        lf.close()
        return False, f"Launch failed: {str(exc)[:160]}"

    await asyncio.sleep(0.35)
    if proc.returncode is not None:
        try:
            await proc.wait()
        except Exception:
            pass
        lf.close()
        return False, f"Process exited during startup (code {proc.returncode}). See runtime.log."

    key = (uid, slot)
    PROCS[key] = proc
    START_TIMES[key] = time.time()
    PROC_LOGS[key] = logfile
    replace_script(uid, {**item, "status": "running", "last_started": int(time.time())})
    return True, "Running"


async def restart_script(uid: int, slot: int) -> Tuple[bool, str]:
    return await start_script(uid, slot)


async def stop_all_for_user(uid: int) -> None:
    for key in list(PROCS):
        if key[0] == uid:
            await stop_script(*key)


async def health_loop(app: Application) -> None:
    while True:
        await asyncio.sleep(60)
        for uid in all_user_ids():
            for item in get_scripts(uid):
                slot = int(item.get("slot", 0))
                key = (uid, slot)
                proc = PROCS.get(key)
                if proc and proc.returncode is None:
                    continue
                if item.get("autostart"):
                    last_attempt = int(item.get("last_health_attempt", 0) or 0)
                    if time.time() - last_attempt < 180:
                        continue
                    replace_script(uid, {**item, "last_health_attempt": int(time.time())})
                    ok, msg = await start_script(uid, slot)
                    if not ok:
                        log.warning("health restart uid=%s slot=%s: %s", uid, slot, msg)

# ---------------------------------------------------------------------------
# SCRIPT SCANNER
# ---------------------------------------------------------------------------

API_ID_RE = re.compile(r"(?im)(?:API_ID|api_id)\s*=\s*[\"']?(\d{5,12})")
API_HASH_RE = re.compile(r"(?im)(?:API_HASH|api_hash)\s*=\s*[\"']([A-Za-z0-9]{16,128})[\"']")

# These are warnings/blocks for common obvious attempts to exfiltrate the hoster's
# environment or launch shell commands. This is not a sandbox.
DANGEROUS_PATTERNS = [
    (re.compile(r"os\.environ\.(get|__getitem__)\(\s*[\"'](BOT_TOKEN|OWNER_ID)[\"']", re.I), "Attempts to read hoster secrets"),
    (re.compile(r"subprocess\.(Popen|run|call|check_output)\s*\(", re.I), "Uses subprocess execution"),
    (re.compile(r"os\.(system|popen)\s*\(", re.I), "Uses OS command execution"),
    (re.compile(r"requests\.(post|put)\s*\(", re.I), "Contains outbound HTTP write requests"),
    (re.compile(r"socket\.create_connection\s*\(", re.I), "Contains direct socket connections"),
]


def detect_api(text: str) -> Tuple[Optional[int], Optional[str]]:
    aid = None
    ahash = None
    m = API_ID_RE.search(text)
    if m:
        try:
            aid = int(m.group(1))
        except ValueError:
            pass
    m = API_HASH_RE.search(text)
    if m:
        ahash = m.group(1)
    return aid, ahash


def scan_script(path: Path) -> dict:
    raw = path.read_bytes()
    if len(raw) > 3_000_000:
        return {"ok": False, "reason": "Script is larger than 3 MB."}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "reason": "Only UTF-8 Python source is accepted."}

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return {"ok": False, "reason": f"Python syntax error on line {exc.lineno}: {exc.msg}"}

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])

    warnings = []
    blocked = []
    for rx, label in DANGEROUS_PATTERNS:
        if rx.search(text):
            warnings.append(label)
            if "hoster secrets" in label.lower():
                blocked.append(label)

    aid, ahash = detect_api(text)
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "ok": not blocked,
        "size": len(raw),
        "sha256": digest,
        "imports": sorted(imports),
        "api_id": aid,
        "api_hash": ahash,
        "warnings": warnings,
        "blocked": blocked,
    }

# ---------------------------------------------------------------------------
# UI / RUNTIME HELPERS
# ---------------------------------------------------------------------------

def process_status(uid: int, slot: int) -> tuple[bool, str]:
    proc = PROCS.get((uid, slot))
    alive = bool(proc and proc.returncode is None)
    if not alive:
        return False, "Stopped"
    started = START_TIMES.get((uid, slot), time.time())
    age = int(time.time() - started)
    h, rem = divmod(age, 3600)
    m, s = divmod(rem, 60)
    return True, f"{h:02d}:{m:02d}:{s:02d}"

def script_limits_text(uid: int) -> str:
    meta = get_meta(uid)
    refs = len(meta.get("referrals") or [])
    plan = "PREMIUM" if is_premium(uid) else "FREE"
    return (
        f"👑 <b>{plan}</b>  •  "
        f"📦 <b>{len(get_scripts(uid))}/{limit_for(uid)}</b>  •  "
        f"🎁 <b>{refs}/{REFERRAL_TARGET}</b>"
    )

def compact_script_line(uid: int, item: dict) -> str:
    slot = int(item.get("slot", 0))
    name = esc(item.get("name") or f"Script #{slot+1}")
    alive, runtime = process_status(uid, slot)
    icon = "🟢" if alive else "🔴"
    return f"{icon} <b>#{slot+1}</b> {name[:40]}  <code>{runtime}</code>"

async def typing_animation(message, frames: list[str], delay: float = 0.06):
    await animate(message, frames, delay)

# ---------------------------------------------------------------------------
# UI HELPERS
# ---------------------------------------------------------------------------

async def animate(msg, frames: List[str], delay: float = 0.08) -> None:
    for frame in frames:
        try:
            await msg.edit_text(frame, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await asyncio.sleep(delay)


def main_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [premium_button("UPLOAD SCRIPT", "📤", "upload")],
        [premium_button("SEE STATUS", "📊", "status"), premium_button("RESTART", "🔄", "restart_menu")],
        [premium_button("LOGOUT", "🚪", "logout_menu")],
    ])


def script_keyboard(items: list, action_prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        slot = int(item.get("slot", 0))
        title = item.get("name") or f"Script #{slot + 1}"
        rows.append([InlineKeyboardButton(f"#{slot + 1} • {title[:28]}", callback_data=f"{action_prefix}:{slot}")])
    rows.append([InlineKeyboardButton("❌ Close", callback_data="close")])
    return InlineKeyboardMarkup(rows)


def dashboard_text(uid: int) -> str:
    meta = get_meta(uid)
    items = get_scripts(uid)
    premium = is_premium(uid)
    running = sum(1 for x in items if process_status(uid, int(x.get("slot", 0)))[0])
    refs = len(meta.get("referrals") or [])
    api = get_api(uid)
    api_state = "✅ READY" if api.get("api_id") and api.get("api_hash") else "⚠️ ON UPLOAD"
    plan = "👑 PREMIUM" if premium else "🆓 FREE"
    limit = limit_for(uid)
    used = len(items)
    return (
        f"{TOP}\n"
        f"│  ✦ <b>SID MANUAL HOSTER</b> ✦ │\n"
        f"{BOT}\n\n"
        f"👋 <b>Welcome</b>, {esc(meta.get('first_name','User'))}\n"
        f"{SPARK}\n"
        f"💎 <b>PLAN</b>      {plan}\n"
        f"📦 <b>SCRIPTS</b>   {used}/{limit}   <code>{progress_bar(used, limit)}</code>\n"
        f"🟢 <b>RUNNING</b>   {running}\n"
        f"🔐 <b>API PROFILE</b> {api_state}\n"
        f"🎁 <b>REFERRALS</b> {refs}/{REFERRAL_TARGET}\n"
        f"{DIV}\n"
        f"<i>Manual hosting • Your code stays yours • No source rewriting</i>"
    )


# ---------------------------------------------------------------------------
# COMMANDS
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    name = update.effective_user.first_name or "User"
    ref = None
    if context.args:
        raw = context.args[0]
        if raw.startswith("ref_") and raw[4:].isdigit():
            ref = int(raw[4:])
    register_user(uid, name, ref)

    welcome = JSONStore.load(WELCOME_FILE, None)
    text = dashboard_text(uid)
    kb = main_keyboard(uid)

    if welcome and welcome.get("file_id"):
        try:
            await update.message.reply_video(
                video=welcome["file_id"],
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            return
        except Exception as exc:
            log.warning("welcome video failed: %s", exc)

    msg = await update.message.reply_text(
        f"<b>SID USERBOT HOSTER</b>\n\n⚡ Booting secure control panel...",
        parse_mode=ParseMode.HTML,
    )
    await animate(msg, [
        "<b>SID USERBOT HOSTER</b>\n\n⚡ Loading `▱▱▱▱▱`",
        "<b>SID USERBOT HOSTER</b>\n\n⚡ Loading `▰▱▱▱▱`",
        "<b>SID USERBOT HOSTER</b>\n\n⚡ Loading `▰▰▱▱▱`",
        "<b>SID USERBOT HOSTER</b>\n\n⚡ Loading `▰▰▰▱▱`",
        "<b>SID USERBOT HOSTER</b>\n\n✅ Control panel ready `▰▰▰▰▰`",
    ])
    await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    meta = get_meta(uid)
    refs = len(meta.get("referrals") or [])
    status = "👑 Premium unlocked!" if is_premium(uid) else f"{refs}/{REFERRAL_TARGET} successful referrals"
    await update.message.reply_text(
        f"🎁 <b>REFER & EARN</b>\n\n"
        f"{status}\n\n"
        f"🔗 <code>{referral_link(context, uid)}</code>\n\n"
        f"Invite real new users. Each qualifying signup counts once.",
        parse_mode=ParseMode.HTML,
    )


async def premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    await update.message.reply_text(
        f"👑 <b>PREMIUM</b>\n\n"
        f"Free limit: <b>{FREE_SCRIPT_LIMIT}</b> scripts\n"
        f"Premium limit: <b>{PREMIUM_SCRIPT_LIMIT}</b> scripts\n\n"
        f"🎁 Refer {REFERRAL_TARGET} qualifying users to unlock Premium.",
        parse_mode=ParseMode.HTML,
    )


async def begin_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if len(get_scripts(uid)) >= limit_for(uid):
        await update.effective_message.reply_text(
            f"⚠️ <b>Hosting limit reached</b>\n\nYou can host <b>{limit_for(uid)}</b> scripts on your current plan.",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END
    await update.effective_message.reply_text(
        f"📤 <b>UPLOAD USERBOT SCRIPT</b>\n\n"
        f"Send your <code>.py</code> file.\n"
        f"I will syntax-check it and inspect API configuration.\n\n"
        f"The hoster does not modify your userbot source.",
        parse_mode=ParseMode.HTML,
    )
    return 1


async def receive_script(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    doc = update.message.document
    if not doc or not doc.file_name or not doc.file_name.lower().endswith(".py"):
        await update.message.reply_text("❌ Send a Python <code>.py</code> document.", parse_mode=ParseMode.HTML)
        return 1

    if len(get_scripts(uid)) >= limit_for(uid):
        await update.message.reply_text("⚠️ Your script limit has been reached.", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    msg = await update.message.reply_text("🔎 <b>Scanning upload...</b>", parse_mode=ParseMode.HTML)
    slot = 0
    used = {int(x.get("slot", -1)) for x in get_scripts(uid)}
    while slot in used:
        slot += 1

    root = script_root(uid, slot)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "main.py"
    tg_file = await context.bot.get_file(doc.file_id)
    await tg_file.download_to_drive(custom_path=str(path))

    result = scan_script(path)
    if not result.get("ok"):
        shutil.rmtree(root, ignore_errors=True)
        await msg.edit_text(
            f"❌ <b>Scan rejected</b>\n\n{result.get('reason','Unknown scan error')}",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    api = get_api(uid)
    detected_id = result.get("api_id")
    detected_hash = result.get("api_hash")
    if detected_id and detected_hash:
        save_api(uid, detected_id, detected_hash)
        api_state = "✅ API ID/hash detected in script and saved to your private profile."
    elif api.get("api_id") and api.get("api_hash"):
        api_state = "✅ Your saved API profile will be injected when the script starts."
    else:
        # Store the ready script and continue into the API wizard.
        api_state = "⚠️ API ID/hash are missing."

    entry = {
        "slot": slot,
        "name": doc.file_name,
        "stored_name": "main.py",
        "sha256": result["sha256"],
        "size": result["size"],
        "uploaded_at": int(time.time()),
        "status": "ready",
        "autostart": True,
        "warnings": result.get("warnings", []),
    }
    replace_script(uid, entry)

    warn_text = ""
    if result.get("warnings"):
        warn_text = "\n⚠️ <b>Scanner notes:</b>\n" + "\n".join(f"• {x}" for x in result["warnings"])

    context.user_data["pending_slot"] = slot
    context.user_data["pending_warn"] = warn_text
    context.user_data["pending_name"] = doc.file_name

    if not ((detected_id and detected_hash) or (api.get("api_id") and api.get("api_hash"))):
        await msg.edit_text(
            f"✅ <b>Script scanned successfully</b>\n\n"
            f"📄 Name: <code>{doc.file_name}</code>\n"
            f"📦 Size: <b>{result['size']:,}</b> bytes\n"
            f"🔐 SHA256: <code>{result['sha256'][:20]}…</code>\n\n"
            f"🔐 <b>API profile missing</b>\n"
            f"Send your Telegram API ID now. Your value will be saved only for your user profile.",
            parse_mode=ParseMode.HTML,
        )
        return 2

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Host Now", callback_data=f"host:{slot}")],
        [InlineKeyboardButton("🗑️ Delete", callback_data=f"delete:{slot}"), InlineKeyboardButton("❌ Close", callback_data="close")],
    ])
    await msg.edit_text(
        f"✅ <b>Script scanned successfully</b>\n\n"
        f"📄 Name: <code>{doc.file_name}</code>\n"
        f"📦 Size: <b>{result['size']:,}</b> bytes\n"
        f"🔐 SHA256: <code>{result['sha256'][:20]}…</code>\n\n"
        f"{api_state}{warn_text}\n\n"
        f"Press <b>Host Now</b> to run your uploaded script.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    return ConversationHandler.END


async def receive_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    if not raw.isdigit() or not (10000 <= int(raw) <= 999999999999):
        await update.message.reply_text("❌ Invalid API ID. Send the numeric API ID from my.telegram.org.")
        return 2
    context.user_data["api_id_pending"] = int(raw)
    await update.message.reply_text("🔑 Now send your Telegram API hash.")
    return 3


async def receive_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    api_hash = update.message.text.strip()
    api_id = context.user_data.get("api_id_pending")
    if not api_id or not re.fullmatch(r"[A-Za-z0-9]{16,128}", api_hash):
        await update.message.reply_text("❌ Invalid API hash. Please send the hash shown at my.telegram.org.")
        return 3
    uid = update.effective_user.id
    save_api(uid, int(api_id), api_hash)
    slot = context.user_data.get("pending_slot")
    name = context.user_data.get("pending_name", "main.py")
    warn_text = context.user_data.get("pending_warn", "")
    context.user_data.pop("api_id_pending", None)
    context.user_data.pop("pending_slot", None)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Host Now", callback_data=f"host:{int(slot)}")],
        [InlineKeyboardButton("🗑️ Delete", callback_data=f"delete:{int(slot)}"), InlineKeyboardButton("❌ Close", callback_data="close")],
    ])
    await update.message.reply_text(
        f"✅ <b>API profile saved</b>\n\n"
        f"📄 <code>{name}</code> is ready to host.{warn_text}\n\n"
        f"Your API ID/hash are stored under your user profile and reused for future uploads.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    return ConversationHandler.END


async def cancel_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("🚫 Upload cancelled.")
    return ConversationHandler.END


async def setapi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🔐 <b>Per-user API profile</b>\n\n"
        "Usage: <code>/setapi API_ID API_HASH</code>\n\n"
        "Your profile is stored under your own user directory.",
        parse_mode=ParseMode.HTML,
    )
    if len(context.args) >= 2:
        try:
            api_id = int(context.args[0])
            api_hash = context.args[1].strip()
            if api_id <= 0 or not re.fullmatch(r"[A-Za-z0-9]{16,128}", api_hash):
                raise ValueError
            save_api(update.effective_user.id, api_id, api_hash)
            await update.message.reply_text("✅ API profile saved.", parse_mode=ParseMode.HTML)
        except Exception:
            await update.message.reply_text("❌ Invalid API ID/API hash format.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    items = get_scripts(uid)
    if not items:
        await update.message.reply_text("📭 <b>No scripts uploaded yet.</b>", parse_mode=ParseMode.HTML)
        return
    lines = [compact_script_line(uid, x) for x in items]
    await update.message.reply_text(
        f"{TOP}\n│ ✦ <b>LIVE STATUS</b> ✦ │\n{BOT}\n\n"
        f"{script_limits_text(uid)}\n{DIV}\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=script_keyboard(items, "restart"),
    )


async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = get_scripts(update.effective_user.id)
    if not items:
        await update.message.reply_text("📭 No scripts to restart.")
        return
    await update.message.reply_text(f"{TOP}\n│ ✦ <b>RESTART CENTER</b> ✦ │\n{BOT}\n\nSelect a hosted script:", parse_mode=ParseMode.HTML, reply_markup=script_keyboard(items, "restart"))


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = get_scripts(update.effective_user.id)
    if not items:
        await update.message.reply_text("📭 Nothing to logout/delete.")
        return
    await update.message.reply_text(f"{TOP}\n│ ✦ <b>LOGOUT CENTER</b> ✦ │\n{BOT}\n\nStop and permanently remove a script:", parse_mode=ParseMode.HTML, reply_markup=script_keyboard(items, "logout"))


# ---------------------------------------------------------------------------
# CALLBACKS
# ---------------------------------------------------------------------------

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer("✨ Opening…")
    uid = update.effective_user.id
    data = q.data or ""

    if data == "close":
        await q.message.edit_text("✅ Closed.")
        return
    if data == "upload":
        await q.message.reply_text(f"{TOP}\n│ ✦ <b>UPLOAD CENTER</b> ✦ │\n{BOT}\n\n📤 Use <code>/host</code> and send your <code>.py</code> file.", parse_mode=ParseMode.HTML)
        return
    if data == "status":
        items = get_scripts(uid)
        if not items:
            await q.message.reply_text("📭 No scripts uploaded yet.")
            return
        lines = []
        for x in items:
            slot = int(x.get("slot", 0))
            p = PROCS.get((uid, slot))
            alive = bool(p and p.returncode is None)
            icon = "🟢" if alive else "🔴"
            started = START_TIMES.get((uid, slot))
            uptime = "—"
            if alive and started:
                uptime = f"{int(time.time()-started)}s"
            lines.append(f"{icon} <b>#{slot+1}</b> {x.get('name','main.py')} • uptime {mono(uptime)}")
        await q.message.reply_text(
            f"📊 <b>YOUR HOSTED SCRIPTS</b>\n\n" + "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )
        return
    if data == "restart_menu":
        items = get_scripts(uid)
        await q.message.reply_text("🔄 <b>Select a script to restart:</b>", parse_mode=ParseMode.HTML, reply_markup=script_keyboard(items, "restart"))
        return
    if data == "logout_menu":
        items = get_scripts(uid)
        if not items:
            await q.message.reply_text("📭 Nothing to logout/delete.")
            return
        await q.message.reply_text("🚪 <b>Select the hosted script to stop & remove:</b>", parse_mode=ParseMode.HTML, reply_markup=script_keyboard(items, "logout"))
        return
    if data.startswith("host:"):
        slot = int(data.split(":", 1)[1])
        msg = await q.message.reply_text("🚀 <b>Preparing host...</b>", parse_mode=ParseMode.HTML)
        await animate(msg, ["🚀 Preparing `▱▱▱`", "📦 Launching `▰▱▱`", "⚡ Checking `▰▰▰`"])
        ok, detail = await start_script(uid, slot)
        if ok:
            await msg.edit_text(f"✅ <b>Hosted successfully</b>\n\nScript <b>#{slot+1}</b> is running.", parse_mode=ParseMode.HTML)
        else:
            await msg.edit_text(f"❌ <b>Host failed</b>\n\n{detail}", parse_mode=ParseMode.HTML)
        return
    if data.startswith("restart:"):
        slot = int(data.split(":", 1)[1])
        msg = await q.message.reply_text("🔄 Restarting...", parse_mode=ParseMode.HTML)
        ok, detail = await restart_script(uid, slot)
        await msg.edit_text("✅ <b>Restarted</b>" if ok else f"❌ <b>Restart failed</b>\n\n{detail}", parse_mode=ParseMode.HTML)
        return
    if data.startswith("delete:") or data.startswith("logout:"):
        slot = int(data.split(":", 1)[1])
        await stop_script(uid, slot)
        remove_script(uid, slot)
        shutil.rmtree(script_root(uid, slot), ignore_errors=True)
        await q.message.edit_text(f"🚪 <b>Script #{slot+1} removed.</b>", parse_mode=ParseMode.HTML)
        return

# ---------------------------------------------------------------------------
# EXTRA USER FUNCTIONS
# ---------------------------------------------------------------------------

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    await update.message.reply_text(
        f"{TOP}\n│ ✦ <b>HOSTER HELP</b> ✦ │\n{BOT}\n\n"
        f"📤 <code>/host</code> — upload a Python script\n"
        f"📊 <code>/status</code> — detailed live status\n"
        f"🔄 <code>/restart</code> — restart a script\n"
        f"🚪 <code>/logout</code> — stop/remove a script\n"
        f"🎁 <code>/referral</code> — referral reward\n"
        f"👑 <code>/premium</code> — plan details\n"
        f"🔐 <code>/setapi API_ID API_HASH</code> — save your API profile\n\n"
        f"Your upload is executed as a separate process with its own working directory.",
        parse_mode=ParseMode.HTML,
    )

async def api_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    api = get_api(uid)
    if not api:
        await update.message.reply_text("🔐 <b>API profile:</b> Not configured.", parse_mode=ParseMode.HTML)
        return
    masked = esc(api["api_hash"][:4] + "••••••••" + api["api_hash"][-4:])
    await update.message.reply_text(
        f"🔐 <b>YOUR API PROFILE</b>\n\n"
        f"🆔 API ID: <code>{api['api_id']}</code>\n"
        f"🔑 API Hash: <code>{masked}</code>\n"
        f"✅ Stored privately for your account.",
        parse_mode=ParseMode.HTML,
    )


async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    items = get_scripts(uid)
    if not items:
        await update.message.reply_text("📭 No scripts.")
        return
    slot = None
    if context.args and context.args[0].isdigit():
        slot = int(context.args[0])
    if slot is None:
        await update.message.reply_text("Usage: <code>/logs 1</code>", parse_mode=ParseMode.HTML)
        return
    item = find_script(uid, slot - 1)
    if not item:
        await update.message.reply_text("❌ Script not found.")
        return
    lp = log_file(uid, slot - 1)
    if not lp.exists():
        await update.message.reply_text("📭 No runtime log yet.")
        return
    data = lp.read_text(encoding="utf-8", errors="replace")
    tail = data[-3500:]
    await update.message.reply_text(
        f"📜 <b>LOGS — #{slot}</b>\n\n<pre>{esc(tail)}</pre>",
        parse_mode=ParseMode.HTML,
    )
# ---------------------------------------------------------------------------
# ADMIN
# ---------------------------------------------------------------------------

async def setwelcomevideo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    reply = update.message.reply_to_message
    if not reply or not reply.video:
        await update.message.reply_text("Reply to a video with /setwelcomevideo")
        return
    JSONStore.save(WELCOME_FILE, {"file_id": reply.video.file_id})
    await update.message.reply_text("✅ Welcome video saved.")


async def remove_welcomevideo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    try:
        WELCOME_FILE.unlink()
    except FileNotFoundError:
        pass
    await update.message.reply_text("✅ Welcome video removed.")


async def admin_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /premium_user <uid>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user id.")
        return
    save_meta(uid, {"premium": True, "premium_reason": "admin", "premium_at": int(time.time())})
    await update.message.reply_text(f"✅ Premium enabled for {uid}.")


async def admin_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /revoke_premium <uid>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user id.")
        return
    meta = get_meta(uid)
    meta["premium"] = False
    meta["premium_reason"] = "revoked"
    save_meta(uid, meta)
    await update.message.reply_text(f"✅ Premium revoked for {uid}.")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    text = " ".join(context.args).strip()
    if not text and update.message.reply_to_message:
        await update.message.reply_text("For media broadcast, reply handling can be added separately. Text broadcast currently active.")
        return
    if not text:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    sent = 0
    for uid in all_user_ids():
        try:
            await context.bot.send_message(uid, text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            continue
    await update.message.reply_text(f"✅ Broadcast sent to {sent} users.")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    users = all_user_ids()
    scripts = sum(len(get_scripts(uid)) for uid in users)
    prem = sum(1 for uid in users if is_premium(uid))
    await update.message.reply_text(
        f"📊 <b>HOSTER STATS</b>\n\nUsers: <b>{len(users)}</b>\nScripts: <b>{scripts}</b>\nPremium: <b>{prem}</b>\nRunning: <b>{running_count()}</b>",
        parse_mode=ParseMode.HTML,
    )

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

async def post_init(app: Application) -> None:
    app.create_task(health_loop(app))
    await app.bot.set_my_commands([
        ("start", "Open hoster"),
        ("host", "Upload your userbot script"),
        ("status", "View hosted scripts"),
        ("restart", "Restart a script"),
        ("logout", "Stop and remove a script"),
        ("referral", "Refer & earn Premium"),
        ("premium", "Premium information"),
        ("setapi", "Set your API profile"),
    ])


def build_app() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")
    if not OWNER_ID:
        raise RuntimeError("OWNER_ID is not configured")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    upload_conv = ConversationHandler(
        entry_points=[CommandHandler("host", begin_upload)],
        states={
            1: [MessageHandler(filters.Document.ALL, receive_script)],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_id)],
            3: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_hash)],
        },
        fallbacks=[CommandHandler("cancel", cancel_upload)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("referral", referral))
    app.add_handler(CommandHandler("premium", premium))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("apistatus", api_status_command))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(CommandHandler("setapi", setapi))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("logout", logout_command))
    app.add_handler(upload_conv)
    app.add_handler(CommandHandler("setwelcomevideo", setwelcomevideo))
    app.add_handler(CommandHandler("removewelcomevideo", remove_welcomevideo))
    app.add_handler(CommandHandler("premium_user", admin_premium))
    app.add_handler(CommandHandler("revoke_premium", admin_revoke))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CallbackQueryHandler(callbacks))
    return app


def main() -> None:
    app = build_app()
    log.info("SID Manual Userbot Hoster V8 started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
