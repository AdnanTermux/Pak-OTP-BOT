
import logging, asyncio, re, aiohttp, json, os, random, html, sqlite3
import hashlib, ssl, websockets, subprocess, shutil, uuid
from urllib.parse import urljoin, urlparse
import phonenumbers
from phonenumbers import geocoder
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CopyTextButton
from telegram.ext import (ApplicationBuilder, ContextTypes, CommandHandler,
                           MessageHandler, CallbackQueryHandler, filters)
from telegram.error import BadRequest as TelegramBadRequest, Forbidden as TelegramForbidden, TimedOut as TelegramTimedOut, NetworkError as TelegramNetworkError
from sqlalchemy import text as stext, select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

import database as db
from utils import to_bold
import bot_manager as bm

# ═══════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════
class EmojiFormatter(logging.Formatter):
    """Adds emoji prefixes + clean timestamps to console output."""
    EMOJIS = {
        logging.DEBUG:    "🔍 DEBUG",
        logging.INFO:     "📌 INFO ",
        logging.WARNING:  "⚠️  WARN ",
        logging.ERROR:    "❌ ERROR",
        logging.CRITICAL: "🔥 CRIT ",
    }
    def format(self, record):
        label = self.EMOJIS.get(record.levelno, "❓ ?????")
        ts    = self.formatTime(record, "%H:%M:%S")
        msg   = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return f"{ts} | {label} | {msg}"

# ── File handler keeps plain text (easier to grep) ──────────────
_file_fmt    = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s")
_file_h      = logging.FileHandler("bot.log", encoding="utf-8")
_file_h.setFormatter(_file_fmt)

# ── Console handler gets emoji-rich output ───────────────────────
_console_h   = logging.StreamHandler()
_console_h.setFormatter(EmojiFormatter())

logging.basicConfig(level=logging.INFO, handlers=[_file_h, _console_h])

# Silence noisy third-party loggers
for _noisy in ("httpx","httpcore","telegram.ext","apscheduler","aiohttp"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#  SAFE TELEGRAM HELPERS
# ═══════════════════════════════════════════════════════════
async def safe_edit(query, text: str, reply_markup=None, parse_mode="HTML"):
    """
    Wrapper for query.edit_message_text that silently swallows
    'Message is not modified' (content unchanged) and retries
    once on timeout / network error.
    """
    kwargs = dict(text=text, parse_mode=parse_mode)
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    for attempt in range(2):
        try:
            await query.edit_message_text(**kwargs)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return   # already showing correct content — not an error
            if attempt == 0:
                await asyncio.sleep(0.5); continue
            raise
        except (TelegramTimedOut, TelegramNetworkError):
            if attempt == 0:
                await asyncio.sleep(1); continue
            return   # give up gracefully on second timeout


# ═══════════════════════════════════════════════════════════
#  DEFAULT CONSTANTS  (overridden by config.json)
# ═══════════════════════════════════════════════════════════
BOT_TOKEN         = "" # PUT HERE BOT TOKEN
BOT_USERNAME      = "" # PUT HERE BOT USERNAME WITHOUT @
INITIAL_ADMIN_IDS = [] # PUT HERE ADMIN IDS SEPARTED BY COMMAS
SUPPORT_USER      = "" # PUT HERE SUPPORT USERNAME WITH @
DEVELOPER         = "@NONEXPERTCODER"
OTP_GROUP_LINK    = "" # PUT HERE OTP GROUP LINK
GET_NUMBER_URL    = "" # PUT HERE NUMBER CHANNEL LINK
NUMBER_BOT_LINK   = "" # PUT HERE NUMBER BOT LINK
CHANNEL_LINK      = "" # PUT HERE CHANNEL LINK
CHANGE_COOLDOWN_S = 5
COUNTRIES_FILE    = "countries.json"
DEX_FILE          = "dex.txt"
SEEN_DB_FILE      = "sms_database_np.db"
CONFIG_FILE       = "config.json"
OTP_STORE_FILE    = "otp_store.json"
LOG_FILE          = "bot.log"
API_FETCH_INTERVAL= 1   # 1s — optimised for 48-core server
MSG_AGE_LIMIT_MIN = 120
API_MAX_RECORDS   = 200  # max 200 — server limit
IS_CHILD_BOT      = False
DEFAULT_ASSIGN_LIMIT = 4

PERMISSIONS = {
    "manage_panels": "🔌 Manage Panels",
    "manage_files":  "📂 Manage Files",
    "manage_logs":   "📋 Manage Log Groups",
    "broadcast":     "📢 Send Broadcasts",
    "view_stats":    "📊 View Statistics",
    "manage_admins": "👥 Manage Admins",
}

DEFAULT_IVAS_URI = (
    "wss://ivas.tempnum.qzz.io:2087/socket.io/?token=eyJpdiI6IjI4c3JCUVNJa"
    "zRWRkp5M3lHL0pLeEE9PSIsInZhbHVlIjoiU09YK0llL1llc3ZIVzhia0sxTjZYTnZLN"
    "0dFOE1QSEZqMk1GVE1EUDhOVTR2R2tqbGUrVlBNSGJmQ1Q3WjhoUllZWlFTYUlwSmI0"
    "VUZRSHYwUFNqZ1VEY0U1RzFFcmo0MHJlU1BHcHNTYitpK1BKUDRkSGU5NlRoUnB4aThE"
    "TGFwemU2NTRGeUpoczRlNEFBT2tIejlrdWFSWFM1QjlBRURlOXIzbkNaWEJpcTlNV0ZD"
    "KzNrSFVLMEhEem5wUUZlS1NDRmtUVlhX2pxUGZqT2poMWs4UW1JU1d4UmFoTC9LVVHRL"
    "3Zrc00yVkZLcXRzYU9RNkh3dUl1eGNQSWhpZG12aGttMU5qSVovVm9KcytYa0hHb1Rod"
    "TFzYUt0bEdtQ3pVN0pUQkdZR0JGL2hGV21IanJqQXBsSisrSjlMdCtzbUc2dWhVdGdWZz"
    "FPWVgwVDJpSE1jak9LTVl1Vmh4bGNVZlgrT3BWT0g5YldmYVdVWVA1S0crbk9GOTNERWF"
    "1NG5kd0k3YkdXWXBMUk56QVVNNWtFclNoYWdYVXMrQ0NkSEdwamQrZUVNOGJybTdzTmV3"
    "TlpmakU1TmxxdmZIMkVOVGYwc3Y5NTdTeE9Xdm5Jc1FhU092dmE1ZzA4aktXOCtCMTdOb"
    "FgvSmliQlkwYjdmOFkzeHJQdzlOb252NWFHWnR5L3JSQnNDK3k1L0R6U2ZTZStWeDhOQz"
    "dLL01sZDVmamtNZzIrT2NvPSIsIm1hYyI6IjY2MWE1OTcxNWQ5YzU3OTUxZjgwZjA3MW"
    "U2OTUzYmUxMDI4NmQ3Y2ZmOTBkMmRkNTU1MmM0Zjc5ODAyNTRmODAiLCJ0YWciOiIifQ"
    "%3D%3D&user=9704f70096e34e36454e6ad92265698b&EIO=4&transport=websocket"
)

# ═══════════════════════════════════════════════════════════
#  CONFIG LOAD  — reads config.json and overrides constants
# ═══════════════════════════════════════════════════════════
def load_config():
    global DEFAULT_ASSIGN_LIMIT, IS_CHILD_BOT, BOT_TOKEN, BOT_USERNAME
    global INITIAL_ADMIN_IDS, SUPPORT_USER, DEVELOPER
    global OTP_GROUP_LINK, GET_NUMBER_URL, NUMBER_BOT_LINK, CHANNEL_LINK, DEVELOPER
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE) as f:
            c = json.load(f)
        DEFAULT_ASSIGN_LIMIT = c.get("default_limit", DEFAULT_ASSIGN_LIMIT)
        IS_CHILD_BOT          = c.get("IS_CHILD_BOT",   False)
        if c.get("BOT_TOKEN"):       BOT_TOKEN         = c["BOT_TOKEN"]
        if c.get("BOT_USERNAME"):    BOT_USERNAME      = c["BOT_USERNAME"].lstrip("@")
        if c.get("ADMIN_IDS"):       INITIAL_ADMIN_IDS = c["ADMIN_IDS"]
        if c.get("SUPPORT_USER"):    SUPPORT_USER      = c["SUPPORT_USER"]
        if c.get("DEVELOPER"):       DEVELOPER         = c["DEVELOPER"]
        if c.get("OTP_GROUP_LINK"):  OTP_GROUP_LINK    = c["OTP_GROUP_LINK"]
        if c.get("GET_NUMBER_URL"):  GET_NUMBER_URL    = c["GET_NUMBER_URL"]
        if c.get("NUMBER_BOT_LINK"): NUMBER_BOT_LINK   = c["NUMBER_BOT_LINK"]
        if c.get("CHANNEL_LINK"):    CHANNEL_LINK      = c["CHANNEL_LINK"]
        global OTP_GUI_THEME
        OTP_GUI_THEME = int(c.get("OTP_GUI_THEME", 0))
    except Exception as e:
        print(f"Config load error: {e}")

def save_config_key(key: str, value):
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f: cfg = json.load(f)
        except Exception: pass
    cfg[key] = value
    with open(CONFIG_FILE,"w") as f: json.dump(cfg, f, indent=2)

load_config()
if not os.path.exists(LOG_FILE):
    open(LOG_FILE,"a").close()

# ═══════════════════════════════════════════════════════════
#  OTP STORE
# ═══════════════════════════════════════════════════════════
def load_otp_store() -> dict:
    if os.path.exists(OTP_STORE_FILE):
        try:
            with open(OTP_STORE_FILE) as f: return json.load(f)
        except Exception: pass
    return {}

def save_otp_store(store: dict):
    try:
        path = os.path.abspath(OTP_STORE_FILE)
        with open(path, "w") as f:
            json.dump(store, f, indent=2)
    except Exception as e:
        logger.error(f"❌ OTP store save failed ({OTP_STORE_FILE}): {e}", exc_info=True)

def append_otp(num_raw: str, otp_code: str):
    """Thread-safe single OTP save — always writes immediately."""
    try:
        store = load_otp_store()
        store[num_raw] = otp_code
        # Keep max 2000 entries — trim oldest if over limit
        if len(store) > 2000:
            keys = list(store.keys())
            for k in keys[:-2000]: del store[k]
        save_otp_store(store)
        logger.info(f"💾 OTP saved: {mask_number(num_raw)} → {otp_code}")
    except Exception as e:
        logger.error(f"❌ append_otp failed: {e}")

# ═══════════════════════════════════════════════════════════
#  SEEN-SMS  (deduplication)
# ═══════════════════════════════════════════════════════════
def init_seen_db() -> set:
    try:
        conn = sqlite3.connect(SEEN_DB_FILE)
        conn.execute("CREATE TABLE IF NOT EXISTS reported_sms (hash TEXT PRIMARY KEY)")
        conn.commit()
        rows = conn.execute("SELECT hash FROM reported_sms").fetchall()
        conn.close()
        logger.info(f"Loaded {len(rows)} seen-SMS hashes.")
        return {r[0] for r in rows}
    except Exception as e:
        logger.error(f"Seen DB: {e}")
        return set()

def save_seen_hash(h: str):
    try:
        conn = sqlite3.connect(SEEN_DB_FILE)
        conn.execute("INSERT OR IGNORE INTO reported_sms (hash) VALUES (?)", (h,))
        conn.commit(); conn.close()
    except Exception: pass

TEST_NUMBERS = [f"1202555010{i}" for i in range(10)]

# ═══════════════════════════════════════════════════════════
#  OTP EXTRACTION
# ═══════════════════════════════════════════════════════════
# ── 100 OTP extraction patterns ──────────────────────────────────
# Ordered: specific keyword patterns first (most reliable),
# generic numeric patterns last (catch-all).
_OTP_RE = [
    # ── Explicit keyword patterns ─────────────────────────────────
    r"(?:your|the)\s+(?:OTP|one.?time.?pass(?:word|code)?)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:OTP|one.?time.?pass(?:word|code)?)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:verification|confirm(?:ation)?)\s*(?:code|pin|OTP)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:auth(?:entication)?|security|access)\s*code\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:login|sign.?in|sign.?up)\s*(?:code|pin|OTP)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:activation|account)\s*(?:code|pin)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:reset|recovery|2fa|two.?factor)\s*(?:code|pin|OTP)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:registration|signup)\s*(?:code|pin)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"code\s*(?:is|:)\s*([0-9]{4,8})",
    r"pin\s*(?:is|:)\s*([0-9]{4,8})",
    # ── Service-specific patterns ─────────────────────────────────
    r"(?:WhatsApp|WA)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Telegram|TG)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Facebook|FB)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Instagram|IG)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Twitter|TW|X)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:TikTok|TT)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Snapchat|SC)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Google|GG|Gmail|GM)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Microsoft|MS|Outlook)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Apple|iCloud)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Amazon|AM)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:PayPal|PP)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Uber|UB|Lyft|LF)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Discord|DC)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Viber|VB|LINE|LN)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:WeChat|WC|KakaoTalk)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Netflix|NF|Spotify)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:LinkedIn|LI)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Steam|ST|Twitch|TC)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Binance|BN|Coinbase|CB|Crypto)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Shopify|SH|Stripe)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Signal|Skype|Zoom)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Tinder|Bumble|Hinge)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Airbnb|Booking)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Careem|Swvl|Rapido)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Jazz|Telenor|Zong|Ufone|PTCL)\s*(?:code|OTP|PIN)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Easypaisa|JazzCash|HBL|MCB|UBL|Meezan|Allied)\s*(?:code|OTP|PIN)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:Bykea|Daraz|foodpanda|Cheetay)\s*(?:code|OTP)?\s*(?:is|:)?\s*([0-9]{4,8})",
    # ── Pattern phrases used in SMS bodies ───────────────────────
    r"use\s+(?:this\s+)?(?:code|OTP|pin)\s*(?:to|:)?\s*([0-9]{4,8})",
    r"enter\s+(?:this\s+)?(?:code|OTP|pin)\s*(?:to|:)?\s*([0-9]{4,8})",
    r"your\s+code\s+([0-9]{4,8})",
    r"code\s+([0-9]{4,8})\s+(?:is|will)",
    r"([0-9]{4,8})\s+is\s+your\s+(?:OTP|code|pin|password)",
    r"([0-9]{4,8})\s+(?:is\s+)?(?:the|your)\s+(?:verification|auth|login)\s+code",
    r"([0-9]{4,8})\s+(?:is\s+)?(?:the|your)\s+one.?time",
    r"(?:do\s+not\s+share|never\s+share).*?([0-9]{4,8})",
    r"([0-9]{4,8}).*?(?:do\s+not\s+share|never\s+share)",
    r"(?:expires?\s+in|valid\s+for).*?([0-9]{4,8})",
    r"([0-9]{4,8}).*?(?:expires?|valid)",
    r"(?:temporary|temp)\s+(?:code|password|pin)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"secret\s*(?:code|key|pin)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:access|unlock)\s*(?:code|key|pin)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:confirm|verify)\s+(?:with|using)?\s*([0-9]{4,8})",
    r"msverify[\s:/]*([0-9]{4,8})",
    r"msauth[\s:/]*([0-9]{4,8})",
    r"G-([0-9]{6})",                     # Google format
    r"FB-([0-9]{5,8})",                  # Facebook format
    r"([0-9]{6})\s+(?:is|are)\s+your",
    r"(?:send|sent)\s+you\s+(?:a\s+)?(?:code|OTP)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:SMS|text)\s+(?:code|OTP)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"phone\s+(?:verification|confirm)\s*(?:code)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"mobile\s+(?:verification|confirm)\s*(?:code)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:account|profile)\s+(?:verification|confirm)\s*(?:code)?\s*(?:is|:)?\s*([0-9]{4,8})",
    r"number\s+(?:is|:)\s*([0-9]{4,8})",
    r"(?:private|secure)\s*(?:code|key)\s*(?:is|:)?\s*([0-9]{4,8})",
    r"(?:unique|special)\s*(?:code|pin|key)\s*(?:is|:)?\s*([0-9]{4,8})",
    # ── Numeric separators (spaces/dashes in OTP) ─────────────────
    r"\b([0-9]{3}[-\s][0-9]{3})\b",
    r"\b([0-9]{4}[-\s][0-9]{4})\b",
    r"\b([0-9]{3}[-\s][0-9]{3}[-\s][0-9]{3})\b",
    r"\b([0-9]{2}[-\s][0-9]{2}[-\s][0-9]{2})\b",
    # ── Locale/language variants ──────────────────────────────────
    r"(?:رمز|کد|کود)\s*(?:تأیید|التحقق|OTP)?\s*(?:است|:)?\s*([0-9]{4,8})",   # Arabic/Farsi
    r"(?:کوڈ|رمز)\s*(?:ہے|:)?\s*([0-9]{4,8})",                                # Urdu
    r"(?:código|code|clave)\s*(?:de\s+verificación|OTP)?\s*(?:es|:)?\s*([0-9]{4,8})",  # Spanish
    r"(?:код|OTP)\s*(?:подтверждения)?\s*(?:[:—])\s*([0-9]{4,8})",             # Russian
    r"(?:驗證碼|验证码|코드)\s*(?:是|:)?\s*([0-9]{4,8})",                        # Chinese/Korean
    # ── Catch-all numeric patterns (last resort) ──────────────────
    r"\b([0-9]{6})\b",   # 6-digit (most common OTP)
    r"\b([0-9]{5})\b",   # 5-digit
    r"\b([0-9]{8})\b",   # 8-digit
    r"\b([0-9]{7})\b",   # 7-digit
    r"\b([0-9]{4})\b",   # 4-digit PIN
]

def extract_otp_regex(text: str) -> Optional[str]:
    """Extract OTP from SMS body using 100 regex patterns."""
    if not text: return None
    for pat in _OTP_RE:
        try:
            m = re.search(pat, text, re.IGNORECASE | re.UNICODE)
            if m:
                raw = m.group(1).replace(" ", "").replace("-", "")
                if raw.isdigit() and 4 <= len(raw) <= 9:
                    return raw
        except re.error:
            continue
    return None

# ═══════════════════════════════════════════════════════════
#  PHONE / COUNTRY HELPERS
# ═══════════════════════════════════════════════════════════
COUNTRY_DATA: List[dict] = []

def load_countries():
    global COUNTRY_DATA
    if os.path.exists(COUNTRIES_FILE):
        try:
            with open(COUNTRIES_FILE, encoding="utf-8") as f:
                COUNTRY_DATA = json.load(f)
            logger.info(f"Loaded {len(COUNTRY_DATA)} countries.")
        except Exception as e:
            logger.error(f"Countries: {e}")

load_countries()

def get_country_info(num: str):
    try:
        n = num if num.startswith("+") else "+" + num
        p = phonenumbers.parse(n)
        country = geocoder.description_for_number(p, "en")
        region  = phonenumbers.region_code_for_number(p)
        flag = "🌍"
        if region and len(region) == 2:
            b = 127462 - ord("A")
            flag = chr(b+ord(region[0])) + chr(b+ord(region[1]))
        return country or "Unknown", flag, region or ""
    except Exception: return "Unknown", "🌍", ""

def get_country_code(num: str) -> str:
    try:
        n = num if num.startswith("+") else "+" + num
        return f"+{phonenumbers.parse(n).country_code}"
    except Exception: return ""

def get_last5(num: str) -> str:
    d = re.sub(r"[^0-9]","",num)
    return d[-5:] if len(d) >= 5 else d

def mask_number(num: str) -> str:
    c = num.replace("+","").replace(" ","")
    return f"{c[:4]}-SIGMA-{c[-4:]}" if len(c) >= 8 else num

def detect_country_from_numbers(nums: list):
    if not COUNTRY_DATA or not nums: return "Unknown", "🌍"
    sc = sorted(COUNTRY_DATA, key=lambda x: len(x["dial_code"]), reverse=True)
    votes = {}
    for raw in nums[:50]:
        chk = "+" + re.sub(r"[^0-9]","",str(raw))
        for c in sc:
            if chk.startswith(c["dial_code"]):
                k = (c["name"], c["flag"])
                votes[k] = votes.get(k,0) + 1
                break
    return max(votes, key=votes.get) if votes else ("Unknown","🌍")

_SVC_MAP = {
    "whatsapp":"WS","telegram":"TG","facebook":"FB","instagram":"IG","twitter":"TW",
    "tiktok":"TT","snapchat":"SC","google":"GG","gmail":"GM","microsoft":"MS",
    "amazon":"AM","apple":"AP","uber":"UB","lyft":"LF","paypal":"PP","viber":"VB",
    "line":"LN","wechat":"WC","yahoo":"YH","netflix":"NF","discord":"DC",
    "linkedin":"LI","shopify":"SH","binance":"BN","coinbase":"CB","steam":"ST","twitch":"TC",
}

def get_service_short(svc: str) -> str:
    s = svc.lower().strip()
    for k,v in _SVC_MAP.items():
        if k in s: return v
    clean = re.sub(r"[^a-zA-Z]","",svc)
    return clean[:2].upper() if clean else "OT"

def get_message_body(rec: list) -> Optional[str]:
    noise = {"0","0.00","€","$","null","None",""}
    for idx in [4,5]:
        if len(rec) > idx:
            v = str(rec[idx]).strip()
            if v and v not in noise and len(v) > 1: return v
    return None

def parse_panel_dt(dt_str: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S","%Y/%m/%d %H:%M:%S","%d-%m-%Y %H:%M:%S"):
        try: return datetime.strptime(dt_str.strip(), fmt)
        except Exception: pass
    return None

def pbar(cur: int, total: int, length: int = 12) -> str:
    if total <= 0: return f"[{chr(9617)*length}] 0/0"
    f = int(length * cur / total)
    return f"[{chr(9608)*f}{chr(9617)*(length-f)}] {cur}/{total}"

D = "┄" * 22

# ── OTP GUI Theme ─────────────────────────────────────────────────
# 8 selectable themes (0-7). 5 standard + 3 premium. Stored in config.json.
# Admin selects from Admin → Settings → OTP GUI Theme.
OTP_GUI_THEME = 0   # default: Classic

def _get_bot_tag() -> str:
    """Returns @PAKOTPBOT from NUMBER_BOT_LINK or falls back to BOT_USERNAME."""
    nb = NUMBER_BOT_LINK or GET_NUMBER_URL or ""
    if "t.me/" in nb:
        u = nb.rstrip("/").split("t.me/")[-1].lstrip("@")
        if u: return f"@{u}"
    return f"@{BOT_USERNAME}" if BOT_USERNAME else "@PAKOTPBOT"

def _num_display(dial: str, last5: str) -> str:
    bt = _get_bot_tag().lstrip("@")[:9].upper()
    return f"{dial}•{bt}•{last5}"

def build_otp_msg(header: str, count_badge: str, clean: str,
                  msg_body: str, svc: str, panel_name: str,
                  flag: str, region: str, dial: str, last5: str,
                  for_group: bool) -> str:
    """
    Build the OTP message text using the currently selected GUI theme.
    for_group=True  → compact log-group version
    for_group=False → full DM version
    """
    bot_tag = _get_bot_tag()
    nd      = _num_display(dial, last5)
    body260 = html.escape(msg_body[:260])
    body180 = html.escape(msg_body[:180])
    pname   = html.escape(panel_name)

    t = OTP_GUI_THEME % 8   # clamp to 0-7

    if t == 0:
        # ── Theme 0: PREMIUM DARK (bold lines + fire) ────────────────
        if for_group:
            if clean:
                return (f"{flag} <b>#{svc}</b>  📱  <code>{nd}</code>  🔥\n\n"
                        f"<b>{header}</b>  ·  <code>{clean}</code>\n\n"
                        f"<i>©By {html.escape(bot_tag)}</i>")
            return (f"{flag} <b>#{svc}</b>  📱  <code>{nd}</code>\n\n"
                    f"<b>{header}</b>\n"
                    f"💬 <i>{body180}</i>\n\n"
                    f"<i>©By {html.escape(bot_tag)}</i>")
        else:
            if clean:
                return (f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
                        f"  {flag} <b>#{svc}</b>  <code>{nd}</code>  🔥\n"
                        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                        f"<b>{header}</b>  ·  {count_badge}\n\n"
                        f"🔑 <b>OTP:</b>  <code>{clean}</code>\n\n"
                        f"💬 <i>{body260}</i>\n\n"
                        f"<i>©By {html.escape(bot_tag)}</i>")
            return (f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
                    f"  {flag} <b>#{svc}</b>  <code>{nd}</code>\n"
                    f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                    f"<b>{header}</b>\n\n"
                    f"💬 <i>{body260}</i>\n\n"
                    f"<i>©By {html.escape(bot_tag)}</i>")

    elif t == 1:
        # ── Theme 1: NEON / ELECTRIC (electric emojis) ───────────────
        if for_group:
            if clean:
                return (f"⚡️ {flag} <b>#{svc}</b>  <code>{nd}</code> ⚡️\n"
                        f"🔐 <code>{clean}</code>  {count_badge}\n"
                        f"🤖 <i>{html.escape(bot_tag)}</i>")
            return (f"📡 {flag} <b>#{svc}</b>  <code>{nd}</code>\n"
                    f"💬 <i>{body180}</i>\n"
                    f"🤖 <i>{html.escape(bot_tag)}</i>")
        else:
            if clean:
                return (f"⚡️⚡️ <b>{header}</b> ⚡️⚡️\n"
                        f"{'─'*24}\n"
                        f"🌍 {flag} <b>#{region}</b>  📱 <code>{nd}</code>\n"
                        f"🎯 <b>Service:</b> #{svc}  🔌 <code>{pname}</code>\n"
                        f"{'─'*24}\n"
                        f"🔐 <b>OTP CODE</b>\n"
                        f"💎 <code>{clean}</code> 💎\n"
                        f"{'─'*24}\n"
                        f"💬 <i>{body260}</i>\n"
                        f"🤖 <i>{html.escape(bot_tag)}</i>")
            return (f"📡 <b>{header}</b>\n"
                    f"{'─'*24}\n"
                    f"🌍 {flag} <b>#{region}</b>  📱 <code>{nd}</code>\n"
                    f"💬 <i>{body260}</i>\n"
                    f"🤖 <i>{html.escape(bot_tag)}</i>")

    elif t == 2:
        # ── Theme 2: SIGMA CLASSIC (original Sigma branding) ─────────
        if for_group:
            if clean:
                return (f"🔥 {flag}#{region}  📱 <code>{nd}</code>\n"
                        f"🔑 <code>{clean}</code>  📡#{svc}\n"
                        f"©️ {html.escape(bot_tag)}")
            return (f"📩 {flag}#{region}  📱 <code>{nd}</code>\n"
                    f"💬 <i>{body180}</i>\n"
                    f"©️ {html.escape(bot_tag)}")
        else:
            if clean:
                return (f"🔥 <b>{header}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📱 <code>{nd}</code>  {flag}\n"
                        f"🌍 #{region}  📡 #{svc}\n"
                        f"🔌 <code>{pname}</code>\n\n"
                        f"╔══ 🔑 OTP ══╗\n"
                        f"  <code>{clean}</code>\n"
                        f"╚════════════╝\n\n"
                        f"💬 <i>{body260}</i>\n"
                        f"©️ <b>{html.escape(bot_tag)}</b>")
            return (f"📩 <b>{header}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📱 <code>{nd}</code>  {flag}  #{region}\n"
                    f"💬 <i>{body260}</i>\n"
                    f"©️ <b>{html.escape(bot_tag)}</b>")

    elif t == 3:
        # ── Theme 3: MINIMAL (clean, no decorations) ─────────────────
        if for_group:
            if clean:
                return (f"{flag} {nd}  ·  <code>{clean}</code>\n"
                        f"<i>{html.escape(bot_tag)}</i>")
            return (f"{flag} {nd}\n"
                    f"<i>{body180}</i>\n"
                    f"<i>{html.escape(bot_tag)}</i>")
        else:
            if clean:
                return (f"{flag} <b>#{svc}</b>  <code>{nd}</code>\n"
                        f"<b>{header}</b>\n\n"
                        f"<code>{clean}</code>\n\n"
                        f"<i>{body260}</i>\n"
                        f"<i>{html.escape(bot_tag)}</i>")
            return (f"{flag} <b>#{svc}</b>  <code>{nd}</code>\n"
                    f"<b>{header}</b>\n\n"
                    f"<i>{body260}</i>\n"
                    f"<i>{html.escape(bot_tag)}</i>")

    elif t == 4:
        # ── Theme 4: ROYAL GOLD ──────────────────────────────────────
        if for_group:
            if clean:
                return (f"👑 {flag} <b>#{svc}</b>  <code>{nd}</code> 🌟\n"
                        f"🔑 <code>{clean}</code>\n"
                        f"✨ <i>{html.escape(bot_tag)}</i> ✨")
            return (f"💫 {flag} <b>#{svc}</b>  <code>{nd}</code>\n"
                    f"💬 <i>{body180}</i>\n"
                    f"✨ <i>{html.escape(bot_tag)}</i> ✨")
        else:
            if clean:
                return (f"👑 <b>━━ {header} ━━</b> 👑\n\n"
                        f"🌟 {flag} <b>#{svc}</b>  <code>{nd}</code> 🌟\n"
                        f"🏆 {count_badge}\n\n"
                        f"💰 <b>OTP CODE</b>\n"
                        f"🔑 <code>{clean}</code>\n\n"
                        f"💬 <i>{body260}</i>\n\n"
                        f"✨ <i>{html.escape(bot_tag)}</i> ✨")
            return (f"💫 <b>{header}</b>\n\n"
                    f"🌟 {flag} <b>#{svc}</b>  <code>{nd}</code>\n"
                    f"💬 <i>{body260}</i>\n\n"
                    f"✨ <i>{html.escape(bot_tag)}</i> ✨")

    elif t == 5:
        # ── Theme 5: TEMPNUM STYLE (structured info boxes) ───────────
        # Matches Image 1: each field on its own labelled row
        from datetime import datetime as _dt
        now_full = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        e_fire  = "<tg-emoji emoji-id=\"5773906538459573336\">🔥</tg-emoji>"
        e_key   = "<tg-emoji emoji-id=\"5472211234521076011\">🔑</tg-emoji>"
        e_time  = "<tg-emoji emoji-id=\"5462884015492509702\">⏰</tg-emoji>"
        e_globe = "<tg-emoji emoji-id=\"5467477376523975543\">🌏</tg-emoji>"
        e_phone = "<tg-emoji emoji-id=\"5472308992514464048\">📱</tg-emoji>"
        e_svc   = "<tg-emoji emoji-id=\"5471730021695673489\">💬</tg-emoji>"
        e_msg   = "<tg-emoji emoji-id=\"5435885010490511155\">💌</tg-emoji>"
        if for_group:
            if clean:
                return (f"{e_fire} {flag} <b>#{svc} OTP!</b>\n\n"
                        f"{e_phone} <code>+{nd.replace('•','-',1)}</code>\n"
                        f"{e_key} <b>OTP:</b> <code>{clean}</code>\n\n"
                        f"<i>©By {html.escape(bot_tag)}</i>")
            return (f"📩 {flag} <b>#{svc}</b>  <code>{nd}</code>\n"
                    f"<i>{body180}</i>\n<i>©By {html.escape(bot_tag)}</i>")
        else:
            if clean:
                return (f"{e_fire} <b>{flag} {region} {svc} OTP Received!</b> ✨\n\n"
                        f"┌─────────────────────────┐\n"
                        f"│ {e_time} <b>Time:</b> <code>{now_full}</code>\n"
                        f"│ {e_globe} <b>Country:</b> {region} {flag}\n"
                        f"│ {e_svc} <b>Service:</b> #{svc}\n"
                        f"│ {e_phone} <b>Number:</b> <code>+{nd.replace('•','-',1)}</code>\n"
                        f"│ {e_key} <b>OTP:</b> <code>{clean}</code>\n"
                        f"│ {e_msg} <b>Full Message:</b>\n"
                        f"│ <i>{body260}</i>\n"
                        f"└─────────────────────────┘\n\n"
                        f"<i>©By {html.escape(bot_tag)}</i>")
            return (f"📩 <b>{header}</b> {flag}\n\n"
                    f"│ {e_time} <code>{now_full}</code>\n"
                    f"│ {e_phone} <code>+{nd.replace('•','-',1)}</code>\n"
                    f"│ {e_msg} <i>{body260}</i>\n\n"
                    f"<i>©By {html.escape(bot_tag)}</i>")

    elif t == 6:
        # ── Theme 6: JACK-X STYLE ─────────────────────────────────────
        # Matches Image 2: →  flag #service [GN] dial•BOT•last5  |
        e_fire  = "<tg-emoji emoji-id=\"5773906538459573336\">🔥</tg-emoji>"
        e_key   = "<tg-emoji emoji-id=\"5472211234521076011\">🔑</tg-emoji>"
        e_bolt  = "<tg-emoji emoji-id=\"5461151367559362727\">⚡</tg-emoji>"
        e_gem   = "<tg-emoji emoji-id=\"5471952986970267163\">💎</tg-emoji>"
        vbadge  = f"<tg-emoji emoji-id=\"5368324170671202286\">⭐</tg-emoji> V2 <tg-emoji emoji-id=\"5368324170671202286\">⭐</tg-emoji>"
        if for_group:
            if clean:
                return (f"{vbadge}\n"
                        f"→ {flag} <b>#{svc}</b>  {nd}  {e_fire}\n\n"
                        f"<i>©By {html.escape(bot_tag)}</i>")
            return (f"→ {flag} <b>#{svc}</b>  {nd}\n"
                    f"<i>{body180}</i>\n<i>©By {html.escape(bot_tag)}</i>")
        else:
            if clean:
                return (f"{e_bolt} <b>{html.escape(bot_tag)}</b>  {vbadge}\n"
                        f"→ {flag} <b>#{svc}</b>  [{region.upper()}]  <code>{nd}</code>  {e_fire}\n\n"
                        f"{e_key} <b>{count_badge}</b>\n"
                        f"<code>{clean}</code>\n\n"
                        f"<i>{body260}</i>\n\n"
                        f"{e_gem} <i>©By {html.escape(bot_tag)}</i> {e_gem}")
            return (f"{e_bolt} <b>{html.escape(bot_tag)}</b>\n"
                    f"→ {flag} <b>#{svc}</b>  <code>{nd}</code>\n\n"
                    f"<i>{body260}</i>\n\n"
                    f"{e_gem} <i>©By {html.escape(bot_tag)}</i> {e_gem}")

    else:
        # ── Theme 7: CYBER / MATRIX (premium dark) ───────────────────
        e_skull = "<tg-emoji emoji-id=\"5350934059607329445\">💀</tg-emoji>"
        e_lock  = "<tg-emoji emoji-id=\"5472308992514464048\">🔒</tg-emoji>"
        e_key   = "<tg-emoji emoji-id=\"5472211234521076011\">🔑</tg-emoji>"
        e_fire  = "<tg-emoji emoji-id=\"5773906538459573336\">🔥</tg-emoji>"
        e_bolt  = "<tg-emoji emoji-id=\"5461151367559362727\">⚡</tg-emoji>"
        if for_group:
            if clean:
                return (f"{e_fire} {flag} #{svc}  <code>{nd}</code> {e_bolt}\n"
                        f"{e_key} <code>{clean}</code>\n"
                        f"<i>©By {html.escape(bot_tag)}</i>")
            return (f"{e_bolt} {flag} #{svc}  <code>{nd}</code>\n"
                    f"<i>{body180}</i>\n<i>©By {html.escape(bot_tag)}</i>")
        else:
            if clean:
                return (f"<b>[ {e_bolt} SIGMA FETCHER {e_bolt} ]</b>\n"
                        f"{'─'*26}\n"
                        f"  {flag}  <b>#{svc}</b>  <code>{nd}</code>  {e_fire}\n"
                        f"  {count_badge}\n"
                        f"{'─'*26}\n"
                        f"  {e_key} <b>DECRYPTED CODE</b>\n"
                        f"  <code>{clean}</code>\n"
                        f"{'─'*26}\n"
                        f"  {e_lock} <i>{body260}</i>\n\n"
                        f"  {e_skull} <i>©By {html.escape(bot_tag)}</i> {e_skull}")
            return (f"<b>[ {e_bolt} SIGMA FETCHER {e_bolt} ]</b>\n"
                    f"  {flag}  <b>#{svc}</b>  <code>{nd}</code>\n"
                    f"  {e_lock} <i>{body260}</i>\n\n"
                    f"  <i>©By {html.escape(bot_tag)}</i>")

# ═══════════════════════════════════════════════════════════
#  GLOBAL STATE
# ═══════════════════════════════════════════════════════════
PANELS:               List              = []
IVAS_TASKS:           Dict[str,asyncio.Task] = {}
PROCESSED_MESSAGES:   set               = set()
OTP_SESSION_COUNTS:   Dict[str,int]     = {}
LAST_CHANGE_TIME:     Dict[int,datetime]= {}
CATEGORY_MAP:         Dict[str,str]     = {}
PANEL_ADD_STATES:     Dict[int,dict]    = {}
PANEL_EDIT_STATES:    Dict[int,dict]    = {}
AWAITING_ADMIN_ID:    Dict[int,bool]    = {}
AWAITING_PERMISSIONS: Dict[tuple,list]  = {}
AWAITING_LOG_ID:      Dict[int,bool]    = {}
BOT_ADD_STATES:       Dict[int,dict]    = {}
app = None

# ── OTP GUI Style (1-5, saved in config.json, changes all message formats) ──
GUI_STYLE: int = 1   # 1-8 styles

def load_gui_style():
    global GUI_STYLE
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                GUI_STYLE = int(json.load(f).get("GUI_STYLE", 1))
    except Exception:
        GUI_STYLE = 1

def save_gui_style(style: int):
    global GUI_STYLE
    GUI_STYLE = max(1, min(8, style))
    save_config_key("GUI_STYLE", GUI_STYLE)

load_gui_style()

# ═══════════════════════════════════════════════════════════
#  PANEL SESSION
# ═══════════════════════════════════════════════════════════
class PanelSession:
    """
    Each PanelSession owns a completely isolated aiohttp.ClientSession with its
    own CookieJar.  This means two different accounts on the same panel host
    (same base_url, different username/password) never share cookies or auth
    state — they are treated as entirely separate HTTP clients.
    """
    def __init__(self, base_url, username=None, password=None,
                 name="Unknown", panel_type="login", token=None, uri=None):
        self.base_url = base_url.rstrip("/")
        self.username = username; self.password = password
        self.name = name; self.panel_type = panel_type
        self.token = token; self.uri = uri
        self.login_url = f"{self.base_url}/login" if panel_type=="login" else None
        self.api_url = base_url; self.sesskey = None
        self.is_logged_in = False; self.id = None
        self.last_login_attempt = None; self.fail_count = 0
        self.stats_url: Optional[str] = None  # stored during endpoint discovery
        # Each PanelSession gets its OWN CookieJar — fully isolated from every
        # other session even when the same host is used by multiple accounts.
        self._cookie_jar = aiohttp.CookieJar(unsafe=True)
        self._session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # High-performance connector — 48-core server can handle more connections
            connector = aiohttp.TCPConnector(
                limit=100,           # max concurrent connections per session
                limit_per_host=20,   # max per host (prevents hammering one panel)
                ttl_dns_cache=300,   # cache DNS 5 minutes
                enable_cleanup_closed=True,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                headers={"User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36")},
                cookie_jar=self._cookie_jar)
        return self._session

    async def reset_session(self):
        """Close HTTP session and wipe cookies — call before re-login."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        # Fresh CookieJar so stale cookies from a failed login don't interfere
        self._cookie_jar = aiohttp.CookieJar(unsafe=True)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

# ═══════════════════════════════════════════════════════════
#  PANEL DB HELPERS
# ═══════════════════════════════════════════════════════════
async def init_panels_table():
    async with db.AsyncSessionLocal() as s:
        await s.execute(stext("""
            CREATE TABLE IF NOT EXISTS panels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, base_url TEXT NOT NULL,
                username TEXT, password TEXT, sesskey TEXT, api_url TEXT,
                token TEXT, uri TEXT, panel_type TEXT DEFAULT 'login',
                is_logged_in INTEGER DEFAULT 0, last_login_attempt TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""))
        await s.commit()

async def migrate_panels_table():
    async with db.AsyncSessionLocal() as s:
        cols = [r[1] for r in (await s.execute(stext("PRAGMA table_info(panels)"))).fetchall()]
        for col, defval in [("token","TEXT"),("panel_type","TEXT DEFAULT 'login'"),("uri","TEXT")]:
            if col not in cols:
                try: await s.execute(stext(f"ALTER TABLE panels ADD COLUMN {col} {defval}"))
                except Exception: pass
        await s.commit()

async def refresh_panels_from_db():
    global PANELS
    async with db.AsyncSessionLocal() as s:
        rows = (await s.execute(stext("SELECT * FROM panels"))).fetchall()
    new = []
    for r in rows:
        p = PanelSession(base_url=r[2],username=r[3],password=r[4],
                         name=r[1],panel_type=r[9] or "login",token=r[7],uri=r[8])
        p.id=r[0]; p.sesskey=r[5]; p.api_url=r[6] or r[2]
        p.is_logged_in=bool(r[10]); p.last_login_attempt=r[11]
        old = next((x for x in PANELS if x.id==p.id), None)
        if old: p._session = old._session
        new.append(p)
    PANELS = new

async def add_panel_to_db(name,base_url,username,password,panel_type="login",token=None,uri=None):
    async with db.AsyncSessionLocal() as s:
        await s.execute(stext(
            "INSERT INTO panels (name,base_url,username,password,panel_type,token,uri) "
            "VALUES (:n,:u,:us,:pw,:pt,:tk,:uri)"),
            dict(n=name,u=base_url,us=username,pw=password,pt=panel_type,tk=token,uri=uri))
        await s.commit()

async def update_panel_in_db(pid,name,base_url,username,password,panel_type,token,uri):
    async with db.AsyncSessionLocal() as s:
        await s.execute(stext(
            "UPDATE panels SET name=:n,base_url=:u,username=:us,password=:pw,"
            "panel_type=:pt,token=:tk,uri=:uri WHERE id=:id"),
            dict(n=name,u=base_url,us=username,pw=password,pt=panel_type,tk=token,uri=uri,id=pid))
        await s.commit()

async def delete_panel_from_db(pid: int):
    async with db.AsyncSessionLocal() as s:
        await s.execute(stext("DELETE FROM panels WHERE id=:id"),{"id":pid})
        await s.commit()

async def update_panel_login(pid,sesskey,api_url,logged_in:bool):
    async with db.AsyncSessionLocal() as s:
        await s.execute(stext(
            "UPDATE panels SET sesskey=:sk,api_url=:au,is_logged_in=:li,"
            "last_login_attempt=:now WHERE id=:id"),
            dict(sk=sesskey,au=api_url,li=1 if logged_in else 0,now=datetime.now(),id=pid))
        await s.commit()

async def load_panels_from_dex_to_db():
    """
    Load panels from dex.txt into the database.

    OLD behaviour: if count: return  — skipped entirely when even ONE panel
    already existed, so new dex.txt entries were never picked up after the
    first run.

    NEW behaviour: reads every entry and inserts only those whose name is not
    already in the database.  Adding panels to dex.txt and restarting is now
    enough — no need to wipe the database.

    Comment lines (starting with #) are stripped before parsing so example
    values like PANEL_BASE_URL = "<http://ip/ints>" in the header never
    accidentally create a phantom panel entry.
    """
    to_add = []

    if os.path.exists(DEX_FILE):
        try:
            raw = open(DEX_FILE, encoding="utf-8").read()
            # Remove comment lines so header examples never match the regex
            clean = "\n".join(
                l for l in raw.splitlines() if not l.strip().startswith("#"))

            for block in clean.split("panel="):
                if not block.strip():
                    continue
                name = block.strip().split("\n")[0].strip()
                if not name or name.startswith("<"):   # skip placeholder "<n>"
                    continue
                url = re.search(r'PANEL_BASE_URL\s*=\s*["\'\']([^"\'\']+)["\'\']', block)
                usr = re.search(r'PANEL_USERNAME\s*=\s*["\'\']([^"\'\']+)["\'\']', block)
                pw  = re.search(r'PANEL_PASSWORD\s*=\s*["\'\']([^"\'\']+)["\'\']', block)
                if not (url and usr and pw):
                    continue
                base_url = url.group(1).rstrip("/")
                if base_url.startswith("<") or not base_url.startswith("http"):
                    continue   # skip any remaining comment-derived junk
                to_add.append((name, base_url, usr.group(1), pw.group(1), "login", None, None))
                logger.info(f"📋 DEX entry found: {name}  →  {base_url}  user={usr.group(1)}")
        except Exception as e:
            logger.error(f"❌ DEX read error: {e}", exc_info=True)

    # Built-in CR-API panel — always ensure it is present
    to_add.append((
        "CR-API Panel 1",
        "http://147.135.212.197/crapi/had/viewstats",
        None, None, "api",
        "R1NQRTRSQopzh1aHZHSCfmiCklpycXSBeFV3QmaAdGtidFJeWItQ",
        None,
    ))

    inserted = 0
    skipped  = 0
    async with db.AsyncSessionLocal() as s:
        # Only insert panels whose name does not already exist in the database
        existing = {
            r[0] for r in
            (await s.execute(stext("SELECT name FROM panels"))).fetchall()
        }
        for name, url, usr, pw, pt, tok, uri in to_add:
            if name in existing:
                skipped += 1
                continue
            await s.execute(
                stext("INSERT INTO panels "
                      "(name,base_url,username,password,panel_type,token,uri) "
                      "VALUES (:n,:u,:us,:pw,:pt,:tk,:uri)"),
                dict(n=name, u=url, us=usr, pw=pw, pt=pt, tk=tok, uri=uri))
            inserted += 1
        await s.commit()

    logger.info(
        f"✅ DEX load done — {inserted} new panel(s) inserted, "
        f"{skipped} already in DB")


# ═══════════════════════════════════════════════════════════
#  ADMIN PERMISSIONS
# ═══════════════════════════════════════════════════════════
async def init_permissions_table():
    async with db.AsyncSessionLocal() as s:
        await s.execute(stext("""
            CREATE TABLE IF NOT EXISTS admin_permissions (
                user_id INTEGER PRIMARY KEY, permissions TEXT NOT NULL)"""))
        for uid in INITIAL_ADMIN_IDS:
            await s.execute(stext(
                "INSERT OR REPLACE INTO admin_permissions (user_id,permissions) VALUES (:u,:p)"),
                {"u":uid,"p":json.dumps(list(PERMISSIONS.keys()))})
        await s.commit()

async def get_admin_permissions(uid: int) -> List[str]:
    async with db.AsyncSessionLocal() as s:
        row = (await s.execute(stext(
            "SELECT permissions FROM admin_permissions WHERE user_id=:u"),{"u":uid})).fetchone()
        return json.loads(row[0]) if row else []

async def set_admin_permissions(uid: int, perms: List[str]):
    async with db.AsyncSessionLocal() as s:
        await s.execute(stext(
            "INSERT OR REPLACE INTO admin_permissions (user_id,permissions) VALUES (:u,:p)"),
            {"u":uid,"p":json.dumps(perms)})
        await s.commit()

async def remove_admin_permissions(uid: int):
    async with db.AsyncSessionLocal() as s:
        await s.execute(stext("DELETE FROM admin_permissions WHERE user_id=:u"),{"u":uid})
        await s.commit()

async def list_all_admins() -> List[int]:
    async with db.AsyncSessionLocal() as s:
        return [r[0] for r in (await s.execute(stext("SELECT user_id FROM admin_permissions"))).fetchall()]

def is_super_admin(uid: int) -> bool:
    return uid in INITIAL_ADMIN_IDS

# ═══════════════════════════════════════════════════════════
#  OTP KEYBOARD  — uses bot's own configured links
# ═══════════════════════════════════════════════════════════
def otp_keyboard(otp: Optional[str], full_msg: str = "",
                 for_group: bool = False) -> InlineKeyboardMarkup:
    """
    OTP keyboard — layout depends on GUI_STYLE (1-5).
    for_group=True  → group log version (no copy buttons, stays minimal)
    for_group=False → DM version (full copy buttons)
    """
    clean     = re.sub(r"[^0-9]", "", otp) if otp else ""
    panel_url = NUMBER_BOT_LINK or GET_NUMBER_URL or (
        f"https://t.me/{BOT_USERNAME.lstrip('@')}" if BOT_USERNAME else None)
    info_url  = OTP_GROUP_LINK or CHANNEL_LINK
    kb = []

    if for_group:
        # ── Group layout: copy button + PANEL/INFO ─────────────────
        if clean:
            kb.append([InlineKeyboardButton(
                f"»»  📋  {clean}", copy_text=CopyTextButton(text=clean))])
        row = []
        if panel_url: row.append(InlineKeyboardButton("📱  PANEL  ↗", url=panel_url))
        if info_url:  row.append(InlineKeyboardButton("💬  INFO  ↗",  url=info_url))
        if row: kb.append(row)
        return InlineKeyboardMarkup(kb)

    # ── DM layout varies by GUI_STYLE ──────────────────────────────
    style = GUI_STYLE

    if style == 1:  # Classic — green copy + blue PANEL/INFO
        if clean:
            kb.append([InlineKeyboardButton(
                f"»»  📋  {clean}", copy_text=CopyTextButton(text=clean))])
        if full_msg:
            kb.append([InlineKeyboardButton(
                "📩  Copy Full SMS", copy_text=CopyTextButton(text=full_msg[:256]))])
        row = []
        if panel_url: row.append(InlineKeyboardButton("📱  PANEL  ↗", url=panel_url))
        if info_url:  row.append(InlineKeyboardButton("💬  INFO  ↗",  url=info_url))
        if row: kb.append(row)

    elif style == 2:  # Neon — emoji-heavy inline buttons
        if clean:
            kb.append([InlineKeyboardButton(
                f"⚡  {clean}  ⚡", copy_text=CopyTextButton(text=clean))])
        row = []
        if panel_url: row.append(InlineKeyboardButton("🔥  GET NUMBER", url=panel_url))
        if info_url:  row.append(InlineKeyboardButton("💎  OTP GROUP",  url=info_url))
        if row: kb.append(row)
        if full_msg:
            kb.append([InlineKeyboardButton(
                "📋  Copy SMS", copy_text=CopyTextButton(text=full_msg[:256]))])

    elif style == 3:  # Minimal — single copy + one link row
        if clean:
            kb.append([InlineKeyboardButton(
                f"📋  Copy  {clean}", copy_text=CopyTextButton(text=clean))])
        links = []
        if panel_url: links.append(InlineKeyboardButton("🤖  Bot", url=panel_url))
        if info_url:  links.append(InlineKeyboardButton("📢  Group", url=info_url))
        if links: kb.append(links)

    elif style == 4:  # Bold — large icons, force attention
        if clean:
            kb.append([InlineKeyboardButton(
                f"🔑  COPY OTP  →  {clean}", copy_text=CopyTextButton(text=clean))])
        if full_msg:
            kb.append([InlineKeyboardButton(
                "💬  COPY FULL MSG", copy_text=CopyTextButton(text=full_msg[:256]))])
        row = []
        if panel_url: row.append(InlineKeyboardButton("📲  NUMBER BOT", url=panel_url))
        if info_url:  row.append(InlineKeyboardButton("🔔  OTP CHANNEL", url=info_url))
        if row: kb.append(row)

    elif style == 5:  # Premium — copy + full suite of links
        if clean:
            kb.append([InlineKeyboardButton(
                f"✅  Copy OTP: {clean}", copy_text=CopyTextButton(text=clean))])
        if full_msg:
            kb.append([InlineKeyboardButton(
                "📩  Copy Message", copy_text=CopyTextButton(text=full_msg[:256]))])
        row1 = []
        if panel_url: row1.append(InlineKeyboardButton("🤖  Get Number", url=panel_url))
        if info_url:  row1.append(InlineKeyboardButton("💬  OTP Group",  url=info_url))
        if row1: kb.append(row1)
        if CHANNEL_LINK and info_url != CHANNEL_LINK:
            kb.append([InlineKeyboardButton("📢  Channel", url=CHANNEL_LINK)])

    # GUI picker button — always shown in DM
    kb.append([InlineKeyboardButton(f"🎨  GUI Style: {style}/5", callback_data="pick_gui")])
    return InlineKeyboardMarkup(kb)

# ═══════════════════════════════════════════════════════════
#  USER KEYBOARDS
# ═══════════════════════════════════════════════════════════
def main_menu_kb() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("🧇  Get Number",  callback_data="buy_menu"),
         InlineKeyboardButton("🫁  My Profile",  callback_data="profile")],
    ]
    if OTP_GROUP_LINK or CHANNEL_LINK:
        row2 = []
        if CHANNEL_LINK:    row2.append(InlineKeyboardButton("📢  Channel",   url=CHANNEL_LINK))
        if OTP_GROUP_LINK:  row2.append(InlineKeyboardButton("💬  OTP Group", url=OTP_GROUP_LINK))
        if row2: kb.append(row2)
    nb = NUMBER_BOT_LINK or GET_NUMBER_URL
    row3 = []
    if nb: row3.append(InlineKeyboardButton("📞  Number Bot", url=nb))
    sup = SUPPORT_USER.lstrip("@")
    if sup: row3.append(InlineKeyboardButton("🛟  Support",   url=f"https://t.me/{sup}"))
    if row3: kb.append(row3)
    dev = DEVELOPER.lstrip("@")
    if dev:
        kb.append([InlineKeyboardButton("🧠  Developer",  url=f"https://t.me/{dev}")])
    return InlineKeyboardMarkup(kb)

def services_kb(svcs: list) -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(f"📱  {s}", callback_data=f"svc_{s}")] for s in svcs]
    kb.append([InlineKeyboardButton("🔙  Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(kb)

def countries_kb(svc: str, countries: list) -> InlineKeyboardMarkup:
    kb = []; row = []
    for flag, name in countries:
        row.append(InlineKeyboardButton(f"{flag} {name}", callback_data=f"cntry|{svc}|{name}"))
        if len(row)==2: kb.append(row); row=[]
    if row: kb.append(row)
    kb.append([InlineKeyboardButton("🔙  Back", callback_data="buy_menu")])
    return InlineKeyboardMarkup(kb)

def waiting_kb(prefix=None, service=None) -> InlineKeyboardMarkup:
    pfx = f"ON ({prefix})" if prefix else "OFF"
    kb = [
        [InlineKeyboardButton("🚫  Block Number",   callback_data="ask_block"),
         InlineKeyboardButton("🔄  Change Number",  callback_data="skip_next")],
        [InlineKeyboardButton("🌍  Change Country", callback_data="change_country"),
         InlineKeyboardButton(f"🔡  Prefix: {pfx}", callback_data="set_prefix")],
        [InlineKeyboardButton("📋  Change Service", callback_data="buy_menu")],
    ]
    if OTP_GROUP_LINK:
        kb.append([InlineKeyboardButton("💬  OTP Group", url=OTP_GROUP_LINK)])
    return InlineKeyboardMarkup(kb)

def confirm_block_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅  Yes Block",  callback_data="block_yes"),
        InlineKeyboardButton("❌  No Keep",    callback_data="block_no"),
    ]])

# ═══════════════════════════════════════════════════════════
#  ADMIN KEYBOARDS  — Pro-level submenus
# ═══════════════════════════════════════════════════════════
def admin_main_kb(perms: list, is_sup: bool) -> InlineKeyboardMarkup:
    kb = []
    r1 = []
    if "manage_files" in perms: r1.append(InlineKeyboardButton("📂  Numbers",    callback_data="admin_numbers"))
    if "broadcast"    in perms: r1.append(InlineKeyboardButton("📢  Broadcast",  callback_data="admin_broadcast"))
    if r1: kb.append(r1)
    r2 = []
    if "view_stats"    in perms: r2.append(InlineKeyboardButton("📊  Statistics", callback_data="admin_stats_menu"))
    if is_sup:                   r2.append(InlineKeyboardButton("👤  Users",       callback_data="admin_users"))
    if r2: kb.append(r2)
    r3 = []
    if "manage_panels" in perms: r3.append(InlineKeyboardButton("🔌  Panels",     callback_data="admin_panel_manager"))
    if "manage_logs"   in perms: r3.append(InlineKeyboardButton("📋  Logs",        callback_data="admin_manage_logs"))
    if r3: kb.append(r3)
    r4 = []
    if is_sup: r4.append(InlineKeyboardButton("👥  Admins",    callback_data="admin_manage_admins"))
    r4.append(   InlineKeyboardButton("⚙️  Settings",          callback_data="admin_settings"))
    kb.append(r4)
    r5 = []
    if "manage_panels" in perms: r5.append(InlineKeyboardButton("📡  Fetch SMS",  callback_data="admin_fetch_sms"))
    if is_sup:                   r5.append(InlineKeyboardButton("🛠  Advanced",   callback_data="admin_advanced"))
    if r5: kb.append(r5)
    if is_sup:
        kb.append([InlineKeyboardButton("🔑  OTP Tools",        callback_data="admin_otp_tools"),
                   InlineKeyboardButton("🔔  Notify",           callback_data="admin_notify_menu")])
    if is_sup and not IS_CHILD_BOT:
        kb.append([InlineKeyboardButton("🤖  Add Bot",          callback_data="add_bot_start"),
                   InlineKeyboardButton("🖥  Manage Bots",      callback_data="admin_bots")])
        kb.append([InlineKeyboardButton("📢  Broadcast All Bots", callback_data="broadcast_all_bots")])
    return InlineKeyboardMarkup(kb)

# ── Numbers submenu ───────────────────────────────────────────
def admin_numbers_kb(cats: list) -> InlineKeyboardMarkup:
    kb = []
    for cat, cnt in cats:
        sid = hashlib.md5(cat.encode()).hexdigest()[:10]
        CATEGORY_MAP[sid] = cat
        kb.append([
            InlineKeyboardButton(f"📁 {cat}  ({cnt})", callback_data="ignore"),
            InlineKeyboardButton("📊", callback_data=f"cat_stats_{sid}"),
            InlineKeyboardButton("🗑", callback_data=f"del_{sid}"),
        ])
    kb.append([InlineKeyboardButton("📤  Upload Numbers",   callback_data="admin_upload_info"),
               InlineKeyboardButton("📋  All Categories",   callback_data="admin_files")])
    kb.append([InlineKeyboardButton("♻️  Free Cooldowns",   callback_data="admin_reset"),
               InlineKeyboardButton("🗑  Purge Used",        callback_data="purge_used")])
    kb.append([InlineKeyboardButton("🚫  Purge Blocked",    callback_data="purge_blocked"),
               InlineKeyboardButton("📊  Full Stats",        callback_data="admin_stats")])
    kb.append([InlineKeyboardButton("🔙  Back",             callback_data="admin_home")])
    return InlineKeyboardMarkup(kb)

# ── Stats submenu ─────────────────────────────────────────────
def admin_stats_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊  Live Stats",       callback_data="admin_stats"),
         InlineKeyboardButton("📈  OTP History",      callback_data="admin_otp_history")],
        [InlineKeyboardButton("🔌  Panel Status",     callback_data="test_panels"),
         InlineKeyboardButton("💾  DB Summary",       callback_data="admin_db_summary")],
        [InlineKeyboardButton("👤  User Count",       callback_data="admin_list_users"),
         InlineKeyboardButton("🔑  OTP Store",        callback_data="admin_otp_store")],
        [InlineKeyboardButton("🔙  Back",             callback_data="admin_home")],
    ])

# ── OTP Tools submenu (super only) ────────────────────────────
def admin_otp_tools_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑  View OTP Store",   callback_data="admin_otp_store"),
         InlineKeyboardButton("📤  Export OTPs",      callback_data="export_otps")],
        [InlineKeyboardButton("🗑  Clear OTP Store",  callback_data="clear_otps"),
         InlineKeyboardButton("📈  OTP History",      callback_data="admin_otp_history")],
        [InlineKeyboardButton("🔍  Find OTP by Number", callback_data="find_otp_prompt")],
        [InlineKeyboardButton("🔙  Back",             callback_data="admin_home")],
    ])

# ── Notify/Broadcast submenu ──────────────────────────────────
def admin_notify_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢  Broadcast Users",  callback_data="admin_broadcast"),
         InlineKeyboardButton("📢  Broadcast All Bots", callback_data="broadcast_all_bots")],
        [InlineKeyboardButton("📋  Log Groups",       callback_data="admin_manage_logs"),
         InlineKeyboardButton("➕  Add Log Group",    callback_data="add_log_prompt")],
        [InlineKeyboardButton("🔔  Send Test OTP",    callback_data="send_test_otp"),
         InlineKeyboardButton("📡  Ping Log Groups",  callback_data="ping_log_groups")],
        [InlineKeyboardButton("🔙  Back",             callback_data="admin_home")],
    ])

# ── Users submenu (super admin) ───────────────────────────────
def admin_users_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥  All Users",         callback_data="admin_list_users"),
         InlineKeyboardButton("📊  User Stats",        callback_data="admin_db_summary")],
        [InlineKeyboardButton("🔢  Global Limit",      callback_data="set_limit"),
         InlineKeyboardButton("🚫  Free All Numbers",  callback_data="admin_reset")],
        [InlineKeyboardButton("📢  Broadcast Users",   callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔙  Back",              callback_data="admin_home")],
    ])

# ── Panel Manager ─────────────────────────────────────────────
def panel_mgr_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑  Login Panels",     callback_data="panels_login"),
         InlineKeyboardButton("🔌  API Panels",       callback_data="panels_api")],
        [InlineKeyboardButton("📡  IVAS Panels",      callback_data="panels_ivas")],
        [InlineKeyboardButton("🔄  Login All Now",    callback_data="login_all_panels"),
         InlineKeyboardButton("🔁  Restart Workers",  callback_data="restart_workers")],
        [InlineKeyboardButton("📋  View Logs",        callback_data="view_logs"),
         InlineKeyboardButton("📡  Fetch SMS Now",    callback_data="admin_fetch_sms")],
        [InlineKeyboardButton("🔙  Back",             callback_data="admin_home")],
    ])

def panel_list_kb(panels: list, ptype: str) -> InlineKeyboardMarkup:
    kb = []
    for p in panels:
        if ptype=="ivas": st="🟢" if (p.name in IVAS_TASKS and not IVAS_TASKS[p.name].done()) else "🔴"
        else:             st="🟢" if p.is_logged_in else "🔴"
        kb.append([
            InlineKeyboardButton(f"{st} {p.name}", callback_data="ignore"),
            InlineKeyboardButton("🔍", callback_data=f"p_info_{p.id}"),
            InlineKeyboardButton("🔄", callback_data=f"p_test_{p.id}"),
            InlineKeyboardButton("✏️", callback_data=f"p_edit_{p.id}"),
            InlineKeyboardButton("🗑", callback_data=f"p_del_{p.id}"),
        ])
    kb.append([InlineKeyboardButton("➕  Add Panel", callback_data="p_add")])
    kb.append([InlineKeyboardButton("🔙  Back",      callback_data="admin_panel_manager")])
    return InlineKeyboardMarkup(kb)

def ptype_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑  Login Panel", callback_data="pt_login"),
         InlineKeyboardButton("🔌  API Panel",   callback_data="pt_api")],
        [InlineKeyboardButton("📡  IVAS Panel",  callback_data="pt_ivas")],
        [InlineKeyboardButton("❌  Cancel",      callback_data="cancel_action")],
    ])

def confirm_del_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅  Yes, Delete", callback_data="p_del_confirm"),
        InlineKeyboardButton("❌  Cancel",      callback_data="admin_panel_manager"),
    ]])

_THEME_NAMES = {
    0: "🖤 Premium Dark",
    1: "⚡ Neon Electric",
    2: "🔥 Sigma Classic",
    3: "🤍 Minimal Clean",
    4: "👑 Royal Gold",
    5: "💫 TempNum Style   ⭐PREMIUM",
    6: "🚀 Jack-X Style    ⭐PREMIUM",
    7: "☠️  Cyber Matrix    ⭐PREMIUM",
}

def admin_settings_kb() -> InlineKeyboardMarkup:
    theme_name = _THEME_NAMES.get(OTP_GUI_THEME % 8, "Unknown")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📦  Assign Limit: {DEFAULT_ASSIGN_LIMIT}", callback_data="set_limit")],
        [InlineKeyboardButton("🔗  Bot Links",        callback_data="admin_links"),
         InlineKeyboardButton("🤖  Bot Info",         callback_data="admin_botinfo")],
        [InlineKeyboardButton(f"🎨  OTP GUI: {theme_name}", callback_data="admin_gui_theme")],
        [InlineKeyboardButton("🧹  Maintenance",      callback_data="admin_maintenance"),
         InlineKeyboardButton("🔑  Change Token",     callback_data="change_token_prompt")],
        [InlineKeyboardButton("🌍  Reload Countries", callback_data="reload_countries"),
         InlineKeyboardButton("📋  View Bot Logs",    callback_data="view_logs")],
        [InlineKeyboardButton("🔙  Back",             callback_data="admin_home")],
    ])

def gui_theme_kb() -> InlineKeyboardMarkup:
    kb = []
    for tid, name in _THEME_NAMES.items():
        mark = "✅ " if tid == OTP_GUI_THEME % 8 else ""
        kb.append([InlineKeyboardButton(f"{mark}{name}", callback_data=f"set_gui_theme_{tid}")])
    kb.append([InlineKeyboardButton("🔙  Back", callback_data="admin_settings")])
    return InlineKeyboardMarkup(kb)

def admin_links_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢  Channel Link",    callback_data="set_channel_prompt")],
        [InlineKeyboardButton("💬  OTP Group Link",  callback_data="set_otpgroup_prompt")],
        [InlineKeyboardButton("📞  Number Bot Link", callback_data="set_numbot_prompt")],
        [InlineKeyboardButton("🛟  Support User",    callback_data="set_support_prompt")],
        [InlineKeyboardButton("🧠  Developer",       callback_data="set_developer_prompt")],
        [InlineKeyboardButton("🔙  Back",            callback_data="admin_settings")],
    ])

def admin_maintenance_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("♻️  Free All Cooldowns", callback_data="admin_reset"),
         InlineKeyboardButton("🗑  Clear OTP Store",    callback_data="clear_otps")],
        [InlineKeyboardButton("🗑  Purge Used Numbers", callback_data="purge_used"),
         InlineKeyboardButton("🗑  Purge Blocked Nums", callback_data="purge_blocked")],
        [InlineKeyboardButton("🔄  Reload Countries",   callback_data="reload_countries"),
         InlineKeyboardButton("🔁  Restart All Workers",callback_data="restart_workers")],
        [InlineKeyboardButton("🔙  Back",               callback_data="admin_settings")],
    ])

def limit_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(str(i), callback_data=f"glimit_{i}") for i in rng]
            for rng in [range(1,4), range(4,7), range(7,11)]]
    rows.append([InlineKeyboardButton("🔙  Back", callback_data="admin_settings")])
    return InlineKeyboardMarkup(rows)

def advanced_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄  Test All Panels",   callback_data="test_panels"),
         InlineKeyboardButton("🔌  Login All Panels",  callback_data="login_all_panels")],
        [InlineKeyboardButton("📡  Fetch SMS Now",     callback_data="admin_fetch_sms"),
         InlineKeyboardButton("🔁  Restart Workers",   callback_data="restart_workers")],
        [InlineKeyboardButton("🔑  OTP Tools",         callback_data="admin_otp_tools"),
         InlineKeyboardButton("🧹  Maintenance",       callback_data="admin_maintenance")],
        [InlineKeyboardButton("📋  View Logs",         callback_data="view_logs"),
         InlineKeyboardButton("💾  DB Summary",        callback_data="admin_db_summary")],
        [InlineKeyboardButton("📊  OTP History",       callback_data="admin_otp_history"),
         InlineKeyboardButton("🔔  Notify Menu",       callback_data="admin_notify_menu")],
        [InlineKeyboardButton("🌍  Reload Countries",  callback_data="reload_countries"),
         InlineKeyboardButton("🔢  Set Limit",         callback_data="set_limit")],
        [InlineKeyboardButton("🔙  Back",              callback_data="admin_home")],
    ])

def files_kb(cats: list) -> InlineKeyboardMarkup:
    """Legacy alias kept for compatibility — use admin_numbers_kb for new code."""
    return admin_numbers_kb(cats)

def svc_sel_kb(selected: list = None) -> InlineKeyboardMarkup:
    if not selected: selected = []
    ALL = ["WhatsApp","Telegram","Facebook","Instagram","Twitter","TikTok",
           "Google","Microsoft","Snapchat","Signal","Tinder","Uber","Amazon","PayPal"]
    kb = []; row = []
    for s in ALL:
        row.append(InlineKeyboardButton(("✅ " if s in selected else "")+s, callback_data=f"us_{s}"))
        if len(row)==2: kb.append(row); row=[]
    if row: kb.append(row)
    kb.append([InlineKeyboardButton("✅  Done",   callback_data="us_done"),
               InlineKeyboardButton("❌  Cancel", callback_data="us_cancel")])
    return InlineKeyboardMarkup(kb)

def admin_list_kb(admins: list) -> InlineKeyboardMarkup:
    kb = []
    for aid in admins:
        crown = "👑 " if aid in INITIAL_ADMIN_IDS else ""
        kb.append([InlineKeyboardButton(f"{crown}{aid}", callback_data="ignore"),
                   InlineKeyboardButton("❌ Remove", callback_data=f"rm_admin_{aid}")])
    kb.append([InlineKeyboardButton("➕  Add Admin",  callback_data="add_admin_prompt")])
    kb.append([InlineKeyboardButton("🔙  Back",       callback_data="admin_home")])
    return InlineKeyboardMarkup(kb)

def perms_kb(selected: list, uid: int) -> InlineKeyboardMarkup:
    kb = []
    for perm, desc in PERMISSIONS.items():
        mark = "✅ " if perm in selected else "⬜ "
        kb.append([InlineKeyboardButton(f"{mark}{desc}",
                                        callback_data=f"ptoggle|{uid}|{perm}")])
    kb.append([InlineKeyboardButton("✅  Save", callback_data=f"pdone|{uid}")])
    kb.append([InlineKeyboardButton("❌  Cancel", callback_data="cancel_action")])
    return InlineKeyboardMarkup(kb)

def logs_kb(chats: list) -> InlineKeyboardMarkup:
    kb = []
    for cid in chats:
        kb.append([InlineKeyboardButton(f"📢 {cid}", callback_data="ignore"),
                   InlineKeyboardButton("❌", callback_data=f"rm_log_{cid}")])
    kb.append([InlineKeyboardButton("➕  Add Log Group", callback_data="add_log_prompt")])
    kb.append([InlineKeyboardButton("🔙  Back",          callback_data="admin_home")])
    return InlineKeyboardMarkup(kb)

def bots_list_kb(bots: list) -> InlineKeyboardMarkup:
    """Premium child bot list with status indicators and quick actions."""
    kb = []
    run_count = sum(1 for b in bots if b.get("running"))
    for info in bots:
        bid = info["id"]; st = "🟢" if info.get("running") else "🔴"
        name = html.escape(info["name"])[:18]
        kb.append([
            InlineKeyboardButton(f"{st} {name}", callback_data="ignore"),
            InlineKeyboardButton("ℹ️", callback_data=f"bot_info_{bid}"),
            InlineKeyboardButton("▶️" if not info.get("running") else "⏹", 
                                 callback_data=f"bot_start_{bid}" if not info.get("running") else f"bot_stop_{bid}"),
            InlineKeyboardButton("🔁", callback_data=f"bot_restart_{bid}"),
            InlineKeyboardButton("🗑", callback_data=f"bot_del_{bid}"),
        ])
    kb.append([
        InlineKeyboardButton("🤖  Add Bot",        callback_data="add_bot_start"),
        InlineKeyboardButton("📢  Broadcast All",  callback_data="broadcast_all_bots"),
    ])
    kb.append([
        InlineKeyboardButton("▶️  Start All",      callback_data="bots_start_all"),
        InlineKeyboardButton("⏹  Stop All",        callback_data="bots_stop_all"),
    ])
    kb.append([
        InlineKeyboardButton("📊  All Stats",      callback_data="bots_all_stats"),
        InlineKeyboardButton("🔁  Refresh",        callback_data="admin_bots"),
    ])
    kb.append([InlineKeyboardButton("🔙  Back to Admin", callback_data="admin_home")])
    return InlineKeyboardMarkup(kb)

def bot_actions_kb(bid: str, running: bool, info: dict = None) -> InlineKeyboardMarkup:
    """Expanded per-bot action panel."""
    info = info or {}
    r_row = []
    if running:
        r_row = [InlineKeyboardButton("⏹  Stop",    callback_data=f"bot_stop_{bid}"),
                 InlineKeyboardButton("🔁  Restart", callback_data=f"bot_restart_{bid}")]
    else:
        r_row = [InlineKeyboardButton("▶️  Start",   callback_data=f"bot_start_{bid}"),
                 InlineKeyboardButton("🔁  Restart", callback_data=f"bot_restart_{bid}")]
    return InlineKeyboardMarkup([
        r_row,
        [InlineKeyboardButton("📋  View Logs",    callback_data=f"bot_log_{bid}"),
         InlineKeyboardButton("📊  Bot Stats",    callback_data=f"bot_stats_{bid}")],
        [InlineKeyboardButton("📢  Broadcast",    callback_data=f"bot_bcast_{bid}"),
         InlineKeyboardButton("🔗  Edit Links",   callback_data=f"bot_editlinks_{bid}")],
        [InlineKeyboardButton("🗑  Delete Bot",   callback_data=f"bot_del_{bid}"),
         InlineKeyboardButton("🔙  Back",         callback_data="admin_bots")],
    ])

def confirm_del_bot_kb(bid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅  Yes, Delete", callback_data=f"bot_delok_{bid}"),
        InlineKeyboardButton("❌  Cancel",      callback_data=f"bot_info_{bid}"),
    ]])

def bot_edit_links_kb(bid: str) -> InlineKeyboardMarkup:
    """Edit a child bot's configured links inline."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢  Channel Link",    callback_data=f"bot_setlink_{bid}_CHANNEL_LINK")],
        [InlineKeyboardButton("💬  OTP Group Link",  callback_data=f"bot_setlink_{bid}_OTP_GROUP_LINK")],
        [InlineKeyboardButton("📞  Number Bot Link", callback_data=f"bot_setlink_{bid}_NUMBER_BOT_LINK")],
        [InlineKeyboardButton("🛟  Support User",    callback_data=f"bot_setlink_{bid}_SUPPORT_USER")],
        [InlineKeyboardButton("🔙  Back",            callback_data=f"bot_info_{bid}")],
    ])

def confirm_kb(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅  Confirm", callback_data=f"confirm_{action}"),
        InlineKeyboardButton("❌  Cancel",  callback_data="cancel_action"),
    ]])


# ═══════════════════════════════════════════════════════════
#  PANEL LOGIN / FETCH
# ═══════════════════════════════════════════════════════════
async def test_api_panel(panel: PanelSession) -> bool:
    try:
        s = await panel.get_session()
        now = datetime.now(); prev = now - timedelta(hours=24)
        params = {"token":panel.token,"dt1":prev.strftime("%Y-%m-%d %H:%M:%S"),
                  "dt2":now.strftime("%Y-%m-%d %H:%M:%S"),"records":1}
        async with s.get(panel.base_url,params=params,timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200: return False
            try: data = await resp.json(content_type=None)
            except Exception: return False
            if isinstance(data, list): return True
            if isinstance(data, dict):
                st = str(data.get("status","")).lower()
                if st == "error": return False
                return st == "success" or any(k in data for k in ("data","records","sms"))
    except Exception as e: logger.error(f"API test '{panel.name}': {e}")
    return False

async def login_to_panel(panel: PanelSession) -> bool:
    """
    Login for panels that follow the /ints/ URL structure, e.g.:
        base_url  = http://185.2.83.39/ints          (trailing slash stripped)
        login page= http://185.2.83.39/ints/login
        form POST = http://185.2.83.39/ints/signin   (relative → urljoin)
        stats page= http://185.2.83.39/ints/SMSCDRStats
        data API  = http://185.2.83.39/ints/res/data_smscdr.php

    Key rule: panel.base_url already contains the full path prefix (/ints),
    so use it directly for all endpoint construction.  Never strip the path.
    """
    if panel.panel_type == "api":
        ok = await test_api_panel(panel)
        panel.is_logged_in = ok
        if ok:  logger.info(f"🔌 API panel \"{panel.name}\" — token OK")
        else:   logger.warning(f"🔌 API panel \"{panel.name}\" — token FAILED, check credentials")
        return ok

    logger.info(f"🔑 Logging in to \"{panel.name}\"  →  {panel.base_url}")
    await panel.reset_session()  # fresh isolated CookieJar every attempt

    try:
        s = await panel.get_session()

        # ── 1. Load the login page ────────────────────────────────────
        # base_url already has /ints, so /login gives http://ip/ints/login
        login_url = panel.login_url or f"{panel.base_url}/login"
        logger.info(f"   ↗ GET  {login_url}")
        async with s.get(login_url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                logger.warning(f"   ✗ Login page HTTP {r.status}  panel=\"{panel.name}\"")
                return False
            pg = await r.text()

        # ── 2. Parse the form ─────────────────────────────────────────
        soup = BeautifulSoup(pg, "html.parser")
        form = soup.find("form")
        if not form:
            logger.warning(f"   ✗ No <form> found at {login_url}")
            return False

        payload = {}
        for tag in form.find_all("input"):
            nm  = tag.get("name")
            val = tag.get("value", "")
            ph  = (tag.get("placeholder", "") + " " + (nm or "")).lower()
            tp  = tag.get("type", "text").lower()
            if not nm:
                continue
            if tp == "hidden":
                # Keep all hidden fields exactly — these carry CSRF tokens
                payload[nm] = val
            elif any(k in ph for k in ("user", "email", "login", "uname", "username")):
                payload[nm] = panel.username or ""
                logger.info(f"   ↳ username field → {nm}")
            elif any(k in ph for k in ("pass", "pwd", "secret", "password")):
                payload[nm] = panel.password or ""
                logger.info(f"   ↳ password field → {nm}")
            elif any(k in ph for k in ("ans", "captcha", "answer", "result", "sum", "calc")):
                # Solve the arithmetic captcha (e.g. "What is 4 + 7?")
                cap = re.search(r"(\d+)\s*([+\-*])\s*(\d+)", form.get_text() or pg)
                if cap:
                    n1, op, n2 = int(cap.group(1)), cap.group(2), int(cap.group(3))
                    ans = n1 + n2 if op == "+" else (n1 - n2 if op == "-" else n1 * n2)
                    payload[nm] = str(ans)
                    logger.info(f"   ↳ captcha {n1}{op}{n2} = {ans}")
            else:
                payload[nm] = val

        # ── 3. Resolve the form action URL ────────────────────────────
        # MUST use urljoin so "signin" on page "/ints/login" becomes
        # "/ints/signin", NOT "/signin".
        # e.g.  urljoin("http://ip/ints/login", "signin")
        #       → "http://ip/ints/signin"   ✓
        raw_action = (form.get("action") or "").strip()
        if raw_action:
            if raw_action.startswith("http"):
                action = raw_action                         # already absolute
            else:
                # urljoin resolves relative to the current page directory
                from urllib.parse import urljoin
                action = urljoin(login_url, raw_action)
        else:
            action = login_url                              # no action = post to same URL

        origin = login_url.split("/ints/")[0] if "/ints/" in login_url else                  "/".join(login_url.split("/")[:3])

        logger.info(f"   ↗ POST {action}")
        async with s.post(
            action, data=payload,
            headers={"Referer": login_url, "Origin": origin},
            timeout=aiohttp.ClientTimeout(total=20),
            allow_redirects=True,
        ) as pr:
            final_url = str(pr.url)
            body      = await pr.text()
            body_l    = body.lower()
            logger.info(f"   ← HTTP {pr.status}  final URL → {final_url}")

            # ── 4. Detect success ─────────────────────────────────────
            #
            # IMPORTANT: All /ints/ panels POST to ints/signin (that is the
            # form action).  After a successful login they redirect to
            # ints/agent/SMSDashboard.  The old "still_auth" check wrongly
            # flagged Wolf and others because "signin" appeared in either the
            # form action URL or a redirect step, even though login succeeded.
            #
            # Rule: if the response body contains dashboard/logout keywords
            # → login succeeded, regardless of what the URL says.
            # Only fall back to URL inspection when the body is ambiguous.
            _OK_BODY = {
                "logout", "log out", "sign out", "signout",
                "dashboard", "smscdr", "sms log", "sms report",
                "smscdrstats", "welcome", "my account",
                "sms dashboard", "smsdashboard",
            }
            # A failed login usually returns a page with these keywords
            # AND has no dashboard content in the body.
            _FAIL_BODY = {"invalid", "incorrect", "wrong password",
                          "failed", "error", "invalid credentials"}
            _OK_URL    = {"dashboard", "smscdr", "smscdrstats",
                          "welcome", "inbox", "report", "home"}

            body_ok    = any(k in body_l for k in _OK_BODY)
            body_fail  = any(k in body_l for k in _FAIL_BODY)
            url_ok     = any(k in final_url.lower() for k in _OK_URL)

            # body_fail + no body_ok = definite failure
            # body_ok alone = definite success (URL doesn't matter)
            # neither: use URL as tiebreaker
            if body_fail and not body_ok:
                err_el = BeautifulSoup(body,"html.parser").find(
                    class_=re.compile(r"error|alert|danger|invalid", re.I))
                hint = err_el.get_text(strip=True)[:120] if err_el else body_l[:120]
                logger.warning(
                    f"   ✗ Login FAILED  panel=\"{panel.name}\"  hint=\"{hint}\""
                )
                panel.fail_count += 1
                return False

            if not body_ok and not url_ok:
                logger.warning(
                    f"   ✗ Login FAILED  panel=\"{panel.name}\"  "
                    f"(no success signal in body or URL)  final=\"{final_url[-60:]}\""
                )
                panel.fail_count += 1
                return False

            logger.info(f"   ✓ Authenticated  panel=\"{panel.name}\""
                        f"  (body_ok={body_ok} url_ok={url_ok})")

            # ── 5. Discover the SMS data endpoint ─────────────────────
            #
            # The screenshot showed the panel redirects to:
            #   http://ip/ints/agent/SMSDashboard
            # which means the stats page is at:
            #   http://ip/ints/agent/SMSCDRStats  (agent sub-dir)
            # NOT at:
            #   http://ip/ints/SMSCDRStats         (always 404)
            #
            # Strategy: extract the directory portion of final_url
            # and try it first.  Fall back to panel.base_url if that fails.
            from urllib.parse import urlparse as _up
            parsed_final = _up(final_url)
            # directory of the redirect URL, e.g. /ints/agent from /ints/agent/SMSDashboard
            path_parts       = parsed_final.path.rstrip("/").rsplit("/", 1)
            redirect_dir     = path_parts[0] if len(path_parts) > 1 else ""
            redirect_base    = f"{parsed_final.scheme}://{parsed_final.netloc}{redirect_dir}"
            # e.g. http://185.2.83.39/ints/agent

            # Try the redirect directory first, then panel.base_url as fallback
            candidate_bases = []
            if redirect_base and redirect_base != panel.base_url:
                candidate_bases.append(redirect_base)    # /ints/agent  ← correct for your panels
            candidate_bases.append(panel.base_url)       # /ints         ← fallback

            for disc_base in candidate_bases:
                for stats_path in ["/SMSCDRStats", "/client/SMSCDRStats",
                                   "/smscdrstats", "/sms/log", "/smslogs", "/sms"]:
                    try:
                        stats_url = disc_base + stats_path
                        logger.info(f"   🔍 Trying {stats_url}")
                        async with s.get(stats_url, timeout=aiohttp.ClientTimeout(total=10)) as sr:
                            if sr.status != 200:
                                logger.info(f"      → {sr.status} skip")
                                continue
                            page = await sr.text()
                            for sc in BeautifulSoup(page, "html.parser").find_all("script"):
                                if not sc.string:
                                    continue
                                m = re.search(
                                    r'sAjaxSource["\'\\s]*:\s*["\']([^"\']+)["\']',
                                    sc.string)
                                if m:
                                    found = m.group(1)
                                    if not found.startswith("http"):
                                        found = disc_base + "/" + found.lstrip("/")
                                    if "sesskey=" in found:
                                        parts         = found.split("?", 1)
                                        panel.api_url = parts[0]
                                        sk = re.search(r"sesskey=([^&]+)", parts[1])
                                        if sk: panel.sesskey = sk.group(1)
                                    else:
                                        panel.api_url = found
                                    panel.stats_url    = stats_url   # store for Referer
                                    panel.is_logged_in = True
                                    panel.fail_count   = 0
                                    logger.info(
                                        f"   📡 Endpoint found: {panel.api_url}"
                                        + (f"  sesskey={panel.sesskey[:12]}…" if panel.sesskey else ""))
                                    return True
                    except Exception as disc_err:
                        logger.info(f"   ↳ error checking {stats_url}: {disc_err}")

            # ── 6. Fallback: use redirect directory + conventional path ──
            # e.g. http://ip/ints/agent/res/data_smscdr.php
            best_base          = candidate_bases[0]   # prefer agent-dir if found
            panel.api_url      = f"{best_base}/res/data_smscdr.php"
            panel.stats_url    = f"{best_base}/SMSCDRStats"
            panel.is_logged_in = True
            panel.fail_count   = 0
            logger.info(f"   📡 Fallback endpoint: {panel.api_url}")
            return True

    except aiohttp.ClientConnectorError as e:
        logger.error(f"🔌 Cannot connect to panel \"{panel.name}\": {e}")
    except asyncio.TimeoutError:
        logger.error(f"⏱  Connection timeout  panel=\"{panel.name}\"")
    except Exception as e:
        logger.error(f"❌ Login error  panel=\"{panel.name}\": {e}", exc_info=True)

    panel.fail_count += 1
    return False


async def fetch_panel_sms(panel: PanelSession) -> Optional[list]:
    if panel.panel_type == "api":
        try:
            s=await panel.get_session(); now=datetime.now(); prev=now-timedelta(days=1)
            params={"token":panel.token,"dt1":prev.strftime("%Y-%m-%d %H:%M:%S"),
                    "dt2":now.strftime("%Y-%m-%d %H:%M:%S"),"records":API_MAX_RECORDS}
            async with s.get(panel.base_url,params=params,timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status!=200:
                    panel.fail_count+=1
                    if panel.fail_count>=3: panel.is_logged_in=False
                    return None
                try: data=await resp.json(content_type=None)
                except Exception as je:
                    logger.error(f"API JSON '{panel.name}': {je}")
                    panel.fail_count+=1
                    if panel.fail_count>=3: panel.is_logged_in=False
                    return None
                records=[]
                if isinstance(data,list): records=data
                elif isinstance(data,dict):
                    st=str(data.get("status","")).lower()
                    if st=="error":
                        logger.error(f"API '{panel.name}' auth: {data.get('msg','')}")
                        panel.fail_count+=1
                        if panel.fail_count>=3: panel.is_logged_in=False
                        return None
                    records=(data.get("data") or data.get("records") or
                             data.get("sms") or data.get("messages") or [])
                panel.fail_count=0; panel.is_logged_in=True
                if not records: return []
                out=[]
                for rec in records:
                    if not isinstance(rec,dict): continue
                    dt =(rec.get("dt")      or rec.get("date")      or rec.get("timestamp") or "")
                    num=(rec.get("num")     or rec.get("number")    or rec.get("recipient") or rec.get("phone") or "")
                    cli=(rec.get("cli")     or rec.get("sender")    or rec.get("originator")or rec.get("service") or "unknown")
                    msg=(rec.get("message") or rec.get("text")      or rec.get("body")      or rec.get("content") or "")
                    if not msg and not num: continue
                    out.append([str(dt),str(num).replace("+","").strip(),str(cli).lower(),str(msg)])
                out.sort(key=lambda x:x[0],reverse=True)
                return out
        except Exception as e:
            logger.error(f"API fetch '{panel.name}': {e}")
            panel.fail_count+=1
            if panel.fail_count>=3: panel.is_logged_in=False
            return None
    elif panel.panel_type=="login":
        if not panel.api_url: return None
        try:
            s=await panel.get_session(); now=datetime.now(); prev=now-timedelta(days=1)
            params={"fdate1":prev.strftime("%Y-%m-%d %H:%M:%S"),"fdate2":now.strftime("%Y-%m-%d %H:%M:%S"),
                    "sEcho":"1","iDisplayStart":"0","iDisplayLength":"200","iSortCol_0":"0","sSortDir_0":"desc"}
            if panel.sesskey: params["sesskey"]=panel.sesskey
            # Use the discovered stats page URL as Referer (server validates this)
            _referer = panel.stats_url or f"{panel.base_url}/SMSCDRStats"
            headers={"X-Requested-With":"XMLHttpRequest",
                     "Referer": _referer,
                     "Accept":"application/json, text/javascript, */*; q=0.01"}
            async with s.get(panel.api_url,params=params,headers=headers,
                             timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status!=200:
                    panel.fail_count+=1
                    if panel.fail_count>=3: panel.is_logged_in=False
                    return None
                data=await resp.json(content_type=None)
                if "aaData" in data:
                    panel.fail_count=0; data["aaData"].sort(key=lambda x:str(x[0]),reverse=True)
                    return data["aaData"]
                panel.fail_count+=1
                if panel.fail_count>=3: panel.is_logged_in=False
                return None
        except Exception as e:
            logger.error(f"Login fetch '{panel.name}': {e}")
            panel.fail_count+=1
            if panel.fail_count>=3: panel.is_logged_in=False
            return None
    return None

# ═══════════════════════════════════════════════════════════
#  IVAS WORKER
# ═══════════════════════════════════════════════════════════
async def _ivas_ping(ws, ms: int):
    while True:
        await asyncio.sleep(ms/1000)
        try: await ws.send("3")
        except Exception: break

async def ivas_worker(panel: PanelSession):
    logger.info(f"📡 IVAS worker starting → \"{panel.name}\"")
    seen: set = set()
    while True:
        try:
            if panel.panel_type!="ivas" or not panel.uri: break
            ctx=ssl._create_unverified_context()
            try:
                async with websockets.connect(panel.uri,ssl=ctx) as ws:
                    logger.info(f"✅ IVAS \"{panel.name}\" connected — listening for SMS")
                    initial=await ws.recv(); ping_ms=25000
                    try:
                        if initial.startswith("0{"): ping_ms=json.loads(initial[1:]).get("pingInterval",25000)
                    except Exception: pass
                    await ws.send("40/livesms,")
                    pt=asyncio.create_task(_ivas_ping(ws,ping_ms))
                    try:
                        while True:
                            if panel.id is not None and panel.id not in [p.id for p in PANELS]: break
                            msg=await ws.recv()
                            if not msg.startswith("42/livesms,"): continue
                            try:
                                data=json.loads(msg[msg.find("["):])
                                if not (isinstance(data,list) and len(data)>1 and isinstance(data[1],dict)): continue
                                sms=data[1]
                                number=str(sms.get("recipient","")).replace("+","").strip()
                                body=str(sms.get("message","") or "")
                                service=str(sms.get("originator","") or "unknown")
                                otp=extract_otp_regex(body)
                                uniq=f"{number}-{body[:20]}"
                                if uniq in seen: continue
                                seen.add(uniq)
                                if len(seen)>500: seen.clear()
                                await process_incoming_sms(None,number,body,otp,service,panel.name)
                            except Exception as e: logger.error(f"IVAS parse '{panel.name}': {e}")
                    finally: pt.cancel()
            except websockets.exceptions.WebSocketException as e:
                logger.error(f"IVAS WS '{panel.name}': {e}. Retry 5s."); await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"IVAS err '{panel.name}': {e}. Retry 5s."); await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"IVAS crit '{panel.name}': {e}. Retry 10s."); await asyncio.sleep(10)

def handle_task_exception(task: asyncio.Task):
    try: task.result()
    except asyncio.CancelledError: pass
    except Exception as e: logger.error(f"Task '{task.get_name()}': {e}", exc_info=True)

async def start_ivas_workers():
    for panel in PANELS:
        if panel.panel_type=="ivas":
            task=asyncio.create_task(ivas_worker(panel),name=f"IVAS-{panel.name}")
            task.add_done_callback(handle_task_exception)
            IVAS_TASKS[panel.name]=task

# ═══════════════════════════════════════════════════════════
#  SMS PROCESSING
# ═══════════════════════════════════════════════════════════
async def process_incoming_sms(bot_app,num_raw:str,msg_body:str,
                                otp_code:Optional[str],service_name:str,panel_name:str):
    global app
    if bot_app is None: bot_app=app
    if otp_code and num_raw:
        append_otp(num_raw, otp_code)
    async with db.AsyncSessionLocal() as session:
        db_obj=(await session.execute(
            select(db.Number).where(db.Number.phone_number==num_raw)
        )).scalar_one_or_none()
        if db_obj and db_obj.assigned_to and db_obj.status in ("ASSIGNED","RETENTION"):
            await do_sms_hit(bot_app,db_obj,otp_code,msg_body,service_name,panel_name,num_raw,session)
        else:
            await log_unassigned(bot_app,num_raw,msg_body,otp_code,service_name,panel_name)

async def do_sms_hit(bot_app,db_obj,otp_code,msg_body,service_name,panel_name,num_raw,session):
    global app
    if bot_app is None: bot_app=app
    if bot_app is None: return
    db_obj.last_msg=msg_body
    if otp_code: db_obj.last_otp=otp_code
    cnt=OTP_SESSION_COUNTS.get(num_raw,0)
    if otp_code: cnt+=1; OTP_SESSION_COUNTS[num_raw]=cnt
    header={1:"✅ OTP RECEIVED",2:"🫟 2nd OTP",3:"🫂 3rd OTP"}.get(
        cnt,f"☠️ {cnt}th OTP" if cnt>3 else "📩 NEW MESSAGE")
    clean=re.sub(r"[^0-9]","",otp_code) if otp_code else ""
    kb=otp_keyboard(otp_code,msg_body)
    _,flag,region=get_country_info(num_raw)
    dial=get_country_code(num_raw) or ""; last5=get_last5(num_raw)
    svc=get_service_short(service_name)
    now_ts      = datetime.now().strftime("%H:%M:%S")
    count_badge = {1:"1️⃣ First OTP", 2:"2️⃣ Second OTP", 3:"3️⃣ Third OTP"}.get(
        cnt, f"🔢 OTP #{cnt}" if cnt > 0 else "📩 New SMS")

    # Use the selected OTP GUI theme (build_otp_msg dispatches by OTP_GUI_THEME)
    dm_txt  = build_otp_msg(header, count_badge, clean, msg_body,
                             svc, panel_name, flag, region, dial, last5,
                             for_group=False)
    grp_txt = build_otp_msg(header, count_badge, clean, msg_body,
                             svc, panel_name, flag, region, dial, last5,
                             for_group=True)

    dm_kb  = otp_keyboard(otp_code, msg_body, for_group=False)
    grp_kb = otp_keyboard(otp_code, msg_body, for_group=True)

    # ── DM to assigned user ───────────────────────────────
    if db_obj.assigned_to:
        try:
            await bot_app.bot.send_message(
                chat_id=db_obj.assigned_to, text=dm_txt,
                reply_markup=dm_kb, parse_mode="HTML")
        except TelegramForbidden:
            logger.warning(f"User {db_obj.assigned_to} blocked bot.")
        except Exception as e:
            logger.error(f"DM error ({db_obj.assigned_to}): {e}")

    # ── Log groups — compact reference format + 15-min auto-delete ──
    _DEL_SEC = 900   # 15 minutes
    for gid in await db.get_all_log_chats():
        try:
            sent = await bot_app.bot.send_message(
                chat_id=gid, text=grp_txt,
                reply_markup=grp_kb, parse_mode="HTML")
            # Schedule deletion
            if bot_app.job_queue:
                bot_app.job_queue.run_once(
                    _delete_msg_job, when=_DEL_SEC,
                    data={"chat_id": gid, "msg_id": sent.message_id},
                    name=f"del_{gid}_{sent.message_id}")
            else:
                asyncio.create_task(
                    _delete_msg_after(bot_app, gid, sent.message_id, _DEL_SEC))
        except TelegramForbidden:
            logger.error(f"Not in log group {gid}")
        except Exception as e:
            logger.error(f"Log group ({gid}): {e}")
    # ── Record & reassign ─────────────────────────────────
    if otp_code:
        await session.commit()
        cat,user_id,msg_id=await db.record_success(num_raw,otp_code)
        if user_id is None: return
        limit=await db.get_user_limit(user_id) or DEFAULT_ASSIGN_LIMIT
        await db.request_numbers(user_id,cat,count=limit,message_id=msg_id)
        active=await db.get_active_numbers(user_id)
        if active and msg_id:
            try:
                pfx=await db.get_user_prefix(user_id)
                pfx_txt=f"on-{pfx}" if pfx else "off"
                svc_lbl=(active[0].category.split(" - ")[1]
                         if " - " in active[0].category else active[0].category)
                lines=[]
                for idx,n in enumerate(active,1):
                    e=f"{idx}\uFE0F\u20E3" if idx<10 else ("🔟" if idx==10 else f"[{idx}]")
                    lines.append(f"{e} <code>+{n.phone_number}</code>")
                await bot_app.bot.edit_message_text(
                    chat_id=user_id,message_id=msg_id,
                    text=(f"🎉 <b>New Numbers Ready!</b>\n{D}\n"
                          f"🌍 <b>Service:</b> {html.escape(svc_lbl)}\n"
                          +"\n".join(lines)+
                          f"\n\n🔡 <b>Prefix:</b> {pfx_txt}\n⚡ <b>Waiting for SMS…</b>"),
                    reply_markup=waiting_kb(pfx,service=svc_lbl),parse_mode="HTML")
            except Exception as e: logger.error(f"Edit msg: {e}")
    else:
        session.add(db_obj); await session.commit()

async def log_unassigned(bot_app,num_raw,msg_body,otp_code,service_name,panel_name):
    global app
    if bot_app is None: bot_app=app
    if bot_app is None: return
    log_chats=await db.get_all_log_chats()
    if not log_chats: return
    _,flag,region=get_country_info(num_raw)
    dial=get_country_code(num_raw) or ""; last5=get_last5(num_raw)
    svc=get_service_short(service_name)
    clean=re.sub(r"[^0-9]","",otp_code) if otp_code else ""
    # Unassigned OTPs use "📩 UNASSIGNED" as the header in the current theme
    _ua_header = "📩 UNASSIGNED"
    _ua_badge  = ""
    txt = build_otp_msg(_ua_header, _ua_badge, clean, msg_body,
                        svc, panel_name, flag, region, dial, last5,
                        for_group=True)
    kb = otp_keyboard(otp_code, msg_body, for_group=True)
    _DEL_SEC2 = 900
    for gid in log_chats:
        try:
            sent = await bot_app.bot.send_message(chat_id=gid,text=txt,reply_markup=kb,parse_mode="HTML")
            if bot_app.job_queue:
                bot_app.job_queue.run_once(
                    _delete_msg_job, when=_DEL_SEC2,
                    data={"chat_id": gid, "msg_id": sent.message_id},
                    name=f"del_{gid}_{sent.message_id}")
            else:
                asyncio.create_task(_delete_msg_after(bot_app, gid, sent.message_id, _DEL_SEC2))
        except TelegramForbidden: logger.error(f"Not in log group {gid}")
        except Exception as e: logger.error(f"Log ({gid}): {e}")


# ═══════════════════════════════════════════════════════════
#  AUTO-DELETE HELPERS  (group messages removed after 15 min)
# ═══════════════════════════════════════════════════════════
async def _delete_msg_after(bot_app, chat_id: int, msg_id: int, delay_sec: int):
    """Coroutine: waits delay_sec then silently deletes the message."""
    await asyncio.sleep(delay_sec)
    try:
        await bot_app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        logger.info(f"🗑️  Auto-deleted group msg {msg_id} from {chat_id}")
    except Exception:
        pass   # already deleted or bot lacks permission — ignore

async def _delete_msg_job(context):
    """PTB job_queue callback for auto-delete."""
    d = context.job.data or {}
    try:
        await context.bot.delete_message(
            chat_id=d["chat_id"], message_id=d["msg_id"])
        logger.info(f"🗑️  Auto-deleted group msg {d['msg_id']} from {d['chat_id']}")
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════
#  ACTIVE WATCHER
# ═══════════════════════════════════════════════════════════
async def active_watcher(application):
    global app, PROCESSED_MESSAGES
    app = application
    logger.info(f"🚀 Active watcher started  ({len(PANELS)} panel(s) loaded)")

    # ── Initial login pass ────────────────────────────────────────
    # IMPORTANT: panels sharing the same base_url (same server, multiple
    # accounts) MUST login sequentially with a small gap.  Concurrent logins
    # on the same server cause the server to invalidate the earlier session
    # the moment the second one completes, leaving only one account active.
    # We group by base_url and login each group one account at a time.
    from collections import defaultdict as _dd
    login_groups = _dd(list)
    api_panels   = []
    for panel in PANELS:
        if panel.panel_type == "login":
            login_groups[panel.base_url].append(panel)
        elif panel.panel_type == "api":
            api_panels.append(panel)

    # Login panels — sequentially within each host group
    for host, group in login_groups.items():
        if len(group) > 1:
            logger.info(f"🔑 Logging in {len(group)} accounts on {host} (sequential to avoid session clash)")
        for panel in group:
            logger.info(f"🔑 Initial login → \"{panel.name}\"")
            ok = await login_to_panel(panel)
            if ok:
                if panel.id: await update_panel_login(panel.id, panel.sesskey, panel.api_url, True)
                logger.info(f"✅ \"{panel.name}\" ready  →  {panel.api_url}")
            else:
                logger.warning(f"⚠️  \"{panel.name}\" login failed, will retry each cycle")
            if len(group) > 1:
                await asyncio.sleep(1.5)  # give the server time between accounts

    # Initialise API panel sessions (no login needed, just a session object)
    for panel in api_panels:
        await panel.get_session()
        logger.info(f"🔌 API panel \"{panel.name}\" session ready")
    for gid in await db.get_all_log_chats():
        try: await application.bot.send_message(gid,"🚀 <b>OTP Engine Online</b>",parse_mode="HTML")
        except Exception: pass
    first_cycle = True
    while True:
        t0 = datetime.now()
        try:
            try: await db.clean_cooldowns()
            except Exception: pass
            async with db.AsyncSessionLocal() as session:
                from sqlalchemy import or_
                active_nums=(await session.execute(
                    select(db.Number).filter(
                        or_(db.Number.status=="ASSIGNED",db.Number.status=="RETENTION"))
                )).scalars().all()
                targets={n.phone_number:n for n in active_nums}

                async def fetch_one(panel):
                    try:
                        # ── Login / reconnect if needed ──────────────────────
                        if not panel.is_logged_in:
                            if panel.panel_type == "login":
                                logger.info(f"🔄 Re-logging in to \"{panel.name}\"…")
                                ok = await login_to_panel(panel)
                                if ok:
                                    await update_panel_login(
                                        panel.id or 0, panel.sesskey, panel.api_url, True)
                                    logger.info(f"✅ \"{panel.name}\" logged in, fetching SMS")
                                else:
                                    logger.warning(f"⏸  \"{panel.name}\" login failed, skipping cycle")
                                    return None, panel
                            elif panel.panel_type == "api":
                                ok = await test_api_panel(panel)
                                panel.is_logged_in = ok
                                if not ok:
                                    logger.warning(f"⏸  API \"{panel.name}\" unreachable, skipping cycle")
                                    return None, panel
                        # ── Fetch SMS ─────────────────────────────────────────
                        sms_list = await fetch_panel_sms(panel)
                        if sms_list is not None:
                            logger.info(
                                f"📥 \"{panel.name}\" → {len(sms_list)} record(s) fetched")
                        return sms_list, panel
                    except Exception as e:
                        logger.error(f"❌ Watcher error on \"{panel.name}\": {e}", exc_info=True)
                    return None, panel

                # Run panels sequentially to avoid same-host session collisions.
                # asyncio.gather ran them all at once; for panels sharing a host
                # this caused the second login to invalidate the first mid-fetch.
                results = []
                for p in PANELS:
                    if p.panel_type != "ivas":
                        results.append(await fetch_one(p))
                for sms_list,panel in results:
                    if not sms_list: continue
                    for rec in sms_list:
                        if len(rec)<4: continue
                        if panel.panel_type=="api":
                            dt_str=str(rec[0]); num_raw=str(rec[1]).replace("+","").strip()
                            svc_raw=str(rec[2]); msg_body=str(rec[3])
                        else:
                            dt_str=str(rec[0])
                            num_raw=str(rec[2]).replace("+","").strip() if len(rec)>2 else ""
                            svc_raw=str(rec[3]) if len(rec)>3 else "unknown"
                            msg_body=get_message_body(rec) or ""
                        if not msg_body or not num_raw: continue
                        msg_time=parse_panel_dt(dt_str)
                        if msg_time is None: continue
                        if (datetime.now()-msg_time).total_seconds()/60>MSG_AGE_LIMIT_MIN: continue
                        otp_code=extract_otp_regex(msg_body)
                        uid_str=hashlib.md5(f"{panel.base_url}-{dt_str}-{num_raw}-{msg_body}".encode()).hexdigest()
                        if uid_str in PROCESSED_MESSAGES: continue
                        PROCESSED_MESSAGES.add(uid_str); save_seen_hash(uid_str)
                        if first_cycle: continue
                        if num_raw in targets:
                            db_obj=targets[num_raw]
                            if db_obj.last_msg==msg_body: continue
                            await do_sms_hit(application,db_obj,otp_code,msg_body,svc_raw,panel.name,num_raw,session)
                        else:
                            await log_unassigned(application,num_raw,msg_body,otp_code,svc_raw,panel.name)
                first_cycle=False
        except Exception as e:
            logger.error(f"Watcher loop: {e}"); await asyncio.sleep(5)
        await asyncio.sleep(API_FETCH_INTERVAL)

# ═══════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    name = html.escape(update.effective_user.first_name)
    await db.add_user(uid)
    bot_name = f"@{BOT_USERNAME}" if BOT_USERNAME else "@PAKOTPBOT"
    perms = await get_admin_permissions(uid)
    role_line = ""
    if is_super_admin(uid): role_line = "\n👑 <b>Super Admin</b>"
    elif perms:             role_line = "\n👮 <b>Admin</b>"
    msg = (
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"💎 <b>SIGMA FETCHER V11</b>  |  🤖 {html.escape(bot_name)}\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"👋 Welcome, <a href='tg://user?id={uid}'><b>{name}</b></a>!{role_line}\n\n"
        "⚡ Real-Time OTP Delivery\n"
        "🧬 Multi-Panel + IVAS Support\n"
        "🔑 Auto-Assign Number System\n"
        "🚀 Get Numbers for Any Service\n\n"
        "👇 <b>Choose an option:</b>"
    )
    await update.message.reply_text(msg, reply_markup=main_menu_kb(), parse_mode="HTML")

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    perms= await get_admin_permissions(uid)
    sup  = is_super_admin(uid)
    if not perms and not sup:
        await update.message.reply_text(
            "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            "🚫 <b>Access Denied</b>\n"
            "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            "You are not authorised to access the admin panel.",
            parse_mode="HTML"); return
    role      = "👑 Super Admin" if sup else "👮 Admin"
    stats     = await db.get_stats()
    panel_cnt = len(PANELS)
    run_cnt   = len([p for p in PANELS if p.is_logged_in or
                     (p.panel_type=="ivas" and p.name in IVAS_TASKS
                      and not IVAS_TASKS[p.name].done())])
    # Build lines list — avoids f-string concatenation which caused the crash
    lines = [
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>",
        "🛡 <b>SIGMA ADMIN PANEL</b>",
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>",
        "",
        f"👤 {role}  |  🆔 <code>{uid}</code>",
        "",
        f"📱 Available:   <b>{stats.get('available',0)}</b>",
        f"🔌 Panels:      <b>{run_cnt}/{panel_cnt}</b> online",
    ]
    if not IS_CHILD_BOT:
        bot_cnt = len(bm.list_bots())
        lines.append(f"🤖 Child Bots:  <b>{bot_cnt}</b>")
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=admin_main_kb(perms, sup), parse_mode="HTML")

async def cmd_add_admin(u,c): await u.message.reply_text("Use Admin Panel → Admins.")
async def cmd_rm_admin(u,c):  await u.message.reply_text("Use Admin Panel → Admins.")
async def cmd_list_admins(u,c):
    admins=await list_all_admins()
    lines="\n".join(f"• <code>{a}</code>{'  👑' if a in INITIAL_ADMIN_IDS else ''}" for a in admins)
    await u.message.reply_text(f"👮 <b>Admins</b>\n\n{lines or 'None'}",parse_mode="HTML")

async def cmd_add_log(update,context):
    uid=update.effective_user.id; perms=await get_admin_permissions(uid)
    if "manage_logs" not in perms and not is_super_admin(uid):
        await update.message.reply_text("❌ No permission."); return
    if not context.args: await update.message.reply_text("Usage: /addlogchat <chat_id>"); return
    try:
        cid=int(context.args[0]); ok=await db.add_log_chat(cid)
        await update.message.reply_text(f"{'✅ Added' if ok else '⚠️ Exists'}: <code>{cid}</code>",parse_mode="HTML")
    except ValueError: await update.message.reply_text("❌ Invalid chat ID.")

async def cmd_rm_log(update,context):
    uid=update.effective_user.id; perms=await get_admin_permissions(uid)
    if "manage_logs" not in perms and not is_super_admin(uid):
        await update.message.reply_text("❌ No permission."); return
    if not context.args: await update.message.reply_text("Usage: /removelogchat <chat_id>"); return
    try:
        cid=int(context.args[0]); ok=await db.remove_log_chat(cid)
        await update.message.reply_text(f"{'✅ Removed' if ok else '❌ Not found'}: <code>{cid}</code>",parse_mode="HTML")
    except ValueError: await update.message.reply_text("❌ Invalid chat ID.")

async def cmd_list_logs(update,context):
    uid=update.effective_user.id; perms=await get_admin_permissions(uid)
    if "manage_logs" not in perms and not is_super_admin(uid):
        await update.message.reply_text("❌ No permission."); return
    chats=await db.get_all_log_chats()
    txt="📋 <b>Log Groups</b>\n\n"+"\n".join(f"• <code>{c}</code>" for c in chats) if chats else "📭 None."
    await update.message.reply_text(txt,parse_mode="HTML")

async def cmd_dox(update,context):
    uid=update.effective_user.id
    if uid not in INITIAL_ADMIN_IDS: await update.message.reply_text("❌ Unauthorized."); return
    if len(context.args)<2: await update.message.reply_text("Usage: /dox <amount> <on/off>"); return
    try:
        lim=int(context.args[0]); st=context.args[1].lower()
        if st=="on":  await db.set_user_limit(uid,lim);  await update.message.reply_text(f"✅ Limit→{lim}")
        elif st=="off": await db.set_user_limit(uid,None); await update.message.reply_text("✅ Dox OFF")
        else: await update.message.reply_text("Usage: /dox <amount> <on/off>")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def cmd_otpfor(update,context):
    if not context.args: await update.message.reply_text("Usage: /otpfor <phone>"); return
    target=context.args[0].replace("+","").strip()
    found=next((otp for num,otp in load_otp_store().items() if target in num),None)
    if found:
        await update.message.reply_text(
            f"🔑 OTP for <code>{target}</code>: <b>{found}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"📋 Copy OTP: {found}", copy_text=CopyTextButton(text=found))]]),
            parse_mode="HTML")
    else:
        await update.message.reply_text(f"❌ No OTP for <code>{target}</code>.",parse_mode="HTML")

async def cmd_set_channel(update,context):
    uid=update.effective_user.id
    if not is_super_admin(uid): await update.message.reply_text("❌ Unauthorized."); return
    if not context.args: await update.message.reply_text("Usage: /set_channel <url>"); return
    global CHANNEL_LINK; CHANNEL_LINK=context.args[0]
    save_config_key("CHANNEL_LINK",CHANNEL_LINK)
    await update.message.reply_text(f"✅ Channel → {CHANNEL_LINK}")

async def cmd_set_otpgroup(update,context):
    uid=update.effective_user.id
    if not is_super_admin(uid): await update.message.reply_text("❌ Unauthorized."); return
    if not context.args: await update.message.reply_text("Usage: /set_otpgroup <url>"); return
    global OTP_GROUP_LINK; OTP_GROUP_LINK=context.args[0]
    save_config_key("OTP_GROUP_LINK",OTP_GROUP_LINK)
    await update.message.reply_text(f"✅ OTP Group → {OTP_GROUP_LINK}")

async def cmd_set_numbot(update,context):
    uid=update.effective_user.id
    if not is_super_admin(uid): await update.message.reply_text("❌ Unauthorized."); return
    if not context.args: await update.message.reply_text("Usage: /set_numberbot <url>"); return
    global NUMBER_BOT_LINK; NUMBER_BOT_LINK=context.args[0]
    save_config_key("NUMBER_BOT_LINK",NUMBER_BOT_LINK)
    await update.message.reply_text(f"✅ Number Bot → {NUMBER_BOT_LINK}")

async def cmd_groups(u,c): await cmd_list_logs(u,c)
async def cmd_addgrp(u,c): await cmd_add_log(u,c)
async def cmd_rmgrp(u,c):  await cmd_rm_log(u,c)

async def cmd_bots(update,context):
    uid=update.effective_user.id
    if not is_super_admin(uid): await update.message.reply_text("❌ Super admin only."); return
    if IS_CHILD_BOT: await update.message.reply_text("ℹ️ Not available on child bots."); return
    bots=bm.list_bots()
    if not bots: await update.message.reply_text("🤖 No bots registered yet."); return
    lines=[f"{'🟢' if b.get('running') else '🔴'} <b>{html.escape(b['name'])}</b>  <code>{b['id']}</code>" for b in bots]
    await update.message.reply_text(f"🖥 <b>Child Bots ({len(bots)})</b>\n\n"+"\n".join(lines),
                                    reply_markup=bots_list_kb(bots),parse_mode="HTML")

async def cmd_startbot(u,c):
    uid=u.effective_user.id
    if not is_super_admin(uid): await u.message.reply_text("❌ Unauthorized."); return
    if not c.args: await u.message.reply_text("Usage: /startbot <id>"); return
    ok,msg=bm.start_bot(c.args[0]); await u.message.reply_text(f"{'✅' if ok else '❌'} {msg}")

async def cmd_stopbot(u,c):
    uid=u.effective_user.id
    if not is_super_admin(uid): await u.message.reply_text("❌ Unauthorized."); return
    if not c.args: await u.message.reply_text("Usage: /stopbot <id>"); return
    ok,msg=bm.stop_bot(c.args[0]); await u.message.reply_text(f"{'✅' if ok else '❌'} {msg}")

async def cmd_test1(update,context):
    uid=update.effective_user.id
    try:
        num=random.choice(TEST_NUMBERS); cat="🇺🇸 USA - TestService"
        await db.release_number(uid)
        async with db.AsyncSessionLocal() as session:
            obj=(await session.execute(select(db.Number).where(db.Number.phone_number==num))).scalar_one_or_none()
            if not obj: obj=db.Number(phone_number=num,category=cat,status="AVAILABLE"); session.add(obj)
            obj.status="ASSIGNED"; obj.assigned_to=uid; obj.assigned_at=datetime.now()
            obj.category=cat; obj.last_msg=None; obj.last_otp=None
            await session.commit()
        pfx=await db.get_user_prefix(uid)
        msg=await context.bot.send_message(chat_id=uid,
            text=f"🎉 <b>Test Number</b>\n{D}\n📱 <code>+{num}</code>\n\nUse /send1 to simulate OTP.",
            reply_markup=waiting_kb(pfx),parse_mode="HTML")
        await db.update_message_id(num,msg.message_id)
    except Exception as e: await update.message.reply_text(f"❌ /test1: {e}")

async def cmd_send1(update,context):
    uid=update.effective_user.id
    async with db.AsyncSessionLocal() as session:
        obj=(await session.execute(
            select(db.Number).where(db.Number.assigned_to==uid,db.Number.status=="ASSIGNED")
        )).scalars().first()
        if not obj: await update.message.reply_text("❌ No active number. Use /test1."); return
        otp=str(random.randint(100000,999999))
        await do_sms_hit(context.application,obj,otp,f"Telegram code: {otp}. Do not share.",
                         "TELEGRAM","TEST-PANEL",obj.phone_number,session)
        await update.message.reply_text(f"✅ Simulated OTP <b>{otp}</b>",parse_mode="HTML")


# ═══════════════════════════════════════════════════════════
#  DOCUMENT UPLOAD
# ═══════════════════════════════════════════════════════════
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; perms=await get_admin_permissions(uid)
    if "manage_files" not in perms and not is_super_admin(uid):
        await update.message.reply_text("❌ No permission."); return
    doc=update.message.document
    if not doc or not doc.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Send a <code>.txt</code> file.",parse_mode="HTML"); return
    f=await doc.get_file(); path=doc.file_name
    await f.download_to_drive(path)
    try:
        lines=[l.strip() for l in open(path).readlines() if l.strip()]
        if not lines: await update.message.reply_text("❌ File empty."); os.remove(path); return
        country,flag=detect_country_from_numbers(lines)
        context.user_data.update({"upload_path":path,"upload_country":country,
                                   "upload_flag":flag,"upload_count":len(lines),"upload_svcs":[]})
        await update.message.reply_text(
            f"📂 <b>File Received</b>\n{D}\n🔢 <b>{len(lines)}</b> numbers\n"
            f"🌍 Detected: {flag} <b>{country}</b>\n\nSelect services:",
            reply_markup=svc_sel_kb(),parse_mode="HTML")
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

# ═══════════════════════════════════════════════════════════
#  TEXT INPUT HANDLER
# ═══════════════════════════════════════════════════════════
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    user_text=update.message.text

    # ── Panel Edit Flow ──────────────────────────────────
    if uid in PANEL_EDIT_STATES:
        st=PANEL_EDIT_STATES[uid]; step=st["step"]; d=st["data"]; pid=st["panel_id"]
        if user_text=="/cancel": del PANEL_EDIT_STATES[uid]; await update.message.reply_text("❌ Cancelled."); return
        if step=="name": d["name"]=user_text; st["step"]="url"; await update.message.reply_text(f"URL now: {d['base_url']}\nNew URL (/skip):")
        elif step=="url":
            if user_text.lower()!="/skip": d["base_url"]=user_text
            if d["panel_type"]=="login": st["step"]="username"; await update.message.reply_text(f"User: {d['username']}\nNew (/skip):")
            elif d["panel_type"]=="api": st["step"]="token"; await update.message.reply_text("New token (/skip):")
            else: st["step"]="uri"; await update.message.reply_text("New URI (/skip):")
        elif step=="username":
            if user_text.lower()!="/skip": d["username"]=user_text
            st["step"]="password"; await update.message.reply_text("New password (/skip):")
        elif step=="password":
            if user_text.lower()!="/skip": d["password"]=user_text
            await update_panel_in_db(pid,d["name"],d["base_url"],d.get("username"),d.get("password"),d["panel_type"],d.get("token"),d.get("uri"))
            await refresh_panels_from_db(); del PANEL_EDIT_STATES[uid]; await update.message.reply_text("✅ Panel updated!")
        elif step=="token":
            if user_text.lower()!="/skip": d["token"]=user_text
            await update_panel_in_db(pid,d["name"],d["base_url"],None,None,d["panel_type"],d.get("token"),None)
            await refresh_panels_from_db(); del PANEL_EDIT_STATES[uid]; await update.message.reply_text("✅ API panel updated!")
        elif step=="uri":
            if user_text.lower()!="/skip": d["uri"]=user_text
            await update_panel_in_db(pid,d["name"],d["base_url"],None,None,d["panel_type"],None,d.get("uri"))
            await refresh_panels_from_db(); del PANEL_EDIT_STATES[uid]; await update.message.reply_text("✅ IVAS panel updated!")
        return

    # ── Panel Add Flow ───────────────────────────────────
    if uid in PANEL_ADD_STATES:
        st=PANEL_ADD_STATES[uid]; step=st["step"]; d=st["data"]
        if user_text=="/cancel": del PANEL_ADD_STATES[uid]; await update.message.reply_text("❌ Cancelled."); return
        if step=="name": d["name"]=user_text; st["step"]="type"; await update.message.reply_text("Select panel type:",reply_markup=ptype_kb())
        elif step=="url":
            d["base_url"]=user_text
            pt=d["panel_type"]
            if pt=="login": st["step"]="username"; await update.message.reply_text("Enter username:")
            elif pt=="api": st["step"]="token"; await update.message.reply_text("Enter API token:")
            else: st["step"]="uri"; await update.message.reply_text("Paste IVAS URI (wss://...):")
        elif step=="username": d["username"]=user_text; st["step"]="password"; await update.message.reply_text("Enter password:")
        elif step=="password":
            await add_panel_to_db(d["name"],d["base_url"],d["username"],user_text,"login")
            await refresh_panels_from_db(); del PANEL_ADD_STATES[uid]; await update.message.reply_text("✅ Login panel added!")
        elif step=="token":
            await add_panel_to_db(d["name"],d["base_url"],None,None,"api",token=user_text.strip())
            await refresh_panels_from_db(); del PANEL_ADD_STATES[uid]; await update.message.reply_text("✅ API panel added!")
        elif step=="uri":
            await add_panel_to_db(d["name"],d.get("base_url",""),None,None,"ivas",uri=user_text.strip())
            await refresh_panels_from_db()
            panel=next((p for p in PANELS if p.name==d["name"]),None)
            if panel:
                task=asyncio.create_task(ivas_worker(panel),name=f"IVAS-{d['name']}")
                task.add_done_callback(handle_task_exception); IVAS_TASKS[d["name"]]=task
            del PANEL_ADD_STATES[uid]; await update.message.reply_text("✅ IVAS panel added and worker started!")
        return

    # ── Add Admin ID ─────────────────────────────────────
    if uid in AWAITING_ADMIN_ID:
        if not is_super_admin(uid): del AWAITING_ADMIN_ID[uid]; await update.message.reply_text("❌ Unauthorized."); return
        del AWAITING_ADMIN_ID[uid]
        try:
            new_a=int(user_text.strip())
            if new_a in INITIAL_ADMIN_IDS: await update.message.reply_text("❌ Already super admin."); return
            AWAITING_PERMISSIONS[(uid,new_a)]=[]
            await update.message.reply_text(
                f"✅ User <code>{new_a}</code>. Select permissions:",
                reply_markup=perms_kb([],new_a),parse_mode="HTML")
        except ValueError: await update.message.reply_text("❌ Invalid user ID.")
        return

    # ── Add Log Group ID ─────────────────────────────────
    if uid in AWAITING_LOG_ID:
        perms=await get_admin_permissions(uid)
        if "manage_logs" not in perms and not is_super_admin(uid):
            del AWAITING_LOG_ID[uid]; await update.message.reply_text("❌ Unauthorized."); return
        del AWAITING_LOG_ID[uid]
        try:
            cid=int(user_text.strip()); ok=await db.add_log_chat(cid)
            await update.message.reply_text(
                f"{'✅ Added' if ok else '⚠️ Exists'}: <code>{cid}</code>",parse_mode="HTML")
        except ValueError: await update.message.reply_text("❌ Invalid chat ID.")
        return

    # ── Config Link Prompts ──────────────────────────────
    if context.user_data.get("awaiting_link"):
        link_key=context.user_data.pop("awaiting_link")
        global CHANNEL_LINK, OTP_GROUP_LINK, NUMBER_BOT_LINK, SUPPORT_USER
        val=user_text.strip()
        if link_key=="CHANNEL_LINK":    CHANNEL_LINK=val
        elif link_key=="OTP_GROUP_LINK": OTP_GROUP_LINK=val
        elif link_key=="NUMBER_BOT_LINK": NUMBER_BOT_LINK=val
        elif link_key=="SUPPORT_USER":   SUPPORT_USER=val
        elif link_key=="DEVELOPER":      DEVELOPER=val
        save_config_key(link_key,val)
        if link_key == "FIND_OTP":
            target = val.replace("+","").strip()
            found_otp = next((otp for num,otp in load_otp_store().items() if target in num), None)
            if found_otp:
                await update.message.reply_text(
                    f"🔑 OTP for <code>{target}</code>\n\n"
                    f"<code>{found_otp}</code>",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"📋 Copy: {found_otp}",
                                             copy_text=CopyTextButton(text=found_otp)),
                        InlineKeyboardButton("🔙 Back",callback_data="admin_otp_tools")]]),
                    parse_mode="HTML")
            else:
                await update.message.reply_text(
                    f"❌ No OTP found for <code>{target}</code>.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back",callback_data="admin_otp_tools")]]),
                    parse_mode="HTML")
            return
        label={"CHANNEL_LINK":"Channel","OTP_GROUP_LINK":"OTP Group",
               "NUMBER_BOT_LINK":"Number Bot","SUPPORT_USER":"Support",
               "DEVELOPER":"Developer"}.get(link_key,link_key)
        await update.message.reply_text(
            f"✅ {label} updated to:\n<code>{html.escape(val)}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_links")]]))
        return

    # ── Prefix Setting ───────────────────────────────────
    if context.user_data.get("awaiting_prefix"):
        cat=context.user_data.pop("prefix_cat",None)
        context.user_data["awaiting_prefix"]=False
        if user_text.lower()=="off":
            await db.set_user_prefix(uid,None); await update.message.reply_text("✅ Prefix disabled.")
        else:
            cnt=await db.check_prefix_availability(cat,user_text)
            if cnt>0:
                await db.set_user_prefix(uid,user_text)
                await update.message.reply_text(
                    f"✅ Prefix <code>{user_text}</code> set. {cnt} numbers match.",parse_mode="HTML")
                await db.release_number(uid)
                limit=await db.get_user_limit(uid) or DEFAULT_ASSIGN_LIMIT
                phones,_,_=await db.request_numbers(uid,cat,count=limit)
                if phones:
                    active=await db.get_active_numbers(uid)
                    svc=(active[0].category.split(" - ")[1] if active and " - " in active[0].category else cat)
                    lines=[f"{i+1}. <code>+{n.phone_number}</code>" for i,n in enumerate(active)]
                    msg=await context.bot.send_message(chat_id=uid,
                        text=f"🎉 <b>New Numbers</b>\n{D}\n"+"\n".join(lines)+"\n\n⚡ Waiting…",
                        reply_markup=waiting_kb(user_text,service=svc),parse_mode="HTML")
                    for n in active: await db.update_message_id(n.phone_number,msg.message_id)
            else:
                await update.message.reply_text(f"❌ No numbers with prefix <code>{user_text}</code>.",parse_mode="HTML")
        return

    # ── Child Bot Link Edit Flow ─────────────────────────
    if context.user_data.get("bot_setlink_bid") and not IS_CHILD_BOT:
        bid  = context.user_data.pop("bot_setlink_bid", None)
        key  = context.user_data.pop("bot_setlink_key", None)
        if not is_super_admin(uid):
            await update.message.reply_text("❌ Unauthorized."); return
        if user_text == "/cancel":
            await update.message.reply_text("❌ Cancelled."); return
        val = user_text.strip()
        # Update the registry entry
        reg = bm.load_registry()
        key_map = {
            "CHANNEL_LINK":    "channel_link",
            "OTP_GROUP_LINK":  "otp_group_link",
            "NUMBER_BOT_LINK": "number_bot_link",
            "SUPPORT_USER":    "support_user",
        }
        reg_key = key_map.get(key, key.lower())
        if bid and bid in reg:
            reg[bid][reg_key] = val
            bm.save_registry(reg)
            # Also update the config.json inside the child folder
            try:
                folder = reg[bid].get("folder", "")
                cfg_path = os.path.join(folder, "config.json")
                if os.path.exists(cfg_path):
                    with open(cfg_path) as f: child_cfg = json.load(f)
                    child_cfg[key] = val
                    with open(cfg_path, "w") as f: json.dump(child_cfg, f, indent=2)
            except Exception as e:
                logger.error(f"Child config update: {e}")
        label_map = {"CHANNEL_LINK":"Channel","OTP_GROUP_LINK":"OTP Group","NUMBER_BOT_LINK":"Number Bot","SUPPORT_USER":"Support"}
        await update.message.reply_text(
            f"✅ <b>{label_map.get(key,key)}</b> updated for bot <code>{bid}</code>\n"
            f"New value: <code>{html.escape(val)}</code>\n\n"
            "<i>Restart the child bot to apply changes.</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Bot", callback_data=f"bot_info_{bid}")]]),
            parse_mode="HTML")
        return

    # ── Multi-Bot Add Flow ───────────────────────────────
    if uid in BOT_ADD_STATES and not IS_CHILD_BOT:
        st=BOT_ADD_STATES[uid]; step=st["step"]; d=st["data"]
        if user_text=="/cancel": del BOT_ADD_STATES[uid]; await update.message.reply_text("❌ Bot creation cancelled."); return
        steps={
            "name":        ("token",       "🔑 Now send the <b>Bot Token</b>\n<i>Get from @BotFather → /newbot</i>"),
            "token":       ("username",    "🤖 Send the <b>Bot Username</b> (e.g. @MyOTPBot)\n<i>No need for @ symbol</i>"),
            "username":    ("admin_id",    "👤 Send the <b>Admin Telegram ID</b>\n<i>Numeric ID — use @userinfobot</i>"),
            "admin_id":    ("channel",     "📢 Send the <b>Channel Link</b> (https://t.me/...)\nor /skip to leave blank"),
            "channel":     ("otp_group",   "💬 Send the <b>OTP Group Link</b> (https://t.me/...)\nor /skip to leave blank"),
            "otp_group":   ("numbot",      "📞 Send the <b>Number Bot Link</b> (https://t.me/...)\nor /skip to leave blank"),
            "numbot":      ("support",     "🛟 Send the <b>Support Username</b> (e.g. @support)\nor /skip to leave blank"),
            "support":     ("developer",   "🧠 Send the <b>Developer Username</b> (e.g. @dev)\nor /skip to leave blank"),
            "developer":   (None,          ""),
        }
        if step=="token":
            if not re.match(r'^\d+:[A-Za-z0-9_-]{35,}$',user_text.strip()):
                await update.message.reply_text(
                    "❌ Invalid token format.\nExpected: <code>123456:ABCxyz...</code>\nTry again or /cancel.",
                    parse_mode="HTML"); return
        if step=="admin_id":
            try: d["admin_ids"]=[int(user_text.strip())]
            except ValueError: await update.message.reply_text("❌ Must be a numeric ID."); return
        elif step not in ("admin_id",):
            val="" if user_text.strip()=="/skip" else user_text.strip()
            d[step]=val

        nxt,prompt=steps.get(step,(None,""))
        if nxt:
            st["step"]=nxt
            await update.message.reply_text(prompt,parse_mode="HTML")
        else:
            # All steps done — create the bot
            await update.message.reply_text("⏳ Creating bot folder and files…")
            bot_id=str(uuid.uuid4())[:8]
            ok,folder,err=bm.create_bot_folder(
                bot_id=bot_id, name=d.get("name",""),
                token=d.get("token",""), bot_username=d.get("username",""),
                admin_ids=d.get("admin_ids",[uid]),
                channel_link=d.get("channel",""), otp_group_link=d.get("otp_group",""),
                number_bot_link=d.get("numbot",""), support_user=d.get("support","@ownersigma"),
                developer=d.get("developer","@NONEXPERTCODER"),
                get_number_url=d.get("numbot","https://t.me/PakOTPBOT"))
            del BOT_ADD_STATES[uid]
            if ok:
                start_ok,start_msg=bm.start_bot(bot_id)
                st_icon="🟢" if start_ok else "🔴"
                await update.message.reply_text(
                    f"✅ <b>Bot Created Successfully!</b>\n{D}\n"
                    f"🤖 <b>Name:</b>     {html.escape(d.get('name',''))}\n"
                    f"👤 <b>Username:</b> @{html.escape(d.get('username',''))}\n"
                    f"🆔 <b>Bot ID:</b>   <code>{bot_id}</code>\n"
                    f"📁 <b>Folder:</b>   <code>{html.escape(folder)}</code>\n"
                    f"📢 <b>Channel:</b>  {html.escape(d.get('channel','—') or '—')}\n"
                    f"💬 <b>OTP Group:</b>{html.escape(d.get('otp_group','—') or '—')}\n"
                    f"{st_icon} <b>Status:</b>  {start_msg}\n\n"
                    f"<i>The bot runs independently with its own database.</i>",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🖥  Manage Bots",callback_data="admin_bots")
                    ]]),parse_mode="HTML")
            else:
                await update.message.reply_text(f"❌ Failed: {err}")
        return

    # ── Broadcast Flow ───────────────────────────────────
    if context.user_data.get("awaiting_broadcast"):
        perms=await get_admin_permissions(uid)
        if "broadcast" not in perms and not is_super_admin(uid):
            context.user_data["awaiting_broadcast"]=False; await update.message.reply_text("❌ Unauthorized."); return
        context.user_data["awaiting_broadcast"]=False
        bcast_text=user_text
        all_users=await db.get_all_users()
        total=len(all_users); sent=0; failed=0
        sm=await context.bot.send_message(
            chat_id=uid,
            text=f"📢 <b>Broadcasting to {total} users…</b>\n\n{pbar(0,max(total,1))}\nStarting…",
            parse_mode="HTML")
        for target in all_users:
            try:
                await context.bot.send_message(chat_id=target,
                    text=f"📢 <b>Announcement</b>\n{D}\n{bcast_text}",parse_mode="HTML")
                sent+=1
            except TelegramForbidden: failed+=1
            except Exception: failed+=1
            if (sent+failed)%10==0 or (sent+failed)==total:
                try:
                    await sm.edit_text(
                        f"📢 Broadcasting…\n\n{pbar(sent+failed,max(total,1))}\n✅{sent} ❌{failed}",
                        parse_mode="HTML")
                except Exception: pass
            await asyncio.sleep(0.04)
        try:
            await sm.edit_text(
                f"✅ <b>Broadcast Done</b>\n\n{pbar(total,max(total,1))}\n✅{sent} ❌{failed}",
                parse_mode="HTML")
        except Exception: pass
        await context.bot.send_message(uid,"Done.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_home")]]))
        return

    # ── Broadcast to ALL bots users (master + all child bots) ──
    if context.user_data.get("bcast_all_bots") and not IS_CHILD_BOT:
        context.user_data.pop("bcast_all_bots", None)
        perms_check = await get_admin_permissions(uid)
        if not is_super_admin(uid):
            await update.message.reply_text("❌ Unauthorized."); return
        bcast_text = user_text
        # Collect all users from this master bot
        master_users = await db.get_all_users()
        # Collect users from each child bot's database
        all_targets = list(master_users)
        child_dbs_users = []
        bots_reg = bm.load_registry()
        for bid, info in bots_reg.items():
            folder = info.get("folder","")
            child_db = os.path.join(folder, "bot_database.db")
            if os.path.exists(child_db):
                try:
                    import sqlite3 as _sq
                    conn = _sq.connect(child_db)
                    rows = conn.execute("SELECT user_id FROM users").fetchall()
                    conn.close()
                    child_dbs_users.extend([r[0] for r in rows])
                except Exception: pass
        # Deduplicate
        all_targets = list(set(all_targets + child_dbs_users))
        total = len(all_targets); sent = 0; failed = 0
        sm = await context.bot.send_message(
            chat_id=uid,
            text=f"📢 <b>Broadcasting to ALL bots: {total} users…</b>",
            parse_mode="HTML")
        for target in all_targets:
            try:
                await context.bot.send_message(
                    chat_id=target,
                    text=f"📢 <b>Announcement</b>\n{D}\n{bcast_text}",
                    parse_mode="HTML")
                sent += 1
            except (TelegramForbidden, Exception): failed += 1
            if (sent+failed) % 20 == 0:
                try:
                    await sm.edit_text(
                        f"📢 Broadcasting all bots…\n{pbar(sent+failed,max(total,1))}\n✅{sent} ❌{failed}",
                        parse_mode="HTML")
                except Exception: pass
            await asyncio.sleep(0.04)
        try:
            await sm.edit_text(
                f"✅ <b>All-Bots Broadcast Done</b>\n{pbar(total,max(total,1))}\n"
                f"✅ Sent: {sent}  ❌ Failed: {failed}\n"
                f"📊 Total unique users: {total}",
                parse_mode="HTML")
        except Exception: pass
        return

    await update.message.reply_text("Use /start to see the menu.")


# ═══════════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ═══════════════════════════════════════════════════════════
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global DEFAULT_ASSIGN_LIMIT, CHANNEL_LINK, OTP_GROUP_LINK, NUMBER_BOT_LINK, SUPPORT_USER
    global OTP_GUI_THEME, GUI_STYLE
    query=update.callback_query; data=query.data; uid=query.from_user.id

    if data=="ignore": await query.answer(); return

    if data=="main_menu":
        await query.answer()
        await query.edit_message_text("🏠 <b>Main Menu</b>",reply_markup=main_menu_kb(),parse_mode="HTML")
        return

    if data=="profile":
        await query.answer()
        stats=await db.get_user_stats(uid)
        active=await db.get_active_numbers(uid)
        perms=await get_admin_permissions(uid)
        role="👑 Super Admin" if is_super_admin(uid) else ("👮 Admin" if perms else "👤 User")
        ai=""
        if active: ai="\n📱 "+", ".join(f"<code>+{n.phone_number}</code>" for n in active)
        await query.edit_message_text(
            f"🫁 <b>USER PROFILE</b>\n{D}\n"
            f"🆔 <code>{uid}</code>\n👤 {html.escape(query.from_user.first_name)}\n"
            f"🎭 {role}\n{D}\n"
            f"✅ OTPs: <b>{stats['success']}</b>\n"
            f"🔄 Total: <b>{stats['total']}</b>{ai}",
            reply_markup=main_menu_kb(),parse_mode="HTML")
        return

    if data=="buy_menu":
        await query.answer()
        svcs=await db.get_distinct_services()
        if not svcs:
            await query.edit_message_text(
                "🚫 <b>No Services Available</b>\nAsk admin to upload numbers.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="main_menu")]]),
                parse_mode="HTML")
        else:
            await query.edit_message_text(
                f"📱 <b>Select Service</b>\n{D}",reply_markup=services_kb(svcs),parse_mode="HTML")
        return

    if data.startswith("svc_"):
        svc=data[4:]; await query.answer()
        countries=await db.get_countries_for_service(svc)
        if not countries:
            await query.edit_message_text(f"🚫 No countries for <b>{svc}</b>.",
                reply_markup=services_kb(await db.get_distinct_services()),parse_mode="HTML")
        else:
            await query.edit_message_text(f"🌍 <b>Select Country</b> — {svc}\n{D}",
                reply_markup=countries_kb(svc,countries),parse_mode="HTML")
        return

    if data.startswith("cntry|"):
        _,svc,country=data.split("|",2); await query.answer()
        await db.set_user_prefix(uid,None)
        clist=await db.get_countries_for_service(svc)
        flag=next((f for f,c in clist if c==country),"🌍")
        category=f"{flag} {country} - {svc}"
        limit=await db.get_user_limit(uid) or DEFAULT_ASSIGN_LIMIT
        active=await db.get_active_numbers(uid)
        if active and active[0].category!=category: await db.release_number(uid); active=[]
        if len(active)<limit:
            await db.request_numbers(uid,category,count=limit-len(active))
            active=await db.get_active_numbers(uid)
        if active:
            try: await query.message.delete()
            except Exception: pass
            pfx=await db.get_user_prefix(uid)
            lines=[f"{i+1}\uFE0F\u20E3 <code>+{n.phone_number}</code>" for i,n in enumerate(active)]
            msg=await context.bot.send_message(chat_id=uid,
                text=(f"🎉 <b>Numbers Assigned!</b>\n{D}\n"
                      f"🌍 <b>Service:</b> {svc} {flag}\n\n"+"\n".join(lines)+
                      "\n\n⚡ <b>Waiting for SMS…</b>"),
                reply_markup=waiting_kb(pfx,service=svc),parse_mode="HTML")
            for n in active: await db.update_message_id(n.phone_number,msg.message_id)
        else:
            await context.bot.send_message(uid,f"❌ <b>Out of Stock</b> — {svc} / {country}",parse_mode="HTML")
        return

    if data=="change_country":
        active=await db.get_active_numbers(uid)
        if not active: await query.answer("No active number.",show_alert=True); return
        svc=active[0].category.split(" - ")[1] if " - " in active[0].category else active[0].category
        countries=await db.get_countries_for_service(svc)
        if not countries: await query.answer("No other countries.",show_alert=True); return
        await query.answer()
        await query.edit_message_text(f"🌍 <b>Select Country</b> — {svc}",
            reply_markup=countries_kb(svc,countries),parse_mode="HTML")
        return

    if data=="skip_next":
        now_=datetime.now(); last=LAST_CHANGE_TIME.get(uid)
        if last and (now_-last).total_seconds()<CHANGE_COOLDOWN_S:
            await query.answer(f"⏳ Wait {CHANGE_COOLDOWN_S-int((now_-last).total_seconds())}s",show_alert=True); return
        LAST_CHANGE_TIME[uid]=now_; await query.answer()
        ok,cat=await db.release_number(uid)
        if ok and cat:
            limit=await db.get_user_limit(uid) or DEFAULT_ASSIGN_LIMIT
            await db.request_numbers(uid,cat,count=limit)
            active=await db.get_active_numbers(uid)
            if active:
                try: await query.message.delete()
                except Exception: pass
                svc=active[0].category.split(" - ")[1] if " - " in active[0].category else cat
                pfx=await db.get_user_prefix(uid)
                lines=[f"{i+1}. <code>+{n.phone_number}</code>" for i,n in enumerate(active)]
                msg=await context.bot.send_message(chat_id=uid,
                    text=f"🔄 <b>New Numbers</b>\n{D}\n"+"\n".join(lines)+"\n\n⚡ Waiting…",
                    reply_markup=waiting_kb(pfx,service=svc),parse_mode="HTML")
                for n in active: await db.update_message_id(n.phone_number,msg.message_id)
            else:
                await query.edit_message_text("❌ Out of stock.",reply_markup=main_menu_kb(),parse_mode="HTML")
        else: await query.answer("No active number.",show_alert=True)
        return

    if data=="ask_block":
        await query.answer()
        await query.edit_message_text(
            f"⚠️ <b>Block This Number?</b>\n{D}\nPermanently removed — no one can use it again.",
            reply_markup=confirm_block_kb(),parse_mode="HTML")
        return

    if data=="block_no":
        await query.answer()
        active=await db.get_active_numbers(uid)
        if active:
            pfx=await db.get_user_prefix(uid)
            svc=active[0].category.split(" - ")[1] if " - " in active[0].category else active[0].category
            lines=[f"{i+1}. <code>+{n.phone_number}</code>" for i,n in enumerate(active)]
            await query.edit_message_text("✅ Kept.\n\n"+"\n".join(lines)+"\n\n⚡ Waiting…",
                reply_markup=waiting_kb(pfx,service=svc),parse_mode="HTML")
        else:
            await query.edit_message_text("No active number.",reply_markup=main_menu_kb(),parse_mode="HTML")
        return

    if data=="block_yes":
        await query.answer(); await query.edit_message_text("⏳ Blocking…")
        ok,cat=await db.block_number(uid)
        if ok and cat:
            svc=cat.split(" - ")[1] if " - " in cat else cat
            cntrs=await db.get_countries_for_service(svc)
            if cntrs:
                await query.edit_message_text(f"✅ Blocked. Select new country for {svc}:",
                    reply_markup=countries_kb(svc,cntrs),parse_mode="HTML")
            else:
                await query.edit_message_text("✅ Blocked.",
                    reply_markup=services_kb(await db.get_distinct_services()),parse_mode="HTML")
        else:
            await query.edit_message_text("❌ No active number.",reply_markup=main_menu_kb(),parse_mode="HTML")
        return

    if data=="set_prefix":
        await query.answer()
        active=await db.get_active_numbers(uid)
        if not active: await query.answer("No active number.",show_alert=True); return
        cur=await db.get_user_prefix(uid)
        if cur:
            await db.set_user_prefix(uid,None); await query.answer("Prefix disabled.")
            svc=active[0].category.split(" - ")[1] if " - " in active[0].category else active[0].category
            lines=[f"{i+1}. <code>+{n.phone_number}</code>" for i,n in enumerate(active)]
            await query.edit_message_text("⚡ Waiting…\n\n"+"\n".join(lines),
                reply_markup=waiting_kb(None,service=svc),parse_mode="HTML")
        else:
            context.user_data["awaiting_prefix"]=True
            context.user_data["prefix_cat"]=active[0].category
            svc=active[0].category.split(" - ")[1] if " - " in active[0].category else active[0].category
            await context.bot.send_message(uid,
                f"🔡 <b>Set Prefix</b>\n{D}\nService: {svc}\n\n"
                "Type prefix (e.g. <code>9198</code>) or <code>off</code>:",parse_mode="HTML")
        return

    # ── Upload service selection ─────────────────────────
    if data.startswith("us_"):
        action=data[3:]
        if action=="done":
            await query.answer()
            sel=context.user_data.get("upload_svcs",[])
            path=context.user_data.get("upload_path")
            country=context.user_data.get("upload_country","Unknown")
            flag=context.user_data.get("upload_flag","🌍")
            if not sel: await query.answer("Select at least one service.",show_alert=True); return
            if not path or not os.path.exists(path): await query.edit_message_text("❌ File lost. Re-upload."); return
            lines=[l.strip() for l in open(path).readlines() if l.strip()]
            total_added=0
            for svc in sel:
                cat=f"{flag} {country} - {svc}"
                total_added+=await db.add_numbers_bulk(lines,cat)
            os.remove(path); context.user_data.pop("upload_path",None)
            await query.edit_message_text(
                f"✅ <b>Upload Complete</b>\n{D}\n📥 Added: <b>{total_added}</b>\n"
                f"📱 Services: {', '.join(sel)}\n🌍 {flag} {country}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_files")]]),
                parse_mode="HTML")
        elif action=="cancel":
            await query.answer()
            path=context.user_data.pop("upload_path",None)
            if path and os.path.exists(path): os.remove(path)
            await query.edit_message_text("❌ Upload cancelled.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_home")]]))
        else:
            sel=context.user_data.get("upload_svcs",[])
            if action in sel: sel.remove(action)
            else: sel.append(action)
            context.user_data["upload_svcs"]=sel
            await query.edit_message_reply_markup(reply_markup=svc_sel_kb(sel)); await query.answer()
        return

    # ── ADMIN SECTION ─────────────────────────────────────
    perms=await get_admin_permissions(uid); is_sup=is_super_admin(uid)

    if data=="admin_home":
        await query.answer()
        context.user_data["awaiting_broadcast"]=False
        role="👑 Super Admin" if is_sup else "👮 Admin"
        s2=await db.get_stats(); avail=s2.get("available",0)
        online=len([p for p in PANELS if p.is_logged_in or
                    (p.panel_type=="ivas" and p.name in IVAS_TASKS
                     and not IVAS_TASKS[p.name].done())])
        await query.edit_message_text(
            f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"🛡 <b>SIGMA ADMIN PANEL</b>  ·  {role}\n"
            f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"📱 {avail} numbers  🔌 {online}/{len(PANELS)} panels",
            reply_markup=admin_main_kb(perms,is_sup),parse_mode="HTML")
        return

    # ── OTP Tools submenu ──────────────────────────────────────────
    if data=="admin_otp_tools":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        store=load_otp_store()
        await query.edit_message_text(
            f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"🔑 <b>OTP Tools</b>\n"
            f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"Store entries: <b>{len(store)}</b>",
            reply_markup=admin_otp_tools_kb(),parse_mode="HTML")
        return

    # ── Notify / Broadcast menu ────────────────────────────────────
    if data=="admin_notify_menu":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        chats=await db.get_all_log_chats()
        users=await db.get_all_users()
        await query.edit_message_text(
            f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"🔔 <b>Notify &amp; Broadcast</b>\n"
            f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"👤 Users: <b>{len(users)}</b>   📋 Log groups: <b>{len(chats)}</b>",
            reply_markup=admin_notify_kb(),parse_mode="HTML")
        return

    if data=="ping_log_groups":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        chats=await db.get_all_log_chats()
        ok=0; fail=0
        for gid in chats:
            try:
                await context.bot.send_message(gid,"📡 <b>Sigma Fetcher — Panel Online ✅</b>",parse_mode="HTML")
                ok+=1
            except Exception: fail+=1
        await query.answer(f"✅ Pinged {ok} groups, ❌ {fail} failed",show_alert=True)
        return

    if data=="send_test_otp":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        fake_otp=str(random.randint(100000,999999))
        await query.answer()
        bot_tag=f"@{BOT_USERNAME}" if BOT_USERNAME else "@PAKOTPBOT"
        now_ts=datetime.now().strftime("%H:%M:%S")
        test_txt=(
            f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"  ✅ OTP RECEIVED  ·  1️⃣ First OTP\n"
            f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            f"🤖 <b>{html.escape(bot_tag)}</b>   ⏰ <code>{now_ts}</code>\n\n"
            f"┌─────────────────────────┐\n"
            f"│  🔑  <b>OTP CODE</b>\n"
            f"│  <code>{fake_otp}</code>\n"
            f"└─────────────────────────┘\n\n"
            f"📱  <code>+92-𝗦𝗜𝗚𝗠𝗔-12345</code>   🇵🇰 #PK\n"
            f"📡  <b>Service:</b> #TG\n"
            f"🔌  <b>Panel:</b>   TEST\n\n"
            f"💬  <i>Your Telegram code: {fake_otp}. Do not share.</i>"
        )
        kb=otp_keyboard(fake_otp,"Your Telegram code: "+fake_otp,for_group=False)
        await context.bot.send_message(uid,test_txt,reply_markup=kb,parse_mode="HTML")
        await query.edit_message_text("✅ Test OTP sent to your DM.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_notify_menu")]]))
        return

    if data=="find_otp_prompt":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        context.user_data["awaiting_link"]="FIND_OTP"
        await query.answer()
        await query.edit_message_text(
            "🔍 <b>Find OTP by Number</b>\n\nSend the phone number to search:",
            parse_mode="HTML")
        return

    # ── Numbers submenu ──────────────────────────────────────
    if data=="admin_numbers":
        if "manage_files" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        cats=await db.get_categories_summary()
        if not cats:
            await query.edit_message_text(
                f"📂 <b>Numbers</b>\n{D}\n"
                "No numbers uploaded yet.\n\n"
                "📤 Upload a <code>.txt</code> file with one number per line.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙  Back",callback_data="admin_home")]]),
                parse_mode="HTML")
        else:
            s=await db.get_stats()
            await query.edit_message_text(
                f"📂 <b>Numbers Manager</b>\n{D}\n"
                f"🟢 Available: <b>{s.get('available',0)}</b>  |  "
                f"🔴 In Use: <b>{s.get('assigned',0)}</b>\n"
                f"🧊 Cooldown: <b>{s.get('cooldown',0)}</b>  |  "
                f"✅ Used: <b>{s.get('used',0)}</b>",
                reply_markup=admin_numbers_kb(cats),parse_mode="HTML")
        return

    if data=="admin_upload_info":
        if "manage_files" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        await query.edit_message_text(
            f"📤 <b>Upload Numbers</b>\n{D}\n"
            "Send a <b>.txt file</b> in this chat.\n\n"
            "Format: one phone number per line\n"
            "<code>923001234567</code>\n"
            "<code>923009876543</code>\n\n"
            "The bot will auto-detect country and ask for services.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  Back",callback_data="admin_numbers")]]),
            parse_mode="HTML")
        return

    if data.startswith("cat_stats_"):
        if "manage_files" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        sid=data[10:]; cat=CATEGORY_MAP.get(sid)
        if not cat: await query.answer("Expired. Reopen Numbers.",show_alert=True); return
        await query.answer()
        async with db.AsyncSessionLocal() as session:
            from sqlalchemy import func as sfunc
            statuses=["AVAILABLE","ASSIGNED","RETENTION","USED","BLOCKED"]
            lines=[]
            for st in statuses:
                cnt=await session.scalar(
                    select(sfunc.count(db.Number.id)).where(
                        db.Number.category==cat,db.Number.status==st)) or 0
                if cnt>0:
                    icons={"AVAILABLE":"🟢","ASSIGNED":"🔴","RETENTION":"🧊","USED":"✅","BLOCKED":"🚫"}
                    lines.append(f"{icons[st]} {st}: <b>{cnt}</b>")
        await query.edit_message_text(
            f"📊 <b>Category Stats</b>\n{D}\n"
            f"<b>{html.escape(cat)}</b>\n\n"+("\n".join(lines) or "Empty"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  Back",callback_data="admin_numbers")]]),
            parse_mode="HTML")
        return

    if data=="purge_used":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        await query.edit_message_text("⚠️ Delete ALL <b>USED</b> numbers permanently?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅  Yes Purge",callback_data="confirm_purge_used"),
                InlineKeyboardButton("❌  Cancel",   callback_data="admin_numbers"),
            ]]),parse_mode="HTML")
        return

    if data=="confirm_purge_used":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        async with db.AsyncSessionLocal() as session:
            r=await session.execute(stext("DELETE FROM numbers WHERE status='USED'"))
            await session.commit(); n=r.rowcount
        await query.answer(f"✅ Purged {n} used numbers.",show_alert=True)
        cats=await db.get_categories_summary()
        await query.edit_message_text(f"📂 <b>Numbers Manager</b>\n{D}",
            reply_markup=admin_numbers_kb(cats),parse_mode="HTML")
        return

    if data=="purge_blocked":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        async with db.AsyncSessionLocal() as session:
            r=await session.execute(stext("DELETE FROM numbers WHERE status='BLOCKED'"))
            await session.commit(); n=r.rowcount
        await query.answer(f"✅ Purged {n} blocked numbers.",show_alert=True)
        cats=await db.get_categories_summary()
        await query.edit_message_text(f"📂 <b>Numbers Manager</b>\n{D}",
            reply_markup=admin_numbers_kb(cats),parse_mode="HTML")
        return

    # ── Stats submenu ─────────────────────────────────────────
    if data=="admin_stats_menu":
        if "view_stats" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        await query.edit_message_text(f"📊 <b>Statistics</b>\n{D}",
            reply_markup=admin_stats_menu_kb(),parse_mode="HTML")
        return

    if data=="admin_db_summary":
        if "view_stats" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        users=await db.get_all_users(); logs=await db.get_all_log_chats()
        s=await db.get_stats()
        total_n=sum(s.values())
        active=[p for p in PANELS if p.is_logged_in or (p.panel_type=="ivas" and p.name in IVAS_TASKS and not IVAS_TASKS[p.name].done())]
        await query.edit_message_text(
            f"💾 <b>Database Summary</b>\n{D}\n"
            f"👤 Users:       <b>{len(users)}</b>\n"
            f"📋 Log Groups:  <b>{len(logs)}</b>\n"
            f"🔌 Panels:      <b>{len(PANELS)}</b>  (active: {len(active)})\n"
            f"📱 Numbers:     <b>{total_n}</b>\n"
            f"🟢 Available:   <b>{s.get('available',0)}</b>\n"
            f"🔴 In Use:      <b>{s.get('assigned',0)}</b>\n"
            f"🧊 Cooldown:    <b>{s.get('cooldown',0)}</b>\n"
            f"✅ Used:        <b>{s.get('used',0)}</b>\n"
            f"🚫 Blocked:     <b>{s.get('blocked',0)}</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙  Back",callback_data="admin_stats_menu")]]),
            parse_mode="HTML")
        return

    if data=="admin_otp_history":
        if "view_stats" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        async with db.AsyncSessionLocal() as session:
            rows=(await session.execute(
                stext("SELECT phone_number,otp_code,service,timestamp FROM history "
                      "ORDER BY timestamp DESC LIMIT 10")
            )).fetchall()
        if not rows:
            await query.edit_message_text("📈 No OTP history yet.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  Back",callback_data="admin_stats_menu")]]))
            return
        lines=[]
        for row in rows:
            ts=str(row[3])[:16] if row[3] else "?"
            lines.append(f"📱 <code>{mask_number(str(row[0]))}</code>  🔑 <code>{row[1]}</code>  ⏰ {ts}")
        await query.edit_message_text(
            f"📈 <b>Last 10 OTP Deliveries</b>\n{D}\n"+"\n".join(lines),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  Back",callback_data="admin_stats_menu")]]),
            parse_mode="HTML")
        return

    # ── Users submenu ─────────────────────────────────────────
    if data=="admin_users":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        await query.edit_message_text(f"👤 <b>User Manager</b>\n{D}",
            reply_markup=admin_users_kb(),parse_mode="HTML")
        return

    if data=="admin_list_users":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        all_u=await db.get_all_users()
        lines=[]
        for u in all_u[:25]:
            stats=await db.get_user_stats(u)
            crown="👑 " if u in INITIAL_ADMIN_IDS else ""
            lines.append(f"{crown}<code>{u}</code>  ✅{stats['success']}")
        more="" if len(all_u)<=25 else f"\n<i>…and {len(all_u)-25} more</i>"
        await query.edit_message_text(
            f"👤 <b>All Users ({len(all_u)})</b>\n{D}\n"
            +("\n".join(lines) or "None")+more,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  Back",callback_data="admin_users")]]),
            parse_mode="HTML")
        return

    # ── Maintenance submenu ───────────────────────────────────
    if data=="admin_maintenance":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        await query.edit_message_text(f"🧹 <b>Maintenance</b>\n{D}",
            reply_markup=admin_maintenance_kb(),parse_mode="HTML")
        return

    if data=="reload_countries":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        load_countries()
        await query.answer(f"✅ Reloaded {len(COUNTRY_DATA)} countries.",show_alert=True)
        return

    if data=="login_all_panels":
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer("🔄 Logging in to all panels…")
        ok=0; fail=0
        for p in PANELS:
            if p.panel_type=="login":
                if await login_to_panel(p): ok+=1
                else: fail+=1
            elif p.panel_type=="api":
                if await test_api_panel(p): p.is_logged_in=True; ok+=1
                else: fail+=1
        await refresh_panels_from_db()
        await query.edit_message_text(
            f"🔄 <b>Login All Panels</b>\n{D}\n✅ OK: {ok}  |  ❌ Failed: {fail}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  Back",callback_data="admin_panel_manager")]]),
            parse_mode="HTML")
        return

    # ── Settings extras ────────────────────────────────────────
    if data=="change_token_prompt":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        context.user_data["awaiting_link"]="BOT_TOKEN"
        await query.edit_message_text(
            "⚠️ <b>Change Bot Token</b>\n{D}\n"
            "Send the new bot token.\nThe bot will need to be restarted after this.\n\n"
            "/cancel to abort.",parse_mode="HTML")
        return

    if data=="set_developer_prompt":
        context.user_data["awaiting_link"]="DEVELOPER"
        await query.edit_message_text("🧠 Send the new Developer username (@username):")
        return

    if data in ("pt_login","pt_api","pt_ivas"):
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        if uid not in PANEL_ADD_STATES: await query.answer("No pending addition."); return
        ptype=data[3:]
        PANEL_ADD_STATES[uid]["data"]["panel_type"]=ptype
        if ptype=="ivas":
            PANEL_ADD_STATES[uid]["step"]="confirm_uri"
            await query.edit_message_text("📡 <b>IVAS Panel</b>\nUse default URI or enter custom?",parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Use Default",callback_data="pt_ivas_default")],
                    [InlineKeyboardButton("✏️ Custom URI", callback_data="pt_ivas_custom")],
                    [InlineKeyboardButton("❌ Cancel",     callback_data="cancel_action")],
                ]))
        else:
            PANEL_ADD_STATES[uid]["step"]="url"
            prompts={"login":"Enter Base URL (http://…):","api":"Enter API endpoint URL:"}
            await query.edit_message_text(prompts[ptype],parse_mode="HTML")
        return

    if data=="pt_ivas_default":
        if uid not in PANEL_ADD_STATES: await query.answer("No pending addition."); return
        name=PANEL_ADD_STATES[uid]["data"]["name"]
        await add_panel_to_db(name,"",None,None,"ivas",uri=DEFAULT_IVAS_URI)
        await refresh_panels_from_db()
        panel=next((p for p in PANELS if p.name==name),None)
        if panel:
            task=asyncio.create_task(ivas_worker(panel),name=f"IVAS-{name}")
            task.add_done_callback(handle_task_exception); IVAS_TASKS[name]=task
        del PANEL_ADD_STATES[uid]
        await query.edit_message_text("✅ IVAS panel added (default URI) and worker started!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_panel_manager")]]))
        return

    if data=="pt_ivas_custom":
        if uid in PANEL_ADD_STATES: PANEL_ADD_STATES[uid]["step"]="uri"
        await query.edit_message_text("Paste the custom IVAS URI (wss://…):")
        return

    if data=="admin_stats":
        if "view_stats" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer(); s=await db.get_stats()
        pi="\n".join(f"  {'🟢' if p.is_logged_in else '🔴'} {p.name} [{p.panel_type.upper()}]" for p in PANELS) or "  None"
        await query.edit_message_text(
            f"📊 <b>Live Stats</b>\n{D}\n"
            f"📦 Total:     <b>{s.get('available',0)+s.get('assigned',0)+s.get('cooldown',0)+s.get('used',0)+s.get('blocked',0)}</b>\n"
            f"🟢 Available: <b>{s.get('available',0)}</b>\n"
            f"🔴 In Use:    <b>{s.get('assigned',0)}</b>\n"
            f"🧊 Cooldown:  <b>{s.get('cooldown',0)}</b>\n"
            f"✅ Used:      <b>{s.get('used',0)}</b>\n"
            f"🚫 Blocked:   <b>{s.get('blocked',0)}</b>\n\n"
            f"🔌 <b>Panels:</b>\n{pi}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_home")]]),
            parse_mode="HTML")
        return

    if data=="admin_reset":
        if "view_stats" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        n=await db.clean_cooldowns(); await query.answer(f"✅ {n} numbers released.",show_alert=True); return

    if data in ("admin_files","admin_numbers"):
        if "manage_files" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        cats=await db.get_categories_summary()
        s=await db.get_stats()
        if not cats:
            await query.edit_message_text(
                f"📂 <b>Numbers Manager</b>\n{D}\n"
                "No numbers uploaded yet.\n\n"
                "📤 Send a <code>.txt</code> file with one number per line.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  Back",callback_data="admin_home")]]),
                parse_mode="HTML")
        else:
            await query.edit_message_text(
                f"📂 <b>Numbers Manager</b>\n{D}\n"
                f"🟢 Available: <b>{s.get('available',0)}</b>  "
                f"🔴 In Use: <b>{s.get('assigned',0)}</b>  "
                f"🧊 Cooldown: <b>{s.get('cooldown',0)}</b>",
                reply_markup=admin_numbers_kb(cats),parse_mode="HTML")
        return

    if data.startswith("del_"):
        if "manage_files" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        sid=data[4:]; cat=CATEGORY_MAP.get(sid)
        if not cat: await query.edit_message_text("❌ Expired menu. Reopen File Manager."); return
        await db.delete_category(cat)
        cats=await db.get_categories_summary()
        if not cats:
            await query.edit_message_text("📂 All files deleted.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_home")]]))
        else:
            await query.edit_message_text(f"✅ Deleted.\n\n📂 <b>File Manager</b>",
                reply_markup=files_kb(cats),parse_mode="HTML")
        return

    if data=="admin_broadcast":
        if "broadcast" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        context.user_data["awaiting_broadcast"]=True
        await query.edit_message_text(
            f"📢 <b>Broadcast Mode</b>\n{D}\n"
            "Type your announcement and send it.\nDelivered to <b>all registered users</b>.",
            parse_mode="HTML")
        return

    if data=="admin_panel_manager":
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.edit_message_text(f"🔌 <b>Panel Manager</b>\n{D}",reply_markup=panel_mgr_kb(),parse_mode="HTML")
        return

    if data in ("panels_login","panels_api","panels_ivas"):
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await refresh_panels_from_db(); ptype=data.split("_")[1]
        pl=[p for p in PANELS if p.panel_type==ptype]
        icons={"login":"🔑","api":"🔌","ivas":"📡"}; labels={"login":"Login","api":"API","ivas":"IVAS"}
        if not pl:
            await query.edit_message_text(f"{icons[ptype]} <b>{labels[ptype]} Panels</b>\n\nNone yet.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Panel",callback_data="p_add")],
                    [InlineKeyboardButton("🔙 Back",callback_data="admin_panel_manager")]]),parse_mode="HTML")
            return
        lines=[]
        for p in pl:
            if ptype=="ivas": st="🟢" if (p.name in IVAS_TASKS and not IVAS_TASKS[p.name].done()) else "🔴"
            else: st="🟢" if p.is_logged_in else "🔴"
            lines.append(f"{st} <b>{html.escape(p.name)}</b>")
        await query.edit_message_text(f"{icons[ptype]} <b>{labels[ptype]} Panels</b>\n{D}\n"+"\n".join(lines),
            reply_markup=panel_list_kb(pl,ptype),parse_mode="HTML")
        return

    if data=="p_add":
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        PANEL_ADD_STATES[uid]={"step":"name","data":{}}; await query.answer()
        await query.edit_message_text(f"➕ <b>Add Panel</b>\n{D}\nStep 1 — Enter panel name:",parse_mode="HTML")
        return

    if data.startswith("p_test_"):
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        pid=int(data.split("_")[-1]); panel=next((p for p in PANELS if p.id==pid),None)
        if not panel: await query.answer("Not found",show_alert=True); return
        await query.answer("🔄 Testing…")
        if panel.panel_type=="login":
            ok=await login_to_panel(panel)
            await update_panel_login(pid,panel.sesskey if ok else None,panel.api_url if ok else None,ok)
            result=f"{'✅ OK' if ok else '❌ FAILED'}\n{panel.base_url}"
        elif panel.panel_type=="api":
            ok=await test_api_panel(panel); panel.is_logged_in=ok
            await update_panel_login(pid,None,panel.base_url if ok else None,ok)
            result=f"{'✅ API OK' if ok else '❌ API FAILED'}\n{panel.base_url}"
        else:
            running=panel.name in IVAS_TASKS and not IVAS_TASKS[panel.name].done()
            result=f"{'✅ Running' if running else '❌ Stopped'}"
        await refresh_panels_from_db()
        back_cb={"login":"panels_login","api":"panels_api","ivas":"panels_ivas"}.get(panel.panel_type,"admin_panel_manager")
        await query.edit_message_text(f"<b>Test: {html.escape(panel.name)}</b>\n{D}\n{result}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data=back_cb)]]))
        return

    if data.startswith("p_info_"):
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        pid=int(data.split("_")[-1]); panel=next((p for p in PANELS if p.id==pid),None)
        if not panel: await query.answer("Not found",show_alert=True); return
        st="🟢 Online" if panel.is_logged_in else "🔴 Offline"
        info=f"🔍 <b>{html.escape(panel.name)}</b>\n{D}\n🆔 {panel.id} | {panel.panel_type.upper()} | {st}\n\n"
        if panel.panel_type=="login":
            info+=(f"🔗 <code>{html.escape(panel.base_url)}</code>\n"
                   f"👤 <code>{html.escape(panel.username or '')}</code>\n"
                   f"📡 API: <code>{html.escape(panel.api_url or 'N/A')}</code>")
        elif panel.panel_type=="api":
            info+=f"🌐 <code>{html.escape(panel.base_url)}</code>\n🪙 Token: {'✅' if panel.token else '❌'}"
        else:
            uri_=((panel.uri or "")[:80]+"…") if panel.uri and len(panel.uri)>80 else (panel.uri or "")
            running=panel.name in IVAS_TASKS and not IVAS_TASKS[panel.name].done()
            info+=(f"📡 <code>{html.escape(uri_)}</code>\n"
                   f"⚙️ {'🟢 Running' if running else '🔴 Stopped'}")
        back_cb={"login":"panels_login","api":"panels_api","ivas":"panels_ivas"}.get(panel.panel_type,"admin_panel_manager")
        await query.answer()
        try:
            await query.edit_message_text(info,parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Test",callback_data=f"p_test_{pid}"),
                     InlineKeyboardButton("✏️ Edit",callback_data=f"p_edit_{pid}")],
                    [InlineKeyboardButton("🔙 Back",callback_data=back_cb)],
                ]))
        except TelegramBadRequest: pass
        return

    if data.startswith("p_edit_"):
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        pid=int(data.split("_")[-1]); panel=next((p for p in PANELS if p.id==pid),None)
        if not panel: await query.answer("Not found",show_alert=True); return
        PANEL_EDIT_STATES[uid]={"step":"name","panel_id":pid,
            "data":{"name":panel.name,"base_url":panel.base_url,"username":panel.username,
                    "password":panel.password,"panel_type":panel.panel_type,"token":panel.token,"uri":panel.uri}}
        await query.answer()
        await query.edit_message_text(f"✏️ <b>Edit: {html.escape(panel.name)}</b>\n\nCurrent: <code>{html.escape(panel.name)}</code>\nNew name (/skip):",parse_mode="HTML")
        return

    if data.startswith("p_del_") and not data.endswith("confirm"):
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        pid=int(data.split("_")[-1]); context.user_data["confirm_del_panel"]=pid
        p=next((x for x in PANELS if x.id==pid),None)
        await query.answer()
        await query.edit_message_text(f"⚠️ Delete panel <b>{html.escape(p.name if p else str(pid))}</b>?",
            reply_markup=confirm_del_panel_kb(),parse_mode="HTML")
        return

    if data=="p_del_confirm":
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        pid=context.user_data.pop("confirm_del_panel",None)
        if pid:
            p=next((x for x in PANELS if x.id==pid),None)
            if p:
                if p.panel_type=="ivas" and p.name in IVAS_TASKS:
                    IVAS_TASKS[p.name].cancel(); IVAS_TASKS.pop(p.name,None)
                await p.close()
            await delete_panel_from_db(pid); await refresh_panels_from_db()
            await query.answer("✅ Deleted.")
        await query.edit_message_text(f"🔌 <b>Panel Manager</b>\n{D}",reply_markup=panel_mgr_kb(),parse_mode="HTML")
        return

    if data=="admin_manage_logs":
        if "manage_logs" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        chats=await db.get_all_log_chats()
        await query.edit_message_text(f"📋 <b>Log Groups</b>\n{D}\nTotal: <b>{len(chats)}</b>",
            reply_markup=logs_kb(chats),parse_mode="HTML")
        return

    if data.startswith("rm_log_"):
        if "manage_logs" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        cid=int(data.split("_")[-1]); ok=await db.remove_log_chat(cid)
        await query.answer(f"{'✅ Removed' if ok else '❌ Not found'}: {cid}")
        chats=await db.get_all_log_chats()
        await query.edit_message_text(f"📋 <b>Log Groups</b>\n{D}",reply_markup=logs_kb(chats),parse_mode="HTML")
        return

    if data=="add_log_prompt":
        if "manage_logs" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        AWAITING_LOG_ID[uid]=True
        await query.edit_message_text("📋 <b>Add Log Group</b>\n\nSend the numeric chat ID.\n(/cancel to abort)",parse_mode="HTML")
        return

    if data=="admin_manage_admins":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        admins=await list_all_admins()
        await query.edit_message_text(f"👥 <b>Admin Management</b>\n{D}\nTotal: <b>{len(admins)}</b>",
            reply_markup=admin_list_kb(admins),parse_mode="HTML")
        return

    if data.startswith("rm_admin_"):
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        aid=int(data.split("_")[-1])
        if aid==uid: await query.answer("Can't remove yourself!",show_alert=True); return
        if aid in INITIAL_ADMIN_IDS: await query.answer("Can't remove super admin!",show_alert=True); return
        await remove_admin_permissions(aid); await query.answer(f"✅ Removed {aid}")
        admins=await list_all_admins()
        await query.edit_message_text(f"👥 <b>Admin Management</b>",reply_markup=admin_list_kb(admins),parse_mode="HTML")
        return

    if data=="add_admin_prompt":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        AWAITING_ADMIN_ID[uid]=True
        await query.edit_message_text("👥 <b>Add Admin</b>\n\nSend the user's numeric Telegram ID.",parse_mode="HTML")
        return

    if data.startswith("ptoggle|"):
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        _,tuid_str,perm=data.split("|",2); tuid=int(tuid_str)
        sel=AWAITING_PERMISSIONS.get((uid,tuid),[])
        if perm in sel: sel.remove(perm)
        else: sel.append(perm)
        AWAITING_PERMISSIONS[(uid,tuid)]=sel
        await query.edit_message_reply_markup(reply_markup=perms_kb(sel,tuid)); await query.answer()
        return

    if data.startswith("pdone|"):
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        tuid=int(data.split("|")[1]); sel=AWAITING_PERMISSIONS.pop((uid,tuid),[])
        if not sel: await query.answer("Select at least one!",show_alert=True); return
        await set_admin_permissions(tuid,sel); AWAITING_ADMIN_ID.pop(uid,None)
        plist="\n".join(f"• {PERMISSIONS.get(p,p)}" for p in sel)
        await query.edit_message_text(f"✅ <b>Admin {tuid} added!</b>\n\n<b>Permissions:</b>\n{plist}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_manage_admins")]]))
        return

    if data=="admin_settings":
        await query.answer()
        await query.edit_message_text(f"⚙️ <b>Settings</b>\n{D}",reply_markup=admin_settings_kb(),parse_mode="HTML")
        return

    if data=="admin_gui_theme":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        theme_name = _THEME_NAMES.get(OTP_GUI_THEME % 8, "Unknown")
        await query.edit_message_text(
            f"🎨 <b>OTP GUI Theme</b>\n{D}\n"
            f"Current: <b>{theme_name}</b>\n\n"
            "Select a theme to see how OTP messages will look.\n"
            "Both DM and group messages update instantly.",
            reply_markup=gui_theme_kb(), parse_mode="HTML")
        return

    if data.startswith("set_gui_theme_"):
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        OTP_GUI_THEME = int(data.split("_")[-1])
        save_config_key("OTP_GUI_THEME", OTP_GUI_THEME)
        theme_name = _THEME_NAMES.get(OTP_GUI_THEME, "Unknown")
        await query.answer(f"✅ Theme → {theme_name}", show_alert=False)
        await query.edit_message_text(
            f"🎨 <b>OTP GUI Theme</b>\n{D}\n"
            f"✅ Active: <b>{theme_name}</b>",
            reply_markup=gui_theme_kb(), parse_mode="HTML")
        return

    if data=="admin_links":
        await query.answer()
        ch=CHANNEL_LINK or "—"; og=OTP_GROUP_LINK or "—"; nb=NUMBER_BOT_LINK or "—"; su=SUPPORT_USER or "—"
        await query.edit_message_text(
            f"🔗 <b>Bot Links</b>\n{D}\n"
            f"📢 Channel:   <code>{html.escape(ch)}</code>\n"
            f"💬 OTP Group: <code>{html.escape(og)}</code>\n"
            f"📞 Num Bot:   <code>{html.escape(nb)}</code>\n"
            f"🛟 Support:   <code>{html.escape(su)}</code>",
            reply_markup=admin_links_kb(),parse_mode="HTML")
        return

    if data=="admin_botinfo":
        await query.answer()
        await query.edit_message_text(
            f"🤖 <b>Bot Info</b>\n{D}\n"
            f"👤 Username:  @{html.escape(BOT_USERNAME)}\n"
            f"🆔 Token:     <code>{'•'*20}</code>\n"
            f"🧸 Child Bot: {'Yes' if IS_CHILD_BOT else 'No'}\n"
            f"📦 Limit:     {DEFAULT_ASSIGN_LIMIT}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_settings")]]),
            parse_mode="HTML")
        return

    if data.endswith("_prompt") and data in ("set_channel_prompt","set_otpgroup_prompt","set_numbot_prompt","set_support_prompt"):
        key_map={"set_channel_prompt":"CHANNEL_LINK","set_otpgroup_prompt":"OTP_GROUP_LINK",
                 "set_numbot_prompt":"NUMBER_BOT_LINK","set_support_prompt":"SUPPORT_USER"}
        context.user_data["awaiting_link"]=key_map[data]
        label_map={"CHANNEL_LINK":"Channel Link (https://t.me/...)","OTP_GROUP_LINK":"OTP Group Link (https://t.me/...)","NUMBER_BOT_LINK":"Number Bot Link (https://t.me/...)","SUPPORT_USER":"Support Username (@username)"}
        k=key_map[data]
        await query.edit_message_text(f"✏️ Send new {label_map[k]}:",parse_mode="HTML")
        return

    if data=="set_limit":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.edit_message_text(f"📦 <b>Global Limit</b>\nCurrent: <b>{DEFAULT_ASSIGN_LIMIT}</b>",
            reply_markup=limit_kb(),parse_mode="HTML")
        return

    if data.startswith("glimit_"):
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        DEFAULT_ASSIGN_LIMIT=int(data.split("_")[-1])
        save_config_key("default_limit",DEFAULT_ASSIGN_LIMIT)
        await query.answer(f"✅ Limit → {DEFAULT_ASSIGN_LIMIT}")
        await query.edit_message_text(f"⚙️ <b>Settings</b>\n{D}",reply_markup=admin_settings_kb(),parse_mode="HTML")
        return

    if data=="admin_advanced":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.edit_message_text(f"🛠 <b>Advanced Tools</b>\n{D}",reply_markup=advanced_kb(),parse_mode="HTML")
        return

    if data=="test_panels":
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer("🔄 Testing…")
        lines=[]
        for p in PANELS:
            if p.panel_type=="login":   ok=await login_to_panel(p); lines.append(f"{'✅' if ok else '❌'} {html.escape(p.name)} [LOGIN]")
            elif p.panel_type=="api":   ok=await test_api_panel(p); p.is_logged_in=ok; lines.append(f"{'✅' if ok else '❌'} {html.escape(p.name)} [API]")
            else:
                running=p.name in IVAS_TASKS and not IVAS_TASKS[p.name].done()
                lines.append(f"{'🟢' if running else '🔴'} {html.escape(p.name)} [IVAS]")
        await query.edit_message_text(f"🔍 <b>Panel Tests</b>\n{D}\n"+"\n".join(lines),parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_advanced")]]))
        return

    if data=="restart_workers":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        for nm,task in list(IVAS_TASKS.items()): task.cancel(); IVAS_TASKS.pop(nm,None)
        for p in PANELS:
            if p.panel_type=="ivas":
                task=asyncio.create_task(ivas_worker(p),name=f"IVAS-{p.name}")
                task.add_done_callback(handle_task_exception); IVAS_TASKS[p.name]=task
        await query.answer("✅ Workers restarted.",show_alert=True)
        await query.edit_message_text(f"🛠 <b>Advanced Tools</b>\n{D}\n✅ Workers restarted.",
            reply_markup=advanced_kb(),parse_mode="HTML")
        return

    if data=="clear_otps":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.edit_message_text("🗑 Clear ALL OTPs?",reply_markup=confirm_kb("clear_otps"))
        return
    if data=="confirm_clear_otps":
        save_otp_store({})
        await query.edit_message_text("✅ All OTPs cleared.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_advanced")]]))
        return

    if data=="export_otps":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        store=load_otp_store()
        if not store: await query.answer("No OTPs.",show_alert=True); return
        fname=f"otp_export_{datetime.now():%Y%m%d_%H%M%S}.json"
        with open(fname,"w") as f: json.dump(store,f,indent=2)
        try:
            with open(fname,"rb") as f: await context.bot.send_document(chat_id=uid,document=f,caption="📤 OTP Export")
        finally: os.remove(fname)
        await query.answer("✅ Exported."); return

    if data=="view_logs":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        try:
            lines_=open(LOG_FILE,errors="replace").readlines()[-25:]
            await query.edit_message_text(
                f"<b>Last 25 log lines</b>\n<pre>{html.escape(''.join(lines_)[-3500:])}</pre>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_advanced")]]))
        except Exception as e: await query.edit_message_text(f"Error: {e}")
        return

    if data=="admin_fetch_sms":
        if "manage_panels" not in perms and not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer("📡 Fetching…")
        report=f"📋 <b>SMS Fetch Report</b>\n🕒 {datetime.now():%Y-%m-%d %H:%M:%S}\n{D}\n"
        for p in PANELS:
            if p.panel_type=="ivas":
                running=p.name in IVAS_TASKS and not IVAS_TASKS[p.name].done()
                report+=f"📡 <b>{html.escape(p.name)}</b> [IVAS] {'🟢 Running' if running else '🔴 Stopped'}\n\n"; continue
            if p.panel_type=="login" and not p.is_logged_in: await login_to_panel(p)
            sms=await fetch_panel_sms(p)
            if sms is None: report+=f"❌ <b>{html.escape(p.name)}</b>: Auth failed.\n\n"
            elif not sms: report+=f"✅ <b>{html.escape(p.name)}</b>: Connected — no recent SMS.\n\n"
            else:
                report+=f"✅ <b>{html.escape(p.name)}</b> — {len(sms)} records (latest 5):\n"
                for rec in sms[:5]:
                    if p.panel_type=="api": dt_=str(rec[0]); num_=str(rec[1]); msg_=str(rec[3])
                    else: dt_=str(rec[0]); num_=str(rec[2]) if len(rec)>2 else "?"; msg_=get_message_body(rec) or ""
                    otp_=extract_otp_regex(msg_) or ""; time_=dt_[11:19] if len(dt_)>=19 else dt_
                    report+=f"  ⏰{time_} 📱{mask_number(num_)} {'🔑'+otp_ if otp_ else ''}\n  {html.escape(msg_[:60])}\n"
                report+="\n"
        bkb=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_home")]])
        if len(report)>4000:
            for chunk in [report[i:i+4000] for i in range(0,len(report),4000)]:
                await context.bot.send_message(uid,chunk,parse_mode="HTML")
            await context.bot.send_message(uid,"Done.",reply_markup=bkb)
        else:
            await context.bot.send_message(uid,report,parse_mode="HTML",reply_markup=bkb)
        return

    # ── Multi-Bot Management ─────────────────────────────
    if not IS_CHILD_BOT:
        if data=="admin_bots":
            if not is_sup: await query.answer("Unauthorized",show_alert=True); return
            await query.answer(); bots=bm.list_bots()
            tr=sum(1 for b in bots if b.get("running"))
            lines_txt="\n".join(f"{'🟢' if b.get('running') else '🔴'} <b>{html.escape(b['name'])}</b>  <code>{b['id']}</code>" for b in bots) if bots else "<i>No bots yet.</i>"
            await query.edit_message_text(
                f"🖥  <b>Bot Manager</b>\n{D}\nTotal: <b>{len(bots)}</b>  |  Running: <b>{tr}</b>\n\n{lines_txt}",
                reply_markup=bots_list_kb(bots),parse_mode="HTML")
            return

        if data=="add_bot_start":
            if not is_sup: await query.answer("Unauthorized",show_alert=True); return
            BOT_ADD_STATES[uid]={"step":"name","data":{}}; await query.answer()
            await query.edit_message_text(
                f"🤖 <b>Add New Bot</b>\n{D}\n"
                "Step 1/9 — Send a <b>name</b> for this bot\n"
                "<i>e.g. MyStore, OTPBot2, SigmaV2</i>\n\nSend /cancel to abort.",
                parse_mode="HTML")
            return

        if data.startswith("bot_info_"):
            if not is_sup: await query.answer("Unauthorized",show_alert=True); return
            bid=data[9:]; info=bm.get_bot_info(bid)
            if not info: await query.answer("Not found.",show_alert=True); return
            running=bm.is_running(bid); st="🟢 Running" if running else "🔴 Stopped"
            created=info.get("created_at","?")[:16].replace("T"," ")
            uname=info.get("bot_username","?")
            bot_link=f"https://t.me/{uname.lstrip('@')}" if uname and uname!="?" else "—"
            await query.answer()
            try:
                await query.edit_message_text(
                    f"╔══════════════════════════╗\n"
                    f"║  🤖  {html.escape(info.get('name','?')):<21}║\n"
                    f"╠══════════════════════════╣\n"
                    f"║  📶 {st:<24}║\n"
                    f"║  🆔 <code>{bid}</code>         ║\n"
                    f"╠══════════════════════════╣\n"
                    f"  👤 @{html.escape(uname):<23}\n"
                    f"  👥 Admins: {str(info.get('admin_ids',[]))[:20]}\n"
                    f"  📅 Created: {created}\n"
                    f"  📁 <code>{html.escape(info.get('folder','?'))[-30:]}</code>\n"
                    f"╠══════════════════════════╣\n"
                    f"  📢 {html.escape(info.get('channel_link','—') or '—')[:30]}\n"
                    f"  💬 {html.escape(info.get('otp_group_link','—') or '—')[:30]}\n"
                    f"  📞 {html.escape(info.get('number_bot_link','—') or '—')[:30]}\n"
                    f"  🛟 {html.escape(info.get('support_user','—') or '—')}",
                    reply_markup=bot_actions_kb(bid,running,info),parse_mode="HTML")
            except TelegramBadRequest: pass
            return

        if data.startswith("bot_start_"):
            if not is_sup: await query.answer("Unauthorized",show_alert=True); return
            bid=data[10:]; ok,msg=bm.start_bot(bid)
            await query.answer(f"{'✅' if ok else '❌'} {msg}",show_alert=True)
            bots=bm.list_bots()
            try: await query.edit_message_reply_markup(reply_markup=bots_list_kb(bots))
            except TelegramBadRequest: pass
            return

        if data.startswith("bot_stop_"):
            if not is_sup: await query.answer("Unauthorized",show_alert=True); return
            bid=data[9:]; ok,msg=bm.stop_bot(bid)
            await query.answer(f"{'✅' if ok else '❌'} {msg}",show_alert=True)
            bots=bm.list_bots()
            try: await query.edit_message_reply_markup(reply_markup=bots_list_kb(bots))
            except TelegramBadRequest: pass
            return

        if data.startswith("bot_restart_"):
            if not is_sup: await query.answer("Unauthorized",show_alert=True); return
            bid=data[12:]; await query.answer("🔁 Restarting…")
            ok,msg=bm.restart_bot(bid); info=bm.get_bot_info(bid) or {}; running=bm.is_running(bid)
            try:
                await query.edit_message_text(
                    f"🤖 <b>{html.escape(info.get('name','?'))}</b>\n"
                    f"{'🟢 Running' if running else '🔴 Stopped'}\nResult: {msg}",
                    reply_markup=bot_actions_kb(bid,running,info),parse_mode="HTML")
            except TelegramBadRequest: pass
            return

        if data.startswith("bot_log_"):
            if not is_sup: await query.answer("Unauthorized",show_alert=True); return
            bid=data[8:]; log=bm.get_bot_log(bid,lines=30); info=bm.get_bot_info(bid) or {}
            await query.answer()
            try:
                await query.edit_message_text(
                    f"📋 <b>Log: {html.escape(info.get('name','?'))}</b>\n<pre>{html.escape(log[-3000:])}</pre>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔁 Refresh",callback_data=f"bot_log_{bid}"),
                        InlineKeyboardButton("🔙 Back",   callback_data=f"bot_info_{bid}"),
                    ]]))
            except TelegramBadRequest: pass
            return

        if data.startswith("bot_del_") and not data.startswith("bot_delok_"):
            if not is_sup: await query.answer("Unauthorized",show_alert=True); return
            bid=data[8:]; info=bm.get_bot_info(bid) or {}; await query.answer()
            await query.edit_message_text(
                f"⚠️ Delete bot <b>{html.escape(info.get('name','?'))}</b>?\n\n"
                "This permanently stops and deletes its folder.",
                reply_markup=confirm_del_bot_kb(bid),parse_mode="HTML")
            return

        if data.startswith("bot_delok_"):
            if not is_sup: await query.answer("Unauthorized",show_alert=True); return
            bid=data[10:]; ok,msg=bm.delete_bot(bid)
            await query.answer(f"{'✅' if ok else '❌'} {msg}",show_alert=True)
            bots=bm.list_bots()
            await query.edit_message_text(f"🖥  <b>Bot Manager</b>\nTotal: <b>{len(bots)}</b>",
                reply_markup=bots_list_kb(bots),parse_mode="HTML")
            return

    # ═══════════════════════════════════════════════════
    #  OTP STORE VIEWER
    # ═══════════════════════════════════════════════════
    if data=="admin_otp_store":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        store=load_otp_store()
        if not store:
            await query.edit_message_text("🔑 <b>OTP Store</b>\nEmpty.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_home")]]),
                parse_mode="HTML")
            return
        lines=[f"📱 <code>{mask_number(k)}</code>  🔑 <code>{v}</code>" for k,v in list(store.items())[-20:]]
        await query.edit_message_text(
            f"🔑 <b>OTP Store</b>  ({len(store)} entries, last 20)\n{D}\n"+"\n".join(lines),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Clear All", callback_data="clear_otps"),
                 InlineKeyboardButton("📤 Export",    callback_data="export_otps")],
                [InlineKeyboardButton("🔙 Back",      callback_data="admin_home")]]),
            parse_mode="HTML")
        return

    # ═══════════════════════════════════════════════════
    #  BROADCAST TO ALL BOTS USERS
    # ═══════════════════════════════════════════════════
    if data=="broadcast_all_bots":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        context.user_data["bcast_all_bots"]=True
        context.user_data["awaiting_broadcast"]=True
        await query.answer()
        bots=bm.list_bots(); total_bots=len(bots)
        await query.edit_message_text(
            f"📢 <b>Broadcast to ALL Bots</b>\n{D}\n"
            f"This will send your message to users of <b>ALL {total_bots} child bots</b> "
            f"plus this master bot.\n\n"
            "✏️ <b>Type your message and send it:</b>\n"
            "<i>(Supports HTML formatting)</i>",
            parse_mode="HTML")
        return

    # ═══════════════════════════════════════════════════
    #  CHILD BOT — START ALL / STOP ALL
    # ═══════════════════════════════════════════════════
    if data=="bots_start_all":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer("▶️ Starting all bots…")
        bots=bm.list_bots(); ok=0; fail=0
        for b in bots:
            if not bm.is_running(b["id"]):
                res,_=bm.start_bot(b["id"])
                if res: ok+=1
                else:   fail+=1
        bots=bm.list_bots(); run=sum(1 for b in bots if b.get("running"))
        await query.edit_message_text(
            f"🖥 <b>Bot Manager</b>\n{D}\n"
            f"▶️ Started: <b>{ok}</b>  ❌ Failed: <b>{fail}</b>\n"
            f"🟢 Running: <b>{run}/{len(bots)}</b>",
            reply_markup=bots_list_kb(bots),parse_mode="HTML")
        return

    if data=="bots_stop_all":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer("⏹ Stopping all bots…")
        bots=bm.list_bots(); stopped=0
        for b in bots:
            if bm.is_running(b["id"]):
                bm.stop_bot(b["id"]); stopped+=1
        bots=bm.list_bots()
        await query.edit_message_text(
            f"🖥 <b>Bot Manager</b>\n{D}\n⏹ Stopped: <b>{stopped}</b> bots",
            reply_markup=bots_list_kb(bots),parse_mode="HTML")
        return

    if data=="bots_all_stats":
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        await query.answer()
        bots=bm.list_bots(); lines=[]
        for b in bots:
            st="🟢" if b.get("running") else "🔴"
            reg=bm.load_registry().get(b["id"],{})
            lines.append(
                f"{st} <b>{html.escape(b['name'])}</b>\n"
                f"   📢 {html.escape(reg.get('channel_link','—') or '—')}\n"
                f"   💬 {html.escape(reg.get('otp_group_link','—') or '—')}\n"
                f"   👤 {reg.get('admin_ids',[])}"
            )
        await query.edit_message_text(
            f"📊 <b>All Bots Overview</b>  ({len(bots)} total)\n{D}\n"
            +("\n\n".join(lines) if lines else "None"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_bots")]]),
            parse_mode="HTML")
        return

    # ═══════════════════════════════════════════════════
    #  CHILD BOT — INDIVIDUAL STATS + BROADCAST
    # ═══════════════════════════════════════════════════
    if data.startswith("bot_stats_"):
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        bid=data[10:]; info=bm.get_bot_info(bid) or {}; await query.answer()
        running=bm.is_running(bid)
        log_preview=bm.get_bot_log(bid,lines=5)
        last_lines=log_preview[-300:] if log_preview else "(no log)"
        await query.edit_message_text(
            f"📊 <b>Bot: {html.escape(info.get('name','?'))}</b>\n{D}\n"
            f"📶 Status: {'🟢 Running' if running else '🔴 Stopped'}\n"
            f"📢 Channel: {html.escape(info.get('channel_link','—') or '—')}\n"
            f"💬 OTP Grp: {html.escape(info.get('otp_group_link','—') or '—')}\n"
            f"📞 Num Bot: {html.escape(info.get('number_bot_link','—') or '—')}\n"
            f"👤 Admins:  {info.get('admin_ids',[])}\n\n"
            f"📋 <b>Last log lines:</b>\n<pre>{html.escape(last_lines)}</pre>",
            reply_markup=bot_actions_kb(bid,running,info),parse_mode="HTML")
        return

    if data.startswith("bot_bcast_"):
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        bid=data[10:]; info=bm.get_bot_info(bid) or {}; await query.answer()
        context.user_data["bcast_single_bot"]=bid
        context.user_data["awaiting_broadcast"]=True
        await query.edit_message_text(
            f"📢 <b>Broadcast — {html.escape(info.get('name','?'))}</b>\n{D}\n"
            "Type your message and send it.\n"
            "It will be delivered to users of <b>this bot only</b>.\n\n"
            "<i>(HTML formatting supported)</i>",
            parse_mode="HTML")
        return

    # ═══════════════════════════════════════════════════
    #  CHILD BOT — EDIT LINKS INLINE
    # ═══════════════════════════════════════════════════
    if data.startswith("bot_editlinks_"):
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        bid=data[14:]; info=bm.get_bot_info(bid) or {}; await query.answer()
        await query.edit_message_text(
            f"🔗 <b>Edit Links: {html.escape(info.get('name','?'))}</b>\n{D}\n"
            f"📢 Channel:  <code>{html.escape(info.get('channel_link','—') or '—')}</code>\n"
            f"💬 OTP Grp:  <code>{html.escape(info.get('otp_group_link','—') or '—')}</code>\n"
            f"📞 Num Bot:  <code>{html.escape(info.get('number_bot_link','—') or '—')}</code>\n"
            f"🛟 Support:  <code>{html.escape(info.get('support_user','—') or '—')}</code>",
            reply_markup=bot_edit_links_kb(bid),parse_mode="HTML")
        return

    if data.startswith("bot_setlink_"):
        if not is_sup: await query.answer("Unauthorized",show_alert=True); return
        parts=data.split("_",3); bid=parts[2]; link_key=parts[3]
        await query.answer()
        context.user_data["bot_setlink_bid"]=bid
        context.user_data["bot_setlink_key"]=link_key
        labels={"CHANNEL_LINK":"Channel Link (https://t.me/...)","OTP_GROUP_LINK":"OTP Group Link","NUMBER_BOT_LINK":"Number Bot Link","SUPPORT_USER":"Support Username"}
        await query.edit_message_text(
            f"✏️ Send the new <b>{labels.get(link_key,link_key)}</b> for bot <b>{bid}</b>:\n"
            "/cancel to abort.",parse_mode="HTML")
        return

    # ═══════════════════════════════════════════════════
    #  CANCEL
    # ═══════════════════════════════════════════════════
    if data=="pick_gui":
        await query.answer()
        cur = GUI_STYLE
        def _g(n): return "✅" if cur==n else f"{n}️⃣"
        await safe_edit(query,
            f"🎨 <b>Select OTP GUI Style</b>\n\nCurrent: <b>Style {cur}</b>\n\n"
            f"<b>1</b> — Screenshot  (flag + number + 🔥)\n"
            f"<b>2</b> — Neon / Electric  (⚡ dashes)\n"
            f"<b>3</b> — Sigma Classic  (━ lines)\n"
            f"<b>4</b> — Minimal Clean\n"
            f"<b>5</b> — Royal Gold  (👑)\n"
            f"<b>6</b> — ⭐ TempNum Style  (boxes)\n"
            f"<b>7</b> — ⭐ Jack-X Style  (→ arrow)\n"
            f"<b>8</b> — ⭐ Cyber Matrix  (☠️ dark)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{_g(1)}  Screenshot", callback_data="gui_set_1"),
                 InlineKeyboardButton(f"{_g(2)}  Neon",       callback_data="gui_set_2")],
                [InlineKeyboardButton(f"{_g(3)}  Sigma",      callback_data="gui_set_3"),
                 InlineKeyboardButton(f"{_g(4)}  Minimal",    callback_data="gui_set_4")],
                [InlineKeyboardButton(f"{_g(5)}  Royal Gold", callback_data="gui_set_5")],
                [InlineKeyboardButton(f"⭐ {_g(6)}  TempNum", callback_data="gui_set_6"),
                 InlineKeyboardButton(f"⭐ {_g(7)}  Jack-X",  callback_data="gui_set_7")],
                [InlineKeyboardButton(f"⭐ {_g(8)}  Cyber",   callback_data="gui_set_8")],
                [InlineKeyboardButton("🔙  Back", callback_data="main_menu")],
            ]))
        return

    if data.startswith("gui_set_"):
        style = int(data.split("_")[-1])
        GUI_STYLE = style
        OTP_GUI_THEME = style - 1   # GUI style 1-8 → theme index 0-7
        save_config_key("OTP_GUI_THEME", OTP_GUI_THEME)
        save_config_key("GUI_STYLE", style)
        await query.answer(f"✅ GUI Style {style} activated!", show_alert=True)
        # Show preview
        bot_tag = _get_bot_tag()
        nd = f"92•{bot_tag.lstrip('@')[:9].upper()}•12345"
        preview = build_otp_msg(
            "✅ OTP RECEIVED", "1️⃣ First OTP", "491138",
            "Your code is 491138. Do not share.", "WS", "Hadi",
            "🇵🇰", "PK", "+92", "12345", for_group=False)
        await query.edit_message_text(
            f"🎨 <b>Style {style} Preview:</b>\n\n{preview}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙  Back to Styles", callback_data="pick_gui"),
                InlineKeyboardButton("✅  Keep This",      callback_data="main_menu"),
            ]]), parse_mode="HTML")
        return

    if data=="cancel_action":
        PANEL_ADD_STATES.pop(uid,None); PANEL_EDIT_STATES.pop(uid,None)
        AWAITING_ADMIN_ID.pop(uid,None); AWAITING_LOG_ID.pop(uid,None)
        BOT_ADD_STATES.pop(uid,None)
        context.user_data["awaiting_broadcast"]=False
        context.user_data["awaiting_prefix"]=False
        context.user_data.pop("awaiting_link",None)
        context.user_data.pop("bot_setlink_bid",None)
        context.user_data.pop("bot_setlink_key",None)
        context.user_data.pop("bcast_all_bots",None)
        context.user_data.pop("bcast_single_bot",None)
        await query.edit_message_text("❌ Action cancelled.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin",callback_data="admin_home")]]))
        return

    await query.answer()

# ═══════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════
async def start_watcher_job(ctx):
    asyncio.create_task(active_watcher(ctx.application))

async def _delayed_child_bot_start():
    """
    Start child bots 5 minutes after the main bot fully initialises.

    Why 5 minutes:
      The main bot spends the first 60-70 seconds doing initial panel logins
      (14 panels × ~5s each).  During that time RAM is at peak.  The old 90s
      delay started child bots while the main bot was still logging in and at
      peak memory, triggering the OOM kill.

      At 5 minutes the main bot is fully settled: all logins done, GC has run,
      RAM is at steady-state (~80-100 MB).  The child bot then has room to
      load its own imports without pushing the server over the limit.

    Staggering:
      Each child bot is started one at a time with a 3-minute gap between
      them so their import spikes never overlap.
    """
    await asyncio.sleep(300)   # 5 minutes — main bot fully settled by then
    logger.info("🤖 Starting child bots (staggered to avoid OOM)…")

    reg = bm.load_registry()
    bots_to_start = [
        (bid, info) for bid, info in reg.items()
        if info.get("status") == "running"   # only those that were running before shutdown
    ]

    if not bots_to_start:
        logger.info("🤖 No child bots marked as running — nothing to auto-restore")
        return

    for i, (bid, info) in enumerate(bots_to_start):
        ok, msg = bm.start_bot(bid)
        logger.info(f"{'▶️' if ok else '❌'} Child bot \"{info.get('name',bid)}\": {msg}")
        if i < len(bots_to_start) - 1:
            logger.info(f"   ⏳ Waiting 3 minutes before starting next child bot…")
            await asyncio.sleep(180)   # 3-minute gap between each child bot

async def post_init(application):
    global app; app = application
    await db.init_db()
    await init_panels_table()
    await migrate_panels_table()
    await init_permissions_table()
    await load_panels_from_dex_to_db()
    await refresh_panels_from_db()
    await start_ivas_workers()
    if application.job_queue:
        application.job_queue.run_once(start_watcher_job, 10)
    else:
        asyncio.create_task(active_watcher(application))
    # Child bots are started AFTER a delay — not immediately — to prevent
    # the OOM-kill caused by two Python processes loading simultaneously.
    if not IS_CHILD_BOT:
        asyncio.create_task(_delayed_child_bot_start())
    logger.info(f"✅ Bot ready. Engine starts in 10s. IS_CHILD_BOT={IS_CHILD_BOT}")

if __name__=="__main__":
    PROCESSED_MESSAGES=init_seen_db()
    application=(ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build())
    application.add_handler(CommandHandler("start",         cmd_start))
    application.add_handler(CommandHandler("admin",         cmd_admin))
    application.add_handler(CommandHandler("addadmin",      cmd_add_admin))
    application.add_handler(CommandHandler("removeadmin",   cmd_rm_admin))
    application.add_handler(CommandHandler("listadmins",    cmd_list_admins))
    application.add_handler(CommandHandler("addlogchat",    cmd_add_log))
    application.add_handler(CommandHandler("removelogchat", cmd_rm_log))
    application.add_handler(CommandHandler("listlogchats",  cmd_list_logs))
    application.add_handler(CommandHandler("dox",           cmd_dox))
    application.add_handler(CommandHandler("test1",         cmd_test1))
    application.add_handler(CommandHandler("send1",         cmd_send1))
    application.add_handler(CommandHandler("otpfor",        cmd_otpfor))
    application.add_handler(CommandHandler("groups",        cmd_groups))
    application.add_handler(CommandHandler("addgroup",      cmd_addgrp))
    application.add_handler(CommandHandler("removegroup",   cmd_rmgrp))
    application.add_handler(CommandHandler("set_channel",   cmd_set_channel))
    application.add_handler(CommandHandler("set_otpgroup",  cmd_set_otpgroup))
    application.add_handler(CommandHandler("set_numberbot", cmd_set_numbot))
    application.add_handler(CommandHandler("bots",          cmd_bots))
    application.add_handler(CommandHandler("startbot",      cmd_startbot))
    application.add_handler(CommandHandler("stopbot",       cmd_stopbot))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.Document.MimeType("text/plain"), handle_document))
    application.add_handler(CallbackQueryHandler(callback_handler))

    # ── Global error handler ──────────────────────────────────────
    # Without this, any unhandled exception in a PTB callback prints
    # "No error handlers are registered, logging exception" and the
    # traceback never reaches our logger properly.
    async def ptb_error_handler(update, context):
        logger.error(f"❌ PTB unhandled exception: {context.error}", exc_info=context.error)
        # Optionally notify super admins
        for admin_id in INITIAL_ADMIN_IDS:
            try:
                err_txt = str(context.error)[:300]
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"⚠️ <b>Bot Error</b>\n<code>{html.escape(err_txt)}</code>",
                    parse_mode="HTML")
            except Exception:
                pass

    application.add_error_handler(ptb_error_handler)
    application.run_polling(drop_pending_updates=True)
